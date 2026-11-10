package app.autplay.desktop.media

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.*
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** One JVM file/publication owner; no native destination path or retained-source grant. */
internal class DesktopDownloadOwner private constructor(
    private val root: Path,
    private val lockChannel: FileChannel,
    private val lifetimeLock: FileLock,
    private val journal: DesktopAccountMedia,
    private val work: DesktopMediaWorkOwner,
    private val admission: DesktopMediaAccountAdmission,
) : DesktopMediaLocalSourcePort {
    private val writer = Mutex()
    private val stateGate = Any()
    private val current = AtomicReference<Execution?>(null)
    private val readLeases = mutableMapOf<MediaId, Int>()
    private val visibleCopyScopes = mutableMapOf<MediaId, MediaId>()
    private val reservations = mutableMapOf<Pair<MediaId, MediaId>, Long>()
    private val reservationPaths = mutableMapOf<Pair<MediaId, MediaId>, Path>()
    private val closed = AtomicBoolean(false)
    private val writePending = AtomicBoolean(false)
    private val publicationPending = AtomicBoolean(false)
    private var lifetimeReleased = false
    private val fileOwner = Files.getOwner(root, NOFOLLOW_LINKS)
    private val partial: Path = root.resolve("partial")
    private val retained: Path = root.resolve("retained")
    lateinit var currentCapture: (RemoteMediaTransferCapture) -> SelectionCapture.VaultNetwork
    class Progress(val transferId: UUID, val attempt: Long, val settledBytes: Long, val totalBytes: Long)
    private val mutableProgress = kotlinx.coroutines.flow.MutableStateFlow<Progress?>(null)
    val progress: kotlinx.coroutines.flow.StateFlow<Progress?> get() = mutableProgress

    class AttemptInput(
        val capture: SelectionCapture.VaultNetwork, @Volatile var accountLease: AccountLease,
        val intent: MediaDownloadIntent, val attempt: MediaAttempt,
        val original: MediaSourceEvidence, val representation: MediaRepresentation,
        val transfer: RemoteMediaTransferCapture,
    ) { override fun toString(): String = "DownloadAttemptInput(<redacted>)" }

    private class Execution(val input: AttemptInput, val path: Path, val channel: FileChannel,
        var attempt: MediaAttempt, val fullHash: MessageDigest, var blockHash: MessageDigest,
        var blockBytes: Int, var nextOffset: Long, var sequence: Long = 0) {
        val retired = AtomicBoolean(false)
        val publicationAllowed = AtomicBoolean(true)
        @Volatile var settled = false
        @Volatile var failed = false
        var confirmed: Long = attempt.confirmedPrefixBytes
        var delivered = false
    }

    /** Caller has persisted PREPARED; restart/rollover re-verifies every recorded prefix block. */
    suspend fun openAttempt(input: AttemptInput): Long = writer.withLock {
        mediaCheck(!closed.get() && current.get() == null && !publicationPending.get(), MediaCode.CAPACITY_EXCEEDED, MediaStage.WRITE)
        requireLease(input.capture.network.local, input.accountLease)
        val target = input.intent.target ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.WRITE)
        val snapshot = input.original.snapshot
        mediaCheck(input.original.kind == MediaObservationKind.ACQUISITION && input.original.intentId == input.intent.intentId &&
            input.original.evidenceId == target.sourceEvidenceId && input.original.scopeId == input.accountLease.scopeId &&
            input.original.owner == input.capture.network.local.localOwner && target.variantId == snapshot.audioVariantId &&
            target.recordingId == snapshot.recordingId && target.userTrackRefId == snapshot.userTrackRefId &&
            target.userTrackRefRowVersion == snapshot.userTrackRefRowVersion && input.attempt.sourceEvidenceId == target.sourceEvidenceId &&
            input.attempt.intentId == input.intent.intentId && DesktopMediaLimits.next(input.attempt.intentRevision) == input.intent.record.revision &&
            input.attempt.sourceGeneration == input.transfer.attempt && input.attempt.attemptLeaseRevision == input.transfer.attemptLeaseRevision &&
            input.representation.digestSha256 == snapshot.sha256 && input.representation.totalBytes == snapshot.totalBytes &&
            input.representation.mediaType == snapshot.mediaType && input.intent.storageClass in setOf("PINNED", "USER_DOWNLOAD"),
            MediaCode.RECONCILIATION_REQUIRED, MediaStage.WRITE)
        val key = input.accountLease.scopeId to input.intent.intentId
        reservations[key] = input.representation.totalBytes
        val path = partial.resolve("${input.attempt.prefixId.value}.part")
        reservationPaths[key] = path
        checkSpace(0)
        val exists = Files.exists(path, NOFOLLOW_LINKS)
        if (!exists) mediaCheck(input.attempt.confirmedPrefixBytes == 0L, MediaCode.SOURCE_UNKNOWN, MediaStage.VERIFY)
        val channel = if (exists) openFile(path, writable = true) else FileChannel.open(path,
            setOf(CREATE_NEW, READ, WRITE, NOFOLLOW_LINKS), PosixFilePermissions.asFileAttribute(FILE_MODE))
        try {
            requireFile(path)
            val hash = MessageDigest.getInstance("SHA-256")
            val size = channel.size()
            mediaCheck(size >= input.attempt.confirmedPrefixBytes && size <= input.representation.totalBytes,
                MediaCode.RECONCILIATION_REQUIRED, MediaStage.VERIFY)
            var offset = 0L
            var index = -1
            while (offset < input.attempt.confirmedPrefixBytes) {
                val blocks = journal.readPrefixBlocks(input.accountLease, input.attempt.prefixId, index, 100)
                mediaCheck(blocks.isNotEmpty(), MediaCode.RECONCILIATION_REQUIRED, MediaStage.VERIFY)
                for (block in blocks) {
                    mediaCheck(block.blockIndex == index + 1 && block.offset == offset && block.length in 1..262_144 &&
                        block.offset + block.length <= input.attempt.confirmedPrefixBytes, MediaCode.RECONCILIATION_REQUIRED, MediaStage.VERIFY)
                    val bytes = readExact(channel, offset, block.length)
                    try {
                        mediaCheck(digest(bytes) == block.digestSha256, MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY)
                        hash.update(bytes)
                    } finally { bytes.fill(0) }
                    offset += block.length; index++
                }
            }
            mediaCheck(offset == input.attempt.confirmedPrefixBytes && offset == input.transfer.initialOffset,
                MediaCode.RECONCILIATION_REQUIRED, MediaStage.VERIFY)
            // Only this exact exclusive partial is truncated, after the preceding attempt drained.
            // Its unconfirmed tail was never admitted as durable progress or a completed copy.
            if (size != offset) { channel.truncate(offset); channel.force(true) }
            val execution = Execution(input, path, channel, input.attempt, hash,
                MessageDigest.getInstance("SHA-256"), 0, offset)
            mediaCheck(current.compareAndSet(null, execution), MediaCode.CAPACITY_EXCEEDED, MediaStage.WRITE)
            offset
        } catch (failure: Exception) { channel.close(); throw failure }
    }

    /** Pure admission readback; called from the bridge's pipe writer immediately before credit. */
    fun creditIsCurrent(capture: RemoteMediaTransferCapture): Boolean {
        val execution = current.get() ?: return false
        return !closed.get() && !execution.retired.get() && !execution.failed && !execution.settled &&
            execution.input.transfer == capture && execution.nextOffset < execution.input.representation.totalBytes
    }

    fun retire(capture: RemoteMediaTransferCapture): Unit {
        current.get()?.takeIf { it.input.transfer == capture }?.retired?.set(true)
    }
    fun vetoAllPublication(): Unit { current.get()?.publicationAllowed?.set(false) }
    fun vetoPublication(capture: RemoteMediaTransferCapture): Unit {
        current.get()?.takeIf { it.input.transfer == capture }?.publicationAllowed?.set(false)
    }

    /** The future settles only after the actual channel write and any admitted SQL checkpoint. */
    fun write(frame: Wire.DataFrame, capture: RemoteMediaTransferCapture): CompletionStage<RemoteMediaWriteWitness> {
        val result = CompletableFuture<RemoteMediaWriteWitness>()
        if (closed.get()) {
            result.completeExceptionally(mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.WRITE))
            return result.minimalCompletionStage()
        }
        if (!writePending.compareAndSet(false, true)) {
            result.completeExceptionally(mediaFailure(MediaCode.CAPACITY_EXCEEDED, MediaStage.WRITE))
            return result.minimalCompletionStage()
        }
        work.scope.launch {
            try {
                val witness = writer.withLock {
                    val execution = current.get() ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.WRITE)
                    val input = execution.input
                    mediaCheck(input.transfer == capture && frame.transferId == capture.transferId && frame.attempt == capture.attempt &&
                        frame.sequence == execution.sequence + 1 && frame.offset == execution.nextOffset && frame.size in 1..Wire.MAX_DATA &&
                        frame.offset <= input.representation.totalBytes - frame.size, MediaCode.PROTOCOL_ERROR, MediaStage.WRITE)
                    if (execution.retired.get() || closed.get()) {
                        execution.sequence = frame.sequence
                        SettledWrite(capture, frame.sequence, execution.nextOffset, Wire.Disposition.DISCARDED)
                    } else {
                        // Admission is synchronous. The file owner retains this admitted operation
                        // through a later veto; a cancellation cannot pretend it did not write.
                        val liveCapture = currentCapture(capture)
                        mediaCheck(liveCapture.fence == input.capture.fence && liveCapture.network.local.localOwner == input.original.owner &&
                            liveCapture.network.sessionFamilyId == input.capture.network.sessionFamilyId &&
                            liveCapture.network.bindingCommitId == input.capture.network.bindingCommitId,
                            MediaCode.OWNER_STALE, MediaStage.WRITE)
                        admission.withCurrent(MediaAdmissionRequest(liveCapture, null, MediaAdmissionStep.USE,
                            null, input.original, null, null)) { live ->
                            mediaCheck(live.networkAvailable && !execution.retired.get(), MediaCode.OWNER_STALE, MediaStage.WRITE)
                        }
                        checkSpace(frame.size.toLong())
                        requireFile(execution.path)
                        frame.withBytes { bytes ->
                            while (bytes.hasRemaining()) {
                                val count = minOf(bytes.remaining(), 262_144 - execution.blockBytes)
                                val slice = bytes.slice().apply { limit(count) }
                                val hashBytes = ByteArray(count)
                                slice.duplicate().get(hashBytes)
                                try {
                                    var written = 0
                                    while (slice.hasRemaining()) {
                                        val n = execution.channel.write(slice, execution.nextOffset + written)
                                        mediaCheck(n > 0, MediaCode.STORAGE_FULL, MediaStage.WRITE)
                                        written += n
                                    }
                                    execution.fullHash.update(hashBytes); execution.blockHash.update(hashBytes)
                                } finally { hashBytes.fill(0) }
                                execution.nextOffset += count; execution.blockBytes += count
                                bytes.position(bytes.position() + count)
                                if (execution.blockBytes == 262_144 || execution.nextOffset == input.representation.totalBytes) {
                                    // runBlocking belongs to this dedicated IO operation, never the
                                    // playback actor or a native/bridge lock. Settlement owns the frame.
                                    runBlocking { checkpoint(execution) }
                                }
                            }
                        }
                        execution.sequence = frame.sequence
                        SettledWrite(capture, frame.sequence, execution.nextOffset, Wire.Disposition.WRITTEN)
                    }
                }
                if (witness.disposition == Wire.Disposition.WRITTEN) mutableProgress.value = Progress(capture.transferId,
                    capture.attempt, witness.nextOffset, requireNotNull(current.get()).input.representation.totalBytes)
                writePending.set(false)
                result.complete(witness)
            } catch (failure: Exception) {
                current.get()?.apply { failed = true; retired.set(true) }
                writePending.set(false)
                result.completeExceptionally(if (failure is MediaSourceException) failure else mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.WRITE))
            }
        }
        return result.minimalCompletionStage()
    }

    private suspend fun checkpoint(execution: Execution): Unit {
        val input = execution.input
        execution.channel.force(true)
        val offset = execution.nextOffset - execution.blockBytes
        val block = MediaPrefixBlock(record(input.accountLease), input.attempt.prefixId,
            (offset / 262_144).toInt(), offset, execution.blockBytes, hex(execution.blockHash.digest()))
        journal.confirmPrefixBlocks(input.accountLease, MediaId.random(), input.intent.intentId,
            input.attempt.attempt, execution.attempt.record.revision, listOf(block))
        execution.attempt = journal.readAttempt(input.accountLease, input.intent.intentId, input.attempt.attempt)
            ?: throw mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.WRITE)
        mediaCheck(execution.attempt.confirmedPrefixBytes == execution.nextOffset, MediaCode.PUBLICATION_UNCERTAIN, MediaStage.WRITE)
        execution.confirmed = execution.nextOffset; execution.blockBytes = 0
        execution.blockHash = MessageDigest.getInstance("SHA-256")
    }

    /** ACCESS_QUIESCED already settled the exact emitted frame; bytes/attempt/run never change. */
    suspend fun adoptRenewal(expected: AccountLease, fresh: AccountLease): Unit = writer.withLock {
        mediaCheck(!writePending.get() && !publicationPending.get() && !closed.get(), MediaCode.DRAIN_UNCONFIRMED, MediaStage.AUTH)
        current.get()?.let { execution ->
            mediaCheck(execution.input.accountLease == expected && expected.storeId == fresh.storeId &&
                expected.scopeId == fresh.scopeId && expected.bindingCommit == fresh.bindingCommit &&
                expected.sessionFamily == fresh.sessionFamily && !execution.failed && !execution.retired.get(),
                MediaCode.OWNER_STALE, MediaStage.AUTH)
            execution.input.accountLease = fresh
        }
    }

    /** Native source drain has already settled every emitted frame, including discarded frames. */
    suspend fun settle(capture: RemoteMediaTransferCapture, delivered: Boolean): MediaAttempt = writer.withLock {
        val execution = current.get() ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.DRAIN)
        mediaCheck(execution.input.transfer == capture && !writePending.get(), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        execution.retired.set(true)
        mediaCheck(!delivered || (!execution.failed && execution.nextOffset == execution.input.representation.totalBytes &&
            execution.confirmed == execution.nextOffset), MediaCode.REPRESENTATION_INVALID, MediaStage.DRAIN)
        execution.channel.force(true)
        execution.delivered = delivered
        execution.settled = true
        execution.attempt
    }

    /** Exact full bytes plus LOCAL authority suffice after DELIVERED, even if network went away. */
    suspend fun publish(capture: RemoteMediaTransferCapture, currentLocal: AccountLocalCapture): AccountMediaCopyView = writer.withLock {
        val execution = current.get() ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.PUBLISH)
        mediaCheck(execution.input.transfer == capture && execution.settled && execution.delivered && !execution.failed && execution.publicationAllowed.get() &&
            publicationPending.compareAndSet(false, true), MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
        val input = execution.input
        var marker: MediaPublication? = null
        try {
            var local = admission.recaptureLocal(currentLocal)
            requireLease(local, input.accountLease)
            DesktopMediaSourcePolicy.requireLocalGuard(local, downloaded = true)
            mediaCheck(local.localOwner == input.original.owner && local.kOwnerReference == input.capture.network.local.kOwnerReference &&
                local.originLocalGuard?.ownerReference == input.capture.network.local.originLocalGuard?.ownerReference,
                MediaCode.OWNER_STALE, MediaStage.PUBLISH)
            admission.refresh(SelectionCapture.AccountLocal(input.capture.fence, local), null)
            admission.withCurrent(MediaAdmissionRequest(SelectionCapture.AccountLocal(input.capture.fence, local),
                null, MediaAdmissionStep.USE, null, input.original, null, null)) { }
            mediaCheck(execution.publicationAllowed.get(), MediaCode.CANCELLED, MediaStage.PUBLISH)
            checkSpace(0)
            val identity = identity(execution.path)
            val incremental = hex(execution.fullHash.digest())
            mediaCheck(incremental == input.representation.digestSha256 &&
                fullHash(execution.channel, input.representation.totalBytes) { execution.publicationAllowed.get() } == incremental && identity(execution.path) == identity,
                MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY)
            execution.channel.force(true); execution.channel.close()
            val copyId = MediaId.random(); val publicationId = MediaId.random(); val locator = MediaId.random()
            val completionRevision = 1L
            val target = requireNotNull(input.intent.target)
            val metadata = record(input.accountLease)
            val projection = journal.projection(input.accountLease, input.intent.localTrackRefId)
            val exact = projection?.takeIf { it.userTrackRefId == target.userTrackRefId &&
                it.userTrackRefRowVersion == target.userTrackRefRowVersion && it.recordingId == target.recordingId &&
                !it.conflict && !it.tombstone && !it.pendingTargetMutation }
            marker = MediaPublication(metadata, publicationId, copyId, input.intent.intentId, input.attempt.attempt,
                target.sourceEvidenceId, input.intent.record.revision, input.attempt.attemptLeaseRevision,
                local.localGuard.revision, local.originLocalGuard?.revision, exact?.projectionRevision,
                if (exact != null) "ATTACHED" else "DETACHED", null, locator, input.attempt.prefixId, completionRevision, "PREPARED")
            val copy = MediaCopy(metadata, copyId, input.intent.localTrackRefId, target, input.representation.strongEtag,
                incremental, input.representation.totalBytes, input.representation.mediaType, input.representation.format.suffix,
                input.intent.storageClass, "VERIFIED", "PREPARED", locator, completionRevision, publicationId)
            val ownership = MediaCopyOwnership(metadata, copyId, local.localOwner, local.kOwnerReference,
                local.originLocalGuard?.ownerReference, local.kAuthorityRevision, "USER_DOWNLOAD",
                input.intent.intentId, completionRevision, publicationId, "PREPARED")
            val attachment = MediaCopyAttachment(metadata, copyId, if (exact != null) input.attempt.admissionEvidenceId else null,
                exact?.localTrackRefId, exact?.userTrackRefId, exact?.userTrackRefRowVersion, exact?.recordingId,
                exact?.projectionRevision, null, if (exact != null) "ATTACHED" else "DETACHED")
            local = admission.recaptureLocal(local)
            admission.refresh(SelectionCapture.AccountLocal(input.capture.fence, local), null)
            journal.preparePublication(input.accountLease, MediaId.random(), AccountMediaCompletion(copy, ownership, attachment, marker))
            mediaCheck(execution.publicationAllowed.get(), MediaCode.CANCELLED, MediaStage.PUBLISH)
            val destination = retained.resolve("${locator.value}.${input.representation.format.suffix}")
            mediaCheck(Files.getFileStore(execution.path) == Files.getFileStore(retained) && !Files.exists(destination, NOFOLLOW_LINKS),
                MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            // createLink has CREATE_NEW semantics; no rename-overwrite or copy/delete fallback.
            Files.createLink(destination, execution.path)
            mediaCheck(identity(destination) == identity && identity(execution.path) == identity,
                MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            openFile(destination, false).use { mediaCheck(fullHash(it, copy.totalBytes) { execution.publicationAllowed.get() } == incremental, MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY) }
            local = admission.recaptureLocal(local)
            admission.refresh(SelectionCapture.AccountLocal(input.capture.fence, local), null)
            admission.withCurrent(MediaAdmissionRequest(SelectionCapture.AccountLocal(input.capture.fence, local),
                null, MediaAdmissionStep.USE, null, input.original, null, null)) { }
            mediaCheck(execution.publicationAllowed.get(), MediaCode.CANCELLED, MediaStage.PUBLISH)
            val committed = journal.commitPublication(input.accountLease, MediaId.random(), publicationId, marker.record.revision, local)
            mediaCheck(committed.copy.publicationState == "COMMITTED" && committed.ownership.state == "OWNED" &&
                committed.copy.publicationId == publicationId && committed.ownership.publicationId == publicationId,
                MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            // The exact temp alias is removable only after the confirmed SQL completion.
            if (identity(execution.path) == identity && identity(destination) == identity) Files.delete(execution.path)
            reservations.remove(input.accountLease.scopeId to input.intent.intentId)
            reservationPaths.remove(input.accountLease.scopeId to input.intent.intentId)
            current.compareAndSet(execution, null)
            committed
        } catch (failure: Exception) {
            execution.failed = true
            marker?.let { prepared ->
                try { journal.markPublicationUncertain(input.accountLease, MediaId.random(), prepared.publicationId, prepared.record.revision) }
                catch (_: Exception) { /* Original marker and both aliases remain owned. */ }
            }
            throw if (failure is MediaSourceException) failure else mediaFailure(MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
        } finally { publicationPending.set(false) }
    }

    suspend fun closeAttempt(capture: RemoteMediaTransferCapture): Unit = writer.withLock {
        val execution = current.get() ?: return@withLock
        mediaCheck(execution.input.transfer == capture && execution.settled && !writePending.get() && !publicationPending.get(),
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        execution.channel.close()
        current.compareAndSet(execution, null)
    }

    /** Explicit exceptional-process reconciliation; preserves the recorded prefix and all bytes. */
    suspend fun reconcileFailedSource(source: Wire.SourceKey): Unit = writer.withLock {
        val execution = current.get()?.takeIf { it.input.transfer.source == source } ?: return@withLock
        mediaCheck(!writePending.get() && !publicationPending.get(), MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        execution.retired.set(true); execution.publicationAllowed.set(false)
        execution.channel.close()
        mediaCheck(!execution.channel.isOpen, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
        execution.failed = true; execution.settled = true
        current.compareAndSet(execution, null)
    }

    /** Explicit reviewed materialization only; consumes an already owned standalone read lease. */
    suspend fun copySelectedSource(capture: SelectionCapture.AccountLocal, selected: AccountLease,
        selectionId: MediaId, reviewRevision: Long, localTrackId: MediaId,
        source: MediaLocalReadLease): AccountMediaCopyView = writer.withLock {
        requireLease(capture.account, selected)
        DesktopMediaSourcePolicy.requireLocalGuard(capture.account, downloaded = false)
        mediaCheck(source.ownership == null && source.representation.kind == MediaRepresentationKind.INDEPENDENT_LOCAL &&
            current.get() == null && !writePending.get() && !publicationPending.get(), MediaCode.OWNER_STALE, MediaStage.PUBLISH)
        val value = source.representation
        val copyId = MediaId.random(); val locator = MediaId.random(); val publication = MediaId.random()
        // EXPLICIT_SOURCE_COPY has no download prefix record. Its exact publication locator
        // names the private pre-link file too, so recovery never needs an orphan scan.
        val prefixId = locator
        val temporary = partial.resolve("${prefixId.value}.part")
        val destination = retainedPath(locator, value.format.suffix)
        reservations[selected.scopeId to selectionId] = value.totalBytes
        reservationPaths[selected.scopeId to selectionId] = temporary
        checkSpace(0)
        var prepared = false
        try {
            source.verifyCurrentFileIdentityAndIntegrity()
            val inputPath = source.withManagedFile { Path.of(it.managedPath) }
            mediaCheck(!Files.isSymbolicLink(inputPath) && inputPath == inputPath.toRealPath(NOFOLLOW_LINKS),
                MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
            FileChannel.open(inputPath, READ, NOFOLLOW_LINKS).use { input ->
                FileChannel.open(temporary, setOf(CREATE_NEW, READ, WRITE, NOFOLLOW_LINKS),
                    PosixFilePermissions.asFileAttribute(FILE_MODE)).use { output ->
                    mediaCheck(input.size() == value.totalBytes, MediaCode.REPRESENTATION_CHANGED, MediaStage.VERIFY)
                    var offset = 0L
                    while (offset < value.totalBytes) {
                        admission.refresh(capture, null)
                        admission.withCurrent(MediaAdmissionRequest(capture, null, MediaAdmissionStep.USE, null, null, null, null)) { }
                        source.verifyCurrentFileIdentityAndIntegrity()
                        val bytes = readExact(input, offset, minOf(262_144, value.totalBytes - offset).toInt())
                        try {
                            checkSpace(bytes.size.toLong())
                            val buffer = ByteBuffer.wrap(bytes)
                            while (buffer.hasRemaining()) mediaCheck(output.write(buffer, offset + buffer.position()) > 0,
                                MediaCode.STORAGE_FULL, MediaStage.WRITE)
                            offset += bytes.size
                        } finally { bytes.fill(0) }
                    }
                    output.force(true)
                    val actual = fullHash(output, value.totalBytes)
                    mediaCheck(actual == value.digestSha256 && fullHash(input, value.totalBytes) == actual,
                        MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY)
                }
            }
            val digest = openFile(temporary, false).use { fullHash(it, value.totalBytes) }
            val record = record(selected)
            val copy = MediaCopy(record, copyId, localTrackId, null, null, digest, value.totalBytes, value.mediaType,
                value.format.suffix, "USER_DOWNLOAD", "VERIFIED", "PREPARED", locator, 1, publication)
            val ownership = MediaCopyOwnership(record, copyId, capture.account.localOwner, capture.account.kOwnerReference,
                null, capture.account.kAuthorityRevision, "EXPLICIT_SOURCE_COPY", selectionId, 1, publication, "PREPARED")
            val attachment = MediaCopyAttachment(record, copyId, null, null, null, null, null, null, null, "DETACHED")
            val marker = MediaPublication(record, publication, copyId, null, null, null, null, null,
                capture.account.localGuard.revision, null, null, "DETACHED", reviewRevision, locator, null, 1, "PREPARED")
            // J validates the exact MATERIALIZED/PARTIAL source selection and review revision.
            admission.refresh(capture, null)
            journal.preparePublication(selected, MediaId.random(), AccountMediaCompletion(copy, ownership, attachment, marker))
            prepared = true
            val identity = identity(temporary)
            mediaCheck(Files.getFileStore(temporary) == Files.getFileStore(retained), MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            Files.createLink(destination, temporary)
            mediaCheck(identity(destination) == identity && identity(temporary) == identity,
                MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            admission.refresh(capture, null)
            admission.withCurrent(MediaAdmissionRequest(capture, null, MediaAdmissionStep.USE, null, null, null, null)) { }
            val committed = journal.commitPublication(selected, MediaId.random(), publication, 1, capture.account)
            if (identity(destination) == identity && identity(temporary) == identity) Files.delete(temporary)
            reservations.remove(selected.scopeId to selectionId)
            reservationPaths.remove(selected.scopeId to selectionId)
            committed
        } catch (failure: Exception) {
            if (prepared) try { journal.markPublicationUncertain(selected, MediaId.random(), publication, 1) }
                catch (_: Exception) { /* Both exact aliases and original owner remain retained. */ }
            throw failure
        }
    }

    /** No startup orphan sweep: only an exact J publication marker can identify recovery files. */
    suspend fun reconcilePublication(lease: AccountLease, publicationId: MediaId,
        local: AccountLocalCapture, acquisitionScope: MediaId = lease.scopeId): AccountMediaCopyView = writer.withLock {
        mediaCheck(current.get() == null && !writePending.get() && !publicationPending.get(), MediaCode.CAPACITY_EXCEEDED, MediaStage.PUBLISH)
        val marker = journal.readPublication(lease, publicationId, acquisitionScope) ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.PUBLISH)
        val view = journal.readCopy(lease, marker.copyId, acquisitionScope) ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.PUBLISH)
        val currentLocal = admission.recaptureLocal(local)
        requireLease(currentLocal, lease)
        mediaCheck(marker.stage in setOf("PREPARED", "LINKED", "UNCERTAIN", "COMMITTED") &&
            view.copy.publicationId == marker.publicationId && view.copy.source?.sourceEvidenceId == marker.sourceEvidenceId &&
            view.copy.locatorId == marker.locatorId && view.ownership.localOwner == currentLocal.localOwner &&
            view.ownership.kOwnerRef == currentLocal.kOwnerReference,
            MediaCode.RECONCILIATION_REQUIRED, MediaStage.PUBLISH)
        DesktopMediaSourcePolicy.requireLocalGuard(currentLocal, view.ownership.acquisitionKind == "USER_DOWNLOAD")
        val fence = MediaSelectionFence(lease.bootId, MediaId.random(), 1, 1, 1, null, null, null, 1)
        val capture = SelectionCapture.AccountLocal(fence, currentLocal)
        admission.refresh(capture, null)
        val path = retainedPath(marker.locatorId, view.copy.formatSuffix)
        val temporary = partial.resolve("${(marker.prefixId ?: marker.locatorId).value}.part")
        if (!Files.exists(path, NOFOLLOW_LINKS)) {
            mediaCheck(marker.stage != "COMMITTED" && Files.exists(temporary, NOFOLLOW_LINKS),
                MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            val before = identity(temporary)
            openFile(temporary, false).use { mediaCheck(fullHash(it, view.copy.totalBytes) == view.copy.digestSha256 &&
                identity(temporary) == before, MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY) }
            mediaCheck(Files.getFileStore(temporary) == Files.getFileStore(retained), MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
            admission.refresh(capture, null)
            admission.withCurrent(MediaAdmissionRequest(capture, null, MediaAdmissionStep.USE, null, null, null, null)) {
                Files.createLink(path, temporary)
            }
            mediaCheck(identity(path) == before && identity(temporary) == before, MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
        }
        val retainedIdentity = identity(path)
        openFile(path, false).use { mediaCheck(fullHash(it, view.copy.totalBytes) == view.copy.digestSha256,
            MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY) }
        mediaCheck(identity(path) == retainedIdentity, MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
        val refreshed = admission.recaptureLocal(currentLocal)
        admission.refresh(SelectionCapture.AccountLocal(fence, refreshed), null)
        val committed = if (marker.stage == "COMMITTED") view else
            journal.commitPublication(lease, MediaId.random(), publicationId, marker.record.revision, refreshed, acquisitionScope)
        mediaCheck(committed.copy.publicationState == "COMMITTED" && committed.ownership.state == "OWNED",
            MediaCode.PUBLICATION_UNCERTAIN, MediaStage.PUBLISH)
        if (Files.exists(temporary, NOFOLLOW_LINKS) && identity(temporary) == retainedIdentity && identity(path) == retainedIdentity)
            Files.delete(temporary)
        committed
    }

    /** Must be bound to the real selected J lease by the runtime adapter. No synthetic lease. */
    lateinit var selectedLease: (AccountLocalCapture) -> AccountLease

    suspend fun ownedCopies(local: AccountLocalCapture, afterScope: MediaId?, afterCopy: MediaId?): List<AccountMediaCopyView> {
        val selected = selectedLease(local)
        requireLease(local, selected)
        val page = journal.readOwnedCopies(selected, afterScope, afterCopy, 100)
        synchronized(stateGate) {
            visibleCopyScopes.clear()
            page.forEach { visibleCopyScopes[it.copy.copyId] = it.copy.record.scopeId }
        }
        return page
    }

    override fun startLocalRead(capture: SelectionCapture, intent: SourceIntent,
        admission: MediaLiveAdmission): MediaOwnedWork<MediaLocalLookup> = work.start { cancelled ->
        val local = (capture as? SelectionCapture.AccountLocal)?.account
            ?: return@start MediaLocalLookup.Missing(LocalAvailability.ABSENT)
        val lease = selectedLease(local)
        requireLease(local, lease)
        val candidates = when (intent) {
            is SourceIntent.AccountCopy -> listOfNotNull(journal.readCopy(lease, intent.copyId,
                intent.acquisitionScopeId ?: synchronized(stateGate) { visibleCopyScopes[intent.copyId] } ?: lease.scopeId))
            // Empty means J exhausted its raw scan. Uncertainty/capacity propagates as a refusal.
            is SourceIntent.VaultPlayback -> journal.readTrackCopies(lease, intent.projection.localTrackRefId, null, 1)
            else -> emptyList()
        }
        val view = candidates.firstOrNull { it.copy.integrityState == "VERIFIED" && it.copy.publicationState == "COMMITTED" &&
            it.ownership.state == "OWNED" && (intent is SourceIntent.AccountCopy || it.attachment.state == "ATTACHED") }
            ?: return@start MediaLocalLookup.Missing(LocalAvailability.ABSENT)
        val copy = view.copy; val ownership = view.ownership
        DesktopMediaSourcePolicy.requireLocalGuard(local, ownership.acquisitionKind == "USER_DOWNLOAD")
        mediaCheck(ownership.localOwner == local.localOwner && ownership.kOwnerRef == local.kOwnerReference &&
            ownership.completionRevision == copy.completionRevision && ownership.publicationId == copy.publicationId &&
            (!cancelled.get()), MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        val path = retainedPath(copy.locatorId, copy.formatSuffix)
        synchronized(stateGate) { readLeases[copy.copyId] = (readLeases[copy.copyId] ?: 0) + 1 }
        var channel: FileChannel? = null
        try {
            channel = openFile(path, false)
            val identity = identity(path)
            mediaCheck(fullHash(channel, copy.totalBytes) { !cancelled.get() } == copy.digestSha256 && identity(path) == identity,
                MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY)
            val original = copy.source?.let { journal.readEvidence(lease, it.sourceEvidenceId, copy.record.scopeId) }
            mediaCheck(copy.source == null || original != null, MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
            val projection = if (intent is SourceIntent.VaultPlayback && view.attachment.state == "ATTACHED")
                journal.projection(lease, requireNotNull(view.attachment.localTrackRefId)) else null
            if (intent is SourceIntent.VaultPlayback) mediaCheck(projection == intent.projection &&
                projection?.projectionRevision == view.attachment.projectionRevision, MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
            val publication = journal.readPublication(lease, copy.publicationId, copy.record.scopeId)
                ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
            mediaCheck(publication.stage == "COMMITTED" && publication.copyId == copy.copyId,
                MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER)
            val readback = MediaOwnedCopyReadback(copy.copyId, local,
                if (ownership.acquisitionKind == "USER_DOWNLOAD") MediaCopyAcquisition.USER_DOWNLOAD else MediaCopyAcquisition.EXPLICIT_SOURCE_COPY,
                ownership.acquisitionId, ownership.acquisitionId, publication.materializationRevision, copy.completionRevision, ownership.completionRevision,
                copy.publicationId, ownership.publicationId, true, true, false, original, projection, null, copy.record.scopeId)
            this.admission.refresh(capture, intent)
            val owned = CopyLease(copy, readback, channel, path, identity)
            work.retainLease(owned)
            MediaLocalLookup.Found(owned)
        } catch (failure: Exception) {
            channel?.close(); releaseRead(copy.copyId)
            throw failure
        }
    }

    private inner class CopyLease(val copy: MediaCopy, override val ownership: MediaOwnedCopyReadback,
        val channel: FileChannel, val path: Path, val identity: String) : MediaLocalReadLease {
        private val retired = AtomicBoolean(false)
        private val released = AtomicBoolean(false)
        private val terminal = kotlinx.coroutines.CompletableDeferred<MediaHandleRelease>()
        override val copyId: MediaId = copy.copyId
        override val representation = MediaRepresentation(copy.digestSha256, copy.totalBytes, copy.mediaType,
            MediaFormat.entries.single { it.suffix == copy.formatSuffix }, if (copy.source == null) MediaRepresentationKind.INDEPENDENT_LOCAL else MediaRepresentationKind.VAULT_CONTENT)
        override val leaseRevision: Long = copy.completionRevision
        override fun verifyCurrentFileIdentityAndIntegrity(): Unit = mediaCheck(!retired.get() && !released.get() && channel.isOpen,
            MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        override fun <T> withManagedFile(action: (MediaRuntimeSource.ManagedFile) -> T): T {
            verifyCurrentFileIdentityAndIntegrity()
            return action(MediaRuntimeSource.ManagedFile(path.toString(), identity))
        }
        override fun retire(): Unit { retired.set(true) }
        override suspend fun awaitReleased(): MediaHandleRelease {
            retire()
            if (released.compareAndSet(false, true)) {
                val actual = try {
                    channel.close()
                    mediaCheck(!channel.isOpen, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                    releaseRead(copyId)
                    MediaHandleRelease.RELEASED
                } catch (_: Exception) { MediaHandleRelease.FAILED_RETAINED }
                terminal.complete(actual)
            }
            return terminal.await()
        }
    }

    private class SettledWrite(override val transfer: RemoteMediaTransferCapture,
        override val sequence: Long, override val nextOffset: Long,
        override val disposition: Wire.Disposition) : RemoteMediaWriteWitness {
        override fun isSettled(): Boolean = true
    }

    private fun releaseRead(id: MediaId): Unit = synchronized(stateGate) {
        val count = readLeases[id] ?: return@synchronized
        if (count <= 1) readLeases.remove(id) else readLeases[id] = count - 1
    }
    private fun retainedPath(locator: MediaId, suffix: String): Path {
        mediaCheck(suffix in Wire.SUFFIXES, MediaCode.UNSUPPORTED_MEDIA, MediaStage.LOCAL_OWNER)
        return retained.resolve("${locator.value}.$suffix")
    }
    private fun requireLease(local: AccountLocalCapture, lease: AccountLease): Unit = mediaCheck(
        local.storeId == lease.storeId && local.scopeId == lease.scopeId && local.activationRevision == lease.activationRevision &&
            local.publicationCommitId == lease.bindingCommit && local.kAuthorityRevision == lease.authorityRevision,
        MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
    private fun record(lease: AccountLease): AccountMediaRecord = AccountMediaRecord(lease.storeId, lease.scopeId, 1)
    private fun checkSpace(incoming: Long): Unit {
        val volume = Files.getFileStore(root)
        val quota = minOf(4L * GIB, maxOf(256L * MIB, volume.totalSpace / 4))
        val reserve = maxOf(256L * MIB, minOf(512L * MIB, maxOf(64L * MIB, volume.totalSpace / 20)))
        var bytes = 0L; var count = 0
        val chargedIdentities = HashSet<String>()
        for (directory in listOf(partial, retained)) Files.newDirectoryStream(directory).use { stream ->
            for (path in stream) {
                mediaCheck(++count <= 200_000, MediaCode.CAPACITY_EXCEEDED, MediaStage.WRITE)
                requireFile(path)
                val identity = identity(path)
                if (chargedIdentities.add(identity)) bytes = Math.addExact(bytes, Files.size(path))
            }
        }
        // Charge retained/unknown files plus each reservation's remaining unwritten capacity.
        val remaining = reservations.entries.fold(0L) { total, (key, reserved) ->
            val path = reservationPaths[key]
            val present = if (path != null && Files.exists(path, NOFOLLOW_LINKS)) {
                requireFile(path); Files.size(path)
            } else 0L
            Math.addExact(total, maxOf(0, reserved - present))
        }
        val charge = Math.addExact(bytes, remaining)
        mediaCheck(charge <= quota && incoming <= volume.usableSpace - reserve,
            MediaCode.STORAGE_FULL, MediaStage.WRITE)
    }
    private fun requireFile(path: Path): Unit {
        mediaCheck(path.parent == partial || path.parent == retained, MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
        mediaCheck(Files.isRegularFile(path, NOFOLLOW_LINKS) && !Files.isSymbolicLink(path) &&
            Files.getOwner(path, NOFOLLOW_LINKS) == fileOwner && Files.getPosixFilePermissions(path, NOFOLLOW_LINKS) == FILE_MODE,
            MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
    }
    private fun openFile(path: Path, writable: Boolean): FileChannel {
        requireFile(path)
        return FileChannel.open(path, if (writable) setOf(READ, WRITE, NOFOLLOW_LINKS) else setOf(READ, NOFOLLOW_LINKS))
    }
    private fun identity(path: Path): String {
        requireFile(path)
        val attributes = Files.readAttributes(path, "unix:dev,ino", NOFOLLOW_LINKS)
        return "${(attributes["dev"] as Number).toLong()}:${(attributes["ino"] as Number).toLong()}"
    }
    private fun fullHash(channel: FileChannel, expected: Long, allowed: () -> Boolean = { true }): String {
        mediaCheck(channel.size() == expected, MediaCode.REPRESENTATION_CHANGED, MediaStage.VERIFY)
        val hash = MessageDigest.getInstance("SHA-256")
        var offset = 0L
        while (offset < expected) {
            mediaCheck(allowed() && !closed.get() && !admission.isRetired(), MediaCode.CANCELLED, MediaStage.VERIFY)
            val bytes = readExact(channel, offset, minOf(262_144L, expected - offset).toInt())
            try { hash.update(bytes); offset += bytes.size } finally { bytes.fill(0) }
        }
        mediaCheck(channel.size() == expected, MediaCode.REPRESENTATION_CHANGED, MediaStage.VERIFY)
        return hex(hash.digest())
    }
    private fun readExact(channel: FileChannel, offset: Long, length: Int): ByteArray {
        val bytes = ByteArray(length); val buffer = ByteBuffer.wrap(bytes)
        try {
            while (buffer.hasRemaining()) mediaCheck(channel.read(buffer, offset + buffer.position()) > 0,
                MediaCode.SOURCE_UNKNOWN, MediaStage.VERIFY)
            return bytes
        } catch (failure: Exception) { bytes.fill(0); throw failure }
    }
    suspend fun close(): MediaHandleRelease = withContext(NonCancellable) {
        closed.set(true); current.get()?.retired?.set(true)
        writer.withLock {
            if (lifetimeReleased) return@withLock MediaHandleRelease.RELEASED
            if (current.get() != null || publicationPending.get() || writePending.get() || synchronized(stateGate) { readLeases.isNotEmpty() })
                return@withLock MediaHandleRelease.FAILED_RETAINED
            if (lifetimeLock.isValid) lifetimeLock.release()
            lockChannel.close(); lifetimeReleased = true; MediaHandleRelease.RELEASED
        }
    }

    companion object {
        private const val MIB = 1_048_576L
        private const val GIB = 1_073_741_824L
        private val FILE_MODE = PosixFilePermissions.fromString("rw-------")
        private val DIRECTORY_MODE = PosixFilePermissions.fromString("rwx------")
        fun open(journal: DesktopAccountMedia, work: DesktopMediaWorkOwner,
            admission: DesktopMediaAccountAdmission, attempt: DesktopMediaOpenAttempt): DesktopDownloadOwner {
            mediaCheck(System.getProperty("os.name") == "Mac OS X", MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            val home = Path.of(System.getProperty("user.home")).toAbsolutePath().normalize()
            mediaCheck(home == home.toRealPath(NOFOLLOW_LINKS), MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
            val root = home.resolve("Library/Application Support/AutPlay/media/remote-media-v1")
            var cursor = home
            for (name in home.relativize(root)) {
                cursor = cursor.resolve(name)
                if (!Files.exists(cursor, NOFOLLOW_LINKS)) Files.createDirectory(cursor, PosixFilePermissions.asFileAttribute(DIRECTORY_MODE))
                mediaCheck(Files.isDirectory(cursor, NOFOLLOW_LINKS) && !Files.isSymbolicLink(cursor) &&
                    cursor == cursor.toRealPath(NOFOLLOW_LINKS) && Files.getOwner(cursor, NOFOLLOW_LINKS) == Files.getOwner(home, NOFOLLOW_LINKS),
                    MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
            }
            for (directory in listOf(root, root.resolve("partial"), root.resolve("retained"))) {
                if (!Files.exists(directory, NOFOLLOW_LINKS)) Files.createDirectory(directory, PosixFilePermissions.asFileAttribute(DIRECTORY_MODE))
                mediaCheck(Files.isDirectory(directory, NOFOLLOW_LINKS) && !Files.isSymbolicLink(directory) &&
                    Files.getPosixFilePermissions(directory, NOFOLLOW_LINKS) == DIRECTORY_MODE,
                    MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
            }
            val lockPath = root.resolve("media-owner.lock")
            val channel = FileChannel.open(lockPath, setOf(CREATE, READ, WRITE, NOFOLLOW_LINKS), PosixFilePermissions.asFileAttribute(FILE_MODE))
            attempt.channel = channel
            try {
                mediaCheck(Files.getPosixFilePermissions(lockPath, NOFOLLOW_LINKS) == FILE_MODE && Files.getOwner(lockPath, NOFOLLOW_LINKS) == Files.getOwner(home, NOFOLLOW_LINKS),
                    MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
                val lock = channel.tryLock() ?: throw mediaFailure(MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
                attempt.lock = lock
                return DesktopDownloadOwner(root, channel, lock, journal, work, admission)
            } catch (failure: Exception) { channel.close(); throw failure }
        }
        private fun digest(bytes: ByteArray): String = hex(MessageDigest.getInstance("SHA-256").digest(bytes))
        private fun hex(bytes: ByteArray): String = try { bytes.joinToString("") { "%02x".format(it.toInt() and 255) } } finally { bytes.fill(0) }
    }
}
