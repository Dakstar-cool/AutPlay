package app.autplay.ui.desktop

public enum class DesktopLanguage { Russian, English }

public enum class DesktopSection { Library, Playlists, Queue, History, Settings }

internal enum class DesktopText(public val russian: String, public val english: String) {
    LocalCollection("Локальная коллекция", "Local collection"),
    Library("Библиотека", "Library"),
    Playlists("Плейлисты", "Playlists"),
    Queue("Очередь", "Queue"),
    History("История", "History"),
    Settings("Настройки", "Settings"),
    LibrarySubtitle("Ваша музыка. На вашем устройстве.", "Your music. On your device."),
    EmptyLibrary("Здесь будет ваша музыка", "Your music belongs here"),
    EmptyLibraryBody(
        "Выберите файлы или папки. AutPlay сохранит управляемые копии; оригиналы останутся на месте.",
        "Choose files or folders. AutPlay keeps managed copies and preserves your originals.",
    ),
    Import("Добавить музыку", "Add music"),
    LocalOnly("Без подключения к серверу", "No server connected"),
    EmptyPlaylists("Пока нет плейлистов", "No playlists yet"),
    EmptyPlaylistsBody(
        "Создайте плейлист и добавляйте в него музыку из библиотеки.",
        "Create a playlist and add music from your library.",
    ),
    CreatePlaylist("Создать плейлист", "Create playlist"),
    SettingsSubtitle("Устройте AutPlay под себя.", "Make AutPlay feel like home."),
    Appearance("Оформление", "Appearance"),
    Light("Светлая", "Light"),
    Dark("Тёмная", "Dark"),
    Language("Язык интерфейса", "Interface language"),
    SessionSettings("Тема и язык действуют до закрытия приложения.", "Theme and language apply until the app closes."),
    NothingPlaying("Ничего не воспроизводится", "Nothing playing"),
    PlaybackUnavailable("Аудиодвижок недоступен", "Audio engine unavailable"),
    ControlsUnavailable("Управление сейчас недоступно", "Controls are currently unavailable"),
    Previous("Назад", "Previous"),
    Play("Слушать", "Play"),
    Pause("Пауза", "Pause"),
    Next("Далее", "Next"),
    Seek("Позиция воспроизведения", "Playback position"),
    Elapsed("Прошло", "Elapsed"),
    Duration("Длительность", "Duration"),
    Search("Поиск по названию и исполнителю", "Search title and artist"),
    Importing("Импорт…", "Importing…"),
    PlayAll("Слушать всё", "Play all"),
    Enqueue("В очередь", "Enqueue"),
    AddToPlaylist("В плейлист", "Add to playlist"),
    SelectPlaylist("Выберите плейлист", "Choose playlist"),
    PlaylistName("Название плейлиста", "Playlist name"),
    Remove("Удалить", "Remove"),
    EmptyQueue("Очередь пуста. Выберите музыку в библиотеке.", "Queue empty. Choose music in your library."),
    EmptyHistory("Здесь появятся ваши прослушивания.", "Your listening history will appear here."),
    Shuffle("Перемешать", "Shuffle"),
    RepeatOff("Повтор: выкл.", "Repeat: off"),
    RepeatOne("Повтор: трек", "Repeat: one"),
    RepeatAll("Повтор: всё", "Repeat: all"),
    NoResults("Ничего не найдено", "No results"),
    Imported("Добавлено", "Imported"),
    Duplicates("Уже в библиотеке", "Duplicates"),
    Failed("Не удалось", "Failed"),
    OperationFailed("Операция не выполнена. Попробуйте ещё раз.", "Operation failed. Try again."),
    SourceMissing("Файл недоступен. Запись сохранена в библиотеке; выберите другой трек.", "File unavailable. Library entry preserved; choose another track."),
    AudioError("Аудиодвижок не смог воспроизвести файл. Выберите другой трек или повторите попытку.", "Audio engine could not play the file. Choose another track or retry."),
    UnsupportedMode("Сохранённый режим очереди не поддерживается. Создайте новую очередь из библиотеки.", "Saved queue mode unsupported. Start a new queue from the library."),
    Played("Прослушано", "Listened"),
    ActiveSession("Текущая сессия", "Active session"),
    ListBound("Показаны первые 10 000 записей. Уточните поиск.", "Showing the first 10,000 entries. Refine your search."),
}

internal fun DesktopText.inLanguage(language: DesktopLanguage): String = when (language) {
    DesktopLanguage.Russian -> russian
    DesktopLanguage.English -> english
}

internal fun DesktopSection.label(language: DesktopLanguage): String = when (this) {
    DesktopSection.Library -> DesktopText.Library
    DesktopSection.Playlists -> DesktopText.Playlists
    DesktopSection.Queue -> DesktopText.Queue
    DesktopSection.History -> DesktopText.History
    DesktopSection.Settings -> DesktopText.Settings
}.inLanguage(language)
