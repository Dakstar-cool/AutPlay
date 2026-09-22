package app.autplay.application.sync

import androidx.room3.withWriteTransaction
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.SyncBootstrapStateEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.domain.LocalId

/** Creates the local sync lineage as soon as a device is bound, before its first local edit. */
class SyncBindingInitializer(
    private val database: AutPlayDatabase,
    private val nowMs: () -> Long = System::currentTimeMillis,
    private val identifier: () -> LocalId = LocalId::random,
) {
    suspend fun ensure(binding: ClientEventBinding): SyncCursorEntity = database.withWriteTransaction {
        database.syncDao().cursor(binding.serverProfileId.value)?.let { cursor ->
            check(cursor.deviceId == binding.deviceId.value) { "SERVER_PROFILE_LINEAGE_MISMATCH" }
            val lineage = checkNotNull(database.journalDao().lineageById(cursor.journalLineageId)) {
                "SYNC_LINEAGE_MISSING"
            }
            check(lineage.userId == binding.userId.value && lineage.journalEpoch == cursor.journalEpoch) {
                "SERVER_PROFILE_LINEAGE_MISMATCH"
            }
            return@withWriteTransaction cursor
        }

        val lineage = database.journalDao().lineageByDeviceId(binding.deviceId.value)?.also {
            check(it.userId == binding.userId.value) { "JOURNAL_LINEAGE_USER_MISMATCH" }
            binding.journalEpoch?.let { expected ->
                check(it.journalEpoch == expected.value) { "JOURNAL_EPOCH_MISMATCH" }
            }
        } ?: run {
            val epoch = binding.journalEpoch ?: identifier()
            check(database.journalDao().lineageByJournalEpoch(epoch.value) == null) {
                "JOURNAL_EPOCH_DEVICE_MISMATCH"
            }
            JournalLineageEntity(
                lineageId = identifier().value,
                userId = binding.userId.value,
                deviceId = binding.deviceId.value,
                journalEpoch = epoch.value,
                nextDeviceSequence = 1,
                createdAtMs = nowMs(),
            ).also { database.journalDao().insertLineage(it) }
        }
        SyncCursorEntity(
            serverProfileId = binding.serverProfileId.value,
            journalLineageId = lineage.lineageId,
            deviceId = lineage.deviceId,
            journalEpoch = lineage.journalEpoch,
            opaqueCursor = null,
            lastPulledServerSequence = 0,
            lastAckedDeviceSequence = 0,
            bootstrapSnapshotId = null,
            bootstrapState = "NOT_STARTED",
            lastSyncAtMs = null,
            updatedAtMs = nowMs(),
        ).also { database.syncDao().upsertCursor(it) }
    }

    /** Requests a fresh owner snapshot while preserving the incremental cursor until cutover. */
    suspend fun requestFullSnapshot(binding: ClientEventBinding): SyncCursorEntity =
        database.withWriteTransaction {
            val cursor = checkNotNull(database.syncDao().cursor(binding.serverProfileId.value)) {
                "SYNC_CURSOR_NOT_BOUND"
            }
            check(cursor.deviceId == binding.deviceId.value) { "SERVER_PROFILE_LINEAGE_MISMATCH" }
            val lineage = checkNotNull(database.journalDao().lineageById(cursor.journalLineageId)) {
                "SYNC_LINEAGE_MISSING"
            }
            check(lineage.userId == binding.userId.value && lineage.journalEpoch == cursor.journalEpoch) {
                "SERVER_PROFILE_LINEAGE_MISMATCH"
            }
            val now = nowMs()
            val reset = cursor.copy(
                bootstrapSnapshotId = null,
                bootstrapState = "RESET_REQUIRED",
                updatedAtMs = now,
            )
            database.syncDao().upsertCursor(reset)
            database.syncDao().upsertBootstrapState(
                SyncBootstrapStateEntity(
                    serverProfileId = binding.serverProfileId.value,
                    snapshotId = null,
                    pageToken = null,
                    finalCursor = null,
                    state = "RESET_REQUIRED",
                    updatedAtMs = now,
                ),
            )
            reset
        }
}
