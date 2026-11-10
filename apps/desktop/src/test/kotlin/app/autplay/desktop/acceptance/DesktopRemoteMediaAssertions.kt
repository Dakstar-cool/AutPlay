package app.autplay.desktop.acceptance

import app.autplay.desktop.media.MacRemoteMediaBridge
import app.autplay.desktop.media.RemoteMediaCodec
import app.autplay.desktop.media.RemoteMediaFailureDelivery
import app.autplay.desktop.media.RemoteMediaHostAuthority
import app.autplay.desktop.media.RemoteMediaPackageIdentity
import app.autplay.desktop.media.RemoteMediaPackageWitness
import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import app.autplay.desktop.media.RemoteMediaRelease
import app.autplay.desktop.media.RemoteMediaReleaseKind
import app.autplay.desktop.media.RemoteMediaSourceRelease
import app.autplay.desktop.media.RemoteMediaTransferCapture
import app.autplay.desktop.media.RemoteMediaWriteWitness
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.lang.reflect.InvocationTargetException
import java.nio.ByteBuffer
import java.nio.file.Path
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.CountDownLatch
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Future assertions only: no @Test, composite call, launch, crypto vector, or execution evidence.
 * A later authorized gate can call this from an existing composite without adding an entry point.
 * These codec sentinels do not prove native decoding, account authority, or a media relationship.
 */
internal fun assertFutureRemoteMediaWireSentinels(): Unit {
    fun framed(body: ByteArray): ByteArray = ByteArrayOutputStream().also { bytes ->
        DataOutputStream(bytes).use { output -> output.writeInt(body.size); output.write(body) }
    }.toByteArray()
    fun json(value: String): ByteArray = framed(byteArrayOf(1) + value.toByteArray(Charsets.UTF_8))
    fun decode(frame: ByteArray): Wire.Incoming = DataInputStream(ByteArrayInputStream(frame)).use { input ->
        val first = input.read()
        check(first >= 0)
        RemoteMediaCodec.read(input, first).also { check(input.read() == -1) }
    }
    fun refused(frame: ByteArray): Unit {
        val result = runCatching { decode(frame) }
        (result.getOrNull() as? Wire.DataFrame)?.close()
        check(result.isFailure) { "REMOTE_MEDIA_INVALID_FRAME_ACCEPTED" }
    }

    check(decode(json("""{"v":1,"type":"hello","protocol":"autplay.remote-media","maxFrameBytes":65536}""")) is Wire.Hello)
    check(decode(json("""{"v":1,"type":"ready"}""")) is Wire.Ready)
    val run = "00000000-0000-4000-8000-000000000001"
    val network = decode(json("""{"v":1,"type":"network","runId":"$run","revision":1,"status":"UNKNOWN","expensive":null,"constrained":null}"""))
    check(network is Wire.Network && network.expensive == null && network.constrained == null)

    listOf(
        """{"v":1,"v":1,"type":"ready"}""",
        """{"v":1,"\u0076":1,"type":"ready"}""",
        """{"v":1,"type":"ready","unknown":null}""",
        """{"v":1,"type":{"type":"ready"}}""",
        """{"v":1,"type":["ready"]}""",
        """{"v":true,"type":"ready"}""",
        """{"v":"1","type":"ready"}""",
        """{"v":1.0,"type":"ready"}""",
        """{"v":1e0,"type":"ready"}""",
        """{"v":01,"type":"ready"}""",
        """{"v":-0,"type":"ready"}""",
        """{"v":1,"type":"ready"}{}""",
        """{"v":1,"type":"\ud800"}""",
        """{"v":1,"type":"\u+001"}""",
        """{"v":1,"type":"hello","protocol":"autplay.local-audio","maxFrameBytes":65536}""",
        """{"v":1,"type":"hello","protocol":"autplay.remote-media","maxFrameBytes":65537}""",
        """{"v":1,"type":"ack","id":9007199254740992,"outcome":"ADMITTED","code":null}""",
        """{"v":1,"type":"ack","id":1,"outcome":"DRAINED"}""",
        """{"v":1,"type":"ack","id":1,"outcome":"DRAINED","code":"DRAIN_UNCONFIRMED"}""",
        """{"v":1,"type":"network","runId":"00000000-0000-4000-8000-00000000000A","revision":1,"status":"UNKNOWN","expensive":null,"constrained":null}""",
        """{"v":1,"type":"network","runId":"$run","revision":1,"status":"UNKNOWN","expensive":null}""",
    ).forEach { refused(json(it)) }
    refused(framed(byteArrayOf(1, 0xff.toByte())))
    refused(framed(byteArrayOf(3)))
    refused(byteArrayOf(0, 0, 0, 0))
    refused(ByteBuffer.allocate(4).putInt(Wire.MAX_FRAME + 1).array())

    fun binary(attempt: Long, sequence: Long, offset: Long, size: Int): ByteArray {
        val id = UUID.fromString(run)
        return framed(ByteBuffer.allocate(41 + size).put(2).putLong(id.mostSignificantBits)
            .putLong(id.leastSignificantBits).putLong(attempt).putLong(sequence).putLong(offset)
            .put(ByteArray(size)).array())
    }
    val maximal = decode(binary(1, 1, 0, Wire.MAX_DATA)) as Wire.DataFrame
    try {
        check(maximal.transferId == UUID.fromString(run) && maximal.attempt == 1L && maximal.sequence == 1L)
        check(maximal.offset == 0L && maximal.size == Wire.MAX_DATA)
        maximal.withBytes { check(it.isReadOnly && it.remaining() == Wire.MAX_DATA) }
    } finally { maximal.close() }
    refused(binary(0, 1, 0, 1))
    refused(binary(1, 0, 0, 1))
    refused(binary(Wire.MAX_INTEGER + 1, 1, 0, 1))
    refused(binary(1, 1, -1, 1))
    refused(binary(1, 1, 0, 0))
    refused(binary(1, 1, 0, Wire.MAX_DATA + 1))
    refused(binary(1, 1, Wire.MAX_BYTES - 1, 2))
}

