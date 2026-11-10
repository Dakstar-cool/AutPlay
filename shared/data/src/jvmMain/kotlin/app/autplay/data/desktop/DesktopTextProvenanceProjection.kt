package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopEmbeddedTextFormat
import app.autplay.application.desktop.DesktopTrackFieldProvenance
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopTrackTextOrigin
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.application.desktop.validDesktopEmbeddedText
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Called only inside a DAO transaction. UUID ordering is pagination, NEVER mutation order.
 * No history is discarded or rewritten. Above the reconstruction budget the answer is UNKNOWN.
 * SQL counts are scalar anchors over immutable local facts, not an authenticated journal.
 * All legacy/new metadata writers must keep appending their existing metadata fact. The frozen
 * schema has no aggregate index: scalar COUNT/guard queries may scan the outbox; this bounds
 * materialized history, not query latency. Unsupported history is retained, never compacted here.
 */
internal object DesktopTextProvenanceProjection {
    const val MAX_FACTS: Int = 256
    const val PAGE_SIZE: Int = 32
    const val MAX_PROVENANCE_BYTES: Int = 131_072

    private data class Step(
        val sequence: Long, val revision: Long, val metadataId: String,
        val beforeTitle: String, val beforeArtist: String, val afterTitle: String, val afterArtist: String,
        val operation: String, val format: DesktopEmbeddedTextFormat?,
    )

