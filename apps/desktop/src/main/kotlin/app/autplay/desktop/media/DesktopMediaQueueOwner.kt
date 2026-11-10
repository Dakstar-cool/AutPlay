package app.autplay.desktop.media

import app.autplay.application.desktop.*
import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

/** Ordered persistence effects only. Logical queue/listen/timer reduction stays in the actor. */
internal class DesktopMediaQueueOwner(
    private val standalone: DesktopLibraryStore,
    private val domain: DesktopAccountDomain,
    private val admission: DesktopMediaAccountAdmission,
    work: DesktopMediaWorkOwner,
) {
    private class Effect(val queue: DesktopQueue, val event: DesktopListeningEvent?, val eventQueue: DesktopQueue?,
        val lease: AccountLease?, val taste: Pair<Boolean, Boolean>?, val duration: Pair<String, Long>?,
        val processOnly: Boolean, val eventProcessOnly: Boolean, val settled: CompletableDeferred<Unit>,
        val renewal: CompletableDeferred<AccountLease>?)
    private val gate = Any()
    private val closing = AtomicBoolean(false)
    private val effects = Channel<Effect>(64)
    private var latest = CompletableDeferred<Unit>().apply { complete(Unit) }
    private var renewal: Pair<AccountLease, CompletableDeferred<AccountLease>>? = null
    private val knownQueues = LinkedHashMap<String, DesktopQueue>()
    private val copyQueues = LinkedHashSet<String>()
    private val durations = LinkedHashMap<String, Long>()
    @Volatile private var failed: Exception? = null
    var onFailure: (DesktopQueue, Exception) -> Unit = { _, _ -> }
    var onCommitted: (AccountLease?, DesktopQueue) -> Unit = { _, _ -> }

    init { work.scope.launch {
        for (effect in effects) {
            try {
                failed?.let { throw it }
                val selected = effect.renewal?.await() ?: effect.lease
                if (effect.duration != null) {
                    if (selected == null) standalone.updateTrackDuration(effect.duration.first, effect.duration.second)
                } else if (selected == null) {
                    effect.taste?.let { (listen, excluded) ->
                        val confirmed = if (listen) standalone.setCurrentListenTasteExcluded(effect.queue.id,
                            requireNotNull(effect.queue.currentEntryId), requireNotNull(effect.queue.sessionId), excluded)
                        else standalone.setQueueSessionTasteExcluded(effect.queue.id, excluded)
                        mediaCheck(confirmed != null, MediaCode.INTENT_STALE, MediaStage.RESOLVE)
                    }
                    standalone.saveQueue(effect.queue, effect.event)
                } else persistAccount(effect, selected)
                if (effect.duration == null) onCommitted(selected, effect.queue)
                effect.settled.complete(Unit)
            } catch (failure: Exception) {
                failed = failure
                effect.settled.completeExceptionally(failure)
                onFailure(effect.queue, failure)
            }
        }
    } }

    fun enqueue(queue: DesktopQueue, event: DesktopListeningEvent?, taste: Pair<Boolean, Boolean>? = null,
        duration: Pair<String, Long>? = null, copySelection: DesktopMediaCopySelection? = null): Unit = synchronized(gate) {
        mediaCheck(!closing.get() && failed == null, MediaCode.OWNER_UNAVAILABLE, MediaStage.RESOLVE)
        val copy = queue.copy(entries = queue.entries.toList(), shuffleOrder = queue.shuffleOrder.toList())
        val eventQueue = event?.let { e -> knownQueues.values.lastOrNull { q ->
            q.sessionId == e.id && q.entries.any { it.id == e.queueEntryId && it.trackId == e.trackId }
        } ?: copy.takeIf { it.sessionId == e.id && it.entries.any { row -> row.id == e.queueEntryId && row.trackId == e.trackId } } }
        val completion = CompletableDeferred<Unit>()
        val selected = admission.selectedLeaseOrNull()
        val processOnly = copySelection != null || queue.id in copyQueues
        mediaCheck(!processOnly || selected != null, MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
        val effect = Effect(copy, event?.copy(), eventQueue, selected, taste, duration,
            processOnly, eventQueue?.id?.let { it in copyQueues } == true, completion,
            renewal?.takeIf { it.first == selected }?.second)
        mediaCheck(effects.trySend(effect).isSuccess, MediaCode.CAPACITY_EXCEEDED, MediaStage.RESOLVE)
        latest = completion
        knownQueues.remove(copy.id); knownQueues[copy.id] = copy
        if (processOnly) copyQueues.add(copy.id)
        while (knownQueues.size > 2) knownQueues.keys.first().let { knownQueues.remove(it); copyQueues.remove(it) }
    }

    suspend fun fenceForRenewal(expected: AccountLease): Unit {
        val prior = synchronized(gate) {
            mediaCheck(!closing.get() && failed == null && renewal == null, MediaCode.OWNER_STALE, MediaStage.AUTH)
            renewal = expected to CompletableDeferred()
            latest
        }
        prior.await()
    }
    fun completeRenewal(expected: AccountLease, fresh: AccountLease): Unit = synchronized(gate) {
        val pending = renewal ?: throw mediaFailure(MediaCode.OWNER_STALE, MediaStage.AUTH)
        mediaCheck(pending.first == expected, MediaCode.OWNER_STALE, MediaStage.AUTH)
        renewal = null; pending.second.complete(fresh)
    }
    fun abortRenewal(): Unit = synchronized(gate) {
        renewal?.second?.completeExceptionally(mediaFailure(MediaCode.OWNER_STALE, MediaStage.AUTH)); renewal = null
    }

    suspend fun awaitPending(): Unit {
        while (true) {
            val observed = synchronized(gate) { latest }
            observed.await()
            if (synchronized(gate) { latest === observed }) return
        }
    }

    fun duration(queue: DesktopQueue, trackId: String, durationMs: Long): Unit = synchronized(gate) {
        if (admission.hasSelectedLease() || durations[trackId] == durationMs) return@synchronized
        enqueue(queue, null, duration = trackId to durationMs)
        durations[trackId] = durationMs
        while (durations.size > DesktopLocalLimits.MAX_QUEUE_ENTRIES) durations.remove(durations.keys.first())
    }

    private suspend fun persistAccount(effect: Effect, lease: AccountLease): Unit {
        val event = effect.event.takeUnless { effect.eventProcessOnly }
        if (effect.processOnly) {
            // An original copy may belong to another retained scope and have no UTR in the
            // selected ROOT scope. Keep this explicit copy queue/listen process-only; never
            // fabricate a Track FK, authenticated listening event or standalone Room row.
            if (event != null && event.id != effect.queue.sessionId) checkpoint(lease, event,
                effect.eventQueue ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.RESOLVE))
            return
        }
        val currentListen = event != null && event.id == effect.queue.sessionId &&
            effect.queue.entries.any { it.id == event.queueEntryId && it.trackId == event.trackId }
        if (event != null && !currentListen) checkpoint(lease, event,
            effect.eventQueue ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.RESOLVE))
        val id = AccountUuid(effect.queue.id)
        val previous = domain.readQueue(lease, id)
        val ordered = if (effect.queue.shuffleEnabled) effect.queue.shuffleOrder.map { entry ->
            effect.queue.entries.single { it.id == entry }
        } else effect.queue.entries
        val next = AccountQueueSnapshot(id, DesktopMediaLimits.next(previous?.revision ?: 0),
            ordered.mapIndexed { position, entry -> AccountQueueEntry(AccountUuid(entry.id), AccountUuid(entry.trackId), position) },
            effect.queue.repeatMode, effect.queue.shuffleEnabled, effect.queue.sessionId?.let(::AccountUuid),
            effect.queue.currentEntryId?.let(::AccountUuid))
        domain.saveQueue(lease, AccountUuid.random(), previous?.revision ?: 0, next)
        if (event != null && currentListen) checkpoint(lease, event, effect.queue)
    }

    private suspend fun checkpoint(lease: AccountLease, event: DesktopListeningEvent, queue: DesktopQueue): Unit {
        val id = AccountUuid(event.id)
        val old = domain.readCheckpoint(lease, id)
        val excluded = event.sessionTasteState == "EXCLUDED" || event.listenTasteState == "EXCLUDED"
        mediaCheck(event.sessionTasteState in setOf("INCLUDED", "EXCLUDED") && event.listenTasteState in setOf("INCLUDED", "EXCLUDED"),
            MediaCode.INTENT_STALE, MediaStage.RESOLVE)
        if (old?.finalized == true) {
            mediaCheck(event.finalized && old.playedMs == event.playedMs && old.positionMs == event.positionMs &&
                old.excludedFromTaste == excluded, MediaCode.INTENT_STALE, MediaStage.RESOLVE)
            return
        }
        val checkpoint = AccountListeningCheckpoint(id, AccountUuid(queue.id), AccountUuid(event.queueEntryId), id,
            AccountUuid(event.trackId), event.startedAtMs, event.playedMs, event.positionMs,
            DesktopMediaLimits.next(old?.revision ?: 0), old?.origin ?: if (queue.queueType == "PLAYLIST") "PLAYLIST" else "ORGANIC",
            old?.context ?: "GENERAL", excluded, event.finalized)
        // A mutable/final-time Recording projection must never relabel the earlier listen.
        // The existing v1 language explicitly permits absent remote/Recording observations.
        val final = if (!event.finalized) null else buildJsonObject {
            put("interaction_type", "LISTENING_EVENT_RECORDED"); put("local_user_track_ref_id", event.trackId)
            put("server_user_track_ref_id", JsonNull); put("recording_id", JsonNull)
            put("played_ms", event.playedMs); put("track_duration_ms", JsonNull); put("completion_ratio", JsonNull)
            put("event_origin", checkpoint.origin); put("context", checkpoint.context)
            put("explicit_feedback", "NONE"); put("excluded_from_taste", excluded); put("recommendation", JsonNull)
        }
        domain.checkpoint(lease, if (event.finalized) id else AccountUuid.random(), old?.revision ?: 0, checkpoint, final)
    }

    suspend fun close(): Unit = withContext(NonCancellable) {
        synchronized(gate) { closing.set(true); abortRenewal(); effects.close() }
        awaitPending()
    }
}
