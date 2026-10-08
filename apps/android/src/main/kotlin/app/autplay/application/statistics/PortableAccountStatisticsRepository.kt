package app.autplay.application.statistics

import androidx.room3.withReadTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.PortableAccountStatisticsEntity
import app.autplay.domain.ServerProfileId
import java.time.Clock
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull

/** Server snapshots are never added together: different servers may contain the same facts. */
class PortableAccountStatisticsRepository(
    private val database: AutPlayDatabase,
    private val clock: Clock = Clock.systemUTC(),
) {
    suspend fun refresh(accountId: String, profile: ServerProfileId?, remote: AccountStatisticsPort?): OwnerProfileStatistics {
        require(UUID.fromString(accountId).toString() == accountId)
        var authenticated: OwnerProfileStatistics? = null
        if (profile != null && remote != null) {
            try {
                val loaded = remote.load(profile, accountId)
                require(loaded.accountId == accountId && loaded.throughMs <= clock.millis() + 60_000L)
                val encoded = encodeAccountStatistics(loaded)
                // Revalidate the stored representation before accepting it as an offline fallback.
                decodeAccountStatistics(encoded.toByteArray(Charsets.UTF_8), accountId)
                database.portableAccountDao().putStatistics(PortableAccountStatisticsEntity(
                    accountId = accountId, profileId = profile.value, documentJson = encoded, throughMs = loaded.throughMs,
                ))
                authenticated = loaded
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { /* The account-owned Room cache survives connectivity failures. */ }
        }
        val local = normalizedSnapshot(accountId)
        if (authenticated != null) return if (local.listenedMs > authenticated.listenedMs) local else authenticated
        val saved = database.portableAccountDao().allStatistics(accountId).mapNotNull { row ->
            runCatching { decodeAccountStatistics(row.documentJson.toByteArray(Charsets.UTF_8), accountId) }
                .getOrNull()?.takeIf { it.throughMs <= clock.millis() + 60_000L }
        }.maxWithOrNull(compareBy<OwnerProfileStatistics> { it.listenedMs }.thenBy { it.throughMs })
        return if (saved != null && saved.listenedMs > local.listenedMs) saved else local
    }

    private suspend fun normalizedSnapshot(accountId: String): OwnerProfileStatistics = database.withReadTransaction {
        val builder = PortableStatisticsAccumulator(accountId, clock.millis())
        var after = ""
        var count = 0
        do {
            val page = database.portableAccountDao().kindPage(accountId, "LISTENING", after, 128)
            count += page.size
            require(count <= 200_000) { "ACCOUNT_STATISTICS_LIMIT" }
            page.forEach { builder.add(it.documentJson) }
            page.lastOrNull()?.let { after = it.recordId }
        } while (page.size == 128)
        builder.snapshot()
    }
}

/** Alias groups also cover a partially downloaded bridge between two older server copies. */
internal class PortableStatisticsAccumulator(private val accountId: String, private val through: Long) {
    private class Fact(val document: JsonObject, val updatedAt: Long) {
        val aliases = mutableSetOf<String>()
    }
    private val byAlias = mutableMapOf<String, Fact>()
    private val facts = mutableSetOf<Fact>()

    fun add(raw: String) {
        require(raw.toByteArray(Charsets.UTF_8).size <= 16_384)
        val document = Json.parseToJsonElement(raw) as? JsonObject ?: error("ACCOUNT_STATISTICS_INVALID")
        require(document.integer("version") == 1L)
        val aliases = (document["event_ids"] as? JsonArray ?: error("ACCOUNT_STATISTICS_INVALID")).map {
            val value = (it as? JsonPrimitive)?.takeIf { primitive -> primitive.isString }?.content
                ?: error("ACCOUNT_STATISTICS_INVALID")
            require(UUID.fromString(value).toString() == value)
            value
        }
        require(aliases.size in 1..32 && aliases.distinct().size == aliases.size)
        val matched = aliases.mapNotNull(byAlias::get).toSet()
        val updated = document.integer("updated_at_ms")
        val candidate = Fact(document, updated)
        val winner = (matched + candidate).maxBy { it.updatedAt }
        matched.forEach { previous ->
            facts.remove(previous)
            winner.aliases.addAll(previous.aliases)
        }
        winner.aliases.addAll(aliases)
        winner.aliases.forEach { byAlias[it] = winner }
        facts.add(winner)
    }

    fun snapshot(): OwnerProfileStatistics {
        data class Track(var title: String?, var artist: String?, var count: Long = 0, var played: Long = 0)
        val tracks = mutableMapOf<String, Track>()
        val artists = mutableMapOf<String, OwnerTopArtist>()
        val genres = mutableMapOf<String, OwnerTopGenre>()
        var listened = 0L
        facts.forEach { fact ->
            val document = fact.document
            val started = document.integer("started_at_ms")
            val played = document.integer("played_ms").also { require(it <= 604_800_000L) }
            if (started > through || played == 0L) return@forEach
            val trackKey = requireNotNull(document.text("track_key", 80))
            val title = document.text("title", 2000)
            val artist = document.text("artist", 2000)?.trim()?.takeIf(String::isNotEmpty)
            listened = Math.addExact(listened, played)
            val track = tracks.getOrPut(trackKey) { Track(title, artist) }
            if (track.title == null) track.title = title
            if (track.artist == null) track.artist = artist
            track.count++; track.played = Math.addExact(track.played, played)
            if (artist != null) {
                val previous = artists[artist]
                artists[artist] = OwnerTopArtist(artist, (previous?.playSessionCount ?: 0) + 1,
                    Math.addExact(previous?.listenedMs ?: 0, played))
            }
            val values = document["genres"] as? JsonArray ?: error("ACCOUNT_STATISTICS_INVALID")
            require(values.size <= 12)
            values.map { value ->
                (value as? JsonPrimitive)?.takeIf { it.isString }?.content?.trim()?.also {
                    require(it.length in 1..100 && it.none { char -> char.code < 32 })
                } ?: error("ACCOUNT_STATISTICS_INVALID")
            }.distinctBy { it.lowercase(Locale.ROOT) }.forEach { genre ->
                val key = genre.lowercase(Locale.ROOT)
                val previous = genres[key]
                genres[key] = OwnerTopGenre(previous?.genre ?: genre, Math.addExact(previous?.listenedMs ?: 0, played))
            }
        }
        return OwnerProfileStatistics(throughMs = through, listenedMs = listened,
            topGenres = genres.values.sortedWith(compareByDescending<OwnerTopGenre> { it.listenedMs }.thenBy { it.genre }).take(5),
            topTracks = tracks.map { (key, value) -> OwnerTopTrack(key, value.title, value.artist, value.count, value.played) }
                .sortedWith(compareByDescending<OwnerTopTrack> { it.listenedMs }.thenByDescending { it.playSessionCount }.thenBy { it.identityKey }).take(5),
            topArtists = artists.values.sortedWith(compareByDescending<OwnerTopArtist> { it.listenedMs }.thenByDescending { it.playSessionCount }.thenBy { it.artistName }).take(5),
            accountId = accountId)
    }
}

private fun JsonObject.integer(name: String): Long = (this[name] as? JsonPrimitive)?.takeUnless { it.isString }
    ?.longOrNull?.also { require(it >= 0) } ?: error("ACCOUNT_STATISTICS_INVALID")
private fun JsonObject.text(name: String, max: Int): String? = if (this[name] == JsonNull) null else
    (this[name] as? JsonPrimitive)?.takeIf { it.isString }?.content?.also {
        require(it.length in 1..max && it.none { char -> char.code < 32 })
    } ?: error("ACCOUNT_STATISTICS_INVALID")
