package app.autplay.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.platform.LocalContext
import app.autplay.AutPlayRuntime
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.settings.applicationNonSecretSettingsStore
import java.io.File
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext

private val coverRequests = Semaphore(2)

@Composable
internal fun rememberCatalogCover(id: String?): Painter? {
    if (id == null || runCatching { UUID.fromString(id).toString() }.getOrNull() != id) return null
    val context = LocalContext.current
    val settings by remember(context) { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val user = settings?.activeUserId ?: return null
    val device = settings?.deviceId ?: return null
    val profile = settings?.activeServerProfileId ?: return null
    val bitmap by key(profile, user, id) {
        produceState<Bitmap?>(initialValue = null) {
            withContext(Dispatchers.IO) {
                val directory = File(context.cacheDir, "catalog-art").apply { mkdirs() }
                val file = File(directory, id)
                if (file.isFile && file.length() in 1..1_048_576) {
                    value = decodeCover(file.readBytes())
                    if (value != null && System.currentTimeMillis() - file.lastModified() in 0 until 604_800_000L) return@withContext
                }
                try {
                    coverRequests.withPermit {
                        val bytes = AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile)).catalogCover(id)
                        val image = decodeCover(bytes) ?: return@withPermit
                        val temp = File.createTempFile("cover-", ".tmp", directory)
                        try { temp.writeBytes(bytes); temp.renameTo(file) } finally { temp.delete() }
                        value = image
                    }
                } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { /* Optional artwork. */ }
            }
        }
    }
    return rememberArtworkPainter(bitmap)
}

private fun decodeCover(bytes: ByteArray): Bitmap? {
    if (bytes.size !in 1..1_048_576) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
    if (bounds.outWidth !in 1..2048 || bounds.outHeight !in 1..2048) return null
    return BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
}
