package app.autplay.desktop.presentation

import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.account.*
import app.autplay.data.desktop.desktopStandaloneAccountSource
import app.autplay.data.desktop.desktopStandaloneAccountReview
import app.autplay.data.desktop.requireDesktopStandaloneAccountOwner
import app.autplay.ui.desktop.*
import java.util.UUID
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*

/** Explicit, frozen, path-free source review. J owns registration, claims and all account writes. */
public class DesktopAccountLocalDataOwner(
    private val library: DesktopLibraryStore,
    private val store: DesktopAccountStore,
    private val publicId: () -> String?,
    private val language: () -> DesktopLanguage,
) : DesktopAccountHostLocalData, AutoCloseable {
    private val materialization = store.materialization
        ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
    private val serial = Mutex()
    private val publication = Any()
    private val retired = AtomicBoolean(false)
    private var revision = 0L
    private var current: Frozen? = null
    private class BeforeGrant(val view: DesktopLocalDataReview, val server: String,
        val snapshot: StandaloneAccountReviewSnapshot, val next: String?, val revision: Long, val cutoff: Long,
        var selected: List<String>? = null, var intent: AccountUuid? = null, var completed: Boolean = false,
        var reserved: Boolean = false)
    private var beforeGrant: BeforeGrant? = null
    private class Frozen(val view: DesktopLocalDataReview, val lease: AccountLease,
        val snapshot: AccountSelectedSourceSnapshot, val source: DesktopStandaloneAccountSource,
        val next: String?, val revision: Long, val cutoff: Long)

    override suspend fun reviewBeforeGrant(server: String, continuation: String?): DesktopLocalDataReview = serial.withLock {
        requireOpen()
        val prior = beforeGrant
        if (prior?.reserved == true && !prior.completed) changed()
        val after = if (continuation == null) null else prior?.takeIf {
            it.server == server && it.view.id == continuation && it.selected == null
        }?.next ?: changed()
        val source = desktopStandaloneAccountReview(library)
        val page = source.readPage(after)
        val ids = page.facts.filter { it.eligible }.map { it.changeId }
        val snapshot = if (ids.isEmpty()) StandaloneAccountReviewSnapshot(emptyList(), emptyMap()) else source.readSelected(ids)
        val cutoff = System.currentTimeMillis().also { require(it in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS) }
        val view = DesktopLocalDataReview(UUID.randomUUID().toString(),
            if (language() == DesktopLanguage.Russian) "Аккаунт будет подтверждён при подключении" else "Account confirmed during connection",
            page.facts.map { fact -> DesktopLocalDataFact(fact.changeId, label(fact, snapshot.facts, snapshot.dependencies),
                fact.eligible && snapshot.facts.any { it.changeId == fact.changeId && it.occurredAtMs <= cutoff }) },
            page.nextChangeId != null)
        if (revision == DesktopAccountLimits.MAX_REVISION) changed()
        requireOpen()
        beforeGrant = BeforeGrant(view, server, snapshot, page.nextChangeId, ++revision, cutoff)
        view
    }

    override suspend fun selectBeforeGrant(review: DesktopLocalDataReview, ids: List<String>): Unit = serial.withLock {
        requireOpen()
        val frozen = beforeGrant?.takeIf { it.view === review && it.intent == null && !it.reserved } ?: changed()
        // A conservative subset leaves room for the complete immutable <=4096-byte J2 intent.
        if (ids.size !in 1..32 || ids.distinct().size != ids.size || ids.any { id ->
                review.facts.none { it.id == id && it.eligible }
            }) changed()
        ids.forEach { AccountUuid(it) }
        val actual = desktopStandaloneAccountReview(library).readSelected(ids)
        val expected = frozen.snapshot.facts.filter { it.changeId in ids }
        if (!sameFacts(expected, actual.facts) ||
            dependencies(expected, frozen.snapshot.dependencies) != actual.dependencies) changed()
        frozen.selected = ids.toList()
    }

    override suspend fun sealBeforeGrant(server: String, ids: List<String>): Unit = serial.withLock {
        requireOpen()
        val frozen = beforeGrant?.takeIf { it.server == server && it.selected == ids } ?: changed()
        frozen.reserved = true
    }

    override suspend fun releaseUnsentBeforeGrant(): Unit = serial.withLock {
        requireOpen()
        beforeGrant?.takeIf { it.intent == null && !it.completed }?.reserved = false
    }

    override suspend fun completeBeforeGrant(lease: AccountLease, intentId: AccountUuid): Unit {
        val dispatch = serial.withLock {
            requireOpen()
            val intent = store.readBindingIntent(intentId) ?: changed()
            if (intent.localDataChoice == AccountBindingLocalChoice.KEEP_LOCAL) return@withLock null
            val saved = materialization.readSelection(lease, intentId)
            if (saved != null) {
                if (saved.facts.size != intent.selectedChangeIds.size ||
                    saved.facts.map { it.changeId }.toSet() != intent.selectedChangeIds.map { it.value }.toSet()) changed()
                if (saved.rawState != "MATERIALIZED") {
                    val registration = materialization.readSourceRegistration(lease) ?: changed()
                    materialization.materializeSelected(lease, intentId, saved.reviewRevision,
                        desktopStandaloneAccountSource(library, registration))
                }
                requireMaterialized(materialization.readSelection(lease, intentId) ?: changed())
                beforeGrant?.takeIf { it.intent == intentId }?.completed = true
                return@withLock null
            }
            val frozen = beforeGrant?.takeIf { it.server == intent.serverInstanceId.value &&
                it.reserved &&
                it.selected == intent.selectedChangeIds.map { id -> id.value } &&
                (it.intent == null || it.intent == intentId) } ?: changed()
            if (frozen.completed) return@withLock null
            frozen.intent = intentId
            val selected = requireNotNull(frozen.selected)
            val registration = materialization.readSourceRegistration(lease)
                ?: materialization.registerSource(lease, AccountUuid.random(), AccountUuid.random())
            val facts = frozen.snapshot.facts.filter { it.changeId in selected }
            val snapshot = AccountSelectedSourceSnapshot(registration, facts, dependencies(facts, frozen.snapshot.dependencies))
            val grantView = frozen.view.copy(id = intentId.value)
            current = Frozen(grantView, lease, snapshot, desktopStandaloneAccountSource(library, registration),
                null, frozen.revision, frozen.cutoff)
            Triple(frozen, grantView, selected)
        } ?: return
        connectSelected(lease, dispatch.second, dispatch.third)
        serial.withLock { if (beforeGrant === dispatch.first) dispatch.first.completed = true }
    }

    private fun sameFacts(expected: List<AccountSelectedFact>, actual: List<AccountSelectedFact>): Boolean =
        expected.size == actual.size && expected.all { old -> actual.singleOrNull { it.changeId == old.changeId }?.let {
            it.kind == old.kind && it.aggregateId == old.aggregateId && it.version == old.version &&
                it.occurredAtMs == old.occurredAtMs && it.canonicalBytes.contentEquals(old.canonicalBytes)
        } == true }

    override suspend fun review(lease: AccountLease, continuation: String?): DesktopLocalDataReview = serial.withLock {
        requireOpen()
        requireDesktopStandaloneAccountOwner(library)
        val previous = synchronized(publication) { current }
        val after = if (continuation != null && continuation == previous?.view?.id) {
            val prior = previous ?: changed()
            if (prior.lease != lease || prior.next == null) changed()
            prior.next
        } else {
            if (continuation != null && continuation !in setOf(lease.profileId.value, lease.bindingCommit.value)) changed()
            null
        }
        val registration = materialization.readSourceRegistration(lease)
            ?: materialization.registerSource(lease, AccountUuid.random(), AccountUuid.random())
        val source = desktopStandaloneAccountSource(library, registration)
        val page = source.readPage(after)
        val eligible = page.facts.filter { it.eligible }.map { it.changeId }
        val snapshot = if (eligible.isEmpty()) AccountSelectedSourceSnapshot(registration, emptyList(), emptyMap())
            else source.capture(registration, eligible).use { capture ->
                if (!capture.stillExact()) changed()
                capture.snapshot
            }
        val cutoff = System.currentTimeMillis().also { require(it in 0..DesktopAccountLimits.MAX_EVENT_TIME_MS) }
        val accountLabel = publicId() ?: throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
        val view = DesktopLocalDataReview(UUID.randomUUID().toString(), accountLabel, page.facts.map { choice ->
            DesktopLocalDataFact(choice.changeId, label(choice, snapshot), choice.eligible &&
                snapshot.facts.any { it.changeId == choice.changeId && it.occurredAtMs <= cutoff })
        }, page.nextChangeId != null)
        requireOpen()
        if (revision == DesktopAccountLimits.MAX_REVISION) changed()
        synchronized(publication) {
            requireOpen()
            current = Frozen(view, lease, snapshot, source, page.nextChangeId, ++revision, cutoff)
        }
        view
    }

    override suspend fun connectSelected(lease: AccountLease, review: DesktopLocalDataReview,
        selected: List<String>): Unit = serial.withLock {
        requireOpen()
        val frozen = synchronized(publication) { current?.takeIf { it.view === review && it.lease == lease } } ?: changed()
        if (selected.size !in 1..100 || selected.distinct().size != selected.size ||
            selected.any { id -> review.facts.none { it.id == id && it.eligible } }) changed()
        val selectedFacts = frozen.snapshot.facts.filter { it.changeId in selected }
        if (selectedFacts.size != selected.size) changed()
        val expectedDependencies = dependencies(selectedFacts, frozen.snapshot.dependencies)
        val expected = AccountSelectedSourceSnapshot(frozen.snapshot.source, selectedFacts, expectedDependencies)
        val guarded = object : DesktopSelectedSourcePort {
            override suspend fun capture(source: AccountSourceRegistration,
                selectedChangeIds: List<String>): AccountSelectedSourceCapture {
                requireOpen()
                if (source != expected.source || selectedChangeIds.toSet() != selected.toSet() ||
                    selectedChangeIds.size != selected.size) changed()
                val actual = frozen.source.capture(source, selectedChangeIds)
                try {
                    val observed = actual.snapshot
                    if (!actual.stillExact() || observed.source != expected.source ||
                        observed.dependencies != expected.dependencies || observed.facts.size != expected.facts.size ||
                        expected.facts.any { old -> observed.facts.singleOrNull { it.changeId == old.changeId }?.let {
                            it.kind == old.kind && it.aggregateId == old.aggregateId && it.version == old.version &&
                                it.occurredAtMs == old.occurredAtMs && it.canonicalBytes.contentEquals(old.canonicalBytes)
                        } != true }) changed()
                    return actual
                } catch (failure: Throwable) { actual.close(); throw failure }
            }
        }
        val selection = AccountUuid(review.id)
        val consent = AccountSelectionConsent(selection, frozen.revision, frozen.cutoff, lease.bindingCommit,
            lease.authorityRevision, selected.toList())
        materialization.copySelected(lease, consent, expected.source, guarded)
        requireOpen()
        materialization.materializeSelected(lease, selection, frozen.revision, guarded)
        requireMaterialized(materialization.readSelection(lease, selection) ?: changed())
        synchronized(publication) { if (current === frozen) current = null }
    }

    private fun requireMaterialized(result: AccountSelectionStatus): Unit {
        if (result.rawState != "MATERIALIZED" || result.facts.any {
                it.rawState != "MATERIALIZED" && !(it.kind == "TRACK_TEXT_PROVENANCE_UPDATED" &&
                    it.rawState == "SUPPORT_ONLY" && it.reason == "SELECTED_MANUAL_METADATA_PROVENANCE")
            }) {
            throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        }
    }

    private fun dependencies(facts: List<AccountSelectedFact>, all: Map<String, JsonObject>): Map<String, JsonObject> {
        val ids = LinkedHashSet<String>()
        fun collect(value: JsonElement, depth: Int = 0) {
            require(depth <= 8)
            when (value) {
                is JsonObject -> value.forEach { (key, child) ->
                    if (key in setOf("track_id", "playlist_id", "entry_id"))
                        (child as? JsonPrimitive)?.takeIf { it.isString }?.content?.let { ids.add(it) }
                    if (child is JsonObject || child is JsonArray) collect(child, depth + 1)
                }
                is JsonArray -> value.forEach { collect(it, depth + 1) }
                else -> Unit
            }
            require(ids.size <= 100)
        }
        facts.forEach { collect(Json.parseToJsonElement(it.canonicalBytes.toString(Charsets.UTF_8))) }
        var inspected = emptySet<String>()
        while (inspected != ids) {
            val pending = ids - inspected
            inspected = ids.toSet()
            pending.forEach { all[it]?.let { snapshot -> collect(snapshot) } }
        }
        return all.filterKeys { it in ids }
    }

    private fun label(choice: StandaloneAccountFactChoice, snapshot: AccountSelectedSourceSnapshot): String =
        label(choice, snapshot.facts, snapshot.dependencies)

    private fun label(choice: StandaloneAccountFactChoice, facts: List<AccountSelectedFact>, dependencies: Map<String, JsonObject>): String {
        val ru = language() == DesktopLanguage.Russian
        val kind = when {
            choice.kind == "TRACK_IMPORTED" -> if (ru) "Трек" else "Track"
            choice.kind == "TRACK_PREFERENCE_UPDATED" -> if (ru) "Оценка трека" else "Track preference"
            choice.kind == "TRACK_METADATA_UPDATED" -> if (ru) "Название и исполнитель" else "Title and artist"
            choice.kind == "TRACK_TEXT_PROVENANCE_UPDATED" -> if (ru) "Подтверждение изменения" else "Change evidence"
            choice.kind.startsWith("TRACK_") -> if (ru) "Состав библиотеки" else "Library membership"
            choice.kind.startsWith("PLAYLIST_ENTRY_") -> if (ru) "Элемент плейлиста" else "Playlist entry"
            choice.kind.startsWith("PLAYLIST_") -> if (ru) "Плейлист" else "Playlist"
            else -> if (ru) "Неподдерживаемая запись" else "Unsupported record"
        }
        val fact = facts.singleOrNull { it.changeId == choice.changeId } ?: return kind
        val body = (Json.parseToJsonElement(fact.canonicalBytes.toString(Charsets.UTF_8)) as? JsonObject)
            ?.get("snapshot") as? JsonObject
        val title = (body?.get("title") as? JsonPrimitive)?.content
            ?: (body?.get("name") as? JsonPrimitive)?.content
            ?: (dependencies[fact.aggregateId]?.get("title") as? JsonPrimitive)?.content
        return if (title.isNullOrBlank()) kind else "$kind: $title"
    }

    private fun requireOpen(): Unit {
        if (retired.get()) throw AccountStoreException(AccountFailure.ACCOUNT_CLOSED)
    }
    private fun changed(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    override fun close(): Unit = synchronized(publication) { retired.set(true); current = null; beforeGrant = null }
}
