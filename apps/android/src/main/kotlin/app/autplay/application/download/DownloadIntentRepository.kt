package app.autplay.application.download

import android.content.Context
import android.database.sqlite.SQLiteFullException
import android.net.Uri
import android.os.StatFs
import android.system.ErrnoException
import android.system.OsConstants
import androidx.media3.common.ParserException
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.HttpDataSource
import androidx.media3.exoplayer.offline.Download
import androidx.media3.exoplayer.offline.DownloadManager
import androidx.media3.exoplayer.offline.DownloadRequest
import androidx.media3.exoplayer.offline.DownloadService
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.DownloadIntentEntity
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.download.AutPlayDownloadService
import app.autplay.download.CacheEntry
import app.autplay.download.DownloadFailureSignal
import app.autplay.download.DownloadIndexSnapshot
import app.autplay.download.DownloadIntentReconciler
import app.autplay.download.DownloadIntentSnapshot
import app.autplay.download.DownloadIntentState
import app.autplay.download.DownloadStorageClass
import app.autplay.download.DownloadStoragePolicy
import app.autplay.download.MediaDownloadComponents
import app.autplay.download.Media3DownloadSnapshot
import app.autplay.download.Media3DownloadState
import app.autplay.download.Media3Command
import app.autplay.download.StorageAdmission
import app.autplay.download.StoragePolicy
import app.autplay.playback.AndroidPlaybackSourceResolver
import java.io.IOException
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext

