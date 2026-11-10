package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.*
import app.autplay.data.desktop.account.AccountCatalogFtsSearch
import app.autplay.desktop.sync.*
import app.autplay.ui.desktop.*
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong

/** Frozen J attribution. A presentation target never supplies media or network authority. */
public class DesktopAccountTrackTarget internal constructor(
    public val lease: AccountLease,
    public val trackId: AccountUuid,
    public val occurrenceId: AccountUuid?,
) {
    override fun toString(): String = "DesktopAccountTrackTarget(<redacted>)"
}

/** Bounded source reads on the host worker. Every publication replaces its opaque page targets. */
public class DesktopAccountHostCatalog(private val store: DesktopAccountStore, private val language: () -> DesktopLanguage,
    private val canonical: DesktopCanonicalCatalog, private val searchOwner: AccountCatalogFtsSearch) {
    private sealed interface Target {
        val lease: AccountLease
        data class Browse(override val lease: AccountLease, val kind: DesktopCatalogKind,
            val after: AccountUuid? = null, val parent: DesktopCatalogKey? = null,
            val afterSubject: AccountCatalogSubjectCursor? = null,
            val afterPlaylist: AccountPlaylistCursor? = null, val query: String = "",
            val albumAnchor: AccountUuid? = null, val searchAfter: String? = null,
            val searchRevision: Long? = null) : Target
        class Open(override val lease: AccountLease, val key: DesktopCatalogKey,
            val occurrenceId: String? = null, val albumAnchor: AccountUuid? = null) : Target
        class Search(val browse: Browse) : Target { override val lease: AccountLease = browse.lease }
        class Track(val value: DesktopAccountTrackTarget) : Target { override val lease: AccountLease = value.lease }
        class Canonical(override val lease: AccountLease, val key: DesktopCatalogKey,
            val after: DesktopCanonicalContinuation?) : Target
    }
    private val targets: MutableMap<String, Target> = mutableMapOf()
    private val mutations = DesktopAccountHostDomain(store, language)
    private val generation = AtomicLong(1)
    private var canonicalAfter: DesktopCanonicalContinuation? = null
    private var selection: Target.Browse? = null
    private var detail: Target.Open? = null
    private var activeLease: AccountLease? = null
    private var presentationLifetime: String? = null
    private var navigationRequested: Boolean = false
    public var view: DesktopCatalogView = unavailable()
        private set

    public fun retire(): Unit {
        generation.incrementAndGet(); canonical.invalidate(); canonicalAfter = null
        mutations.retire()
        targets.clear(); selection = null; detail = null; activeLease = null; presentationLifetime = null
        navigationRequested = false; view = unavailable()
    }

    public suspend fun refresh(lease: AccountLease, lifetime: String? = null): Unit {
        if (activeLease != lease || presentationLifetime != lifetime) retire()
        activeLease = lease
        presentationLifetime = lifetime
        read((selection ?: Target.Browse(lease, DesktopCatalogKind.Track)).copy(searchAfter = null, searchRevision = null))
    }

    public suspend fun mutate(lease: AccountLease, command: DesktopAccountCommand.DomainEdit): Unit {
        try {
            if (mutations.execute(lease, command)) {
                detail = null; navigationRequested = true
                read(Target.Browse(lease, DesktopCatalogKind.Playlist))
            }
        } finally { view = mutations.prepare(lease, view) }
    }

    public fun track(targetId: String, lease: AccountLease): DesktopAccountTrackTarget =
        (targets[targetId] as? Target.Track)?.value?.takeIf { it.lease == lease && activeLease == lease }
            ?: throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)

    public suspend fun search(targetId: String, query: String, lease: AccountLease): Unit {
        val target = targets[targetId] as? Target.Search
            ?: throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        if (target.lease != lease || activeLease != lease)
            throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        if (!DesktopAccountCatalogQuery.accepts(query)) throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
        if (target.browse.kind in listOf(DesktopCatalogKind.Artist, DesktopCatalogKind.Album)) accountCatalogSearchExpression(query)
        canonicalAfter = null; canonical.invalidate()
        detail = null; navigationRequested = true
        read(target.browse.copy(after = null, afterSubject = null, afterPlaylist = null, query = query,
            searchAfter = null, searchRevision = null))
    }

    public suspend fun open(targetId: String, lease: AccountLease): Unit {
        val target = targets[targetId]?.takeIf { it.lease == lease && activeLease == lease }
            ?: throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        navigationRequested = true
        when (target) {
            is Target.Browse -> { canonicalAfter = null; canonical.invalidate(); detail = null; read(target) }
            is Target.Open -> {
                canonicalAfter = null; canonical.invalidate()
                detail = target
                when (target.key.kind) {
                    DesktopCatalogKind.Artist -> read(Target.Browse(lease, DesktopCatalogKind.Recording, parent = target.key))
                    DesktopCatalogKind.Playlist, DesktopCatalogKind.Album, DesktopCatalogKind.Recording ->
                        read(Target.Browse(lease, DesktopCatalogKind.Track, parent = target.key, albumAnchor = target.albumAnchor))
                    DesktopCatalogKind.Release -> read(Target.Browse(lease, DesktopCatalogKind.Release, parent = target.key))
                    DesktopCatalogKind.Track -> read(selection ?: Target.Browse(lease, DesktopCatalogKind.Track))
                }
            }
            is Target.Track -> {
                canonicalAfter = null; canonical.invalidate()
                detail = Target.Open(lease, DesktopCatalogKey(DesktopCatalogKind.Track, target.value.trackId.value),
                    target.value.occurrenceId?.value)
                read(selection ?: Target.Browse(lease, DesktopCatalogKind.Track))
            }
            is Target.Canonical -> {
                if (detail?.key != target.key) throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                canonicalAfter = target.after
                read(selection ?: throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED))
            }
            is Target.Search -> throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        }
    }

    public fun actions(): List<Pair<DesktopAccountAction, String>> = buildList {
        mutations.actions().forEach { add(DesktopAccountAction.DomainMutation to it) }
        targets.forEach { (id, target) -> when (target) {
            is Target.Browse, is Target.Search, is Target.Canonical -> add(DesktopAccountAction.CatalogPage to id)
            is Target.Track -> {
                add(DesktopAccountAction.OpenTrack to id); add(DesktopAccountAction.PlayTrack to id)
                add(DesktopAccountAction.QueueTrack to id); add(DesktopAccountAction.Download to id)
            }
            is Target.Open -> add(openAction(target.key.kind) to id)
        } }
    }

    private suspend fun read(request: Target.Browse): Unit {
        val capturedGeneration = generation.get()
        targets.clear(); view = unavailable()
        val lease = request.lease
        val domain = store.domain ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        val catalog = store.catalog ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        val nextTargets = mutableMapOf<String, Target>()
        fun retain(target: Target): String {
            if (nextTargets.size >= 1_000) throw AccountStoreException(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
            return UUID.randomUUID().toString().also { nextTargets[it] = target }
        }
        fun prepare(entry: DesktopCatalogEntry, albumAnchor: AccountUuid? = null): DesktopCatalogEntry = entry.copy(
            openTargetId = retain(Target.Open(lease, entry.key, entry.occurrenceId, albumAnchor)),
            credits = entry.credits.map { credit -> credit.copy(actionTargetId =
                retain(Target.Open(lease, DesktopCatalogKey(DesktopCatalogKind.Artist, credit.artistId)))) })
        fun playable(row: AccountLocalDomainRow, occurrence: AccountLocalDomainRow? = null): String? =
            if (row.header.removed || occurrence?.header?.removed == true) null
            else retain(Target.Track(DesktopAccountTrackTarget(lease, row.header.localId, occurrence?.header?.localId)))
        suspend fun trackEntry(row: AccountLocalDomainRow, occurrence: AccountLocalDomainRow? = null): DesktopCatalogEntry {
            val data = row.detail as AccountLocalDomainDetail.Track
            val metadata = catalog.readMetadata(lease, row.header.localId)
            val state = when {
                occurrence != null && condition(occurrence) == DesktopAccountCondition.Attention -> DesktopAccountCondition.Attention
                occurrence != null && condition(occurrence) == DesktopAccountCondition.Unavailable -> DesktopAccountCondition.Unavailable
                metadata?.rawState == "REVIEW" -> DesktopAccountCondition.Attention
                occurrence?.header?.pending == true -> DesktopAccountCondition.Pending
                else -> condition(row)
            }
            return prepare(DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Track, row.header.localId.value),
                data.title, data.artist, state, identityResolved = data.recordingId != null,
                pending = row.header.pending || occurrence?.header?.pending == true,
                occurrenceId = occurrence?.header?.localId?.value, actionTargetId = playable(row, occurrence),
                availabilityUnconfirmed = true))
        }
        var nextAfter: AccountUuid? = null
        var nextSubject: AccountCatalogSubjectCursor? = null
        var nextPlaylist: AccountPlaylistCursor? = null
        var globalSearch: AccountCatalogSearchPage? = null

        // Re-read the detail instead of treating a retained label as current owner evidence.
        val opened = detail?.takeIf { it.lease == lease } ?: request.parent?.let {
            Target.Open(lease, it, albumAnchor = request.albumAnchor)
        }
        var detailEntry: DesktopCatalogEntry? = opened?.let { target -> when (target.key.kind) {
            DesktopCatalogKind.Artist -> catalog.readArtist(lease, AccountUuid(target.key.id))?.let {
                prepare(desktopAccountArtistEntry(it, true))
            }
            DesktopCatalogKind.Playlist -> catalog.readPlaylist(lease, AccountUuid(target.key.id))?.let { row ->
                val data = row.detail as AccountLocalDomainDetail.Playlist
                prepare(DesktopCatalogEntry(target.key, data.name, data.description, condition(row), pending = row.header.pending))
            }
            DesktopCatalogKind.Album -> target.albumAnchor?.let { anchor ->
                val context = catalog.readTrackContext(lease, anchor)
                val album = catalog.readMetadata(lease, anchor)?.album
                if (context == null || context.track.header.removed || album == null || album.key != target.key.id) null
                else prepare(desktopAccountMetadataAlbumEntry(album), anchor)
            }
            DesktopCatalogKind.Recording, DesktopCatalogKind.Release -> catalog.readSubject(lease, subject(target.key))?.let {
                desktopAccountAppearanceEntry(it)?.let { entry -> prepare(entry) }
            }
            DesktopCatalogKind.Track -> catalog.readTrackContext(lease, AccountUuid(target.key.id))?.let { context ->
                val row = context.track
                val data = row.detail as AccountLocalDomainDetail.Track
                val metadata = catalog.readMetadata(lease, row.header.localId)
                val appearance = data.recordingId?.let { catalog.readSubject(lease,
                    AccountCatalogSubjectCursor(it, "RECORDING_ARTIST_CREDIT")) }
                val base = trackEntry(row).copy(occurrenceId = target.occurrenceId,
                    // A detail outside its occurrence page cannot manufacture occurrence playback.
                    actionTargetId = if (target.occurrenceId == null) playable(row) else null)
                val links = buildList {
                    data.recordingId?.let { id -> add(DesktopCatalogLink(DesktopCatalogKind.Recording,
                        retain(Target.Open(lease, DesktopCatalogKey(DesktopCatalogKind.Recording, id.value))))) }
                    metadata?.album?.let { album -> add(DesktopCatalogLink(DesktopCatalogKind.Album,
                        retain(Target.Open(lease, DesktopCatalogKey(DesktopCatalogKind.Album, album.key), albumAnchor = row.header.localId)))) }
                }
                prepare(base.copy(credits = appearance?.let { desktopAccountAppearanceEntry(it)?.credits }.orEmpty(),
                    ownerProofConfirmed = appearance?.let { true }, links = links,
                    condition = if (metadata?.rawState == "REVIEW") DesktopAccountCondition.Attention else base.condition,
                    facts = buildList {
                        add(DesktopCatalogFact(DesktopCatalogField.StableId, target.key.id))
                        data.recordingId?.let { add(DesktopCatalogFact(DesktopCatalogField.RecordingId, it.value)) }
                        data.durationMs?.let { add(DesktopCatalogFact(DesktopCatalogField.DurationMs, it.toString())) }
                        metadata?.let { add(DesktopCatalogFact(DesktopCatalogField.MetadataState, it.rawState)) }
                        metadata?.album?.discNumber?.let { add(DesktopCatalogFact(DesktopCatalogField.DiscNumber, it.toString())) }
                        metadata?.album?.trackNumber?.let { add(DesktopCatalogFact(DesktopCatalogField.TrackNumber, it.toString())) }
                        row.header.rawProjectionState?.let { add(DesktopCatalogFact(DesktopCatalogField.ProjectionState, it)) }
                        target.occurrenceId?.let { add(DesktopCatalogFact(DesktopCatalogField.OccurrenceId, it)) }
                        context.library.filter { !it.header.removed }.forEach { library ->
                            val value = library.detail as AccountLocalDomainDetail.Library
                            value.rawSource?.let { add(DesktopCatalogFact(DesktopCatalogField.Source, it)) }
                            value.rawAvailability?.let { add(DesktopCatalogFact(DesktopCatalogField.Availability, it)) }
                        }
                    }, partialContext = context.libraryHasMore))
            } ?: prepare(DesktopCatalogEntry(target.key, null, null, DesktopAccountCondition.Unavailable,
                occurrenceId = target.occurrenceId, availabilityUnconfirmed = true,
                facts = listOfNotNull(target.occurrenceId?.let {
                    DesktopCatalogFact(DesktopCatalogField.OccurrenceId, it)
                })))
        } }
        var canonicalPositions: DesktopCanonicalPositionsView? = null
        val canonicalKey = opened?.key?.takeIf { it.kind == DesktopCatalogKind.Recording || it.kind == DesktopCatalogKind.Release }
        if (canonicalKey != null && detailEntry != null) {
            val projectedDetail = detailEntry
            val recording = canonicalKey.kind == DesktopCatalogKind.Recording
            val subjectId = AccountUuid(canonicalKey.id)
            try {
                val current = canonical.detail(lease, subjectId, recording)
                val currentCredits = current.credit.members.map {
                    DesktopCreditView(it.artistId.value, it.creditedName, it.position, it.joinPhrase, it.rawRole)
                }
                detailEntry = projectedDetail.copy(title = current.title, subtitle = current.credit.displayName,
                    credits = currentCredits, creditProjectionOnly = false, ownerProofConfirmed = true,
                    facts = buildList {
                        add(DesktopCatalogFact(DesktopCatalogField.StableId, current.id.value))
                        add(DesktopCatalogFact(DesktopCatalogField.RowVersion, current.revision.toString()))
                        when (current) {
                            is DesktopCanonicalDetail.Recording -> {
                                current.durationMs?.let { add(DesktopCatalogFact(DesktopCatalogField.DurationMs, it.toString())) }
                                current.versionText?.let { add(DesktopCatalogFact(DesktopCatalogField.VersionText, it)) }
                                current.disambiguation?.let { add(DesktopCatalogFact(DesktopCatalogField.Disambiguation, it)) }
                                add(DesktopCatalogFact(DesktopCatalogField.RecordingKind, current.rawKind))
                                add(DesktopCatalogFact(DesktopCatalogField.IdentityStatus, current.rawIdentityStatus))
                            }
                            is DesktopCanonicalDetail.Release -> {
                                current.date?.let { add(DesktopCatalogFact(DesktopCatalogField.CanonicalDate, it)) }
                                current.precision?.let { add(DesktopCatalogFact(DesktopCatalogField.DatePrecision, it)) }
                                current.country?.let { add(DesktopCatalogFact(DesktopCatalogField.Country, it)) }
                                add(DesktopCatalogFact(DesktopCatalogField.ReleaseStatus, current.rawStatus))
                            }
                        }
                    })
                val positions = canonical.positions(lease, subjectId, recording, canonicalAfter)
                val items = positions.items.map { row ->
                    DesktopCanonicalPositionView(row.id.value, row.title, row.releaseTitle, row.release.value,
                        row.recording.value, row.medium.value, row.mediumPosition, row.sequence, row.number, row.durationMs,
                        row.releaseDate, row.datePrecision, row.credit.members.map {
                            DesktopCreditView(it.artistId.value, it.creditedName, it.position, it.joinPhrase, it.rawRole)
                        }, retain(Target.Open(lease, DesktopCatalogKey(DesktopCatalogKind.Release, row.release.value))),
                        retain(Target.Open(lease, DesktopCatalogKey(DesktopCatalogKind.Recording, row.recording.value))))
                }
                canonicalPositions = DesktopCanonicalPositionsView(if (items.isEmpty()) DesktopAccountCondition.Empty else DesktopAccountCondition.Ready,
                    items, if (canonicalAfter == null) null else retain(Target.Canonical(lease, canonicalKey, null)),
                    positions.next?.let { retain(Target.Canonical(lease, canonicalKey, it)) }, positions.exhausted)
            } catch (error: DesktopSyncException) {
                canonicalAfter = null
                canonicalPositions = DesktopCanonicalPositionsView(DesktopAccountCondition.Unavailable, emptyList(), null, null, false,
                    if (language() == DesktopLanguage.Russian) "Канонический вид недоступен или изменился. Откройте запись или релиз заново."
                    else "The canonical view is unavailable or changed. Open the Recording or Release again.")
            }
        }
        val parentVisible = when (val parent = request.parent) {
            null -> true
            else -> when (parent.kind) {
                DesktopCatalogKind.Artist -> catalog.readArtist(lease, AccountUuid(parent.id)) != null
                DesktopCatalogKind.Playlist -> catalog.readPlaylist(lease, AccountUuid(parent.id))?.header?.removed == false
                DesktopCatalogKind.Recording, DesktopCatalogKind.Release -> catalog.readSubject(lease, subject(parent)) != null
                DesktopCatalogKind.Album -> request.albumAnchor?.let { anchor ->
                    catalog.readTrackContext(lease, anchor)?.track?.header?.removed == false &&
                        catalog.readMetadata(lease, anchor)?.album?.key == parent.id
                } == true
                DesktopCatalogKind.Track -> false
            }
        }
        val entries: List<DesktopCatalogEntry> = if (!parentVisible) emptyList() else when {
            request.parent?.kind == DesktopCatalogKind.Release -> emptyList()
            request.parent?.kind == DesktopCatalogKind.Artist -> {
                val page = catalog.readAppearancePage(lease, AccountUuid(request.parent.id), request.afterSubject, PAGE)
                if (!page.exhausted) nextSubject = page.scannedThrough ?: unavailableData()
                page.items.mapNotNull { desktopAccountAppearanceEntry(it) }.map { prepare(it) }
            }
            request.parent?.kind == DesktopCatalogKind.Playlist -> {
                val page = domain.readPlaylistPage(lease, AccountUuid(request.parent.id), request.afterPlaylist, PAGE)
                nextPlaylist = page.next
                page.items.map { item ->
                    val occurrence = item.occurrence.detail as AccountLocalDomainDetail.Entry
                    item.track?.let { trackEntry(it, item.occurrence) } ?: prepare(DesktopCatalogEntry(
                        DesktopCatalogKey(DesktopCatalogKind.Track, occurrence.trackId.value), null, null,
                        DesktopAccountCondition.Unavailable, occurrenceId = item.occurrence.header.localId.value))
                }
            }
            request.parent == null && request.kind in listOf(DesktopCatalogKind.Artist, DesktopCatalogKind.Album) -> {
                val kind = if (request.kind == DesktopCatalogKind.Artist) AccountCatalogSearchKind.ARTIST else AccountCatalogSearchKind.ALBUM
                val snapshot = catalog.captureSearch(lease, kind)
                if (snapshot.lease != lease || snapshot.kind != kind ||
                    request.searchRevision?.let { it != snapshot.revision } == true)
                    throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
                val page = searchOwner.page(snapshot, request.query, request.searchAfter) {
                    capturedGeneration == generation.get()
                }
                globalSearch = page
                page.items.map { document ->
                    if (kind == AccountCatalogSearchKind.ARTIST) prepare(desktopAccountArtistEntry(requireNotNull(document.artist), true))
                    else prepare(desktopAccountMetadataAlbumEntry(requireNotNull(document.album)), requireNotNull(document.albumAnchor))
                }
            }
            request.kind == DesktopCatalogKind.Recording || request.kind == DesktopCatalogKind.Release -> {
                val page = catalog.readSubjectPage(lease, subjectType(request.kind), request.afterSubject, PAGE)
                if (!page.exhausted) nextSubject = page.scannedThrough ?: unavailableData()
                page.items.mapNotNull { desktopAccountAppearanceEntry(it) }.map { prepare(it) }
            }
            request.kind == DesktopCatalogKind.Playlist -> {
                val rows = domain.readProjectionPage(lease, AccountProjectionKind.PLAYLIST, request.after, PAGE)
                if (rows.size == PAGE) nextAfter = rows.last().header.localId
                rows.map { row ->
                    val data = row.detail as AccountLocalDomainDetail.Playlist
                    prepare(DesktopCatalogEntry(DesktopCatalogKey(DesktopCatalogKind.Playlist, row.header.localId.value),
                        data.name, data.description, condition(row), pending = row.header.pending))
                }
            }
            else -> {
                val rows = domain.readProjectionPage(lease, AccountProjectionKind.TRACK, request.after, PAGE)
                if (rows.size == PAGE) nextAfter = rows.last().header.localId
                when {
                    request.parent?.kind == DesktopCatalogKind.Recording -> rows.filter {
                        (it.detail as AccountLocalDomainDetail.Track).recordingId?.value == request.parent.id
                    }.map { trackEntry(it) }
                    request.parent?.kind == DesktopCatalogKind.Album -> {
                        val matches = mutableListOf<DesktopCatalogEntry>()
                        for (row in rows) if (catalog.readMetadata(lease, row.header.localId)?.album?.key == request.parent.id)
                            matches.add(trackEntry(row))
                        matches
                    }
                    request.kind == DesktopCatalogKind.Album -> {
                        val albums = linkedMapOf<String, Pair<AccountMetadataAlbum, AccountUuid>>()
                        for (row in rows) if (!row.header.removed) catalog.readMetadata(lease, row.header.localId)?.album?.let {
                            albums.putIfAbsent(it.key, it to row.header.localId)
                        }
                        albums.values.map { (album, anchor) -> prepare(desktopAccountMetadataAlbumEntry(album), anchor) }
                    }
                    else -> rows.map { trackEntry(it) }
                }
            }
        }
        val visible = if (globalSearch != null) entries else
            DesktopAccountCatalogQuery(request.query).let { query -> entries.filter(query::includes) }
        // Only the source cursor determines continuation, including an empty filtered/proof page.
        val next = if (globalSearch?.nextKey != null)
            retain(request.copy(searchAfter = globalSearch.nextKey, searchRevision = globalSearch.revision))
        else if (nextPlaylist != null || nextSubject != null || nextAfter != null)
            retain(request.copy(after = nextAfter, afterSubject = nextSubject, afterPlaylist = nextPlaylist)) else null
        val previous = if (request.after != null || request.afterSubject != null || request.afterPlaylist != null || request.searchAfter != null)
            retain(request.copy(after = null, afterSubject = null, afterPlaylist = null, searchAfter = null, searchRevision = null)) else null
        val destinations = DesktopCatalogKind.entries.map {
            DesktopCatalogDestination(retain(Target.Browse(lease, it, query = if (it == request.kind) request.query else "")), it)
        }
        val home = mutableListOf<DesktopCatalogHomeSection>()
        // Independent bounded previews. Page order is stable ID order, never a release-date claim.
        for (kind in listOf(DesktopCatalogKind.Artist, DesktopCatalogKind.Release, DesktopCatalogKind.Album)) {
            val preview = when (kind) {
                DesktopCatalogKind.Artist -> catalog.readArtistPage(lease, null, 8).items.map { prepare(desktopAccountArtistEntry(it, true)) }
                DesktopCatalogKind.Release -> catalog.readSubjectPage(lease, subjectType(kind), null, 8).items
                    .mapNotNull { desktopAccountAppearanceEntry(it) }.map { prepare(it) }
                else -> {
                    val albums = linkedMapOf<String, Pair<AccountMetadataAlbum, AccountUuid>>()
                    for (row in domain.readProjectionPage(lease, AccountProjectionKind.TRACK, null, 8))
                        if (!row.header.removed) catalog.readMetadata(lease, row.header.localId)?.album?.let {
                            albums.putIfAbsent(it.key, it to row.header.localId)
                        }
                    albums.values.map { (album, anchor) -> prepare(desktopAccountMetadataAlbumEntry(album), anchor) }
                }
            }
            val title = when (kind) {
                DesktopCatalogKind.Artist -> if (language() == DesktopLanguage.Russian) "Исполнители каталога" else "Catalog artists"
                DesktopCatalogKind.Release -> if (language() == DesktopLanguage.Russian) "Релизы: подтверждённые credits" else "Releases: confirmed credits"
                else -> if (language() == DesktopLanguage.Russian) "Альбомы по метаданным" else "Metadata albums"
            }
            home.add(DesktopCatalogHomeSection(UUID.randomUUID().toString(), title, preview,
                retain(Target.Browse(lease, kind))))
        }
        val occurrenceDetail = detailEntry
        if (occurrenceDetail?.occurrenceId != null) detailEntry = occurrenceDetail.copy(actionTargetId = visible.singleOrNull {
            it.key == occurrenceDetail.key && it.occurrenceId == occurrenceDetail.occurrenceId
        }?.actionTargetId)
        val searchTarget = retain(Target.Search(request))
        val page = DesktopCatalogPage(UUID.randomUUID().toString(), visible, previous, next)
        val relation = when (request.parent?.kind) {
            DesktopCatalogKind.Artist -> DesktopCatalogRelation.ArtistAppearances
            DesktopCatalogKind.Playlist -> DesktopCatalogRelation.PlaylistOccurrences
            DesktopCatalogKind.Album -> DesktopCatalogRelation.MetadataAlbumTracks
            DesktopCatalogKind.Recording -> DesktopCatalogRelation.RecordingReferences
            DesktopCatalogKind.Release -> DesktopCatalogRelation.ReleaseCredits
            else -> null
        }
        val nextView = mutations.prepare(lease, DesktopCatalogView(if (!parentVisible) DesktopAccountCondition.Unavailable
            else if (visible.isEmpty()) DesktopAccountCondition.Empty else DesktopAccountCondition.Ready,
            page, detailEntry, home, destinations, request.kind, request.query, searchTarget, request.query.isNotBlank(), navigationRequested, relation,
            canonicalPositions = canonicalPositions, currentAccountScopeId = lease.scopeId.value))
        globalSearch?.let { catalog.requireSearchRevision(lease, it.revision) }
        if (capturedGeneration != generation.get() || activeLease != lease)
            throw AccountStoreException(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        targets.clear(); targets.putAll(nextTargets); selection = request; view = nextView
    }

    private fun condition(row: AccountLocalDomainRow): DesktopAccountCondition = when {
        row.header.removed || row.header.hasConflict -> DesktopAccountCondition.Attention
        row.header.rawProjectionState != null && row.header.rawProjectionState != "COMPLETE" -> DesktopAccountCondition.Unavailable
        row.header.pending || row.header.snapshotConfirmed == false -> DesktopAccountCondition.Pending
        else -> DesktopAccountCondition.Ready
    }
    private companion object {
        const val PAGE: Int = 100
        fun unavailableData(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        fun subjectType(kind: DesktopCatalogKind): String = when (kind) {
            DesktopCatalogKind.Recording -> "RECORDING_ARTIST_CREDIT"
            DesktopCatalogKind.Release -> "RELEASE_ARTIST_CREDIT"
            else -> throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        }
        fun subject(key: DesktopCatalogKey): AccountCatalogSubjectCursor =
            AccountCatalogSubjectCursor(AccountUuid(key.id), subjectType(key.kind))
        fun openAction(kind: DesktopCatalogKind): DesktopAccountAction = when (kind) {
            DesktopCatalogKind.Artist -> DesktopAccountAction.OpenArtist
            DesktopCatalogKind.Album -> DesktopAccountAction.OpenAlbum
            DesktopCatalogKind.Recording -> DesktopAccountAction.OpenRecording
            DesktopCatalogKind.Release -> DesktopAccountAction.OpenRelease
            DesktopCatalogKind.Track -> DesktopAccountAction.OpenTrack
            DesktopCatalogKind.Playlist -> DesktopAccountAction.OpenPlaylist
        }
        fun unavailable(): DesktopCatalogView = DesktopCatalogView(DesktopAccountCondition.Unavailable, null, null, emptyList())
    }
}