/**
 * Authored only; deliberately uncalled. All process/pipe/file authority is synthetic and in memory.
 * Reflection stays in this future file so production gets no unsigned launcher/admission bypass.
 * Real package identity, native drain, media integrity and J/K integration remain separate gates.
 *
 * Repeated stale-check/async-ownership repair research (JDK17 primary sources, 2026-10-16):
 * 1. Choose final writer admission after both receipt encodings, with exact zero-credit settlement.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/io/OutputStream.html
 * 2. A held admission witness could serialize revocation, but creditIsCurrent supplies a Boolean,
 *    not a lease. Do not invent a witness or retain authority across a possibly blocked pipe write.
 * 3. Reject enqueue-only checks: queue/encoding waits separate that observation from dispatch.
 * 4. Reject cancel/drop of an emitted frame's receipt: cancellation does not prove actual IO ended.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/Future.html
 * 5. Choose owned callback accounting before scheduling and finally after return/throw. Shutdown,
 *    cancellation and process destruction cannot supply actual completion evidence.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/ExecutorService.html
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.html
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/lang/Process.html
 *
 * Recurrent failed-cleanup/recovery repair, five primary-source remedies compared, 2026-10-16:
 * 1. CHOSEN: separate diagnostic delivery from actual work settlement. The private JDK17 executor
 *    is exactly one worker, queue28, AbortPolicy, no caller-run/discard/custom hooks. Its execute
 *    rejection leaves no task; accepted tasks/decisions/writes retain counts until actual finally.
 *    A callback-thrown RejectedExecutionException is THREW on that worker, never submission rejection.
 *    FAILED_RETAINED + REJECTED remains visible through release, even after explicit slot recovery.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/Executor.html
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.AbortPolicy.html
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/RejectedExecutionException.html
 * 2. An explicitly owned bounded deferred notification retry could preserve delivery if required;
 *    it would need its own pending obligation/deadline and cannot be detached fire-and-forget.
 *    Not selected: this contract permits visible failed delivery plus explicit owner reconciliation.
 * 3. Reject permanent false-settlement tombstones once no real work remains: they strand a dead
 *    process slot even though AbortPolicy rejected the notification before it could run.
 * 4. Reject optimistic ordinary success: rejected/thrown delivery and confirmed forced exit prove
 *    neither delivered notification nor native DRAINED, server release, J commit or authority.
 * 5. Reject timeout/cancel/shutdown/destroy as settlement. Process.onExit and actual pipe/callback/
 *    write completion remain required; unknown scheduling retains ownership. No retry was added.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/ThreadPoolExecutor.html
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/lang/Process.html
 *
 * Graceful publication ordering research, three compatible JDK17 remedies, 2026-10-16:
 * 1. CHOSEN: after the existing terminal claim, finish fast executor cleanup and the one-time
 *    graceful slot CAS before completing any public result. Non-async dependents can run inline;
 *    program order supplies the prerequisite without another future or a lock across callbacks.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/CompletableFuture.html
 *    https://docs.oracle.com/javase/specs/jls/se17/html/jls-17.html#jls-17.4.5
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/atomic/AtomicBoolean.html
 * 2. Expose only a derived whenComplete cleanup stage, which completes after its action returns.
 *    All public/recovery observations would have to use that stage, hiding the raw precursor.
 *    Not selected: it adds a completion boundary to the existing exact-result recovery contract.
 *    https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/concurrent/CompletionStage.html
 * 3. An allOf prerequisite barrier could join separately modelled terminal-owner futures before
 *    cleanup and public completion. Not selected: it duplicates the current settlement accounting;
 *    the remaining cleanup is synchronous, and failed settlement must still retain its slot.
 *    The CompletableFuture reference above defines allOf. Sibling continuations have no mutual
 *    ordering guarantee; executor delay, retry/spin and completion under a callback lock are absent.
 */
