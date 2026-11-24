package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.desktop.account.AccountUuid
import java.util.prefs.Preferences
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

internal data class CatalogTarget(val kind: String, val id: AccountUuid, val alternatives: Boolean) {
    init { require(kind in setOf("artist", "recording", "release") && (!alternatives || kind == "artist")) }
    val slot get() = "$kind-${id.value}-${if (alternatives) "all" else "standard"}"
}
internal data class CatalogProgress(val job: CatalogJob? = null, val running: Boolean = false, val error: Boolean = false)

/** One durable user decision per operation. Replaying a preview never grants approval to start. */
internal class DesktopCatalogDownloads(
    private val scope: CoroutineScope,
    private val current: () -> AccountLease?,
    private val preview: suspend (AccountLease, AccountUuid, String, AccountUuid, Boolean) -> CatalogJob,
    private val action: suspend (AccountLease, AccountUuid, String) -> CatalogJob,
    private val sync: suspend (AccountLease) -> Unit,
    private val storage: Preferences = Preferences.userRoot().node("app/autplay/catalog-work"),
) {
    private val workers = mutableMapOf<Pair<AccountLease, CatalogTarget>, Job>()
    private val slots = Semaphore(2)
    val states = MutableStateFlow<Map<Pair<AccountLease, CatalogTarget>, CatalogProgress>>(emptyMap())

    private fun journal(lease: AccountLease) = storage.node("${lease.storeId.value}/${lease.scopeId.value}/${lease.profileId.value}")
    private fun requireOwner(lease: AccountLease) = check(current() == lease) { "ACCOUNT_SCOPE_CHANGED" }

    fun restore(lease: AccountLease) {
        scope.launch {
            try {
                requireOwner(lease)
                val prefs = journal(lease)
                for (key in prefs.keys()) {
                    val kind = key.substringBefore('-')
                    val id = key.removePrefix("$kind-").substringBeforeLast('-')
                    val target = runCatching { CatalogTarget(kind, AccountUuid(id), key.endsWith("-all")) }.getOrNull() ?: continue
                    if (target.slot != key) continue
                    val raw = prefs.get(key, null) ?: continue
                    if (raw.endsWith("|preview") || raw.endsWith("|start")) submit(lease, target)
                }
            } catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { /* Restore is retried on the next attachment. */ }
        }
    }

    fun submit(lease: AccountLease, target: CatalogTarget, start: Boolean = false, restart: Boolean = false) {
        val key = lease to target
        synchronized(workers) {
            if (workers[key]?.isActive == true || current() != lease) return
            fun publish(progress: CatalogProgress) { states.update { it + (key to progress) } }
            publish(CatalogProgress(states.value[key]?.job, running = true))
            val worker = scope.launch(start = CoroutineStart.LAZY) {
                var state: CatalogJob? = states.value[key]?.job
                try {
                    requireOwner(lease)
                    val prefs = journal(lease)
                    val saved = prefs.get(target.slot, null) ?: legacyOperation(lease, target)?.let { "$it|preview" }
                    val previous = saved?.substringBefore('|')?.let(::AccountUuid)
                    val phase = saved?.substringAfter('|')
                    val operation = if (previous == null || (restart && phase == "done")) AccountUuid.random() else previous
                    val approved = start || phase == "start"
                    val intent = if (approved) "start" else "preview"
                    if (phase != "done" || operation != previous) {
                        prefs.put(target.slot, "${operation.value}|$intent")
                        prefs.flush()
                    }
                    var backoff = 30_000L
                    var advances = 0
                    var last: CatalogJob? = null
                    while (true) {
                        ensureActive(); requireOwner(lease)
                        try {
                            val snapshot = slots.withPermit {
                                var job = last ?: preview(lease, operation, target.kind, target.id, target.alternatives)
                                if (approved && job.phase == "preview") job = action(lease, operation, "start")
                                if (job.phase in setOf("discovery", "inspect", "downloading"))
                                    job = action(lease, operation, "advance")
                                job
                            }
                            state = snapshot
                            last = snapshot
                            requireOwner(lease)
                            val done = snapshot.phase in setOf("preview", "complete")
                            if (done) {
                                if (approved && snapshot.phase == "complete") sync(lease)
                                requireOwner(lease)
                                prefs.put(target.slot, "${operation.value}|${if (snapshot.phase == "complete") "done" else "ready"}")
                                prefs.flush()
                            }
                            if (done) {
                                val finished = currentCoroutineContext()[Job]
                                synchronized(workers) {
                                    workers.remove(key, finished)
                                    publish(CatalogProgress(snapshot))
                                }
                                break
                            }
                            publish(CatalogProgress(snapshot, running = true))
                            backoff = 30_000L
                            if (++advances >= 45) { delay(30_000); advances = 0 }
                            if (snapshot.phase == "downloading" && snapshot.counts["available"] == 0) delay(4_000)
                        } catch (cancelled: CancellationException) { throw cancelled }
                        catch (failure: Exception) {
                            requireOwner(lease)
                            if (!retryableMusicRequest(failure)) throw failure
                            last = null
                            publish(CatalogProgress(state, running = true, error = true))
                            delay(backoff); backoff = (backoff * 2).coerceAtMost(900_000)
                        }
                    }
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (_: Exception) { publish(CatalogProgress(state, error = true)) }
            }
            workers[key] = worker
            worker.invokeOnCompletion { synchronized(workers) { workers.remove(key, worker) } }
            worker.start()
        }
    }

    private fun legacyOperation(lease: AccountLease, target: CatalogTarget): String? {
        val path = "app/autplay/catalog/${lease.scopeId.value}/${target.kind}/${target.id.value}"
        val root = Preferences.userRoot()
        if (!root.nodeExists(path)) return null
        return root.node(path).get(if (target.alternatives) "all" else "standard", null)
    }

    fun hasOperation(lease: AccountLease, target: CatalogTarget): Boolean =
        current() == lease && (journal(lease).get(target.slot, null) != null || legacyOperation(lease, target) != null)
}
