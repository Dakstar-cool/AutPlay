package app.autplay.data.security

import app.autplay.domain.ServerProfileId
import app.autplay.data.network.withAutPlayRedirectPolicy
import java.io.Closeable
import java.nio.charset.StandardCharsets
import java.time.Duration
import java.time.Instant
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

/** Signals that user action is required without classifying immutable journal intent as failed. */
class SessionRequiredException : IllegalStateException("SESSION_REQUIRED")

/** One short-lived bearer copy that callers must destroy after constructing a request. */
data class SessionAccess(
    val token: ByteArray,
    val generation: Long,
) : Closeable {
    override fun close() = token.fill(0)
}

/**
 * Reads profile-scoped credentials, refreshes expiring access tokens, and rotates refresh tokens
 * under one process-wide per-profile mutex. A rejected generation is retried at most once by the
 * caller, while concurrent callers observe the already-rotated generation instead of replaying a
 * single-use refresh token.
 */
class RefreshingSessionCredentials(
    private val authBaseUrl: String,
    private val credentials: CredentialStore,
    client: OkHttpClient = OkHttpClient.Builder()
        .callTimeout(Duration.ofSeconds(30))
        .build(),
    private val now: () -> Instant = Instant::now,
    private val m5Rotation: M5SessionRotationClient? = null,
) {
    private val client = client.withAutPlayRedirectPolicy()
    suspend fun access(profileId: ServerProfileId): SessionAccess {
        val current = readEnvelope(profileId) ?: throw SessionRequiredException()
        if (current.bindingCommitId != null) {
            val rotation = m5Rotation ?: throw SessionRequiredException()
            rotation.persistSuccessor(profileId, current)
            if (current.refreshPending || current.shouldRefresh(now())) return rotateM5(profileId, current.generation)
            return current.toAccess()
        }
        if (current.refreshPending) throw SessionRequiredException()
        return if (current.shouldRefresh(now())) {
            refreshCurrent(profileId, current.generation)
        } else {
            current.toAccess()
        }
    }

    suspend fun refreshAfterRejection(
        profileId: ServerProfileId,
        rejectedGeneration: Long,
    ): SessionAccess {
        val current = readEnvelope(profileId) ?: throw SessionRequiredException()
        return if (current.bindingCommitId != null) rotateM5(profileId, rejectedGeneration) else refreshCurrent(profileId, rejectedGeneration)
    }

    private suspend fun rotateM5(profileId: ServerProfileId, rejectedGeneration: Long): SessionAccess = mutex(profileId).withLock {
        val current = readEnvelope(profileId) ?: throw SessionRequiredException()
        if (current.bindingCommitId == null) throw SessionRequiredException()
        if (current.generation != rejectedGeneration && !current.refreshPending && !current.shouldRefresh(now())) return@withLock current.toAccess()
        val rotation = m5Rotation ?: throw SessionRequiredException()
        val pending = if (current.refreshPending) current else rotation.prepare(profileId, current)
        if (!current.refreshPending) persist(profileId, pending)
        try {
            val successor = rotation.execute(profileId, pending)
            persist(profileId, successor)
            rotation.persistSuccessor(profileId, successor)
            successor.toAccess()
        } catch (error: SessionRequiredException) {
            throw error
        } catch (_: Exception) {
            // Retain the exact encrypted request for an idempotent replay after a lost response.
            throw SessionRequiredException()
        }
    }

    private suspend fun refreshCurrent(
        profileId: ServerProfileId,
        rejectedGeneration: Long,
    ): SessionAccess = mutex(profileId).withLock {
        val current = readEnvelope(profileId) ?: throw SessionRequiredException()
        if (current.refreshPending) throw SessionRequiredException()
        if (current.bindingCommitId != null) throw SessionRequiredException()
        if (current.generation != rejectedGeneration && !current.shouldRefresh(now())) {
            return@withLock current.toAccess()
        }
        val refreshToken = current.refreshToken ?: throw SessionRequiredException()
        try {
            persist(profileId, current.copy(refreshPending = true))
        } catch (error: Exception) {
            // No request was sent, so retrying this preflight write cannot replay the refresh token.
            throw error
        }
        try {
            val rotated = rotate(refreshToken, current.generation)
            persist(profileId, rotated)
            rotated.toAccess()
        } catch (error: SessionRequiredException) {
            throw error
        } catch (error: Exception) {
            // The POST may have committed. The durable pending marker prevents token replay.
            throw SessionRequiredException()
        }
    }

    private suspend fun persist(profileId: ServerProfileId, value: SessionCredentialEnvelope) {
        val encoded = SessionCredentialEnvelopeCodec.encode(value)
        try {
            credentials.write(profileId, encoded)
        } finally {
            encoded.fill(0)
        }
    }

    private suspend fun readEnvelope(profileId: ServerProfileId): SessionCredentialEnvelope? {
        val material = credentials.read(profileId) ?: return null
        return try {
            SessionCredentialEnvelopeCodec.decode(material)
        } finally {
            material.fill(0)
        }
    }

    private suspend fun rotate(
        refreshToken: String,
        generation: Long,
    ): SessionCredentialEnvelope = withContext(Dispatchers.IO) {
        val body = buildJsonObject { put("refresh_token", refreshToken) }
            .toString()
            .toRequestBody(JSON_MEDIA_TYPE)
        val request = Request.Builder()
            .url(authBaseUrl.trimEnd('/') + "/auth/refresh")
            .header("Accept", "application/json")
            .header("Cache-Control", "no-store")
            .post(body)
            .build()
        client.newCall(request).execute().use { response ->
            if (response.code == 401 || response.code == 403) throw SessionRequiredException()
            check(response.isSuccessful) { "AUTH_REFRESH_HTTP_${response.code}" }
            val source = response.body.source()
            check(!source.request(MAX_REFRESH_RESPONSE_BYTES + 1L)) {
                "AUTH_REFRESH_RESPONSE_TOO_LARGE"
            }
            val value = Json.parseToJsonElement(source.readUtf8()).jsonObject
            SessionCredentialEnvelope(
                accessToken = value.requiredString("access_token"),
                refreshToken = value.requiredString("refresh_token"),
                generation = generation + 1,
            )
        }
    }

    private fun SessionCredentialEnvelope.toAccess(): SessionAccess = SessionAccess(
        accessToken.toByteArray(StandardCharsets.UTF_8),
        generation,
    )

    private fun SessionCredentialEnvelope.shouldRefresh(instant: Instant): Boolean {
        val expiresAt = accessToken.jwtExpiresAt() ?: return false
        return !expiresAt.isAfter(instant.plusSeconds(REFRESH_SKEW_SECONDS))
    }

    private fun String.jwtExpiresAt(): Instant? = runCatching {
        val payload = split('.').takeIf { it.size == 3 }?.get(1) ?: return null
        val value = Json.parseToJsonElement(
            Base64.getUrlDecoder().decode(payload).toString(StandardCharsets.UTF_8),
        ).jsonObject
        value["exp"]?.jsonPrimitive?.longOrNull?.let(Instant::ofEpochSecond)
    }.getOrNull()

    private fun kotlinx.serialization.json.JsonObject.requiredString(name: String): String =
        requireNotNull(this[name]) { "AUTH_REFRESH_RESPONSE_INVALID" }.jsonPrimitive.content

    private fun mutex(profileId: ServerProfileId): Mutex =
        PROFILE_MUTEXES.computeIfAbsent(profileId.value) { Mutex() }

    private companion object {
        val JSON_MEDIA_TYPE = "application/json".toMediaType()
        const val MAX_REFRESH_RESPONSE_BYTES = 64 * 1024
        const val REFRESH_SKEW_SECONDS = 30L
        val PROFILE_MUTEXES = ConcurrentHashMap<String, Mutex>()
    }
}
