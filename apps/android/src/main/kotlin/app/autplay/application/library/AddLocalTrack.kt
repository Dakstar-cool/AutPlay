package app.autplay.application.library

import androidx.room3.withWriteTransaction
import app.autplay.application.sync.ClientEventBinding
import app.autplay.application.sync.LocalIntentPayloadPolicy
import app.autplay.application.sync.P04ClientEventHashInput
import app.autplay.application.sync.P04ClientEventHasher
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.JournalLineageEntity
import app.autplay.data.local.entity.LibraryEntryEntity
import app.autplay.data.local.entity.LocalMutationOutboxEntity
import app.autplay.data.local.entity.OfflineJournalEventEntity
import app.autplay.data.local.entity.SyncCursorEntity
import app.autplay.data.local.entity.TrackSearchContentEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.UserId
import java.time.Instant
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

data class AddLocalTrackCommand(
    val binding: ClientEventBinding?,
    val trackRefId: LocalId,
    val libraryEntryId: LocalId,
    val localChangeId: LocalId,
    val title: String,
    val artist: String,
    val occurredAtMs: Long,
)

sealed interface AddLocalTrackResult {
    val trackRefId: LocalId
    val libraryEntryId: LocalId

    data class Standalone(
        override val trackRefId: LocalId,
        override val libraryEntryId: LocalId,
        val localChangeId: LocalId,
    ) : AddLocalTrackResult

    data class Journaled(
        override val trackRefId: LocalId,
        override val libraryEntryId: LocalId,
        val eventId: LocalId,
        val journalLineageId: LocalId,
        val deviceSequence: Long,
    ) : AddLocalTrackResult
}

data class MaterializeLocalChangeCommand(
    val localChangeId: LocalId,
    val eventId: LocalId,
    val binding: ClientEventBinding,
    val materializedAtMs: Long,
)

data class MaterializeLocalChangeResult(
    val localChangeId: LocalId,
    val eventId: LocalId,
    val journalLineageId: LocalId,
    val deviceSequence: Long,
)

enum class LocalLibraryErrorCode {
    JOURNAL_LINEAGE_USER_MISMATCH,
    JOURNAL_EPOCH_MISMATCH,
    JOURNAL_EPOCH_DEVICE_MISMATCH,
    SERVER_PROFILE_LINEAGE_MISMATCH,
    OUTBOX_NOT_FOUND,
    OUTBOX_NOT_MATERIALIZABLE,
    OUTBOX_MATERIALIZATION_CORRUPT,
    OUTBOX_DOMAIN_MISSING,
    OUTBOX_DOMAIN_MISMATCH,
    OUTBOX_EVENT_ID_MUST_BE_NEW,
    OUTBOX_LINK_RACE,
}

/** Stable failure with no owner identifiers or payload text in its message. */
class LocalLibraryException(val code: LocalLibraryErrorCode) : IllegalStateException(code.name)

/** Test seam proving domain writes and their durable mutation record share one Room transaction. */
fun interface LocalCommandFailureInjector {
    suspend fun afterDomainWrites()

    companion object {
        val NONE = LocalCommandFailureInjector {}
    }
}

/** Test seam proving materialization, sequence allocation, event insert and outbox link are atomic. */
fun interface MaterializationFailureInjector {
    suspend fun afterJournalInsert()

    companion object {
        val NONE = MaterializationFailureInjector {}
    }
}

/**
 * Local-first command repository with no network dependency.
 *
 * Standalone commands append a local-only outbox row. Bound commands append an immutable P04
 * Journal event. P09 may invoke [materializeOutboxToJournal] only after consent and authenticated
 * binding revalidation.
 */
