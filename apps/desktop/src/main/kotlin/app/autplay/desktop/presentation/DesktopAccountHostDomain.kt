package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.*
import app.autplay.ui.desktop.*
import java.util.UUID

/** Frozen account commands only. No Room, remote-ID aliasing or optimistic UI receipt. */
internal class DesktopAccountHostDomain(private val store: DesktopAccountStore, private val language: () -> DesktopLanguage) {
    private data class Target(val lease: AccountLease, val operation: AccountUuid,
        val command: AccountDomainCommand?, val review: AccountDomainReview, val control: DesktopDomainControl,
        val addTrack: AccountLocalDomainRow? = null)
    private data class Pending(val target: Target, val change: AccountDomainChange)
    private val targets = linkedMapOf<String, Target>()
    private val pending = linkedMapOf<Pair<AccountUuid, AccountUuid>, Pending>()
    private var adding: Pair<AccountLease, AccountLocalDomainRow>? = null
    private var owner: AccountLease? = null
    private val domain get() = store.domain ?: fail(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)

    fun retire(): Unit { targets.clear(); adding = null; owner = null }
    fun actions(): List<String> = targets.keys.toList()
    private fun text(ru: String, en: String): String = if (language() == DesktopLanguage.Russian) ru else en
    private fun expected(kind: AccountProjectionKind, row: AccountLocalDomainRow): AccountDomainExpected =
        AccountDomainExpected(kind, row.header.localId, row.header)
    private fun usable(row: AccountLocalDomainRow): Boolean = !row.header.hasConflict &&
        row.header.rawProjectionState.let { it == null || it == "COMPLETE" } && row.header.snapshotConfirmed != false
    private fun manual(row: AccountLocalDomainRow): Boolean = usable(row) && !row.header.removed &&
        (row.detail as? AccountLocalDomainDetail.Playlist)?.rawType == "MANUAL"

    private fun retain(lease: AccountLease, command: AccountDomainCommand?, review: AccountDomainReview,
        label: String, kind: DesktopDomainEditKind = DesktopDomainEditKind.Apply,
        name: String? = null, description: String? = null, addTrack: AccountLocalDomainRow? = null): DesktopDomainControl {
        if (targets.size >= 500) fail(AccountFailure.ACCOUNT_DATA_UNAVAILABLE)
        val id = UUID.randomUUID().toString()
        val control = DesktopDomainControl(id, label, kind, name, description)
        targets[id] = Target(lease, AccountUuid.random(), command, review, control, addTrack)
        return control
    }

