package app.autplay.application.library

import app.autplay.application.artist.trackNames
import app.autplay.data.local.AutPlayDatabase
import app.autplay.application.playback.OrdinaryQueueReplacementGuard
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.ReleaseTrackProjectionEntity
import app.autplay.domain.ServerId
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.mapLatest
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * Bounded, presentation-safe detail projections for M4.  This repository deliberately exposes
 * no content URI, Vault location, artwork URL, or inferred artist identifier.  Mutating actions
 * remain owned by the existing playback, library, download, and import-review application
 * commands; these capabilities only tell the UI which of those commands may be offered.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CoreProductRepository(
    private val database: AutPlayDatabase,
) {
    private val metadataAlbums = AlbumGroupRepository(database)
    fun libraryEntries(profileId: String?, limit: Int = Int.MAX_VALUE): Flow<List<CoreLibraryEntrySummary>> {
        require(limit > 0)
        return database.libraryDao().browseLibrary(effectiveProfile(profileId), if (limit == Int.MAX_VALUE) -1 else limit)
            .mapLatest { rows -> withContext(Dispatchers.Default) { rows.map { row ->
                val names = trackNames(row.nameInput, row.artistName, row.title, row.hasTitle, row.hasArtist, row.cleanSourceTitle, row.cleanSourceArtist)
                row.copy(title = names.title, artistName = names.artist)
            } } }
    }

    fun recentlyAdded(profileId: String?, limit: Int = 12): Flow<List<CoreHomeTrackSummary>> {
        require(limit in 1..50)
        return combine(database.libraryDao().activeEntriesForProfile(effectiveProfile(profileId), limit), database.trackMetadataDao().changes(effectiveProfile(profileId))) { entries, _ -> entries }.mapLatest { entries ->
            val tracks = resolveTrackRefs(entries.map { it.localUserTrackRefId }, effectiveProfile(profileId))
            entries.mapNotNull { entry ->
                tracks[entry.localUserTrackRefId]?.let { track ->
                    CoreHomeTrackSummary(
                        stableId = track.localUserTrackRefId,
                        title = track.rawTitle,
                        artistName = track.rawArtist,
                        occurredAtMs = entry.addedAtMs,
                    )
                }
            }
        }
    }

    fun recentlyPlayed(profileId: String?, limit: Int = 12): Flow<List<CoreHomeTrackSummary>> {
        require(limit in 1..50)
        return combine(database.historyDao().recentForProfile(effectiveProfile(profileId), limit), database.trackMetadataDao().changes(effectiveProfile(profileId))) { events, _ -> events }.mapLatest { events ->
            val tracks = resolveTrackRefs(events.map { it.localUserTrackRefId }, effectiveProfile(profileId))
            events.mapNotNull { event ->
                tracks[event.localUserTrackRefId]?.let { track ->
                    CoreHomeTrackSummary(
                        stableId = track.localUserTrackRefId,
                        title = track.rawTitle,
                        artistName = track.rawArtist,
                        occurredAtMs = event.startedAtMs,
                    )
                }
            }.distinctBy(CoreHomeTrackSummary::stableId)
        }
    }

    fun playlists(profileId: String?, limit: Int = 200): Flow<List<CoreHomePlaylistSummary>> {
        require(limit in 1..500)
        return database.playlistDao().libraryPlaylists(effectiveProfile(profileId), limit).map { rows ->
            rows.map { (playlist, artworkTrackId) -> CoreHomePlaylistSummary(playlist.localPlaylistId, playlist.name, playlist.description, artworkTrackId) }
        }
    }

    fun historyCount(profileId: String?, limit: Int = 2_000): Flow<Int> {
        require(limit in 1..5_000)
        return database.historyDao().recentForProfile(effectiveProfile(profileId), limit).map { it.size }
    }

    /** A new personal queue cannot take authority from a group or an unknown queue type. */
    suspend fun ordinaryPlaybackReplacementGuard(profileId: String?): OrdinaryQueueReplacementGuard? {
        val snapshot = database.queueDao().activeSnapshotOnce()
        if (snapshot != null && (snapshot.serverProfileId != profileId ||
            snapshot.queueType !in setOf("USER", "LIBRARY", "SEARCH", "PLAYLIST"))) return null
        return OrdinaryQueueReplacementGuard(snapshot?.queueSnapshotId, snapshot?.queueType, profileId)
    }

    /** Sample the entire owner's library, independently of the bounded, sorted UI projection. */
    suspend fun randomPlaybackCandidates(profileId: String?, vaultAvailable: Boolean, limit: Int = 200): List<String> {
        require(limit in 1..200)
        return database.libraryDao().playbackCandidates(effectiveProfile(profileId), vaultAvailable, limit)
    }

    fun activeQueue(profileId: String?): Flow<CoreResumeQueueSummary?> =
        combine(database.queueDao().activeSnapshot(), database.trackMetadataDao().changes(effectiveProfile(profileId))) { snapshot, _ -> snapshot }.mapLatest { snapshot ->
            if (snapshot == null || snapshot.serverProfileId != profileId) return@mapLatest null
            val entry = snapshot.currentEntryId?.let { database.queueDao().entry(it) }
                ?: database.queueDao().entries(snapshot.queueSnapshotId, 1).firstOrNull()
                ?: return@mapLatest null
            val track = resolveTrackRefs(listOf(entry.localUserTrackRefId), effectiveProfile(profileId))[entry.localUserTrackRefId] ?: return@mapLatest null
            if (track.serverProfileId != effectiveProfile(profileId)) return@mapLatest null
            CoreResumeQueueSummary(
                queueSnapshotId = snapshot.queueSnapshotId,
                localUserTrackRefId = track.localUserTrackRefId,
                title = track.rawTitle,
                artistName = track.rawArtist,
                positionMs = snapshot.currentPositionMs,
                queueType = snapshot.queueType,
            )
        }

    fun releases(profileId: String?, limit: Int = 200): Flow<List<CoreReleaseSummary>> {
        require(limit in 1..500)
        return releaseSummaries(profileId, limit)
    }

    internal fun releaseSummaries(profileId: String?, limit: Int): Flow<List<CoreReleaseSummary>> =
        combine(database.catalogProjectionDao().releasesForProfile(effectiveProfile(profileId), limit),
            metadataAlbums.observe(profileId), database.catalogProjectionDao().libraryEditionTracks(effectiveProfile(profileId)),
            database.catalogProjectionDao().taggedAlbumTracks(effectiveProfile(profileId), emptyList(), true, true)) { canonical, groups, tracks, tagged ->
            val editions = tracks.groupBy { it.releaseId }
            val matched = editions.values.mapNotNull { it.first().releaseMbid }.groupingBy { it }.eachCount().filterValues { it == 1 }.keys
            val legacyIds = canonical.associate { it.release.serverReleaseId to it.release.localReleaseId }
            val coverTracks = editionCoverTracks(effectiveProfile(profileId), editions.keys)
            val summaries = editions.map { (id, rows) ->
                val edition = rows.first()
                CoreReleaseSummary(legacyIds[id] ?: editionTargetId(id), edition.album, edition.albumArtist,
                    edition.releaseDate, rows.size, coverTracks[id], mbid = edition.releaseMbid)
            }
            (groups.filterNot { it.group.provider == "MUSICBRAINZ" && it.group.releaseId in matched }
                .map { album -> CoreReleaseSummary(album.group.stableId, album.group.title, album.group.albumArtist,
                    album.group.releaseDate, album.members.size, album.artworkTrackId,
                    mbid = album.group.releaseId.takeIf { album.group.provider == "MUSICBRAINZ" }) } +
                summaries + canonical.filterNot { it.release.serverReleaseId in editions }.map { (release, artworkTrackId) ->
                    CoreReleaseSummary(release.localReleaseId, release.title, release.displayArtist, release.releaseDateText, artworkTrackId = artworkTrackId)
                } + tagged.summaries()).sortedWith(compareByDescending<CoreReleaseSummary> { it.releaseDateText }
                .thenBy(String.CASE_INSENSITIVE_ORDER) { it.title }.thenBy { it.stableId }).take(limit)
        }

    suspend fun artistReleases(profile: String, trackIds: Set<String>): List<CoreReleaseSummary> {
        if (trackIds.isEmpty()) return emptyList()
        val catalog = database.catalogProjectionDao()
        val pages = trackIds.chunked(MAX_IN_QUERY_BINDINGS)
        val editions = pages.flatMap { catalog.editionsForTracks(profile, it) }.distinctBy { it.releaseId }
        val keys = editions.mapNotNull { it.releaseMbid }.toSet()
        val projector = MetadataAlbumProjector()
        for (page in pages) database.trackMetadataDao().artistAlbumSources(profile, page).forEach(projector::add)
        val metadata = projector.albums().filterNot { album ->
            album.group.provider == "MUSICBRAINZ" && album.group.releaseId in keys
        }
        val coverTracks = editionCoverTracks(profile, editions.map { it.releaseId })
        return editions.map { row -> CoreReleaseSummary(editionTargetId(row.releaseId), row.album, row.albumArtist,
            row.releaseDate, artworkTrackId = coverTracks[row.releaseId], mbid = row.releaseMbid, releaseGroup = row.releaseGroupId) } +
            metadata.map { album -> CoreReleaseSummary(album.group.stableId, album.group.title, album.group.albumArtist,
                album.group.releaseDate, artworkTrackId = album.artworkTrackId,
                mbid = album.group.releaseId.takeIf { album.group.provider == "MUSICBRAINZ" }) } +
            pages.flatMap { catalog.taggedAlbumTracks(profile, it, false, false).first() }.summaries()
    }

    fun artistChanges(): Flow<Unit> = database.invalidationTracker.createFlow(
        "track_metadata_projection", "user_track_ref", "library_entry", "user_track_preference", "playlist", "playlist_entry",
        "release_projection", "release_track_projection", "recording_projection", "catalog_edition_track", "artist_projection",
        "artist_credit_projection", "artist_credit_name_projection", "catalog_artist_credit_link", "catalog_artist_credit_link_owner",
        "listening_event", emitInitialState = false,
    ).map { }

    fun releaseChanges(): Flow<Unit> = database.invalidationTracker.createFlow(
        "track_metadata_projection", "user_track_ref", "library_entry", "user_track_preference", "playlist", "playlist_entry",
        "release_projection", "release_track_projection", "recording_projection", "catalog_edition_track",
    ).map { }

    fun trackPreference(profileId: String?, trackRefId: String): Flow<String?> =
        database.libraryDao().observePreference(trackRefId).map { row ->
            row?.takeIf { it.serverProfileId == effectiveProfile(profileId) }?.preference
        }.distinctUntilChanged()

    fun localAudio(profileId: String?, limit: Int = 2_000): Flow<List<CoreLocalAudioSummary>> {
        require(limit in 1..5_000)
        return database.localAudioDao().statesForProfile(effectiveProfile(profileId), limit).map { rows ->
            rows.map { CoreLocalAudioSummary(it.localUserTrackRefId, it.status, it.persistedUriPermission,
                requiresPersistedUriPermission = !it.contentUri.startsWith("content://media/")) }
        }
    }

    fun downloadedTrackIds(profileId: String?, limit: Int = 2_000): Flow<Set<String>> {
        require(limit in 1..5_000)
        val rows = profileId?.let { database.localAudioDao().observeDownloadIntentsForProfile(it, limit) }
            ?: database.localAudioDao().observeStandaloneDownloadIntents(limit)
        return rows.map { values ->
            values.asSequence().filter { it.state == "COMPLETED" }.map { it.localUserTrackRefId }.toSet()
        }
    }

    suspend fun availableAudioForTrack(localUserTrackRefId: String, profileId: String?): CoreAvailableAudio? {
        val track = database.libraryDao().trackRef(localUserTrackRefId) ?: return null
        if (track.serverProfileId != effectiveProfile(profileId)) return null
        val recordingId = track.serverRecordingId ?: return null
        val audio = database.localAudioDao().statesForPlayback(localUserTrackRefId, MAX_AUDIO_STATES)
            .firstOrNull { it.status == "AVAILABLE" }
            ?: return null
        return CoreAvailableAudio(
            localUserTrackRefId = localUserTrackRefId,
            serverRecordingId = recordingId,
            localAudioStateId = audio.localAudioStateId,
            localSha256 = audio.localSha256,
            byteSize = audio.byteSize,
        )
    }

    suspend fun serverRecordingId(localUserTrackRefId: String, profileId: String?): String? {
        val track = database.libraryDao().trackRef(localUserTrackRefId) ?: return null
        if (track.serverProfileId != effectiveProfile(profileId) || track.deletedAtMs != null) return null
        return track.serverRecordingId
    }

    fun trackMetadataChanges(trackId: String, profileId: String?): Flow<String?> = database.trackMetadataDao().observe(effectiveProfile(profileId), trackId).map { it?.payloadJson }.distinctUntilChanged()

    suspend fun libraryEntryForTrack(trackId: String, profileId: String?) =
        database.libraryDao().entryForTrack(trackId)?.takeIf { it.serverProfileId == effectiveProfile(profileId) }

    suspend fun trackDetail(localUserTrackRefId: String, profileId: String?): CoreTrackDetail? {
        val track = database.libraryDao().trackRef(localUserTrackRefId) ?: return null
        if (track.serverProfileId != effectiveProfile(profileId)) return null
        val recording = track.localRecordingId?.let { localRecordingId ->
            database.catalogProjectionDao().recording(localRecordingId)
        }
        val libraryEntry = database.libraryDao().entryForTrack(localUserTrackRefId)
        val preference = database.libraryDao().preference(localUserTrackRefId)
        val audioStates = database.localAudioDao().statesForPlayback(localUserTrackRefId, MAX_AUDIO_STATES)
        val downloads = database.localAudioDao().downloadIntentsForTrack(localUserTrackRefId, MAX_DOWNLOAD_INTENTS)
        val audioCapabilityStates = audioStates.map { CoreAudioCapabilityState(it.status, it.persistedUriPermission,
            requiresPersistedUriPermission = !it.contentUri.startsWith("content://media/")) }
        val hasServerPlaybackCandidate = profileId != null &&
            track.serverUserTrackRefId != null &&
            track.serverRecordingId != null &&
            track.resolutionStatus == "RESOLVED" &&
            track.deletedAtMs == null
        val metadata = database.trackMetadataDao().get(track.serverProfileId, localUserTrackRefId)?.decoded()
        val names = metadata.names(track.rawTitle ?: recording?.title, track.rawArtist ?: recording?.displayArtist)
        return CoreTrackDetail(
            localUserTrackRefId = track.localUserTrackRefId,
            localRecordingId = track.localRecordingId,
            serverRecordingId = track.serverRecordingId?.let(::ServerId),
            title = names.title,
            artistName = names.artist,
            albumName = if (metadata == null) track.rawAlbum else metadata.text("album", track.rawAlbum),
            metadata = metadata,
            durationMs = recording?.durationMs ?: track.rawDurationMs,
            artworkRef = recording?.artworkRef,
            preference = CoreTrackPreferenceState(
                preference = preference?.preference ?: "NEUTRAL",
                excludedFromTaste = preference?.excludedFromTaste ?: false,
            ),
            availability = CoreProductDetailPolicy.availability(
                audioCapabilityStates,
                hasServerPlaybackCandidate,
            ),
            capabilities = CoreProductDetailPolicy.capabilities(
                CoreTrackCapabilityInput(
                    libraryMembership = libraryEntry?.let { CoreLibraryMembership(it.removedAtMs != null) },
                    audioStates = audioCapabilityStates,
                    hasDownloadableVariant = profileId != null && downloads.any {
                        it.serverProfileId == profileId && it.serverAudioVariantId != null
                    },
                    hasServerPlaybackCandidate = hasServerPlaybackCandidate,
                    resolutionStatus = track.resolutionStatus,
                ),
            ),
            technicalDetails = CoreTechnicalDetails(
                resolutionStatus = track.resolutionStatus,
                resolutionConfidence = track.resolutionConfidence,
                recordingKind = recording?.recordingKind,
                versionText = recording?.versionText,
            ),
        )
    }

    suspend fun releaseDetail(localReleaseId: String, profileId: String?,
        weeklyRelease: app.autplay.data.local.dao.RecentRelevantReleaseRow? = null): CoreReleaseDetail? {
        if (localReleaseId.startsWith(ARTIST_ALBUM_PREFIX)) {
            val ids = localReleaseId.removePrefix(ARTIST_ALBUM_PREFIX).split('|').distinct()
            if (ids.any { it.startsWith(ARTIST_ALBUM_PREFIX) || it.isBlank() }) return null
            val editions = ids.mapNotNull { releaseDetail(it, profileId) }
            val merged = combineArtistEditions(localReleaseId, editions) ?: return null
            return merged.copy(artworkTrackId = orderedCoverTrack(effectiveProfile(profileId), merged.tracks.mapNotNull { it.localUserTrackRefId }))
        }
        val profile = effectiveProfile(profileId)
        val catalog = database.catalogProjectionDao()
        if (localReleaseId.startsWith("tag-album:")) {
            val rows = catalog.taggedAlbumTracks(profile, emptyList(), true, false).first().filter { it.albumKey() == localReleaseId }
            val first = rows.firstOrNull() ?: return null
            return CoreReleaseDetail(localReleaseId, null, first.album, first.albumArtist, rows.firstNotNullOfOrNull { it.date },
                null, null, rows.map { CoreReleaseDetailTrack("tag-member:${it.trackId}", it.recording, it.disc, it.position,
                    it.position?.toString(), it.title.orEmpty(), it.artist.orEmpty(), it.duration, it.trackId) }, rows.coverTrackId())
        }
        val editionId = when {
            localReleaseId.startsWith(EDITION_PREFIX) -> localReleaseId.removePrefix(EDITION_PREFIX)
            localReleaseId.startsWith(METADATA_ALBUM_PREFIX + "musicbrainz:release:") -> {
                val mbid = localReleaseId.removePrefix(METADATA_ALBUM_PREFIX + "musicbrainz:release:")
                catalog.libraryEditionTracks(profile).first().filter { it.releaseMbid == mbid }
                    .map { it.releaseId }.distinct().singleOrNull()
            }
            else -> catalog.release(localReleaseId)?.serverReleaseId
        }
        if (editionId != null) {
            val rows = catalog.editionTracks(profile, listOf(editionId))
            if (rows.isNotEmpty()) {
                val refs = rows.map { it.recordingId }.distinct().chunked(MAX_IN_QUERY_BINDINGS).flatMap {
                    database.libraryDao().trackRefsByServerRecordings(profile, it, it.size)
                }.associateBy { it.serverRecordingId }
                val header = rows.first()
                val target = catalog.releasesByServerIdsForProfile(profile, listOf(editionId), 1).firstOrNull()?.localReleaseId
                    ?: editionTargetId(editionId)
                return CoreReleaseDetail(target, ServerId(editionId), header.album, header.albumArtist, header.releaseDate,
                    header.releaseType, null, rows.map { row ->
                        val ref = refs[row.recordingId]
                        CoreReleaseDetailTrack(row.trackId, ref?.localRecordingId, row.disc, row.position, row.numberText,
                            row.title, row.artist, row.durationMs, ref?.localUserTrackRefId, serverRecordingId = row.recordingId)
                    }, orderedCoverTrack(profile, rows.mapNotNull { refs[it.recordingId]?.localUserTrackRefId }), header.releaseMbid)
            }
        }
        if (localReleaseId.startsWith(METADATA_ALBUM_PREFIX)) {
            val album = metadataAlbums.snapshot(profileId, personal = false).firstOrNull { it.group.stableId == localReleaseId } ?: return null
            return CoreReleaseDetail(localReleaseId, null, album.group.title, album.group.albumArtist,
                album.group.releaseDate, null, null, album.members.map { member ->
                    CoreReleaseDetailTrack(
                        localReleaseTrackId = "metadata-member:${member.localUserTrackRefId}",
                        localRecordingId = member.localRecordingId,
                        mediumPosition = member.discNumber,
                        sequenceNo = member.trackNumber,
                        numberText = member.trackNumber?.toString(),
                        title = member.title.orEmpty(), artistName = member.artistName.orEmpty(),
                        durationMs = member.durationMs, localUserTrackRefId = member.localUserTrackRefId,
                    )
                }, album.artworkTrackId,
                album.group.releaseId.takeIf { album.group.provider == "MUSICBRAINZ" })
        }
        val release = catalog.releaseForProfile(localReleaseId, profile)
            ?: weeklyRelease?.takeIf { it.profileId == profileId && it.localReleaseId == localReleaseId }?.let { catalog.release(localReleaseId) }
            ?: return null
        val releaseTracks = database.catalogProjectionDao().releaseTracks(localReleaseId, Int.MAX_VALUE)
        val recordingIds = releaseTracks.map { it.localRecordingId }.distinct()
        val recordings = recordingIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { ids ->
            catalog.recordings(ids, ids.size)
        }.associateBy { it.localRecordingId }
        val trackRefsByRecording = recordingIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { ids ->
            database.libraryDao().trackRefsByRecordings(effectiveProfile(profileId), ids, ids.size)
        }.associateBy { it.localRecordingId }
        return CoreReleaseDetail(
            localReleaseId = release.localReleaseId,
            serverReleaseId = release.serverReleaseId?.let(::ServerId),
            title = release.title,
            artistName = release.displayArtist,
            releaseDateText = release.releaseDateText,
            releaseType = release.releaseType,
            artworkRef = release.artworkRef,
            artworkTrackId = orderedCoverTrack(profile, releaseTracks.mapNotNull { trackRefsByRecording[it.localRecordingId]?.localUserTrackRefId }),
            tracks = releaseTracks.map {
                it.toDetailRow(recordings[it.localRecordingId], trackRefsByRecording[it.localRecordingId]?.localUserTrackRefId)
            },
        )
    }

    private suspend fun orderedCoverTrack(profile: String, tracks: List<String>): String? {
        for (page in tracks.chunked(MAX_IN_QUERY_BINDINGS)) {
            val available = database.trackMetadataDao().tracksWithArtwork(profile, page).toSet()
            page.firstOrNull { it in available }?.let { return it }
        }
        return tracks.firstOrNull()
    }

    private suspend fun editionCoverTracks(profile: String, releases: Collection<String>): Map<String, String?> =
        releases.chunked(MAX_IN_QUERY_BINDINGS).flatMap { database.catalogProjectionDao().coverTracksForEditions(profile, it) }
            .associate { it.releaseId to it.trackRef }

    suspend fun playlistDetail(localPlaylistId: String, profileId: String?, limit: Int = Int.MAX_VALUE): CorePlaylistDetail? {
        val playlist = database.playlistDao().playlist(localPlaylistId) ?: return null
        if (playlist.deletedAtMs != null || playlist.serverProfileId != effectiveProfile(profileId)) return null
        val entries = database.playlistDao().activeEntryList(localPlaylistId, limit)
        val trackRefIds = entries.map { it.localUserTrackRefId }.distinct()
        val trackRefs = trackRefIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { ids ->
            database.libraryDao().trackRefs(localIds = ids, limit = ids.size)
        }.filter { it.serverProfileId == effectiveProfile(profileId) }.associateBy { it.localUserTrackRefId }
        val recordingIds = trackRefs.values.mapNotNull { it.localRecordingId }.distinct()
        val recordings = recordingIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { ids ->
            database.catalogProjectionDao().recordings(localIds = ids, limit = ids.size)
        }.associateBy { it.localRecordingId }
        val metadata = trackRefIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { database.trackMetadataDao().forTracks(effectiveProfile(profileId), it) }.associateBy { it.localUserTrackRefId }
        return CorePlaylistDetail(
            localPlaylistId = playlist.localPlaylistId,
            name = playlist.name,
            description = playlist.description,
            playlistType = playlist.playlistType,
            artworkTrackId = trackRefIds.firstOrNull { metadata[it]?.artworkSha256 != null },
            entries = entries.map { entry ->
                val track = trackRefs[entry.localUserTrackRefId]
                val recording = track?.localRecordingId?.let(recordings::get)
                val description = metadata[entry.localUserTrackRefId]?.decoded()
                val names = description.names(track?.rawTitle ?: recording?.title, track?.rawArtist ?: recording?.displayArtist)
                CorePlaylistDetailEntry(
                    // This is the UI identity. Do not substitute the repeated track-ref ID.
                    localPlaylistEntryId = entry.localPlaylistEntryId,
                    localUserTrackRefId = entry.localUserTrackRefId,
                    title = names.title,
                    artistName = names.artist,
                    durationMs = recording?.durationMs ?: track?.rawDurationMs,
                    unavailable = track == null || track.deletedAtMs != null || track.resolutionStatus == "CATALOG_ONLY",
                )
            },
        )
    }

    private suspend fun resolveTrackRefs(ids: List<String>, profileId: String): Map<String, app.autplay.data.local.entity.UserTrackRefEntity> = withContext(Dispatchers.Default) {
        val chunks = ids.distinct().chunked(MAX_IN_QUERY_BINDINGS)
        val metadata = chunks.flatMap { database.trackMetadataDao().forTracks(profileId, it) }.associateBy { it.localUserTrackRefId }
        chunks.flatMap { database.libraryDao().trackRefs(it, it.size) }
            .filter { it.serverProfileId == profileId && it.deletedAtMs == null }.map { track ->
                val values = metadata[track.localUserTrackRefId]?.decoded()
                val names = values.names(track.rawTitle, track.rawArtist)
                track.copy(rawTitle = names.title, rawArtist = names.artist,
                    rawAlbum = if (values == null) track.rawAlbum else values.text("album", track.rawAlbum))
            }.associateBy { it.localUserTrackRefId }
    }

    private fun ReleaseTrackProjectionEntity.toDetailRow(
        recording: RecordingProjectionEntity?,
        localUserTrackRefId: String?,
    ) =
        CoreReleaseDetailTrack(
            localReleaseTrackId = localReleaseTrackId,
            localRecordingId = localRecordingId,
            mediumPosition = mediumPosition,
            sequenceNo = sequenceNo,
            numberText = numberText,
            title = creditedTitle.ifBlank { recording?.title.orEmpty() },
            artistName = creditedArtist.ifBlank { recording?.displayArtist.orEmpty() },
            durationMs = durationMs ?: recording?.durationMs,
            localUserTrackRefId = localUserTrackRefId,
            serverRecordingId = recording?.serverRecordingId,
        )

    private fun effectiveProfile(profileId: String?): String = profileId ?: LEGACY_PROFILE_ID

    private companion object {
        const val MAX_AUDIO_STATES = 8
        const val MAX_DOWNLOAD_INTENTS = 32
        const val MAX_IN_QUERY_BINDINGS = 900
        const val LEGACY_PROFILE_ID = "legacy-unscoped"
    }
}