    suspend fun read(dao: DesktopDao, row: TrackRow): DesktopTrackTextProvenance {
        val revision = dao.metadataFactCount(row.id)
        val unknown = DesktopTrackTextProvenance(
            DesktopTrackTextBaseline(row.id, row.title, row.artist, row.sha256, row.byteSize), revision,
        )
        val count = dao.textProvenanceFactCount(row.id)
        if (count !in 1..MAX_FACTS.toLong() || dao.unsupportedTrackFact(row.id) != null) return unknown
        val steps = ArrayList<Step>(count.toInt())
        var cursor: String? = null
        while (steps.size < count) {
            currentCoroutineContext().ensureActive()
            val page = dao.textProvenanceFactPage(row.id, cursor, PAGE_SIZE)
            if (page.isEmpty()) return unknown
            for (fact in page) {
                steps.add(decode(fact, row) ?: return unknown)
                if (steps.size > count) return unknown
            }
            cursor = page.last().changeId
        }
        // Complete sequence coverage detects a truncated prefix/middle as well as duplicate order.
        val ordered = steps.sortedBy { it.revision }
        var previous: Step? = null
        var title = DesktopTrackFieldProvenance()
        var artist = DesktopTrackFieldProvenance()
        val linked = HashSet<String>()
        for ((index, step) in ordered.withIndex()) {
            currentCoroutineContext().ensureActive()
            val prior = previous
            if (step.sequence != index.toLong() + 1 || step.revision <= (prior?.revision ?: 0) ||
                !linked.add(step.metadataId)) return unknown
            val metadata = dao.textProvenanceMetadataFact(step.metadataId) ?: return unknown
            if (!matchesMetadata(metadata, row, step)) return unknown
            if (prior == null || step.revision != prior.revision + 1) {
                // Legacy writers have no field intent. Even A->B->A invalidates previous evidence.
                title = DesktopTrackFieldProvenance()
                artist = DesktopTrackFieldProvenance()
            } else if (step.beforeTitle != prior.afterTitle || step.beforeArtist != prior.afterArtist) return unknown
            if (step.operation == "DURATION" &&
                (step.beforeTitle != step.afterTitle || step.beforeArtist != step.afterArtist)) return unknown
            val changed = when (step.operation) {
                "USER" -> DesktopTrackFieldProvenance(DesktopTrackTextOrigin.USER)
                "EMBEDDED" -> DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, step.format)
                else -> DesktopTrackFieldProvenance()
            }
            if (step.beforeTitle != step.afterTitle) title = changed
            if (step.beforeArtist != step.afterArtist) artist = changed
            previous = step
        }
        val last = previous ?: return unknown
        if (last.revision != revision || last.afterTitle != row.title || last.afterArtist != row.artist) return unknown
        return unknown.copy(title = title, artist = artist)
    }

    private fun decode(fact: LocalFactRow, row: TrackRow): Step? = parse {
        val value = snapshot(fact, "TRACK_TEXT_PROVENANCE_UPDATED", MAX_PROVENANCE_BYTES)
        require(value.keys == setOf("track_id", "revision", "sequence", "metadata_change_id", "operation", "format",
            "before_title", "before_artist", "after_title", "after_artist", "sha256", "byte_size"))
        identity(value, fact, row)
        val formatValue = value.getValue("format")
        val format = if (formatValue == JsonNull) null else DesktopEmbeddedTextFormat.valueOf(string(value, "format"))
        val operation = string(value, "operation")
        require(operation in setOf("USER", "EMBEDDED", "COMPATIBILITY", "DURATION"))
        require((operation == "EMBEDDED") == (format != null))
        val metadataId = string(value, "metadata_change_id")
        require(metadataId.matches(Regex("[A-Za-z0-9_-]{1,128}")))
        Step(positive(value, "sequence"), positive(value, "revision"), metadataId,
            string(value, "before_title"), string(value, "before_artist"),
            string(value, "after_title"), string(value, "after_artist"), operation, format)
    }

    private fun matchesMetadata(fact: LocalFactRow, row: TrackRow, step: Step): Boolean = parse {
        val value = snapshot(fact, "TRACK_METADATA_UPDATED", 262_144)
        require(value.keys == setOf("track_id", "title", "artist", "duration_ms", "sha256", "byte_size"))
        identity(value, fact, row)
        require(string(value, "title") == step.afterTitle && string(value, "artist") == step.afterArtist)
        val duration = value.getValue("duration_ms")
        require(duration == JsonNull || string(value, "duration_ms").toLongOrNull() != null)
        true
    } ?: false

    private fun snapshot(fact: LocalFactRow, kind: String, maxBytes: Int): JsonObject {
        require(fact.version == 1 && fact.kind == kind && fact.payloadJson.length <= maxBytes)
        require(fact.payloadJson.toByteArray(Charsets.UTF_8).size <= maxBytes)
        requireBoundedNesting(fact.payloadJson)
        val document = Json.parseToJsonElement(fact.payloadJson) as JsonObject
        // Writer canonical form also rejects duplicate members, trailing bytes and alternate encodings.
        require(document.toString() == fact.payloadJson && document.keys == setOf("version", "mutation", "snapshot"))
        require(document.getValue("version") == JsonPrimitive(1) && string(document, "mutation") == kind)
        return document.getValue("snapshot") as JsonObject
    }

    private fun requireBoundedNesting(value: String): Unit {
        var depth = 0
        var quoted = false
        var escaped = false
        for (character in value) {
            if (quoted) {
                if (escaped) escaped = false
                else if (character == '\\') escaped = true
                else if (character == '"') quoted = false
            } else when (character) {
                '"' -> quoted = true
                '{', '[' -> { depth++; require(depth <= 8) }
                '}', ']' -> { depth--; require(depth >= 0) }
            }
        }
        require(depth == 0 && !quoted)
    }

    private fun identity(value: JsonObject, fact: LocalFactRow, row: TrackRow): Unit {
        require(fact.aggregateId == row.id && string(value, "track_id") == row.id &&
            string(value, "sha256") == row.sha256 && string(value, "byte_size") == row.byteSize.toString())
    }

    private fun string(value: JsonObject, key: String): String {
        val primitive = value.getValue(key) as JsonPrimitive
        require(primitive.isString)
        require(validDesktopEmbeddedText(primitive.content, allowBlank = true))
        return primitive.content
    }

    private fun positive(value: JsonObject, key: String): Long {
        val text = string(value, key)
        val number = text.toLong()
        require(number > 0 && number.toString() == text)
        return number
    }

    private inline fun <T> parse(block: () -> T): T? = try { block() } catch (_: IllegalArgumentException) {
        null
    } catch (_: IllegalStateException) {
        null
    } catch (_: ClassCastException) {
        null
    } catch (_: NoSuchElementException) {
        null
    }
}
