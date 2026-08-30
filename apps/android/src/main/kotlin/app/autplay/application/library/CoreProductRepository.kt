package app.autplay.application.library

import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.RecordingProjectionEntity
import app.autplay.data.local.entity.ReleaseTrackProjectionEntity
import app.autplay.domain.ServerId
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.mapLatest
import kotlinx.coroutines.ExperimentalCoroutinesApi

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
    fun libraryEntries(profileId: String?, limit: Int = 5_000): Flow<List<CoreLibraryEntrySummary>> {
        require(limit in 1..5_000)
        val effectiveProfileId = effectiveProfile(profileId)
        return database.libraryDao().entriesForProfile(effectiveProfileId, limit).mapLatest { entries ->
            val tracks = resolveTrackRefs(entries.map { it.localUserTrackRefId }, effectiveProfileId)
            entries.map { entry ->
                val track = tracks[entry.localUserTrackRefId]
                CoreLibraryEntrySummary(
                    localLibraryEntryId = entry.localLibraryEntryId,
                    localUserTrackRefId = entry.localUserTrackRefId,
                    title = track?.rawTitle,
                    artistName = track?.rawArtist,
                    addedAtMs = entry.addedAtMs,
                    availabilityStatus = entry.availabilityStatus,
                    removed = entry.removedAtMs != null,
                    serverPlaybackCandidate = track?.serverUserTrackRefId != null &&
                        track.serverRecordingId != null &&
                        track.resolutionStatus == "RESOLVED" &&
                        track.deletedAtMs == null,
                )
            }
        }
    }

    fun recentlyAdded(profileId: String?, limit: Int = 12): Flow<List<CoreHomeTrackSummary>> {
        require(limit in 1..50)
        return database.libraryDao().activeEntriesForProfile(effectiveProfile(profileId), limit).mapLatest { entries ->
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
        return database.historyDao().recentForProfile(effectiveProfile(profileId), limit).mapLatest { events ->
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
        return database.playlistDao().activePlaylistsForProfile(effectiveProfile(profileId), limit).map { rows ->
            rows.map { CoreHomePlaylistSummary(it.localPlaylistId, it.name, it.description) }
        }
    }

    fun homePlaylists(profileId: String?, limit: Int = 8): Flow<List<CoreHomePlaylistSummary>> {
        require(limit in 1..20)
        return playlists(profileId, limit)
    }

    fun historyCount(profileId: String?, limit: Int = 2_000): Flow<Int> {
        require(limit in 1..5_000)
        return database.historyDao().recentForProfile(effectiveProfile(profileId), limit).map { it.size }
    }

    fun activeQueue(profileId: String?): Flow<CoreResumeQueueSummary?> =
        database.queueDao().activeSnapshot().mapLatest { snapshot ->
            if (snapshot == null || snapshot.serverProfileId != profileId) return@mapLatest null
            val entry = snapshot.currentEntryId?.let { database.queueDao().entry(it) }
                ?: database.queueDao().entries(snapshot.queueSnapshotId, 1).firstOrNull()
                ?: return@mapLatest null
            val track = database.libraryDao().trackRef(entry.localUserTrackRefId) ?: return@mapLatest null
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
        return database.catalogProjectionDao().releasesForProfile(effectiveProfile(profileId), limit).map { rows ->
            rows.map { CoreReleaseSummary(it.localReleaseId, it.title, it.displayArtist) }
        }
    }

    fun preferences(profileId: String?, limit: Int = 2_000): Flow<List<CoreTrackPreferenceSummary>> {
        require(limit in 1..5_000)
        return database.libraryDao().preferencesForProfile(effectiveProfile(profileId), limit).map { rows ->
            rows.map {
                CoreTrackPreferenceSummary(
                    stableId = it.localUserTrackRefId,
                    loved = it.preference == "LIKED",
                    disliked = it.preference == "DISLIKED",
                )
            }
        }
    }

    fun localAudio(profileId: String?, limit: Int = 2_000): Flow<List<CoreLocalAudioSummary>> {
        require(limit in 1..5_000)
        return database.localAudioDao().statesForProfile(effectiveProfile(profileId), limit).map { rows ->
            rows.map { CoreLocalAudioSummary(it.localUserTrackRefId, it.status, it.persistedUriPermission) }
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
        val audioCapabilityStates = audioStates.map { CoreAudioCapabilityState(it.status, it.persistedUriPermission) }
        val hasServerPlaybackCandidate = profileId != null &&
            track.serverUserTrackRefId != null &&
            track.serverRecordingId != null &&
            track.resolutionStatus == "RESOLVED" &&
            track.deletedAtMs == null
        return CoreTrackDetail(
            localUserTrackRefId = track.localUserTrackRefId,
            localRecordingId = track.localRecordingId,
            serverRecordingId = track.serverRecordingId?.let(::ServerId),
            title = recording?.title ?: track.rawTitle,
            artistName = recording?.displayArtist ?: track.rawArtist,
            albumName = track.rawAlbum,
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

    suspend fun releaseDetail(localReleaseId: String, profileId: String?): CoreReleaseDetail? {
        val release = database.catalogProjectionDao().releaseForProfile(
            localReleaseId,
            effectiveProfile(profileId),
        ) ?: return null
        val releaseTracks = database.catalogProjectionDao().releaseTracks(localReleaseId, MAX_RELEASE_TRACKS)
        val recordingIds = releaseTracks.map { it.localRecordingId }.distinct()
        val recordings = if (recordingIds.isEmpty()) emptyMap() else database.catalogProjectionDao().recordings(
            localIds = recordingIds,
            limit = MAX_RELEASE_TRACKS,
        ).associateBy { it.localRecordingId }
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
            tracks = releaseTracks.map {
                it.toDetailRow(recordings[it.localRecordingId], trackRefsByRecording[it.localRecordingId]?.localUserTrackRefId)
            },
        )
    }

    suspend fun playlistDetail(localPlaylistId: String, profileId: String?): CorePlaylistDetail? {
        val playlist = database.playlistDao().playlist(localPlaylistId) ?: return null
        if (playlist.deletedAtMs != null || playlist.serverProfileId != effectiveProfile(profileId)) return null
        val entries = database.playlistDao().activeEntryList(localPlaylistId, MAX_PLAYLIST_ENTRIES)
        val trackRefIds = entries.map { it.localUserTrackRefId }.distinct()
        val trackRefs = trackRefIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { ids ->
            database.libraryDao().trackRefs(localIds = ids, limit = ids.size)
        }.filter { it.serverProfileId == effectiveProfile(profileId) }.associateBy { it.localUserTrackRefId }
        val recordingIds = trackRefs.values.mapNotNull { it.localRecordingId }.distinct()
        val recordings = recordingIds.chunked(MAX_IN_QUERY_BINDINGS).flatMap { ids ->
            database.catalogProjectionDao().recordings(localIds = ids, limit = ids.size)
        }.associateBy { it.localRecordingId }
        return CorePlaylistDetail(
            localPlaylistId = playlist.localPlaylistId,
            name = playlist.name,
            description = playlist.description,
            playlistType = playlist.playlistType,
            entries = entries.map { entry ->
                val track = trackRefs[entry.localUserTrackRefId]
                val recording = track?.localRecordingId?.let(recordings::get)
                CorePlaylistDetailEntry(
                    // This is the UI identity. Do not substitute the repeated track-ref ID.
                    localPlaylistEntryId = entry.localPlaylistEntryId,
                    localUserTrackRefId = entry.localUserTrackRefId,
                    title = recording?.title ?: track?.rawTitle,
                    artistName = recording?.displayArtist ?: track?.rawArtist,
                    durationMs = recording?.durationMs ?: track?.rawDurationMs,
                    unavailable = track == null || track.deletedAtMs != null,
                )
            },
        )
    }

    private suspend fun resolveTrackRefs(ids: List<String>, profileId: String): Map<String, app.autplay.data.local.entity.UserTrackRefEntity> =
        ids.distinct().chunked(MAX_IN_QUERY_BINDINGS).flatMap { chunk ->
            database.libraryDao().trackRefs(chunk, chunk.size)
        }.filter { it.serverProfileId == profileId && it.deletedAtMs == null }.associateBy { it.localUserTrackRefId }

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
        )

    private fun effectiveProfile(profileId: String?): String = profileId ?: LEGACY_PROFILE_ID

    private companion object {
        const val MAX_AUDIO_STATES = 8
        const val MAX_DOWNLOAD_INTENTS = 32
        const val MAX_RELEASE_TRACKS = 500
        const val MAX_PLAYLIST_ENTRIES = 1_000
        const val MAX_IN_QUERY_BINDINGS = 900
        const val LEGACY_PROFILE_ID = "legacy-unscoped"
    }
}

data class CoreReleaseSummary(val stableId: String, val title: String, val artistName: String)
data class CoreLibraryEntrySummary(
    val localLibraryEntryId: String,
    val localUserTrackRefId: String,
    val title: String?,
    val artistName: String?,
    val addedAtMs: Long,
    val availabilityStatus: String,
    val removed: Boolean,
    val serverPlaybackCandidate: Boolean,
)
data class CoreHomeTrackSummary(
    val stableId: String,
    val title: String?,
    val artistName: String?,
    val occurredAtMs: Long,
)
data class CoreHomePlaylistSummary(val stableId: String, val title: String, val description: String?)
data class CoreResumeQueueSummary(
    val queueSnapshotId: String,
    val localUserTrackRefId: String,
    val title: String?,
    val artistName: String?,
    val positionMs: Long,
    val queueType: String,
)
data class CoreTrackPreferenceSummary(
    val stableId: String,
    val loved: Boolean,
    val disliked: Boolean = false,
)
data class CoreLocalAudioSummary(
    val stableId: String,
    val status: String,
    val persistedUriPermission: Boolean,
)
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
)

data class CoreTrackPreferenceState(
    val preference: String,
    val excludedFromTaste: Boolean,
)

data class CoreReleaseDetail(
    val localReleaseId: String,
    val serverReleaseId: ServerId?,
    val title: String,
    val artistName: String,
    val releaseDateText: String?,
    val releaseType: String?,
    val artworkRef: String?,
    val tracks: List<CoreReleaseDetailTrack>,
)

data class CoreReleaseDetailTrack(
    val localReleaseTrackId: String,
    val localRecordingId: String,
    val mediumPosition: Int,
    val sequenceNo: Int,
    val numberText: String?,
    val title: String,
    val artistName: String,
    val durationMs: Long?,
    val localUserTrackRefId: String?,
)

data class CorePlaylistDetail(
    val localPlaylistId: String,
    val name: String,
    val description: String?,
    val playlistType: String,
    val entries: List<CorePlaylistDetailEntry>,
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

enum class CoreTrackAvailability { PLAYABLE_LOCAL, PLAYABLE_SERVER, PERMISSION_REVOKED, UNAVAILABLE, NO_LOCAL_SOURCE }

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
)

/** Pure policy seam: test it without Room or Android runtime. */
object CoreProductDetailPolicy {
    fun availability(
        audioStates: List<CoreAudioCapabilityState>,
        hasServerPlaybackCandidate: Boolean = false,
    ): CoreTrackAvailability = when {
        audioStates.any { it.status == "AVAILABLE" && it.persistedUriPermission } -> CoreTrackAvailability.PLAYABLE_LOCAL
        hasServerPlaybackCandidate -> CoreTrackAvailability.PLAYABLE_SERVER
        audioStates.any { it.status == "PERMISSION_REVOKED" || !it.persistedUriPermission } -> CoreTrackAvailability.PERMISSION_REVOKED
        audioStates.isNotEmpty() -> CoreTrackAvailability.UNAVAILABLE
        else -> CoreTrackAvailability.NO_LOCAL_SOURCE
    }

    fun capabilities(input: CoreTrackCapabilityInput): Set<CoreTrackDetailCapability> = buildSet {
        if (
            availability(input.audioStates, input.hasServerPlaybackCandidate) in
            setOf(CoreTrackAvailability.PLAYABLE_LOCAL, CoreTrackAvailability.PLAYABLE_SERVER)
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