class LocalLibraryCommandRepository(
    private val database: AutPlayDatabase,
    private val failureInjector: LocalCommandFailureInjector = LocalCommandFailureInjector.NONE,
    private val materializationFailureInjector: MaterializationFailureInjector =
        MaterializationFailureInjector.NONE,
    private val identifierFactory: () -> LocalId = { LocalId.random() },
) {
    fun activeEntryCount(profileId: String? = null, limit: Int = DEFAULT_LIBRARY_LIMIT): Flow<Int> {
        require(limit in 1..DEFAULT_LIBRARY_LIMIT)
        return (profileId?.let { database.libraryDao().activeEntriesForProfile(it, limit) }
            ?: database.libraryDao().activeLegacyEntries(limit)).map { entries -> entries.size }
    }

    fun entries(profileId: String? = null, limit: Int = DEFAULT_LIBRARY_LIMIT): Flow<List<LibraryEntryEntity>> {
        require(limit in 1..DEFAULT_LIBRARY_LIMIT)
        return profileId?.let { database.libraryDao().entriesForProfile(it, limit) }
            ?: database.libraryDao().legacyEntries(limit)
    }

    suspend fun trackRef(localTrackRefId: String): UserTrackRefEntity? =
        database.libraryDao().trackRef(localTrackRefId)

    suspend fun add(command: AddLocalTrackCommand): AddLocalTrackResult {
        validate(command)
        val payload = LocalIntentPayloadPolicy.canonicalize(
            eventType = EVENT_TYPE,
            schemaVersion = SCHEMA_VERSION,
            aggregateType = AGGREGATE_TYPE,
            payloadJson = canonicalPayload(command),
        )
        return database.withWriteTransaction {
            val lineage = command.binding?.let { resolveOrCreateLineage(it, command.occurredAtMs) }
            val sequence = lineage?.let { database.journalDao().allocateSequence(it.lineageId) } ?: 0
            writeDomain(command, sequence, lineage != null)
            failureInjector.afterDomainWrites()

            if (lineage == null) {
                database.journalDao().insertOutbox(
                    LocalMutationOutboxEntity(
                        localChangeId = command.localChangeId.value,
                        eventType = EVENT_TYPE,
                        schemaVersion = SCHEMA_VERSION,
                        aggregateType = AGGREGATE_TYPE,
                        aggregateLocalId = command.trackRefId.value,
                        payloadJson = payload,
                        occurredAtMs = command.occurredAtMs,
                        materializationState = OUTBOX_UNMATERIALIZED,
                    ),
                )
                AddLocalTrackResult.Standalone(
                    trackRefId = command.trackRefId,
                    libraryEntryId = command.libraryEntryId,
                    localChangeId = command.localChangeId,
                )
            } else {
                val binding = checkNotNull(command.binding)
                database.journalDao().insert(
                    journalEvent(
                        lineage = lineage,
                        binding = binding,
                        eventId = command.localChangeId,
                        aggregateLocalId = command.trackRefId,
                        sequence = sequence,
                        payload = payload,
                        occurredAtMs = command.occurredAtMs,
                    ),
                )
                AddLocalTrackResult.Journaled(
                    trackRefId = command.trackRefId,
                    libraryEntryId = command.libraryEntryId,
                    eventId = command.localChangeId,
                    journalLineageId = LocalId(lineage.lineageId),
                    deviceSequence = sequence,
                )
            }
        }
    }

    suspend fun materializeOutboxToJournal(
        command: MaterializeLocalChangeCommand,
    ): MaterializeLocalChangeResult {
        require(command.materializedAtMs >= 0)
        if (command.eventId == command.localChangeId) {
            fail(LocalLibraryErrorCode.OUTBOX_EVENT_ID_MUST_BE_NEW)
        }
        return database.withWriteTransaction {
            val source = database.journalDao().outbox(command.localChangeId.value)
                ?: fail(LocalLibraryErrorCode.OUTBOX_NOT_FOUND)
            source.materializedEventId?.let { existingEventId ->
                if (source.materializationState != OUTBOX_MATERIALIZED) {
                    fail(LocalLibraryErrorCode.OUTBOX_MATERIALIZATION_CORRUPT)
                }
                val existing = database.journalDao().event(existingEventId)
                    ?: fail(LocalLibraryErrorCode.OUTBOX_MATERIALIZATION_CORRUPT)
                if (
                    existing.eventType != source.eventType ||
                    existing.schemaVersion != source.schemaVersion ||
                    existing.aggregateType != source.aggregateType ||
                    existing.aggregateLocalId != source.aggregateLocalId ||
                    existing.payloadJson != source.payloadJson
                ) {
                    fail(LocalLibraryErrorCode.OUTBOX_MATERIALIZATION_CORRUPT)
                }
                return@withWriteTransaction result(source.localChangeId, existing)
            }
            if (source.materializationState != OUTBOX_UNMATERIALIZED) {
                fail(LocalLibraryErrorCode.OUTBOX_NOT_MATERIALIZABLE)
            }
            LocalIntentPayloadPolicy.validateStoredCanonical(
                eventType = source.eventType,
                schemaVersion = source.schemaVersion,
                aggregateType = source.aggregateType,
                payloadJson = source.payloadJson,
            )

            val lineage = resolveOrCreateLineage(command.binding, command.materializedAtMs)
            val sequence = database.journalDao().allocateSequence(lineage.lineageId)
            val event = journalEvent(
                lineage = lineage,
                binding = command.binding,
                eventId = command.eventId,
                aggregateLocalId = LocalId(source.aggregateLocalId),
                sequence = sequence,
                payload = source.payloadJson,
                occurredAtMs = source.occurredAtMs,
            )
            database.journalDao().insert(event)
            materializationFailureInjector.afterJournalInsert()

            val payload = Json.parseToJsonElement(source.payloadJson).jsonObject
            val libraryEntryId = payload.getValue("library_entry_local_id").jsonPrimitive.content
            val trackRef = database.libraryDao().trackRef(source.aggregateLocalId)
                ?: fail(LocalLibraryErrorCode.OUTBOX_DOMAIN_MISSING)
            val libraryEntry = database.libraryDao().entry(libraryEntryId)
                ?: fail(LocalLibraryErrorCode.OUTBOX_DOMAIN_MISSING)
            if (libraryEntry.localUserTrackRefId != source.aggregateLocalId) {
                fail(LocalLibraryErrorCode.OUTBOX_DOMAIN_MISMATCH)
            }
            database.libraryDao().upsertTrackRef(
                trackRef.copy(syncState = SYNC_DIRTY, lastLocalSequence = sequence, serverProfileId = command.binding.serverProfileId.value),
            )
            database.libraryDao().upsertEntry(
                libraryEntry.copy(syncState = SYNC_DIRTY, lastLocalSequence = sequence, serverProfileId = command.binding.serverProfileId.value),
            )
            if (
                database.journalDao().linkOutboxMaterialized(
                    localChangeId = source.localChangeId,
                    eventId = event.eventId,
                    materializedAtMs = command.materializedAtMs,
                ) != 1
            ) {
                fail(LocalLibraryErrorCode.OUTBOX_LINK_RACE)
            }
            result(source.localChangeId, event)
        }
    }

    private suspend fun resolveOrCreateLineage(
        binding: ClientEventBinding,
        createdAtMs: Long,
    ): JournalLineageEntity {
        val journalDao = database.journalDao()
        val existing = journalDao.lineageByDeviceId(binding.deviceId.value)
        val lineage = if (existing != null) {
            if (existing.userId != binding.userId.value) {
                fail(LocalLibraryErrorCode.JOURNAL_LINEAGE_USER_MISMATCH)
            }
            binding.journalEpoch?.let { expected ->
                if (existing.journalEpoch != expected.value) {
                    fail(LocalLibraryErrorCode.JOURNAL_EPOCH_MISMATCH)
                }
            }
            existing
        } else {
            val epoch = binding.journalEpoch ?: identifierFactory()
            journalDao.lineageByJournalEpoch(epoch.value)?.let {
                fail(LocalLibraryErrorCode.JOURNAL_EPOCH_DEVICE_MISMATCH)
            }
            JournalLineageEntity(
                lineageId = identifierFactory().value,
                userId = binding.userId.value,
                deviceId = binding.deviceId.value,
                journalEpoch = epoch.value,
                nextDeviceSequence = 1,
                createdAtMs = createdAtMs,
            ).also { journalDao.insertLineage(it) }
        }
        ensureCursor(binding, lineage, createdAtMs)
        return lineage
    }

    private suspend fun ensureCursor(
        binding: ClientEventBinding,
        lineage: JournalLineageEntity,
        updatedAtMs: Long,
    ) {
        val existing = database.syncDao().cursor(binding.serverProfileId.value)
        if (existing != null) {
            if (
                existing.journalLineageId != lineage.lineageId ||
                existing.deviceId != lineage.deviceId ||
                existing.journalEpoch != lineage.journalEpoch
            ) {
                fail(LocalLibraryErrorCode.SERVER_PROFILE_LINEAGE_MISMATCH)
            }
            return
        }
        database.syncDao().upsertCursor(
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
                updatedAtMs = updatedAtMs,
            ),
        )
    }

    private suspend fun writeDomain(
        command: AddLocalTrackCommand,
        sequence: Long,
        isBound: Boolean,
    ) {
        val now = command.occurredAtMs
        val syncState = if (isBound) SYNC_DIRTY else SYNC_LOCAL_ONLY
        // A bound local-first create belongs to that profile immediately. Standalone imports are
        // intentionally retained under the visible legacy scope until explicit materialization.
        val profileId = command.binding?.serverProfileId?.value ?: "legacy-unscoped"
        database.libraryDao().upsertTrackRef(
            UserTrackRefEntity(
                localUserTrackRefId = command.trackRefId.value,
                serverUserTrackRefId = null,
                localRecordingId = null,
                serverRecordingId = null,
                resolutionStatus = "UNRESOLVED",
                rawTitle = command.title,
                rawArtist = command.artist,
                rawAlbum = null,
                rawDurationMs = null,
                resolutionConfidence = null,
                syncState = syncState,
                serverRowVersion = null,
                lastLocalSequence = sequence,
                createdAtMs = now,
                updatedAtMs = now,
                deletedAtMs = null,
                serverProfileId = profileId,
            ),
        )
        database.libraryDao().upsertEntry(
            LibraryEntryEntity(
                localLibraryEntryId = command.libraryEntryId.value,
                serverLibraryEntryId = null,
                localUserTrackRefId = command.trackRefId.value,
                addedAtMs = now,
                source = "LOCAL",
                availabilityStatus = "PENDING",
                syncState = syncState,
                serverRowVersion = null,
                lastLocalSequence = sequence,
                removedAtMs = null,
                updatedAtMs = now,
                serverProfileId = profileId,
            ),
        )
        database.searchDao().insertContent(
            TrackSearchContentEntity(
                localUserTrackRefId = command.trackRefId.value,
                title = command.title,
                artist = command.artist,
                album = null,
                aliases = null,
                transliterations = null,
            ),
        )
    }

    private fun journalEvent(
        lineage: JournalLineageEntity,
        binding: ClientEventBinding,
        eventId: LocalId,
        aggregateLocalId: LocalId,
        sequence: Long,
        payload: String,
        occurredAtMs: Long,
    ): OfflineJournalEventEntity {
        val eventBinding = binding.copy(
            userId = UserId(lineage.userId),
            deviceId = DeviceId(lineage.deviceId),
        )
        val hashInput = P04ClientEventHashInput(
            eventId = eventId,
            idempotencyKey = eventId.value,
            binding = eventBinding,
            deviceSequence = sequence,
            eventType = EVENT_TYPE,
            aggregateType = AGGREGATE_TYPE,
            aggregateLocalId = aggregateLocalId,
            aggregateServerId = null,
            baseServerRowVersion = null,
            occurredAt = Instant.ofEpochMilli(occurredAtMs).toString(),
            payloadJson = payload,
        )
        return OfflineJournalEventEntity(
            eventId = eventId.value,
            journalLineageId = lineage.lineageId,
            idempotencyKey = eventId.value,
            userId = lineage.userId,
            deviceId = lineage.deviceId,
            serverProfileId = binding.serverProfileId.value,
            deviceSequence = sequence,
            eventType = EVENT_TYPE,
            schemaVersion = SCHEMA_VERSION,
            aggregateType = AGGREGATE_TYPE,
            aggregateLocalId = aggregateLocalId.value,
            aggregateServerId = null,
            baseServerRowVersion = null,
            payloadJson = payload,
            requestHash = P04ClientEventHasher.sha256(hashInput),
            occurredAtMs = occurredAtMs,
            state = "PENDING",
            attemptCount = 0,
            nextAttemptAtMs = null,
            leaseToken = null,
            leaseExpiresAtMs = null,
            lastErrorCode = null,
            ackedAtMs = null,
        )
    }

    private fun result(localChangeId: String, event: OfflineJournalEventEntity) =
        MaterializeLocalChangeResult(
            localChangeId = LocalId(localChangeId),
            eventId = LocalId(event.eventId),
            journalLineageId = LocalId(event.journalLineageId),
            deviceSequence = event.deviceSequence,
        )

    private fun validate(command: AddLocalTrackCommand) {
        require(command.title.isNotBlank() && command.title.length <= MAX_TEXT_CHARACTERS)
        require(command.artist.isNotBlank() && command.artist.length <= MAX_TEXT_CHARACTERS)
        require(command.occurredAtMs >= 0)
    }

    private fun canonicalPayload(command: AddLocalTrackCommand): String =
        "{\"artist\":${jsonString(command.artist)}," +
            "\"library_entry_local_id\":${jsonString(command.libraryEntryId.value)}," +
            "\"title\":${jsonString(command.title)}}"

    private fun jsonString(value: String): String = buildString {
        append('"')
        for (character in value) {
            when (character) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\b' -> append("\\b")
                '\u000C' -> append("\\f")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (character.code < 0x20) {
                    append("\\u").append(character.code.toString(16).padStart(4, '0'))
                } else {
                    append(character)
                }
            }
        }
        append('"')
    }

    private fun fail(code: LocalLibraryErrorCode): Nothing = throw LocalLibraryException(code)

    private companion object {
        const val MAX_TEXT_CHARACTERS = 1_000
        const val DEFAULT_LIBRARY_LIMIT = 10_000
        const val EVENT_TYPE = LocalIntentPayloadPolicy.USER_TRACK_REF_CREATED
        const val AGGREGATE_TYPE = LocalIntentPayloadPolicy.USER_TRACK_REF
        const val SCHEMA_VERSION = LocalIntentPayloadPolicy.SCHEMA_VERSION
        const val OUTBOX_UNMATERIALIZED = "UNMATERIALIZED"
        const val OUTBOX_MATERIALIZED = "MATERIALIZED"
        const val SYNC_LOCAL_ONLY = "LOCAL_ONLY"
        const val SYNC_DIRTY = "DIRTY"
    }
}
