package app.autplay.desktop.systemmedia

import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path

/**
 * Explicit opt-in after the desktop AppKit application exists. No load during class discovery or
 * fake-boundary tests. Only the Compose app resource directory is eligible; no PATH/JAR extraction,
 * user media path, working-directory fallback, download or helper process is involved.
 */
public fun macSystemMediaApi(): DesktopSystemMediaApi? = MacSystemMediaLibrary.load()

private object MacSystemMediaLibrary {
    private var attempted: Boolean = false
    private var loaded: Boolean = false

    @Synchronized
    fun load(): DesktopSystemMediaApi? {
        if (!attempted) {
            attempted = true
            loaded = try {
                if (System.getProperty("os.name") != "Mac OS X" ||
                    System.getProperty("os.arch") !in setOf("aarch64", "arm64")) false
                else {
                    val root = System.getProperty("compose.application.resources.dir")?.let { Path.of(it) }
                    if (root == null) false else {
                        val canonicalRoot = root.toRealPath()
                        val library = canonicalRoot.resolve("native/libAutPlaySystemMedia.dylib")
                        if (!Files.isRegularFile(library, NOFOLLOW_LINKS) ||
                            !library.toRealPath().startsWith(canonicalRoot)) false
                        else { System.load(library.toString()); true }
                    }
                }
            } catch (_: Exception) { false } catch (_: LinkageError) { false }
        }
        return if (loaded) MacSystemMediaApi else null
    }
}

private object MacSystemMediaApi : DesktopSystemMediaApi {
    override fun abiVersion(): Int = MacSystemMediaJni.abiVersion()
    override fun acquire(expectedAbi: Int, listener: DesktopSystemMediaListener): Long =
        MacSystemMediaJni.acquire(expectedAbi, listener)

    override fun publish(lease: Long, payload: DesktopSystemMediaPayload?): Boolean {
        if (payload == null) return MacSystemMediaJni.clear(lease)
        val artwork = payload.artwork
        return MacSystemMediaJni.publish(lease, payload.targetToken, payload.title, payload.artist,
            payload.positionMs, payload.durationMs ?: -1, payload.isPlaying, payload.allowedCommands,
            artwork?.copyPngBytes(), artwork?.width ?: 0, artwork?.height ?: 0)
    }

    override fun retire(lease: Long): Unit = MacSystemMediaJni.retire(lease)
    override fun isReleased(lease: Long): Boolean = MacSystemMediaJni.isReleased(lease)
}

/** Keep these names/descriptors paired with AutPlaySystemMedia.mm; no JNI name-based overloads. */
internal object MacSystemMediaJni {
    public external fun abiVersion(): Int
    public external fun acquire(expectedAbi: Int, listener: DesktopSystemMediaListener): Long
    public external fun publish(lease: Long, token: Long, title: String, artist: String, positionMs: Long,
        durationMs: Long, playing: Boolean, commands: Int, png: ByteArray?, width: Int, height: Int): Boolean
    public external fun clear(lease: Long): Boolean
    public external fun retire(lease: Long): Unit
    public external fun isReleased(lease: Long): Boolean
}
