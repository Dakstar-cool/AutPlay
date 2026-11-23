package app.autplay.desktop.presentation

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.account.*
import app.autplay.application.server.InternetMetadataDiscoveryCodec
import app.autplay.application.server.InternetMetadataDiscoveryPage
import app.autplay.desktop.sync.*
import java.time.LocalDateTime
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.*

internal data class VaultHit(val ref: AccountUuid, val publication: AccountUuid?, val title: String, val artist: String)
internal data class VaultPage(val items: List<VaultHit>, val next: String?)
internal data class MusicBrowse(val query: String, val kind: String, val entity: AccountUuid? = null, val releases: Boolean = false)
internal data class CatalogJob(val id: AccountUuid, val phase: String, val counts: Map<String, Int>)

internal class DesktopOnlineMusic(
    private val services: DesktopAccountServices,
    private val current: () -> AccountLease?,
    private val renew: suspend (AccountLease) -> AccountLease,
    private val play: suspend (AccountLease, List<AccountUuid>, String) -> Unit,
) {
    private fun requireOwner(lease: AccountLease) = check(current() == lease) { "ACCOUNT_SCOPE_CHANGED" }

    private suspend fun request(lease: AccountLease, route: DomainRoute, body: JsonObject? = null): JsonObject {
        requireOwner(lease)
        check(renew(lease) == lease) { "ACCOUNT_SCOPE_CHANGED" }
        val witness = services.session.capture(lease)
        return services.session.request(lease, route, body?.toString()?.encodeToByteArray() ?: byteArrayOf()).use { reply ->
            services.transport.requireSuccess(reply, setOf(200, 201, 202))
            val result = DomainJson.document(reply.bytes, route.responseCap)
            services.session.revalidate(witness)
            requireOwner(lease)
            currentCoroutineContext().ensureActive()
            result
        }
    }

    suspend fun vault(lease: AccountLease, query: String, cursor: String?): VaultPage {
        val response = request(lease, DomainRoute.VaultSearch(query.trim(), cursor))
        val rows = response.getValue("items").jsonArray
        require(rows.size <= 50)
        return VaultPage(rows.map { item ->
            val row = item.jsonObject
            VaultHit(AccountUuid(DomainJson.text(row, "user_track_ref_id", 36)),
                row["catalog_id"]?.jsonPrimitive?.contentOrNull?.let(::AccountUuid),
                row["title"]?.jsonPrimitive?.contentOrNull.orEmpty().take(2048),
                row["artist"]?.jsonPrimitive?.contentOrNull.orEmpty().take(2048))
        }, response["next_cursor"]?.jsonPrimitive?.contentOrNull?.also { require(it.length in 1..2048) })
    }

    suspend fun discover(lease: AccountLease, target: MusicBrowse, offset: Int): InternetMetadataDiscoveryPage {
        val route = target.entity?.let { DomainRoute.Discography(target.kind == "artist", it, target.releases, offset) }
            ?: DomainRoute.Discover(target.kind, target.query.trim(), offset)
        return InternetMetadataDiscoveryCodec.decode(request(lease, route))
    }

    suspend fun downloadPreview(lease: AccountLease, operation: AccountUuid, kind: String, id: AccountUuid, alternatives: Boolean): CatalogJob {
        require(kind in setOf("artist", "release", "release_track", "recording"))
        return job(operation, request(lease, DomainRoute.DownloadPreview(), buildJsonObject {
            put("operation_id", operation.value); put("target_kind", kind); put("entity_id", id.value); put("alternatives", alternatives)
        }))
    }

    suspend fun downloadAction(lease: AccountLease, id: AccountUuid, action: String): CatalogJob =
        job(id, request(lease, DomainRoute.CatalogDownload(id, action), if (action == "status") null else JsonObject(emptyMap())))

    private fun job(id: AccountUuid, value: JsonObject): CatalogJob {
        val phase = DomainJson.text(value, "phase", 50)
        val counts = value.getValue("counts").jsonObject
        return CatalogJob(id, phase, listOf("vault", "queued", "available", "missing", "failed").associateWith {
            counts[it]?.jsonPrimitive?.intOrNull?.also { n -> require(n >= 0) } ?: 0
        })
    }

    suspend fun stream(lease: AccountLease, hit: VaultHit) {
        val expected = hit.publication?.let { publication ->
            val result = request(lease, DomainRoute.PreparePlayback(), buildJsonObject {
                putJsonArray("publications") { add(publication.value) }
            })
            val refs = result.getValue("refs").jsonArray
            check(refs.size == 1)
            AccountUuid(refs.single().jsonPrimitive.content)
        } ?: hit.ref
        var id = resolve(lease, listOf(expected), recording = false).singleOrNull()
        if (id == null) {
            services.sync.runPass(lease)
            id = resolve(lease, listOf(expected), recording = false).singleOrNull()
        }
        requireOwner(lease)
        play(lease, listOf(checkNotNull(id) { "SYNC_REQUIRED" }), "SEARCH")
    }

    suspend fun wave(lease: AccountLease, context: String) {
        require(context in setOf("GENERAL", "WORK", "SLEEP"))
        val session = AccountUuid.random()
        val now = LocalDateTime.now()
        val result = request(lease, DomainRoute.Recommendations(), buildJsonObject {
            put("context", context); put("limit", 50); put("exploration", 0.2); put("seed", session.value.hashCode())
            put("pipeline_key", "cpu-baseline"); put("pipeline_version", JsonNull); put("shadow", false)
            putJsonObject("wave_session") {
                put("version", 1); put("session_id", session.value); put("local_date", now.toLocalDate().toString()); put("local_hour", now.hour)
            }
        })
        require(DomainJson.text(result, "replay") == "served")
        val requestId = AccountUuid(DomainJson.text(result, "recommendation_request_id", 36))
        val items = result.getValue("items").jsonArray
        require(items.size <= 50)
        val ranked = items.map { item ->
            val row = item.jsonObject
            require(AccountUuid(DomainJson.text(row, "recommendation_request_id", 36)) == requestId)
            DomainJson.number(row, "source_rank") to AccountUuid(DomainJson.text(row, "recording_id", 36))
        }.sortedBy { it.first }.map { it.second }.distinct()
        var selected = resolve(lease, ranked, recording = true)
        if (selected.isEmpty() && ranked.isNotEmpty()) {
            services.sync.runPass(lease)
            selected = resolve(lease, ranked, recording = true)
        }
        requireOwner(lease)
        check(selected.isNotEmpty()) { "NO_PLAYABLE_RECOMMENDATIONS" }
        play(lease, selected, "USER")
    }

    private suspend fun resolve(lease: AccountLease, ids: List<AccountUuid>, recording: Boolean): List<AccountUuid> {
        val catalog = checkNotNull(services.store.catalog)
        val matches = catalog.resolveMusicTargets(lease, ids, recording)
        return ids.mapNotNull(matches::get)
    }

    suspend fun publish(lease: AccountLease, playlist: DesktopPlaylist, library: DesktopLibrarySnapshot): String {
        val witness = services.session.capture(lease)
        val root = request(lease, DomainRoute.SharePlaylist(), playlistShareBody(playlist, library))
        services.session.revalidate(witness)
        val token = AccountUuid(DomainJson.text(root, "token", 36))
        return witness.server.transport.api.toASCIIString() + "/api/v1/playlist-shares/" + token.value
    }
}

/** A local track has no authenticated server reference. Publish only its visible labels and duration. */
internal fun playlistShareBody(playlist: DesktopPlaylist, library: DesktopLibrarySnapshot): JsonObject {
    require(playlist.entries.size <= 10_000 && playlist.name.length in 1..120)
    val order = playlist.entries.map { it.trackId }
    val indices = order.distinct().withIndex().associate { it.value to it.index }
    val tracks = library.tracks.associateBy { it.id }
    return buildJsonObject {
        put("playlist_id", playlist.id); put("name", playlist.name); put("description", JsonPrimitive(playlist.description))
        putJsonArray("tracks") {
            indices.keys.forEach { id ->
                val track = tracks.getValue(id)
                add(buildJsonObject {
                    put("ref", JsonNull); put("title", track.title); put("artist", track.artist); put("duration_ms", JsonPrimitive(track.durationMs))
                })
            }
        }
        putJsonArray("entries") { order.forEach { add(indices.getValue(it)) } }
    }
}
