package app.autplay.ui

import app.autplay.application.artist.ArtistAppearance
import app.autplay.application.server.InternetMetadataDiscoveryCard
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive

internal data class ArtistRecording(
    val key: String,
    val local: ArtistAppearance?,
    val card: InternetMetadataDiscoveryCard?,
    val count: Long?,
    val guest: Boolean,
) {
    val catalogPerformers: List<Pair<String, String>> get() =
        card?.performerLinks().orEmpty()
    val title get() = local?.title ?: card?.title
    val displayKey get() = local?.displayKey ?: key
}

internal fun artistRecordings(state: CoreProductDetailUiState): List<ArtistRecording> {
    val publicCards = state.publicRecordings.filter { it.entityType == "recording" && it.recordingId == it.entityId }
        .distinctBy { it.entityId }.associateBy { it.entityId }
    val byLocalId = state.libraryRecordings.entries.associate { (mbid, id) -> id to mbid }
    val rows = linkedMapOf<String, ArtistRecording>()
    fun guest(card: InternetMetadataDiscoveryCard?) =
        (card?.rawPayload?.get("artist_members") as? JsonArray).orEmpty().size > 1 ||
        (card?.rawPayload?.get("guest_artist_ids") as? JsonArray).orEmpty()
            .any { (it as? JsonPrimitive)?.content == state.artistMbid }
    for (local in state.artistAppearances.filter { it.subjectType == "RECORDING" }) {
        val mbid = byLocalId[local.displayKey]
        val key = mbid ?: "library:${local.displayKey}"
        val card = publicCards[mbid]
        rows.putIfAbsent(key, ArtistRecording(key, local, card,
            state.publicCounts[mbid] ?: state.ranking[local.displayKey] ?: local.listenCount.toLong().takeIf { it > 0 }, local.guest || guest(card)))
    }
    for ((mbid, card) in publicCards) {
        rows.putIfAbsent(mbid, ArtistRecording(mbid, null, card, state.publicCounts[mbid], guest(card)))
    }
    return rows.values.sortedWith(compareByDescending<ArtistRecording> { it.count }
        .thenBy(String.CASE_INSENSITIVE_ORDER) { it.title.orEmpty() }.thenBy { it.key })
}

internal fun InternetMetadataDiscoveryCard.performerLinks(): List<Pair<String, String>> =
        (rawPayload["artist_members"] as? JsonArray).orEmpty().mapNotNull { value ->
            val member = value as? kotlinx.serialization.json.JsonObject ?: return@mapNotNull null
            val id = (member["mbid"] as? JsonPrimitive)?.content ?: return@mapNotNull null
            val name = (member["name"] as? JsonPrimitive)?.content ?: return@mapNotNull null
            id.takeIf { runCatching { java.util.UUID.fromString(it).toString() == it }.getOrDefault(false) }?.let { it to name }
        }
