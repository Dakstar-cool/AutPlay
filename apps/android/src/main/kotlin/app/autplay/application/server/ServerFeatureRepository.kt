package app.autplay.application.server

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.SessionAccess
import app.autplay.data.security.SessionRequiredException
import app.autplay.domain.ServerProfileId
import java.io.ByteArrayOutputStream
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.time.Duration
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

data class ServerHealth(
    val apiReady: Boolean,
    val streamLive: Boolean,
)

data class RemoteLibraryEntry(
    val libraryEntryId: String,
    val userTrackRefId: String,
    val source: String,
    val availabilityStatus: String,
    val rowVersion: Long,
)

data class RemotePlaylist(
    val playlistId: String,
    val name: String,
    val description: String?,
    val rowVersion: Long,
)

data class RemoteHistoryEntry(
    val listeningEventId: String,
    val userTrackRefId: String,
    val recordingId: String?,
    val playedMs: Long,
    val eventOrigin: String,
)

data class RemoteLibrarySnapshot(
    val entries: List<RemoteLibraryEntry>,
    val playlists: List<RemotePlaylist>,
    val history: List<RemoteHistoryEntry>,
)

data class RemoteImportStart(
    val importJobId: String,
    val deliveryJobId: String,
    val replayed: Boolean,
)

data class RemoteImportEntry(
    val sourceRowKey: String,
    val importEntryId: String,
    val status: String,
    val resolverState: String?,
    val decisionId: String?,
    val candidateCount: Int,
    val errorCode: String?,
)

data class RemoteImportReport(
    val importJobId: String,
    val state: String,
    val progressCurrent: Int,
    val progressTotal: Int,
    val counts: Map<String, Int>,
    val entries: List<RemoteImportEntry>,
    val nextAfter: String?,
)

data class VaultUploadResult(
    val uploadId: String,
    val offset: Long,
    val expectedSize: Long,
    val state: String,
)

data class ServerRecommendationItem(
    val recordingId: String,
    val sourceRank: Int,
    val score: Double,
    val reasonCode: String,
    val section: String,
)

data class ServerRecommendationResult(
    val requestId: String,
    val replay: String,
    val items: List<ServerRecommendationItem>,
)

data class RemoteDiscoveryPolicy(
    val policyId: String,
    val canonicalArtistId: String,
    val providerArtistId: String,
    val discoveryMode: String,
    val importMode: String,
    val automationEnabled: Boolean,
    val revision: Int,
    val lastCheckedAt: String?,
    val nextEligibleAt: String?,
)

data class RemoteDiscoveryRun(
    val runId: String,
    val policyId: String,
    val policyRevision: Int,
    val state: String,
    val observedCount: Int,
    val selectedCount: Int,
    val pageCount: Int,
    val createdAt: String,
    val completedAt: String?,
    val errorCode: String?,
)

data class RemoteDiscoveryCandidate(
    val candidateId: String,
    val runId: String,
    val title: String,
    val artist: String,
    val album: String?,
    val releasedAt: String?,
    val disposition: String,
    val acquisitionState: String,
    val selectedAutomatically: Boolean,
)

data class RemoteDiscoverySnapshot(
    val policies: List<RemoteDiscoveryPolicy>,
    val runs: List<RemoteDiscoveryRun>,
)

data class DiscoveryPolicyCommand(
    val canonicalArtistId: String,
    val providerArtistId: String,
    val discoveryMode: String,
    val importMode: String,
    val expectedRevision: Int?,
)

/**
 * Typed access to the already-delivered server surfaces that are not owned by Sync or Wave.
 * Local Room projections remain authoritative for normal Android rendering and mutation.
 */