/** Maps user intent to Media3 commands and reconciles only coarse state back into Room. */
@UnstableApi
class DownloadIntentRepository(
    context: Context,
    private val database: AutPlayDatabase,
) {
    private val applicationContext = context.applicationContext

    fun observeIntents(limit: Int = 100): Flow<List<DownloadIntentEntity>> =
        database.localAudioDao().observeDownloadIntents(limit.coerceIn(1, 100))

    suspend fun requestPreferredVaultDownload(
        trackRefId: LocalId,
        profileId: ServerProfileId,
        storageClass: DownloadStorageClass,
        nowMs: Long,
    ): DownloadIntentEntity {
        val states = database.localAudioDao().statesForPlayback(trackRefId.value, 32)
        val preferredState = states.firstOrNull { it.serverAudioVariantId != null }
        val variantId = preferredState?.serverAudioVariantId
            ?: database.localAudioDao().downloadIntentsForTrack(trackRefId.value, 32)
                .firstNotNullOfOrNull { it.serverAudioVariantId }
            ?: error("VAULT_VARIANT_UNKNOWN")
        return requestVaultDownload(
            trackRefId,
            profileId,
            variantId,
            storageClass,
            nowMs,
            preferredState?.byteSize,
        )
    }

    suspend fun requestVaultDownload(
        trackRefId: LocalId,
        profileId: ServerProfileId,
        audioVariantId: String,
        storageClass: DownloadStorageClass,
        nowMs: Long,
        requestedBytes: Long? = null,
    ): DownloadIntentEntity {
        require(storageClass != DownloadStorageClass.STREAM_CACHE)
        database.localAudioDao().downloadIntentsForTrack(trackRefId.value, MAX_TRACK_INTENTS)
            .firstOrNull { existing ->
                existing.serverProfileId == profileId.value &&
                    existing.serverAudioVariantId == audioVariantId &&
                    existing.desiredStorageClass == storageClass.name &&
                    existing.state !in setOf("FAILED", "CANCELLED")
            }
            ?.let { return it }
        val intentId = LocalId.random().value
        val row = DownloadIntentEntity(
            downloadIntentId = intentId,
            localUserTrackRefId = trackRefId.value,
            serverAudioVariantId = audioVariantId,
            media3DownloadId = intentId,
            desiredStorageClass = storageClass.name,
            qualityPolicy = "DIRECT_ORIGINAL",
            sourcePolicy = "VAULT_ONLY",
            state = "REQUESTED",
            failureCode = null,
            createdAtMs = nowMs,
            updatedAtMs = nowMs,
            completedAtMs = null,
            serverProfileId = profileId.value,
            lastAccessedAtMs = null,
        )
        val admission = applyStorageAdmission(storageClass, requestedBytes ?: DEFAULT_ADMISSION_BYTES)
        if (admission is StorageAdmission.Rejected) {
            val rejected = row.copy(state = "FAILED", failureCode = admission.failureCode.name)
            database.localAudioDao().upsertDownloadIntent(rejected)
            return rejected
        }
        if (admission is StorageAdmission.Evict) {
            admission.entries.forEach { entry ->
                DownloadService.sendRemoveDownload(
                    applicationContext,
                    AutPlayDownloadService::class.java,
                    entry.id,
                    false,
                )
            }
        }
        database.localAudioDao().upsertDownloadIntent(row)
        DownloadService.sendAddDownload(
            applicationContext,
            AutPlayDownloadService::class.java,
            row.toDownloadRequest(),
            false,
        )
        return row
    }

    private suspend fun applyStorageAdmission(
        requestClass: DownloadStorageClass,
        requestedBytes: Long,
    ): StorageAdmission = withContext(Dispatchers.IO) {
        val components = MediaDownloadComponents.get(applicationContext)
        val intents = database.localAudioDao().downloadIntents(MAX_RECONCILE_INTENTS)
            .associateBy { it.media3DownloadId ?: it.downloadIntentId }
        val entries = mutableListOf<CacheEntry>()
        components.downloadManager.downloadIndex.getDownloads().use { cursor ->
            while (cursor.moveToNext()) {
                val download = cursor.download
                val size = download.contentLength.takeIf { it > 0 } ?: continue
                val intent = intents[download.request.id] ?: continue
                entries += CacheEntry(
                    id = download.request.id,
                    storageClass = intent.desiredStorageClass.toStorageClass(),
                    byteSize = size,
                    lastAccessedAtMs = intent.lastAccessedAtMs ?: intent.createdAtMs,
                )
            }
        }
        val storage = StatFs(applicationContext.filesDir.absolutePath)
        val totalBytes = storage.totalBytes.coerceAtLeast(MIN_MANAGED_QUOTA_BYTES)
        DownloadStoragePolicy.admit(
            requestClass = requestClass,
            requestedBytes = requestedBytes.coerceIn(1, MAX_SINGLE_ADMISSION_BYTES),
            managedBytesInUse = components.downloadCache.cacheSpace,
            freeBytes = storage.availableBytes,
            entries = entries,
            policy = StoragePolicy(
                managedQuotaBytes = minOf(DEFAULT_MANAGED_QUOTA_BYTES, (totalBytes / 4).coerceAtLeast(MIN_MANAGED_QUOTA_BYTES)),
                minimumFreeBytes = minOf(DEFAULT_MINIMUM_FREE_BYTES, (totalBytes / 20).coerceAtLeast(MINIMUM_FREE_FLOOR_BYTES)),
            ),
        )
    }

    suspend fun reconcile(download: Download, finalException: Exception?, nowMs: Long) = withContext(Dispatchers.IO) {
        val intent = database.localAudioDao().downloadIntentByMedia3Id(download.request.id) ?: return@withContext
        val coreIntent = DownloadIntentSnapshot(
            intentId = intent.downloadIntentId,
            localUserTrackRefId = intent.localUserTrackRefId,
            media3DownloadId = intent.media3DownloadId,
            storageClass = intent.desiredStorageClass.toStorageClass(),
            state = intent.state.toIntentState(),
            failureCode = intent.failureCode?.let { code ->
                runCatching { app.autplay.download.DownloadFailureCode.valueOf(code) }.getOrNull()
            },
        )
        val media = download.toSnapshot(finalException)
        val result = DownloadIntentReconciler.reconcile(
            coreIntent,
            DownloadIndexSnapshot(mapOf(media.downloadId to media)),
        )
        if (!result.changes(coreIntent)) return@withContext
        database.localAudioDao().upsertDownloadIntent(
            intent.copy(
                state = result.projectedState.toPersistedState(),
                failureCode = result.failureCode?.name,
                updatedAtMs = nowMs,
                completedAtMs = if (result.projectedState == DownloadIntentState.COMPLETED) {
                    intent.completedAtMs ?: nowMs
                } else {
                    null
                },
            ),
        )
    }

    suspend fun reconcileAll(manager: DownloadManager, nowMs: Long) = withContext(Dispatchers.IO) {
        val observed = linkedMapOf<String, Download>()
        manager.downloadIndex.getDownloads().use { cursor ->
            while (cursor.moveToNext()) observed[cursor.download.request.id] = cursor.download
        }
        val intents = database.localAudioDao().downloadIntents(MAX_RECONCILE_INTENTS)
        for (intent in intents) {
            val download = observed[intent.media3DownloadId ?: intent.downloadIntentId]
            if (download != null) {
                reconcile(download, null, nowMs)
                continue
            }
            val coreIntent = intent.toSnapshot()
            when (DownloadIntentReconciler.reconcile(coreIntent, DownloadIndexSnapshot(emptyMap())).command) {
                is Media3Command.Enqueue -> intent.toDownloadRequestOrNull()?.let(manager::addDownload)
                is Media3Command.Remove -> manager.removeDownload(coreIntent.media3DownloadId ?: coreIntent.intentId)
                Media3Command.None -> Unit
            }
        }
    }

    private fun DownloadIntentEntity.toSnapshot() = DownloadIntentSnapshot(
        intentId = downloadIntentId,
        localUserTrackRefId = localUserTrackRefId,
        media3DownloadId = media3DownloadId,
        storageClass = desiredStorageClass.toStorageClass(),
        state = state.toIntentState(),
        failureCode = failureCode?.let { code ->
            runCatching { app.autplay.download.DownloadFailureCode.valueOf(code) }.getOrNull()
        },
    )

    private fun DownloadIntentEntity.toDownloadRequest(): DownloadRequest =
        requireNotNull(toDownloadRequestOrNull()) { "DOWNLOAD_INTENT_REFERENCE_INCOMPLETE" }

    private fun DownloadIntentEntity.toDownloadRequestOrNull(): DownloadRequest? {
        val profileId = serverProfileId ?: return null
        val variantId = serverAudioVariantId ?: return null
        val stableUri = Uri.Builder()
            .scheme(AndroidPlaybackSourceResolver.VAULT_SCHEME)
            .authority(profileId)
            .appendPath("audio-variants")
            .appendPath(variantId)
            .build()
        val requestData = "track_ref=$localUserTrackRefId;class=$desiredStorageClass"
            .toByteArray(StandardCharsets.US_ASCII)
        return DownloadRequest.Builder(media3DownloadId ?: downloadIntentId, stableUri)
            .setData(requestData)
            .build()
    }

    private fun Download.toSnapshot(error: Exception?): Media3DownloadSnapshot = Media3DownloadSnapshot(
        downloadId = request.id,
        state = when (state) {
            Download.STATE_QUEUED, Download.STATE_RESTARTING -> Media3DownloadState.QUEUED
            Download.STATE_DOWNLOADING -> Media3DownloadState.DOWNLOADING
            Download.STATE_STOPPED -> Media3DownloadState.STOPPED
            Download.STATE_COMPLETED -> Media3DownloadState.COMPLETED
            Download.STATE_FAILED -> Media3DownloadState.FAILED
            Download.STATE_REMOVING -> Media3DownloadState.REMOVING
            else -> Media3DownloadState.QUEUED
        },
        failure = if (state == Download.STATE_FAILED) error.toFailureSignal() else null,
    )

    private fun Exception?.toFailureSignal(): DownloadFailureSignal {
        val causes = generateSequence(this as Throwable?) { it.cause }.toList()
        val http = causes
            .filterIsInstance<HttpDataSource.InvalidResponseCodeException>()
            .firstOrNull()
        val storageFull = causes.any { cause ->
            cause is SQLiteFullException || (cause is ErrnoException && cause.errno == OsConstants.ENOSPC)
        }
        return when (http?.responseCode) {
            401 -> DownloadFailureSignal(storageFull = storageFull, authExpired = true)
            403 -> DownloadFailureSignal(authorizationDenied = true)
            404 -> DownloadFailureSignal(notFound = true)
            in 500..599 -> DownloadFailureSignal(httpServer = true)
            else -> DownloadFailureSignal(
                storageFull = storageFull,
                network = causes.any { it is IOException },
                malformedMedia = causes.any { it is ParserException },
            )
        }
    }

    private fun String.toStorageClass(): DownloadStorageClass =
        runCatching { DownloadStorageClass.valueOf(this) }.getOrDefault(DownloadStorageClass.PROACTIVE_CACHE)

    private fun String.toIntentState(): DownloadIntentState = when (this) {
        "CANCELLED" -> DownloadIntentState.CANCELED
        else -> runCatching { DownloadIntentState.valueOf(this) }.getOrDefault(DownloadIntentState.REQUESTED)
    }

    private fun DownloadIntentState.toPersistedState(): String =
        if (this == DownloadIntentState.CANCELED) "CANCELLED" else name

    private companion object {
        const val MAX_TRACK_INTENTS = 32
        const val MAX_RECONCILE_INTENTS = 10_000
        const val DEFAULT_ADMISSION_BYTES = 64L * 1024 * 1024
        const val MAX_SINGLE_ADMISSION_BYTES = 4L * 1024 * 1024 * 1024
        const val DEFAULT_MANAGED_QUOTA_BYTES = 4L * 1024 * 1024 * 1024
        const val MIN_MANAGED_QUOTA_BYTES = 256L * 1024 * 1024
        const val DEFAULT_MINIMUM_FREE_BYTES = 512L * 1024 * 1024
        const val MINIMUM_FREE_FLOOR_BYTES = 64L * 1024 * 1024
    }
}
