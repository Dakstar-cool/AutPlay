package app.autplay.ui.desktop

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.NonSkippableComposable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.DesktopLocalStatisticsState

public class DesktopLocalStatisticsCallbacks(
    val onRefresh: (DesktopLocalStatisticsState) -> Unit = {},
    val onRetry: (DesktopLocalStatisticsState) -> Unit = {},
    val isCurrent: (DesktopLocalStatisticsState) -> Boolean = { false },
)

internal class DesktopLocalStatisticsInput(
    val state: DesktopLocalStatisticsState,
    val owner: DesktopLocalStatisticsCallbacks,
    val blocked: Boolean,
) {
    fun matches(other: DesktopLocalStatisticsInput): Boolean = state === other.state &&
        owner === other.owner && blocked == other.blocked
    fun available(): Boolean = !blocked && !state.loading
}

/** A-B-A and disposed compositions cannot revive retained read callbacks. */
internal class DesktopLocalStatisticsLifetime {
    private var previous: DesktopLocalStatisticsInput? = null
    private var revision: Long = 0
    private var active: Boolean = true
    fun observe(input: DesktopLocalStatisticsInput): Long {
        if (previous?.matches(input) != true) { previous = input; revision += 1 }
        return revision
    }
    fun matches(captured: Long): Boolean = active && captured == revision
    fun retire(): Unit { active = false }
}

internal fun desktopLocalStatisticsGuard(lifetime: DesktopLocalStatisticsLifetime, revision: Long,
    captured: DesktopLocalStatisticsInput, latest: () -> DesktopLocalStatisticsInput,
    navigation: () -> Boolean): () -> Boolean = {
    lifetime.matches(revision) && navigation() && captured.matches(latest()) && captured.available() &&
        captured.owner.isCurrent(captured.state)
}

internal fun desktopLocalStatisticsStatus(state: DesktopLocalStatisticsState, blocked: Boolean): DesktopStatisticsText? = when {
    blocked -> null
    state.loading -> DesktopStatisticsText.Loading
    state.errorCode != null -> if (state.snapshot == null) DesktopStatisticsText.Failure else DesktopStatisticsText.RetainedFailure
    state.snapshot == null -> DesktopStatisticsText.NotLoaded
    state.snapshot?.listenedMs == 0L -> DesktopStatisticsText.Empty
    else -> null
}

internal enum class DesktopStatisticsText(private val ru: String, private val en: String) {
    Title("Статистика на этом Mac", "Statistics on this Mac"),
    Scope("Вся сохранённая локальная история. Данные других устройств и аккаунта сюда не входят.",
        "All saved local history. Other devices and account statistics are outside this view."),
    Snapshot("Срез обновляется вручную. Активное прослушивание учитывается по последнему сохранению.",
        "Refresh this snapshot manually. Active playback counts through its last saved checkpoint."),
    Refresh("Обновить статистику", "Refresh statistics"),
    Retry("Повторить срез", "Retry snapshot"),
    Loading("Загрузка статистики…", "Loading statistics…"),
    NotLoaded("Нажмите «Обновить статистику», чтобы получить срез.", "Choose Refresh statistics to load a snapshot."),
    Empty("В этом срезе ещё нет сохранённого времени прослушивания.", "This snapshot has no saved listening time yet."),
    Failure("Статистика недоступна. Повторите обновление.", "Statistics are unavailable. Retry refreshing."),
    RetainedFailure("Обновление не удалось. Показан предыдущий срез.", "Refresh failed. The previous snapshot is shown."),
    Total("Время прослушивания", "Listening time"),
    Sessions("Прослушиваний", "Listening sessions"),
    Tracks("Топ треков", "Top tracks"),
    Artists("Топ по именам исполнителей", "Top artist labels"),
    ArtistHint("Группы по сохранённым именам; одинаковые имена могут принадлежать разным исполнителям.",
        "Grouped by saved names; the same name may belong to different artists."),
    NoArtists("В этом срезе нет имён исполнителей.", "This snapshot has no artist labels."),
    Genres("Топ жанров пока недоступен.", "Genre rankings are currently unavailable."),
    Hours("ч", "h"), Minutes("мин", "min");

