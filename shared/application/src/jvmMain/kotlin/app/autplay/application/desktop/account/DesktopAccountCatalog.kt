package app.autplay.application.desktop.account

import kotlinx.serialization.json.JsonObject

data class AccountCatalogArtist(val artistId: AccountUuid, val name: String, val sortName: String?,
    val rawType: String?, val disambiguation: String?, val countryCode: String?, val rawIdentityStatus: String?)
data class AccountCatalogCreditMember(val artistId: AccountUuid, val position: Int,
    val creditedName: String, val joinPhrase: String, val rawRole: String)
data class AccountCatalogAppearance(val subjectType: String, val subjectId: AccountUuid,
    val creditId: AccountUuid, val displayName: String, val members: List<AccountCatalogCreditMember>)
data class AccountMetadataAlbum(val key: String, val provider: String, val releaseId: String,
    val title: String, val albumArtist: String?, val releaseDate: String?, val discNumber: Int?, val trackNumber: Int?)
data class AccountTrackMetadata(val trackId: AccountUuid, val revision: Long, val rawState: String,
    val fields: JsonObject, val provenance: JsonObject, val album: AccountMetadataAlbum?, val retainedDocument: JsonObject)
data class AccountAttribution(val sourceId: AccountUuid, val aggregateType: String, val aggregateId: AccountUuid, val document: JsonObject,val rawObservationKind:String="SERVER")
data class AccountCatalogSubjectCursor(val subjectId:AccountUuid,val subjectType:String)
/** Empty visible items do not mean EOF: continue from scannedThrough until exhausted. */
data class AccountCatalogArtistPage(val items:List<AccountCatalogArtist>,val scannedThrough:AccountUuid?,val exhausted:Boolean)
data class AccountCatalogAppearancePage(val items:List<AccountCatalogAppearance>,val scannedThrough:AccountCatalogSubjectCursor?,val exhausted:Boolean)
data class AccountCatalogTrackContext(val track: AccountLocalDomainRow,
    val library: List<AccountLocalDomainRow>, val libraryHasMore: Boolean)
interface DesktopAccountCatalog {
    /** Entire admitted current J set or an explicit refusal; never a first-page snapshot. */
    suspend fun captureSearch(lease: AccountLease, kind: AccountCatalogSearchKind): AccountCatalogSearchSnapshot =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun requireSearchRevision(lease: AccountLease, revision: Long): Unit =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun readPlaylist(lease: AccountLease, playlistId: AccountUuid): AccountLocalDomainRow? =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    /** Descriptive availability only; this read never admits playback or grants a source. */
    suspend fun readTrackContext(lease: AccountLease, trackId: AccountUuid): AccountCatalogTrackContext? =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    /** Exact ID reads retain the same current owner-proof visibility as paged appearances. */
    suspend fun readArtist(lease: AccountLease, artistId: AccountUuid): AccountCatalogArtist? =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun readSubject(lease: AccountLease, subject: AccountCatalogSubjectCursor): AccountCatalogAppearance? =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    /** Credit links only: neither a Recording title nor a canonical release track list is implied. */
    suspend fun readSubjectPage(lease: AccountLease, subjectType: String,
        after: AccountCatalogSubjectCursor?, limit: Int = 100): AccountCatalogAppearancePage =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun readArtistPage(lease:AccountLease,after:AccountUuid?,limit:Int=100):AccountCatalogArtistPage =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    suspend fun readAppearancePage(lease:AccountLease,artistId:AccountUuid,after:AccountCatalogSubjectCursor?,limit:Int=100):AccountCatalogAppearancePage =
        throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    /** Compatibility list drains bounded scan pages; reports unavailable instead of false EOF. */
    suspend fun readArtists(lease: AccountLease, after: AccountUuid?, limit: Int = 100): List<AccountCatalogArtist>
    suspend fun readAppearances(lease: AccountLease, artistId: AccountUuid, afterSubject: AccountUuid?, limit: Int = 100): List<AccountCatalogAppearance>
    suspend fun readMetadata(lease: AccountLease, trackId: AccountUuid): AccountTrackMetadata?
    suspend fun readAttribution(lease: AccountLease, sourceId: AccountUuid): List<AccountAttribution>
}
