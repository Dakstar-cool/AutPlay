package app.autplay.download

/**
 * Framework-free download policy.  Media3 remains the only authority for execution and byte
 * progress; these values intentionally contain no byte counters or media URLs.
 */
enum class DownloadStorageClass {
    PINNED,
    USER_DOWNLOAD,
    PROACTIVE_CACHE,
    STREAM_CACHE,
}

enum class DownloadIntentState {
    REQUESTED,
    QUEUED,
    DOWNLOADING,
    PAUSED,
    COMPLETED,
    FAILED,
    CANCELED,
}

enum class Media3DownloadState {
    QUEUED,
    DOWNLOADING,
    STOPPED,
    COMPLETED,
    FAILED,
    REMOVING,
}

/** Stable, non-sensitive values suitable for the Room failure_code column and UI mapping. */
enum class DownloadFailureCode {
    STORAGE_FULL,
    AUTH_EXPIRED,
    AUTHORIZATION_DENIED,
    NOT_FOUND,
    NETWORK,
    HTTP_SERVER,
    MALFORMED_MEDIA,
    CANCELED,
    UNKNOWN,
}

data class DownloadIntentSnapshot(
    val intentId: String,
    val localUserTrackRefId: String,
    val media3DownloadId: String?,
    val storageClass: DownloadStorageClass,
    val state: DownloadIntentState,
    val failureCode: DownloadFailureCode? = null,
) {
    init {
        require(intentId.isNotBlank())
        require(localUserTrackRefId.isNotBlank())
        require(media3DownloadId.isNullOrBlank() || media3DownloadId.length <= 128)
    }
}

/** Minimal adapter-owned view of DownloadIndex. It deliberately has no progress fields. */
data class Media3DownloadSnapshot(
    val downloadId: String,
    val state: Media3DownloadState,
    val failure: DownloadFailureSignal? = null,
) {
    init {
        require(downloadId.isNotBlank())
        require(state != Media3DownloadState.FAILED || failure != null) {
            "A failed Media3 download must carry a classified input."
        }
    }
}

data class DownloadIndexSnapshot(val downloadsById: Map<String, Media3DownloadSnapshot>) {
    init {
        require(downloadsById.all { (id, item) -> id == item.downloadId })
    }
}

/**
 * Sanitized facts from the adapter. Never put exception text, signed URLs, or tokens here.
 * Media3/HTTP exception mapping belongs at the adapter edge.
 */
data class DownloadFailureSignal(
    val storageFull: Boolean = false,
    val authExpired: Boolean = false,
    val authorizationDenied: Boolean = false,
    val notFound: Boolean = false,
    val network: Boolean = false,
    val httpServer: Boolean = false,
    val malformedMedia: Boolean = false,
    val canceled: Boolean = false,
)

object DownloadFailureClassifier {
    fun classify(signal: DownloadFailureSignal): DownloadFailureCode = when {
        signal.storageFull -> DownloadFailureCode.STORAGE_FULL
        signal.authExpired -> DownloadFailureCode.AUTH_EXPIRED
        signal.authorizationDenied -> DownloadFailureCode.AUTHORIZATION_DENIED
        signal.notFound -> DownloadFailureCode.NOT_FOUND
        signal.network -> DownloadFailureCode.NETWORK
        signal.httpServer -> DownloadFailureCode.HTTP_SERVER
        signal.malformedMedia -> DownloadFailureCode.MALFORMED_MEDIA
        signal.canceled -> DownloadFailureCode.CANCELED
        else -> DownloadFailureCode.UNKNOWN
    }
}

sealed interface Media3Command {
    data class Enqueue(val downloadId: String, val intentId: String) : Media3Command
    data class Remove(val downloadId: String) : Media3Command
    data object None : Media3Command
}

data class DownloadReconciliation(
    val projectedState: DownloadIntentState,
    val failureCode: DownloadFailureCode?,
    val command: Media3Command,
) {
    /** True when applying this result would change the durable intent projection. */
    fun changes(intent: DownloadIntentSnapshot): Boolean =
        intent.state != projectedState || intent.failureCode != failureCode
}

/**
 * Reconciles a Room *intent* with the Media3 index. The index is authoritative whenever an ID is
 * assigned: Room never synthesizes completion/progress from its previous projection.
 */
