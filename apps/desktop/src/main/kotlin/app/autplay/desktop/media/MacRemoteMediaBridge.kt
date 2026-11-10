package app.autplay.desktop.media

import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermission
import java.util.ArrayDeque
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Isolated source adapter, deliberately not constructed by Main or the playback actor yet.
 * It owns the process, bounded pipes, exact protocol correlations and outstanding callbacks.
 * Host ports own all J/K/evidence/audio/file authority. No timeout releases those owners.
 */
internal class MacRemoteMediaBridge private constructor(
    private val bundle: Path,
    private val identity: RemoteMediaPackageIdentity,
    private val authority: RemoteMediaHostAuthority,
    private val initialPolicy: Wire.SetNetworkPolicy,
    val runId: UUID,
) {
    private val lock: Object = Object()
    private val queue: ArrayDeque<Outgoing> = ArrayDeque()
    private val sources: MutableMap<Wire.SourceKey, SourceOwner> = mutableMapOf()
    private val budgets: MutableMap<Wire.Purpose, AttemptBudget> = mutableMapOf()
    private val reads: MutableMap<Pair<Wire.SourceKey, Long>, PendingRead> = mutableMapOf()
    private val timer: ScheduledThreadPoolExecutor = ScheduledThreadPoolExecutor(1) { action ->
        Thread(action, "autplay-remote-media-deadlines").apply { isDaemon = true }
    }.apply { removeOnCancelPolicy = true }
    private val callbacks: ThreadPoolExecutor = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue(28), { action -> Thread(action, "autplay-remote-media-host").apply { isDaemon = true } },
        ThreadPoolExecutor.AbortPolicy())
    private val started: AtomicBoolean = AtomicBoolean(false)
    private val processSlotReleased: AtomicBoolean = AtomicBoolean(false)
    private val secretOwned: AtomicBoolean = AtomicBoolean(false)
    private val readyResult: CompletableFuture<Unit> = CompletableFuture()
    private val releaseResult: CompletableFuture<RemoteMediaRelease> = CompletableFuture()
    val ready: CompletionStage<Unit> get() = readyResult.minimalCompletionStage()
    val release: CompletionStage<RemoteMediaRelease> get() = releaseResult.minimalCompletionStage()
    private var process: Process? = null
    private var executable: Path? = null
    private var packageWitness: RemoteMediaPackageWitness? = null
    private var helloReceived: Boolean = false
    private var helloWritten: Boolean = false
    private var readyReceived: Boolean = false
    private var policyApplied: Boolean = false
    private var nextCommandId: Long = 0
    private var pendingOrdinary: PendingCommand? = null
    private var pendingIntent: PendingCommand? = null
    private var latestIntent: Wire.UpdateIntent? = null
    private var appliedIntent: Wire.IntentFence? = null
    private var networkRevision: Long = 0
    private var policyRevision: Long = 0
    private var transfer: TransferOwner? = null
    private var closing: Boolean = false
    private var closeAcknowledged: Boolean = false
    private var closeAcknowledgedAt: Long? = null
    private var writingAt: Long? = null
    private var failure: Wire.Code? = null
    private var processExited: Boolean = false
    private var readerSettled: Boolean = false
    private var writerSettled: Boolean = false
    private var hostCallbacks: Int = 0
    private var failureDelivery: FailureDelivery = FailureDelivery.NONE
    private var releaseClaimed: Boolean = false
    private var startupAt: Long = 0
    private var lastWallMs: Long = System.currentTimeMillis()
    private var lastNanos: Long = System.nanoTime()

    /** Launch requires the explicit verifier and all current-authority ports; there is no fallback. */
    fun start(): CompletionStage<Unit> {
        if (started.compareAndSet(false, true)) {
            synchronized(lock) { startupAt = System.nanoTime() }
            timer.scheduleAtFixedRate(::watchdog, 100, 100, TimeUnit.MILLISECONDS)
            Thread(::runProcess, "autplay-remote-media-writer").apply { isDaemon = true }.start()
        }
        return ready
    }

    /** ACK reports only the operation-specific native admission/application/terminal meaning. */
    fun submit(command: Wire.Command): CompletionStage<Wire.Ack> {
        val result = CompletableFuture<Wire.Ack>()
        val abandoned = mutableListOf<Outgoing>()
        var acquiredSecret = false
        try {
            RemoteMediaCodec.validate(command, runId, 1)
            if (command.secret != null) {
                acquiredSecret = secretOwned.compareAndSet(false, true)
                if (!acquiredSecret) throw Wire.Invalid()
            }
            synchronized(lock) {
                if (!policyApplied || failure != null || closing) throw Wire.Invalid()
                if (command is Wire.SetNetworkPolicy && command.revision <= policyRevision) throw Wire.Invalid()
                if (command is Wire.UpdateIntent) {
                    val old = latestIntent
                    if (old != null && (command.fence.desiredIntentRevision <= old.fence.desiredIntentRevision ||
                        command.fence.selectionRevision < old.fence.selectionRevision ||
                        command.fence.sleepFenceRevision < old.fence.sleepFenceRevision ||
                        command.fence.timerFenceRevision < old.fence.timerFenceRevision)) throw Wire.Invalid()
                    latestIntent = command
                    // A negative fence closes play admission before any pipe IO. Never coalesce it away.
                    if (!command.wantsPlay) appliedIntent = null
                    abandoned += removeQueued { queued ->
                        val intent = queued.command as? Wire.UpdateIntent
                        intent != null && (!command.wantsPlay || intent.wantsPlay)
                    }
                    abandoned += removeQueued { queued ->
                        val pending = queued.command
                        pending != null && (pending is Wire.Play || pending is Wire.Seek || pending is Wire.ActivateAudio) &&
                            audioFence(pending) != command.fence
                    }
                }
                if (command is Wire.RetireSource) {
                    source(command.source).retiring = true
                    abandoned += removeQueued { it.command != null && it.command.source == command.source }
                }
                if (command is Wire.Close) {
                    closing = true
                    sources.values.forEach { it.retiring = true }
                    abandoned += removeQueued { it.command != null }
                }
                offer(Outgoing(command = command, acknowledged = result))
            }
        } catch (_: Exception) {
            if (acquiredSecret) { command.secret?.close(); secretOwned.set(false) }
            else if (command.secret != null) command.secret?.close()
            result.completeExceptionally(Wire.Invalid())
            fail(Wire.Code.PROTOCOL_ERROR)
        } finally {
            abandoned.forEach {
                if (it.command?.secret != null) { it.command.secret?.close(); secretOwned.set(false) }
                it.acknowledged?.completeExceptionally(Wire.Invalid())
            }
        }
        return result.minimalCompletionStage()
    }

    /** Native DRAINED plus this source's admitted host work and physical pipe-write settlement. */
    fun retire(source: Wire.SourceKey, reason: Wire.RetireReason): CompletionStage<RemoteMediaSourceRelease> {
        val future = synchronized(lock) { sources[source]?.drained } ?: return failedFuture()
        submit(Wire.RetireSource(source, reason))
        return future.minimalCompletionStage()
    }

    fun close(): CompletionStage<RemoteMediaRelease> {
        if (synchronized(lock) { !closing && failure == null && policyApplied }) submit(Wire.Close())
        else if (started.get() && synchronized(lock) { !closing && failure == null }) fail(Wire.Code.DRAIN_UNCONFIRMED)
        else if (!started.get()) {
            fail(Wire.Code.DRAIN_UNCONFIRMED)
            synchronized(lock) { processExited = true; readerSettled = true; writerSettled = true }
            settleRelease()
        }
        return release
    }

    /** Exceptional exit stays FAILED_RETAINED; only the separately admitted new owner may proceed. */
    fun releaseFailedProcessForExplicitNewAttempt(): Boolean {
        val result = releaseResult.getNow(null) ?: return false
        if (result.kind != RemoteMediaReleaseKind.FAILED_RETAINED || !result.processExitConfirmed ||
            !result.pipeReaderSettled || !result.pipeWriterSettled || !result.hostCallbacksSettled ||
            !runCatching { authority.exceptionalRecoveryIsCurrent(runId, result) }.getOrDefault(false)) return false
        if (!processSlotReleased.compareAndSet(false, true)) return false
        PROCESS_OWNER.set(false)
        return true
    }

    /** Initial credit is a real future file-owner admission, never a consequence of startTransfer ACK. */
    fun grantInitialCredit(transferId: UUID, creditBytes: Int): Unit {
        try {
            synchronized(lock) {
                val owner = transfer ?: throw Wire.Invalid()
                if (failure != null || closing || owner.capture.transferId != transferId || !owner.startAcknowledged || owner.started ||
                    owner.retiring || source(owner.capture.source).retiring || creditBytes !in 1..Wire.MAX_DATA) throw Wire.Invalid()
                // The writer owns final admission, including a bounded access wait. A closed
                // renewal fence is not itself evidence that this immutable transfer was lost.
                owner.started = true
                enqueueReceipt(owner, 0, owner.nextOffset, creditBytes, Wire.Disposition.START)
            }
        } catch (_: Exception) { fail(Wire.Code.OWNER_STALE) }
    }

    private fun runProcess(): Unit {
        var output: DataOutputStream? = null
        try {
            if (System.getProperty("os.name") != "Mac OS X") throw Wire.Invalid()
            val canonicalBundle = bundle.toRealPath()
            val resources = canonicalBundle.resolve("Contents/app/resources/native")
            val candidate = resources.resolve("AutPlayRemoteMediaHelper")
            // Every component must be real, bundle-owned, and non-symlink. Signature is separate.
            var component = canonicalBundle
            for (name in canonicalBundle.relativize(candidate)) {
                component = component.resolve(name)
                if (Files.isSymbolicLink(component) || !Files.exists(component, NOFOLLOW_LINKS)) throw Wire.Invalid()
            }
            val canonicalExecutable = candidate.toRealPath(NOFOLLOW_LINKS)
            if (!canonicalExecutable.startsWith(canonicalBundle) || canonicalExecutable != candidate ||
                !Files.isRegularFile(candidate, NOFOLLOW_LINKS) || !Files.isExecutable(candidate) ||
                PosixFilePermission.OWNER_EXECUTE !in Files.getPosixFilePermissions(candidate, NOFOLLOW_LINKS)) throw Wire.Invalid()
            val witness = identity.verify(canonicalBundle, canonicalExecutable) ?: throw Wire.Invalid()
            if (synchronized(lock) { failure != null || closing }) throw Wire.Invalid()
            val builder = ProcessBuilder(canonicalExecutable.toString())
                .directory(canonicalBundle.toFile()).redirectInput(ProcessBuilder.Redirect.PIPE)
                .redirectOutput(ProcessBuilder.Redirect.PIPE).redirectError(ProcessBuilder.Redirect.DISCARD)
            // Do not inherit arbitrary credential-bearing environment, DYLD options or PATH.
            builder.environment().clear()
            val launched = builder.start()
            synchronized(lock) { process = launched; executable = canonicalExecutable; packageWitness = witness }
            observeProcessExit(launched)
            if (synchronized(lock) { failure != null }) launched.destroyForcibly()
            output = DataOutputStream(launched.outputStream)
            val input = DataInputStream(launched.inputStream)
            Thread({ readEvents(input) }, "autplay-remote-media-reader").apply { isDaemon = true }.start()
            val until = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            synchronized(lock) {
                while (!helloReceived && failure == null && System.nanoTime() < until) lock.wait(50)
                if (!helloReceived || failure != null) throw Wire.Invalid()
                helloWritten = true // Publish before writing; ready may immediately follow the flush.
            }
            RemoteMediaCodec.write(output, Wire.helloFields())
            synchronized(lock) {
                while (!readyReceived && failure == null && System.nanoTime() < until) lock.wait(50)
                if (!readyReceived || failure != null) throw Wire.Invalid()
                offer(Outgoing(command = initialPolicy, acknowledged = CompletableFuture()))
            }
            while (true) {
                val outgoing = synchronized(lock) {
                    while (failure == null && !(closeAcknowledged && queue.isEmpty()) && nextSendable() == null) lock.wait(50)
                    if (failure != null || (closeAcknowledged && queue.isEmpty())) null else takeSendable()
                } ?: break
                writeOutgoing(output, outgoing)
            }
        } catch (_: Exception) { fail(Wire.Code.DRAIN_UNCONFIRMED) }
        finally {
            runCatching { output?.close() }
            synchronized(lock) {
                writerSettled = true
                if (process == null) { processExited = true; readerSettled = true }
            }
            settleRelease()
        }
    }

    private fun writeOutgoing(output: DataOutputStream, outgoing: Outgoing): Unit {
        val command = outgoing.command
        try {
            synchronized(lock) { writingAt = System.nanoTime() }
            if (command != null) {
                val isCleanup = command is Wire.RetireSource || command is Wire.Close || command is Wire.Pause ||
                    (command is Wire.UpdateIntent && !command.wantsPlay) || command is Wire.SetNetworkPolicy
                if (!isCleanup && !authority.commandIsCurrent(command)) throw Wire.Invalid()
                if (command.secret != null) {
                    val witness = synchronized(lock) { packageWitness } ?: throw Wire.Invalid()
                    val child = synchronized(lock) { process } ?: throw Wire.Invalid()
                    val path = synchronized(lock) { executable } ?: throw Wire.Invalid()
                    if (!identity.stillMatches(witness, child, path)) throw Wire.Invalid()
                }
                if (command is Wire.ActivateAudio && !authority.audioLeaseIsCurrent(command)) throw Wire.Invalid()
                val requestedFence = audioFence(command)
                if (command is Wire.Play && (requestedFence == null || !authority.intentIsCurrent(requestedFence, true))) throw Wire.Invalid()
                if (command is Wire.ReplaceAccess) {
                    val quiesced = synchronized(lock) { source(command.source).quiesced } ?: throw Wire.Invalid()
                    if (!authority.accessPublicationIsCurrent(command, quiesced)) throw Wire.Invalid()
                }
                val pending = synchronized(lock) {
                    if (failure != null) throw Wire.Invalid()
                    prepare(command, outgoing.admittedAt)
                    nextCommandId = next(nextCommandId)
                    PendingCommand(nextCommandId, command, outgoing.acknowledged ?: throw Wire.Invalid()).also {
                        if (command is Wire.UpdateIntent) pendingIntent = it else pendingOrdinary = it
                    }
                }
                RemoteMediaCodec.write(output, Wire.commandFields(runId, pending.id, command), outgoing.admittedAt)
            } else {
                var reply = outgoing.reply ?: throw Wire.Invalid()
                if (reply is Wire.ReadResponse) {
                    val response = reply as Wire.ReadResponse
                    val request = outgoing.read?.request ?: throw Wire.Invalid()
                    val alive = synchronized(lock) { failure == null && sources[request.source]?.nativeDrained == false }
                    if (!alive) return
                    val obsoleteAccess = synchronized(lock) {
                        source(request.source).renewalId != null || source(request.source).credential != request.credentialGeneration
                    }
                    if (obsoleteAccess) return // The old read was cancelled by the quiescence transition.
                    val closed = synchronized(lock) { closing || source(request.source).retiring }
                    if (closed || (response.decision == Wire.ReadDecision.ALLOW && !authority.readDecisionIsCurrent(request, response))) {
                        reply = denied(request, Wire.Code.OWNER_STALE)
                    }
                }
                if (reply is Wire.DataConsumed) writeReceipt(output, outgoing, reply)
                else RemoteMediaCodec.write(output, Wire.replyFields(reply), outgoing.admittedAt)
            }
        } finally {
            // Wipe after encoding, but retain the single secret-frame slot through its ACK.
            command?.secret?.close()
            synchronized(lock) {
                writingAt = null
                outgoing.countedOwner?.let { it.outbound-- }
                lock.notifyAll()
            }
            settleSources(); settleRelease()
        }
    }

    private fun observeProcessExit(launched: Process): Unit {
        launched.onExit().whenComplete { _, error ->
            // onExit can race the reader consuming a final ACK already in the OS pipe.
            // Own failure delivery before publishing a failed exit to competing settlers.
            if (error != null || launched.exitValue() != 0) fail(Wire.Code.DRAIN_UNCONFIRMED)
            synchronized(lock) { processExited = error == null }
            settleRelease()
        }
    }

    private fun writeReceipt(output: DataOutputStream, outgoing: Outgoing, receipt: Wire.DataConsumed): Unit {
        val owner = outgoing.receiptOwner ?: throw Wire.Invalid()
        val zero = Wire.DataConsumed(receipt.source, receipt.transferId, receipt.attempt, receipt.sequence,
            receipt.nextOffset, 0, receipt.disposition)
        fun encode(value: Wire.DataConsumed): ByteArray = ByteArrayOutputStream().also { bytes ->
            RemoteMediaCodec.write(DataOutputStream(bytes), Wire.replyFields(value), outgoing.admittedAt)
        }.toByteArray()
        // Finish BOTH bounded encodings before the final admission. No serialization/queue wait
        // follows it, and the zero-credit alternative preserves actual WRITTEN/DISCARDED evidence.
        val encoded = encode(receipt)
        val encodedZero = if (receipt.creditBytes == 0) encoded else encode(zero)
        var retire = false
        try {
            val selected = synchronized(lock) {
                val source = source(receipt.source)
                if (transfer !== owner || owner.capture.source != receipt.source ||
                    owner.capture.transferId != receipt.transferId || owner.capture.attempt != receipt.attempt ||
                    (receipt.disposition != Wire.Disposition.START && owner.sequence != receipt.sequence) || owner.nextOffset != receipt.nextOffset ||
                    !owner.creditQueued || owner.ended || source.nativeDrained) throw Wire.Invalid()
                if (receipt.disposition == Wire.Disposition.START && (owner.frame != null || owner.credit != 0)) throw Wire.Invalid()
                if (failure != null) return
                val closed = closing || owner.retiring || source.retiring
                val waiting = !closed && source.renewalId != null && source.renewalDeadline?.live() == true
                val revision = source.revision; val credential = source.credential; val session = source.session
                val current = receipt.creditBytes == 0 || (!closed && !waiting && source.renewalId == null &&
                    source.deadline.live() && runCatching { authority.creditIsCurrent(owner.capture) }.getOrDefault(false) &&
                    // Recheck the local fence/deadline after the nonblocking host observation.
                    failure == null && !closing && !owner.retiring && !source.retiring && source.renewalId == null &&
                    source.revision == revision && source.credential == credential && source.session == session && source.deadline.live())
                if (!current && !closed && !waiting) {
                    source.retiring = true; owner.retiring = true; retire = true
                }
                if (waiting && receipt.creditBytes > 0) owner.withheldCredit = maxOf(owner.withheldCredit, receipt.creditBytes)
                owner.credit = if (current && !closed) receipt.creditBytes else 0
                if (owner.credit > 0 || closed || retire) owner.withheldCredit = 0
                owner.creditQueued = false
                if (receipt.disposition == Wire.Disposition.START && owner.credit == 0) null
                else {
                    // Next data may arrive inside write/flush. This settles only this frame;
                    // outgoing.outbound and writingAt still own the unfinished physical pipe IO.
                    owner.frame?.close(); owner.frame = null
                    if (owner.credit == receipt.creditBytes) encoded else encodedZero
                }
            }
            if (selected != null) { output.write(selected); output.flush() }
            // A denied START has no emitted frame to settle. Every later receipt still crosses
            // the pipe with its real disposition before retirement can await the final ACK.
            if (retire) submit(Wire.RetireSource(receipt.source, Wire.RetireReason.AUTHORITY))
        } finally { encoded.fill(0); if (encodedZero !== encoded) encodedZero.fill(0) }
    }

    // At most one ordinary ACK and one intent ACK. Replies bypass both without reordering replies.
    private fun nextSendable(): Outgoing? {
        val firstOrdinary = queue.firstOrNull { it.command != null && it.command !is Wire.UpdateIntent }
        return queue.firstOrNull { outgoing ->
            when (val command = outgoing.command) {
                null -> true
                is Wire.UpdateIntent -> pendingIntent == null
                is Wire.Play -> outgoing === firstOrdinary && pendingOrdinary == null &&
                    (appliedIntent == command.fence || latestIntent?.fence != command.fence)
                else -> outgoing === firstOrdinary && pendingOrdinary == null
            }
        }
    }
    private fun takeSendable(): Outgoing = (nextSendable() ?: throw Wire.Invalid()).also { queue.remove(it) }
    private fun offer(outgoing: Outgoing): Unit {
        // Nineteen ordinary controls + nine read/receipt slots + four negative/intent controls.
        // Neither a full ordinary queue nor terminal ACK wait can starve last-frame settlement.
        if (queue.size >= 32 || queue.count { it.lane == outgoing.lane } >= when (outgoing.lane) {
                Lane.ORDINARY -> 19; Lane.REPLY -> 9; Lane.FENCE -> 4
            }) throw Wire.Invalid()
        queue.addLast(outgoing)
        outgoing.sourceKey?.let { sources[it]?.let { owner -> owner.outbound++; outgoing.countedOwner = owner } }
        lock.notifyAll()
    }
    private fun removeQueued(predicate: (Outgoing) -> Boolean): List<Outgoing> {
        val removed = queue.filter(predicate)
        removed.forEach { queue.remove(it); it.countedOwner?.let { owner -> owner.outbound-- } }
        return removed
    }

    private fun prepare(command: Wire.Command, admittedAt: Long): Unit {
        when (command) {
            is Wire.SetNetworkPolicy -> {
                if (command.revision <= policyRevision) throw Wire.Invalid()
                policyRevision = command.revision
            }
            is Wire.InstallSource -> {
                if (sources.size >= 2 || sources.containsKey(command.source) || sources.values.any {
                    !it.nativeDrained && (it.purpose == command.purpose ||
                        (command.purpose == Wire.Purpose.INSPECT_PLAYBACK && it.purpose == null))
                }) throw Wire.Invalid()
                val budget = budgets[command.purpose]
                if (budget != null && (command.executionAttempt < budget.attempt ||
                    (command.executionAttempt == budget.attempt && (command.variantId != budget.variantId ||
                        command.copyId != budget.copyId || budget.authFailed)))) throw Wire.Invalid()
                if (budget == null || command.executionAttempt > budget.attempt) {
                    budgets[command.purpose] = AttemptBudget(command.executionAttempt, command.variantId, command.copyId)
                }
                sources[command.source] = SourceOwner(command.source, command.purpose, command.permitRevision,
                    command.credentialGeneration, command.sessionGeneration, command.transportPolicyRevision,
                    command.executionAttempt, Deadline(command.lifetime, admittedAt = admittedAt)).also { it.accessExpiresAtMs = command.accessExpiresAtMs }
            }
            is Wire.InstallRetained -> {
                val retained = Path.of(System.getProperty("user.home"), "Library", "Application Support",
                    "AutPlay", "media", "remote-media-v1", "retained").toAbsolutePath().normalize()
                val path = Path.of(command.managedPath).toAbsolutePath().normalize()
                val filename = path.fileName.toString()
                val suffix = ".${command.representation.formatSuffix}"
                if (path.parent != retained || path.toString() != command.managedPath || !filename.endsWith(suffix) ||
                    !filename.removeSuffix(suffix).matches(Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))) throw Wire.Invalid()
                // This lexical namespace check is not file proof. The required host copy lease
                // and the future child's no-follow fd walk/identity/length admission remain mandatory.
                if (sources.size >= 2 || sources.containsKey(command.source) || sources.values.any {
                    it.purpose != Wire.Purpose.INSPECT_DOWNLOAD && !it.nativeDrained
                }) throw Wire.Invalid()
                sources[command.source] = SourceOwner(command.source, null, command.permitRevision, null, null, null,
                    null, Deadline(command.lifetime, admittedAt = admittedAt)).also { it.representation = command.representation; it.authorized = true }
            }
            is Wire.AuthorizeBytes -> {
                val owner = liveSource(command.source)
                if (owner.purpose == null || owner.authorized || owner.quiesced != null || owner.renewalId != null ||
                    command.permitRevision <= owner.revision || command.representation != owner.representation ||
                    (command.resourceType == Wire.ResourceType.PLAY_INSTANCE) != (owner.purpose == Wire.Purpose.INSPECT_PLAYBACK) ||
                    command.lifetime.authorityExpiresAtMs > (owner.accessExpiresAtMs ?: throw Wire.Invalid())) throw Wire.Invalid()
                owner.publication = PermitPublication(command.permitRevision, Deadline(command.lifetime, admittedAt = admittedAt), true)
            }
            is Wire.RenewPermit -> {
                val owner = liveSource(command.source)
                if (owner.renewalId != null || command.expectedPermitRevision != owner.revision ||
                    command.permitRevision <= owner.revision || (owner.accessExpiresAtMs?.let {
                        command.lifetime.authorityExpiresAtMs > it
                    } == true)) throw Wire.Invalid()
                owner.publication = PermitPublication(command.permitRevision, Deadline(command.lifetime, admittedAt = admittedAt), owner.authorized)
            }
            is Wire.BeginAccessRenewal -> {
                val owner = source(command.source)
                if (owner.retiring || owner.purpose == null || owner.revision != command.expectedPermitRevision ||
                    owner.credential != command.expectedCredentialGeneration || owner.session != command.expectedSessionGeneration ||
                    command.renewalId != next(owner.lastRenewal) ||
                    (owner.renewalId != null && owner.renewalId != command.renewalId) ||
                    (command.renewalReason == Wire.RenewalReason.REJECTED_401 && !owner.rejected401)) throw Wire.Invalid()
                owner.renewalId = command.renewalId
                owner.reason = if (owner.rejected401) Wire.RenewalReason.REJECTED_401 else command.renewalReason
                owner.renewalDeadline = Deadline(Wire.Lifetime(command.renewalDeadlineAtMs, command.remainingRenewalMs), 30_000, admittedAt)
            }
            is Wire.ReplaceAccess -> {
                val owner = source(command.source); val old = owner.quiesced ?: throw Wire.Invalid()
                if (owner.retiring || owner.replacing != null || owner.renewalDeadline?.live() != true ||
                    command.renewalId != owner.renewalId || command.renewalReason != owner.reason ||
                    command.expectedPermitRevision != old.permitRevision || command.expectedCredentialGeneration != old.credentialGeneration ||
                    command.expectedSessionGeneration != old.sessionGeneration || command.permitRevision <= owner.revision ||
                    command.credentialGeneration <= old.credentialGeneration || command.sessionGeneration <= old.sessionGeneration) throw Wire.Invalid()
                owner.replacing = Replacement(command.permitRevision, command.credentialGeneration, command.sessionGeneration,
                    command.accessExpiresAtMs, Deadline(command.lifetime, admittedAt = admittedAt))
            }
            is Wire.UpdateIntent -> Unit
            is Wire.ActivateAudio -> {
                val owner = liveSource(command.source)
                if (!owner.authorized || owner.purpose == Wire.Purpose.INSPECT_DOWNLOAD || owner.renewalId != null ||
                    owner.audioLease != null || sources.values.any { it.audioLease != null } ||
                    appliedIntent != command.fence || latestIntent?.fence != command.fence) throw Wire.Invalid()
                owner.audioLease = command.audioLeaseId
            }
            is Wire.Play, is Wire.Pause, is Wire.Seek -> {
                val key = command.source ?: throw Wire.Invalid(); val owner = source(key)
                val lease = when (command) {
                    is Wire.Play -> command.audioLeaseId
                    is Wire.Pause -> command.audioLeaseId
                    is Wire.Seek -> command.audioLeaseId
                    else -> throw Wire.Invalid()
                }
                if (owner.audioLease != lease || owner.retiring) throw Wire.Invalid()
                if (command !is Wire.Pause && (!owner.deadline.live() || owner.renewalId != null ||
                    latestIntent?.fence != audioFence(command) || appliedIntent != audioFence(command))) throw Wire.Invalid()
                if (command is Wire.Play && latestIntent?.wantsPlay != true) throw Wire.Invalid()
                if (command is Wire.Seek) {
                    if (command.seekId <= owner.lastSeek) throw Wire.Invalid()
                    owner.lastSeek = command.seekId
                }
            }
            is Wire.StartTransfer -> {
                val owner = liveSource(command.source)
                val total = owner.representation?.totalBytes ?: throw Wire.Invalid()
                if (!owner.authorized || owner.purpose != Wire.Purpose.INSPECT_DOWNLOAD || transfer != null ||
                    command.attempt != owner.attempt || command.offset >= total || owner.renewalId != null) throw Wire.Invalid()
                transfer = TransferOwner(RemoteMediaTransferCapture(command.source, command.transferId, command.attempt,
                    command.intentRevision, command.attemptLeaseRevision, command.offset))
            }
            is Wire.RetireSource -> {
                source(command.source).retiring = true
                transfer?.takeIf { it.capture.source == command.source }?.retiring = true
            }
            is Wire.Close -> {
                closing = true; sources.values.forEach { it.retiring = true }; transfer?.retiring = true
            }
        }
    }

    private fun readEvents(input: DataInputStream, nanoTime: () -> Long = System::nanoTime): Unit {
        var window = nanoTime(); var count = 0
        try {
            while (true) {
                val first = input.read()
                if (first < 0) {
                    if (!synchronized(lock) { closeAcknowledged }) fail(Wire.Code.DRAIN_UNCONFIRMED)
                    break
                }
                val partial = AtomicBoolean(true)
                val watchdog = timer.schedule({ if (partial.get()) fail(Wire.Code.PROTOCOL_ERROR) }, 5, TimeUnit.SECONDS)
                val incoming = try { RemoteMediaCodec.read(input, first) } finally { partial.set(false); watchdog.cancel(false) }
                when (incoming) {
                    is Wire.DataFrame -> receiveData(incoming)
                    is Wire.Event -> {
                        val now = nanoTime()
                        if (now < window) throw Wire.Invalid()
                        if (now - window >= TimeUnit.SECONDS.toNanos(1)) { window = now; count = 0 }
                        if (++count > 256) throw Wire.Invalid()
                        receiveEvent(incoming)
                    }
                }
            }
        } catch (_: Exception) { fail(Wire.Code.PROTOCOL_ERROR) }
        finally {
            runCatching { input.close() }
            synchronized(lock) { readerSettled = true }
            settleRelease()
        }
    }

    private fun receiveEvent(event: Wire.Event): Unit {
        if (event is Wire.Ack) { receiveAck(event); return }
        if (event is Wire.Hello || event is Wire.Ready) {
            synchronized(lock) {
                when (event) {
                    is Wire.Hello -> { if (helloReceived || readyReceived) throw Wire.Invalid(); helloReceived = true }
                    is Wire.Ready -> { if (!helloWritten || readyReceived) throw Wire.Invalid(); readyReceived = true }
                    else -> throw Wire.Invalid()
                }
                lock.notifyAll()
            }
            return
        }
        synchronized(lock) {
            if (!policyApplied || failure != null) throw Wire.Invalid()
            if (event is Wire.Network) {
                if (event.runId != runId || event.revision <= networkRevision) throw Wire.Invalid()
                networkRevision = event.revision
            }
            if (event is Wire.SourceEvent) {
                val owner = source(event.source)
                if (event.eventSeq != next(owner.eventSeq) || owner.nativeDrained) throw Wire.Invalid()
                owner.eventSeq = event.eventSeq; owner.lastEventAt = System.nanoTime()
                when (event) {
                    is Wire.Inspected -> {
                        if (owner.purpose == null || owner.authorized || owner.revision != event.permitRevision ||
                            owner.credential != event.credentialGeneration || owner.policy != event.transportPolicyRevision ||
                            owner.representation != null) throw Wire.Invalid()
                        owner.representation = event.representation
                    }
                    is Wire.Playback -> {
                        if (owner.audioLease != event.audioLeaseId) throw Wire.Invalid()
                        owner.active = event.isPlaying || event.status == Wire.PlaybackStatus.BUFFERING
                    }
                    is Wire.SeekResult -> if (owner.audioLease != event.audioLeaseId || event.seekId > owner.lastSeek) throw Wire.Invalid()
                    is Wire.AccessState -> accessState(owner, event)
                    is Wire.AuthNeeded -> authNeeded(owner, event)
                    is Wire.TransferEnd -> transferEnd(owner, event)
                    is Wire.SourceFailure -> {
                        owner.retiring = true; owner.active = false
                        if (event.code == Wire.Code.AUTH_REQUIRED || event.code == Wire.Code.ACCESS_RENEWAL_FAILED) {
                            owner.purpose?.let { budgets[it]?.authFailed = true }
                        }
                    }
                    is Wire.ReadAdmission -> Unit
                }
            }
        }
        if (event is Wire.ReadAdmission) receiveRead(event)
        else hostEvent(event)
    }

    private fun receiveAck(ack: Wire.Ack): Unit {
        val completed = synchronized(lock) {
            if (!readyReceived) throw Wire.Invalid()
            val pending = listOfNotNull(pendingOrdinary, pendingIntent).singleOrNull { it.id == ack.id } ?: throw Wire.Invalid()
            if (ack.outcome != Wire.AckOutcome.REJECTED && ack.outcome != expectedAck(pending.command)) throw Wire.Invalid()
            if (pending === pendingIntent) pendingIntent = null else pendingOrdinary = null
            if (pending.command.secret != null) secretOwned.set(false)
            if (ack.outcome != Wire.AckOutcome.REJECTED) {
                when (val command = pending.command) {
                    is Wire.SetNetworkPolicy -> policyApplied = true
                    is Wire.UpdateIntent -> appliedIntent = command.fence
                    is Wire.AuthorizeBytes, is Wire.RenewPermit -> {
                        val owner = source(command.source ?: throw Wire.Invalid())
                        val publication = owner.publication ?: throw Wire.Invalid()
                        owner.revision = publication.revision; owner.deadline = publication.deadline
                        owner.authorized = publication.authorized; owner.publication = null
                    }
                    is Wire.RetireSource -> {
                        if (transfer?.takeIf { it.capture.source == command.source }?.ended == false) throw Wire.Invalid()
                        source(command.source).nativeDrained = true
                    }
                    is Wire.StartTransfer -> (transfer ?: throw Wire.Invalid()).startAcknowledged = true
                    is Wire.Close -> {
                        if (transfer?.ended == false) throw Wire.Invalid()
                        closeAcknowledged = true; closeAcknowledgedAt = System.nanoTime()
                        sources.values.forEach { it.nativeDrained = true }
                    }
                    else -> Unit
                }
            }
            lock.notifyAll()
            pending
        }
        completed.acknowledged.complete(ack)
        if (ack.outcome == Wire.AckOutcome.REJECTED) fail(ack.code ?: Wire.Code.PROTOCOL_ERROR)
        else if (completed.command === initialPolicy) readyResult.complete(Unit)
        settleSources()
    }

    private fun accessState(owner: SourceOwner, event: Wire.AccessState): Unit {
        if (owner.purpose == null || owner.retiring || event.renewalId != next(owner.lastRenewal) ||
            (owner.renewalId != null && owner.renewalId != event.renewalId)) throw Wire.Invalid()
        if (!event.applied) {
            if (owner.quiesced != null || event.permitRevision != owner.revision || event.credentialGeneration != owner.credential ||
                event.sessionGeneration != owner.session || (owner.rejected401 && event.renewalReason != Wire.RenewalReason.REJECTED_401)) throw Wire.Invalid()
            val currentTransfer = transfer?.takeIf { it.capture.source == owner.key }
            // A queued START has no emitted frame and cannot delay native quiescence. A real
            // frame must already have crossed its one terminal receipt before this event.
            if (currentTransfer?.frame != null) throw Wire.Invalid()
            currentTransfer?.let {
                it.withheldCredit = maxOf(it.withheldCredit, it.credit); it.credit = 0
            }
            owner.renewalId = event.renewalId; owner.reason = event.renewalReason; owner.quiesced = event
            if (owner.renewalDeadline == null) owner.renewalDeadline = Deadline(Wire.Lifetime(System.currentTimeMillis() + 30_000, 30_000), 30_000)
        } else {
            val expected = owner.replacing ?: throw Wire.Invalid()
            if (owner.quiesced == null || !expected.deadline.live() || event.renewalReason != owner.reason || event.permitRevision != expected.revision ||
                event.credentialGeneration != expected.credential || event.sessionGeneration != expected.session) throw Wire.Invalid()
            owner.revision = expected.revision; owner.credential = expected.credential; owner.session = expected.session
            owner.accessExpiresAtMs = expected.accessExpiresAtMs; owner.deadline = expected.deadline
            owner.lastRenewal = event.renewalId; owner.renewalId = null; owner.renewalDeadline = null
            owner.quiesced = null; owner.replacing = null; owner.rejected401 = false
        }
    }
    private fun authNeeded(owner: SourceOwner, event: Wire.AuthNeeded): Unit {
        val purpose = owner.purpose ?: throw Wire.Invalid()
        val budget = budgets[purpose] ?: throw Wire.Invalid()
        if (event.renewalId != next(owner.lastRenewal) || (owner.renewalId != null && owner.renewalId != event.renewalId) || event.executionAttempt != owner.attempt ||
            event.permitRevision != owner.revision || event.rejectedCredentialGeneration != owner.credential ||
            event.sessionGeneration != owner.session || budget.replay401Used) {
            budget.authFailed = true; throw Wire.Invalid()
        }
        budget.replay401Used = true; owner.rejected401 = true; owner.reason = Wire.RenewalReason.REJECTED_401
        owner.renewalId = event.renewalId
        if (owner.renewalDeadline == null) owner.renewalDeadline = Deadline(Wire.Lifetime(System.currentTimeMillis() + 30_000, 30_000), 30_000)
    }

    private fun receiveRead(request: Wire.ReadAdmission): Unit {
        val pending = synchronized(lock) {
            val owner = source(request.source)
            if (hostCallbacks >= 31 || reads.size >= 8 || request.readId <= owner.lastRead || request.permitRevision != owner.revision ||
                request.credentialGeneration != owner.credential || request.transportPolicyRevision != owner.policy ||
                (request.kind == Wire.ReadKind.FILE) != (owner.purpose == null)) throw Wire.Invalid()
            val total = owner.representation?.totalBytes
            if (request.kind != Wire.ReadKind.HEAD && (total == null || !owner.authorized || request.end!! >= total)) throw Wire.Invalid()
            if (request.kind == Wire.ReadKind.HEAD && owner.authorized) throw Wire.Invalid()
            owner.lastRead = request.readId
            PendingRead(request, System.nanoTime() + TimeUnit.SECONDS.toNanos(5), owner.deadline).also {
                reads[request.source to request.readId] = it; owner.callbacks++; hostCallbacks++
            }
        }
        dispatchCallback({
            val closed = synchronized(lock) {
                failure != null || closing || source(request.source).retiring || source(request.source).renewalId != null ||
                    !source(request.source).deadline.live()
            }
            if (closed) completeRead(pending, denied(request, Wire.Code.OWNER_STALE), null)
            else try {
                authority.decideRead(request).whenComplete { response, error -> completeRead(pending, response, error) }
            } catch (_: Exception) { completeRead(pending, null, Wire.Invalid()) }
        }, { completeRead(pending, null, Wire.Invalid()) })
    }

    private fun completeRead(pending: PendingRead, response: Wire.ReadResponse?, error: Throwable?): Unit {
        if (!pending.completed.compareAndSet(false, true)) { fail(Wire.Code.PROTOCOL_ERROR); return }
        var bad = false
        try {
            if (error != null) throw Wire.Invalid()
            val request = pending.request
            var reply = response ?: denied(request, Wire.Code.OWNER_UNAVAILABLE)
            if (error != null || response == null) bad = true
            if (reply.source != request.source || reply.readId != request.readId || reply.permitRevision != request.permitRevision ||
                reply.credentialGeneration != request.credentialGeneration || reply.transportPolicyRevision != request.transportPolicyRevision) throw Wire.Invalid()
            RemoteMediaCodec.validate(reply)
            synchronized(lock) {
                val owner = source(request.source)
                if (failure == null && !owner.nativeDrained && owner.renewalId == null && owner.credential == request.credentialGeneration) {
                    if (closing || owner.retiring || !owner.deadline.live() || System.nanoTime() >= pending.deadline) {
                        reply = denied(request, Wire.Code.OWNER_STALE)
                    } else if (reply.decision == Wire.ReadDecision.ALLOW && !pending.authorityDeadline.covers(reply.lifetime ?: throw Wire.Invalid())) throw Wire.Invalid()
                    offer(Outgoing(reply = reply, read = pending))
                }
            }
        } catch (_: Exception) { bad = true }
        finally {
            if (bad) fail(Wire.Code.OWNER_UNAVAILABLE)
            synchronized(lock) {
                reads.remove(pending.request.source to pending.request.readId)
                sources[pending.request.source]?.let { it.callbacks-- }; hostCallbacks--
            }
            settleSources(); settleRelease()
        }
    }

    private fun receiveData(frame: Wire.DataFrame): Unit {
        var accepted = false
        try {
            val owner = synchronized(lock) {
                val current = transfer ?: throw Wire.Invalid(); val source = source(current.capture.source)
                if (hostCallbacks >= 31 || failure != null || source.quiesced != null || current.ended || current.frame != null || current.creditQueued || current.credit <= 0 ||
                    frame.transferId != current.capture.transferId || frame.attempt != current.capture.attempt ||
                    frame.sequence != next(current.sequence) || frame.offset != current.nextOffset || frame.size > current.credit ||
                    frame.size > (source.representation?.totalBytes ?: 0) - frame.offset) throw Wire.Invalid()
                current.credit = 0; current.sequence = frame.sequence; current.frame = frame
                source.callbacks++; hostCallbacks++; accepted = true
                current
            }
            val settled = AtomicBoolean(false)
            dispatchCallback({
                try { authority.write(frame, owner.capture).whenComplete { witness, error -> completeWrite(owner, frame, settled, witness, error) } }
                catch (_: Exception) { completeWrite(owner, frame, settled, null, Wire.Invalid()) }
            }, { completeWrite(owner, frame, settled, null, Wire.Invalid()) })
        } finally { if (!accepted) frame.close() }
    }

    private fun completeWrite(owner: TransferOwner, frame: Wire.DataFrame, settled: AtomicBoolean, witness: RemoteMediaWriteWitness?, error: Throwable?): Unit {
        if (!settled.compareAndSet(false, true)) { fail(Wire.Code.PROTOCOL_ERROR); return }
        var bad = error != null || witness == null
        try {
            if (error != null) throw Wire.Invalid()
            val actual = witness ?: throw Wire.Invalid()
            if (!actual.isSettled() || actual.transfer != owner.capture || actual.sequence != frame.sequence ||
                actual.disposition == Wire.Disposition.START) throw Wire.Invalid()
            val retiring = synchronized(lock) { closing || failure != null || owner.retiring || source(owner.capture.source).retiring }
            val written = actual.disposition == Wire.Disposition.WRITTEN
            if ((!written && !retiring) || actual.nextOffset != owner.nextOffset + (if (written) frame.size.toLong() else 0L)) throw Wire.Invalid()
            val total = synchronized(lock) { source(owner.capture.source).representation?.totalBytes } ?: throw Wire.Invalid()
            // A candidate only. The writer checks quota/authority/deadline after encoding, even
            // if authority was valid when this settled write enqueued its receipt.
            val credit = if (!retiring && written && actual.nextOffset < total) Wire.MAX_DATA else 0
            synchronized(lock) {
                owner.nextOffset = actual.nextOffset
                if (failure == null && !source(owner.capture.source).nativeDrained) {
                    enqueueReceipt(owner, frame.sequence, actual.nextOffset, credit, actual.disposition)
                } else { frame.close(); owner.frame = null }
            }
        } catch (_: Exception) {
            bad = true; frame.close(); synchronized(lock) { owner.frame = null }
        } finally {
            if (bad) fail(Wire.Code.DRAIN_UNCONFIRMED)
            synchronized(lock) { sources[owner.capture.source]?.let { it.callbacks-- }; hostCallbacks-- }
            settleSources(); settleRelease()
        }
    }
    private fun enqueueReceipt(owner: TransferOwner, sequence: Long, offset: Long, credit: Int, disposition: Wire.Disposition): Unit {
        if (owner.creditQueued) throw Wire.Invalid()
        owner.creditQueued = true
        offer(Outgoing(reply = Wire.DataConsumed(owner.capture.source, owner.capture.transferId, owner.capture.attempt,
            sequence, offset, credit, disposition), receiptOwner = owner))
    }
    private fun transferEnd(source: SourceOwner, event: Wire.TransferEnd): Unit {
        val current = transfer ?: throw Wire.Invalid()
        if (current.capture.source != source.key || current.capture.transferId != event.transferId || current.capture.attempt != event.attempt ||
            current.ended || current.frame != null || current.creditQueued || event.nextOffset != current.nextOffset ||
            (event.outcome == Wire.TransferOutcome.DELIVERED && event.nextOffset != source.representation?.totalBytes)) throw Wire.Invalid()
        current.ended = true
    }

    private fun resumeTransferCredit(event: Wire.AccessState): Unit = synchronized(lock) {
        val source = sources[event.source] ?: return
        val owner = transfer?.takeIf { it.capture.source == event.source } ?: return
        if (failure != null || closing || source.retiring || source.nativeDrained || owner.retiring || owner.ended ||
            source.renewalId != null || source.lastRenewal != event.renewalId || source.revision < event.permitRevision ||
            source.credential != event.credentialGeneration || source.session != event.sessionGeneration ||
            owner.frame != null || owner.creditQueued || owner.credit != 0 || owner.withheldCredit == 0 ||
            owner.nextOffset >= (source.representation?.totalBytes ?: 0)) return
        // START opens only the empty credit slot of this applied renewal. It never replays
        // WRITTEN, resets the transfer sequence/prefix, or creates a new intent/attempt/lease.
        val credit = owner.withheldCredit; owner.withheldCredit = 0
        enqueueReceipt(owner, 0, owner.nextOffset, credit, Wire.Disposition.START)
    }

    private fun hostEvent(event: Wire.Event): Unit {
        synchronized(lock) {
            if (hostCallbacks >= 31 || releaseClaimed) throw Wire.Invalid()
            hostCallbacks++; (event as? Wire.SourceEvent)?.let { source(it.source).callbacks++ }
        }
        dispatchCallback({
            try {
                authority.event(event)
                // Let the authority owner observe the exact native swap before final writer
                // credit admission. A newer transition/retirement still closes that admission.
                if (event is Wire.AccessState && event.applied) resumeTransferCredit(event)
            } catch (_: Exception) { fail(Wire.Code.OWNER_UNAVAILABLE) }
            finally { callbackFinished(event) }
        }, { fail(Wire.Code.CAPACITY_EXCEEDED); callbackFinished(event) })
    }
    private fun callbackFinished(event: Wire.Event): Unit {
        synchronized(lock) { hostCallbacks--; (event as? Wire.SourceEvent)?.let { sources[it.source]?.let { owner -> owner.callbacks-- } } }
        settleSources(); settleRelease()
    }
    private fun dispatchCallback(action: () -> Unit, rejected: () -> Unit): Unit {
        // This private executor uses AbortPolicy, never CallerRuns/discard/custom hooks. Only its
        // submission rejection proves this task cannot run. Callback exceptions occur on its worker.
        try { callbacks.execute(action) }
        catch (_: RejectedExecutionException) { rejected() }
        catch (_: Exception) { fail(Wire.Code.OWNER_UNAVAILABLE) } // Unknown scheduling retains ownership.
    }

    private fun watchdog(): Unit {
        val code = synchronized(lock) {
            if (failure != null || releaseResult.isDone) return
            val now = System.nanoTime(); val wall = System.currentTimeMillis()
            val elapsedMs = TimeUnit.NANOSECONDS.toMillis(now - lastNanos)
            val wallDelta = wall - lastWallMs
            lastNanos = now; lastWallMs = wall
            when {
                elapsedMs < 0 || wallDelta < 0 || kotlin.math.abs(wallDelta - elapsedMs) > 1000 -> Wire.Code.OWNER_STALE
                !policyApplied && now - startupAt >= TimeUnit.SECONDS.toNanos(5) -> Wire.Code.DRAIN_UNCONFIRMED
                writingAt?.let { now - it >= TimeUnit.SECONDS.toNanos(2) } == true -> Wire.Code.DRAIN_UNCONFIRMED
                closeAcknowledgedAt?.let { !processExited && now - it >= TimeUnit.SECONDS.toNanos(5) } == true -> Wire.Code.DRAIN_UNCONFIRMED
                listOfNotNull(pendingOrdinary, pendingIntent).any { now - it.sentAt >= TimeUnit.SECONDS.toNanos(5) } -> Wire.Code.DRAIN_UNCONFIRMED
                reads.values.any { !it.completed.get() && now >= it.deadline } -> Wire.Code.OWNER_UNAVAILABLE
                sources.values.any { !it.retiring && (it.renewalDeadline?.let { wait -> !wait.live() } ?: !(it.publication?.deadline ?: it.deadline).live()) } -> Wire.Code.OWNER_STALE
                sources.values.any { it.active && it.renewalId == null && now - it.lastEventAt > TimeUnit.SECONDS.toNanos(15) } -> Wire.Code.NETWORK_TIMEOUT
                else -> null
            }
        }
        if (code != null) fail(code)
    }

    private fun fail(code: Wire.Code): Unit {
        val abandoned = mutableListOf<CompletableFuture<Wire.Ack>>()
        val changed = synchronized(lock) {
            if (failure != null || releaseClaimed) false else {
                // Reserve the 32nd callback slot BEFORE failure/exit can become observable.
                // Queuing, executor shutdown and reentrant failure never release this obligation.
                hostCallbacks++; failureDelivery = FailureDelivery.OWNED
                failure = code; closing = true; sources.values.forEach { it.retiring = true }; transfer?.retiring = true
                queue.forEach { it.command?.secret?.close(); it.acknowledged?.let(abandoned::add) }; secretOwned.set(false)
                queue.clear(); lock.notifyAll(); true
            }
        }
        if (!changed) return
        runCatching { authority.closeAdmission(code) }
        readyResult.completeExceptionally(Wire.Invalid())
        abandoned.forEach { it.completeExceptionally(Wire.Invalid()) }
        val pending = synchronized(lock) { listOfNotNull(pendingOrdinary, pendingIntent) }
        pending.forEach { it.acknowledged.completeExceptionally(Wire.Invalid()) }
        runCatching { synchronized(lock) { process }?.destroyForcibly() }
        // destroyForcibly is a request only. onExit + actual reader/writer/host settlement own cleanup.
        dispatchCallback({
            var outcome = FailureDelivery.THREW
            try { authority.failed(code); outcome = FailureDelivery.RETURNED }
            catch (_: Throwable) { /* Fixed delivery outcome only; never expose the callback error. */ }
            finally { failureCallbackFinished(outcome) }
        }, { failureCallbackFinished(FailureDelivery.REJECTED) })
        settleRelease()
    }

    private fun failureCallbackFinished(outcome: FailureDelivery): Unit {
        synchronized(lock) {
            if (failureDelivery != FailureDelivery.OWNED) return
            failureDelivery = outcome; hostCallbacks--
        }
        // Rejection is explicit failed delivery, never an untracked future or successful callback.
        // AbortPolicy left no task to join. Other accepted/pending work still blocks release;
        // once it ends, only the explicit current-recovery guard may release this failed slot.
        settleRelease()
    }

    private fun settleSources(): Unit {
        val finished = synchronized(lock) {
            if (failure != null) return
            sources.values.filter { owner -> owner.nativeDrained && owner.callbacks == 0 && owner.outbound == 0 &&
                (transfer?.takeIf { it.capture.source == owner.key }?.frame == null)
            }.also { owners ->
                owners.forEach { owner ->
                    sources.remove(owner.key)
                    if (transfer?.capture?.source == owner.key) transfer = null
                }
            }
        }
        finished.forEach { it.drained.complete(RemoteMediaSourceRelease(RemoteMediaReleaseKind.DRAINED, null, true, true, true, false)) }
    }
    private fun settleRelease(): Unit {
        val result = synchronized(lock) {
            if (releaseClaimed || !processExited || !readerSettled || !writerSettled || writingAt != null || hostCallbacks != 0) return
            val delivery = when (failureDelivery) {
                FailureDelivery.NONE -> RemoteMediaFailureDelivery.NOT_REQUIRED
                FailureDelivery.OWNED -> return
                FailureDelivery.RETURNED -> RemoteMediaFailureDelivery.RETURNED
                FailureDelivery.THREW -> RemoteMediaFailureDelivery.THREW
                FailureDelivery.REJECTED -> RemoteMediaFailureDelivery.REJECTED
            }
            releaseClaimed = true
            transfer?.frame?.close(); transfer?.frame = null
            val graceful = failure == null && closeAcknowledged && queue.isEmpty() && reads.isEmpty() && pendingOrdinary == null && pendingIntent == null
            RemoteMediaRelease(if (graceful) RemoteMediaReleaseKind.DRAINED else RemoteMediaReleaseKind.FAILED_RETAINED,
                if (graceful) null else failure ?: Wire.Code.DRAIN_UNCONFIRMED, true, true, true, true, delivery)
        }
        // Completion may run a successor's synchronous continuation inline. Finish fast cleanup
        // and publish graceful slot release before ANY result completion can expose DRAINED.
        timer.shutdown(); callbacks.shutdown()
        // Failed owners are intentionally retained. A fresh process owner needs explicit recovery.
        if (result.kind == RemoteMediaReleaseKind.DRAINED && processSlotReleased.compareAndSet(false, true)) PROCESS_OWNER.set(false)
        releaseResult.complete(result)
        synchronized(lock) { sources.values.toList() }.forEach { owner ->
            owner.drained.complete(RemoteMediaSourceRelease(result.kind, result.code, owner.nativeDrained, true, result.hostCallbacksSettled,
                result.kind == RemoteMediaReleaseKind.FAILED_RETAINED && result.processExitConfirmed))
        }
    }

    private fun source(key: Wire.SourceKey): SourceOwner = sources[key] ?: throw Wire.Invalid()
    private fun liveSource(key: Wire.SourceKey): SourceOwner = source(key).also {
        if (it.retiring || it.nativeDrained || !it.deadline.live()) throw Wire.Invalid()
    }
    private fun next(value: Long): Long = if (value in 0 until Wire.MAX_INTEGER) value + 1 else throw Wire.Invalid()
    private fun audioFence(command: Wire.Command): Wire.IntentFence? = when (command) {
        is Wire.ActivateAudio -> command.fence
        is Wire.Play -> command.fence
        is Wire.Pause -> command.fence
        is Wire.Seek -> command.fence
        else -> null
    }
    private fun denied(request: Wire.ReadAdmission, code: Wire.Code): Wire.ReadResponse = Wire.ReadResponse(
        request.source, request.readId, request.permitRevision, request.credentialGeneration, request.transportPolicyRevision,
        Wire.ReadDecision.DENY, null, code)
    private fun expectedAck(command: Wire.Command): Wire.AckOutcome = when (command) {
        is Wire.InstallSource, is Wire.ActivateAudio, is Wire.Play, is Wire.Pause, is Wire.Seek,
        is Wire.StartTransfer, is Wire.BeginAccessRenewal, is Wire.ReplaceAccess -> Wire.AckOutcome.ADMITTED
        is Wire.RetireSource, is Wire.Close -> Wire.AckOutcome.DRAINED
        else -> Wire.AckOutcome.APPLIED
    }
    private fun <T> failedFuture(): CompletionStage<T> = CompletableFuture<T>().also { it.completeExceptionally(Wire.Invalid()) }.minimalCompletionStage()

    private enum class FailureDelivery { NONE, OWNED, RETURNED, THREW, REJECTED }
    private enum class Lane { ORDINARY, REPLY, FENCE }
    private class Outgoing(
        val command: Wire.Command? = null, val acknowledged: CompletableFuture<Wire.Ack>? = null,
        val reply: Wire.Reply? = null, val read: PendingRead? = null,
        val receiptOwner: TransferOwner? = null,
    ) {
        val admittedAt: Long = System.nanoTime()
        var countedOwner: SourceOwner? = null
        val sourceKey: Wire.SourceKey? get() = command?.source ?: when (reply) {
            is Wire.DataConsumed -> reply.source
            is Wire.ReadResponse -> reply.source
            else -> null
        }
        val lane: Lane get() = when {
            reply != null -> Lane.REPLY
            command is Wire.UpdateIntent || command is Wire.Pause || command is Wire.RetireSource || command is Wire.Close -> Lane.FENCE
            else -> Lane.ORDINARY
        }
    }
    private class PendingCommand(val id: Long, val command: Wire.Command, val acknowledged: CompletableFuture<Wire.Ack>) {
        val sentAt: Long = System.nanoTime()
    }
    private class PendingRead(val request: Wire.ReadAdmission, val deadline: Long, val authorityDeadline: Deadline) {
        val completed: AtomicBoolean = AtomicBoolean(false)
    }
    private class Replacement(val revision: Long, val credential: Long, val session: Long, val accessExpiresAtMs: Long, val deadline: Deadline)
    private class PermitPublication(val revision: Long, val deadline: Deadline, val authorized: Boolean)
    private class AttemptBudget(val attempt: Long, val variantId: UUID, val copyId: UUID) {
        var replay401Used: Boolean = false
        var authFailed: Boolean = false
    }
    private class SourceOwner(
        val key: Wire.SourceKey, val purpose: Wire.Purpose?, var revision: Long,
        var credential: Long?, var session: Long?, val policy: Long?, val attempt: Long?, var deadline: Deadline,
    ) {
        val drained: CompletableFuture<RemoteMediaSourceRelease> = CompletableFuture()
        var accessExpiresAtMs: Long? = null
        var eventSeq: Long = 0
        var lastRead: Long = 0
        var lastSeek: Long = 0
        var representation: Wire.Representation? = null
        var authorized: Boolean = false
        var publication: PermitPublication? = null
        var audioLease: UUID? = null
        var retiring: Boolean = false
        var nativeDrained: Boolean = false
        var callbacks: Int = 0
        var outbound: Int = 0
        var active: Boolean = false
        var lastEventAt: Long = System.nanoTime()
        var lastRenewal: Long = 0
        var renewalId: Long? = null
        var reason: Wire.RenewalReason? = null
        var rejected401: Boolean = false
        var renewalDeadline: Deadline? = null
        var quiesced: Wire.AccessState? = null
        var replacing: Replacement? = null
    }
    private class TransferOwner(val capture: RemoteMediaTransferCapture) {
        var nextOffset: Long = capture.initialOffset
        var sequence: Long = 0
        var credit: Int = 0
        var withheldCredit: Int = 0
        var creditQueued: Boolean = false
        var startAcknowledged: Boolean = false
        var started: Boolean = false
        var retiring: Boolean = false
        var ended: Boolean = false
        var frame: Wire.DataFrame? = null
    }
    private class Deadline(
        lifetime: Wire.Lifetime, maximum: Long = 10_000, admittedAt: Long = System.nanoTime(),
        private val wallTime: () -> Long = System::currentTimeMillis,
        private val nanoTime: () -> Long = System::nanoTime,
    ) {
        private val absolute: Long = lifetime.authorityExpiresAtMs
        private val nanos: Long = admittedAt + TimeUnit.MILLISECONDS.toNanos(lifetime.remainingLifetimeMs)
        init { if (lifetime.remainingLifetimeMs !in 1..maximum || absolute !in 1..Wire.MAX_INTEGER) throw Wire.Invalid() }
        fun live(): Boolean = wallTime() + 1000 < absolute && nanoTime() + TimeUnit.SECONDS.toNanos(1) < nanos
        fun covers(lifetime: Wire.Lifetime): Boolean = lifetime.authorityExpiresAtMs <= absolute &&
            nanoTime() + TimeUnit.MILLISECONDS.toNanos(lifetime.remainingLifetimeMs) <= nanos
    }

    companion object {
        private val PROCESS_OWNER: AtomicBoolean = AtomicBoolean(false)
        fun create(
            canonicalAppBundle: Path, identity: RemoteMediaPackageIdentity, authority: RemoteMediaHostAuthority,
            initialNetworkPolicyRevision: Long, allowMeteredDownloads: Boolean, runId: UUID,
        ): MacRemoteMediaBridge {
            if (initialNetworkPolicyRevision !in 1..Wire.MAX_INTEGER || !PROCESS_OWNER.compareAndSet(false, true)) throw Wire.Invalid()
            return MacRemoteMediaBridge(canonicalAppBundle, identity, authority,
                Wire.SetNetworkPolicy(initialNetworkPolicyRevision, allowMeteredDownloads), runId)
        }
    }
}
