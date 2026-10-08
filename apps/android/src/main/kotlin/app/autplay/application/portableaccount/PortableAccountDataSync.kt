package app.autplay.application.portableaccount

import java.util.concurrent.TimeUnit
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext

enum class PortableAccountDataSyncOutcome { COMPLETE, CONTINUE }

/** One finite pass: bounded capture, upload, six remote sources, then a final upload. */
class PortableAccountDataSync(
    private val repository: PortableAccountDataRepository,
    private val transport: PortableAccountDataTransport,
    private val monotonicNanos: () -> Long = System::nanoTime,
    private val maxPages: Int = 12,
    private val deadlineMs: Long = 60_000,
) {
    init { require(maxPages in 1..32 && deadlineMs in 1..90_000) }

    suspend fun run(accountId: String, profileId: String): PortableAccountDataSyncOutcome {
        PortableAccountDataCodec.uuid(accountId); PortableAccountDataCodec.uuid(profileId)
        repository.beginPass(accountId, profileId)
        val scopes = repository.scopes(accountId)
        check(scopes.size <= 32 && scopes.any { it.profileId == profileId }) { "ACCOUNT_SCOPE_REQUIRED" }
        var pages = 0
        val started = monotonicNanos()
        fun exhausted(): Boolean = pages >= maxPages || monotonicNanos() - started >= TimeUnit.MILLISECONDS.toNanos(deadlineMs)
        for (scope in scopes.sortedBy { it.profileId }) for (kind in PortableAccountDataRepository.localKinds) {
            while (!repository.cursor(accountId, profileId, "local:${scope.profileId}:$kind").complete) {
                coroutineContext.ensureActive()
                if (exhausted()) return PortableAccountDataSyncOutcome.CONTINUE
                repository.capturePage(accountId, profileId, scope, kind); pages++
            }
        }
        suspend fun upload(): Boolean {
            while (true) {
                coroutineContext.ensureActive()
                val records = repository.uploadPage(accountId, profileId)
                if (records.isEmpty()) return true
                if (exhausted()) return false
                transport.upload(accountId, records.map(PortableAccountDataRepository::decode))
                repository.acknowledgeUpload(accountId, profileId, records.maxOf { it.revision }); pages++
            }
        }
        if (!upload()) return PortableAccountDataSyncOutcome.CONTINUE
        for (source in PortableAccountDataCodec.sources) {
            while (true) {
                val cursor = repository.cursor(accountId, profileId, "remote:$source")
                if (cursor.complete) break
                coroutineContext.ensureActive()
                if (exhausted()) return PortableAccountDataSyncOutcome.CONTINUE
                val page = transport.download(accountId, source, PortableDataCursor(cursor.cursorTime, cursor.cursorId))
                repository.applyPage(accountId, profileId, source, page); pages++
            }
        }
        // A full last download page can exhaust the budget. Persisted cursors still require
        // continuation until the final upload is acknowledged, even when all sources are complete.
        if (!upload()) return PortableAccountDataSyncOutcome.CONTINUE
        repository.finishPass(accountId, profileId)
        return PortableAccountDataSyncOutcome.COMPLETE
    }
}
