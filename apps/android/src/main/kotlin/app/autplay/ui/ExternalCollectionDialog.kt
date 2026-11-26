package app.autplay.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import app.autplay.AutPlayRuntime
import app.autplay.R
import app.autplay.application.library.ExternalCollection
import app.autplay.application.sync.ClientEventBinding
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkSubject
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

@Composable
internal fun ExternalCollectionDialog(binding: ClientEventBinding, initialLink: String, initialFavorites: Boolean,
                                      onFavoritesSaved: (() -> Unit)? = null, onClose: () -> Unit) = key(binding, initialLink, initialFavorites) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    suspend fun service() = AutPlayRuntime.serverFeatures(context, binding)
    var link by rememberSaveable { mutableStateOf(initialLink) }
    var favorites by rememberSaveable { mutableStateOf(initialFavorites) }
    var newCopy by rememberSaveable { mutableStateOf(false) }
    var operation by rememberSaveable { mutableStateOf<String?>(null) }
    var preview by remember { mutableStateOf<ExternalCollection?>(null) }
    var recent by remember { mutableStateOf(emptyList<Pair<String, String>>()) }
    var registered by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableIntStateOf(0) }
    fun load(block: suspend () -> ExternalCollection) {
        if (busy) return
        busy = true
        error = 0
        scope.launch {
            try {
                var page = block()
                preview = page
                while (page.state == "READING") {
                    page = service().resumeCollection(page.operation)
                    preview = page
                }
                if (page.state == "SAVED" && registered != page.operation) {
                    page.job?.let { id ->
                        app.autplay.application.server.ServerFeatureStateRepository(AutPlayRuntime.database(context))
                            .recordImportReport(binding.serverProfileId, service().importReport(id), System.currentTimeMillis())
                        app.autplay.work.RemoteImportWorkScheduler.enqueue(context, id)
                    }
                    AutPlayRuntime.syncScheduler(context).enqueue(DeferredWorkRequest(DeferredWorkKind.SYNC,
                        DeferredWorkSubject.Device(binding.deviceId), binding.serverProfileId))
                    registered = page.operation
                    if (page.action == "FAVORITES") onFavoritesSaved?.invoke()
                }
            } catch (e: CancellationException) { throw e }
            catch (e: app.autplay.application.server.ServerFeatureHttpException) {
                error = when (e.errorCode?.removePrefix("external_import_")) {
                    "soundcloud_not_configured", "spotify_not_configured" -> R.string.ext_not_configured
                    "choose_favorites" -> R.string.ext_choose_favorites
                    "source_access_required", "playlist_ownership_required" -> R.string.ext_access
                    "rate_limited" -> R.string.ext_rate
                    "catalog_album_not_found" -> R.string.ext_no_album
                    else -> R.string.ext_failed
                }
                if (e.errorCode in setOf("external_import_source_access_required", "external_import_stale_preview")) preview = null
            }
            catch (_: Exception) { error = R.string.ext_failed }
            finally { busy = false }
        }
    }
    LaunchedEffect(binding) {
        try { recent = service().recentCollections() }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { /* The link field can still be used. */ }
    }
    val page = preview
    AutPlayDialog(onDismissRequest = onClose, title = { Text(page?.name ?: stringResource(
        if (favorites) R.string.ext_favorites else R.string.ext_import)) }, text = {
        Column(Modifier.verticalScroll(rememberScrollState())) {
            if (page == null) {
                OutlinedTextField(link, { link = it.take(2048); operation = null }, singleLine = true,
                    label = { Text(stringResource(R.string.ext_link)) })
                if (onFavoritesSaved == null) Row { Checkbox(favorites, { favorites = it; operation = null }, enabled = !busy)
                    Text(stringResource(R.string.ext_favorites)) }
                if (!favorites) Row { Checkbox(newCopy, { newCopy = it; operation = null }, enabled = !busy)
                    Text(stringResource(R.string.ext_new_copy)) }
                if (recent.isNotEmpty()) Text(stringResource(R.string.ext_recent))
                recent.take(8).forEach { (id, name) ->
                    TextButton(enabled = !busy, onClick = { operation = id; load { service().resumeCollection(id) } }) { Text(name) }
                }
            } else {
                Text(stringResource(when (page.action) {
                    "FAVORITES" -> R.string.ext_favorites
                    "ALBUM" -> R.string.ext_album
                    else -> R.string.ext_playlist
                }))
                Text(stringResource(R.string.ext_counts, page.read, page.expected?.toString() ?: "?", page.identified, page.playable))
                page.likes?.let { Text(stringResource(R.string.ext_marks, it)) }
                page.sets?.let { Text(stringResource(R.string.ext_sets, it, page.expectedSets?.toString() ?: "?")) }
                if (!page.complete && page.state in setOf("PREVIEW", "SESSION_PREVIEW")) Text(stringResource(R.string.ext_partial))
                if (page.state == "SESSION_PREVIEW") Text(stringResource(R.string.ext_session))
                page.setLinks.forEach { (url, name) ->
                    TextButton(enabled = !busy, onClick = {
                        val id = UUID.randomUUID().toString()
                        load { service().startCollection(url, false, id, false) }
                    }) { Text(name) }
                }
                page.authorization?.let { auth -> TextButton(onClick = {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(auth)))
                }) { Text(stringResource(R.string.ext_connect)) } }
                if (page.state == "SAVED") Text(stringResource(R.string.ext_saved))
                page.missingOccurrences?.takeIf { it > 0 }?.let { Text(stringResource(R.string.ext_missing, it)) }
                if (page.state == "CANCELLED") Text(stringResource(R.string.ext_cancelled))
                page.tracks.forEach { Text(it.ifEmpty { stringResource(R.string.ext_unknown) }) }
            }
            if (page != null) {
                Row {
                    if (page.offset > 0) TextButton(enabled = !busy, onClick = {
                        load { service().collectionPage(page.operation, (page.offset - 50).coerceAtLeast(0)) }
                    }) { Text(stringResource(R.string.ext_previous)) }
                    page.nextOffset?.let { offset ->
                        TextButton(enabled = !busy, onClick = { load { service().collectionPage(page.operation, offset) } }) {
                            Text(stringResource(R.string.ext_more))
                        }
                    }
                }
                if (page.state !in setOf("SAVED", "CANCELLED")) TextButton(enabled = !busy, onClick = {
                    load { service().cancelCollection(page.operation) }
                }) { Text(stringResource(R.string.ext_cancel)) }
            }
            if (error != 0) Text(stringResource(error))
            if (busy) LinearProgressIndicator()
        }
    }, confirmButton = {
        when {
            page == null -> TextButton(enabled = !busy && ExternalCollection.isProviderLink(link), onClick = {
                val id = operation ?: UUID.randomUUID().toString().also { operation = it }
                load { service().startCollection(link.trim(), favorites, id, newCopy && !favorites) }
            }) { Text(stringResource(R.string.playlist_preview)) }
            page.state == "PREVIEW" || page.selectionKey != null -> TextButton(enabled = !busy, onClick = { load { service().confirmCollection(page) } }) {
                if (page.selectionKey != null) Text(stringResource(R.string.ext_catalog_selection, page.playable))
                else Text(stringResource(if (page.action == "FAVORITES") R.string.ext_favorites else R.string.ext_save))
            }
            page.state in setOf("AUTH_REQUIRED", "READING", "PARTIAL") -> TextButton(enabled = !busy, onClick = {
                load { service().resumeCollection(page.operation) }
            }) { Text(stringResource(R.string.ext_continue)) }
        }
    }, dismissButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.ext_close)) } })
}
