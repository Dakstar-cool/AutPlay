package app.autplay.ui.desktop

import app.autplay.application.desktop.account.AccountCatalogAppearance
import app.autplay.application.desktop.account.AccountCatalogArtist
import app.autplay.application.desktop.account.AccountMediaCopyView
import app.autplay.application.desktop.account.AccountMetadataAlbum
import app.autplay.application.desktop.account.AccountPortableStatistics
import app.autplay.application.desktop.account.AccountRemotePlaylist
import app.autplay.application.desktop.account.AccountRemotePlaylistEntry
import app.autplay.application.desktop.account.AccountRemoteTrack
import app.autplay.application.desktop.account.MediaAttempt
import app.autplay.application.desktop.account.MediaDownloadIntent
import java.text.NumberFormat
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale

/** Pure projection adapters. The host performs bounded repository reads on its own worker. */
public fun desktopAccountArtistEntry(artist: AccountCatalogArtist, ownerProofConfirmed: Boolean?): DesktopCatalogEntry =
    DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Artist, artist.artistId.value), artist.name,
        artist.disambiguation, DesktopAccountCondition.Ready, ownerProofConfirmed = ownerProofConfirmed,
        facts = buildList {
            add(DesktopCatalogFact(DesktopCatalogField.StableId, artist.artistId.value))
            artist.sortName?.let { add(DesktopCatalogFact(DesktopCatalogField.SortName, it)) }
            artist.rawType?.let { add(DesktopCatalogFact(DesktopCatalogField.ArtistType, it)) }
            artist.countryCode?.let { add(DesktopCatalogFact(DesktopCatalogField.Country, it)) }
            artist.rawIdentityStatus?.let { add(DesktopCatalogFact(DesktopCatalogField.IdentityStatus, it)) }
        })

/** readAppearances emits only complete owner-proof projections. No display-name identity is created. */
public fun desktopAccountAppearanceEntry(appearance: AccountCatalogAppearance, subjectTitle: String? = null): DesktopCatalogEntry? {
    val kind = when (appearance.subjectType) {
        "RECORDING_ARTIST_CREDIT" -> DesktopCatalogKind.Recording
        "RELEASE_ARTIST_CREDIT" -> DesktopCatalogKind.Release
        else -> return null
    }
    return DesktopCatalogEntry(DesktopCatalogKey(kind, appearance.subjectId.value), subjectTitle, appearance.displayName,
        DesktopAccountCondition.Ready, appearance.members.sortedBy { it.position }.map { credit ->
            DesktopCreditView(credit.artistId.value, credit.creditedName, credit.position, credit.joinPhrase, credit.rawRole)
        }, ownerProofConfirmed = true, creditProjectionOnly = true, availabilityUnconfirmed = true,
        facts = listOf(DesktopCatalogFact(DesktopCatalogField.StableId, appearance.subjectId.value)))
}

/** An evidence key groups metadata albums; provider release text is never a canonical Release ID. */
public fun desktopAccountMetadataAlbumEntry(album: AccountMetadataAlbum): DesktopCatalogEntry =
    DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Album, album.key), album.title,
        album.albumArtist, DesktopAccountCondition.Ready, metadataAlbumOnly = true, availabilityUnconfirmed = true,
        facts = buildList {
            add(DesktopCatalogFact(DesktopCatalogField.Provider, album.provider))
            add(DesktopCatalogFact(DesktopCatalogField.ProviderEdition, album.releaseId))
            album.releaseDate?.let { add(DesktopCatalogFact(DesktopCatalogField.ReleaseDate, it)) }
        })

