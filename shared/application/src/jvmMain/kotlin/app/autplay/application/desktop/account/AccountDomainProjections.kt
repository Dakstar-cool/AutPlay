package app.autplay.application.desktop.account

enum class AccountProjectionKind { TRACK, LIBRARY, PREFERENCE, PLAYLIST, PLAYLIST_ENTRY, LISTENING }

/** Original FK identity and explicit redirect resolution are both exposed; reads never rekey rows. */
data class AccountProjectionHeader(
    val localId: AccountUuid,
    val serverId: AccountUuid,
    val canonicalLocalId: AccountUuid?,
    val canonicalServerId: AccountUuid,
    val observedVersion: Long?,
    val rawState: String,
    val deleted: Boolean,
    val pending: Boolean,
    /** null: no full snapshot for this family; false: retained data lacks snapshot/newer-effect confirmation. */
    val snapshotConfirmed: Boolean? = null,
) { override fun toString(): String = "AccountProjectionHeader" }

sealed interface AccountDomainProjection { val header: AccountProjectionHeader }
data class AccountRemoteTrack(
    override val header: AccountProjectionHeader,
    val title: String?, val artist: String?, val album: String?, val durationMs: Long?,
    val recordingId: AccountUuid?, val rawResolutionStatus: String?,
) : AccountDomainProjection { override fun toString(): String = "AccountRemoteTrack" }
data class AccountRemoteLibraryEntry(
    override val header: AccountProjectionHeader, val trackRefId: AccountUuid,
    val rawSource: String?, val rawAvailability: String?,
) : AccountDomainProjection { override fun toString(): String = "AccountRemoteLibraryEntry" }
data class AccountRemotePreference(
    override val header: AccountProjectionHeader, val trackRefId: AccountUuid,
    val rawPreference: String, val excludedFromTaste: Boolean,
) : AccountDomainProjection { override fun toString(): String = "AccountRemotePreference" }
data class AccountRemotePlaylist(
    override val header: AccountProjectionHeader, val name: String?, val description: String?,
    val rawVisibility: String?, val rawPlaylistType: String?,
) : AccountDomainProjection { override fun toString(): String = "AccountRemotePlaylist" }
data class AccountRemotePlaylistEntry(
    override val header: AccountProjectionHeader, val playlistId: AccountUuid,
    val trackRefId: AccountUuid, val positionKey: String,
) : AccountDomainProjection { override fun toString(): String = "AccountRemotePlaylistEntry" }
data class AccountRemoteListening(
    override val header: AccountProjectionHeader, val trackRefId: AccountUuid,
    val sourceLocalTrackId: AccountUuid, val recordingId: AccountUuid?, val playedMs: Long,
    val durationMs: Long?, val completionRatio: Double?, val rawOrigin: String, val rawContext: String,
    val rawExplicitFeedback: String, val excludedFromTaste: Boolean,
    /** Wire has created_at, not the original listening started_at: never relabel this as start time. */
    val sourceCreatedAt: String?,
) : AccountDomainProjection { override fun toString(): String = "AccountRemoteListening" }
