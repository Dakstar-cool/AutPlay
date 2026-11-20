package app.autplay.application.statistics

import app.autplay.application.recommendation.VariationSkips
import app.autplay.data.network.readCancellable
import app.autplay.data.network.withAutPlayRedirectPolicy
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionAccess
import app.autplay.domain.ServerProfileId
import java.time.Duration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import okhttp3.Request
import org.erdtman.jcs.JsonCanonicalizer

fun interface AccountStatisticsPort {
    suspend fun load(profile: ServerProfileId, expectedAccountId: String): OwnerProfileStatistics
}

/** The account is taken from the active authenticated binding, never from a URL or QR label. */
class OkHttpAccountStatisticsPort(
    private val apiBaseUrl: String,
    credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient(),
    rotation: M5SessionRotationClient? = null,
) : AccountStatisticsPort {
    private val client = client.newBuilder().cache(null).callTimeout(Duration.ofSeconds(30)).build().withAutPlayRedirectPolicy()
    private val sessions = RefreshingSessionCredentials(apiBaseUrl, credentials, this.client, m5Rotation = rotation)

    override suspend fun load(profile: ServerProfileId, expectedAccountId: String): OwnerProfileStatistics =
        decodeAccountStatistics(request(profile, "/social/profile-statistics/owner"), expectedAccountId)

    internal suspend fun loadSonaVariationSkips(profile: ServerProfileId, expectedAccountId: String): SonaVariationSkipReport =
        decodeSonaVariationSkips(request(profile, "/privacy/shared-training/sona-variation-skips"), expectedAccountId)

    private suspend fun request(profile: ServerProfileId, path: String): ByteArray = withContext(Dispatchers.IO) {
        var access = sessions.access(profile)
        try {
            var result = fetch(access, path)
            if (result.first == 401) {
                val generation = access.generation
                access.close()
                access = sessions.refreshAfterRejection(profile, generation)
                result = fetch(access, path)
            }
            require(result.first == 200) { "ACCOUNT_STATISTICS_UNAVAILABLE" }
            result.second
        } finally { access.close() }
    }

    private suspend fun fetch(access: SessionAccess, path: String): Pair<Int, ByteArray> {
        val request = Request.Builder().url(apiBaseUrl.trimEnd('/') + path)
            .header("Authorization", "Bearer " + access.token.toString(Charsets.UTF_8))
            .header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache").get().build()
        return client.newCall(request).readCancellable { response ->
            val source = response.body.source()
            require(!source.request(65_537)) { "ACCOUNT_STATISTICS_UNAVAILABLE" }
            response.code to source.readByteArray()
        }
    }
}

internal fun decodeAccountStatistics(bytes: ByteArray, expectedAccountId: String): OwnerProfileStatistics {
    val root = statisticsDocument(bytes)
    // Older owner caches can contain this field. Discard it before displaying or persisting totals.
    require(root.keys - "variation_skips" == setOf("schema_version", "account_id", "through_ms", "listened_ms", "top_genres", "top_tracks", "top_artists"))
    require(root.number("schema_version") == 1L && root.text("account_id") == expectedAccountId) { "ACCOUNT_STATISTICS_IDENTITY_CHANGED" }
    fun rows(name: String): List<JsonObject> = (root[name] as? JsonArray ?: error("ACCOUNT_STATISTICS_INVALID")).also {
        require(it.size <= 5)
    }.map { it.jsonObject }
    return OwnerProfileStatistics(
        throughMs = root.number("through_ms"), listenedMs = root.number("listened_ms"),
        topGenres = rows("top_genres").map { row ->
            require(row.keys == setOf("genre", "listened_ms"))
            OwnerTopGenre(row.text("genre", 100), row.number("listened_ms"))
        },
        topTracks = rows("top_tracks").map { row ->
            require(row.keys == setOf("identity_key", "title", "artist_name", "play_session_count", "listened_ms"))
            OwnerTopTrack(row.text("identity_key", 80), row.nullableText("title"), row.nullableText("artist_name"),
                row.number("play_session_count"), row.number("listened_ms"))
        },
        topArtists = rows("top_artists").map { row ->
            require(row.keys == setOf("artist_name", "play_session_count", "listened_ms"))
            OwnerTopArtist(row.nullableText("artist_name"), row.number("play_session_count"), row.number("listened_ms"))
        },
        accountId = expectedAccountId,
    )
}