    suspend fun prepare(lease: AccountLease, view: DesktopCatalogView): DesktopCatalogView {
        if (owner != lease) { retire(); owner = lease }
        targets.clear()
        val retained = pending[lease.storeId to lease.scopeId]
        if (retained != null) {
            // An uncertain commit keeps exactly its original command/operation and captured lease.
            if (retained.target.lease.storeId != lease.storeId || retained.target.lease.scopeId != lease.scopeId) return view
            val retry = retained.target.control.copy(kind = DesktopDomainEditKind.Retry,
                label = text("Выяснить результат изменения", "Resolve change outcome"))
            targets[retry.targetId] = retained.target.copy(lease = lease, control = retry)
            return view.copy(mutations = listOf(retry))
        }
        val newId = AccountUuid.random()
        val create = retain(lease, AccountDomainCommand.Playlist(newId, "", null, true),
            AccountDomainReview(listOf(AccountDomainExpected(AccountProjectionKind.PLAYLIST, newId, null))),
            text("Создать ручной плейлист аккаунта", "Create account manual playlist"), DesktopDomainEditKind.PlaylistMetadata)
        var detail = view.detail
        if (detail?.key?.kind == DesktopCatalogKind.Track) {
            val track = domain.readExact(lease, AccountProjectionKind.TRACK, AccountUuid(detail.key.id))
            if (track != null && usable(track) && !track.header.removed) {
                val controls = mutableListOf<DesktopDomainControl>()
                val preferences = domain.readTrackPreferences(lease, track.header.localId)
                if (preferences.size <= 1 && preferences.all { usable(it) && !it.header.removed }) {
                    val old = preferences.singleOrNull()
                    val id = old?.header?.localId ?: AccountUuid.random()
                    val data = old?.detail as? AccountLocalDomainDetail.Preference
                    if (data == null || data.rawPreference in setOf("NEUTRAL", "LIKED", "DISLIKED")) {
                        val review = AccountDomainReview(listOf(expected(AccountProjectionKind.TRACK, track),
                            AccountDomainExpected(AccountProjectionKind.PREFERENCE, id, old?.header)))
                        for ((value, label) in listOf("NEUTRAL" to text("Нейтрально", "Neutral"),
                            "LIKED" to text("Нравится", "Like"), "DISLIKED" to text("Не нравится", "Dislike"))) {
                            controls += retain(lease, AccountDomainCommand.Preference(id, track.header.localId,
                                value, data?.excludedFromTaste ?: false), review,
                                if (data == null) label + text("; учитывать во вкусе", "; include in taste") else label)
                        }
                        if (data != null) controls += retain(lease, AccountDomainCommand.Preference(id, track.header.localId,
                            data.rawPreference, !data.excludedFromTaste), review,
                            if (data.excludedFromTaste) text("Учитывать во вкусе", "Include in taste")
                            else text("Исключить из вкуса", "Exclude from taste"))
                        detail = detail.copy(facts = detail.facts + listOf(
                            DesktopCatalogFact(DesktopCatalogField.Preference, when (data?.rawPreference) {
                                "NEUTRAL" -> text("Нейтрально", "Neutral")
                                "LIKED" -> text("Нравится", "Liked")
                                "DISLIKED" -> text("Не нравится", "Disliked")
                                else -> text("Не получена", "Not supplied")
                            }), DesktopCatalogFact(DesktopCatalogField.TasteExclusion, when (data?.excludedFromTaste) {
                                true -> text("Исключён", "Excluded")
                                false -> text("Учитывается", "Included")
                                null -> text("Не получен", "Not supplied")
                            })))
                    }
                }
                val context = store.catalog?.readTrackContext(lease, track.header.localId)
                context?.library?.filter(::usable)?.forEach { row ->
                    controls += retain(lease, AccountDomainCommand.Library(row.header.localId, track.header.localId, !row.header.removed),
                        AccountDomainReview(listOf(expected(AccountProjectionKind.TRACK, track), expected(AccountProjectionKind.LIBRARY, row))),
                        if (row.header.removed) text("Вернуть библиотечную ссылку", "Restore library reference")
                        else text("Убрать библиотечную ссылку", "Remove library reference"), DesktopDomainEditKind.Delete)
                }
                if (context != null && context.library.isEmpty() && !context.libraryHasMore) {
                    val id = AccountUuid.random()
                    controls += retain(lease, AccountDomainCommand.Library(id, track.header.localId, false),
                        AccountDomainReview(listOf(expected(AccountProjectionKind.TRACK, track),
                            AccountDomainExpected(AccountProjectionKind.LIBRARY, id, null))),
                        text("Добавить в коллекцию аккаунта", "Add to account collection"))
                }
                controls += retain(lease, null, AccountDomainReview(listOf(expected(AccountProjectionKind.TRACK, track))),
                    text("Добавить в ручной плейлист…", "Add to manual playlist…"), DesktopDomainEditKind.BeginAdd, addTrack = track)
                detail = detail.copy(mutations = controls)
            }
        }
        val selectedPlaylist = detail?.takeIf { it.key.kind == DesktopCatalogKind.Playlist }?.let {
            domain.readExact(lease, AccountProjectionKind.PLAYLIST, AccountUuid(it.key.id))
        }
        if (selectedPlaylist != null && manual(selectedPlaylist)) {
            val data = selectedPlaylist.detail as AccountLocalDomainDetail.Playlist
            val review = AccountDomainReview(listOf(expected(AccountProjectionKind.PLAYLIST, selectedPlaylist)))
            detail = detail?.copy(mutations = listOf(
                retain(lease, AccountDomainCommand.Playlist(selectedPlaylist.header.localId, data.name ?: "", data.description, false),
                    review, text("Название и описание", "Name and description"), DesktopDomainEditKind.PlaylistMetadata, data.name, data.description),
                retain(lease, AccountDomainCommand.Delete(selectedPlaylist.header.localId, "PLAYLIST"), review,
                    text("Удалить плейлист аккаунта", "Delete account playlist"), DesktopDomainEditKind.Delete)))
        }
        val editable = selectedPlaylist?.takeIf(::manual)?.let { domain.readEditablePlaylist(lease, it.header.localId) }
        val page = view.page?.let { page ->
            val entries = page.entries.map { entry ->
                val controls = mutableListOf<DesktopDomainControl>()
                val playlist = if (entry.key.kind == DesktopCatalogKind.Playlist)
                    domain.readExact(lease, AccountProjectionKind.PLAYLIST, AccountUuid(entry.key.id)) else null
                val add = adding?.takeIf { it.first == lease }?.second
                if (playlist != null && manual(playlist) && add != null) {
                    val id = AccountUuid.random()
                    val review = AccountDomainReview(listOf(expected(AccountProjectionKind.TRACK, add),
                        expected(AccountProjectionKind.PLAYLIST, playlist), AccountDomainExpected(AccountProjectionKind.PLAYLIST_ENTRY, id, null)))
                    controls += retain(lease, AccountDomainCommand.Entry(id, playlist.header.localId, add.header.localId,
                        "0", false, serverOrdering = true), review,
                        text("Добавить выбранный трек сюда", "Add selected track here"))
                }
                val occurrenceId = entry.occurrenceId
                if (selectedPlaylist != null && manual(selectedPlaylist) && occurrenceId != null) {
                    val occurrence = domain.readExact(lease, AccountProjectionKind.PLAYLIST_ENTRY, AccountUuid(occurrenceId))
                    if (occurrence != null && usable(occurrence) && !occurrence.header.removed) {
                        controls += retain(lease, AccountDomainCommand.Delete(occurrence.header.localId, "PLAYLIST_ENTRY"),
                            AccountDomainReview(listOf(expected(AccountProjectionKind.PLAYLIST, selectedPlaylist),
                                expected(AccountProjectionKind.PLAYLIST_ENTRY, occurrence))),
                            text("Удалить это вхождение", "Remove this occurrence"), DesktopDomainEditKind.Delete)
                        val order = requireNotNull(editable)
                        if (order.next == null && order.items.all { usable(it.occurrence) && !it.occurrence.header.removed }) {
                            val ids = order.items.map { it.occurrence.header.localId }
                            val index = ids.indexOf(occurrence.header.localId)
                            val review = AccountDomainReview(listOf(expected(AccountProjectionKind.PLAYLIST, selectedPlaylist)) +
                                order.items.map { expected(AccountProjectionKind.PLAYLIST_ENTRY, it.occurrence) }, selectedPlaylist.header.localId, ids)
                            val data = occurrence.detail as AccountLocalDomainDetail.Entry
                            if (index > 0) controls += retain(lease, AccountDomainCommand.Entry(occurrence.header.localId,
                                data.playlistId, data.trackId, data.positionKey, true, serverOrdering = true, beforeEntryId = ids[index - 1]),
                                review, text("Выше", "Move up"))
                            if (index >= 0 && index < ids.lastIndex) controls += retain(lease, AccountDomainCommand.Entry(occurrence.header.localId,
                                data.playlistId, data.trackId, data.positionKey, true, serverOrdering = true, beforeEntryId = ids.getOrNull(index + 2)),
                                review, text("Ниже", "Move down"))
                        }
                    }
                }
                entry.copy(mutations = controls)
            }
            page.copy(entries = entries)
        }
        return view.copy(detail = detail, page = page, mutations = listOf(create), mutationNotice =
            if (adding?.first == lease || editable?.next != null) text(
                "Выберите ручной плейлист для выбранного трека. Добавление и перестановка доступны при полном списке до 100 активных вхождений. Иные списки остаются доступными для просмотра, воспроизведения и удаления отдельных вхождений.",
                "Choose a manual playlist for the selected track. Adding and reordering require a complete list of up to 100 active occurrences. Other lists remain available for browsing, playback and individual occurrence removal.") else null)
    }

