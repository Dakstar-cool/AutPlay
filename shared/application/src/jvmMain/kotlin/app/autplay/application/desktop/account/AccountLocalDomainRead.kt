package app.autplay.application.desktop.account

import kotlinx.serialization.json.JsonObject

/** A local ID can remain usable before ACK; absent server identity/version is explicit. */
data class AccountLocalDomainHeader(val localId:AccountUuid,val serverId:AccountUuid?,val observedVersion:Long?,
    val localRevision:Long,val pending:Boolean,val removed:Boolean,val rawProjectionState:String?=null,
    val snapshotConfirmed:Boolean?=null,val hasConflict:Boolean=false)
data class AccountLocalDomainRow(val header:AccountLocalDomainHeader,val detail:AccountLocalDomainDetail)
/** Ordering is the persisted position key followed by the distinct occurrence ID. */
data class AccountPlaylistCursor(val positionKey:String,val occurrenceId:AccountUuid)
data class AccountPlaylistOccurrence(val occurrence:AccountLocalDomainRow,val track:AccountLocalDomainRow?)
data class AccountPlaylistPage(val items:List<AccountPlaylistOccurrence>,val next:AccountPlaylistCursor?)
sealed interface AccountLocalDomainDetail {
    data class Track(val title:String?,val artist:String?,val album:String?,val durationMs:Long?,val recordingId:AccountUuid?):AccountLocalDomainDetail
    data class Library(val trackId:AccountUuid,val rawSource:String?,val rawAvailability:String?):AccountLocalDomainDetail
    data class Preference(val trackId:AccountUuid,val rawPreference:String,val excludedFromTaste:Boolean):AccountLocalDomainDetail
    data class Playlist(val name:String?,val description:String?,val rawVisibility:String?,val rawType:String?):AccountLocalDomainDetail
    data class Entry(val playlistId:AccountUuid,val trackId:AccountUuid,val positionKey:String):AccountLocalDomainDetail
    data class Listening(val trackId:AccountUuid,val sourceLocalTrackId:AccountUuid,val recordingId:AccountUuid?,val playedMs:Long,
        val durationMs:Long?,val completionRatio:Double?,val rawOrigin:String,val rawContext:String,val rawFeedback:String,
        val excludedFromTaste:Boolean,val recommendation:JsonObject?,val sourceCreatedAt:String?):AccountLocalDomainDetail
}
