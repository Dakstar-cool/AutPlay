package app.autplay.data.local

import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.LocalId
import app.autplay.work.DeferredWorkKind
import app.autplay.work.DeferredWorkRequest
import app.autplay.work.DeferredWorkScheduler
import app.autplay.work.DeferredWorkSubject

/** Room adapter for the exact F-018 transaction; unsupported outbox families remain local-only. */
class RoomM5LocalIntentMaterializer(
    private val database: AutPlayDatabase,
    private val repository: LibraryVerticalSliceRepository,
    private val syncScheduler: DeferredWorkScheduler? = null,
) : M5LocalIntentMaterializer {
    override suspend fun pending(limit: Int): List<PendingLocalIntentSummary> {
        require(limit in 1..MAX_SELECTION)
        return database.journalDao().pendingOutbox(MAX_SELECTION)
            .asSequence()
            .filter { (it.eventType to it.aggregateType) in SUPPORTED_EVENTS }
            .take(limit)
            .map { PendingLocalIntentSummary(LocalId(it.localChangeId), it.eventType, it.occurredAtMs) }
            .toList()
    }

    override suspend fun materialize(
        binding: ClientEventBinding,
        localChangeId: LocalId,
        eventId: LocalId,
        materializedAtMs: Long,
    ): LocalId {
        val source = database.journalDao().outbox(localChangeId.value)
            ?: error("OUTBOX_NOT_FOUND")
        source.materializedEventId?.let { existingId ->
            val existing = database.journalDao().event(existingId) ?: error("OUTBOX_MATERIALIZATION_CORRUPT")
            check(source.materializationState == "MATERIALIZED") { "OUTBOX_MATERIALIZATION_CORRUPT" }
            return LocalId(existing.eventId)
        }
        check((source.eventType to source.aggregateType) in SUPPORTED_EVENTS) {
            "OUTBOX_UNSUPPORTED_EVENT"
        }
        val result = repository.materializeStandalone(binding, localChangeId, eventId, materializedAtMs)
        syncScheduler?.enqueue(
            DeferredWorkRequest(
                DeferredWorkKind.SYNC,
                DeferredWorkSubject.Device(binding.deviceId),
                binding.serverProfileId,
            ),
        )
        return result.changeId
    }

    private companion object {
        const val MAX_SELECTION = 100
        val SUPPORTED_EVENTS = setOf(
            "USER_TRACK_REF_CREATED" to "USER_TRACK_REF",
            "LIBRARY_ENTRY_UPSERTED" to "LIBRARY_ENTRY",
            "AGGREGATE_DELETED" to "LIBRARY_ENTRY",
            "USER_TRACK_PREFERENCE_SET" to "USER_TRACK_PREFERENCE",
            "PLAYLIST_CREATED" to "PLAYLIST",
            "PLAYLIST_METADATA_PATCHED" to "PLAYLIST",
            "AGGREGATE_DELETED" to "PLAYLIST",
            "PLAYLIST_ENTRY_UPSERTED" to "PLAYLIST_ENTRY",
            "PLAYLIST_ENTRY_MOVED" to "PLAYLIST_ENTRY",
            "AGGREGATE_DELETED" to "PLAYLIST_ENTRY",
            "LISTENING_EVENT_RECORDED" to "LISTENING_EVENT",
        )
    }
}
