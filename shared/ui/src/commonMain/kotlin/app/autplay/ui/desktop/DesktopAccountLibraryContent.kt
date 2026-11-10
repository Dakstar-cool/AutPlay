package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp

/** Optional insertion into the existing Home, without replacing the standalone sections/player. */
@Composable
public fun DesktopAccountHomeSections(state: DesktopAccountWorkspaceState, actions: DesktopAccountActionSink,
    language: DesktopLanguage, modifier: Modifier = Modifier): Unit {
    val latestState = rememberUpdatedState(state)
    val latestActions = rememberUpdatedState(actions)
    val owner = remember(state, DesktopAccountSinkKey(actions)) {
        DesktopAccountUiOwner(state, null, actions, { latestState.value }, { latestActions.value })
    }
    DisposableEffect(owner) { onDispose { owner.retire() } }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        if (state.catalog.navigationRequested) DesktopAccountCatalogContent(owner, language, includeHome = false)
        AccountStatus(state.catalog.condition, DesktopAccountIssue.None, language)
        state.catalog.homeSections.forEach { section -> key(section.id) {
            Text(section.title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
            Text(DesktopCatalogText.Preview.text(language))
            section.entries.forEach { entry -> key(entry.key, entry.occurrenceId) { AccountCatalogEntry(owner, entry, language) } }
            section.browseTargetId?.let { AccountButton(owner, DesktopAccountAction.CatalogPage, language, it,
                label = DesktopCatalogText.Browse.text(language)) }
        } }
    }
}

private enum class DesktopCatalogPageSort { Source, Title, Artist, SortName }

@Composable
internal fun DesktopAccountCatalogContent(owner: DesktopAccountUiOwner, language: DesktopLanguage, includeHome: Boolean = true): Unit {
    val state = owner.state.catalog
    var pageSort by remember(owner.state.scope.lifetime, state.selectedKind, state.query, state.relation) {
        mutableStateOf(DesktopCatalogPageSort.Source)
    }
    AccountCard(DesktopAccountText.Catalog, language) {
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        DesktopAccountDomainControls(owner, state.mutations, language)
        state.mutationNotice?.let { Text(it) }
        DesktopAccountCatalogSearch(owner, language)
        state.relation?.let { Text(desktopCatalogRelationText(it, language)) }
        if (state.selectedKind == DesktopCatalogKind.Album || state.detail?.metadataAlbumOnly == true)
            Text(DesktopCatalogText.AlbumPage.text(language))
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            state.destinations.forEach { destination -> key(destination.targetId) {
                FilterChip(selected = destination.kind == state.selectedKind,
                    enabled = owner.available(DesktopAccountAction.CatalogPage, destination.targetId),
                    onClick = { owner.send(DesktopAccountCommand.Invoke(DesktopAccountAction.CatalogPage, destination.targetId)) },
                    label = { Text(desktopCatalogKindText(destination.kind, language)) }, modifier = Modifier.heightIn(min = 48.dp))
            } }
        }
        state.page?.let { page -> key(page.id) {
            Text(if (language == DesktopLanguage.Russian) "Сортировка текущей страницы; порядок обхода источника сохраняется. Даты добавления для этого каталога не получены."
                else "Sort the current page; source traversal order is retained. Added dates have not been supplied for this catalog.")
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val sorts = listOf(DesktopCatalogPageSort.Source, DesktopCatalogPageSort.Title) +
                    (if (state.selectedKind == DesktopCatalogKind.Album || state.selectedKind == DesktopCatalogKind.Track)
                        listOf(DesktopCatalogPageSort.Artist) else emptyList()) +
                    (if (state.selectedKind == DesktopCatalogKind.Artist) listOf(DesktopCatalogPageSort.SortName) else emptyList())
                sorts.forEach { sort ->
                    val label = when (sort) {
                        DesktopCatalogPageSort.Source -> if (language == DesktopLanguage.Russian) "Порядок источника" else "Source order"
                        DesktopCatalogPageSort.Title -> if (language == DesktopLanguage.Russian) "Название" else "Title"
                        DesktopCatalogPageSort.Artist -> if (language == DesktopLanguage.Russian) "Исполнитель по метаданным" else "Metadata artist"
                        DesktopCatalogPageSort.SortName -> desktopCatalogFieldText(DesktopCatalogField.SortName, language)
                    }
                    FilterChip(selected = pageSort == sort,
                        enabled = state.searchTargetId?.let { owner.available(DesktopAccountAction.CatalogPage, it) } == true,
                        onClick = { if (state.searchTargetId?.let { owner.available(DesktopAccountAction.CatalogPage, it) } == true) pageSort = sort },
                        label = { Text(label) }, modifier = Modifier.heightIn(min = 48.dp))
                }
            }
            if (page.entries.isEmpty()) Text((if (page.nextTarget != null) DesktopCatalogText.MoreScan.text(language)
                else DesktopAccountText.EmptyList.text(language)))
            fun value(entry: DesktopCatalogEntry): String? = when (pageSort) {
                DesktopCatalogPageSort.Source -> null
                DesktopCatalogPageSort.Title -> entry.title
                DesktopCatalogPageSort.Artist -> entry.subtitle
                DesktopCatalogPageSort.SortName -> entry.facts.singleOrNull { it.field == DesktopCatalogField.SortName }?.value
            }
            val ordered = if (pageSort == DesktopCatalogPageSort.Source) page.entries else page.entries.sortedWith(
                compareBy<DesktopCatalogEntry> { value(it) == null }.thenBy { value(it).orEmpty().lowercase() }
                    .thenBy { it.key.id }.thenBy { it.occurrenceId.orEmpty() })
            ordered.forEach { entry -> key(entry.key, entry.occurrenceId) { AccountCatalogEntry(owner, entry, language) } }
            page.previousTarget?.let { AccountButton(owner, DesktopAccountAction.CatalogPage, language, it,
                label = DesktopCatalogText.First.text(language)) }
            page.nextTarget?.let { AccountButton(owner, DesktopAccountAction.CatalogPage, language, it,
                label = DesktopAccountText.Next.text(language)) }
        } }
    }
    state.detail?.let { entry -> AccountCard(DesktopAccountText.Detail, language) {
        AccountCatalogEntry(owner, entry, language, detail = true)
    } }
    state.canonicalPositions?.let { DesktopCanonicalPositionsContent(owner, it, language) }
    if (includeHome) state.homeSections.forEach { section -> key(section.id) {
        Text(section.title, style = MaterialTheme.typography.titleLarge, modifier = Modifier.semantics { heading() })
        Text(DesktopCatalogText.Preview.text(language))
        section.entries.forEach { entry -> key(entry.key, entry.occurrenceId) { AccountCatalogEntry(owner, entry, language) } }
        section.browseTargetId?.let { AccountButton(owner, DesktopAccountAction.CatalogPage, language, it,
            label = DesktopCatalogText.Browse.text(language)) }
    } }
}