    /** true requests the existing paged playlist browser; false is a durable local domain result. */
    suspend fun execute(lease: AccountLease, command: DesktopAccountCommand.DomainEdit): Boolean {
        val target = targets[command.targetId]?.takeIf { it.lease == lease && owner == lease } ?: fail(AccountFailure.ACCOUNT_SCOPE_CHANGED)
        if (target.control.kind == DesktopDomainEditKind.BeginAdd) {
            val row = requireNotNull(target.addTrack)
            if (domain.readExact(lease, AccountProjectionKind.TRACK, row.header.localId) != row) fail(AccountFailure.ACCOUNT_LEASE_STALE)
            adding = lease to row
            return true
        }
        val pendingKey = lease.storeId to lease.scopeId
        val original = pending[pendingKey]
        if (original != null && original.target.lease != lease) {
            val receipt = domain.readMutationOutcome(lease, original.change, original.target.review)
            if (receipt != null) requireReceipt(receipt, original.change)
            pending.remove(pendingKey)
            if (receipt == null) fail(AccountFailure.ACCOUNT_LEASE_STALE)
            return false
        }
        val change = if (original != null) {
            if (original.target.operation != target.operation || command.name != null || command.description != null)
                fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            original.change
        } else {
            val base = requireNotNull(target.command)
            val edited = if (target.control.kind == DesktopDomainEditKind.PlaylistMetadata) {
                val playlist = base as AccountDomainCommand.Playlist
                val name = command.name ?: fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                if (name.isBlank() || name.length > 500 || command.description?.length?.let { it > 2000 } == true)
                    fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                playlist.copy(name = name, description = command.description)
            } else {
                if (command.name != null || command.description != null) fail(AccountFailure.ACCOUNT_INPUT_INVALID)
                base
            }
            if (pending.size >= 32) fail(AccountFailure.ACCOUNT_STORAGE_FULL)
            try { AccountDomainWire.payload(edited) }
            catch (_: IllegalArgumentException) { fail(AccountFailure.ACCOUNT_INPUT_INVALID) }
            AccountDomainChange(target.operation, System.currentTimeMillis(), edited).also { pending[pendingKey] = Pending(target, it) }
        }
        val receipt = try { domain.mutateReviewed(lease, change, target.review) }
        catch (failure: AccountStoreException) {
            // A guarded read distinguishes a committed effect from a rejected stale review.
            // If storage/authority cannot establish it, preserve the exact pending command.
            val observed = domain.readMutationOutcome(lease, change, target.review)
            if (observed != null) {
                requireReceipt(observed, change); pending.remove(pendingKey)
                return false
            }
            if (failure.failure in setOf(AccountFailure.ACCOUNT_LEASE_STALE, AccountFailure.ACCOUNT_INPUT_INVALID,
                    AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION, AccountFailure.ACCOUNT_IDENTITY_CONFLICT)) pending.remove(pendingKey)
            throw failure
        }
        requireReceipt(receipt, change)
        pending.remove(pendingKey)
        return false
    }

    private fun requireReceipt(receipt: AccountDomainReceipt, change: AccountDomainChange): Unit {
        if (receipt.operationId != change.operationId || receipt.aggregateId != change.command.localId ||
            (receipt.eventId == null) == (receipt.localIntentId == null)) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }

    private fun fail(code: AccountFailure): Nothing = throw AccountStoreException(code)
}
