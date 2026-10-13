package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopAudioEngine
import app.autplay.application.desktop.DesktopAudioEvent
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.data.desktop.openDesktopLibraryStore
import app.autplay.desktop.library.DesktopFileImporter
import app.autplay.desktop.playback.DesktopPlaybackController
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.Path
import java.util.Comparator
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals

internal suspend fun eventually(description: String, predicate: () -> Boolean): Unit {
    try {
        withTimeout(15_000) {
            while (!predicate()) delay(20)
        }
    } catch (failure: TimeoutCancellationException) {
        throw AssertionError("Timed out: $description", failure)
    }
}

/** Every byte, database and scope belongs to one disposable canonical root. */
internal class DesktopAcceptanceFixture {
    val sandbox: Path = Files.createTempDirectory("autplay-desktop-acceptance-").toRealPath()
    val data: Path = Files.createDirectory(sandbox.resolve("data"))
    val sources: Path = Files.createDirectory(sandbox.resolve("sources"))
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var opened: DesktopLibraryStore? = null
    private var engine: DesktopAudioEngine? = null
    private var controller: DesktopPlaybackController? = null
    val store: DesktopLibraryStore get() = checkNotNull(opened)

    suspend fun openStore(): Unit { opened = openDesktopLibraryStore(data) }
    suspend fun reopenStore(): Unit { store.close(); opened = null; openStore() }
    fun start(engine: DesktopAudioEngine): DesktopPlaybackController {
        check(this.engine == null)
        this.engine = engine
        return DesktopPlaybackController(store, engine, scope).also { controller = it }
    }
    suspend fun stopController(): Unit {
        withTimeout(15_000) { controller?.close() ?: engine?.close() }
        controller = null
        engine = null
    }
    suspend fun importWav(name: String = "Synthetic tone.wav", durationMs: Int = 8_000): String {
        val source = sources.resolve(name)
        Files.write(source, wave(durationMs))
        val report = DesktopFileImporter(store, data).importSelected(listOf(source.toString()))
        assertEquals(1, report.imported)
        assertEquals(0, report.failed)
        return store.state.value.tracks.single().id
    }
    suspend fun close(): Unit = withContext(NonCancellable) {
        try { stopController() } finally {
            scope.cancel()
            try { opened?.close() } finally {
                opened = null
                // Files.walk does not follow symlinks; only this created root is removed.
                Files.walk(sandbox).use { paths -> paths.sorted(Comparator.reverseOrder()).forEach(Files::delete) }
            }
        }
    }
}

/** PCM silence has a real native timeline and does not need bundled media or an audio download. */
internal fun wave(durationMs: Int): ByteArray {
    val sampleRate = 16_000
    val samples = sampleRate * durationMs / 1_000
    val pcmBytes = samples * 2
    return ByteBuffer.allocate(44 + pcmBytes).order(ByteOrder.LITTLE_ENDIAN).apply {
        put("RIFF".toByteArray(Charsets.US_ASCII)); putInt(36 + pcmBytes)
        put("WAVEfmt ".toByteArray(Charsets.US_ASCII)); putInt(16)
        putShort(1.toShort()); putShort(1.toShort()); putInt(sampleRate); putInt(sampleRate * 2)
        putShort(2.toShort()); putShort(16.toShort())
        put("data".toByteArray(Charsets.US_ASCII)); putInt(pcmBytes)
        repeat(samples) { putShort(0.toShort()) }
    }.array()
}

/** Used solely to deliver deterministic generations/EOFs to the real application/store. */
internal class PolicyEngine : DesktopAudioEngine {
    data class Open(val generation: Long, val positionMs: Long, val autoplay: Boolean)
    val opens: MutableList<Open> = CopyOnWriteArrayList()
    val stops: MutableList<Long> = CopyOnWriteArrayList()
    private val facts = MutableStateFlow(DesktopAudioEvent(0, "IDLE"))
    override val state: StateFlow<DesktopAudioEvent> = facts
    fun emit(event: DesktopAudioEvent): Unit { facts.value = event }
    override fun open(managedPath: String, generation: Long, positionMs: Long, autoplay: Boolean): Unit {
        opens.add(Open(generation, positionMs, autoplay))
        emit(DesktopAudioEvent(generation, "READY", positionMs, 8_000, autoplay))
    }
    override fun play(generation: Long): Unit { emit(facts.value.copy(generation = generation, isPlaying = true)) }
    override fun pause(generation: Long): Unit { emit(facts.value.copy(generation = generation, isPlaying = false)) }
    override fun seek(generation: Long, positionMs: Long): Unit { emit(facts.value.copy(generation = generation, positionMs = positionMs)) }
    override fun stop(generation: Long): Unit { stops.add(generation) }
    override suspend fun close(): Unit { emit(facts.value.copy(status = "CLOSED", isPlaying = false)) }
}
