package app.autplay.desktop.presentation

import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopWaveTrack
import app.autplay.application.desktop.DesktopWaveRecommendation
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.account.*
import app.autplay.application.server.MusicCatalogueContextRequest
import app.autplay.application.server.MusicCatalogueContextCodec
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
internal data class WaveCalendar(val id: AccountUuid = AccountUuid.random(), val start: LocalDateTime = LocalDateTime.now())
internal data class ArtistCatalogue(val mbid: AccountUuid, val counts: Map<String, Long>)
internal data class MusicBrowse(val query: String, val kind: String, val entity: AccountUuid? = null, val releases: Boolean = false)
internal data class CatalogJob(val id: AccountUuid, val phase: String, val counts: Map<String, Int>)

internal class DesktopOnlineMusic(
    private val services: DesktopAccountServices,
    private val current: () -> AccountLease?,
    private val renew: suspend (AccountLease) -> AccountLease,
    private val play: suspend (AccountLease, List<AccountUuid>, String) -> Unit,
    private val startWave: suspend (AccountLease, List<DesktopWaveTrack>, suspend () -> List<DesktopWaveTrack>) -> Unit,
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
        play(lease, listOf(trackByReference(lease, expected)), "SEARCH")
    }

    private suspend fun trackByReference(lease: AccountLease, expected: AccountUuid): AccountUuid {
        val local = resolve(lease, listOf(expected), recording = false).singleOrNull() ?: run {
            services.sync.runPass(lease)
            resolve(lease, listOf(expected), recording = false).singleOrNull()
        }
        requireOwner(lease)
        return checkNotNull(local) { "SYNC_REQUIRED" }
    }

    private fun editable(row: AccountLocalDomainRow) = !row.header.hasConflict && !row.header.removed &&
        row.header.snapshotConfirmed != false && row.header.rawProjectionState.let { it == null || it == "COMPLETE" }

    suspend fun playlists(lease: AccountLease, after: AccountUuid?): Pair<List<AccountLocalDomainRow>, AccountUuid?> {
        requireOwner(lease)
        val rows = checkNotNull(services.store.domain).readProjectionPage(lease, AccountProjectionKind.PLAYLIST, after, 25)
        return rows.filter { editable(it) && (it.detail as? AccountLocalDomainDetail.Playlist)?.rawType == "MANUAL" } to
            rows.lastOrNull()?.header?.localId?.takeIf { rows.size == 25 }
    }

    suspend fun appendCatalogueTrack(lease: AccountLease, selection: MusicCatalogueContextRequest, playlist: AccountLocalDomainRow,
        operation: AccountUuid, entry: AccountUuid, timestamp: Long) {
        requireOwner(lease)
        check(editable(playlist) && (playlist.detail as? AccountLocalDomainDetail.Playlist)?.rawType == "MANUAL")
        val receipt = MusicCatalogueContextCodec.decode(request(lease, DomainRoute.CatalogueContext(), selection.document()))
        check(receipt.matches(selection))
        val admitted = AccountUuid(checkNotNull(receipt.admissionContextId(java.time.Instant.now())))
        val ref = AccountUuid(DomainJson.text(request(lease, DomainRoute.CatalogueTrack(admitted)), "user_track_ref_id", 36))
        val trackId = trackByReference(lease, ref)
        val domain = checkNotNull(services.store.domain)
        val track = checkNotNull(domain.readExact(lease, AccountProjectionKind.TRACK, trackId))
        check(editable(track))
        val change = AccountDomainChange(operation, timestamp, AccountDomainCommand.Entry(entry, playlist.header.localId,
            trackId, "0", false, serverOrdering = true))
        val review = AccountDomainReview(listOf(AccountDomainExpected(AccountProjectionKind.TRACK, trackId, track.header),
            AccountDomainExpected(AccountProjectionKind.PLAYLIST, playlist.header.localId, playlist.header),
            AccountDomainExpected(AccountProjectionKind.PLAYLIST_ENTRY, entry, null)))
        requireOwner(lease)
        val saved = domain.readMutationOutcome(lease, change, review) ?: domain.mutateReviewed(lease, change, review)
        check(saved.operationId == operation && saved.aggregateId == entry)
    }

    suspend fun credits(lease: AccountLease, id: AccountUuid): List<AccountCatalogCreditMember> {
        requireOwner(lease)
        val catalog = checkNotNull(services.store.catalog)
        val data = catalog.readTrackContext(lease, id)?.track?.detail as? AccountLocalDomainDetail.Track
        val recording = data?.recordingId ?: return emptyList()
        return catalog.readSubject(lease, AccountCatalogSubjectCursor(recording, "RECORDING_ARTIST_CREDIT"))
            ?.members.orEmpty().sortedBy { it.position }.distinctBy { it.artistId }
    }

    suspend fun artistCatalogue(lease: AccountLease, artist: AccountUuid): ArtistCatalogue? {
        val row = request(lease, DomainRoute.ArtistRanking(artist))
        val mbid = row["artist_mbid"]?.jsonPrimitive?.contentOrNull?.let(::AccountUuid) ?: return null
        val counts = row["recording_counts"]?.jsonObject.orEmpty().mapValues { (_, count) ->
            count.jsonPrimitive.long.also { require(it >= 0) }
        }
        return ArtistCatalogue(mbid, counts)
    }

    suspend fun rankedWave(lease: AccountLease, context: String, session: WaveCalendar = WaveCalendar()): List<DesktopWaveTrack> {
        require(context in setOf("GENERAL", "WORK", "SLEEP"))
        val now = session.start
        val query = buildJsonObject {
            put("context", context); put("limit", 50); put("exploration", 0.2); put("seed", AccountUuid.random().value.hashCode())
            put("pipeline_key", "cpu-baseline"); put("pipeline_version", JsonNull); put("shadow", false)
            putJsonObject("wave_session") {
                put("version", 1); put("session_id", session.id.value); put("local_date", now.toLocalDate().toString()); put("local_hour", now.hour)
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
            val recording = AccountUuid(DomainJson.text(row, "recording_id", 36))
            val rank = DomainJson.number(row, "source_rank").also { require(it in 1..1000) }.toInt()
            val section = row["section"]?.jsonPrimitive?.contentOrNull?.also { require(it.matches(Regex("[a-z][a-z0-9_]{0,99}"))) }
            DesktopWaveRecommendation(requestId.value, recording.value, rank, section, context)
        }.sortedBy { it.sourceRank }.distinctBy { it.recordingId }
        val recordingIds = ranked.map { AccountUuid(it.recordingId) }
        val catalog = checkNotNull(services.store.catalog)
        var selected = catalog.resolveMusicTargets(lease, recordingIds, true)
        if (selected.isEmpty() && ranked.isNotEmpty()) {
            services.sync.runPass(lease)
            selected = catalog.resolveMusicTargets(lease, recordingIds, true)
        }
        requireOwner(lease)
        check(selected.isNotEmpty()) { "NO_PLAYABLE_RECOMMENDATIONS" }
        val domain = checkNotNull(services.store.domain)
        return ranked.mapNotNull { recommendation ->
            val id = selected[AccountUuid(recommendation.recordingId)] ?: return@mapNotNull null
            val excluded = domain.readTrackPreferences(lease, id).any { row ->
                val pref = row.detail as? AccountLocalDomainDetail.Preference
                return@any !row.header.removed && (pref?.rawPreference == "DISLIKED" || pref?.excludedFromTaste == true)
            }
            if (excluded) null else DesktopWaveTrack(id.value, recommendation)
        }
    }

    private suspend fun trackGenres(lease: AccountLease, id: AccountUuid): Set<String> {
        val catalog = checkNotNull(services.store.catalog)
        val tags = catalog.readMetadata(lease, id)?.fields?.get("genres") as? JsonArray ?: return emptySet()
        if (tags.size > 12) return emptySet()
        return tags.mapNotNull { (it as? JsonPrimitive)?.contentOrNull?.let(app.autplay.application.recommendation.WaveWheel::genre) }.toSet()
    }

    suspend fun wheelWeights(lease: AccountLease, ordered: List<DesktopWaveTrack>): Map<String, Double> {
        requireOwner(lease)
        val preferences = checkNotNull(services.store.domain).readProjectionPage(lease, AccountProjectionKind.PREFERENCE, null, 100)
            .filter(::editable).mapNotNull { it.detail as? AccountLocalDomainDetail.Preference }
        val excluded = preferences.filter { it.excludedFromTaste || it.rawPreference == "DISLIKED" }.mapTo(hashSetOf()) { it.trackId }
        val liked = preferences.filter { it.rawPreference == "LIKED" }.mapTo(hashSetOf()) { it.trackId }
        val ranks = ordered.associate { AccountUuid(it.trackId) to requireNotNull(it.recommendation).sourceRank }
        val result = mutableMapOf<String, Double>()
        for (id in (liked + ranks.keys) - excluded) {
            val weight = (if (id in liked) 2.0 else 0.0) + (ranks[id]?.let { 1.5 / it } ?: 0.0)
            for (genre in trackGenres(lease, id)) result[genre] = (result[genre] ?: 0.0) + weight
        }
        requireOwner(lease)
        return result
    }

    suspend fun wheelMix(lease: AccountLease, genre: String, ranked: List<DesktopWaveTrack>): List<DesktopWaveTrack> =
        ranked.filter { genre in trackGenres(lease, AccountUuid(it.trackId)) }.takeIf { it.size >= 2 }.orEmpty()

    suspend fun playMix(lease: AccountLease, tracks: List<DesktopWaveTrack>, genre: String? = null,
        session: WaveCalendar = WaveCalendar()) {
        requireOwner(lease)
        val context = checkNotNull(tracks.firstOrNull()?.recommendation) { "NO_PLAYABLE_RECOMMENDATIONS" }.context
        startWave(lease, tracks) {
            val ranked = rankedWave(lease, context, session)
            if (genre == null) ranked else wheelMix(lease, genre, ranked)
        }
    }

    suspend fun downloadCurrent(lease: AccountLease, track: String) {
        requireOwner(lease)
        download(lease, AccountUuid(track))
    }

    suspend fun wave(lease: AccountLease, context: String) {
        val session = WaveCalendar()
        playMix(lease, rankedWave(lease, context, session), session = session)
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
