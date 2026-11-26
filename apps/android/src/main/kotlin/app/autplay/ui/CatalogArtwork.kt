package app.autplay.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.platform.LocalContext
import app.autplay.AutPlayRuntime
import app.autplay.application.artist.musicSearchKey
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.application.library.TrackMetadataRepository
import kotlinx.serialization.json.*
import java.io.File
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

private val coverRequests = Semaphore(2)

@Composable
internal fun rememberCatalogCover(id: String?, portrait: Boolean = false): Painter? =
    rememberArtworkPainter(rememberCatalogBitmap(id, portrait))

@Composable
internal fun rememberCatalogBitmap(id: String?, portrait: Boolean = false): Bitmap? {
    if (id == null || runCatching { UUID.fromString(id).toString() }.getOrNull() != id) return null
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val user = settings?.activeUserId ?: return null
    val device = settings?.deviceId ?: return null
    val profile = settings?.activeServerProfileId ?: return null
    val bitmap by key(profile, user, id, portrait) {
        produceState<Bitmap?>(initialValue = null) {
            withContext(Dispatchers.IO) {
                val directory = File(context.cacheDir, "catalog-art").apply { mkdirs() }
                val file = File(directory, if (portrait) "artist-$id" else id)
                val missing = File(directory, file.name + ".missing")
                try {
                    val lock = downloadLocks[(file.name.hashCode() and Int.MAX_VALUE) % downloadLocks.size]
                    lock.withLock {
                        if (file.isFile && file.length() in 1..1_048_576) {
                            value = decodeCover(file.readBytes())
                            if (value != null && System.currentTimeMillis() - file.lastModified() in 0 until 604_800_000L) return@withLock
                        }
                        if (missing.isFile && System.currentTimeMillis() - missing.lastModified() in 0 until 43_200_000L) return@withLock
                        coverRequests.withPermit {
                            val api = AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile))
                            val bytes = if (portrait) api.catalogArtistPortrait(id) else api.catalogCover(id)
                            val image = decodeCover(bytes) ?: return@withPermit
                            val temp = File.createTempFile("cover-", ".tmp", directory)
                            try { temp.writeBytes(bytes); temp.renameTo(file) } finally { temp.delete() }
                            missing.delete()
                            value = image
                        }
                    }
                } catch (cancelled: CancellationException) { throw cancelled } catch (error: Exception) {
                    if (error.message == "SERVER_HTTP_404") runCatching { missing.writeText("") }
                }
            }
        }
    }
    return bitmap
}

private fun decodeCover(bytes: ByteArray): Bitmap? {
    if (bytes.size !in 1..1_048_576) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth !in 1..2048 || bounds.outHeight !in 1..2048) return null
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
}

@Composable
internal fun rememberLibraryPortrait(name: String, tracks: List<String>, artists: List<String> = emptyList()): Painter? {
    if (tracks.isEmpty() && artists.isEmpty()) return null
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val user = settings?.activeUserId ?: return null
    val device = settings?.deviceId ?: return null
    val profile = settings?.activeServerProfileId ?: return null
    val database = remember(context) { AutPlayRuntime.database(context) }
    val evidence by key(database, profile, user, tracks, artists) {
        remember {
            val ids = tracks.distinct().chunked(400)
            val credits = artists.distinct().chunked(400)
            val queries = List(maxOf(ids.size, credits.size)) { i ->
                database.trackMetadataDao().portraitSources(profile.value, ids.getOrElse(i) { emptyList() }, credits.getOrElse(i) { emptyList() })
            }
            if (queries.size == 1) queries.single() else kotlinx.coroutines.flow.combine(queries) { pages ->
                pages.flatMap { it }.distinctBy { it.ref }
            }
        }.collectAsState(initial = emptyList())
    }
    val portraits = remember(evidence, name) {
        val artistName = musicSearchKey(name)
        evidence.mapNotNull { row ->
            val image = runCatching { Json.parseToJsonElement(row.image).jsonObject }.getOrNull() ?: return@mapNotNull null
            fun field(key: String) = (image[key] as? JsonPrimitive)?.contentOrNull
            val artist = field("artist_id")?.toLongOrNull()?.takeIf { it > 0 } ?: return@mapNotNull null
            val sha = field("sha256") ?: return@mapNotNull null
            val provider = field("provider")
            if (provider in setOf("DEEZER", "YANDEX") && field("name")?.let(::musicSearchKey) == artistName &&
                sha.matches(Regex("[a-f0-9]{64}"))) Triple(row.ref, provider to artist, sha) else null
        }
    }
    if (portraits.isEmpty()) return null
    val byProvider = portraits.groupBy { it.second.first }
    if (byProvider.values.any { group -> group.map { it.second.second }.distinct().size > 1 }) return null
    val (ref, _, sha) = byProvider.values.maxBy { it.size }.first()
    return rememberArtworkPainter(rememberMetadataBitmap(profile.value, sha, ref, ClientEventBinding(user, device, profile)))
}

private val downloadLocks = Array(32) { kotlinx.coroutines.sync.Mutex() }

@Composable
internal fun rememberMetadataBitmap(profile: String, sha: String, ref: String?, binding: ClientEventBinding?, path: String? = null): Bitmap? {
    val context = LocalContext.current
    val database = remember(context) { AutPlayRuntime.database(context) }
    return key(profile, sha, ref, binding, path) {
        produceState<Bitmap?>(initialValue = null) {
            value = withContext(Dispatchers.IO) {
                val dao = database.trackMetadataDao()
                fun decodeCached(path: String?): Bitmap? {
                    if (path == null) return null
                    val file = File(path)
                    val root = File(context.filesDir, "metadata-art").canonicalPath + File.separator
                    return if (file.canonicalPath.startsWith(root) && file.isFile && file.length() in 1..2_097_152)
                        BitmapFactory.decodeFile(file.path) else null
                }
                try {
                    downloadLocks[(sha.hashCode() and Int.MAX_VALUE) % downloadLocks.size].withLock {
                        decodeCached(path ?: dao.artwork(profile, sha)?.filePath) ?: if (binding != null && ref != null) {
                            coverRequests.withPermit {
                                val bytes = AutPlayRuntime.serverFeatures(context, binding).metadataArtwork(ref, sha)
                                TrackMetadataRepository(context, database).storeArtwork(profile, bytes, sha)
                                BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                            }
                        } else null
                    }
                } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { null }
            }
        }.value
    }
}