public fun desktopAccountTrackEntry(track: AccountRemoteTrack, ownerProofConfirmed: Boolean?,
    identityResolved: Boolean?, appearance: AccountCatalogAppearance? = null,
    occurrence: AccountRemotePlaylistEntry? = null, actionTargetId: String? = null): DesktopCatalogEntry {
    val header = track.header
    // A caller cannot attach the credit of a different recording merely because the labels match.
    val credit = appearance?.takeIf { it.subjectType == "RECORDING_ARTIST_CREDIT" && it.subjectId == track.recordingId }
    val ordered = credit?.members?.sortedBy { it.position }?.map {
        DesktopCreditView(it.artistId.value, it.creditedName, it.position, it.joinPhrase, it.rawRole)
    }.orEmpty()
    val sameOccurrence = occurrence?.takeIf { it.trackRefId == header.localId }
    return DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Track, header.localId.value), track.title,
        track.artist, when {
            header.deleted -> DesktopAccountCondition.Attention
            header.pending || header.snapshotConfirmed != true -> DesktopAccountCondition.Pending
            else -> DesktopAccountCondition.Ready
        }, ordered, if (track.recordingId == null) false else identityResolved, ownerProofConfirmed,
        pending = header.pending, occurrenceId = sameOccurrence?.header?.localId?.value,
        actionTargetId = actionTargetId)
}

public fun desktopAccountPlaylistEntry(playlist: AccountRemotePlaylist): DesktopCatalogEntry =
    DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Playlist, playlist.header.localId.value),
        playlist.name, playlist.description, when {
            playlist.header.deleted -> DesktopAccountCondition.Attention
            playlist.header.pending || playlist.header.snapshotConfirmed != true -> DesktopAccountCondition.Pending
            else -> DesktopAccountCondition.Ready
        }, pending = playlist.header.pending)

/** Missing playlist tracks retain the occurrence and remain openable; duplicate IDs never collapse. */
public fun desktopAccountMissingPlaylistTrack(entry: AccountRemotePlaylistEntry, actionTargetId: String): DesktopCatalogEntry =
    DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Track, entry.trackRefId.value), null, null,
        DesktopAccountCondition.Unavailable, identityResolved = false, pending = entry.header.pending,
        occurrenceId = entry.header.localId.value, actionTargetId = actionTargetId)

public fun desktopAccountStatisticsView(statistics: AccountPortableStatistics, origin: DesktopStatisticsOrigin,
    language: DesktopLanguage): DesktopAccountStatisticsView {
    val locale = accountPresentationLocale(language)
    val numbers = NumberFormat.getIntegerInstance(locale)
    val available = statistics.available
    return DesktopAccountStatisticsView(origin,
        if (available) DesktopAccountCondition.Ready else DesktopAccountCondition.Unavailable,
        statistics.listens?.takeIf { available && it >= 0 }?.let(numbers::format),
        statistics.playedMs?.takeIf { available && it >= 0 }?.let { milliseconds ->
            val seconds = milliseconds / 1_000
            val hours = seconds / 3_600
            val minutes = seconds / 60 % 60
            val remainder = seconds % 60
            if (language == DesktopLanguage.Russian) "${numbers.format(hours)} ч ${numbers.format(minutes)} мин ${numbers.format(remainder)} с"
            else "${numbers.format(hours)} h ${numbers.format(minutes)} min ${numbers.format(remainder)} s"
        }, statistics.checkedThroughMs?.let {
            DateTimeFormatter.ofLocalizedDateTime(FormatStyle.MEDIUM).withLocale(locale).withZone(ZoneId.systemDefault())
                .format(Instant.ofEpochMilli(it))
        }, statistics.complete)
}

/**
 * confirmedOfflinePlayable must come from current LOCAL/media admission, never just a saved J copy.
 * This adapter does not call, cache or mint that authority; click admission still belongs to the host.
 */
