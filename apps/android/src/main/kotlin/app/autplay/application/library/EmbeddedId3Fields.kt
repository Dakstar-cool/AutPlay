package app.autplay.application.library

import android.content.Context
import android.net.Uri
import androidx.media3.common.util.UnstableApi
import androidx.media3.extractor.metadata.id3.Id3Decoder
import androidx.media3.extractor.metadata.id3.TextInformationFrame
import java.io.DataInputStream
import kotlinx.serialization.json.*

/** Some platform retrievers omit ID3v2.4 dates. Decode bounded text frames with Media3. */
@androidx.annotation.OptIn(UnstableApi::class)
internal fun embeddedId3Fields(context: Context, uri: Uri): JsonObject = try {
    context.contentResolver.openInputStream(uri)?.use { raw ->
        val input = DataInputStream(raw)
        val header = ByteArray(10)
        input.readFully(header)
        if (String(header, 0, 3, Charsets.US_ASCII) != "ID3" || (6..9).any { header[it].toInt() < 0 }) return@use JsonObject(emptyMap())
        val length = (6..9).fold(0) { value, index -> (value shl 7) or header[index].toInt() }
        if (length !in 1..4_194_304) return@use JsonObject(emptyMap())
        val bytes = ByteArray(length + 10)
        header.copyInto(bytes)
        input.readFully(bytes, 10, length)
        val decoded = Id3Decoder { _, first, _, _, _ -> first == 'T'.code }.decode(bytes, bytes.size)
            ?: return@use JsonObject(emptyMap())
        val frames = (0 until decoded.length()).mapNotNull { decoded[it] as? TextInformationFrame }
        val fields = linkedMapOf<String, JsonElement>()
        frames.sortedBy { if (it.id in setOf("TYER", "TYE", "TORY")) 0 else 1 }.forEach { frame ->
            val value = frame.values.firstOrNull()?.trim()?.takeIf { it.length in 1..500 && it.none { c -> c.code < 32 } } ?: return@forEach
            val key = when (frame.id) {
                "TIT2", "TT2" -> "title"; "TPE1", "TP1" -> "artist"; "TALB", "TAL" -> "album"
                "TPE2", "TP2" -> "album_artist"; "TPUB", "TPB" -> "label"
                "TDRC", "TDRL", "TYER", "TYE" -> "release_date"; "TDOR", "TORY" -> "original_release_date"
                "TRCK", "TRK" -> "track_number"; "TPOS", "TPA" -> "disc_number"
                else -> null
            } ?: return@forEach
            if (key.endsWith("_date")) {
                if (validMetadataDate(value)) fields[key] = JsonPrimitive(value)
            } else if (key.endsWith("_number")) {
                value.substringBefore('/').toIntOrNull()?.takeIf { it in 1..9999 }?.let { fields[key] = JsonPrimitive(it) }
            } else fields[key] = JsonPrimitive(value)
        }
        JsonObject(fields)
    } ?: JsonObject(emptyMap())
} catch (_: java.io.IOException) { JsonObject(emptyMap())
} catch (_: RuntimeException) { JsonObject(emptyMap()) }

internal fun validMetadataDate(value: String): Boolean = runCatching {
    require(value.matches(Regex("[0-9]{4}(-[0-9]{2})?(-[0-9]{2})?")))
    when (value.length) {
        4 -> require(java.time.Year.parse(value).value in 1..9999)
        7 -> require(java.time.YearMonth.parse(value).year in 1..9999)
        10 -> require(java.time.LocalDate.parse(value).year in 1..9999)
        else -> error("date")
    }
}.isSuccess
