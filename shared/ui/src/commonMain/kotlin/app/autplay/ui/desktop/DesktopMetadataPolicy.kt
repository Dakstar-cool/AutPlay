package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopMetadataPreview
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTrackFieldProvenance
import app.autplay.application.desktop.DesktopTrackTextOrigin
import app.autplay.application.desktop.validDesktopEmbeddedText

/** IDs are host-issued request/preview IDs, except onInspect which takes a track ID. */
public class DesktopMetadataCallbacks(
    public val onInspect: (String) -> Unit = {},
    public val onCancel: (String) -> Unit = {},
    public val onApply: (String) -> Unit = {},
    public val onDismiss: (String) -> Unit = {},
)

internal fun desktopMetadataCanInspect(
    input: DesktopTrackInput,
    view: DesktopTrackView,
    state: DesktopMetadataState,
    reopenRequired: Boolean,
): Boolean = !reopenRequired && !state.busy && !state.applying && input.library != null &&
    input.isSelectionCurrent() && view.input == input && input.selectedId != null &&
    view.selectedTrack?.id == input.selectedId

/** Captured only by an explicit read; reopening the same track cannot revive this selection. */
internal class DesktopMetadataTarget(private val input: DesktopTrackInput, private val inspectedTrack: DesktopTrack) {
    fun isCurrent(current: DesktopTrackInput, view: DesktopTrackView): Boolean =
        input.isSelectionCurrent() && current.isSelectionCurrent() &&
            current.selectedId == input.selectedId && current.selectionRevision == input.selectionRevision &&
            current.library != null && view.input == current && view.selectedTrack?.id == input.selectedId

    fun canApply(current: DesktopTrackInput, view: DesktopTrackView, state: DesktopMetadataState,
        preview: DesktopMetadataPreview, reopenRequired: Boolean): Boolean =
        !reopenRequired && !state.busy && !state.applying && state.errorCode == null && state.preview === preview &&
            state.requestId == preview.id && state.trackId == preview.baseline.trackId && preview.canApply &&
            isCurrent(current, view) && desktopMetadataBaselineMatches(preview, inspectedTrack) &&
            desktopMetadataBaselineMatches(preview, view.selectedTrack)
}

internal fun desktopMetadataBaselineMatches(preview: DesktopMetadataPreview, track: DesktopTrack?): Boolean =
    track != null && preview.baseline.let { baseline ->
        track.id == baseline.trackId && track.title == baseline.title && track.artist == baseline.artist &&
            track.sha256 == baseline.sha256 && track.byteSize == baseline.byteSize
    }

internal fun desktopMetadataCancelId(state: DesktopMetadataState): String? =
    state.requestId?.takeIf { state.busy && !state.applying }

internal fun desktopMetadataDismissId(state: DesktopMetadataState): String? =
    (state.preview?.id ?: state.requestId)?.takeIf { !state.busy && !state.applying }

/** Embedded values are plain bounded text. Invalid legacy/foreign values never reach rendering. */
internal fun desktopMetadataSafeText(value: String?, language: DesktopLanguage): String =
    value?.takeIf { validDesktopEmbeddedText(it, allowBlank = true) }
        ?: DesktopMetadataText.Missing.label(language)

internal fun desktopMetadataStatus(status: String?, language: DesktopLanguage): String = when (status) {
    "AVAILABLE" -> DesktopMetadataText.Available
    "NO_TAGS" -> DesktopMetadataText.NoTags
    "UNSUPPORTED" -> DesktopMetadataText.Unsupported
    "INVALID" -> DesktopMetadataText.Invalid
    else -> DesktopMetadataText.Unavailable
}.label(language)

/** Fixed display copy only, also shared with the desktop host's metadata acceptance composite. */
internal fun desktopMetadataFormat(format: String?, language: DesktopLanguage): String = when (format) {
    "ID3V23" -> "ID3v2.3"
    "ID3V24" -> "ID3v2.4"
    "FLAC" -> "FLAC / Vorbis Comments"
    "MP4" -> DesktopMetadataText.Mp4Format.label(language)
    else -> DesktopMetadataText.UnknownFormat.label(language)
}

