package app.autplay.desktop.audio

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import java.io.DataInputStream
import java.io.DataOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Sanitized failure facts retained after stop; no native descriptions, paths or arbitrary domains. */
public data class MacAudioFailure(
    val generation: Long,
    val errorCode: String,
    val stage: String,
    val causes: List<MacAudioCause> = emptyList(),
)

public data class MacAudioCause(val domain: String, val code: Int)

/**
 * Local prototype execution adapter. The caller supplies the bundled executable and a verified
 * managed-file capability from DesktopLibraryStore; neither media paths nor native errors are logged.
 * Construct one engine per application host and await close before replacing it.
 * Commands return after bounded enqueueing; state contains native facts, not optimistic playback.
 */
public class MacAudioEngine(helperExecutable: Path) : DesktopAudioEngine {
    private val executable: Path = helperExecutable.toAbsolutePath().normalize()
    private val lock: Any = Any()
    private val commands: ArrayBlockingQueue<Command> = ArrayBlockingQueue(QUEUE_CAPACITY)
    private val mutableState: MutableStateFlow<DesktopAudioEvent> =
        MutableStateFlow(DesktopAudioEvent(generation = 0, status = "IDLE"))
    override val state: StateFlow<DesktopAudioEvent> = mutableState.asStateFlow()
    private val mutableFailure: MutableStateFlow<MacAudioFailure?> = MutableStateFlow(null)
    public val lastFailure: StateFlow<MacAudioFailure?> = mutableFailure.asStateFlow()
    private val nativeHello: CompletableFuture<Unit> = CompletableFuture()
    private val handshake: CompletableFuture<Unit> = CompletableFuture()
    private val completion: CompletableFuture<Unit> = CompletableFuture()
    private var process: Process? = null
    private var reader: Thread? = null
    private var watchdog: ScheduledExecutorService? = null
    private var generation: Long = -1
    private var closing: Boolean = false
    private var terminalError: String? = null
    private var pendingAck: Pair<Long, CompletableFuture<Unit>>? = null
    private var helloReceived: Boolean = false
    private var readyReceived: Boolean = false
    private val ownsLease: Boolean
    private var lastEventAt: Long = System.nanoTime()

    init {
        if (System.getProperty("os.name") != "Mac OS X") {
            ownsLease = false
            fail("AUDIO_UNSUPPORTED_PLATFORM")
            completion.complete(Unit)
        } else if (!OWNER_LEASE.compareAndSet(false, true)) {
            ownsLease = false
            fail("AUDIO_OWNER_ALREADY_ACTIVE")
            completion.complete(Unit)
        } else {
            ownsLease = true
            Thread(::runOwner, "autplay-audio-owner").apply { isDaemon = true }.start()
        }
    }

    override fun open(managedPath: String, generation: Long, positionMs: Long, autoplay: Boolean): Unit {
        synchronized(lock) {
            if (closing || generation < 0 || generation <= this.generation) return
            this.generation = generation
            terminalError?.let { code ->
                // Resolve the selected generation's pending state without restarting a failed owner.
                mutableFailure.value = mutableFailure.value?.copy(generation = generation)
                mutableState.value = DesktopAudioEvent(generation, "UNAVAILABLE", errorCode = code)
                return
            }
            mutableFailure.value = null
            lastEventAt = System.nanoTime()
            // Gate incoming old callbacks immediately, before the owner writes the replacement command.
            mutableState.value = DesktopAudioEvent(generation, "BUFFERING")
            if (!validPath(managedPath) || positionMs !in 0..MAX_POSITION_MS) {
                fail("AUDIO_INVALID_ARGUMENT")
                return
            }
            enqueue(Command("open", generation, managedPath, positionMs, autoplay))
        }
    }

    override fun play(generation: Long): Unit = submit("play", generation)
    override fun pause(generation: Long): Unit = submit("pause", generation)
    override fun stop(generation: Long): Unit = submit("stop", generation)

