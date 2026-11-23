package app.autplay.desktop.presentation

import app.autplay.application.recommendation.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.update
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
internal data class SavedHomeTrack(val id: String, val title: String, val artist: String?)
internal data class SavedHome(val stale: Boolean, val items: List<SavedHomeTrack>, val validUntilMs: Long)
internal data class VaultPage(val items: List<VaultHit>, val next: String?)
internal data class WaveCalendar(val id: AccountUuid = AccountUuid.random(), val start: LocalDateTime = LocalDateTime.now())
internal data class ArtistCatalogue(val mbid: AccountUuid, val counts: Map<String, Long>)
internal data class MusicBrowse(val query: String, val kind: String, val entity: AccountUuid? = null, val releases: Boolean = false)
internal sealed interface PlaylistTrackTarget {
    data class Local(val id: AccountUuid) : PlaylistTrackTarget
    data class Vault(val hit: VaultHit) : PlaylistTrackTarget
    data class Catalogue(val selection: MusicCatalogueContextRequest) : PlaylistTrackTarget
}

internal data class CatalogJob(val id: AccountUuid, val phase: String, val counts: Map<String, Int>, val issues: List<Pair<String, String>> = emptyList())

internal class DesktopOnlineMusic(
    private val services: DesktopAccountServices,
    private val packs: DesktopRecommendationPacks,
    private val work: CoroutineScope,
    private val current: () -> AccountLease?,
    private val renew: suspend (AccountLease) -> AccountLease,
    private val play: suspend (AccountLease, List<AccountUuid>, String, Int) -> Unit,
    private val startWave: suspend (AccountLease, List<DesktopWaveTrack>, Int, suspend () -> List<DesktopWaveTrack>) -> Unit,
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
        val hits = rows.map { item ->
            val row = item.jsonObject
            VaultHit(AccountUuid(DomainJson.text(row, "user_track_ref_id", 36)),
                row["catalog_id"]?.jsonPrimitive?.contentOrNull?.let(::AccountUuid),
                row["title"]?.jsonPrimitive?.contentOrNull.orEmpty().take(2048),
                row["artist"]?.jsonPrimitive?.contentOrNull.orEmpty().take(2048),
                row["availability_status"]?.jsonPrimitive?.contentOrNull in setOf("VAULT", "LOCAL"))
        }
        val missing = hits.filterNot { it.available }.map { it.ref }
        val local = if (missing.isEmpty()) emptyMap() else checkNotNull(services.store.catalog).resolveMusicTargets(lease, missing, false)
        val media = checkNotNull(services.store.media)
        val playable = local.filterValues { media.readTrackCopies(lease, it, null, 1).isNotEmpty() }.keys
        requireOwner(lease)
        return VaultPage(hits.map { if (it.ref in playable) it.copy(available = true) else it },
            response["next_cursor"]?.jsonPrimitive?.contentOrNull?.also { require(it.length in 1..2048 && it != cursor) })
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
        suspend fun savedPage(): Pair<InternetMetadataDiscoveryPage, Boolean> {
            val saved = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { cachedFile(checkNotNull(file)) }
            requireOwner(lease)
            return InternetMetadataDiscoveryCodec.decode(saved.getValue("document").jsonObject) to true
        }
        if (savedOnly) return savedPage()
        val result = try { request(lease, route) } catch (failure: Exception) {
            if (!retryableMusicRequest(failure) || file == null || !Files.isRegularFile(file, NOFOLLOW_LINKS)) throw failure
            return savedPage()
        }
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

    val catalogDownloads = DesktopCatalogDownloads(work, current, ::downloadPreview, ::downloadAction,
        { lease -> requireOwner(lease); services.sync.runPass(lease) })

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
        require(phase in setOf("discovery", "inspect", "preview", "downloading", "complete"))
        val counts = value.getValue("counts").jsonObject
        val issues = (value["issues"] as? JsonArray).orEmpty().take(100).map { item ->
            val row = item.jsonObject
            DomainJson.text(row, "title", 2048) to DomainJson.text(row, "reason", 128)
        }
        return CatalogJob(id, phase, listOf("vault", "queued", "available", "missing", "failed").associateWith {
            counts[it]?.jsonPrimitive?.intOrNull?.also { n -> require(n >= 0) } ?: 0
        }, issues)
    }

    suspend fun stream(lease: AccountLease, hit: VaultHit, page: List<VaultHit>) {
        requireOwner(lease)
        val catalog = checkNotNull(services.store.catalog)
        val media = checkNotNull(services.store.media)
        val rows = page.distinctBy { it.ref }
        require(rows.size <= 50 && hit in rows)
        val refs = rows.map { it.ref }
        val initial = catalog.resolveMusicTargets(lease, refs, false)
        val local = initial.filterValues { media.readTrackCopies(lease, it, null, 1).isNotEmpty() }
        val targets = if (hit.ref in local) local else {
            val publications = rows.filter { it.available }.mapNotNull { it.publication }.distinct()
            val granted = if (publications.isEmpty()) emptySet() else {
                val result = request(lease, DomainRoute.PreparePlayback(), buildJsonObject {
                    putJsonArray("publications") { publications.forEach { add(it.value) } }
                })
                val authorized = result.getValue("refs").jsonArray
                require(authorized.size <= publications.size)
                authorized.mapTo(hashSetOf()) { AccountUuid(it.jsonPrimitive.content) }
            }
            val allowed = rows.filter { it.ref in local || it.available && (it.publication == null || it.ref in granted) }.map { it.ref }
            if (allowed.any { it !in initial }) services.sync.runPass(lease)
            catalog.resolveMusicTargets(lease, allowed, false)
        }
        val queue = refs.mapNotNull { targets[it] }.distinct()
        val selected = queue.indexOf(targets[hit.ref])
        check(selected >= 0) { "SYNC_REQUIRED" }
        requireOwner(lease)
        play(lease, queue, "SEARCH", selected)
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

    suspend fun appendPlaylistTrack(lease: AccountLease, target: PlaylistTrackTarget, playlist: AccountLocalDomainRow,
        operation: AccountUuid, entry: AccountUuid, timestamp: Long) {
        requireOwner(lease)
        check(editable(playlist) && (playlist.detail as? AccountLocalDomainDetail.Playlist)?.rawType == "MANUAL")
        val trackId = when (target) {
            is PlaylistTrackTarget.Local -> target.id
            is PlaylistTrackTarget.Vault -> {
                val hit = target.hit
                val existing = resolve(lease, listOf(hit.ref), false).singleOrNull()
                if (existing == null && hit.publication != null) {
                    val grant = request(lease, DomainRoute.PreparePlayback(), buildJsonObject {
                        putJsonArray("publications") { add(hit.publication.value) }
                    })
                    check(grant.getValue("refs").jsonArray.singleOrNull()?.jsonPrimitive?.content == hit.ref.value)
                }
                existing ?: trackByReference(lease, hit.ref)
            }
            is PlaylistTrackTarget.Catalogue -> {
                val selection = target.selection
                val receipt = MusicCatalogueContextCodec.decode(request(lease, DomainRoute.CatalogueContext(), selection.document()))
                check(receipt.matches(selection))
                val admitted = AccountUuid(checkNotNull(receipt.admissionContextId(java.time.Instant.now())))
                val ref = AccountUuid(DomainJson.text(request(lease, DomainRoute.CatalogueTrack(admitted)), "user_track_ref_id", 36))
                trackByReference(lease, ref)
            }
        }
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

    suspend fun artistCatalogue(lease: AccountLease, artist: AccountUuid, cache: Path?): ArtistCatalogue? {
        requireOwner(lease)
        val file = cache?.let { catalogDir(it, lease).resolve("ranking-${artist.value}.json") }
        val saved = withContext(Dispatchers.IO) {
            if (file == null || !Files.isRegularFile(file, NOFOLLOW_LINKS)) null
            else try { cachedFile(file) } catch (_: IllegalArgumentException) { null }
        }
        fun ranking(row: JsonObject): ArtistCatalogue? {
            val mbid = row["artist_mbid"]?.jsonPrimitive?.contentOrNull?.let(::AccountUuid) ?: return null
            return ArtistCatalogue(mbid, row["recording_counts"]?.jsonObject.orEmpty().mapValues { (_, count) ->
                count.jsonPrimitive.long.also { require(it >= 0) }
            })
        }
        val previous = saved?.get("document") as? JsonObject
        val now = System.currentTimeMillis()
        requireOwner(lease)
        if (previous != null && now - (saved["saved_ms"]?.jsonPrimitive?.longOrNull ?: 0L) in 0 until 3_600_000L)
            return ranking(previous)
        val live = try { request(lease, DomainRoute.ArtistRanking(artist)) } catch (failure: Exception) {
            if (!retryableMusicRequest(failure) || previous == null) throw failure
            requireOwner(lease)
            return ranking(previous)
        }
        val row = if (live["ranking_available"] == JsonPrimitive(false) && previous != null &&
            previous["artist_mbid"] == live["artist_mbid"]) JsonObject(live + previous.filterKeys { it == "recording_counts" }) else live
        val result = ranking(row)
        if (file != null) withContext(Dispatchers.IO) {
            try {
                Files.createDirectories(file.parent)
                check(safeFolder(file.parent))
                val temp = Files.createTempFile(file.parent, ".ranking-", ".tmp")
                try {
                    Files.writeString(temp, buildJsonObject { put("document", row); put("saved_ms", now) }.toString())
                    requireOwner(lease)
                    Files.move(temp, file, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
                } finally { Files.deleteIfExists(temp) }
            } catch (_: java.io.IOException) { /* Online metadata is still available. */ }
        }
        requireOwner(lease)
        return result
    }

    private suspend fun packScope(lease: AccountLease): PackScope {
        requireOwner(lease)
        val witness = services.session.capture(lease)
        val preparation = checkNotNull(witness.owner.accountProjection).preparation
        return PackScope(lease.storeId, lease.scopeId, RecommendationPackOwner(lease.profileId.value,
            checkNotNull(preparation.scope.remote).userId.value, checkNotNull(preparation.binding).deviceId.value))
    }

    private val refreshes = mutableMapOf<Pair<AccountLease, String>, Job>()
    val packRevision = kotlinx.coroutines.flow.MutableStateFlow(0L)
    fun schedulePack(lease: AccountLease, mode: String) {
        val key = lease to mode
        synchronized(refreshes) {
            if (refreshes[key]?.isActive == true) return
            val job = work.launch(start = CoroutineStart.LAZY) {
                val now = System.currentTimeMillis()
                val (binding, saved) = try {
                    val owner = packScope(lease)
                    owner to packs.read(owner, mode, now)
                } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { return@launch }
                if (saved != null && now - saved.createdAtMs in 0 until 86_400_000L && saved.expiresAtMs - now > 3_600_000L) return@launch
                var waitMs = 30_000L
                repeat(6) { attempt ->
                    if (current() != lease) return@launch
                    try {
                        val document = request(lease, DomainRoute.RecommendationPack(), buildJsonObject {
                            put("context", mode); put("limit", 25); put("ttl_days", 7)
                            put("exploration", 0.2); put("seed", 0); put("pipeline_key", "cpu-baseline"); put("shadow", false)
                        })
                        requireOwner(lease)
                        packs.store(binding, mode, document, System.currentTimeMillis()) { current() == lease }
                        packRevision.update { it + 1 }
                        return@launch
                    } catch (cancelled: CancellationException) { throw cancelled }
                    catch (failure: Exception) {
                        if (!retryableMusicRequest(failure) || attempt == 5) return@launch
                    }
                    delay(waitMs); waitMs *= 2
                }
            }
            refreshes[key] = job
            job.invokeOnCompletion { synchronized(refreshes) { refreshes.remove(key, job) } }
            job.start()
        }
    }

    private suspend fun protectedCachedPack(lease: AccountLease, context: String,
        expiry: OfflinePackExpiryPolicy): DecodedOfflinePack? = withContext(Dispatchers.IO) {
        val owner = packScope(lease)
        val pack = packs.read(owner, context, System.currentTimeMillis(), expiry)
        requireOwner(lease)
        pack
    }

    suspend fun cachedWave(lease: AccountLease, context: String): List<DesktopWaveTrack> {
        val pack = protectedCachedPack(lease, context, OfflinePackExpiryPolicy.FreshOnly) ?: return emptyList()
        return packTracks(lease, pack, wave = true)
    }

    private suspend fun packTracks(lease: AccountLease, pack: DecodedOfflinePack, wave: Boolean): List<DesktopWaveTrack> {
        val skipHint = if (wave) "FREQUENT_WAVE_SKIPS" else "FREQUENT_HOME_SKIPS"
        val recentHints = System.currentTimeMillis() - pack.createdAtMs in 0 until 86_400_000L
        return selectWave(lease, pack.items.map {
            DesktopWaveRecommendation(pack.recommendationRequestId, it.recordingId, it.sourceRank, it.section, pack.request.context, pack.offlinePackId,
                if (wave) "personal_wave" else "home")
        }, true, pack.items.filter { "LIKED_ORIGINAL_VERSION" in it.reasonCodes }.mapTo(hashSetOf()) { it.recordingId },
            pack.items.filter { recentHints && skipHint in it.reasonCodes }.mapTo(hashSetOf()) { it.recordingId }, wave)
    }

    suspend fun savedHome(lease: AccountLease): SavedHome? {
        val pack = protectedCachedPack(lease, "GENERAL", OfflinePackExpiryPolicy.AllowStaleLocalOnly(86_400_000L)) ?: return null
        val tracks = packTracks(lease, pack, wave = false)
        val domain = checkNotNull(services.store.domain)
        val counts = mutableMapOf<String, Int>()
        val rows = tracks.mapNotNull {
            val track = domain.readExact(lease, AccountProjectionKind.TRACK, AccountUuid(it.trackId))?.detail as? AccountLocalDomainDetail.Track
                ?: return@mapNotNull null
            val artist = track.artist.orEmpty().trim().lowercase(java.util.Locale.ROOT)
            val count = counts.getOrDefault(artist, 0)
            if (count >= 2) return@mapNotNull null
            counts[artist] = count + 1
            SavedHomeTrack(it.trackId, track.title.orEmpty(), track.artist)
        }
        requireOwner(lease)
        return SavedHome(pack.isStale, rows, pack.expiresAtMs + if (pack.isStale) 86_400_001L else 0L)
    }

    suspend fun playSavedHomeTrack(lease: AccountLease, track: String) {
        val home = savedHome(lease) ?: error("PACK_EXPIRED")
        val eligible = home.items.map { AccountUuid(it.id) }
        val index = eligible.indexOf(AccountUuid(track))
        check(index >= 0)
        if (home.stale) play(lease, eligible, "LIBRARY", index) else {
            val pack = protectedCachedPack(lease, "GENERAL", OfflinePackExpiryPolicy.FreshOnly) ?: error("PACK_EXPIRED")
            val batch = packTracks(lease, pack, wave = false).associateBy { it.trackId }
            val items = eligible.mapNotNull { batch[it.value] }
            val position = items.indexOfFirst { it.trackId == track }
            check(position >= 0)
            startWave(lease, items, position) { emptyList() }
        }
    }

    suspend fun rankedWave(lease: AccountLease, context: String, session: WaveCalendar = WaveCalendar()): List<DesktopWaveTrack> {
        return try { onlineWave(lease, context, session) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (failure: Exception) {
            if (!retryableMusicRequest(failure)) throw failure
            cachedWave(lease, context).ifEmpty { throw failure }
        }
    }

    private suspend fun onlineWave(lease: AccountLease, context: String, session: WaveCalendar): List<DesktopWaveTrack> {
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
        val confirmed = hashSetOf<String>()
        val reduced = hashSetOf<String>()
        val ranked = items.map { item ->
            val row = item.jsonObject
            require(AccountUuid(DomainJson.text(row, "recommendation_request_id", 36)) == requestId)
            val recording = AccountUuid(DomainJson.text(row, "recording_id", 36))
            val rank = DomainJson.number(row, "source_rank").also { require(it in 1..1000) }.toInt()
            val reasons = (row["reason_codes"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
            if ("LIKED_ORIGINAL_VERSION" in reasons) confirmed.add(recording.value)
            if ("FREQUENT_WAVE_SKIPS" in reasons) reduced.add(recording.value)
            val section = row["section"]?.jsonPrimitive?.contentOrNull?.also { require(it.matches(Regex("[a-z][a-z0-9_]{0,99}"))) }
            DesktopWaveRecommendation(requestId.value, recording.value, rank, section, context)
        }.sortedBy { it.sourceRank }.distinctBy { it.recordingId }
        schedulePack(lease, context)
        return selectWave(lease, ranked, false, confirmed, reduced, wave = true)
    }

    private suspend fun selectWave(lease: AccountLease, ranked: List<DesktopWaveRecommendation>, offline: Boolean,
        confirmed: Set<String>, reduced: Set<String>, wave: Boolean): List<DesktopWaveTrack> {
        val recordingIds = ranked.map { AccountUuid(it.recordingId) }
        val catalog = checkNotNull(services.store.catalog)
        var selected = catalog.resolveMusicTargets(lease, recordingIds, true)
        if (!offline && selected.isEmpty() && ranked.isNotEmpty()) {
            services.sync.runPass(lease)
            selected = catalog.resolveMusicTargets(lease, recordingIds, true)
        }
        requireOwner(lease)
        if (selected.isEmpty() && offline) return emptyList()
        check(selected.isNotEmpty()) { "NO_PLAYABLE_RECOMMENDATIONS" }
        return selectAccountWave(services.store, lease, ranked, selected, offline, confirmed, reduced, wave).also { requireOwner(lease) }
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
        startWave(lease, tracks, 0) {
            val ranked = rankedWave(lease, context, session)
            if (genre == null) ranked else wheelMix(lease, genre, ranked)
        }
    }

    suspend fun playSavedWave(lease: AccountLease, context: String) {
        val tracks = cachedWave(lease, context)
        check(tracks.isNotEmpty()) { "NO_SAVED_TRACKS" }
        startWave(lease, tracks, 0) { cachedWave(lease, context) }
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

internal fun retryableMusicRequest(error: Exception): Boolean {
    var cause: Throwable = error
    while (cause is java.util.concurrent.CompletionException || cause is java.util.concurrent.ExecutionException) {
        cause = cause.cause ?: return false
    }
    return when (cause) {
        is DesktopSyncException -> cause.failure in setOf(DesktopSyncFailure.RETRY_LATER, DesktopSyncFailure.UNAVAILABLE)
        is app.autplay.desktop.account.AccountControlException -> cause.code in setOf(
            app.autplay.desktop.account.AccountControlFailure.DEADLINE,
            app.autplay.desktop.account.AccountControlFailure.SERVER_UNAVAILABLE,
            app.autplay.desktop.account.AccountControlFailure.TRANSPORT_UNAVAILABLE)
        is java.net.SocketException, is java.net.UnknownHostException, is java.net.SocketTimeoutException -> true
        else -> false
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
