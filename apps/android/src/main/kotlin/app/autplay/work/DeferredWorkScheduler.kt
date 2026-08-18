package app.autplay.work

import androidx.work.Data
import androidx.work.Constraints
import androidx.work.NetworkType
import androidx.work.BackoffPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.ListenableWorker
import androidx.work.OneTimeWorkRequest
import androidx.work.WorkManager
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId

/** Kinds of finite deferred work owned by WorkManager, never media byte transfer. */
enum class DeferredWorkKind {
    SYNC,
    LOCAL_RESCAN,
    METADATA_REFRESH,
}

/** A stable Room/domain identity that a worker can resolve after process recreation. */
sealed interface DeferredWorkSubject {
    val value: String
    val kind: String

    data class LocalAggregate(val id: LocalId) : DeferredWorkSubject {
        override val value: String = id.value
        override val kind: String = "local_aggregate"
    }

    data class Device(val id: DeviceId) : DeferredWorkSubject {
        override val value: String = id.value
        override val kind: String = "device"
    }
}

/**
 * A deliberately small WorkManager payload. Domain data stays in Room; network transport is owned
 * by P09, not this scheduling seam.
 */
data class DeferredWorkRequest(
    val kind: DeferredWorkKind,
    val subject: DeferredWorkSubject,
    val serverProfileId: ServerProfileId? = null,
)

/** Port allowing application code to schedule durable metadata/sync work without WorkManager APIs. */
interface DeferredWorkScheduler {
    fun enqueue(request: DeferredWorkRequest)
}

/** WorkManager adapter which passes only typed stable IDs through input [Data]. */
class WorkManagerDeferredWorkScheduler(
    private val workManager: WorkManager,
    private val workerClass: Class<out ListenableWorker>,
) : DeferredWorkScheduler {
    override fun enqueue(request: DeferredWorkRequest) {
        val workRequest = OneTimeWorkRequest.Builder(workerClass)
            .setInputData(request.toInputData())
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, java.time.Duration.ofSeconds(10))
            .addTag("autplay-deferred-${request.kind.name.lowercase()}")
            .build()
        workManager.enqueueUniqueWork(
            request.uniqueName(),
            ExistingWorkPolicy.KEEP,
            workRequest,
        )
    }
}

/** Parses the stable-ID-only input supplied by [WorkManagerDeferredWorkScheduler]. */
object DeferredWorkInput {
    fun read(data: Data): DeferredWorkRequest? = runCatching {
        val kind = DeferredWorkKind.valueOf(data.getString(KEY_KIND) ?: return null)
        val subjectValue = data.getString(KEY_SUBJECT_VALUE) ?: return null
        val subject = when (data.getString(KEY_SUBJECT_KIND)) {
            SUBJECT_LOCAL_AGGREGATE -> DeferredWorkSubject.LocalAggregate(LocalId(subjectValue))
            SUBJECT_DEVICE -> DeferredWorkSubject.Device(DeviceId(subjectValue))
            else -> return null
        }
        DeferredWorkRequest(
            kind = kind,
            subject = subject,
            serverProfileId = data.getString(KEY_SERVER_PROFILE_ID)?.let(::ServerProfileId),
        )
    }.getOrNull()

    internal const val KEY_KIND = "autplay.work.kind"
    internal const val KEY_SUBJECT_KIND = "autplay.work.subject_kind"
    internal const val KEY_SUBJECT_VALUE = "autplay.work.subject_value"
    internal const val KEY_SERVER_PROFILE_ID = "autplay.work.server_profile_id"
    internal const val SUBJECT_LOCAL_AGGREGATE = "local_aggregate"
    internal const val SUBJECT_DEVICE = "device"
}

private fun DeferredWorkRequest.toInputData(): Data = Data.Builder()
    .putString(DeferredWorkInput.KEY_KIND, kind.name)
    .putString(DeferredWorkInput.KEY_SUBJECT_KIND, subject.kind)
    .putString(DeferredWorkInput.KEY_SUBJECT_VALUE, subject.value)
    .apply { serverProfileId?.let { putString(DeferredWorkInput.KEY_SERVER_PROFILE_ID, it.value) } }
    .build()

private fun DeferredWorkRequest.uniqueName(): String = buildString {
    append("autplay-deferred-")
    append(kind.name.lowercase())
    append('-')
    append(subject.kind)
    append('-')
    append(subject.value)
    serverProfileId?.let { append('-').append(it.value) }
}
