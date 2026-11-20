package app.autplay.playback

import androidx.media3.common.Metadata
import androidx.media3.common.util.UnstableApi
import androidx.media3.extractor.metadata.id3.CommentFrame
import androidx.media3.extractor.metadata.id3.InternalFrame
import androidx.media3.extractor.metadata.id3.TextInformationFrame
import androidx.media3.extractor.metadata.vorbis.VorbisComment

/** Reads the decoder format, not UI metadata: preloading must not change the departing track's gain. */
@UnstableApi
internal fun replayGainTags(metadata: Metadata?): ReplayGainTags {
    if (metadata == null) return ReplayGainTags()
    val entries = buildList {
        for (index in 0 until minOf(metadata.length(), 512)) {
            when (val entry = metadata[index]) {
                is VorbisComment -> add(entry.key to entry.value)
                is TextInformationFrame -> if (entry.id == "TXXX") {
                    entry.values.firstOrNull()?.let { add(entry.description.orEmpty() to it) }
                }
                is InternalFrame -> if (entry.domain == "com.apple.iTunes") add(entry.description to entry.text)
                is CommentFrame -> add(entry.description to entry.text)
            }
        }
    }
    return ReplayGainTags.parse(entries)
}