data class CoreReleaseSummary(val stableId: String, val title: String, val artistName: String?,
    val releaseDateText: String? = null, val ownedTrackCount: Int? = null,
    val artworkTrackId: String? = null, val mbid: String? = null, val releaseGroup: String? = null)
typealias CoreLibraryEntrySummary = app.autplay.data.local.dao.LibraryBrowseRow
data class CoreHomeTrackSummary(
    val stableId: String,
    val title: String?,
    val artistName: String?,
    val occurredAtMs: Long,
)
data class CoreHomePlaylistSummary(val stableId: String, val title: String, val description: String?, val artworkTrackId: String? = null)
data class CoreResumeQueueSummary(
    val queueSnapshotId: String,
    val localUserTrackRefId: String,
    val title: String?,
    val artistName: String?,
    val positionMs: Long,
    val queueType: String,
)
data class CoreLocalAudioSummary(
    val stableId: String,
    val status: String,
    val persistedUriPermission: Boolean,
    val requiresPersistedUriPermission: Boolean = true,
) {
    val hasReadAccess: Boolean get() = persistedUriPermission || !requiresPersistedUriPermission
}
data class CoreAvailableAudio(
    val localUserTrackRefId: String,
    val serverRecordingId: String,
    val localAudioStateId: String,
    val localSha256: ByteArray?,
    val byteSize: Long?,
)