internal fun assertFutureRemoteMediaBridgeOwnershipSentinels(): Unit {
    val type = MacRemoteMediaBridge::class.java
    fun get(target: Any, name: String): Any? = target.javaClass.getDeclaredField(name).apply { isAccessible = true }.get(target)
    fun set(target: Any, name: String, value: Any?): Unit = target.javaClass.getDeclaredField(name).apply { isAccessible = true }.set(target, value)
    fun call(target: Any, name: String, vararg arguments: Any?): Any? {
        val method = target.javaClass.declaredMethods.single { it.name == name && it.parameterCount == arguments.size }
        return try { method.apply { isAccessible = true }.invoke(target, *arguments) }
        catch (failure: InvocationTargetException) { throw failure.targetException }
    }
    fun id(last: Int): UUID = UUID.fromString("00000000-0000-4000-8000-${last.toString().padStart(12, '0')}")
    fun frame(body: ByteArray): ByteArray = ByteArrayOutputStream().also { bytes ->
        DataOutputStream(bytes).use { it.writeInt(body.size); it.write(body) }
    }.toByteArray()
    fun control(text: String): ByteArray = frame(byteArrayOf(1) + text.toByteArray(Charsets.UTF_8))
    fun payload(transfer: UUID, sequence: Long, offset: Long): ByteArray = frame(ByteBuffer.allocate(42)
        .put(2).putLong(transfer.mostSignificantBits).putLong(transfer.leastSignificantBits)
        .putLong(1).putLong(sequence).putLong(offset).put(7).array())
    fun text(bytes: ByteArray): String = DataInputStream(ByteArrayInputStream(bytes)).use {
        val length = it.readInt(); check(length == bytes.size - 4 && it.readUnsignedByte() == 1)
        String(it.readNBytes(length - 1), Charsets.UTF_8)
    }
    fun receipt(value: String, sequence: Long, offset: Long, credit: Int, disposition: Wire.Disposition): Unit {
        check(value.contains("\"type\":\"dataConsumed\""))
        check(value.contains("\"sequence\":$sequence,"))
        check(value.contains("\"nextOffset\":$offset,"))
        check(value.contains("\"creditBytes\":$credit,"))
        check(value.contains("\"disposition\":\"${disposition.name}\""))
    }
    fun witness(capture: RemoteMediaTransferCapture, data: Wire.DataFrame, outcome: Wire.Disposition): RemoteMediaWriteWitness =
        object : RemoteMediaWriteWitness {
            override val transfer: RemoteMediaTransferCapture = capture
            override val sequence: Long = data.sequence
            override val nextOffset: Long = data.offset + if (outcome == Wire.Disposition.WRITTEN) data.size else 0
            override val disposition: Wire.Disposition = outcome
            override fun isSettled(): Boolean = true
        }
    class Child : Process() {
        val exit = CompletableFuture<Process>()
        var status: Int? = null
        var destroyRequests: Int = 0
        override fun getOutputStream(): OutputStream = OutputStream.nullOutputStream()
        override fun getInputStream(): InputStream = InputStream.nullInputStream()
        override fun getErrorStream(): InputStream = InputStream.nullInputStream()
        override fun waitFor(): Int { exit.join(); return exitValue() }
        override fun exitValue(): Int = status ?: throw IllegalThreadStateException()
        override fun destroy(): Unit { destroyRequests++ }
        override fun destroyForcibly(): Process { destroy(); return this }
        override fun isAlive(): Boolean = !exit.isDone
        override fun onExit(): CompletableFuture<Process> = exit
        fun finish(code: Int): Unit { if (status == null) { status = code; exit.complete(this) } }
    }
    class Host : RemoteMediaHostAuthority {
        var creditAllowed: Boolean = true
        var creditThrows: Boolean = false
        var writes: Int = 0
        var networks: Int = 0
        var failures: Int = 0
        var recoveryChecks: Int = 0
        var writeAction: ((Wire.DataFrame, RemoteMediaTransferCapture) -> CompletionStage<RemoteMediaWriteWitness>)? = null
        var failureAction: (() -> Unit)? = null
        var fenceAction: (() -> Unit)? = null
        var eventAction: ((Wire.Event) -> Unit)? = null
        var recoveryAction: ((UUID, RemoteMediaRelease) -> Boolean)? = null
        override fun commandIsCurrent(command: Wire.Command): Boolean = true
        override fun accessPublicationIsCurrent(command: Wire.ReplaceAccess, quiesced: Wire.AccessState): Boolean = true
        override fun audioLeaseIsCurrent(command: Wire.ActivateAudio): Boolean = true
        override fun intentIsCurrent(fence: Wire.IntentFence, wantsPlay: Boolean): Boolean = true
        override fun decideRead(request: Wire.ReadAdmission): CompletionStage<Wire.ReadResponse> = error("UNEXPECTED_READ")
        override fun readDecisionIsCurrent(request: Wire.ReadAdmission, response: Wire.ReadResponse): Boolean = true
        override fun write(frame: Wire.DataFrame, transfer: RemoteMediaTransferCapture): CompletionStage<RemoteMediaWriteWitness> {
            writes++
            return writeAction?.invoke(frame, transfer) ?: CompletableFuture.completedFuture(witness(transfer, frame, Wire.Disposition.WRITTEN))
        }
        override fun creditIsCurrent(transfer: RemoteMediaTransferCapture): Boolean {
            if (creditThrows) error("SYNTHETIC_QUOTA_UNAVAILABLE")
            return creditAllowed
        }
        override fun closeAdmission(code: Wire.Code): Unit { fenceAction?.invoke() }
        override fun event(event: Wire.Event): Unit { if (event is Wire.Network) networks++; eventAction?.invoke(event) }
        override fun failed(code: Wire.Code): Unit { failures++; failureAction?.invoke() }
        override fun exceptionalRecoveryIsCurrent(runId: UUID, release: RemoteMediaRelease): Boolean {
            recoveryChecks++; return recoveryAction?.invoke(runId, release) ?: false
        }
    }
    class Harness(ownsProcessSlot: Boolean = false, runSuffix: Int = 1) : AutoCloseable {
        val child = Child()
        val host = Host()
        val run = id(runSuffix)
        val key = Wire.SourceKey(run, id(2), 1)
        val transferId = id(3)
        val path = Path.of("synthetic-never-launched")
        val packageWitness = object : RemoteMediaPackageWitness {}
        val identity = object : RemoteMediaPackageIdentity {
            override fun verify(canonicalBundle: Path, canonicalExecutable: Path): RemoteMediaPackageWitness? = error("NO_REAL_LAUNCH")
            override fun stillMatches(witness: RemoteMediaPackageWitness, process: Process, canonicalExecutable: Path): Boolean =
                witness === packageWitness && process === child && canonicalExecutable == path
        }
        val policy = Wire.SetNetworkPolicy(1, false)
        val bridge: MacRemoteMediaBridge = if (ownsProcessSlot) MacRemoteMediaBridge.create(path, identity, host, 1, false, run)
            else type.declaredConstructors.single { !it.isSynthetic && it.parameterCount == 5 }.apply { isAccessible = true }
                .newInstance(path, identity, host, policy, run) as MacRemoteMediaBridge
        val lock: Any = get(bridge, "lock")!!
        val callbacks: ThreadPoolExecutor = get(bridge, "callbacks") as ThreadPoolExecutor
        val released: CompletableFuture<RemoteMediaRelease> = bridge.release.toCompletableFuture()
        init {
            // Most cases isolate the constructor. Recovery cases acquire the real slot through
            // create(), never start(), and may release it only through the required exact guard.
            if (!ownsProcessSlot) (get(bridge, "processSlotReleased") as AtomicBoolean).set(true)
            set(bridge, "process", child); set(bridge, "executable", path); set(bridge, "packageWitness", packageWitness)
            call(bridge, "observeProcessExit", child)
            event(Wire.Hello())
            RemoteMediaCodec.write(DataOutputStream(ByteArrayOutputStream()), Wire.helloFields())
            set(bridge, "helloWritten", true); event(Wire.Ready())
            val outgoing = type.declaredClasses.single { it.simpleName == "Outgoing" }.declaredConstructors
                .single { !it.isSynthetic && it.parameterCount == 5 }.apply { isAccessible = true }
                .newInstance(get(bridge, "initialPolicy"), CompletableFuture<Wire.Ack>(), null, null, null)
            synchronized(lock) { call(bridge, "offer", outgoing) }
            pump(); ack(Wire.AckOutcome.APPLIED)
            check(bridge.ready.toCompletableFuture().isDone && get(bridge, "failure") == null)
        }
        fun event(value: Wire.Event): Unit { call(bridge, "receiveEvent", value) }
        fun data(value: Wire.DataFrame): Unit { call(bridge, "receiveData", value) }
        fun pendingId(): Long = get(get(bridge, "pendingOrdinary")!!, "id") as Long
        fun ack(outcome: Wire.AckOutcome): Unit = event(Wire.Ack(pendingId(), outcome, null))
        fun pumpInto(output: DataOutputStream): Unit {
            val outgoing = synchronized(lock) { call(bridge, "takeSendable") }
            call(bridge, "writeOutgoing", output, outgoing)
        }
        fun pump(): String {
            val bytes = ByteArrayOutputStream(); pumpInto(DataOutputStream(bytes))
            return if (bytes.size() == 0) "" else text(bytes.toByteArray())
        }
        fun issue(command: Wire.Command, outcome: Wire.AckOutcome): Unit {
            val result = bridge.submit(command).toCompletableFuture()
            pump(); ack(outcome); check(result.get(5, TimeUnit.SECONDS).outcome == outcome)
        }
        fun drainHost(): Unit { callbacks.submit {}.get(5, TimeUnit.SECONDS) }
        fun sourceOwner(): Any = (get(bridge, "sources") as Map<*, *>)[key]!!
        fun freezeAuthorityClock(): Unit {
            val owner = sourceOwner()
            val absolute = get(get(owner, "deadline")!!, "absolute") as Long
            val deadline = type.declaredClasses.single { it.simpleName == "Deadline" }.declaredConstructors
                .single { !it.isSynthetic && it.parameterCount == 5 }.apply { isAccessible = true }
                .newInstance(Wire.Lifetime(absolute, 10_000), 10_000L, 0L, { absolute - 10_000 }, { 0L })
            // Same authority deadline and unchanged ten-second lease; BOTH clocks are synthetic.
            // No real-clock expiry may turn the fast positive control into a different failure.
            synchronized(lock) { set(owner, "deadline", deadline) }
        }
        @Suppress("UNCHECKED_CAST")
        fun sourceRelease(): CompletableFuture<RemoteMediaSourceRelease> = get(sourceOwner(), "drained") as CompletableFuture<RemoteMediaSourceRelease>
        fun installDownload(total: Long): Unit {
            val until = System.currentTimeMillis() + 10_000
            val lifetime = Wire.Lifetime(until, 10_000)
            issue(Wire.InstallSource(key, Wire.Purpose.INSPECT_DOWNLOAD, 1, id(4), id(5), "https://example.invalid",
                "https://example.invalid/api/v1/stream/audio-variants/${id(5)}", Wire.TransportMode.PLATFORM_PKI, 1, null,
                Wire.AccessBytes.takeOwnership("synthetic-unusable-token".toByteArray()), 0, 0, until + 60_000, 1, lifetime), Wire.AckOutcome.ADMITTED)
            // Syntactically valid opaque representation metadata; no digest/integrity is calculated.
            val representation = Wire.Representation("\"sha256-${"0".repeat(64)}\"", total, "audio/wav", "wav")
            event(Wire.Inspected(key, 1, 1, 0, 1, representation)); drainHost()
            issue(Wire.AuthorizeBytes(key, 2, representation, Wire.ResourceType.DOWNLOAD_INTENT, id(6), id(7), 1, null, lifetime), Wire.AckOutcome.APPLIED)
            issue(Wire.StartTransfer(key, transferId, 1, 1, 1, 0), Wire.AckOutcome.ADMITTED)
        }
        fun startCredit(): Unit { bridge.grantInitialCredit(transferId, 1); receipt(pump(), 0, 0, 1, Wire.Disposition.START) }
        fun end(offset: Long, outcome: Wire.TransferOutcome): Unit {
            event(Wire.TransferEnd(key, 2, transferId, 1, offset, outcome, if (outcome == Wire.TransferOutcome.CANCELLED) Wire.Code.CANCELLED else null))
            drainHost()
        }
        fun fail(): Unit { call(bridge, "fail", Wire.Code.DRAIN_UNCONFIRMED) }
        fun pipesEnded(): Unit {
            // The in-memory pump has returned, and the synthetic reader has no outstanding read.
            synchronized(lock) { set(bridge, "readerSettled", true); set(bridge, "writerSettled", true) }
            call(bridge, "settleRelease")
        }
        fun gracefulExit(): RemoteMediaRelease {
            bridge.close(); pump(); ack(Wire.AckOutcome.DRAINED)
            pipesEnded(); child.finish(0)
            return released.get(5, TimeUnit.SECONDS)
        }
        override fun close(): Unit {
            fail(); pipesEnded(); child.finish(1)
            (get(bridge, "timer") as ScheduledThreadPoolExecutor).shutdown()
            callbacks.shutdown()
            check(callbacks.awaitTermination(5, TimeUnit.SECONDS)) { "SYNTHETIC_HOST_WORK_STILL_OWNED" }
        }
    }
    class Script(private val frames: List<() -> ByteArray>, private val after: (Int) -> Unit) : InputStream() {
        private var index: Int = 0
        private var current: ByteArrayInputStream = ByteArrayInputStream(frames.first().invoke())
        override fun read(): Int {
            while (index < frames.size) {
                val byte = current.read()
                if (byte >= 0) return byte
                after(index++)
                if (index < frames.size) current = ByteArrayInputStream(frames[index].invoke())
            }
            return -1
        }
    }

    // The public DRAINED continuation constructs the successor before the completing call returns.
    // The in-memory pipe pump and synthetic child exit must settle first; no sleep or retry hides order.
    for (last in listOf("process", "pipes")) Harness(ownsProcessSlot = true).use { h ->
        val completingThread = Thread.currentThread()
        var successor: Harness? = null
        val replacement = h.bridge.release.thenApply { release ->
            check(Thread.currentThread() === completingThread && !Thread.holdsLock(h.lock))
            check(release.kind == RemoteMediaReleaseKind.DRAINED && release.code == null)
            check(release.processExitConfirmed && release.pipeReaderSettled && release.pipeWriterSettled && release.hostCallbacksSettled)
            check(release.failureDelivery == RemoteMediaFailureDelivery.NOT_REQUIRED)
            check((get(h.bridge, "timer") as ScheduledThreadPoolExecutor).isShutdown && h.callbacks.isShutdown)
            check((get(h.bridge, "processSlotReleased") as AtomicBoolean).get())
            Harness(ownsProcessSlot = true, runSuffix = 8).also { next ->
                successor = next
                check(!(get(next.bridge, "started") as AtomicBoolean).get())
                repeat(2) {
                    call(h.bridge, "settleRelease")
                    check(h.bridge.close().toCompletableFuture().getNow(null) === release)
                    check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 0)
                    check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
                }
            }
        }.toCompletableFuture()
        try {
            h.bridge.close(); h.pump(); h.ack(Wire.AckOutcome.DRAINED)
            check(!h.released.isDone && !replacement.isDone)
            check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
            if (last == "process") h.pipesEnded() else h.child.finish(0)
            check(!h.released.isDone && !replacement.isDone)
            check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
            if (last == "process") h.child.finish(0) else h.pipesEnded()
            val next = replacement.getNow(null) ?: error("SYNCHRONOUS_SUCCESSOR_NOT_CREATED")
            check(successor === next && h.released.getNow(null)?.kind == RemoteMediaReleaseKind.DRAINED)
            // Returning through the old settler must not free the newly occupied global slot.
            repeat(2) {
                call(h.bridge, "settleRelease")
                check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 0)
                check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
            }
            check(next.gracefulExit().kind == RemoteMediaReleaseKind.DRAINED)
        } finally { successor?.close() }
    }

    // Authored only: a bounded renewal withholds credit without losing the settled receipt,
    // original transfer capture, contiguous prefix or sequence. No native process is launched.
    for (mode in listOf("write", "unused", "initial-queued")) Harness().use { h ->
        h.installDownload(2)
        val write = CompletableFuture<RemoteMediaWriteWitness>()
        var writing: Wire.DataFrame? = null
        var capture: RemoteMediaTransferCapture? = null
        if (mode != "initial-queued") h.startCredit()
        if (mode == "write") {
            h.host.writeAction = { frame, owner -> writing = frame; capture = owner; write }
            h.data(Wire.DataFrame(h.transferId, 1, 1, 0, byteArrayOf(7))); h.drainHost()
        }
        val now = System.currentTimeMillis()
        h.issue(Wire.BeginAccessRenewal(h.key, 1, Wire.RenewalReason.PREEXPIRY, 2, 0, 0,
            now + 30_000, 30_000), Wire.AckOutcome.ADMITTED)
        h.host.creditAllowed = false // The old credential's temporary closed admission.
        val offset = if (mode == "write") 1L else 0L
        if (mode == "write") {
            write.complete(witness(capture!!, writing!!, Wire.Disposition.WRITTEN))
            receipt(h.pump(), 1, offset, 0, Wire.Disposition.WRITTEN)
            check(get(h.bridge, "failure") == null && get(h.sourceOwner(), "retiring") == false)
        } else if (mode == "initial-queued") {
            h.bridge.grantInitialCredit(h.transferId, 1)
        }
        // Native can quiesce before an unsent START; it cannot quiesce before a real frame receipt.
        h.event(Wire.AccessState(h.key, 2, false, 1, Wire.RenewalReason.PREEXPIRY, 2, 0, 0)); h.drainHost()
        if (mode == "initial-queued") check(h.pump().isEmpty())
        val original = get(h.bridge, "transfer")!!
        val originalCapture = get(original, "capture")
        check(get(original, "credit") == 0 && get(original, "frame") == null)
        check(get(original, "withheldCredit") == if (mode == "write") Wire.MAX_DATA else 1)
        val lifetime = Wire.Lifetime(System.currentTimeMillis() + 10_000, 10_000)
        h.issue(Wire.ReplaceAccess(h.key, 1, Wire.RenewalReason.PREEXPIRY, 2, 3, 0, 1, 0, 1,
            Wire.AccessBytes.takeOwnership("synthetic-unusable-successor".toByteArray()),
            lifetime.authorityExpiresAtMs + 60_000, lifetime), Wire.AckOutcome.ADMITTED)
        check(get(original, "credit") == 0 && get(original, "creditQueued") == false)
        h.host.creditAllowed = true
        h.event(Wire.AccessState(h.key, 3, true, 1, Wire.RenewalReason.PREEXPIRY, 3, 1, 1)); h.drainHost()
        val expectedCredit = if (mode == "write") Wire.MAX_DATA else 1
        receipt(h.pump(), 0, offset, expectedCredit, Wire.Disposition.START)
        check(get(h.bridge, "transfer") === original && get(original, "capture") === originalCapture)
        check(get(original, "sequence") == if (mode == "write") 1L else 0L)
        check(get(original, "withheldCredit") == 0 && get(original, "nextOffset") == offset)
        // Repeated host observation cannot grant a second credit slot in the applied epoch.
        call(h.bridge, "resumeTransferCredit", Wire.AccessState(h.key, 3, true, 1,
            Wire.RenewalReason.PREEXPIRY, 3, 1, 1))
        check(get(original, "creditQueued") == false && get(h.bridge, "failure") == null)
        h.host.writeAction = null
        h.data(Wire.DataFrame(h.transferId, 1, if (mode == "write") 2 else 1, offset, byteArrayOf(7))); h.drainHost()
        receipt(h.pump(), if (mode == "write") 2 else 1, offset + 1,
            if (mode == "write") 0 else Wire.MAX_DATA, Wire.Disposition.WRITTEN)
        check(h.host.writes == if (mode == "write") 2 else 1)
    }

    // Valid at enqueue, then revoked, quota refusal/throw, or expired BEFORE writer dispatch.
    for (mode in listOf("revoked", "quota", "quota-throw", "expired", "retired")) Harness().use { h ->
        h.installDownload(2); h.startCredit()
        val drained = h.sourceRelease()
        h.data(Wire.DataFrame(h.transferId, 1, 1, 0, byteArrayOf(7))); h.drainHost()
        check(h.host.creditAllowed && h.host.writes == 1 && !drained.isDone)
        when (mode) {
            "revoked", "quota" -> h.host.creditAllowed = false
            "quota-throw" -> h.host.creditThrows = true
            "expired" -> set(get(h.sourceOwner(), "deadline")!!, "absolute", System.currentTimeMillis())
            "retired" -> h.bridge.retire(h.key, Wire.RetireReason.CANCEL)
        }
        receipt(h.pump(), 1, 1, 0, Wire.Disposition.WRITTEN)
        check(get(get(h.bridge, "transfer")!!, "credit") == 0 && h.host.writes == 1)
        // Retirement is queued after the exact receipt; it can wait for native terminal ACK.
        check(h.pump().contains("\"op\":\"retireSource\""))
        h.end(1, Wire.TransferOutcome.CANCELLED); h.ack(Wire.AckOutcome.DRAINED)
        check(drained.get(5, TimeUnit.SECONDS).kind == RemoteMediaReleaseKind.DRAINED)
        check(h.gracefulExit().kind == RemoteMediaReleaseKind.DRAINED)
    }
    Harness().use { h ->
        h.installDownload(2); h.bridge.grantInitialCredit(h.transferId, 1)
        h.host.creditAllowed = false
        check(h.pump().isEmpty()) // No frame was emitted; never invent a WRITTEN or START-zero grant.
        check(h.pump().contains("\"op\":\"retireSource\""))
        h.end(0, Wire.TransferOutcome.CANCELLED); h.ack(Wire.AckOutcome.DRAINED)
        check(h.host.writes == 0 && h.gracefulExit().kind == RemoteMediaReleaseKind.DRAINED)
    }

    // Retirement ACK remains pending while a real synthetic file-owner future has not settled.
    // Test both a last-frame actual write and an actual retired discard, with no invented progress.
    for (disposition in listOf(Wire.Disposition.WRITTEN, Wire.Disposition.DISCARDED)) Harness().use { h ->
        h.installDownload(1); h.startCredit()
        val settled = CompletableFuture<RemoteMediaWriteWitness>()
        val data = Wire.DataFrame(h.transferId, 1, 1, 0, byteArrayOf(7))
        var captured: RemoteMediaTransferCapture? = null
        h.host.writeAction = { _, capture -> captured = capture; settled }
        h.data(data); h.drainHost()
        val drained = h.bridge.retire(h.key, Wire.RetireReason.CANCEL).toCompletableFuture()
        check(h.pump().contains("\"op\":\"retireSource\""))
        check(!drained.isDone && !h.released.isDone && get(h.bridge, "hostCallbacks") == 1)
        check((get(h.bridge, "queue") as Collection<*>).isEmpty()) // No receipt before actual completion.
        data.withBytes { check(it.remaining() == 1 && it.get().toInt() == 7) }
        h.host.creditAllowed = false
        settled.complete(witness(captured!!, data, disposition))
        val written = ByteArrayOutputStream()
        val enteredFlush = CountDownLatch(1); val finishFlush = CountDownLatch(1)
        val writerDone = CompletableFuture<Unit>()
        val sink = object : OutputStream() {
            override fun write(value: Int): Unit { written.write(value) }
            override fun write(bytes: ByteArray, offset: Int, length: Int): Unit { written.write(bytes, offset, length) }
            override fun flush(): Unit { enteredFlush.countDown(); check(finishFlush.await(5, TimeUnit.SECONDS)) }
        }
        val writer = Thread {
            try { h.pumpInto(DataOutputStream(sink)); writerDone.complete(Unit) }
            catch (failure: Throwable) { writerDone.completeExceptionally(failure) }
        }
        try {
            writer.start(); check(enteredFlush.await(5, TimeUnit.SECONDS))
            val offset = if (disposition == Wire.Disposition.WRITTEN) 1L else 0L
            receipt(text(written.toByteArray()), 1, offset, 0, disposition)
            h.end(offset, if (disposition == Wire.Disposition.WRITTEN) Wire.TransferOutcome.DELIVERED else Wire.TransferOutcome.CANCELLED)
            h.ack(Wire.AckOutcome.DRAINED)
            check(!drained.isDone && !h.released.isDone) // Native drain cannot settle unfinished pipe IO.
            check(get(h.sourceOwner(), "outbound") == 1 && get(h.bridge, "writingAt") != null)
            finishFlush.countDown(); writerDone.get(5, TimeUnit.SECONDS); writer.join(5_000); check(!writer.isAlive)
            check(drained.get(5, TimeUnit.SECONDS).sourcePipeWritesSettled)
            check(h.gracefulExit().kind == RemoteMediaReleaseKind.DRAINED)
        } finally { finishFlush.countDown(); writer.join(5_000) }
    }

    // Next data can arrive BEFORE the prior receipt's write/flush returns. Accounting must already
    // allow exactly that next frame without losing the first receipt's independent pipe ownership.
    Harness().use { h ->
        h.installDownload(2); h.startCredit()
        h.data(Wire.DataFrame(h.transferId, 1, 1, 0, byteArrayOf(7))); h.drainHost()
        val bytes = ByteArrayOutputStream()
        var nextSent = false
        val sink = object : OutputStream() {
            override fun write(value: Int): Unit { bytes.write(value) }
            override fun write(buffer: ByteArray, offset: Int, length: Int): Unit {
                bytes.write(buffer, offset, length)
                if (!nextSent) {
                    nextSent = true
                    check(get(h.sourceOwner(), "outbound") == 1)
                    h.data(Wire.DataFrame(h.transferId, 1, 2, 1, byteArrayOf(8)))
                }
            }
        }
        h.pumpInto(DataOutputStream(sink)); h.drainHost()
        receipt(text(bytes.toByteArray()), 1, 1, Wire.MAX_DATA, Wire.Disposition.WRITTEN)
        h.host.creditAllowed = false
        receipt(h.pump(), 2, 2, 0, Wire.Disposition.WRITTEN)
        h.end(2, Wire.TransferOutcome.DELIVERED)
        check(h.host.writes == 2 && h.gracefulExit().kind == RemoteMediaReleaseKind.DRAINED)
    }

    // All 300 tag-2 frames are correctly credited/contiguous, under one valid authorized source.
    // The rate clock is FROZEN for this synthetic second; no scheduling speed can reset its window.
    Harness().use { h ->
        h.installDownload(300); h.freezeAuthorityClock(); h.startCredit()
        var closeId = 0L
        val frames = (1L..300L).map { sequence -> { payload(h.transferId, sequence, sequence - 1) } } + listOf(
            { control("""{"v":1,"type":"transferEnd","runId":"${h.run}","permitId":"${h.key.permitId}","generation":1,"eventSeq":2,"transferId":"${h.transferId}","attempt":1,"nextOffset":300,"outcome":"DELIVERED","code":null}""") },
            { control("""{"v":1,"type":"ack","id":$closeId,"outcome":"DRAINED","code":null}""") },
        )
        val script = Script(frames) { index ->
            if (index < 300) {
                h.drainHost()
                receipt(h.pump(), index.toLong() + 1, index.toLong() + 1, if (index == 299) 0 else Wire.MAX_DATA, Wire.Disposition.WRITTEN)
                check(get(h.bridge, "failure") == null)
            } else if (index == 300) {
                h.drainHost(); h.bridge.close(); h.pump(); closeId = h.pendingId()
            }
        }
        call(h.bridge, "readEvents", DataInputStream(script), { 0L })
        check(get(h.bridge, "failure") == null && h.host.writes == 300)
        h.pipesEnded(); h.child.finish(0)
        check(h.released.get(5, TimeUnit.SECONDS).kind == RemoteMediaReleaseKind.DRAINED)
    }
    Harness().use { h ->
        val frames = (1..257).map { revision ->
            { control("""{"v":1,"type":"network","runId":"${h.run}","revision":$revision,"status":"UNKNOWN","expensive":null,"constrained":null}""") }
        }
        call(h.bridge, "readEvents", DataInputStream(Script(frames) { h.drainHost() }), { 0L })
        h.drainHost()
        check(h.host.networks == 256 && get(h.bridge, "failure") == Wire.Code.PROTOCOL_ERROR)
        check(h.host.failures == 1) // Refused by the actual 257th CONTROL, not invalid source/ACK/credit.
    }

    // Removing the data rate cap must preserve strict transfer/attempt/sequence/offset/credit
    // validation. Every header below is codec-valid, but violates exactly one live transfer fence.
    for (mode in listOf("transfer", "attempt", "sequence", "offset", "credit")) Harness().use { h ->
        h.installDownload(2); h.startCredit()
        val transfer = if (mode == "transfer") id(9) else h.transferId
        val count = if (mode == "credit") 2 else 1
        val invalid = frame(ByteBuffer.allocate(41 + count).put(2)
            .putLong(transfer.mostSignificantBits).putLong(transfer.leastSignificantBits)
            .putLong(if (mode == "attempt") 2 else 1).putLong(if (mode == "sequence") 2 else 1)
            .putLong(if (mode == "offset") 1 else 0).put(ByteArray(count)).array())
        call(h.bridge, "readEvents", DataInputStream(ByteArrayInputStream(invalid)), { 0L })
        h.drainHost()
        check(get(h.bridge, "failure") == Wire.Code.PROTOCOL_ERROR && h.host.writes == 0)
    }

    // Failure callbacks may block, reenter close/release, and throw. Actual return/throw settles
    // them; process exit and pipe settlement while the callback is live cannot publish release.
    for (mode in listOf("returned", "threw", "threw-rejection")) Harness().use { h ->
        val entered = CountDownLatch(1); val finish = CountDownLatch(1)
        h.host.failureAction = {
            check(!Thread.holdsLock(h.lock)); h.bridge.close(); check(!h.released.isDone)
            check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 0)
            entered.countDown(); check(finish.await(5, TimeUnit.SECONDS))
            if (mode == "threw") error("SYNTHETIC_FAILURE_CALLBACK_THROW")
            if (mode == "threw-rejection") throw RejectedExecutionException("SYNTHETIC_CALLBACK_THROW")
        }
        try {
            h.fail(); check(entered.await(5, TimeUnit.SECONDS))
            h.pipesEnded(); h.child.finish(1)
            check(!h.released.isDone && get(h.bridge, "hostCallbacks") == 1 && h.child.destroyRequests == 1)
            finish.countDown()
            val release = h.released.get(5, TimeUnit.SECONDS)
            check(release.kind == RemoteMediaReleaseKind.FAILED_RETAINED && release.hostCallbacksSettled && h.host.failures == 1)
            check(release.failureDelivery == if (mode == "returned") RemoteMediaFailureDelivery.RETURNED else RemoteMediaFailureDelivery.THREW)
        } finally { finish.countDown() }
    }
    Harness().use { h ->
        val occupied = CountDownLatch(1); val finish = CountDownLatch(1)
        h.callbacks.execute { occupied.countDown(); check(finish.await(5, TimeUnit.SECONDS)) }
        try {
            check(occupied.await(5, TimeUnit.SECONDS)); h.fail(); h.pipesEnded(); h.child.finish(1)
            check(!h.released.isDone && h.host.failures == 0) // Queued failure delivery is still owned.
            h.callbacks.shutdown() // Orderly shutdown still executes the previously accepted callback.
            finish.countDown()
            check(h.released.get(5, TimeUnit.SECONDS).hostCallbacksSettled && h.host.failures == 1)
        } finally { finish.countDown() }
    }
    Harness().use { h ->
        // Deterministically close the executor BETWEEN failure ownership and execute(). Process
        // exit/settlement reentrancy at that exact point must still see the reserved callback slot.
        h.host.fenceAction = {
            check(!Thread.holdsLock(h.lock)); check(get(h.bridge, "hostCallbacks") == 1)
            h.pipesEnded(); h.child.finish(1); check(!h.released.isDone)
            h.callbacks.shutdown()
        }
        h.fail()
        val release = h.released.get(5, TimeUnit.SECONDS)
        check(release.kind == RemoteMediaReleaseKind.FAILED_RETAINED && release.hostCallbacksSettled)
        check(release.failureDelivery == RemoteMediaFailureDelivery.REJECTED)
        check(get(h.bridge, "failureDelivery").toString() == "REJECTED" && h.host.failures == 0)
        check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 1)
    }

    // Actual queue28 saturation: one admitted event blocks, 28 more queue, event30 and failed()
    // are both rejected by AbortPolicy. Rejection never erases another callback/write obligation.
    for (pending in listOf("callback", "write")) Harness(ownsProcessSlot = true).use { h ->
        val writeSettled = CompletableFuture<RemoteMediaWriteWitness>()
        val data = Wire.DataFrame(h.transferId, 1, 1, 0, byteArrayOf(7))
        var capture: RemoteMediaTransferCapture? = null
        if (pending == "write") {
            h.installDownload(1); h.startCredit()
            h.host.writeAction = { _, owner -> capture = owner; writeSettled }
            h.data(data); h.drainHost()
            check(get(h.bridge, "hostCallbacks") == 1 && h.host.writes == 1)
        }
        val sourceRelease = if (pending == "write") h.sourceRelease() else null
        val firstEntered = CountDownLatch(1); val finishFirst = CountDownLatch(1)
        val lastEntered = CountDownLatch(1); val finishLast = CountDownLatch(1)
        h.host.eventAction = { event ->
            if (event is Wire.Network && event.revision == 1L) {
                firstEntered.countDown(); check(finishFirst.await(5, TimeUnit.SECONDS))
            }
            if (event is Wire.Network && event.revision == 29L) {
                lastEntered.countDown(); check(finishLast.await(5, TimeUnit.SECONDS))
            }
        }
        try {
            h.event(Wire.Network(h.run, 1, Wire.NetworkStatus.UNKNOWN, null, null))
            check(firstEntered.await(5, TimeUnit.SECONDS))
            for (revision in 2L..29L) h.event(Wire.Network(h.run, revision, Wire.NetworkStatus.UNKNOWN, null, null))
            check(h.callbacks.queue.size == 28 && h.callbacks.rejectedExecutionHandler is ThreadPoolExecutor.AbortPolicy)
            h.event(Wire.Network(h.run, 30, Wire.NetworkStatus.UNKNOWN, null, null))
            check(get(h.bridge, "failure") == Wire.Code.CAPACITY_EXCEEDED && h.host.failures == 0)
            check(get(h.bridge, "failureDelivery").toString() == "REJECTED")
            h.pipesEnded(); h.child.finish(1)
            check(!h.released.isDone && !h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 0)
            finishFirst.countDown(); check(lastEntered.await(5, TimeUnit.SECONDS))
            synchronized(h.lock) { check(get(h.bridge, "hostCallbacks") == if (pending == "write") 2 else 1) }
            check(!h.released.isDone && !h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 0)
            finishLast.countDown()
            if (pending == "write") {
                h.drainHost()
                synchronized(h.lock) { check(get(h.bridge, "hostCallbacks") == 1) }
                check(!h.released.isDone && sourceRelease?.isDone == false)
                check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 0)
                data.withBytes { check(it.remaining() == 1 && it.get().toInt() == 7) }
                writeSettled.complete(witness(capture!!, data, Wire.Disposition.WRITTEN))
            }
            val release = h.released.get(5, TimeUnit.SECONDS)
            check(release.kind == RemoteMediaReleaseKind.FAILED_RETAINED && release.code == Wire.Code.CAPACITY_EXCEEDED)
            check(release.processExitConfirmed && release.pipeReaderSettled && release.pipeWriterSettled && release.hostCallbacksSettled)
            check(release.failureDelivery == RemoteMediaFailureDelivery.REJECTED && h.host.failures == 0 && h.host.networks == 29)
            sourceRelease?.get(5, TimeUnit.SECONDS)?.let {
                check(it.kind == RemoteMediaReleaseKind.FAILED_RETAINED && !it.nativeDrained && it.hostCallbacksSettled && it.exceptionalProcessExitConfirmed)
            }
            check(!(get(h.bridge, "started") as AtomicBoolean).get() && h.child.destroyRequests == 1)
            check(!(get(h.bridge, "processSlotReleased") as AtomicBoolean).get())
            check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
            // First deny current recovery, then present a stale new-attempt capture. Both retain
            // the global owner. Only this exact failed run/result with current approval may pass.
            check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 1)
            var expectedRun = id(91)
            h.host.recoveryAction = { run, evidence -> run == expectedRun && evidence === release }
            check(!h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 2)
            check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
            expectedRun = h.run
            check(h.bridge.releaseFailedProcessForExplicitNewAttempt() && h.host.recoveryChecks == 3)
            check(h.released.getNow(null) === release && release.kind == RemoteMediaReleaseKind.FAILED_RETAINED)
            check(release.failureDelivery == RemoteMediaFailureDelivery.REJECTED)
            // A new owner is constructed explicitly, never spawned/reconnected by recovery.
            Harness(ownsProcessSlot = true, runSuffix = 9).use { next ->
                check(!(get(next.bridge, "started") as AtomicBoolean).get())
                check(!h.bridge.releaseFailedProcessForExplicitNewAttempt()) // Cannot free the newer owner's slot.
                check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
                next.fail(); next.pipesEnded(); next.child.finish(1)
                val nextRelease = next.released.get(5, TimeUnit.SECONDS)
                next.host.recoveryAction = { run, evidence -> run == h.run && evidence === release }
                check(!next.bridge.releaseFailedProcessForExplicitNewAttempt()) // Foreign old-run evidence.
                check(runCatching { MacRemoteMediaBridge.create(h.path, h.identity, h.host, 1, false, id(90)) }.isFailure)
                next.host.recoveryAction = { run, evidence -> run == next.run && evidence === nextRelease }
                check(next.bridge.releaseFailedProcessForExplicitNewAttempt())
                check(!next.bridge.releaseFailedProcessForExplicitNewAttempt() && !h.bridge.releaseFailedProcessForExplicitNewAttempt())
            }
        } finally { finishFirst.countDown(); finishLast.countDown(); data.close() }
    }
}

