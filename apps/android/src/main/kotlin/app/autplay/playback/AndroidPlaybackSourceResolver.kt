package app.autplay.playback

import android.content.Context
import android.net.Uri
import androidx.core.net.toUri
import androidx.media3.common.util.UnstableApi
import app.autplay.application.importing.ContentUriInspector
import app.autplay.application.importing.ContentUriStatus
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.download.MediaDownloadComponents
import app.autplay.domain.LocalId
import kotlinx.coroutines.flow.first

enum class SelectedAudioSource { LOCAL_URI, MEDIA3_DOWNLOAD, VAULT_STREAM }

data class ResolvedPlaybackSource(
    val trackRefId: LocalId,
    val runtimeUri: Uri,
    val source: SelectedAudioSource,
    val unavailableLocalReason: PlaybackUnavailableReason? = null,
)

sealed interface AndroidSourceResolution {
    data class Available(val value: ResolvedPlaybackSource) : AndroidSourceResolution
    data class Unavailable(val reason: PlaybackUnavailableReason) : AndroidSourceResolution
}

/** Fresh LOCAL -> completed Media3 download -> authorized-at-open Vault selection. */
@UnstableApi
class AndroidPlaybackSourceResolver(
    context: Context,
    private val database: AutPlayDatabase,
    private val settings: NonSecretSettingsStore,
) {
    private val applicationContext = context.applicationContext
    private val inspector = ContentUriInspector(applicationContext.contentResolver)

    suspend fun resolve(trackRefId: LocalId, nowMs: Long): AndroidSourceResolution {
        val localStates = database.localAudioDao().statesForPlayback(trackRefId.value, MAX_SOURCES)
        var localFailure: PlaybackUnavailableReason? = null
        for (state in localStates) {
            val inspection = inspector.inspect(state.contentUri)
            if (inspection.status == ContentUriStatus.AVAILABLE) {
                markInspected(state, "AVAILABLE", nowMs)
                return AndroidSourceResolution.Available(
                    ResolvedPlaybackSource(trackRefId, state.contentUri.toUri(), SelectedAudioSource.LOCAL_URI),
                )
            }
            val status = when (inspection.status) {
                ContentUriStatus.MISSING -> "MISSING"
                ContentUriStatus.PERMISSION_REVOKED -> "PERMISSION_REVOKED"
                ContentUriStatus.INVALID -> "MISSING"
                ContentUriStatus.AVAILABLE -> error("handled")
            }
            markInspected(state, status, nowMs)
            localFailure = when (inspection.status) {
                ContentUriStatus.MISSING, ContentUriStatus.INVALID -> PlaybackUnavailableReason.LOCAL_MISSING_AND_VAULT_UNAVAILABLE
                ContentUriStatus.PERMISSION_REVOKED -> PlaybackUnavailableReason.LOCAL_PERMISSION_REVOKED_AND_VAULT_UNAVAILABLE
                ContentUriStatus.AVAILABLE -> error("handled")
            }
        }

        val intents = database.localAudioDao().downloadIntentsForTrack(trackRefId.value, MAX_SOURCES)
        val components = MediaDownloadComponents.get(applicationContext)
        intents.firstNotNullOfOrNull { intent ->
            val id = intent.media3DownloadId ?: return@firstNotNullOfOrNull null
            components.downloadManager.downloadIndex.getDownload(id)
                ?.takeIf { it.state == androidx.media3.exoplayer.offline.Download.STATE_COMPLETED }
        }?.let { download ->
            return AndroidSourceResolution.Available(
                ResolvedPlaybackSource(
                    trackRefId,
                    download.request.uri,
                    SelectedAudioSource.MEDIA3_DOWNLOAD,
                    localFailure,
                ),
            )
        }

        val variantId = (localStates.mapNotNull { it.serverAudioVariantId } +
            intents.mapNotNull { it.serverAudioVariantId }).firstOrNull()
            ?: return AndroidSourceResolution.Unavailable(localFailure ?: PlaybackUnavailableReason.NO_AUDIO_SOURCE)
        val active = settings.settings.first()
        val profileId = active.activeServerProfileId?.value
            ?: return AndroidSourceResolution.Unavailable(PlaybackUnavailableReason.VAULT_AUTHORIZATION_UNAVAILABLE)
        if (active.serverBaseUrl == null) {
            return AndroidSourceResolution.Unavailable(PlaybackUnavailableReason.VAULT_AUTHORIZATION_UNAVAILABLE)
        }
        val stableUri = Uri.Builder()
            .scheme(VAULT_SCHEME)
            .authority(profileId)
            .appendPath("audio-variants")
            .appendPath(variantId)
            .build()
        return AndroidSourceResolution.Available(
            ResolvedPlaybackSource(trackRefId, stableUri, SelectedAudioSource.VAULT_STREAM, localFailure),
        )
    }

    private suspend fun markInspected(state: LocalAudioStateEntity, status: String, nowMs: Long) {
        if (state.status != status || state.lastVerifiedAtMs != nowMs) {
            database.localAudioDao().upsertState(
                state.copy(status = status, lastVerifiedAtMs = nowMs, updatedAtMs = nowMs),
            )
        }
    }

    companion object {
        const val VAULT_SCHEME = "autplay-vault"
        private const val MAX_SOURCES = 32
    }
}