/** Only transactionally loaded evidence supplies an origin; labels and filenames supply none. */
internal fun desktopMetadataProvenance(provenance: DesktopTrackFieldProvenance?, language: DesktopLanguage): String =
    when (provenance?.origin) {
        DesktopTrackTextOrigin.USER -> DesktopMetadataText.UserOrigin.label(language)
        DesktopTrackTextOrigin.EMBEDDED -> "${DesktopMetadataText.EmbeddedOrigin.label(language)}: " +
            desktopMetadataFormat(provenance?.format?.name, language)
        else -> DesktopMetadataText.UnknownOrigin.label(language)
    }

internal enum class DesktopMetadataText(val russian: String, val english: String) {
    Inspect("Прочитать встроенные теги", "Read embedded tags"),
    Preview("Встроенные теги", "Embedded tags"),
    Reading("Чтение тегов управляемой копии…", "Reading tags from the managed copy…"),
    Applying("Сохранение подписей…", "Saving labels…"),
    Cancel("Отменить чтение", "Cancel reading"),
    Dismiss("Закрыть предпросмотр", "Dismiss preview"),
    Apply("Применить название и исполнителя", "Apply title and artist"),
    Current("Сохранённые подписи на момент чтения", "Stored labels at inspection"),
    Embedded("Исходный текст встроенных тегов", "Raw embedded tag text"),
    Proposed("Подписи после применения", "Labels after applying"),
    Title("Название", "Title"),
    Artist("Исполнитель", "Artist"),
    TitleOrigin("Происхождение названия", "Title origin"),
    ArtistOrigin("Происхождение исполнителя", "Artist origin"),
    UnknownOrigin("Неизвестно: подтверждённых локальных сведений нет", "Unknown: no confirmed local evidence"),
    UserOrigin("Явная правка пользователя", "Explicit user edit"),
    EmbeddedOrigin("Встроенные теги", "Embedded tags"),
    ProvenanceHint("Происхождение подписей показано в предпросмотре после явного чтения. Оно не подтверждает личность исполнителя или право доступа к источнику.", "Inspect tags to preview stored label origins. An origin does not establish artist identity or source access permission."),
    Album("Альбом (только сведения)", "Album (information only)"),
    Format("Формат источника", "Source format"),
    Mp4Format("MP4/M4A / список встроенных тегов", "MP4/M4A / embedded item list"),
    UnknownFormat("Неизвестный формат источника", "Unknown source format"),
    Missing("Нет доступного значения", "No available value"),
    Available("Теги доступны", "Tags available"),
    NoTags("Поддерживаемые теги не найдены", "No supported tags found"),
    Unsupported("Этот формат или вариант тегов не поддерживается", "This tag format or variant is unsupported"),
    Invalid("Некорректные теги или превышен допустимый размер", "Invalid tags or metadata exceeds limits"),
    Unavailable("Теги недоступны", "Tags unavailable"),
    Failed("Операция не подтверждена. Проверьте текущие подписи и явно повторите чтение.", "Operation unconfirmed. Review current labels and explicitly read again."),
    Changed("Выбор, исходные подписи или ревизия метаданных изменились. Для применения снова откройте сведения о нужном треке и прочитайте теги.", "Selection, original labels or metadata revision changed. Reopen details for the intended track and read again before applying."),
    Hint("Применение меняет только локальные название и исполнителя. Альбом не создаёт новую сущность; файл не изменяется.", "Apply changes only local title and artist. Album does not create an identity; the file is unchanged."),
    Cover("Обложка из встроенных тегов", "Cover from embedded tags"),
    NoCover("Обложка недоступна в кэше этого запуска", "Cover unavailable in this process cache"),
    CoverHint("Обложка появляется после явного чтения. Кэш временный; после перезапуска или вытеснения снова показана заглушка.", "Artwork appears after an explicit read. The cache is temporary; restart or eviction restores the placeholder."),
    Reopen("Для новых чтений и применения перезапустите AutPlay. Предпросмотр можно закрыть.", "Restart AutPlay before new reads or applying. The preview can be dismissed."),
    ;

    fun label(language: DesktopLanguage): String = when (language) {
        DesktopLanguage.Russian -> russian
        DesktopLanguage.English -> english
    }
}
