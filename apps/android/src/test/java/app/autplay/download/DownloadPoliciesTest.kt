package app.autplay.download

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DownloadPoliciesTest {
    private fun intent(state: DownloadIntentState = DownloadIntentState.REQUESTED) = DownloadIntentSnapshot(
        intentId = "download-a",
        localUserTrackRefId = "track-a",
        media3DownloadId = "download-a",
        storageClass = DownloadStorageClass.USER_DOWNLOAD,
        state = state,
    )

    @Test
    fun missingMedia3EntryIsEnqueuedAndDuplicateReconciliationIsIdempotent() {
        val input = intent()
        val emptyIndex = DownloadIndexSnapshot(emptyMap())

        val first = DownloadIntentReconciler.reconcile(input, emptyIndex)
        val second = DownloadIntentReconciler.reconcile(input, emptyIndex)

        assertEquals(first, second)
        assertEquals(Media3Command.Enqueue("download-a", "download-a"), first.command)
        assertEquals(DownloadIntentState.REQUESTED, first.projectedState)
        assertFalse(first.changes(input))
    }

    @Test
    fun duplicateCompletedCallbackDoesNotCreateAnotherProjectionChange() {
        val index = DownloadIndexSnapshot(mapOf("download-a" to Media3DownloadSnapshot("download-a", Media3DownloadState.COMPLETED)))
        val result = DownloadIntentReconciler.reconcile(intent(DownloadIntentState.COMPLETED), index)

        assertEquals(Media3Command.None, result.command)
        assertFalse(result.changes(intent(DownloadIntentState.COMPLETED)))
    }

    @Test
    fun indexStateIsTheOnlyCompletionTruthAndNeverIncludesProgress() {
        val index = DownloadIndexSnapshot(mapOf("download-a" to Media3DownloadSnapshot("download-a", Media3DownloadState.COMPLETED)))
        val result = DownloadIntentReconciler.reconcile(intent(DownloadIntentState.DOWNLOADING), index)

        assertEquals(DownloadIntentState.COMPLETED, result.projectedState)
        assertTrue(result.changes(intent(DownloadIntentState.DOWNLOADING)))
    }

    @Test
    fun failureCodesAreStableAndStorageFullWinsAmbiguousSignals() {
        assertEquals(
            DownloadFailureCode.STORAGE_FULL,
            DownloadFailureClassifier.classify(DownloadFailureSignal(storageFull = true, network = true)),
        )
        val index = DownloadIndexSnapshot(mapOf("download-a" to Media3DownloadSnapshot(
            "download-a", Media3DownloadState.FAILED, DownloadFailureSignal(authExpired = true),
        )))
        assertEquals(DownloadFailureCode.AUTH_EXPIRED, DownloadIntentReconciler.reconcile(intent(), index).failureCode)
    }

    @Test
    fun storageFullEvictsStreamThenProactiveAndNeverUserOrPinned() {
        val entries = listOf(
            CacheEntry("pinned", DownloadStorageClass.PINNED, 40, 1),
            CacheEntry("user", DownloadStorageClass.USER_DOWNLOAD, 40, 2),
            CacheEntry("proactive-newer", DownloadStorageClass.PROACTIVE_CACHE, 20, 9),
            CacheEntry("proactive-old", DownloadStorageClass.PROACTIVE_CACHE, 20, 3),
            CacheEntry("stream", DownloadStorageClass.STREAM_CACHE, 20, 10),
        )
        val result = DownloadStoragePolicy.admit(
            requestClass = DownloadStorageClass.PINNED,
            requestedBytes = 60,
            managedBytesInUse = 140,
            freeBytes = 120,
            entries = entries,
            policy = StoragePolicy(managedQuotaBytes = 150, minimumFreeBytes = 40),
        )

        val eviction = result as StorageAdmission.Evict
        assertEquals(listOf("stream", "proactive-old", "proactive-newer"), eviction.entries.map { it.id })
        assertFalse(eviction.entries.any { it.storageClass == DownloadStorageClass.USER_DOWNLOAD })
        assertFalse(eviction.entries.any { it.storageClass == DownloadStorageClass.PINNED })
    }

    @Test
    fun proactiveRequestNeverEvictsUserOrPinnedAndRejectsWhenCacheCannotMakeSpace() {
        val result = DownloadStoragePolicy.admit(
            requestClass = DownloadStorageClass.PROACTIVE_CACHE,
            requestedBytes = 50,
            managedBytesInUse = 100,
            freeBytes = 50,
            entries = listOf(
                CacheEntry("user", DownloadStorageClass.USER_DOWNLOAD, 100, 1),
                CacheEntry("pinned", DownloadStorageClass.PINNED, 100, 1),
            ),
            policy = StoragePolicy(managedQuotaBytes = 120, minimumFreeBytes = 40),
        )

        assertEquals(StorageAdmission.Rejected(), result)
    }

    @Test
    fun requestIsRejectedWhenOnlyUserDownloadWouldCloseTheStorageGap() {
        val result = DownloadStoragePolicy.admit(
            requestClass = DownloadStorageClass.PINNED,
            requestedBytes = 70,
            managedBytesInUse = 140,
            freeBytes = 120,
            entries = listOf(
                CacheEntry("stream", DownloadStorageClass.STREAM_CACHE, 20, 1),
                CacheEntry("proactive", DownloadStorageClass.PROACTIVE_CACHE, 20, 2),
                CacheEntry("user", DownloadStorageClass.USER_DOWNLOAD, 40, 3),
            ),
            policy = StoragePolicy(managedQuotaBytes = 150, minimumFreeBytes = 40),
        )

        assertEquals(StorageAdmission.Rejected(), result)
    }
}
