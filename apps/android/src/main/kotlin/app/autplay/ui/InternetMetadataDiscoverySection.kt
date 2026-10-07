package app.autplay.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.server.*
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.SessionRequiredException
import app.autplay.data.settings.applicationNonSecretSettingsStore
import java.time.Instant
import kotlinx.coroutines.CancellationException

private data class DiscoveryScope(val query: String, val kind: LibrarySearchKind, val request: Int,
    val profile: String?, val user: String?, val device: String?)
private data class SelectedCatalogueTrack(val card: InternetMetadataDiscoveryCard, val attempt: Int)
private data class AdmittedCatalogueLookup(val query: String, val contextId: String, val request: Int)

/** Separate catalogue navigation. Only a user-selected track starts the existing source search. */
@Composable
internal fun InternetMetadataDiscoverySection(query: String, kind: LibrarySearchKind, request: Int,
    session: InternetMusicUiSession? = null) {
    val context = LocalContext.current
    val settings by remember { applicationNonSecretSettingsStore(context).settings }.collectAsState(initial = null)
    val profile = if (session != null) session.binding?.serverProfileId else settings?.activeServerProfileId
    val user = if (session != null) session.binding?.userId else settings?.activeUserId
    val device = if (session != null) session.binding?.deviceId else settings?.deviceId
    val scope = DiscoveryScope(query.trim().take(200), kind, request, profile?.value, user?.value, device?.value)
    val currentScope by rememberUpdatedState(scope)
    var target by remember(scope) { mutableStateOf<InternetMetadataBrowseTarget?>(null) }
    var offset by remember(scope) { mutableIntStateOf(0) }
    var retry by remember(scope) { mutableIntStateOf(0) }
    var page by remember(scope) { mutableStateOf<InternetMetadataDiscoveryPage?>(null) }
    var cards by remember(scope) { mutableStateOf(emptyList<InternetMetadataDiscoveryCard>()) }
    var loading by remember(scope) { mutableStateOf(false) }
    var pageError by remember(scope) { mutableStateOf<String?>(null) }
    var selected by remember(scope) { mutableStateOf<SelectedCatalogueTrack?>(null) }
    var selectionSequence by remember(scope) { mutableIntStateOf(0) }
    var contextLoading by remember(scope) { mutableStateOf(false) }
    var contextError by remember(scope) { mutableStateOf<String?>(null) }
    var admitted by remember(scope) { mutableStateOf<AdmittedCatalogueLookup?>(null) }

    LaunchedEffect(scope, target, offset, retry) {
        val expectedTarget = target
        val expectedOffset = offset
        loading = false
        pageError = null
        if (expectedOffset == 0) { cards = emptyList(); page = null }
        if (profile == null || user == null || device == null || request == 0 || scope.query.isBlank()) return@LaunchedEffect
        val discoveryKind = when (kind) {
            LibrarySearchKind.Artist -> InternetMetadataDiscoveryKind.ARTIST
            LibrarySearchKind.Album -> InternetMetadataDiscoveryKind.ALBUM
            else -> return@LaunchedEffect
        }
        loading = true
        try {
            val server = session?.client ?: AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile))
            val response = when (expectedTarget?.entity) {
                InternetMetadataDiscoveryEntity.ARTIST -> server.discoveryArtistTracks(expectedTarget.entityId, offset = expectedOffset)
                InternetMetadataDiscoveryEntity.RELEASE -> server.discoveryReleaseTracks(expectedTarget.entityId, offset = expectedOffset)
                else -> server.discoverMusic(scope.query, discoveryKind, offset = expectedOffset)
            }
            check(response.offset == expectedOffset && response.limit == 25) { "SERVER_RESPONSE_INVALID" }
            if (currentScope == scope && target == expectedTarget && offset == expectedOffset) {
                // Occurrences retain their release-track ID, including repeated recordings.
                cards = (if (expectedOffset == 0) response.items else cards + response.items).distinctBy { it.id }
                page = response
            }
        } catch (error: CancellationException) { throw error
        } catch (error: Exception) {
            if (currentScope == scope && target == expectedTarget && offset == expectedOffset) pageError = catalogueErrorCode(error)
        } finally {
            if (currentScope == scope && target == expectedTarget && offset == expectedOffset) loading = false
        }
    }

    LaunchedEffect(scope, target, selected) {
        val selection = selected
        val expectedTarget = target
        admitted = null; contextLoading = false; contextError = null
        if (selection == null || profile == null || user == null || device == null) return@LaunchedEffect
        val lookup = selection.card.trackLookup ?: return@LaunchedEffect
        val identities = MusicCatalogueContextRequest.fromCard(selection.card) ?: return@LaunchedEffect
        contextLoading = true
        try {
            val receipt = (session?.client ?: AutPlayRuntime.serverFeatures(context, ClientEventBinding(user, device, profile)))
                .createMusicCatalogueContext(identities)
            check(receipt.matches(identities)) { "SERVER_RESPONSE_INVALID" }
            val contextId = receipt.admissionContextId(Instant.now()) ?: throw IllegalStateException("CONTEXT_EXPIRED")
            if (currentScope == scope && target == expectedTarget && selected == selection) {
                admitted = AdmittedCatalogueLookup(lookup.searchQuery, contextId, selection.attempt)
            }
        } catch (error: CancellationException) { throw error
        } catch (error: Exception) {
            if (currentScope == scope && target == expectedTarget && selected == selection) contextError = catalogueErrorCode(error)
        } finally {
            if (currentScope == scope && target == expectedTarget && selected == selection) contextLoading = false
        }
    }

    Column(Modifier.fillMaxWidth().testTag("internet-metadata-discovery"), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.music_catalogue_title), style = MaterialTheme.typography.titleLarge)
        Text(stringResource(R.string.music_catalogue_metadata_only), style = MaterialTheme.typography.bodySmall)
        if (target != null) TextButton(modifier = Modifier.testTag("catalogue-back"), onClick = {
            target = null; offset = 0; cards = emptyList(); page = null; selected = null; admitted = null
        }) { Text(stringResource(R.string.music_catalogue_back)) }
        when {
            profile == null || user == null || device == null -> Text(stringResource(R.string.music_need_server))
            loading -> LinearProgressIndicator(Modifier.fillMaxWidth())
            pageError != null -> {
                CatalogueErrorText(pageError)
                TextButton(onClick = { retry++ }) { Text(stringResource(R.string.music_retry)) }
            }
            page != null && cards.isEmpty() -> Text(stringResource(R.string.music_internet_empty))
        }
        cards.forEach { card ->
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer,
                modifier = Modifier.fillMaxWidth().testTag("catalogue-card-${card.id}")) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text(card.title, style = MaterialTheme.typography.titleMedium)
                    card.artist?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                    listOfNotNull(card.releaseDate, card.country, card.disambiguation).takeIf { it.isNotEmpty() }
                        ?.let { Text(it.joinToString(" · "), style = MaterialTheme.typography.bodySmall) }
                    card.discNumber?.let { Text(stringResource(R.string.music_catalogue_disc, it), style = MaterialTheme.typography.bodySmall) }
                    card.trackNumber?.let { Text(stringResource(R.string.music_catalogue_track, it), style = MaterialTheme.typography.bodySmall) }
                    card.recordingTitle?.takeIf { it != card.title }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
                    card.browseTarget?.let { browse ->
                        TextButton(modifier = Modifier.testTag("catalogue-browse-${card.id}"), enabled = !loading, onClick = {
                            target = browse; offset = 0; cards = emptyList(); page = null; selected = null; admitted = null
                        }) { Text(stringResource(R.string.music_catalogue_browse)) }
                    }
                    if (card.trackLookup != null) TextButton(modifier = Modifier.testTag("catalogue-source-${card.id}"), enabled = !contextLoading, onClick = {
                        selectionSequence++; selected = SelectedCatalogueTrack(card, selectionSequence); admitted = null
                    }) { Text(stringResource(R.string.music_catalogue_find_source)) }
                }
            }
        }
        if (page?.truncated == true) Text(stringResource(R.string.music_catalogue_truncated))
        page?.nextOffset?.let { next ->
            TextButton(modifier = Modifier.testTag("catalogue-more"), enabled = !loading, onClick = { offset = next }) { Text(stringResource(R.string.music_catalogue_more)) }
        }
        if (contextLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
        if (contextError != null) {
            CatalogueErrorText(contextError)
            TextButton(modifier = Modifier.testTag("catalogue-context-retry"), onClick = { selected?.let { selectionSequence++; selected = it.copy(attempt = selectionSequence) } }) {
                Text(stringResource(R.string.music_retry))
            }
        }
        admitted?.let { lookup ->
            InternetMusicSearchSection(query = lookup.query, request = lookup.request,
                kind = LibrarySearchKind.Track, catalogueContextId = lookup.contextId, session = session,
                onCatalogueContextExpired = {
                    admitted = null
                    selected?.let { selectionSequence++; selected = it.copy(attempt = selectionSequence) }
                })
        }
    }
}

internal fun catalogueErrorCode(error: Exception): String = when (error) {
    is SessionRequiredException -> "SESSION_REQUIRED"
    is ServerFeatureHttpException -> error.errorCode ?: "UNAVAILABLE"
    else -> if (error.message == "CONTEXT_EXPIRED") "music_catalogue_context_expired" else "UNAVAILABLE"
}

@Composable
internal fun CatalogueErrorText(code: String?) {
    val message = when (code) {
        "SESSION_REQUIRED" -> R.string.music_need_server
        "music_catalogue_disabled", "music_discovery_disabled" -> R.string.music_catalogue_disabled
        "music_catalogue_busy", "music_discovery_busy" -> R.string.music_catalogue_busy
        "music_catalogue_unavailable", "music_discovery_unavailable" -> R.string.music_catalogue_unavailable
        "music_catalogue_context_expired" -> R.string.music_catalogue_expired
        "music_catalogue_context_not_found", "music_catalogue_entity_not_found" -> R.string.music_catalogue_unavailable_selection
        "music_operation_conflict" -> R.string.music_catalogue_conflict
        else -> R.string.music_internet_error
    }
    Text(stringResource(message), color = MaterialTheme.colorScheme.error)
}