/** The cache uses the same bounded, identity-checked contract as the authenticated response. */
internal fun encodeAccountStatistics(snapshot: OwnerProfileStatistics): String = buildJsonObject {
    put("schema_version", 1)
    put("account_id", requireNotNull(snapshot.accountId))
    put("through_ms", snapshot.throughMs)
    put("listened_ms", snapshot.listenedMs)
    put("top_genres", buildJsonArray {
        snapshot.topGenres.forEach { genre -> add(buildJsonObject {
            put("genre", genre.genre); put("listened_ms", genre.listenedMs)
        }) }
    })
    put("top_tracks", buildJsonArray {
        snapshot.topTracks.forEach { track -> add(buildJsonObject {
            put("identity_key", track.identityKey); put("title", track.title?.let(::JsonPrimitive) ?: JsonNull)
            put("artist_name", track.artistName?.let(::JsonPrimitive) ?: JsonNull)
            put("play_session_count", track.playSessionCount); put("listened_ms", track.listenedMs)
        }) }
    })
    put("top_artists", buildJsonArray {
        snapshot.topArtists.forEach { artist -> add(buildJsonObject {
            put("artist_name", artist.artistName?.let(::JsonPrimitive) ?: JsonNull)
            put("play_session_count", artist.playSessionCount); put("listened_ms", artist.listenedMs)
        }) }
    })
}.toString()

internal data class SonaVariationSkipReport(val rows: List<VariationSkips>, val remainingMs: Long)

internal fun decodeSonaVariationSkips(bytes: ByteArray, expectedAccountId: String): SonaVariationSkipReport {
    val root = statisticsDocument(bytes)
    require(root.keys == setOf("schema_version", "account_id", "remaining_ms", "variation_skips"))
    require(root.number("schema_version") == 1L && root.text("account_id") == expectedAccountId)
    val remaining = root.number("remaining_ms")
    require(remaining > 0)
    val entries = root["variation_skips"] as JsonArray
    require(entries.size <= 6)
    val rows = entries.map { entry ->
        val row = entry.jsonObject
        require(row.keys == setOf("surface", "kind", "plays", "skips"))
        VariationSkips(row.text("surface"), row.text("kind"), row.number("plays"), row.number("skips"))
    }
    require(rows.distinctBy { it.surface to it.kind }.size == rows.size)
    return SonaVariationSkipReport(rows, remaining)
}

private fun statisticsDocument(bytes: ByteArray): JsonObject {
    require(bytes.size in 2..65_536)
    val raw = Charsets.UTF_8.newDecoder().decode(java.nio.ByteBuffer.wrap(bytes)).toString()
    JsonCanonicalizer(raw)
    return Json.parseToJsonElement(raw).jsonObject
}

private fun JsonObject.number(name: String): Long = (this[name] as? JsonPrimitive)?.takeUnless { it.isString }
    ?.longOrNull?.also { require(it >= 0) } ?: error("ACCOUNT_STATISTICS_INVALID")
private fun JsonObject.text(name: String, max: Int = 2000): String = (this[name] as? JsonPrimitive)?.takeIf { it.isString }
    ?.content?.also { require(it.length in 1..max && it.none { char -> char.code < 32 }) } ?: error("ACCOUNT_STATISTICS_INVALID")
private fun JsonObject.nullableText(name: String): String? = if (this[name] == JsonNull) null else text(name)
