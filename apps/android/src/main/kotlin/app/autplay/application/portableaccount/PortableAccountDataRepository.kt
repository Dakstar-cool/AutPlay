package app.autplay.application.portableaccount

import androidx.room3.withWriteTransaction
import app.autplay.data.local.*
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.*

/** Durable normalized metadata, isolated by the proven stable root account UUID. */
class PortableAccountDataRepository(private val database: AutPlayDatabase) {
    private val dao = database.portableAccountDao()

    /** Call before logout clears its captured binding. Never infer ownership from an endpoint. */
    suspend fun persistOwnership(accountId: String, profileId: String?, ownerSinceMs: Long = 0): Unit = database.withWriteTransaction {
        PortableAccountDataCodec.uuid(accountId)
        require(ownerSinceMs >= 0)
        val profile = profileId ?: STANDALONE_PROFILE
        if (profileId != null) PortableAccountDataCodec.uuid(profileId)
        dao.addScope(PortableAccountScopeEntity(profile, accountId, ownerSinceMs))
        val existing = requireNotNull(dao.scope(profile))
        check(existing.accountId == accountId && existing.ownerSinceMs == ownerSinceMs) { "ACCOUNT_SCOPE_IDENTITY_CONFLICT" }
    }

    /** Paged read for account statistics: each logical listening record appears once. */
    suspend fun listeningPage(accountId: String, afterId: String = "", limit: Int = 32): List<PortableDataRecord> {
        PortableAccountDataCodec.uuid(accountId); require(limit in 1..32)
        return dao.kindPage(accountId, "LISTENING", afterId, limit).map(::decode)
    }

    fun observeListening(accountId: String): Flow<List<PortableAccountRecordEntity>> {
        PortableAccountDataCodec.uuid(accountId)
        return dao.observeListening(accountId)
    }

    fun observeRevision(accountId: String): Flow<Long> = dao.observeRevision(PortableAccountDataCodec.uuid(accountId))

    suspend fun scopes(accountId: String): List<PortableAccountScopeEntity> = dao.scopes(PortableAccountDataCodec.uuid(accountId))

    /** Offline cache capture uses its own durable pass; it needs neither endpoint nor credential. */
    suspend fun captureLocal(accountId: String, maxPages: Int = 12, deadlineMs: Long = 2_000): PortableAccountDataSyncOutcome {
        PortableAccountDataCodec.uuid(accountId); require(maxPages in 1..32 && deadlineMs in 1..60_000)
        val destination = stableId(accountId, "local-cache:v1")
        beginPass(accountId, destination)
        val owned = scopes(accountId)
        check(owned.size <= 32) { "ACCOUNT_SCOPE_LIMIT_REACHED" }
        val started = System.nanoTime()
        var pages = 0
        for (scope in owned.sortedBy { it.profileId }) for (kind in localKinds) {
            while (!cursor(accountId, destination, "local:${scope.profileId}:$kind").complete) {
                if (pages >= maxPages || System.nanoTime() - started >= java.util.concurrent.TimeUnit.MILLISECONDS.toNanos(deadlineMs)) return PortableAccountDataSyncOutcome.CONTINUE
                capturePage(accountId, destination, scope, kind); pages++
            }
        }
        finishPass(accountId, destination)
        return PortableAccountDataSyncOutcome.COMPLETE
    }

    suspend fun cursor(accountId: String, profileId: String, source: String): PortableAccountCursorEntity =
        dao.cursor(accountId, profileId, source) ?: PortableAccountCursorEntity(accountId, profileId, source)

    /** A completed pass can start again on foreground/mutation; continuation resumes its exact pass. */
    suspend fun beginPass(accountId: String, profileId: String): Unit = database.withWriteTransaction {
        val marker = dao.cursor(accountId, profileId, PASS)
        if (marker == null || marker.complete) {
            dao.cursors(accountId, profileId).filter { it.source.startsWith("local:") || it.source.startsWith("remote:") }
                .forEach { old -> dao.putCursor(if (old.source.startsWith("local:")) old.copy(cursorId = "", complete = false) else old.copy(complete = false)) }
            dao.putCursor(PortableAccountCursorEntity(accountId, profileId, PASS))
        }
    }

    suspend fun finishPass(accountId: String, profileId: String): Unit = database.withWriteTransaction {
        dao.putCursor(cursor(accountId, profileId, PASS).copy(complete = true))
    }

