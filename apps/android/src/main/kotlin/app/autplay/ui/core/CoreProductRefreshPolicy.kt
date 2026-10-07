package app.autplay.ui.core

/** Sources follow the active connection, including after restoring an older source selection. */
public fun automaticSearchScopes(bindingKey: String?): Set<SearchScope> =
    if (bindingKey == null) setOf(SearchScope.Local) else setOf(SearchScope.Local, SearchScope.Vault)

/** Keep old enum values readable while retiring their user-facing library controls. */
public fun LibrarySection.browseSection(): LibrarySection = when (this) {
    LibrarySection.Offline, LibrarySection.Unavailable -> LibrarySection.Tracks
    else -> this
}

public fun LibraryFilter.browseFilter(): LibraryFilter = when (this) {
    LibraryFilter.All, LibraryFilter.Loved -> this
    else -> LibraryFilter.All
}

/** Track metadata remains an internal capability projection; only collections have detail routes. */
public fun DetailTarget?.collectionDetail(): DetailTarget? = this?.takeUnless { it.kind == DetailKind.Track }