    override fun seek(generation: Long, positionMs: Long): Unit {
        synchronized(lock) {
            if (!accepts(generation)) return
            if (positionMs !in 0..MAX_POSITION_MS) {
                fail("AUDIO_INVALID_ARGUMENT")
                return
            }
            enqueue(Command("seek", generation, positionMs = positionMs))
        }
    }

    /** Cancellation of the calling scope cannot leave an AVPlayer process running. */
    override suspend fun close(): Unit = withContext(NonCancellable + Dispatchers.IO) {
        synchronized(lock) {
            closing = true
            commands.clear()
        }
        try {
            completion.get(CLOSE_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        } catch (_: Exception) {
            fail("AUDIO_TEARDOWN_TIMEOUT", duringClose = true)
        }
        synchronized(lock) {
            if (completion.isDone && terminalError == null) {
                mutableState.value = mutableState.value.copy(status = "CLOSED", isPlaying = false)
            }
        }
    }

    private fun submit(op: String, requestedGeneration: Long): Unit {
        synchronized(lock) {
            if (accepts(requestedGeneration)) enqueue(Command(op, requestedGeneration))
        }
    }

    private fun accepts(requestedGeneration: Long): Boolean =
        !closing && terminalError == null && generation >= 0 && requestedGeneration == generation

    // Must be called with lock held; overflow fails closed so a pause/stop is never silently lost.
    private fun enqueue(command: Command): Unit {
        if (!commands.offer(command)) fail("AUDIO_COMMAND_QUEUE_FULL")
    }

    private fun runOwner(): Unit {
        var child: Process? = null
        var output: DataOutputStream? = null
        var nextId: Long = 1
        try {
            val timer = ScheduledThreadPoolExecutor(1) { runnable ->
                Thread(runnable, "autplay-audio-watchdog").apply { isDaemon = true }
            }.apply { setRemoveOnCancelPolicy(true) }
            synchronized(lock) { watchdog = timer }
            val startupDeadline = timer.schedule({ fail("AUDIO_HANDSHAKE_TIMEOUT") }, IO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            try {
                if (!Files.isRegularFile(executable) || !Files.isExecutable(executable)) {
                    fail("AUDIO_HELPER_UNAVAILABLE")
                    return
                }
                // No shell or media-bearing arguments/environment; stderr never enters the event channel.
                val launched = ProcessBuilder(executable.toString()).redirectError(ProcessBuilder.Redirect.DISCARD).start()
                child = launched
                synchronized(lock) {
                    process = launched
                    if (terminalError != null) launched.destroyForcibly()
                }
                output = DataOutputStream(launched.outputStream)
                val input = DataInputStream(launched.inputStream)
                val inputThread = Thread({ readEvents(input) }, "autplay-audio-events").apply { isDaemon = true }
                synchronized(lock) { reader = inputThread }
                inputThread.start()
                // The reader validates native hello first. The writer alone owns the stdin stream.
                nativeHello.get(IO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
                writeFrame(output, buildJsonObject {
                    put("v", VERSION)
                    put("type", "hello")
                    put("protocol", PROTOCOL)
                    put("maxFrameBytes", MAX_FRAME_BYTES)
                })
                handshake.get(IO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            } catch (_: TimeoutException) {
                fail("AUDIO_HANDSHAKE_TIMEOUT")
                throw ProtocolFailure()
            } finally {
                startupDeadline.cancel(false)
            }
            timer.scheduleAtFixedRate({
                val timedOut = synchronized(lock) {
                    !closing && terminalError == null &&
                        (mutableState.value.status == "BUFFERING" || mutableState.value.isPlaying) &&
                        System.nanoTime() - lastEventAt >= TimeUnit.MILLISECONDS.toNanos(EVENT_TIMEOUT_MS)
                }
                if (timedOut) fail("AUDIO_EVENT_TIMEOUT")
            }, 1, 1, TimeUnit.SECONDS)
            while (synchronized(lock) { !closing && terminalError == null }) {
                val command = commands.poll(100, TimeUnit.MILLISECONDS) ?: continue
                if (synchronized(lock) { command.generation != generation || closing || terminalError != null }) continue
                send(output, command, nextId++)
                if (nextId <= 0) throw ProtocolFailure()
            }
            if (synchronized(lock) { closing && terminalError == null && readyReceived }) {
                send(output, Command("close", synchronized(lock) { generation.coerceAtLeast(0) }), nextId)
            }
        } catch (_: ProtocolFailure) {
            fail("AUDIO_PROTOCOL_ERROR")
        } catch (_: Exception) {
            if (synchronized(lock) { !closing && terminalError == null }) fail("AUDIO_PROCESS_FAILED")
        } finally {
            // Killing before closing pipe wrappers also releases a writer blocked in an OS pipe write.
            val ended = terminate(child)
            runCatching { output?.close() }
            runCatching { child?.inputStream?.close() }
            val inputThread = synchronized(lock) { reader }
            runCatching { inputThread?.join(THREAD_JOIN_MS) }
            synchronized(lock) {
                watchdog?.shutdownNow()
                commands.clear()
                pendingAck = null
                process = null
                if (!ended || inputThread?.isAlive == true) {
                    terminalError = "AUDIO_TEARDOWN_TIMEOUT"
                    mutableFailure.value = MacAudioFailure(generation.coerceAtLeast(0), "AUDIO_TEARDOWN_TIMEOUT", "AUDIO_TEARDOWN_TIMEOUT")
                    mutableState.value = unavailable("AUDIO_TEARDOWN_TIMEOUT")
                } else if (closing && terminalError == null) {
                    mutableState.value = mutableState.value.copy(status = "CLOSED", isPlaying = false)
                }
            }
            if (ownsLease && ended && inputThread?.isAlive != true) OWNER_LEASE.set(false)
            completion.complete(Unit)
        }
    }

    private fun send(output: DataOutputStream, command: Command, id: Long): Unit {
        val acknowledged = CompletableFuture<Unit>()
        val timer = synchronized(lock) {
            pendingAck = id to acknowledged
            checkNotNull(watchdog)
        }
        val deadline = timer.schedule({ fail("AUDIO_COMMAND_TIMEOUT", duringClose = true) }, IO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        try {
            writeFrame(output, command.frame(id))
            acknowledged.get(IO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) {
            fail("AUDIO_COMMAND_TIMEOUT", duringClose = true)
            throw ProtocolFailure()
        } finally {
            deadline.cancel(false)
            synchronized(lock) { if (pendingAck?.first == id) pendingAck = null }
        }
    }

    private fun readEvents(input: DataInputStream): Unit {
        var windowStart = System.nanoTime()
        var frameCount = 0
        try {
            while (true) {
                val firstByte = input.read()
                if (firstByte < 0) throw java.io.EOFException()
                val current = System.nanoTime()
                if (current - windowStart >= TimeUnit.SECONDS.toNanos(1)) {
                    windowStart = current
                    frameCount = 0
                }
                if (++frameCount > MAX_FRAMES_PER_SECOND) throw ProtocolFailure()
                val timer = synchronized(lock) { checkNotNull(watchdog) }
                val deadline = timer.schedule({ fail("AUDIO_FRAME_TIMEOUT", duringClose = true) }, IO_TIMEOUT_MS, TimeUnit.MILLISECONDS)
                try {
                    val remainingHeader = ByteArray(3)
                    input.readFully(remainingHeader)
                    val size = (firstByte shl 24) or ((remainingHeader[0].toInt() and 255) shl 16) or
                        ((remainingHeader[1].toInt() and 255) shl 8) or (remainingHeader[2].toInt() and 255)
                    if (size !in 1..MAX_FRAME_BYTES) throw ProtocolFailure()
                    val bytes = ByteArray(size)
                    input.readFully(bytes)
                    val text = StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                        .decode(ByteBuffer.wrap(bytes)).toString()
                    // Protocol values have depth <=1; reject parser recursion and duplicate members first.
                    requireFlatObject(text)
                    val frame = Json.parseToJsonElement(text) as? JsonObject ?: throw ProtocolFailure()
                    receive(frame)
                } finally {
                    deadline.cancel(false)
                }
            }
        } catch (_: ProtocolFailure) {
            fail("AUDIO_PROTOCOL_ERROR")
        } catch (_: java.io.EOFException) {
            fail("AUDIO_PROCESS_EXITED")
        } catch (_: Exception) {
            fail("AUDIO_PROTOCOL_ERROR")
        }
    }

    private fun receive(frame: JsonObject): Unit = synchronized(lock) {
        if (frame.long("v") != VERSION.toLong()) throw ProtocolFailure()
        when (frame.string("type")) {
            "hello" -> {
                frame.only("v", "type", "protocol", "maxFrameBytes")
                if (helloReceived || frame.string("protocol") != PROTOCOL || frame.long("maxFrameBytes") != MAX_FRAME_BYTES.toLong()) {
                    throw ProtocolFailure()
                }
                helloReceived = true
                nativeHello.complete(Unit)
            }
            "ready" -> {
                frame.only("v", "type")
                if (!helloReceived || readyReceived) throw ProtocolFailure()
                readyReceived = true
                handshake.complete(Unit)
            }
            "ack" -> {
                frame.only("v", "type", "id")
                val pending = pendingAck ?: throw ProtocolFailure()
                if (!readyReceived || frame.long("id") != pending.first) throw ProtocolFailure()
                pending.second.complete(Unit)
            }
            "event" -> {
                if (!readyReceived) throw ProtocolFailure()
                val event = frame.event()
                if (!closing && terminalError == null && event.generation == generation) {
                    // StateFlow consumers may conflate frames; the native owner emits EOF exactly once.
                    lastEventAt = System.nanoTime()
                    if (event.status == "ERROR" && event.errorCode != null &&
                        (mutableFailure.value?.generation != generation || mutableFailure.value?.errorCode != event.errorCode)) {
                        mutableFailure.value = MacAudioFailure(generation, event.errorCode, event.errorCode)
                    }
                    mutableState.value = event
                }
            }
            "diagnostic" -> {
                if (!readyReceived) throw ProtocolFailure()
                val failure = frame.diagnostic()
                if (!closing && terminalError == null && failure.generation == generation) {
                    mutableFailure.value = failure
                }
            }
            else -> throw ProtocolFailure()
        }
    }

    private fun fail(code: String, duringClose: Boolean = false): Unit {
        val child = synchronized(lock) {
            if (terminalError != null || (closing && !duringClose)) return
            terminalError = code
            mutableFailure.value = MacAudioFailure(generation.coerceAtLeast(0), code, code)
            mutableState.value = unavailable(code)
            commands.clear()
            nativeHello.completeExceptionally(ProtocolFailure())
            handshake.completeExceptionally(ProtocolFailure())
            pendingAck?.second?.completeExceptionally(ProtocolFailure())
            process
        }
        runCatching { child?.destroyForcibly() }
    }

    private fun unavailable(code: String): DesktopAudioEvent = mutableState.value.copy(
        generation = generation.coerceAtLeast(0), status = "UNAVAILABLE", isPlaying = false, errorCode = code,
    )

    private fun terminate(child: Process?): Boolean {
        if (child == null) return true
        if (runCatching { child.waitFor(PROCESS_WAIT_MS, TimeUnit.MILLISECONDS) }.getOrDefault(false)) return true
        runCatching { child.destroy() }
        if (runCatching { child.waitFor(PROCESS_WAIT_MS, TimeUnit.MILLISECONDS) }.getOrDefault(false)) return true
        runCatching { child.destroyForcibly() }
        return runCatching { child.waitFor(PROCESS_WAIT_MS, TimeUnit.MILLISECONDS) }.getOrDefault(false)
    }

    private class Command(
        val op: String,
        val generation: Long,
        val managedPath: String? = null,
        val positionMs: Long? = null,
        val autoplay: Boolean? = null,
    ) {
        fun frame(id: Long): JsonObject = buildJsonObject {
            put("v", VERSION)
            put("type", "command")
            put("id", id)
            put("op", op)
            put("generation", generation)
            managedPath?.let { put("managedPath", it) }
            positionMs?.let { put("positionMs", it) }
            autoplay?.let { put("autoplay", it) }
        }
    }

    private class ProtocolFailure : Exception()

    private companion object {
        const val VERSION: Int = 1
        const val PROTOCOL: String = "autplay.local-audio"
        const val MAX_FRAME_BYTES: Int = 65_536
        const val QUEUE_CAPACITY: Int = 32
        const val IO_TIMEOUT_MS: Long = 5_000
        const val PROCESS_WAIT_MS: Long = 500
        const val THREAD_JOIN_MS: Long = 500
        const val CLOSE_TIMEOUT_MS: Long = 12_000
        const val EVENT_TIMEOUT_MS: Long = 15_000
        const val MAX_FRAMES_PER_SECOND: Int = 256
        const val MAX_POSITION_MS: Long = 604_800_000
        val OWNER_LEASE: AtomicBoolean = AtomicBoolean(false)
        val STATUSES: Set<String> = setOf("IDLE", "BUFFERING", "READY", "ENDED", "ERROR", "CLOSED")
        val NATIVE_ERRORS: Set<String> = setOf("NATIVE_INVALID_PATH", "NATIVE_FILE_UNAVAILABLE", "NATIVE_UNSUPPORTED_MEDIA", "NATIVE_OPEN_FAILED", "NATIVE_OPEN_TIMEOUT", "NATIVE_PLAYBACK_FAILED", "NATIVE_SEEK_FAILED", "NATIVE_SEEK_TIMEOUT")
        val FAILURE_STAGES: Set<String> = NATIVE_ERRORS + setOf("FILE_VALIDATION", "FILE_ATTRIBUTES", "ASSET_PLAYABLE", "ASSET_PROTECTED", "ASSET_DURATION", "ASSET_TRACKS", "ITEM_READY")
        val FAILURE_DOMAINS: Set<String> = setOf("AVFOUNDATION", "OSSTATUS", "COCOA", "POSIX", "URL", "MACH", "OTHER")

        fun validPath(path: String): Boolean = path.length in 1..4096 && path.startsWith('/') && '\u0000' !in path &&
            path.toByteArray(StandardCharsets.UTF_8).size in 1..4096

        fun writeFrame(output: DataOutputStream, frame: JsonObject): Unit {
            val bytes = frame.toString().toByteArray(StandardCharsets.UTF_8)
            if (bytes.size !in 1..MAX_FRAME_BYTES) throw ProtocolFailure()
            output.writeInt(bytes.size)
            output.write(bytes)
            output.flush()
        }

        fun JsonObject.only(vararg allowed: String): Unit {
            if (keys.any { it !in allowed }) throw ProtocolFailure()
        }

        fun JsonObject.string(key: String): String {
            val value = this[key] as? JsonPrimitive ?: throw ProtocolFailure()
            if (!value.isString) throw ProtocolFailure()
            return value.content
        }

        fun JsonObject.long(key: String): Long {
            val value = this[key] as? JsonPrimitive ?: throw ProtocolFailure()
            if (value.isString || !value.content.matches(Regex("0|[1-9][0-9]{0,18}"))) throw ProtocolFailure()
            return value.content.toLongOrNull() ?: throw ProtocolFailure()
        }

        fun JsonObject.optionalLong(key: String): Long? =
            if (this[key] == null || this[key] == JsonNull) null else long(key)

        fun JsonObject.optionalString(key: String): String? =
            if (this[key] == null || this[key] == JsonNull) null else string(key)

        fun JsonObject.event(): DesktopAudioEvent {
            only("v", "type", "generation", "status", "positionMs", "durationMs", "isPlaying", "errorCode", "title", "artist")
            val status = string("status")
            val error = optionalString("errorCode")
            val playing = this["isPlaying"] as? JsonPrimitive ?: throw ProtocolFailure()
            if (status !in STATUSES || (error != null && error !in NATIVE_ERRORS) || playing.isString || playing.content !in setOf("true", "false")) throw ProtocolFailure()
            val isPlaying = playing.content == "true"
            if ((isPlaying && status != "READY") || (status == "ERROR") != (error != null)) throw ProtocolFailure()
            val position = long("positionMs")
            val duration = optionalLong("durationMs")
            if (position > MAX_POSITION_MS || (duration != null && duration > MAX_POSITION_MS)) throw ProtocolFailure()
            val title = optionalString("title")
            val artist = optionalString("artist")
            if (!validMetadata(title) || !validMetadata(artist)) throw ProtocolFailure()
            return DesktopAudioEvent(long("generation"), status, position, duration, isPlaying, error, title, artist)
        }

        fun JsonObject.diagnostic(): MacAudioFailure {
            only("v", "type", "generation", "errorCode", "stage", "cause0Domain", "cause0Code", "cause1Domain", "cause1Code", "cause2Domain", "cause2Code")
            val code = string("errorCode")
            val stage = string("stage")
            if (code !in NATIVE_ERRORS || stage !in FAILURE_STAGES) throw ProtocolFailure()
            val causes = ArrayList<MacAudioCause>(3)
            var missing = false
            for (index in 0..2) {
                val domainKey = "cause${index}Domain"
                val codeKey = "cause${index}Code"
                if (this[domainKey] == null && this[codeKey] == null) {
                    missing = true
                    continue
                }
                if (missing || this[domainKey] == null || this[codeKey] == null) throw ProtocolFailure()
                val domain = string(domainKey)
                val number = this[codeKey] as? JsonPrimitive ?: throw ProtocolFailure()
                if (domain !in FAILURE_DOMAINS || number.isString || number.content == "-0" ||
                    !number.content.matches(Regex("-?(0|[1-9][0-9]{0,9})"))) throw ProtocolFailure()
                causes.add(MacAudioCause(domain, number.content.toIntOrNull() ?: throw ProtocolFailure()))
            }
            return MacAudioFailure(long("generation"), code, stage, causes.toList())
        }

        fun validMetadata(value: String?): Boolean = value == null ||
            (value.toByteArray(StandardCharsets.UTF_8).size <= 256 && value.none { it.isISOControl() } &&
                '/' !in value && '\\' !in value && ':' !in value && !value.startsWith('~'))

        /** The wire is a flat object of primitive values; disallow nested JSON and duplicate keys. */
        fun requireFlatObject(text: String): Unit {
            var inString = false
            var escaped = false
            var depth = 0
            var keyStart = -1
            var expectingKey = false
            val keys = HashSet<String>()
            for ((index, ch) in text.withIndex()) {
                if (inString) {
                    when {
                        escaped -> escaped = false
                        ch == '\\' -> escaped = true
                        ch == '"' -> {
                            inString = false
                            if (keyStart >= 0) {
                                // No escaped keys are needed by this protocol.
                                val key = text.substring(keyStart, index)
                                if ('\\' in key || !keys.add(key)) throw ProtocolFailure()
                                keyStart = -1
                                expectingKey = false
                            }
                        }
                    }
                } else {
                    when (ch) {
                        '"' -> {
                            inString = true
                            if (expectingKey) keyStart = index + 1
                        }
                        '{' -> {
                            if (++depth != 1) throw ProtocolFailure()
                            expectingKey = true
                        }
                        '}' -> if (--depth != 0) throw ProtocolFailure()
                        '[', ']' -> throw ProtocolFailure()
                        ',' -> expectingKey = true
                    }
                }
            }
            if (depth != 0 || inString || keys.size !in 1..11) throw ProtocolFailure()
        }
    }
}