public fun desktopAccountDownloadView(intent: MediaDownloadIntent, attempt: MediaAttempt?,
    copy: AccountMediaCopyView?, title: String?, confirmedOfflinePlayable: Boolean,
    language: DesktopLanguage): DesktopDownloadView {
    val currentAttempt = attempt?.takeIf { it.intentId == intent.intentId && it.attempt == intent.currentAttempt &&
        it.record.storeId == intent.record.storeId && it.record.scopeId == intent.record.scopeId }
    val completed = copy?.takeIf { it.copy.copyId == intent.completedCopyId &&
        it.copy.record.storeId == intent.record.storeId && it.copy.record.scopeId == intent.record.scopeId &&
        it.copy.localTrackRefId == intent.localTrackRefId && it.copy.integrityState == "VERIFIED" &&
        it.copy.publicationState == "COMMITTED" && it.ownership.state == "OWNED" }
    val total = completed?.copy?.totalBytes ?: currentAttempt?.totalBytes
    val confirmed = completed?.copy?.totalBytes ?: currentAttempt?.confirmedPrefixBytes
    val numbers = NumberFormat.getIntegerInstance(accountPresentationLocale(language))
    val progress = if (confirmed != null && confirmed >= 0) {
        val bytes = if (language == DesktopLanguage.Russian) "Б" else "B"
        if (total != null && total >= confirmed) "${numbers.format(confirmed)} / ${numbers.format(total)} $bytes"
        else "${numbers.format(confirmed)} $bytes"
    } else null
    val cancelled = intent.desiredState == "CANCEL" && intent.coarseState != "CANCELED"
    return DesktopDownloadView(intent.intentId.value, intent.localTrackRefId.value, title,
        when (intent.storageClass) {
            "CACHE" -> DesktopMediaStorage.Cache
            "PINNED" -> DesktopMediaStorage.Pinned
            "USER_DOWNLOAD" -> DesktopMediaStorage.UserDownload
            else -> DesktopMediaStorage.Unknown
        }, when {
            cancelled -> DesktopAccountCondition.Pending
            intent.coarseState == "COMPLETED" && completed != null -> DesktopAccountCondition.Ready
            intent.coarseState in setOf("REQUESTED", "QUEUED", "DOWNLOADING") -> DesktopAccountCondition.Pending
            intent.coarseState == "PAUSED" -> DesktopAccountCondition.Attention
            intent.coarseState == "CANCELED" -> DesktopAccountCondition.Ready
            else -> DesktopAccountCondition.Unavailable
        }, confirmed, total, confirmedOfflinePlayable && completed != null,
        if (cancelled) DesktopAccountIssue.CancelPending else when (intent.stopReason) {
            "STORAGE_FULL", "CAPACITY_EXCEEDED" -> DesktopAccountIssue.Capacity
            "NETWORK_UNAVAILABLE" -> DesktopAccountIssue.Retry
            "AUTH_REQUIRED" -> DesktopAccountIssue.Reconnect
            null, "USER", "COMPLETE" -> DesktopAccountIssue.None
            else -> DesktopAccountIssue.Retry
        }, progress)
}

/** Retained CACHE, PINNED and USER_DOWNLOAD copies stay separate from current download intents. */
public fun desktopAccountRetainedCopyView(copy: AccountMediaCopyView, title: String?,
    confirmedOfflinePlayable: Boolean, issue: DesktopAccountIssue, language: DesktopLanguage): DesktopDownloadView {
    val record = copy.copy
    val completed = record.integrityState == "VERIFIED" && record.publicationState == "COMMITTED" &&
        copy.ownership.state == "OWNED"
    val numbers = NumberFormat.getIntegerInstance(accountPresentationLocale(language))
    val unit = if (language == DesktopLanguage.Russian) "Б" else "B"
    return DesktopDownloadView(record.copyId.value, record.localTrackRefId.value, title, when (record.storageClass) {
        "CACHE" -> DesktopMediaStorage.Cache
        "PINNED" -> DesktopMediaStorage.Pinned
        "USER_DOWNLOAD" -> DesktopMediaStorage.UserDownload
        else -> DesktopMediaStorage.Unknown
    }, if (completed && confirmedOfflinePlayable) DesktopAccountCondition.Ready else DesktopAccountCondition.Attention,
        if (completed) record.totalBytes else null, record.totalBytes, completed && confirmedOfflinePlayable,
        issue, "${numbers.format(record.totalBytes)} $unit", retainedCopy = true)
}

private fun accountPresentationLocale(language: DesktopLanguage): Locale =
    if (language == DesktopLanguage.Russian) Locale.forLanguageTag("ru-RU") else Locale.ENGLISH
