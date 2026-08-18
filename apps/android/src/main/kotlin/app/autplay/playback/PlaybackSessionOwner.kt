package app.autplay.playback

import android.content.Context
import android.content.Intent
import androidx.media3.common.util.UnstableApi
import app.autplay.domain.LocalId
import app.autplay.application.wave.WavePlaybackExecutor
import app.autplay.application.wave.WavePreparation
import app.autplay.domain.wave.WaveAvailability
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Application boundary for playback ownership. P08 supplies the Media3 service implementation;
 * callers must not construct players, media sources, or download state directly.
 */
interface PlaybackSessionOwner {
    suspend fun dispatch(command: PlaybackCommand)
}

/** Sends stable-ID commands to the MediaSessionService; the Activity never owns a player. */
@UnstableApi
class ServicePlaybackSessionOwner(context: Context) : PlaybackSessionOwner, WavePlaybackExecutor {
    private val applicationContext = context.applicationContext

    override suspend fun dispatch(command: PlaybackCommand) {
        val intent = Intent(applicationContext, AutPlayPlaybackService::class.java)
            .putExtra(PlaybackCommandAuthorization.EXTRA_PROCESS_TOKEN, PlaybackCommandAuthorization.processToken)
        when (command) {
            is PlaybackCommand.StartQueue -> intent.setAction(AutPlayPlaybackService.ACTION_START_QUEUE)
                .putExtra(AutPlayPlaybackService.EXTRA_QUEUE_SNAPSHOT_ID, command.queueSnapshotId.value)
                .also { command.queueEntryId?.let { id ->
                    // The persisted snapshot owns the current entry; explicit entry selection is
                    // intentionally deferred until it is applied transactionally by the repository.
                    require(id.value.isNotBlank())
                } }
            is PlaybackCommand.PrepareQueue -> intent
                .setAction(AutPlayPlaybackService.ACTION_PREPARE_QUEUE)
                .putExtra(
                    AutPlayPlaybackService.EXTRA_QUEUE_SNAPSHOT_ID,
                    command.queueSnapshotId.value,
                )
            PlaybackCommand.Resume -> intent.action = AutPlayPlaybackService.ACTION_RESUME
            PlaybackCommand.Pause -> intent.action = AutPlayPlaybackService.ACTION_PAUSE
            PlaybackCommand.Stop -> intent.action = AutPlayPlaybackService.ACTION_STOP
            is PlaybackCommand.SeekTo -> intent.setAction(AutPlayPlaybackService.ACTION_SEEK)
                .putExtra(AutPlayPlaybackService.EXTRA_POSITION_MS, command.positionMs)
            is PlaybackCommand.SetShuffleEnabled -> intent.setAction(AutPlayPlaybackService.ACTION_SET_SHUFFLE)
                .putExtra(AutPlayPlaybackService.EXTRA_SHUFFLE_ENABLED, command.enabled)
            is PlaybackCommand.SetRepeatMode -> intent.setAction(AutPlayPlaybackService.ACTION_SET_REPEAT)
                .putExtra(AutPlayPlaybackService.EXTRA_REPEAT_MODE, command.mode)
            is PlaybackCommand.ScheduledPlay -> intent.setAction(AutPlayPlaybackService.ACTION_SCHEDULED_PLAY)
                .putExtra(AutPlayPlaybackService.EXTRA_SCHEDULED_AT_MS, command.atMs)
            is PlaybackCommand.SetSpeed -> intent.setAction(AutPlayPlaybackService.ACTION_SET_SPEED)
                .putExtra(AutPlayPlaybackService.EXTRA_SPEED, command.speed)
        }
        applicationContext.startService(intent)
    }

    override suspend fun schedulePreparedPlayAtElapsedRealtime(targetElapsedRealtimeMs: Long) =
        dispatch(PlaybackCommand.ScheduledPlay(targetElapsedRealtimeMs))

    override suspend fun prepareWaveQueue(
        queueSnapshotId: String,
        queueEntryId: String,
    ): WavePreparation {
        dispatch(PlaybackCommand.PrepareQueue(LocalId(queueSnapshotId)))
        val state = withTimeoutOrNull(8_000) {
            PlaybackRuntimeState.state
                .filter { value ->
                    value.queueEntryId == queueEntryId &&
                        (value.isPrepared || value.unavailableReason != null)
                }
                .first()
        }
        val source = when (state?.source) {
            SelectedAudioSource.LOCAL_URI.name -> WaveAvailability.LOCAL_READABLE
            SelectedAudioSource.MEDIA3_DOWNLOAD.name -> WaveAvailability.DOWNLOADED
            SelectedAudioSource.VAULT_STREAM.name -> WaveAvailability.VAULT_STREAMABLE
            else -> WaveAvailability.UNAVAILABLE
        }
        return WavePreparation(
            ready = state?.isPrepared == true && state.unavailableReason == null,
            source = source,
            bufferedMs = state?.bufferedMs ?: 0,
        )
    }

    override suspend fun pause() = dispatch(PlaybackCommand.Pause)
}

/** Process-local capability prevents other apps from invoking the exported session service actions. */
internal object PlaybackCommandAuthorization {
    const val EXTRA_PROCESS_TOKEN = "app.autplay.playback.PROCESS_COMMAND_TOKEN"
    val processToken: String = UUID.randomUUID().toString()
}

data class PlaybackUiState(
    val queueEntryId: String? = null,
    val localUserTrackRefId: String? = null,
    val title: String? = null,
    val source: String? = null,
    val unavailableReason: String? = null,
    val positionMs: Long = 0,
    val isPlaying: Boolean = false,
    val isPrepared: Boolean = false,
    val bufferedMs: Long = 0,
    val shuffleEnabled: Boolean = false,
    val repeatMode: String = "OFF",
)

/** Process-local projection for Compose; Media3/Room remain the execution and persistence owners. */
object PlaybackRuntimeState {
    private val mutable = MutableStateFlow(PlaybackUiState())
    val state: StateFlow<PlaybackUiState> = mutable.asStateFlow()
    internal fun publish(value: PlaybackUiState) { mutable.value = value }
}

/** Commands describe user intent using durable local identities, never paths or media URLs. */
sealed interface PlaybackCommand {
    data class StartQueue(
        val queueSnapshotId: LocalId,
        val queueEntryId: LocalId? = null,
    ) : PlaybackCommand

    data class PrepareQueue(val queueSnapshotId: LocalId) : PlaybackCommand

    data object Resume : PlaybackCommand

    data object Pause : PlaybackCommand

    data object Stop : PlaybackCommand

    data class SeekTo(val positionMs: Long) : PlaybackCommand {
        init {
            require(positionMs >= 0) { "Playback position must not be negative." }
        }
    }

    data class SetShuffleEnabled(val enabled: Boolean) : PlaybackCommand

    data class SetRepeatMode(val mode: String) : PlaybackCommand {
        init { require(mode in setOf("OFF", "ONE", "ALL")) }
    }

    /** Wave-only execution action; caller must cancel it when room authority is lost. */
    data class ScheduledPlay(val atMs: Long) : PlaybackCommand
    data class SetSpeed(val speed: Float) : PlaybackCommand { init { require(speed in .98f..1.02f || speed == 1f) } }
}