    fun label(language: DesktopLanguage): String = if (language == DesktopLanguage.Russian) ru else en
}

internal fun desktopStatisticsTime(playedMs: Long, language: DesktopLanguage): String {
    val totalMinutes = playedMs / 60_000L
    return "${totalMinutes / 60} ${DesktopStatisticsText.Hours.label(language)} ${totalMinutes % 60} ${DesktopStatisticsText.Minutes.label(language)}"
}

@Composable
@NonSkippableComposable
internal fun DesktopLocalStatisticsControls(state: DesktopLocalStatisticsState, callbacks: DesktopLocalStatisticsCallbacks,
    language: DesktopLanguage, blocked: Boolean, isNavigationCurrent: () -> Boolean) {
    val input = DesktopLocalStatisticsInput(state, callbacks, blocked)
    val current by rememberDesktopBrowseReference(input)
    val lifetime = remember { DesktopLocalStatisticsLifetime() }
    val revision = lifetime.observe(input)
    DisposableEffect(lifetime) { onDispose { lifetime.retire() } }
    val valid = desktopLocalStatisticsGuard(lifetime, revision, input, { current }, isNavigationCurrent)
    Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
        Column(Modifier.fillMaxWidth().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(DesktopStatisticsText.Title.label(language), style = MaterialTheme.typography.titleMedium)
            Text(DesktopStatisticsText.Scope.label(language), style = MaterialTheme.typography.bodySmall)
            Text(DesktopStatisticsText.Snapshot.label(language), style = MaterialTheme.typography.bodySmall)
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { if (valid()) input.owner.onRefresh(input.state) }, enabled = input.available()) {
                    Text(DesktopStatisticsText.Refresh.label(language))
                }
                if (state.errorCode == "desktop_local_statistics_load_failed") {
                    OutlinedButton(onClick = { if (valid()) input.owner.onRetry(input.state) }, enabled = input.available()) {
                        Text(DesktopStatisticsText.Retry.label(language))
                    }
                }
            }
            desktopLocalStatisticsStatus(state, blocked)?.let {
                Text(it.label(language), modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
            }
            state.snapshot?.takeUnless { blocked }?.let { snapshot ->
                Text("${DesktopStatisticsText.Total.label(language)}: ${desktopStatisticsTime(snapshot.listenedMs, language)}")
                Text("${DesktopStatisticsText.Sessions.label(language)}: ${snapshot.playSessionCount}")
                if (snapshot.topTracks.isNotEmpty()) Text(DesktopStatisticsText.Tracks.label(language), style = MaterialTheme.typography.titleSmall)
                snapshot.topTracks.take(5).forEachIndexed { index, track ->
                    Text("${index + 1}. ${track.title}", maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text("${track.artist} · ${desktopStatisticsTime(track.listenedMs, language)} · ${track.playSessionCount}",
                        maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodySmall)
                }
                Text(DesktopStatisticsText.Artists.label(language), style = MaterialTheme.typography.titleSmall)
                Text(DesktopStatisticsText.ArtistHint.label(language), style = MaterialTheme.typography.bodySmall)
                if (snapshot.topArtistLabels.isEmpty()) Text(DesktopStatisticsText.NoArtists.label(language))
                snapshot.topArtistLabels.take(5).forEachIndexed { index, artist ->
                    Text("${index + 1}. ${artist.artistLabel} · ${desktopStatisticsTime(artist.listenedMs, language)} · ${artist.playSessionCount}",
                        maxLines = 2, overflow = TextOverflow.Ellipsis)
                }
                Text(DesktopStatisticsText.Genres.label(language), style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