/*
 * Still required from the future real bridge/native fixture, with valid authority/media inputs:
 * - retireSource awaits a final WRITTEN/DISCARDED receipt while its ordinary ACK is pending;
 *   the receipt, read DENY, and negative intent each physically traverse the independent writer;
 * - a blocked file write outlives native cancellation and process destruction; only actual write
 *   settlement + onExit + both pipe owners settle the exceptional result, always FAILED_RETAINED;
 * - PREEXPIRY and first-401 join one renewalId, quiesce old tasks/session/trust/data frames,
 *   apply an exact required host K-publication witness, preserve a Pause admitted during the wait,
 *   and refuse a second 401 in that executionAttempt after a later proactive replacement;
 * - hold a real file-write future through beginAccessRenewal, deliver one WRITTEN/zero-credit
 *   receipt, then accessQuiesced and the exact new accessApplied. Re-arm only the empty slot
 *   with START; neither transfer sequence, prefix, original intent nor attempt lease resets.
 *   Cross an already-sent positive candidate/START with an autonomous first-401 and a later
 *   PREEXPIRY; withhold it, reject duplicate START per applied epoch, and keep parallel audio alive;
 * - pause main just before confirmContainer, enter PREEXPIRY, then run that stale callback.
 *   Keep exactly one pending preparation through its matching main ACK; after accessApplied
 *   reread under the successor and confirm the same source/lease with a live read grant.
 *   Pause/selection/sleep/retirement and expired grants cannot cause captured autoplay;
 * - refuse observable Transfer-Encoding on fixed-length HEAD/206 before body admission.
 *   Exact raw duplicate Location rejection remains OPEN pending the real M1-H1 reply;
 * - DER optional-field fixtures: absent/default v1, explicit-default refusal, v2/v3 uniqueIDs,
 *   v3-only nonempty explicit Extensions, tail order/duplicate refusal, critical DEFAULT FALSE
 *   omission and implicit BIT STRING zero padding, bounded nodes/lengths and unchanged SPKI TLV.
 *   Primary structural references (not executable evidence): RFC5280 4.1 and X.690 8.6/11.2/11.5:
 *   https://www.rfc-editor.org/rfc/rfc5280.html#section-4.1
 *   https://www.itu.int/ITU-T/studygroups/com17/languages/X.690-0207.pdf
 * - delay resolver/local close, then admit Pause/timer/selection A-B-A before candidate completion;
 *   no old native start, no new audio lease before real old-owner release, no inferred audible stop;
 * - deny read/publication with stale J source evidence, separate M5/D1 guards, missing package
 *   witness, old intent, absent LOCAL owner or a foreign copy; valid offline LOCAL needs no JWT;
 * - package/codec/DER-SPKI/redirect/Range/load-owner/memory/cost/sleep/retained-fd/publication gates
 *   require the actual native implementation and host integration, absent from this source slice.
 */