data class CoreTrackDetail(
    val localUserTrackRefId: String,
    val localRecordingId: String?,
    val serverRecordingId: ServerId?,
    val title: String?,
    val artistName: String?,
    val albumName: String?,
    val durationMs: Long?,
    val artworkRef: String?,
    val preference: CoreTrackPreferenceState,
    val availability: CoreTrackAvailability,
    val capabilities: Set<CoreTrackDetailCapability>,
    val technicalDetails: CoreTechnicalDetails,
    val metadata: TrackMetadata? = null,
)

data class CoreTrackPreferenceState(
    val preference: String,
    val excludedFromTaste: Boolean,
)

data class CoreReleaseDetail(
    val localReleaseId: String,
    val serverReleaseId: ServerId?,
    val title: String,
    val artistName: String?,
    val releaseDateText: String?,
    val releaseType: String?,
    val artworkRef: String?,
    val tracks: List<CoreReleaseDetailTrack>,
    val artworkTrackId: String? = null,
    val releaseMbid: String? = null,
)

data class CoreReleaseDetailTrack(
    val localReleaseTrackId: String,
    val localRecordingId: String?,
    val mediumPosition: Int?,
    val sequenceNo: Int?,
    val numberText: String?,
    val title: String,
    val artistName: String,
    val durationMs: Long?,
    val localUserTrackRefId: String?,
    val serverRecordingId: String? = null,
)

