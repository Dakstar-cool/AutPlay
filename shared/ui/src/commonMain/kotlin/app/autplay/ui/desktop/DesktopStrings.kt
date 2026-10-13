package app.autplay.ui.desktop

public enum class DesktopLanguage { Russian, English }

public enum class DesktopSection { Library, Playlists, Settings }

internal enum class DesktopText(public val russian: String, public val english: String) {
    LocalCollection("Локальная коллекция", "Local collection"),
    Library("Библиотека", "Library"),
    Playlists("Плейлисты", "Playlists"),
    Settings("Настройки", "Settings"),
    LibrarySubtitle("Ваша музыка. На вашем устройстве.", "Your music. On your device."),
    EmptyLibrary("Здесь будет ваша музыка", "Your music belongs here"),
    EmptyLibraryBody(
        "Библиотека пока пуста. Импорт и хранение файлов появятся после подключения локальной библиотеки.",
        "Your library is empty. File import and storage will be available when the local library is connected.",
    ),
    Import("Добавить музыку", "Add music"),
    LocalOnly("Без подключения к серверу", "No server connected"),
    EmptyPlaylists("Пока нет плейлистов", "No playlists yet"),
    EmptyPlaylistsBody(
        "Создание и сохранение плейлистов появятся вместе с локальной библиотекой.",
        "Playlist creation and storage will be available with the local library.",
    ),
    CreatePlaylist("Создать плейлист", "Create playlist"),
    SettingsSubtitle("Устройте AutPlay под себя.", "Make AutPlay feel like home."),
    Appearance("Оформление", "Appearance"),
    Light("Светлая", "Light"),
    Dark("Тёмная", "Dark"),
    Language("Язык интерфейса", "Interface language"),
    SessionSettings("Тема и язык действуют до закрытия приложения.", "Theme and language apply until the app closes."),
    NothingPlaying("Ничего не воспроизводится", "Nothing playing"),
    PlaybackUnavailable("Аудиодвижок ещё не подключён", "Audio engine is not connected yet"),
    ControlsUnavailable("Управление сейчас недоступно", "Controls are currently unavailable"),
    Previous("Назад", "Previous"),
    Play("Слушать", "Play"),
    Pause("Пауза", "Pause"),
    Next("Далее", "Next"),
    Seek("Позиция воспроизведения", "Playback position"),
    Elapsed("Прошло", "Elapsed"),
    Duration("Длительность", "Duration"),
}

internal fun DesktopText.inLanguage(language: DesktopLanguage): String = when (language) {
    DesktopLanguage.Russian -> russian
    DesktopLanguage.English -> english
}

internal fun DesktopSection.label(language: DesktopLanguage): String = when (this) {
    DesktopSection.Library -> DesktopText.Library
    DesktopSection.Playlists -> DesktopText.Playlists
    DesktopSection.Settings -> DesktopText.Settings
}.inLanguage(language)