    /** Reads at most one bounded page and commits the normalized records with its local cursor. */
    suspend fun capturePage(accountId: String, destinationProfile: String, scope: PortableAccountScopeEntity, kind: String): Boolean = database.withWriteTransaction {
        check(dao.scope(scope.profileId) == scope && scope.accountId == accountId) { "ACCOUNT_SCOPE_IDENTITY_CONFLICT" }
        val source = "local:${scope.profileId}:$kind"
        val state = cursor(accountId, destinationProfile, source)
        if (state.complete) return@withWriteTransaction true
        val after = state.cursorId.takeUnless { it == PortableAccountDataCodec.ZERO_ID }.orEmpty()
        val since = scope.ownerSinceMs
        val page: List<Pair<String, PortableDataRecord?>> = when (kind) {
            "LISTENING" -> dao.localListeningPage(scope.profileId, since, after, 32).map { it.eventId to PortableAccountDataCodec.listening(it) }
            "PREFERENCE" -> dao.localPreferencePage(scope.profileId, since, after, 32).map { row ->
                row.id to PortableAccountDataCodec.checked(stableId(accountId, "preference:v1:" + row.trackKey), kind, buildJsonObject {
                    put("version", 1); put("updated_at_ms", row.updatedAtMs); put("track_key", row.trackKey)
                    putNullable("title", PortableAccountDataCodec.safeDescription(row.title, 2000)); putNullable("artist", PortableAccountDataCodec.safeDescription(row.artist, 2000))
                    put("preference", row.preference); put("rating", row.rating?.let(::JsonPrimitive) ?: JsonNull); put("excluded_from_taste", row.excluded)
                })
            }
            "PLAYLIST" -> dao.localPlaylistPage(scope.profileId, since, after, 32).map { row ->
                row.localPlaylistId to PortableAccountDataCodec.checked(row.serverPlaylistId ?: row.localPlaylistId, kind, buildJsonObject {
                    put("version", 1); put("updated_at_ms", row.updatedAtMs); put("name", PortableAccountDataCodec.safeDescription(row.name, 500) ?: "Playlist")
                    putNullable("description", PortableAccountDataCodec.safeDescription(row.description, 2000)); put("deleted", row.deletedAtMs != null)
                })
            }
            "PLAYLIST_ENTRY" -> dao.localEntryPage(scope.profileId, since, after, 32).map { row ->
                row.id to PortableAccountDataCodec.checked(row.recordId, kind, buildJsonObject {
                    put("version", 1); put("updated_at_ms", row.updatedAtMs); put("playlist_id", row.playlistId); put("track_key", row.trackKey)
                    putNullable("title", PortableAccountDataCodec.safeDescription(row.title, 2000)); putNullable("artist", PortableAccountDataCodec.safeDescription(row.artist, 2000))
                    put("position_key", row.positionKey); put("deleted", row.deleted)
                })
            }
            "TASTE" -> dao.localTastePage(scope.profileId, since, after, 32).map { row ->
                // Unknown recommendation payloads remain intact in their original table.
                val p = Json.parseToJsonElement(row.payloadJson) as? JsonObject
                val recommendation = p?.get("recommendation") as? JsonObject
                val key = (recommendation?.get("recording_id") as? JsonPrimitive)?.contentOrNull
                val feedback = (p?.get("feedback_type") as? JsonPrimitive)?.contentOrNull
                val interaction = when (feedback) { "SELECTED" -> "LIKE"; "DISMISSED" -> "DISLIKE"; else -> null }
                row.eventId to if (row.eventType == "RECOMMENDATION_FEEDBACK_RECORDED" && key != null && interaction != null) {
                    PortableAccountDataCodec.checked(row.eventId, kind, buildJsonObject {
                        put("version", 1); put("updated_at_ms", row.createdAtMs); put("track_key", key); put("interaction", interaction)
                        put("weight", if (feedback == "SELECTED") 1000 else -1000)
                    })
                } else null
            }
            else -> error("ACCOUNT_DATA_INVALID")
        }
        page.mapNotNull { it.second }.forEach { upsert(accountId, it) }
        val complete = page.size < 32
        dao.putCursor(state.copy(cursorId = page.lastOrNull()?.first ?: state.cursorId, complete = complete))
        complete
    }