data class CorePlaylistDetail(
    val localPlaylistId: String,
    val name: String,
    val description: String?,
    val playlistType: String,
    val entries: List<CorePlaylistDetailEntry>,
    val artworkTrackId: String? = null,
)

data class CorePlaylistDetailEntry(
    val localPlaylistEntryId: String,
    val localUserTrackRefId: String,
    val title: String?,
    val artistName: String?,
    val durationMs: Long?,
    val unavailable: Boolean,
)

data class CoreTechnicalDetails(
    val resolutionStatus: String,
    val resolutionConfidence: Double?,
    val recordingKind: String?,
    val versionText: String?,
)

enum class CoreTrackAvailability { PLAYABLE_LOCAL, SERVER_CANDIDATE, PERMISSION_REVOKED, UNAVAILABLE, NO_LOCAL_SOURCE }

enum class CoreTrackDetailCapability {
    PLAY,
    REMOVE_FROM_LIBRARY,
    RESTORE_TO_LIBRARY,
    LIKE,
    DOWNLOAD,
    REAUTHORIZE_LIBRARY_ROOT,
    OPEN_IDENTITY_REVIEW,
}

data class CoreTrackCapabilityInput(
    val libraryMembership: CoreLibraryMembership?,
    val audioStates: List<CoreAudioCapabilityState>,
    val hasDownloadableVariant: Boolean,
    val hasServerPlaybackCandidate: Boolean,
    val resolutionStatus: String,
)

