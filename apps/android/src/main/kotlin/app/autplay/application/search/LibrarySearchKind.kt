package app.autplay.application.search

/** A library matching field; Internet Artist/Album modes browse a separate catalogue. */
public enum class LibrarySearchKind(public val wireValue: String, internal val ftsColumns: String) {
    All("all", "{title artist album}"),
    Track("track", "title"),
    Artist("artist", "artist"),
    Album("album", "album"),
}