object DownloadIntentReconciler {
    fun reconcile(intent: DownloadIntentSnapshot, index: DownloadIndexSnapshot): DownloadReconciliation {
        val downloadId = intent.media3DownloadId ?: intent.intentId
        if (intent.state == DownloadIntentState.CANCELED) {
            return if (index.downloadsById.containsKey(downloadId)) {
                DownloadReconciliation(DownloadIntentState.CANCELED, DownloadFailureCode.CANCELED, Media3Command.Remove(downloadId))
            } else {
                DownloadReconciliation(DownloadIntentState.CANCELED, DownloadFailureCode.CANCELED, Media3Command.None)
            }
        }

        val observed = index.downloadsById[downloadId]
        if (observed == null) return DownloadReconciliation(
            DownloadIntentState.REQUESTED,
            null,
            Media3Command.Enqueue(downloadId, intent.intentId),
        )
        return when (observed.state) {
            Media3DownloadState.QUEUED -> DownloadReconciliation(DownloadIntentState.QUEUED, null, Media3Command.None)
            Media3DownloadState.DOWNLOADING -> DownloadReconciliation(DownloadIntentState.DOWNLOADING, null, Media3Command.None)
            Media3DownloadState.STOPPED -> DownloadReconciliation(DownloadIntentState.PAUSED, null, Media3Command.None)
            Media3DownloadState.COMPLETED -> DownloadReconciliation(DownloadIntentState.COMPLETED, null, Media3Command.None)
            Media3DownloadState.FAILED -> DownloadReconciliation(
                DownloadIntentState.FAILED,
                DownloadFailureClassifier.classify(checkNotNull(observed.failure)),
                Media3Command.None,
            )
            Media3DownloadState.REMOVING -> DownloadReconciliation(DownloadIntentState.CANCELED, DownloadFailureCode.CANCELED, Media3Command.None)
        }
    }
}

data class StoragePolicy(
    val managedQuotaBytes: Long,
    val minimumFreeBytes: Long,
) {
    init {
        require(managedQuotaBytes > 0)
        require(minimumFreeBytes >= 0)
    }
}

data class CacheEntry(
    val id: String,
    val storageClass: DownloadStorageClass,
    val byteSize: Long,
    val lastAccessedAtMs: Long,
) {
    init {
        require(id.isNotBlank())
        require(byteSize > 0)
        require(lastAccessedAtMs >= 0)
    }
}

sealed interface StorageAdmission {
    data object Allowed : StorageAdmission
    data class Evict(val entries: List<CacheEntry>) : StorageAdmission
    data class Rejected(val failureCode: DownloadFailureCode = DownloadFailureCode.STORAGE_FULL) : StorageAdmission
}

/** Explicit quota and free-space gate with deterministic least-recently-used eviction ordering. */
object DownloadStoragePolicy {
    fun admit(
        requestClass: DownloadStorageClass,
        requestedBytes: Long,
        managedBytesInUse: Long,
        freeBytes: Long,
        entries: List<CacheEntry>,
        policy: StoragePolicy,
    ): StorageAdmission {
        require(requestedBytes > 0)
        require(managedBytesInUse >= 0)
        require(freeBytes >= 0)
        val requiredByQuota = (managedBytesInUse + requestedBytes - policy.managedQuotaBytes).coerceAtLeast(0)
        val requiredByFreeSpace = (policy.minimumFreeBytes + requestedBytes - freeBytes).coerceAtLeast(0)
        val requiredBytes = maxOf(requiredByQuota, requiredByFreeSpace)
        if (requiredBytes == 0L) return StorageAdmission.Allowed

        val candidates = entries.asSequence()
            .filter { isEvictable(it.storageClass) }
            .sortedWith(compareBy<CacheEntry>({ evictionRank(it.storageClass) }, { it.lastAccessedAtMs }, { it.id }))
            .toList()
        val selected = ArrayList<CacheEntry>()
        var released = 0L
        for (entry in candidates) {
            selected += entry
            released += entry.byteSize
            if (released >= requiredBytes) return StorageAdmission.Evict(selected)
        }
        return StorageAdmission.Rejected()
    }

    private fun isEvictable(existing: DownloadStorageClass): Boolean = when (existing) {
        DownloadStorageClass.STREAM_CACHE, DownloadStorageClass.PROACTIVE_CACHE -> true
        DownloadStorageClass.USER_DOWNLOAD -> false
        DownloadStorageClass.PINNED -> false
    }

    private fun evictionRank(storageClass: DownloadStorageClass): Int = when (storageClass) {
        DownloadStorageClass.STREAM_CACHE -> 0
        DownloadStorageClass.PROACTIVE_CACHE -> 1
        DownloadStorageClass.USER_DOWNLOAD -> 2
        DownloadStorageClass.PINNED -> 3
    }
}