@Composable
private fun AccountCatalogEntry(owner: DesktopAccountUiOwner, entry: DesktopCatalogEntry,
    language: DesktopLanguage, detail: Boolean = false): Unit {
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(desktopCatalogKindText(entry.key.kind, language), style = MaterialTheme.typography.labelMedium)
            Text(entry.title ?: DesktopCatalogText.NoTitle.text(language), style = MaterialTheme.typography.titleMedium)
            entry.subtitle?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            AccountStatus(entry.condition, DesktopAccountIssue.None, language)
            if (entry.metadataAlbumOnly) Text(DesktopAccountText.MetadataAlbum.text(language))
            if (entry.identityResolved == false) Text(DesktopAccountText.Unresolved.text(language))
            if (entry.ownerProofConfirmed == false) Text(DesktopAccountText.ProofMissing.text(language))
            if (entry.pending) Text(DesktopAccountText.PendingChanges.text(language))
            if (entry.creditProjectionOnly) Text(DesktopCatalogText.CreditOnly.text(language))
            if (entry.availabilityUnconfirmed) Text(DesktopCatalogText.Availability.text(language))
            if (entry.partialContext) Text(DesktopCatalogText.Partial.text(language))
            if (detail) entry.facts.forEach { fact -> Text(desktopCatalogFieldText(fact.field, language) + ": " + fact.value) }
            // Ordered credits and stable artist IDs are supplied by the catalog projection adapter.
            entry.credits.forEach { credit -> key(credit.position, credit.artistId) {
                credit.actionTargetId?.let { AccountButton(owner, DesktopAccountAction.OpenArtist, language, it,
                    label = credit.name + credit.joinPhrase) }
                if (credit.actionTargetId == null) Text(credit.name + credit.joinPhrase)
                Text(desktopCatalogFieldText(DesktopCatalogField.CreditRole, language) + ": " + credit.role)
            } }
            val openAction = when (entry.key.kind) {
                DesktopCatalogKind.Artist -> DesktopAccountAction.OpenArtist
                DesktopCatalogKind.Album -> DesktopAccountAction.OpenAlbum
                DesktopCatalogKind.Recording -> DesktopAccountAction.OpenRecording
                DesktopCatalogKind.Release -> DesktopAccountAction.OpenRelease
                DesktopCatalogKind.Track -> DesktopAccountAction.OpenTrack
                DesktopCatalogKind.Playlist -> DesktopAccountAction.OpenPlaylist
            }
            val openTarget = entry.openTargetId ?: entry.actionTargetId
            if (!detail && openTarget != null) AccountButton(owner, openAction, language, openTarget)
            entry.links.forEach { link ->
                val action = when (link.kind) {
                    DesktopCatalogKind.Recording -> DesktopAccountAction.OpenRecording
                    DesktopCatalogKind.Album -> DesktopAccountAction.OpenAlbum
                    else -> null
                }
                if (action != null) AccountButton(owner, action, language, link.targetId,
                    label = desktopCatalogKindText(link.kind, language))
            }
            val target = entry.actionTargetId
            DesktopAccountDomainControls(owner, entry.mutations, language)
            if (entry.key.kind == DesktopCatalogKind.Track && target != null) {
                AccountButton(owner, DesktopAccountAction.PlayTrack, language, target)
                AccountButton(owner, DesktopAccountAction.QueueTrack, language, target)
                AccountButton(owner, DesktopAccountAction.Download, language, target)
            }
        }
    }
}