    suspend fun uploadPage(accountId: String, profileId: String): List<PortableAccountRecordEntity> =
        dao.uploadPage(accountId, cursor(accountId, profileId, UPLOAD).uploadedRevision, 32)

    suspend fun acknowledgeUpload(accountId: String, profileId: String, revision: Long): Unit = database.withWriteTransaction {
        val state = cursor(accountId, profileId, UPLOAD)
        dao.putCursor(state.copy(uploadedRevision = maxOf(state.uploadedRevision, revision)))
    }

    suspend fun applyPage(accountId: String, profileId: String, source: String, page: PortableDataPage): Unit = database.withWriteTransaction {
        PortableAccountDataCodec.uuid(accountId); PortableAccountDataCodec.uuid(profileId)
        require(source in PortableAccountDataCodec.sources && page.records.size <= 32) { "ACCOUNT_DATA_INVALID" }
        check(page.accountId == accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
        val state = cursor(accountId, profileId, "remote:$source")
        page.records.forEach { upsert(accountId, it) }
        dao.putCursor(state.copy(cursorTime = page.cursor?.time ?: state.cursorTime, cursorId = page.cursor?.id ?: state.cursorId, complete = page.next == null))
    }

    /** Public import seam; ownership comes from authenticated transport, never the record document. */
    suspend fun mergeRecords(accountId: String, records: List<PortableDataRecord>): Unit = database.withWriteTransaction {
        PortableAccountDataCodec.uuid(accountId); require(records.size <= 32)
        records.forEach { upsert(accountId, PortableAccountDataCodec.decode(it.wire())) }
    }

    private suspend fun upsert(accountId: String, record: PortableDataRecord) {
        val incoming = PortableAccountDataCodec.decode(record.wire())
        val matches = if (incoming.kind == "LISTENING") dao.listeningMatches(accountId, incoming.document["event_ids"]!!.jsonArray.map { it.jsonPrimitive.content }) else emptyList()
        val exact = dao.record(accountId, incoming.recordId)
        val copies = (matches + listOfNotNull(exact)).distinctBy { it.recordId }.sortedBy { it.recordId }
        var merged = incoming
        copies.forEach { merged = PortableAccountDataCodec.merge(decode(it), merged) }
        val hash = PortableAccountDataCodec.hash(merged.document)
        val previous = dao.record(accountId, merged.recordId)
        if (previous?.contentHash == hash && previous.kind == merged.kind && copies.size <= 1) return
        val revision = Math.addExact(dao.latestRevision(accountId), 1)
        dao.putRecord(PortableAccountRecordEntity(accountId, merged.recordId, merged.kind, PortableAccountDataCodec.canonical(merged.document), hash, revision))
        if (merged.kind == "LISTENING") {
            dao.removeMergedCopies(accountId, copies.map { it.recordId }.filter { it != merged.recordId })
            dao.putAliases(merged.document["event_ids"]!!.jsonArray.map { PortableListeningAliasEntity(accountId, it.jsonPrimitive.content, merged.recordId) })
        }
    }

    companion object {
        const val STANDALONE_PROFILE: String = "legacy-unscoped"
        internal const val PASS: String = "pass"
        internal const val UPLOAD: String = "upload"
        val localKinds: List<String> = listOf("LISTENING", "PREFERENCE", "PLAYLIST", "PLAYLIST_ENTRY", "TASTE")
        fun decode(row: PortableAccountRecordEntity): PortableDataRecord = PortableAccountDataCodec.checked(row.recordId, row.kind, Json.parseToJsonElement(row.documentJson).jsonObject)

        /** RFC4122 UUIDv5, using the same account namespace as server legacy exports. */
        fun stableId(accountId: String, name: String): String {
            val namespace = UUID.fromString(accountId)
            val bytes = ByteBuffer.allocate(16).putLong(namespace.mostSignificantBits).putLong(namespace.leastSignificantBits).array()
            val digest = MessageDigest.getInstance("SHA-1").digest(bytes + ("autplay:portable-" + name).toByteArray(Charsets.UTF_8))
            digest[6] = ((digest[6].toInt() and 15) or 80).toByte(); digest[8] = ((digest[8].toInt() and 63) or 128).toByte()
            val buffer = ByteBuffer.wrap(digest)
            return UUID(buffer.long, buffer.long).toString()
        }
    }
}