data class CoreLibraryMembership(val isRemoved: Boolean)

data class CoreAudioCapabilityState(
    val status: String,
    val persistedUriPermission: Boolean,
    // MediaStore access is verified on open; it does not use persistable SAF grants.
    val requiresPersistedUriPermission: Boolean = true,
) {
    val hasReadAccess: Boolean get() = persistedUriPermission || !requiresPersistedUriPermission
}

/** Pure policy seam: test it without Room or Android runtime. */
object CoreProductDetailPolicy {
    fun availability(
        audioStates: List<CoreAudioCapabilityState>,
        hasServerPlaybackCandidate: Boolean = false,
    ): CoreTrackAvailability = when {
        audioStates.any { it.status == "AVAILABLE" && it.hasReadAccess } -> CoreTrackAvailability.PLAYABLE_LOCAL
        hasServerPlaybackCandidate -> CoreTrackAvailability.SERVER_CANDIDATE
        audioStates.any { it.status == "PERMISSION_REVOKED" || !it.hasReadAccess } -> CoreTrackAvailability.PERMISSION_REVOKED
        audioStates.isNotEmpty() -> CoreTrackAvailability.UNAVAILABLE
        else -> CoreTrackAvailability.NO_LOCAL_SOURCE
    }

    fun capabilities(input: CoreTrackCapabilityInput): Set<CoreTrackDetailCapability> = buildSet {
        if (
            availability(input.audioStates, input.hasServerPlaybackCandidate) in
            setOf(CoreTrackAvailability.PLAYABLE_LOCAL, CoreTrackAvailability.SERVER_CANDIDATE)
        ) {
            add(CoreTrackDetailCapability.PLAY)
        }
        when {
            input.libraryMembership?.isRemoved == false -> add(CoreTrackDetailCapability.REMOVE_FROM_LIBRARY)
            input.libraryMembership?.isRemoved == true -> add(CoreTrackDetailCapability.RESTORE_TO_LIBRARY)
        }
        // The existing LibraryVerticalSliceRepository owns the preference mutation.
        add(CoreTrackDetailCapability.LIKE)
        // A server audio variant is an existing DownloadIntentRepository input; no URL is exposed.
        if (input.hasDownloadableVariant) add(CoreTrackDetailCapability.DOWNLOAD)
        if (availability(input.audioStates, input.hasServerPlaybackCandidate) == CoreTrackAvailability.PERMISSION_REVOKED) {
            add(CoreTrackDetailCapability.REAUTHORIZE_LIBRARY_ROOT)
        }
        if (input.resolutionStatus in REVIEWABLE_RESOLUTION_STATUSES) add(CoreTrackDetailCapability.OPEN_IDENTITY_REVIEW)
    }