@Composable
internal fun DesktopAccountDownloadsContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.downloads
    AccountCard(DesktopAccountText.Downloads, language) {
        AccountStatus(state.condition, DesktopAccountIssue.None, language)
        Text(DesktopAccountText.DownloadHelp.text(language))
        val metered = state.meteredAllowed
        if (metered == null) Text(DesktopAccountText.MeteredUnknown.text(language))
        else AccountCheck(metered, DesktopAccountText.Metered.text(language),
            enabled = owner.available(DesktopAccountAction.SetMetered)) { next ->
            if (next != metered) owner.send(DesktopAccountCommand.Metered(next))
        }
        AccountButton(owner, DesktopAccountAction.RefreshDownloads, language)
        if (state.entries.isEmpty() && state.condition == DesktopAccountCondition.Ready) Text(DesktopAccountText.EmptyList.text(language))
        state.entries.forEach { download -> key(download.id) {
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surface,
                modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(download.title ?: DesktopAccountText.Untitled.text(language), style = MaterialTheme.typography.titleMedium)
                    Text(when (download.storage) {
                        DesktopMediaStorage.Cache -> DesktopAccountText.Cache
                        DesktopMediaStorage.Pinned -> DesktopAccountText.Pinned
                        DesktopMediaStorage.UserDownload -> DesktopAccountText.UserDownload
                        DesktopMediaStorage.Unknown -> DesktopAccountText.UnknownStorage
                    }.text(language))
                    AccountStatus(download.condition, download.issue, language)
                    Text(download.progressLabel ?: DesktopAccountText.Unconfirmed.text(language))
                    val total = download.totalBytes
                    val complete = download.confirmedBytes
                    if (total != null && total > 0 && complete != null && complete in 0..total)
                        LinearProgressIndicator(progress = { complete.toFloat() / total.toFloat() }, modifier = Modifier.fillMaxWidth())
                    // Saved completion records alone never authorize local playback.
                    if (download.offlinePlayable) AccountButton(owner, DesktopAccountAction.PlayOffline, language, download.id)
                    else if (download.retainedCopy) Text(DesktopAccountText.RetainedUnavailable.text(language))
                    if (!download.retainedCopy) {
                        AccountButton(owner, DesktopAccountAction.CancelDownload, language, download.id)
                        AccountButton(owner, DesktopAccountAction.RetryDownload, language, download.id)
                    }
                }
            }
        } }
        if (state.hasMore) Text(DesktopAccountText.MoreDownloads.text(language))
        state.previousTarget?.let { AccountButton(owner, DesktopAccountAction.DownloadPage, language, it,
            label = DesktopAccountText.Previous.text(language)) }
        state.nextTarget?.let { AccountButton(owner, DesktopAccountAction.DownloadPage, language, it,
            label = DesktopAccountText.Next.text(language)) }
    }
}

@Composable
internal fun DesktopAccountStatisticsContent(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    AccountCard(DesktopAccountText.Statistics, language) {
        Text(DesktopAccountText.StatisticsHelp.text(language))
        AccountButton(owner, DesktopAccountAction.RefreshStatistics, language)
        if (owner.state.statistics.isEmpty()) Text(DesktopAccountText.Unconfirmed.text(language))
    }
    owner.state.statistics.forEach { value -> key(value.origin) {
        AccountCard(if (value.origin == DesktopStatisticsOrigin.Account) DesktopAccountText.AccountStatistics
            else DesktopAccountText.PortableStatistics, language) {
            AccountStatus(value.condition, DesktopAccountIssue.None, language)
            AccountDecisionValue(DesktopAccountText.Listens, value.listens ?: DesktopAccountText.Unconfirmed.text(language), language)
            AccountDecisionValue(DesktopAccountText.PlayedTime, value.playedTime ?: DesktopAccountText.Unconfirmed.text(language), language)
            value.checkedThroughLabel?.let { AccountDecisionValue(DesktopAccountText.Through, it, language) }
            if (!value.complete) Text(DesktopAccountText.Partial.text(language))
            if (value.origin == DesktopStatisticsOrigin.Account) value.serverDetails?.let {
                DesktopServerStatisticsContent(it, language)
            }
        }
    } }
}

internal fun desktopCatalogKindText(kind: DesktopCatalogKind, language: DesktopLanguage): String {
    val pair = when (kind) {
        DesktopCatalogKind.Artist -> "Исполнители" to "Artists"
        DesktopCatalogKind.Album -> "Альбомы" to "Albums"
        DesktopCatalogKind.Recording -> "Записи" to "Recordings"
        DesktopCatalogKind.Release -> "Релизы каталога" to "Catalog releases"
        DesktopCatalogKind.Track -> "Треки" to "Tracks"
        DesktopCatalogKind.Playlist -> "Плейлисты" to "Playlists"
    }
    return if (language == DesktopLanguage.Russian) pair.first else pair.second
}
