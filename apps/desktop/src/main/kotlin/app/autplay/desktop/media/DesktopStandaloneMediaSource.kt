package app.autplay.desktop.media

import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.media.*
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.StandardOpenOption.READ
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean

/** Existing standalone store remains its file authority; this adapter owns fresh read descriptors. */
internal class DesktopStandaloneMediaSource(
    private val store: DesktopLibraryStore,
    private val work: DesktopMediaWorkOwner,
) : DesktopMediaLocalSourcePort, DesktopMediaActorSourcePort {
    val processStoreId: MediaId = MediaId.random()
    val processReadOwnerId: MediaId = MediaId.random()
    override suspend fun capture(target: DesktopMediaActorTarget): DesktopMediaActorSource {
        val path = store.resolveManagedFile(target.trackId) ?: throw mediaFailure(MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
        return DesktopMediaActorSource.Standalone(path)
    }
    override fun startLocalRead(capture: SelectionCapture, intent: SourceIntent,
        admission: MediaLiveAdmission): MediaOwnedWork<MediaLocalLookup> = work.start { cancelled ->
        val standalone = capture as? SelectionCapture.StandaloneLocal ?: return@start MediaLocalLookup.Missing(LocalAvailability.ABSENT)
        val selection = intent as? SourceIntent.StandaloneSelection ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.LOCAL_OWNER)
        mediaCheck(standalone.storeId == processStoreId && standalone.readOwnerId == processReadOwnerId,
            MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
        val track = store.state.value.tracks.singleOrNull { it.id == selection.trackId.value }
            ?: return@start MediaLocalLookup.Missing(LocalAvailability.ABSENT)
        val name = store.resolveManagedFile(track.id) ?: return@start MediaLocalLookup.Missing(LocalAvailability.UNKNOWN)
        val path = Path.of(name)
        mediaCheck(path.isAbsolute && !Files.isSymbolicLink(path) && Files.isRegularFile(path, NOFOLLOW_LINKS) && path == path.toRealPath(NOFOLLOW_LINKS),
            MediaCode.SOURCE_UNKNOWN, MediaStage.LOCAL_OWNER)
        val suffix = path.fileName.toString().substringAfterLast('.')
        val format = MediaFormat.entries.singleOrNull { it.suffix == suffix }
            ?: throw mediaFailure(MediaCode.UNSUPPORTED_MEDIA, MediaStage.LOCAL_OWNER)
        val mime = when (format) {
            MediaFormat.MP3 -> "audio/mpeg"; MediaFormat.AAC -> "audio/aac"; MediaFormat.M4A -> "audio/mp4"
            MediaFormat.FLAC -> "audio/flac"; MediaFormat.OPUS -> "audio/ogg"; MediaFormat.WAV -> "audio/wav"
        }
        val channel = FileChannel.open(path, READ, NOFOLLOW_LINKS)
        try {
            val identity = identity(path)
            mediaCheck(channel.size() == track.byteSize && track.byteSize in 1..DesktopMediaLimits.MAX_OBJECT_BYTES,
                MediaCode.REPRESENTATION_CHANGED, MediaStage.VERIFY)
            val hash = MessageDigest.getInstance("SHA-256"); val buffer = ByteBuffer.allocate(262_144)
            var offset = 0L
            while (offset < track.byteSize) {
                mediaCheck(!cancelled.get(), MediaCode.CANCELLED, MediaStage.VERIFY)
                buffer.clear(); buffer.limit(minOf(buffer.capacity().toLong(), track.byteSize - offset).toInt())
                val read = channel.read(buffer, offset)
                mediaCheck(read > 0, MediaCode.SOURCE_UNKNOWN, MediaStage.VERIFY)
                offset += read; buffer.flip(); hash.update(buffer)
            }
            buffer.array().fill(0)
            val digest = hash.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
            mediaCheck(digest == track.sha256 && identity(path) == identity && channel.size() == track.byteSize,
                MediaCode.CHECKSUM_MISMATCH, MediaStage.VERIFY)
            val lease = object : MediaLocalReadLease {
                val retired = AtomicBoolean(false)
                val releaseStarted = AtomicBoolean(false)
                val terminal = kotlinx.coroutines.CompletableDeferred<MediaHandleRelease>()
                override val copyId: MediaId = MediaId(track.id)
                override val representation = MediaRepresentation(digest, track.byteSize, mime, format, MediaRepresentationKind.INDEPENDENT_LOCAL)
                override val leaseRevision: Long = standalone.storeSelectionRevision
                override val ownership: MediaOwnedCopyReadback? = null
                override fun verifyCurrentFileIdentityAndIntegrity(): Unit = mediaCheck(!retired.get() && channel.isOpen,
                    MediaCode.OWNER_STALE, MediaStage.LOCAL_OWNER)
                override fun <T> withManagedFile(action: (MediaRuntimeSource.ManagedFile) -> T): T {
                    verifyCurrentFileIdentityAndIntegrity()
                    return action(MediaRuntimeSource.ManagedFile(path.toString(), identity))
                }
                override fun retire(): Unit { retired.set(true) }
                override suspend fun awaitReleased(): MediaHandleRelease {
                    retire()
                    if (releaseStarted.compareAndSet(false, true)) {
                        val actual = try {
                            channel.close()
                            mediaCheck(!channel.isOpen, MediaCode.DRAIN_UNCONFIRMED, MediaStage.DRAIN)
                            MediaHandleRelease.RELEASED
                        } catch (_: Exception) { MediaHandleRelease.FAILED_RETAINED }
                        terminal.complete(actual)
                    }
                    return terminal.await()
                }
            }
            work.retainLease(lease)
            MediaLocalLookup.Found(lease)
        } catch (failure: Exception) { channel.close(); throw failure }
    }
    private fun identity(path: Path): String = Files.readAttributes(path, "unix:dev,ino", NOFOLLOW_LINKS).let {
        "${(it["dev"] as Number).toLong()}:${(it["ino"] as Number).toLong()}"
    }
}
