package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.account.*
import app.autplay.data.desktop.account.AccountSyncWireV1
import kotlinx.serialization.json.*

internal data class StandaloneFactHeader(val changeId: String, val kind: String, val aggregateId: String,
    val occurredAtMs: Long, val version: Int, val payloadBytes: Long)

internal data class StandaloneSelectedRows(val facts: List<LocalFactRow>, val dependencies: Map<String, JsonObject>)

internal interface StandaloneAccountSourceOwner {
    fun requireCanonicalAccountOwner(): Unit
    fun accountReview(): DesktopStandaloneAccountReview
    fun accountSource(registration: AccountSourceRegistration): DesktopStandaloneAccountSource
}

fun desktopStandaloneAccountReview(store: DesktopLibraryStore): DesktopStandaloneAccountReview =
    (store as? StandaloneAccountSourceOwner)?.accountReview()
        ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)

fun requireDesktopStandaloneAccountOwner(store: DesktopLibraryStore): Unit =
    (store as? StandaloneAccountSourceOwner)?.requireCanonicalAccountOwner()
        ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)

/** Only the actual canonical Room owner supplies this port; no database/path/UUID import seam. */
fun desktopStandaloneAccountSource(store: DesktopLibraryStore,
    registration: AccountSourceRegistration): DesktopStandaloneAccountSource =
    (store as? StandaloneAccountSourceOwner)?.accountSource(registration)
        ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)

internal fun standaloneFactEligible(header: StandaloneFactHeader): Boolean =
    header.version == 1 && header.payloadBytes in 2..DesktopAccountLimits.PAYLOAD_BYTES.toLong() &&
        header.changeId.length in 1..1_024 && header.aggregateId.length in 1..1_024 &&
        header.occurredAtMs in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS && header.kind in setOf(
            "TRACK_IMPORTED", "TRACK_PREFERENCE_UPDATED", "TRACK_METADATA_UPDATED", "TRACK_TEXT_PROVENANCE_UPDATED",
            "TRACK_REMOVED_FROM_COLLECTION", "TRACK_RESTORED_TO_COLLECTION", "PLAYLIST_CREATED", "PLAYLIST_RENAMED",
            "PLAYLIST_DESCRIPTION_UPDATED", "PLAYLIST_DELETED", "PLAYLIST_ENTRY_APPENDED", "PLAYLIST_ENTRY_REMOVED",
            "PLAYLIST_ENTRY_MOVED",
        )

/** Called in one Room read transaction, with the standalone owner's admission mutex held. */
internal suspend fun readStandaloneSelectedRows(dao: DesktopDao, ids: List<String>): StandaloneSelectedRows {
    require(ids.size in 1..100 && ids.distinct().size == ids.size && ids.all { it.length in 1..1_024 })
    val headers = dao.accountSelectedHeaders(ids)
    require(headers.size == ids.size && headers.map { it.changeId }.toSet() == ids.toSet() &&
        headers.all(::standaloneFactEligible)) { "desktop_account_source_changed" }
    // Leave room for framing/dependencies before materializing any selected payload.
    require(headers.sumOf { it.payloadBytes } <= DesktopAccountLimits.PAGE_BYTES - 524_288) {
        "desktop_account_source_capacity"
    }
    val facts = dao.accountSelectedFacts(ids)
    require(facts.size == headers.size && facts.all { fact ->
        headers.single { it.changeId == fact.changeId }.let {
            it.kind == fact.kind && it.aggregateId == fact.aggregateId && it.occurredAtMs == fact.occurredAtMs &&
                it.version == fact.version && it.payloadBytes == fact.payloadJson.toByteArray(Charsets.UTF_8).size.toLong()
        }
    }) { "desktop_account_source_changed" }
    val dependencies = LinkedHashMap<String, JsonObject>()
    val requestedTracks = LinkedHashSet<String>()
    val requestedPlaylists = LinkedHashSet<String>()
    val requestedEntries = LinkedHashSet<String>()
    fun collect(value: JsonElement, depth: Int = 0) {
        require(depth <= 8) { "desktop_account_source_invalid" }
        when (value) {
            is JsonObject -> value.forEach { (key, child) ->
                val id = (child as? JsonPrimitive)?.takeIf { it.isString }?.content
                if (id != null && key in setOf("track_id", "playlist_id", "entry_id")) {
                    require(id.length in 1..1_024)
                    when (key) {
                        "track_id" -> requestedTracks.add(id)
                        "playlist_id" -> requestedPlaylists.add(id)
                        "entry_id" -> requestedEntries.add(id)
                    }
                }
                if (child is JsonObject || child is JsonArray) collect(child, depth + 1)
            }
            is JsonArray -> value.forEach { collect(it, depth + 1) }
            else -> Unit
        }
        require(requestedTracks.size + requestedPlaylists.size + requestedEntries.size <= 100) {
            "desktop_account_source_capacity"
        }
    }
    for (fact in facts) {
        val document = AccountSyncWireV1.document(fact.payloadJson.toByteArray(Charsets.UTF_8))
        require(document.keys == setOf("version", "mutation", "snapshot") &&
            (document["version"] as? JsonPrimitive)?.intOrNull == fact.version &&
            (document["mutation"] as? JsonPrimitive)?.content == fact.kind)
        val snapshot = document["snapshot"] as? JsonObject ?: error("desktop_account_source_invalid")
        collect(snapshot)
    }
    fun put(id: String, document: JsonObject) {
        val previous = dependencies.putIfAbsent(id, document)
        require(previous == null || previous == document) { "desktop_account_source_identity_conflict" }
        require(dependencies.size <= 100 && AccountPortableV1.canonical(JsonObject(dependencies)).size <= 393_216) {
            "desktop_account_source_capacity"
        }
    }
    for (id in requestedEntries) dao.accountPlaylistEntry(id)?.let { entry ->
        requestedTracks.add(entry.trackId); requestedPlaylists.add(entry.playlistId)
        put(id, buildJsonObject {
            put("entry_id", id); put("playlist_id", entry.playlistId); put("track_id", entry.trackId)
            put("ordinal", entry.ordinal.toString())
        })
    }
    require(requestedTracks.size + requestedPlaylists.size + requestedEntries.size <= 100) {
        "desktop_account_source_capacity"
    }
    for (id in requestedTracks) dao.track(id)?.let { track -> put(id, buildJsonObject {
        put("track_id", id); put("title", track.title); put("artist", track.artist)
    }) }
    for (id in requestedPlaylists) dao.playlist(id)?.let { playlist -> put(id, buildJsonObject {
        put("playlist_id", id); put("name", playlist.name)
        put("description", playlist.description?.let(::JsonPrimitive) ?: JsonNull)
    }) }
    // Deleted dependencies stay absent. J may use a prior exact mapping, never a guessed baseline.
    return StandaloneSelectedRows(facts, dependencies)
}