    private val REVIEWABLE_RESOLUTION_STATUSES = setOf(
        "PENDING",
        "REVIEW_REQUIRED",
        "MANUAL_UNRESOLVED",
        "INTEGRITY_CONFLICT",
        "DEFERRED_EVIDENCE",
        "UNRESOLVED",
    )
}

internal const val ARTIST_ALBUM_PREFIX = "artist-editions:"

internal fun combineArtistEditions(id: String, editions: List<CoreReleaseDetail>): CoreReleaseDetail? {
    val header = editions.maxByOrNull { it.tracks.size } ?: return null
    val allTracks = editions.flatMap { it.tracks }
    val serversByLocal = allTracks.mapNotNull { row ->
        row.localRecordingId?.let { local -> row.serverRecordingId?.let { local to it } }
    }.toMap()
    val serversByTrack = allTracks.mapNotNull { row ->
        row.localUserTrackRefId?.let { local -> row.serverRecordingId?.let { local to it } }
    }.toMap()
    val tracks = allTracks.sortedBy { it.localUserTrackRefId == null }
        .distinctBy { row -> row.serverRecordingId ?: serversByLocal[row.localRecordingId] ?: serversByTrack[row.localUserTrackRefId]
            ?: row.localRecordingId ?: row.localUserTrackRefId ?: row.localReleaseTrackId }
        .sortedWith(compareBy<CoreReleaseDetailTrack> { it.mediumPosition ?: Int.MAX_VALUE }
            .thenBy { it.sequenceNo ?: Int.MAX_VALUE }.thenBy { it.title })
    return header.copy(localReleaseId = id, tracks = tracks,
        artworkTrackId = tracks.firstNotNullOfOrNull { it.localUserTrackRefId })
}

internal const val EDITION_PREFIX = "catalog-edition:"
internal fun editionTargetId(id: String) = EDITION_PREFIX + id

internal fun app.autplay.data.local.dao.TaggedAlbumTrack.albumKey(): String {
    val key = albumArtist.trim().lowercase(java.util.Locale.ROOT) + "\u0000" + album.trim().lowercase(java.util.Locale.ROOT)
    return "tag-album:" + java.util.UUID.nameUUIDFromBytes(key.toByteArray(Charsets.UTF_8))
}

private fun List<app.autplay.data.local.dao.TaggedAlbumTrack>.coverTrackId(): String =
    (firstOrNull { it.hasArtwork } ?: first()).trackId

private fun List<app.autplay.data.local.dao.TaggedAlbumTrack>.summaries(): List<CoreReleaseSummary> =
    groupBy { it.albumKey() }.map { (id, tracks) ->
        val first = tracks.first()
        CoreReleaseSummary(id, first.album, first.albumArtist, tracks.firstNotNullOfOrNull { it.date },
            tracks.size, tracks.coverTrackId())
    }
