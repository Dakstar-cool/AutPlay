package app.autplay.desktop.presentation

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.account.*
import app.autplay.application.server.InternetMetadataDiscoveryCodec
import app.autplay.application.server.InternetMetadataDiscoveryPage
import app.autplay.desktop.sync.*
import androidx.compose.ui.graphics.toComposeImageBitmap
import kotlinx.coroutines.sync.withPermit
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.StandardCopyOption
import java.time.LocalDateTime
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.*

internal data class VaultHit(val ref: AccountUuid, val publication: AccountUuid?, val title: String, val artist: String, val available: Boolean)
internal data class VaultPage(val items: List<VaultHit>, val next: String?)
internal data class MusicBrowse(val query: String, val kind: String, val entity: AccountUuid? = null, val releases: Boolean = false)
internal data class CatalogJob(val id: AccountUuid, val phase: String, val counts: Map<String, Int>)

internal class DesktopOnlineMusic(
    private val services: DesktopAccountServices,
    private val current: () -> AccountLease?,
    private val renew: suspend (AccountLease) -> AccountLease,
    private val play: suspend (AccountLease, List<AccountUuid>, String) -> Unit,
    private val download: suspend (AccountLease, AccountUuid) -> Unit,
) {
    private fun requireOwner(lease: AccountLease) = check(current() == lease) { "ACCOUNT_SCOPE_CHANGED" }

    private class CalendarUnsupported : Exception()

    private suspend fun request(lease: AccountLease, route: DomainRoute, body: JsonObject? = null): JsonObject {
        requireOwner(lease)
        check(renew(lease) == lease) { "ACCOUNT_SCOPE_CHANGED" }
        val witness = services.session.capture(lease)
        return services.session.request(lease, route, body?.toString()?.encodeToByteArray() ?: byteArrayOf()).use { reply ->
            if (route is DomainRoute.Recommendations && reply.status == 422 && body?.containsKey("wave_session") == true)
                throw CalendarUnsupported()
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
                row["artist"]?.jsonPrimitive?.contentOrNull.orEmpty().take(2048),
                row["availability_status"]?.jsonPrimitive?.contentOrNull in setOf("VAULT", "LOCAL"))
        }, response["next_cursor"]?.jsonPrimitive?.contentOrNull?.also { require(it.length in 1..2048) })
    }

    private val coverSlots = kotlinx.coroutines.sync.Semaphore(2)
    suspend fun cover(lease: AccountLease, id: AccountUuid): androidx.compose.ui.graphics.ImageBitmap? = coverSlots.withPermit {
        requireOwner(lease)
        val witness = services.session.capture(lease)
        services.session.request(lease, DomainRoute.ReleaseCover(id), byteArrayOf()).use { reply ->
            services.transport.requireSuccess(reply, setOf(200))
            val bitmap = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                javax.imageio.stream.MemoryCacheImageInputStream(reply.bytes.inputStream()).use { input ->
                    val reader = javax.imageio.ImageIO.getImageReaders(input).asSequence().firstOrNull() ?: return@use null
                    try {
                        reader.input = input
                        if (reader.getWidth(0) !in 1..2048 || reader.getHeight(0) !in 1..2048) return@use null
                        val options = reader.defaultReadParam
                        val sample = (maxOf(reader.getWidth(0), reader.getHeight(0)) / 128).coerceAtLeast(1)
                        options.setSourceSubsampling(sample, sample, 0, 0)
                        reader.read(0, options).toComposeImageBitmap()
                    } finally { reader.dispose() }
                }
            }
            services.session.revalidate(witness)
            requireOwner(lease)
            bitmap
        }
    }

    private fun catalogDir(root: Path, lease: AccountLease) = root.resolve("catalog-pages").resolve(lease.storeId.value).resolve(lease.scopeId.value)

    private fun safeFolder(directory: Path) = Files.isDirectory(directory, NOFOLLOW_LINKS) &&
        directory.toRealPath() == directory.toAbsolutePath().normalize()

    private fun cachedFile(path: Path): JsonObject {
        check(safeFolder(path.parent) && Files.isRegularFile(path, NOFOLLOW_LINKS))
        return Files.newInputStream(path, NOFOLLOW_LINKS).use { stream ->
            val cap = 1_048_576 + 16_384
            DomainJson.document(stream.readNBytes(cap + 1), cap)
        }
    }

    suspend fun savedEditions(lease: AccountLease, root: Path?, kind: String, page: Int): List<MusicBrowse> {
        requireOwner(lease)
        if (root == null) return emptyList()
        require(kind in setOf("artist", "album") && page >= 0)
        val dir = catalogDir(root, lease)
        val rows = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            if (!safeFolder(dir)) return@withContext emptyList()
            val files = Files.list(dir).use { listing ->
                listing.filter { it.fileName.toString().startsWith("$kind-") && it.fileName.toString().endsWith("-0.json") }
                    .sorted().skip(page * 12L).limit(13).toList()
            }
            files.map { file ->
                val cached = cachedFile(file)
                MusicBrowse(DomainJson.text(cached, "label", 2048), kind, AccountUuid(DomainJson.text(cached, "id", 36)),
                    cached.getValue("releases").jsonPrimitive.boolean)
            }
        }
        requireOwner(lease)
        return rows
    }

    suspend fun discover(lease: AccountLease, target: MusicBrowse, offset: Int, cache: Path?, savedOnly: Boolean): Pair<InternetMetadataDiscoveryPage, Boolean> {
        val route = target.entity?.let { DomainRoute.Discography(target.kind == "artist", it, target.releases, offset) }
            ?: DomainRoute.Discover(target.kind, target.query.trim(), offset)
        // Only public MusicBrainz discography pages reached by an explicit selection are retained.
        val directory = cache?.takeIf { target.entity != null }?.let { catalogDir(it, lease) }
        val file = directory?.resolve("${target.kind}-${target.entity?.value}-${target.releases}-$offset.json")
        requireOwner(lease)
        if (savedOnly) {
            val saved = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { cachedFile(checkNotNull(file)) }
            requireOwner(lease)
            return InternetMetadataDiscoveryCodec.decode(saved.getValue("document").jsonObject) to true
        }
        val result = request(lease, route)
        val page = InternetMetadataDiscoveryCodec.decode(result)
        if (directory != null && file != null && page.knownActionsAllowed) kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            try {
                Files.createDirectories(directory)
                check(safeFolder(directory))
                val temp = Files.createTempFile(directory, ".page-", ".tmp")
                try {
                    val saved = buildJsonObject {
                        put("label", target.query.take(2048)); put("id", checkNotNull(target.entity).value)
                        put("releases", target.releases); put("document", result)
                    }
                    Files.write(temp, saved.toString().encodeToByteArray())
                    Files.move(temp, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
                } finally { Files.deleteIfExists(temp) }
            } catch (_: java.io.IOException) { /* Online results remain usable without a writable cache. */ }
        }
        requireOwner(lease)
        return page to false
    }

    suspend fun downloadPreview(lease: AccountLease, operation: AccountUuid, kind: String, id: AccountUuid, alternatives: Boolean): CatalogJob {
        require(kind in setOf("artist", "release", "recording"))
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

    suspend fun rankedWave(lease: AccountLease, context: String): List<AccountUuid> {
        require(context in setOf("GENERAL", "WORK", "SLEEP"))
        val session = AccountUuid.random()
        val now = LocalDateTime.now()
        val query = buildJsonObject {
            put("context", context); put("limit", 50); put("exploration", 0.2); put("seed", session.value.hashCode())
            put("pipeline_key", "cpu-baseline"); put("pipeline_version", JsonNull); put("shadow", false)
            putJsonObject("wave_session") {
                put("version", 1); put("session_id", session.value); put("local_date", now.toLocalDate().toString()); put("local_hour", now.hour)
            }
        }
        val result = try { request(lease, DomainRoute.Recommendations(), query) }
        catch (_: CalendarUnsupported) { request(lease, DomainRoute.Recommendations(), JsonObject(query - "wave_session")) }
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
        val domain = checkNotNull(services.store.domain)
        return selected.filter { id ->
            domain.readTrackPreferences(lease, id).none {
                val pref = it.detail as? AccountLocalDomainDetail.Preference
                return@none !it.header.removed && (pref?.rawPreference == "DISLIKED" || pref?.excludedFromTaste == true)
            }
        }
    }

    suspend fun wheelMix(lease: AccountLease, genre: String): List<AccountUuid> {
        val catalog = checkNotNull(services.store.catalog)
        val ids = rankedWave(lease, "GENERAL")
        return ids.filter { id ->
            val values = catalog.readMetadata(lease, id)?.fields?.get("genres") as? JsonArray
            values != null && values.size <= 12 && values.any { value ->
                (value as? JsonPrimitive)?.contentOrNull?.let(app.autplay.application.recommendation.WaveWheel::genre) == genre
            }
        }.takeIf { it.size >= 2 }.orEmpty()
    }

    suspend fun playMix(lease: AccountLease, ids: List<AccountUuid>) {
        requireOwner(lease)
        check(ids.isNotEmpty()) { "NO_PLAYABLE_RECOMMENDATIONS" }
        play(lease, ids, "USER")
    }

    suspend fun downloadCurrent(lease: AccountLease, track: String) {
        requireOwner(lease)
        download(lease, AccountUuid(track))
    }

    suspend fun wave(lease: AccountLease, context: String) {
        playMix(lease, rankedWave(lease, context))
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