class ServerFeatureRepository(
    serverBaseUrl: String,
    streamBaseUrl: String,
    private val profileId: ServerProfileId,
    private val credentials: CredentialStore,
    private val client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(Duration.ofSeconds(45))
        .build(),
    private val m5Rotation: M5SessionRotationClient? = null,
) {
    private val serverRoot = serverBaseUrl.trimEnd('/')
    private val streamRoot = streamBaseUrl.trimEnd('/')
    private val apiBaseUrl = "$serverRoot/api/v1"
    private val sessionCredentials = RefreshingSessionCredentials(apiBaseUrl, credentials, client, m5Rotation = m5Rotation)

    suspend fun health(): ServerHealth = withContext(Dispatchers.IO) {
        ServerHealth(
            apiReady = executePublic("$serverRoot/health/ready") in 200..299,
            streamLive = executePublic("$streamRoot/health/live") in 200..299,
        )
    }

    suspend fun librarySnapshot(limit: Int = 50): RemoteLibrarySnapshot {
        require(limit in 1..100)
        val entries = page("/library/entries", limit).map { value ->
            RemoteLibraryEntry(
                libraryEntryId = value.requiredString("library_entry_id", UUID_TEXT_LENGTH),
                userTrackRefId = value.requiredString("user_track_ref_id", UUID_TEXT_LENGTH),
                source = value.requiredString("source", MAX_SHORT_TEXT),
                availabilityStatus = value.requiredString("availability_status", MAX_SHORT_TEXT),
                rowVersion = value.requiredLong("row_version"),
            )
        }
        val playlists = page("/library/playlists", limit).map { value ->
            RemotePlaylist(
                playlistId = value.requiredString("playlist_id", UUID_TEXT_LENGTH),
                name = value.requiredString("name", MAX_DISPLAY_TEXT),
                description = value.optionalString("description", MAX_DISPLAY_TEXT),
                rowVersion = value.requiredLong("row_version"),
            )
        }
        val history = page("/library/history", limit).map { value ->
            RemoteHistoryEntry(
                listeningEventId = value.requiredString("listening_event_id", UUID_TEXT_LENGTH),
                userTrackRefId = value.requiredString("user_track_ref_id", UUID_TEXT_LENGTH),
                recordingId = value.optionalString("recording_id", UUID_TEXT_LENGTH),
                playedMs = value.requiredLong("played_ms"),
                eventOrigin = value.requiredString("event_origin", MAX_SHORT_TEXT),
            )
        }
        return RemoteLibrarySnapshot(entries, playlists, history)
    }

    suspend fun searchLibrary(query: String, limit: Int = 50): List<RemoteLibraryEntry> {
        val normalized = query.trim()
        require(normalized.isNotEmpty() && normalized.length <= 200)
        require(limit in 1..100)
        val url = "$apiBaseUrl/library/search".toHttpUrl().newBuilder()
            .addQueryParameter("q", normalized)
            .addQueryParameter("limit", limit.toString())
            .build()
        return authorized(Request.Builder().url(url).get().build()).jsonObject
            .requiredArray("items", limit)
            .map { value ->
                RemoteLibraryEntry(
                    libraryEntryId = value.requiredString("library_entry_id", UUID_TEXT_LENGTH),
                    userTrackRefId = value.requiredString("user_track_ref_id", UUID_TEXT_LENGTH),
                    source = value.requiredString("source", MAX_SHORT_TEXT),
                    availabilityStatus = value.requiredString("availability_status", MAX_SHORT_TEXT),
                    rowVersion = value.requiredLong("row_version"),
                )
            }
    }

    /** Resolves only an owner-authorized stable Variant ID; URLs and credentials stay runtime-only. */
    suspend fun playbackVariantId(serverUserTrackRefId: String): String? {
        requireUuidLike(serverUserTrackRefId)
        val root = try {
            authorized(
                Request.Builder()
                    .url("$apiBaseUrl/vault/user-tracks/$serverUserTrackRefId/playback-variant")
                    .get()
                    .build(),
            ).jsonObject
        } catch (error: IllegalStateException) {
            if (error.message == "SERVER_HTTP_404") return null
            throw error
        }
        return root.requiredString("audio_variant_id", UUID_TEXT_LENGTH).also(::requireUuidLike)
    }

    suspend fun startImport(
        payload: ByteArray,
        format: String,
        materialize: Boolean,
    ): RemoteImportStart {
        require(payload.isNotEmpty() && payload.size <= MAX_IMPORT_BYTES)
        require(format in setOf("CSV", "JSON", "HTML"))
        val url = "$apiBaseUrl/imports".toHttpUrl().newBuilder()
            .addQueryParameter("format", format)
            .addQueryParameter("schema_version", "1")
            .addQueryParameter("mode", if (materialize) "MATERIALIZE" else "LIBRARY_ONLY")
            .build()
        val root = authorized(
            Request.Builder()
                .url(url)
                .post(payload.toRequestBody("application/octet-stream".toMediaType()))
                .build(),
        ).jsonObject
        return RemoteImportStart(
            importJobId = root.requiredString("import_job_id", UUID_TEXT_LENGTH),
            deliveryJobId = root.requiredString("delivery_job_id", UUID_TEXT_LENGTH),
            replayed = root.requiredBoolean("replayed"),
        )
    }

    suspend fun importReport(importJobId: String, after: String? = null): RemoteImportReport {
        requireUuidLike(importJobId)
        val url = "$apiBaseUrl/imports/$importJobId".toHttpUrl().newBuilder()
            .addQueryParameter("limit", MAX_IMPORT_REPORT_ENTRIES.toString())
            .apply { after?.let { require(it.length <= 1_000); addQueryParameter("after", it) } }
            .build()
        return decodeImportReport(authorized(Request.Builder().url(url).get().build()).jsonObject)
    }

    suspend fun cancelImport(importJobId: String) {
        requireUuidLike(importJobId)
        authorized(emptyPost("/imports/$importJobId/cancel"))
    }

    suspend fun resumeImport(importJobId: String): RemoteImportStart {
        requireUuidLike(importJobId)
        val root = authorized(emptyPost("/imports/$importJobId/resume")).jsonObject
        return RemoteImportStart(
            root.requiredString("import_job_id", UUID_TEXT_LENGTH),
            root.requiredString("delivery_job_id", UUID_TEXT_LENGTH),
            root.requiredBoolean("replayed"),
        )
    }

    suspend fun reviewImport(
        importJobId: String,
        entry: RemoteImportEntry,
        action: String,
        selectedRank: Int? = null,
    ) {
        requireUuidLike(importJobId)
        requireUuidLike(entry.importEntryId)
        val predecessor = requireNotNull(entry.decisionId) { "SERVER_IMPORT_DECISION_REQUIRED" }
        require(action in setOf("ACCEPT", "REJECT", "KEEP_UNRESOLVED", "CREATE_RECORDING"))
        require(selectedRank == null || selectedRank in 1..100)
        val body = buildJsonObject {
            put("predecessor_decision_id", predecessor)
            put("action", action)
            put("selected_rank", selectedRank?.let(::JsonPrimitive) ?: JsonNull)
            put("idempotency_key", "android:${entry.importEntryId}:$predecessor:$action:${selectedRank ?: 0}")
        }.toString()
        authorized(
            Request.Builder()
                .url("$apiBaseUrl/imports/$importJobId/entries/${entry.importEntryId}/review")
                .post(body.toRequestBody(JSON_MEDIA_TYPE))
                .build(),
        )
    }

    suspend fun createVaultUpload(
        recordingId: String,
        expectedSize: Long,
        declaredSha256: String,
        idempotencyKey: String,
    ): VaultUploadResult {
        requireUuidLike(recordingId)
        require(expectedSize in 1..MAX_UPLOAD_BYTES)
        require(SHA256_REGEX.matches(declaredSha256))
        require(idempotencyKey.isNotBlank() && idempotencyKey.length <= 200)
        return createUpload(recordingId, expectedSize, declaredSha256, idempotencyKey)
    }

    suspend fun vaultUploadStatus(uploadId: String): VaultUploadResult {
        requireUuidLike(uploadId)
        return decodeUpload(
            authorized(Request.Builder().url("$apiBaseUrl/vault/uploads/$uploadId").get().build()).jsonObject,
        )
    }

    suspend fun appendVaultUpload(
        uploadId: String,
        offset: Long,
        chunkIndex: Long,
        chunk: ByteArray,
    ): Long {
        requireUuidLike(uploadId)
        require(offset >= 0 && chunkIndex >= 0 && chunk.isNotEmpty() && chunk.size <= MAX_UPLOAD_CHUNK_BYTES)
        return appendUpload(uploadId, offset, chunkIndex, chunk)
    }

    suspend fun completeVaultUpload(uploadId: String): VaultUploadResult {
        requireUuidLike(uploadId)
        return completeUpload(uploadId)
    }

    suspend fun cancelVaultUpload(uploadId: String) {
        requireUuidLike(uploadId)
        authorized(
            Request.Builder().url("$apiBaseUrl/vault/uploads/$uploadId").delete().build(),
            expectBody = false,
        )
    }

    suspend fun recommendations(limit: Int = 25): ServerRecommendationResult =
        recommendationRequest("/recommendations", limit)

    suspend fun homeRecommendations(limit: Int = 25): ServerRecommendationResult {
        require(limit in 1..100)
        val body = recommendationBody(limit)
        val root = authorized(
            Request.Builder().url("$apiBaseUrl/home").post(body.toRequestBody(JSON_MEDIA_TYPE)).build(),
        ).jsonObject
        val requestId = root.requiredString("recommendation_request_id", UUID_TEXT_LENGTH)
        val items = root.requiredArray("sections", MAX_RECOMMENDATION_SECTIONS).flatMap { section ->
            section.requiredArray("items", limit).map(::decodeRecommendationItem)
        }.take(limit)
        return ServerRecommendationResult(requestId, "served", items)
    }

    suspend fun exactRecommendationReplay(requestId: String): ServerRecommendationResult {
        requireUuidLike(requestId)
        return decodeRecommendationResult(
            authorized(Request.Builder().url("$apiBaseUrl/recommendations/$requestId").get().build()).jsonObject,
        )
    }

    suspend fun algorithmicRecommendationReplay(requestId: String): ServerRecommendationResult {
        requireUuidLike(requestId)
        return decodeRecommendationResult(authorized(emptyPost("/recommendations/$requestId/replay")).jsonObject)
    }

    suspend fun discoveryAutomationSnapshot(): RemoteDiscoverySnapshot {
        val root = authorized(
            Request.Builder().url("$apiBaseUrl/discovery/automation/snapshot").get().build(),
        ).jsonObject
        root.requireDiscoveryContract()
        return RemoteDiscoverySnapshot(
            policies = root.requiredArray("policies", MAX_DISCOVERY_POLICIES).map(::decodeDiscoveryPolicy),
            runs = root.requiredArray("runs", MAX_DISCOVERY_RUNS).map(::decodeDiscoveryRun),
        )
    }

    suspend fun discoveryCandidates(runId: String): List<RemoteDiscoveryCandidate> {
        requireUuidLike(runId)
        val root = authorized(
            Request.Builder()
                .url("$apiBaseUrl/discovery/automation/runs/$runId/candidates")
                .get()
                .build(),
        ).jsonObject
        root.requireDiscoveryContract()
        return root.requiredArray("candidates", MAX_DISCOVERY_CANDIDATES).map(::decodeDiscoveryCandidate)
    }

    suspend fun setDiscoveryPolicy(command: DiscoveryPolicyCommand, operationId: String) {
        requireUuidLike(command.canonicalArtistId)
        requireUuidLike(operationId)
        require(command.providerArtistId.matches(Regex("[0-9]{1,20}")))
        require(command.discoveryMode in setOf("DISABLED", "MANUAL_ONLY", "SCHEDULED"))
        require(command.importMode in setOf("REVIEW_REQUIRED", "AUTO_IMPORT"))
        require(command.expectedRevision == null || command.expectedRevision >= 1)
        val body = buildJsonObject {
            put("contract_version", DISCOVERY_CONTRACT_VERSION)
            put("schema_version", 1)
            put("operation_id", operationId)
            put("action", "SET_ARTIST_POLICY")
            put("canonical_artist_id", command.canonicalArtistId)
            put("provider_artist_id", command.providerArtistId)
            put("discovery_mode", command.discoveryMode)
            put("import_mode", command.importMode)
            put("automation_enabled", command.discoveryMode == "SCHEDULED")
            put("expected_policy_revision", command.expectedRevision?.let(::JsonPrimitive) ?: JsonNull)
            put(
                "consequence_confirmation",
                if (command.importMode == "AUTO_IMPORT") JsonPrimitive(AUTO_IMPORT_CONFIRMATION) else JsonNull,
            )
        }.toString()
        discoveryCommand(body)
    }

    suspend fun startDiscovery(policyId: String, operationId: String) {
        requireUuidLike(policyId)
        requireUuidLike(operationId)
        discoveryCommand(
            buildJsonObject {
                put("contract_version", DISCOVERY_CONTRACT_VERSION)
                put("schema_version", 1)
                put(
                    "operation_id",
                    operationId,
                )
                put("action", "START_DISCOVERY")
                put("policy_id", policyId)
            }.toString(),
        )
    }

    suspend fun actOnDiscoveryCandidate(candidateId: String, action: String, operationId: String) {
        requireUuidLike(candidateId)
        requireUuidLike(operationId)
        require(action in setOf("SELECT_CANDIDATE", "RETRY_CANDIDATE", "IGNORE_CANDIDATE"))
        discoveryCommand(
            buildJsonObject {
                put("contract_version", DISCOVERY_CONTRACT_VERSION)
                put("schema_version", 1)
                put(
                    "operation_id",
                    operationId,
                )
                put("action", action)
                put("candidate_id", candidateId)
            }.toString(),
        )
    }

    suspend fun logout() {
        sessionAction(emptyPost("/auth/logout"))
    }

    suspend fun logoutAll() {
        sessionAction(emptyPost("/auth/logout-all"))
    }

    suspend fun revokeDevice(deviceId: String) {
        requireUuidLike(deviceId)
        sessionAction(emptyPost("/devices/$deviceId/revoke"))
    }

    private suspend fun sessionAction(request: Request) {
        try {
            authorized(request, expectBody = false)
        } catch (_: SessionRequiredException) {
            // The server already considers this local credential unusable.
        } catch (error: IllegalStateException) {
            if (error.message != "SERVER_HTTP_401") throw error
        }
        credentials.clear(profileId)
    }

    private suspend fun page(path: String, limit: Int): List<JsonObject> {
        val url = "$apiBaseUrl$path".toHttpUrl().newBuilder()
            .addQueryParameter("limit", limit.toString())
            .build()
        return authorized(Request.Builder().url(url).get().build()).jsonObject.requiredArray("items", limit)
    }

    private suspend fun recommendationRequest(path: String, limit: Int): ServerRecommendationResult {
        require(limit in 1..100)
        val root = authorized(
            Request.Builder()
                .url(apiBaseUrl + path)
                .post(recommendationBody(limit).toRequestBody(JSON_MEDIA_TYPE))
                .build(),
        ).jsonObject
        return decodeRecommendationResult(root)
    }

    private fun recommendationBody(limit: Int): String = buildJsonObject {
        put("context", "GENERAL")
        put("limit", limit)
        put("exploration", 0.2)
        put("seed", 0)
        put("pipeline_key", "cpu-baseline")
        put("pipeline_version", JsonNull)
        put("shadow", false)
    }.toString()

    private fun decodeRecommendationResult(root: JsonObject): ServerRecommendationResult =
        ServerRecommendationResult(
            requestId = root.requiredString("recommendation_request_id", UUID_TEXT_LENGTH),
            replay = root.requiredString("replay", MAX_SHORT_TEXT),
            items = root.requiredArray("items", MAX_RECOMMENDATION_ITEMS).map(::decodeRecommendationItem),
        )

    private fun decodeRecommendationItem(value: JsonObject): ServerRecommendationItem = ServerRecommendationItem(
        recordingId = value.requiredString("recording_id", UUID_TEXT_LENGTH),
        sourceRank = value.requiredInt("source_rank"),
        score = value.requiredDouble("score"),
        reasonCode = value.requiredString("reason_code", MAX_SHORT_TEXT),
        section = value.requiredString("section", MAX_SHORT_TEXT),
    )

    private suspend fun discoveryCommand(body: String) {
        authorized(
            Request.Builder()
                .url("$apiBaseUrl/discovery/automation/commands")
                .post(body.toRequestBody(JSON_MEDIA_TYPE))
                .build(),
        ).jsonObject.requireDiscoveryContract()
    }

    private fun JsonObject.requireDiscoveryContract() {
        require(requiredString("contract_version", MAX_SHORT_TEXT) == DISCOVERY_CONTRACT_VERSION) {
            "SERVER_RESPONSE_INVALID"
        }
        require(requiredInt("schema_version") == 1) { "SERVER_RESPONSE_INVALID" }
    }

    private fun decodeDiscoveryPolicy(value: JsonObject) = RemoteDiscoveryPolicy(
        policyId = value.requiredString("policy_id", UUID_TEXT_LENGTH),
        canonicalArtistId = value.requiredString("canonical_artist_id", UUID_TEXT_LENGTH),
        providerArtistId = value.requiredString("provider_artist_id", 20),
        discoveryMode = value.requiredString("discovery_mode", MAX_SHORT_TEXT),
        importMode = value.requiredString("import_mode", MAX_SHORT_TEXT),
        automationEnabled = value.requiredBoolean("automation_enabled"),
        revision = value.requiredInt("revision"),
        lastCheckedAt = value.optionalString("last_checked_at", MAX_TIMESTAMP_TEXT),
        nextEligibleAt = value.optionalString("next_eligible_at", MAX_TIMESTAMP_TEXT),
    )

    private fun decodeDiscoveryRun(value: JsonObject) = RemoteDiscoveryRun(
        runId = value.requiredString("run_id", UUID_TEXT_LENGTH),
        policyId = value.requiredString("policy_id", UUID_TEXT_LENGTH),
        policyRevision = value.requiredInt("policy_revision"),
        state = value.requiredString("state", MAX_SHORT_TEXT),
        observedCount = value.requiredInt("observed_count"),
        selectedCount = value.requiredInt("selected_count"),
        pageCount = value.requiredInt("page_count"),
        createdAt = value.requiredString("created_at", MAX_TIMESTAMP_TEXT),
        completedAt = value.optionalString("completed_at", MAX_TIMESTAMP_TEXT),
        errorCode = value.optionalString("error_code", MAX_SHORT_TEXT),
    )

    private fun decodeDiscoveryCandidate(value: JsonObject) = RemoteDiscoveryCandidate(
        candidateId = value.requiredString("candidate_id", UUID_TEXT_LENGTH),
        runId = value.requiredString("run_id", UUID_TEXT_LENGTH),
        title = value.requiredString("title", MAX_DISPLAY_TEXT),
        artist = value.requiredString("artist", MAX_DISPLAY_TEXT),
        album = value.optionalString("album", MAX_DISPLAY_TEXT),
        releasedAt = value.optionalString("released_at", MAX_TIMESTAMP_TEXT),
        disposition = value.requiredString("disposition", MAX_SHORT_TEXT),
        acquisitionState = value.requiredString("acquisition_state", MAX_SHORT_TEXT),
        selectedAutomatically = value.requiredBoolean("selected_automatically"),
    )

    private fun decodeImportReport(root: JsonObject): RemoteImportReport = RemoteImportReport(
        importJobId = root.requiredString("import_job_id", UUID_TEXT_LENGTH),
        state = root.requiredString("state", MAX_SHORT_TEXT),
        progressCurrent = root.requiredInt("progress_current"),
        progressTotal = root.requiredInt("progress_total"),
        counts = root.requiredObject("counts").mapValues { (_, value) -> value.jsonPrimitive.int },
        entries = root.requiredArray("entries", MAX_IMPORT_REPORT_ENTRIES).map { entry ->
            RemoteImportEntry(
                sourceRowKey = entry.requiredString("source_row_key", MAX_DISPLAY_TEXT),
                importEntryId = entry.requiredString("import_entry_id", UUID_TEXT_LENGTH),
                status = entry.requiredString("status", MAX_SHORT_TEXT),
                resolverState = entry.optionalString("resolver_state", MAX_SHORT_TEXT),
                decisionId = entry.optionalString("decision_id", UUID_TEXT_LENGTH),
                candidateCount = entry.requiredInt("candidate_count"),
                errorCode = entry.optionalString("error_code", MAX_SHORT_TEXT),
            )
        },
        nextAfter = root.optionalString("next_after", 1_000),
    )

    private suspend fun createUpload(
        recordingId: String,
        size: Long,
        sha256: String,
        key: String,
    ): VaultUploadResult {
        val body = buildJsonObject {
            put("recording_id", recordingId)
            put("expected_size", size)
            put("declared_sha256", sha256)
        }.toString()
        val root = authorized(
            Request.Builder()
                .url("$apiBaseUrl/vault/uploads")
                .header("Idempotency-Key", key)
                .post(body.toRequestBody(JSON_MEDIA_TYPE))
                .build(),
        ).jsonObject
        return decodeUpload(root)
    }

    private suspend fun appendUpload(
        uploadId: String,
        offset: Long,
        chunkIndex: Long,
        chunk: ByteArray,
    ): Long {
        val result = authorizedRaw(
            Request.Builder()
                .url("$apiBaseUrl/vault/uploads/$uploadId")
                .header("Upload-Offset", offset.toString())
                .header("Upload-Chunk-Index", chunkIndex.toString())
                .header("X-Chunk-SHA256", sha256(chunk))
                .patch(chunk.toRequestBody(OFFSET_MEDIA_TYPE))
                .build(),
            expectBody = false,
        )
        return result.headers["Upload-Offset"]?.toLongOrNull()
            ?: error("VAULT_UPLOAD_OFFSET_MISSING")
    }

    private suspend fun completeUpload(uploadId: String): VaultUploadResult =
        decodeUpload(authorized(emptyPost("/vault/uploads/$uploadId/complete")).jsonObject)

    private fun decodeUpload(root: JsonObject): VaultUploadResult = VaultUploadResult(
        uploadId = root.requiredString("upload_id", UUID_TEXT_LENGTH),
        offset = root.requiredLong("offset"),
        expectedSize = root.requiredLong("expected_size"),
        state = root.requiredString("state", MAX_SHORT_TEXT),
    )

    private fun emptyPost(path: String): Request = Request.Builder()
        .url(apiBaseUrl + path)
        .post(ByteArray(0).toRequestBody(null))
        .build()

    private suspend fun authorized(request: Request, expectBody: Boolean = true) =
        authorizedRaw(request, expectBody).body?.let(Json::parseToJsonElement)
            ?: JsonObject(emptyMap())

    private suspend fun authorizedRaw(request: Request, expectBody: Boolean): HttpResult =
        withContext(Dispatchers.IO) {
            var access = sessionCredentials.access(profileId)
            try {
                var result = executeOnce(request, access, expectBody)
                if (result.status == 401) {
                    val rejectedGeneration = access.generation
                    access.close()
                    access = sessionCredentials.refreshAfterRejection(profileId, rejectedGeneration)
                    result = executeOnce(request, access, expectBody)
                }
                if (result.status !in 200..299) error("SERVER_HTTP_${result.status}")
                result
            } finally {
                access.close()
            }
        }

    private fun executeOnce(request: Request, access: SessionAccess, expectBody: Boolean): HttpResult {
        val authorized = request.newBuilder()
            .header("Authorization", "Bearer ${access.token.toString(StandardCharsets.UTF_8)}")
            .header("Cache-Control", "no-store")
            .build()
        return client.newCall(authorized).execute().use { response ->
            HttpResult(
                response.code,
                if (expectBody) response.body.readBoundedUtf8(MAX_RESPONSE_BYTES) else null,
                response.headers.toMap(),
            )
        }
    }

    private fun executePublic(url: String): Int = client.newCall(Request.Builder().url(url).get().build())
        .execute().use { response -> response.code }

    private fun JsonObject.requiredArray(name: String, maxItems: Int): List<JsonObject> {
        val array = this[name] as? JsonArray ?: error("SERVER_RESPONSE_INVALID")
        require(array.size <= maxItems) { "SERVER_RESPONSE_INVALID" }
        return array.map { it as? JsonObject ?: error("SERVER_RESPONSE_INVALID") }
    }

    private fun JsonObject.requiredObject(name: String): JsonObject =
        this[name] as? JsonObject ?: error("SERVER_RESPONSE_INVALID")

    private fun JsonObject.requiredString(name: String, maxLength: Int): String {
        val value = this[name] as? JsonPrimitive
        if (value == null || !value.isString || value.content.isEmpty() || value.content.length > maxLength) {
            error("SERVER_RESPONSE_INVALID")
        }
        return value.content
    }

    private fun JsonObject.optionalString(name: String, maxLength: Int): String? {
        val value = this[name] ?: return null
        if (value is JsonNull) return null
        val primitive = value as? JsonPrimitive
        if (primitive == null || !primitive.isString || primitive.content.length > maxLength) {
            error("SERVER_RESPONSE_INVALID")
        }
        return primitive.content
    }

    private fun JsonObject.requiredLong(name: String): Long {
        val value = (this[name] as? JsonPrimitive)?.longOrNull ?: error("SERVER_RESPONSE_INVALID")
        require(value >= 0) { "SERVER_RESPONSE_INVALID" }
        return value
    }

    private fun JsonObject.requiredInt(name: String): Int {
        val value = (this[name] as? JsonPrimitive)?.content?.toIntOrNull()
            ?: error("SERVER_RESPONSE_INVALID")
        require(value >= 0) { "SERVER_RESPONSE_INVALID" }
        return value
    }

    private fun JsonObject.requiredDouble(name: String): Double {
        val value = (this[name] as? JsonPrimitive)?.content?.toDoubleOrNull()
            ?: error("SERVER_RESPONSE_INVALID")
        require(value.isFinite()) { "SERVER_RESPONSE_INVALID" }
        return value
    }

    private fun JsonObject.requiredBoolean(name: String): Boolean =
        (this[name] as? JsonPrimitive)?.content?.toBooleanStrictOrNull()
            ?: error("SERVER_RESPONSE_INVALID")

    private fun okhttp3.ResponseBody.readBoundedUtf8(maxBytes: Int): String {
        val length = contentLength()
        if (length > maxBytes) error("SERVER_RESPONSE_TOO_LARGE")
        val output = ByteArrayOutputStream(minOf(maxBytes, if (length > 0) length.toInt() else 8_192))
        byteStream().use { input ->
            val buffer = ByteArray(8_192)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                if (total > maxBytes) error("SERVER_RESPONSE_TOO_LARGE")
                output.write(buffer, 0, count)
            }
        }
        return output.toByteArray().toString(StandardCharsets.UTF_8)
    }

    private fun requireUuidLike(value: String) {
        require(UUID_REGEX.matches(value)) { "SERVER_IDENTIFIER_INVALID" }
    }

    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(value).toHex()

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

    private data class HttpResult(val status: Int, val body: String?, val headers: Map<String, String>)

    private companion object {
        val JSON_MEDIA_TYPE = "application/json".toMediaType()
        val OFFSET_MEDIA_TYPE = "application/offset+octet-stream".toMediaType()
        val UUID_REGEX = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        val SHA256_REGEX = Regex("[0-9a-f]{64}")
        const val UUID_TEXT_LENGTH = 36
        const val MAX_SHORT_TEXT = 200
        const val MAX_DISPLAY_TEXT = 4_096
        const val MAX_IMPORT_BYTES = 2 * 1_024 * 1_024
        const val MAX_IMPORT_REPORT_ENTRIES = 200
        const val MAX_UPLOAD_CHUNK_BYTES = 1 * 1_024 * 1_024
        const val MAX_UPLOAD_BYTES = 2L * 1_024 * 1_024 * 1_024
        const val MAX_RESPONSE_BYTES = 2 * 1_024 * 1_024
        const val MAX_RECOMMENDATION_ITEMS = 100
        const val MAX_RECOMMENDATION_SECTIONS = 20
        const val MAX_DISCOVERY_POLICIES = 100
        const val MAX_DISCOVERY_RUNS = 50
        const val MAX_DISCOVERY_CANDIDATES = 50
        const val MAX_TIMESTAMP_TEXT = 64
        const val DISCOVERY_CONTRACT_VERSION = "release-discovery-v1"
        const val AUTO_IMPORT_CONFIRMATION =
            "AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"
    }
}
