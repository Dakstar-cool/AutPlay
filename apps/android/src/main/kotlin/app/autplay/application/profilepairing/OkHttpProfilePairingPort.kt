package app.autplay.application.profilepairing

import android.util.Base64
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.M5RequestSigner
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.readAccessToken
import app.autplay.data.network.withAutPlayRedirectPolicy
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.nio.charset.StandardCharsets
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.PublicKey
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.time.Duration
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.erdtman.jcs.JsonCanonicalizer

/** Bounded, fail-closed M5 transport. It never logs response bodies or credential material. */
class OkHttpProfilePairingPort(
    private val originForProfile: (ServerProfileId) -> String?,
    private val credentials: CredentialStore,
    private val deviceKeys: M5DeviceKeyStore,
    private val keyAliasForProfile: (ServerProfileId) -> String = { "autplay.m5.${it.value}" },
    private val appVersion: String = "0.1.0",
    private val allowUnsafeDevelopmentHttp: Boolean = false,
    client: OkHttpClient = OkHttpClient.Builder().callTimeout(Duration.ofSeconds(20)).build(),
) : ProfilePairingPort {
    private val client = client.withAutPlayRedirectPolicy()
    override suspend fun discovery(apiOrigin: String) = requestPublic(apiOrigin, "/pairing/discovery", MAX_DISCOVERY_BYTES) { root ->
        val envelope = signedEnvelope(root, DISCOVERY_DOMAIN, MAX_DISCOVERY_BYTES)
        val payload = envelope.payload
        val spki = b64(payload.string("identity_public_key_spki_b64"), url = false)
        try {
            require(sha256(spki) == payload.string("identity_thumbprint_sha256"))
            verifyP1363(spki, DISCOVERY_DOMAIN, envelope.hash, envelope.signature)
            val identity = TrustedServerIdentity(payload.string("server_instance_id"), payload.long("identity_epoch"), payload.string("identity_thumbprint_sha256"))
            val normalizedApi = OriginNormalizer.normalize(payload.string("api_origin"), allowUnsafeDevelopmentHttp)
            val normalizedStream = OriginNormalizer.normalize(payload.string("stream_origin"), allowUnsafeDevelopmentHttp)
            trustedKeys[identityKey(identity)] = spki.copyOf()
            DiscoveryDocument(identity, payload.string("label_hint"), normalizedApi, normalizedStream, payload.array("supported_api_majors").map { it.jsonPrimitive.int }.toSet(), Instant.parse(payload.string("expires_at")), spki.copyOf())
        } finally { spki.fill(0) }
    }

    override suspend fun capabilities(profileId: ServerProfileId, snapshot: PairingFlowSnapshot) = authenticated(profileId, "/profile/capabilities", null, MAX_CAPABILITIES_BYTES) { root ->
        val envelope = signedEnvelope(root, CAPABILITIES_DOMAIN, MAX_CAPABILITIES_BYTES)
        val payload = envelope.payload
        val identity = TrustedServerIdentity(payload.string("server_instance_id"), payload.long("identity_epoch"), snapshot.expectedIdentityThumbprintSha256)
        val spki = trustedKeys[identityKey(identity)] ?: throw Protocol("server_identity_changed")
        verifyP1363(spki, CAPABILITIES_DOMAIN, envelope.hash, envelope.signature)
        val state = CapabilityState(identity, UserId(payload.string("user_id")), DeviceId(payload.string("device_id")), payload.int("api_major"), payload.long("capability_revision"), Instant.parse(payload.string("expires_at")).toEpochMilli(), payload.array("operations").map { it.jsonPrimitive.content }.toSet(), payload["required_features"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty())
        CapabilityDocument(state, envelope.canonical, sha256(envelope.canonical))
    }

    override fun seedTrustedIdentity(identity: TrustedServerIdentity, publicKeySpki: ByteArray) {
        try {
            require(sha256(publicKeySpki) == identity.identityThumbprintSha256)
            KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(publicKeySpki))
            trustedKeys[identityKey(identity)] = publicKeySpki.copyOf()
        } finally {
            publicKeySpki.fill(0)
        }
    }

    override suspend fun exchange(request: EnrollmentExchangeCommand): PairingNetworkResult<EnrollmentSession> {
        val s = request.snapshot; val alias = keyAliasForProfile(s.serverProfileId); deviceKeys.ensure(alias)
        val secret = request.invitationSecret
        return try {
            val fields = linkedMapOf<String, Any>("contract_version" to "v1", "schema_version" to 1, "exchange_id" to requireNotNull(s.operationId), "binding_commit_id" to requireNotNull(s.bindingCommitId), "invitation_id" to request.invitationId, "invitation_secret" to b64(secret, true), "expected_server_instance_id" to s.expectedServerInstanceId, "expected_identity_epoch" to s.expectedIdentityEpoch, "expected_identity_thumbprint_sha256" to s.expectedIdentityThumbprintSha256, "expected_api_origin" to OriginNormalizer.normalize(s.apiOrigin, allowUnsafeDevelopmentHttp), "expected_stream_origin" to OriginNormalizer.normalize(s.streamOrigin, allowUnsafeDevelopmentHttp), "expected_user_id" to requireNotNull(s.expectedUserId).value, "device_name" to request.deviceName, "platform" to "ANDROID", "app_version" to appVersion, "device_public_key_spki_b64" to b64(deviceKeys.publicKeySpki(alias), false), "device_key_thumbprint_sha256" to deviceKeys.publicKeyThumbprintSha256(alias), "next_refresh_token_sha256" to request.nextRefreshTokenSha256, "client_nonce_b64url" to request.clientNonceB64Url)
            signedJson(fields, alias, EXCHANGE_DOMAIN).use { body -> secretRequest(s.apiOrigin, "/pairing/enrollment/exchanges", body.bytes, MAX_SECRET_RESPONSE_BYTES) { response -> exchangeSession(response, s, request.nextRefreshToken.copyOf(), request.nextRefreshTokenSha256) } }
        } catch (e: Protocol) { PairingNetworkResult.Failure(e.code, e.retryAfterMs) } catch (_: Exception) { PairingNetworkResult.Failure("server_unavailable") } finally { secret.fill(0); request.nextRefreshToken.fill(0) }
    }

    override suspend fun createInvitation(profileId: ServerProfileId, operationId: String, expiresInSeconds: Int): PairingNetworkResult<ManagedInvitation> {
        requireCanonicalUuid(operationId)
        require(expiresInSeconds in 60..1800)
        return authenticatedSecret(profileId, "/pairing/enrollment/invitations", "{\"contract_version\":\"v1\",\"schema_version\":1,\"operation_id\":\"$operationId\",\"expires_in_seconds\":$expiresInSeconds}", MAX_SECRET_RESPONSE_BYTES) { root ->
            validateSecretInvitation(root)
            ManagedInvitation(root.string("invitation_id"), root.string("expires_at"), b64(root.string("invitation_secret"), true), root.toString())
        }
    }

    override suspend fun cancelInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): PairingNetworkResult<Unit> {
        requireCanonicalUuid(invitationId); requireCanonicalUuid(operationId)
        return authenticated(profileId, "/pairing/enrollment/invitations/$invitationId/cancel", "{\"contract_version\":\"v1\",\"schema_version\":1,\"operation_id\":\"$operationId\"}", MAX_LIFECYCLE_BYTES) { }
    }

    override suspend fun rotate(request: SessionRotationCommand): PairingNetworkResult<EnrollmentSession> {
        val s = request.snapshot; val alias = keyAliasForProfile(s.serverProfileId); deviceKeys.ensure(alias)
        val material = credentials.read(s.serverProfileId) ?: return PairingNetworkResult.Failure("auth_attention_required")
        return try {
            val refresh = SessionCredentialEnvelopeCodec.decode(material).refreshToken?.toByteArray(StandardCharsets.US_ASCII) ?: return PairingNetworkResult.Failure("auth_attention_required")
            try {
                val currentRefreshToken = refresh.toString(StandardCharsets.US_ASCII)
                require(Regex("^[A-Za-z0-9_-]{43}$").matches(currentRefreshToken))
                val fields = linkedMapOf<String, Any>("contract_version" to "v1", "schema_version" to 1, "rotation_id" to requireNotNull(s.operationId), "expected_server_instance_id" to s.expectedServerInstanceId, "expected_identity_epoch" to s.expectedIdentityEpoch, "device_id" to requireNotNull(s.expectedDeviceId).value, "parent_session_id" to request.parentSessionId, "current_generation" to request.parentGeneration, "current_refresh_token" to currentRefreshToken, "next_refresh_token_sha256" to request.nextRefreshTokenSha256)
                signedJson(fields, alias, ROTATION_DOMAIN).use { body -> secretRequest(s.apiOrigin, "/account/sessions/rotate", body.bytes, MAX_SECRET_RESPONSE_BYTES) { response -> rotationSession(response, s, request.nextRefreshToken.copyOf(), request.nextRefreshTokenSha256) } }
            } finally { refresh.fill(0) }
        } catch (e: Protocol) { PairingNetworkResult.Failure(e.code, e.retryAfterMs) } catch (_: Exception) { PairingNetworkResult.Failure("server_unavailable") } finally { material.fill(0); request.nextRefreshToken.fill(0) }
    }

    override suspend fun devices(profileId: ServerProfileId) = authenticated(profileId, "/account/devices", null, MAX_LIST_BYTES) { root -> root.array("devices").map { item -> item.jsonObject.let { DeviceSummary(DeviceId(it.string("device_id")), it.string("device_name"), false) } } }
    override suspend fun sessions(profileId: ServerProfileId) = authenticated(profileId, "/account/sessions", null, MAX_LIST_BYTES) { root -> root.array("sessions").map { item -> item.jsonObject.let { SessionSummary(it.string("session_id"), DeviceId(it.string("device_id")), it.long("generation"), it.bool("current")) } } }
    override suspend fun lifecycle(profileId: ServerProfileId, command: LifecycleCommand): PairingNetworkResult<Unit> {
        val path = when (command.action) { LifecycleAction.LOGOUT_CURRENT -> "/account/sessions/current/logout"; LifecycleAction.LOGOUT_ALL -> "/account/sessions/logout-all"; LifecycleAction.REVOKE_DEVICE -> "/account/devices/${requireNotNull(command.targetDeviceId).value}/revoke" }
        val reason = command.reasonCode?.let { ",\"reason_code\":\"$it\"" }.orEmpty()
        return authenticated(profileId, path, "{\"contract_version\":\"v1\",\"schema_version\":1,\"operation_id\":\"${command.operationId}\"$reason}", MAX_LIFECYCLE_BYTES) { }
    }

    private suspend fun <T> requestPublic(origin: String, path: String, limit: Long, parse: (JsonObject) -> T): PairingNetworkResult<T> = execute(OriginNormalizer.normalize(origin, allowUnsafeDevelopmentHttp), path, null, limit, false, parse)
    private suspend fun <T> authenticated(profile: ServerProfileId, path: String, body: String?, limit: Long, parse: (JsonObject) -> T): PairingNetworkResult<T> {
        val origin = originForProfile(profile) ?: return PairingNetworkResult.Failure("not_connected")
        val token = credentials.readAccessToken(profile) ?: return PairingNetworkResult.Failure("auth_attention_required")
        return try { execute(OriginNormalizer.normalize(origin, allowUnsafeDevelopmentHttp), path, body, limit, false, parse, token) } finally { token.fill(0) }
    }
    private suspend fun <T> authenticatedSecret(profile: ServerProfileId, path: String, body: String, limit: Long, parse: (JsonObject) -> T): PairingNetworkResult<T> {
        val origin = originForProfile(profile) ?: return PairingNetworkResult.Failure("not_connected")
        val token = credentials.readAccessToken(profile) ?: return PairingNetworkResult.Failure("auth_attention_required")
        return try { execute(OriginNormalizer.normalize(origin, allowUnsafeDevelopmentHttp), path, body, limit, true, parse, token) } finally { token.fill(0) }
    }
    private suspend fun <T> secretRequest(origin: String, path: String, body: ByteArray, limit: Long, parse: (JsonObject) -> T): PairingNetworkResult<T> = execute(OriginNormalizer.normalize(origin, allowUnsafeDevelopmentHttp), path, body.toString(StandardCharsets.UTF_8), limit, true, parse)
    private suspend fun <T> execute(origin: String, path: String, body: String?, limit: Long, secret: Boolean, parse: (JsonObject) -> T, token: ByteArray? = null): PairingNetworkResult<T> = withContext(Dispatchers.IO) {
        try {
            val builder = Request.Builder().url(origin.trimEnd('/') + "/api/v1" + path).header("Accept", "application/json").header("Cache-Control", "no-store").header("Pragma", "no-cache")
            token?.let { builder.header("Authorization", "Bearer ${it.toString(StandardCharsets.UTF_8)}") }
            if (body == null) builder.get() else builder.post(body.toRequestBody(JSON))
            client.newCall(builder.build()).execute().use { response ->
                val raw = response.body.source().let { source -> if (source.request(limit + 1)) throw Protocol("server_unavailable"); source.readUtf8() }
                if (secret && (!response.header("Cache-Control").orEmpty().contains("no-store") || !response.header("Pragma").orEmpty().contains("no-cache"))) throw Protocol("server_unavailable")
                val root = runCatching { Json.parseToJsonElement(raw).jsonObject }.getOrElse { throw Protocol("server_unavailable") }
                if (!response.isSuccessful) throw error(root, response.header("Retry-After"))
                PairingNetworkResult.Success(parse(root))
            }
        } catch (e: Protocol) { PairingNetworkResult.Failure(e.code, e.retryAfterMs) } catch (_: Exception) { PairingNetworkResult.Failure("server_unavailable") }
    }
    private fun signedEnvelope(root: JsonObject, domain: String, max: Long): Envelope {
        require(root.string("contract_version") == "v1" && root.int("schema_version") == 1 && root.string("signature_algorithm") == "ES256-P1363")
        val payload = root["payload"]!!.jsonObject; val canonical = JsonCanonicalizer(payload.toString()).encodedString.toByteArray(StandardCharsets.UTF_8)
        require(canonical.size <= max); val hash = sha256Bytes(canonical); require(hash.joinToString("") { "%02x".format(it.toInt() and 0xff) } == root.string("payload_sha256")); return Envelope(payload, canonical, hash, b64(root.string("signature_b64url"), true))
    }
    private fun signedJson(fields: Map<String, Any>, alias: String, domain: String): SecretBody {
        val unsignedFields = fields + mapOf("signature_algorithm" to "ES256-P1363")
        val unsigned = Json.encodeToString(
            kotlinx.serialization.json.JsonObject.serializer(),
            mapToJson(unsignedFields),
        )
        val signed = M5RequestSigner.sign(
            deviceKeys,
            alias,
            domain,
            JsonCanonicalizer(unsigned).encodedString.toByteArray(StandardCharsets.UTF_8),
        )
        val value = mapToJson(
            unsignedFields + mapOf(
                "request_sha256" to signed.requestSha256,
                "device_signature_b64url" to signed.signatureB64Url,
            ),
        )
        return SecretBody(
            JsonCanonicalizer(value.toString()).encodedString.toByteArray(StandardCharsets.UTF_8),
        )
    }
    private fun exchangeSession(root: JsonObject, s: PairingFlowSnapshot, refresh: ByteArray, expectedRefreshHash: String): EnrollmentSession { validateSecret(root); require(root.string("binding_commit_id") == s.bindingCommitId && root.string("server_instance_id") == s.expectedServerInstanceId && root.string("user_id") == s.expectedUserId?.value && root.long("refresh_generation") == 0L && sha256(refresh) == expectedRefreshHash); return EnrollmentSession(DeviceId(root.string("device_id")), root.string("session_id"), root.string("session_id"), 0, root.string("access_token").toByteArray(StandardCharsets.UTF_8), refresh) }
    private fun rotationSession(root: JsonObject, s: PairingFlowSnapshot, refresh: ByteArray, expectedRefreshHash: String): EnrollmentSession { validateSecret(root); require(root.string("parent_session_id").isNotBlank() && sha256(refresh) == expectedRefreshHash); return EnrollmentSession(requireNotNull(s.expectedDeviceId), root.string("session_id"), root.string("family_id"), root.long("generation"), root.string("access_token").toByteArray(StandardCharsets.UTF_8), refresh) }
    private fun validateSecret(root: JsonObject) { require(root.string("contract_version") == "v1" && root.int("schema_version") == 1 && root.string("access_token").length in 32..8192) }
    private fun validateSecretInvitation(root: JsonObject) {
        require(root.string("contract_version") == "v1" && root.int("schema_version") == 1)
        requireCanonicalUuid(root.string("invitation_id"))
        require(b64(root.string("invitation_secret"), true).size == 32)
    }
    private fun error(root: JsonObject, retryHeader: String?): Protocol {
        val envelope = root["error"]?.jsonObject
        val code = envelope?.get("code")?.jsonPrimitive?.contentOrNull
            ?: root["error_code"]?.jsonPrimitive?.contentOrNull
            ?: "server_unavailable"
        val seconds = envelope?.get("retry_after_seconds")?.jsonPrimitive?.contentOrNull?.toLongOrNull()
            ?: root["retry_after_seconds"]?.jsonPrimitive?.contentOrNull?.toLongOrNull()
            ?: retryHeader?.toLongOrNull()
        return Protocol(code, seconds?.coerceIn(1, 3600)?.times(1000))
    }
    private fun verifyP1363(spki: ByteArray, domain: String, hash: ByteArray, signature: ByteArray) { require(signature.size == 64); val key: PublicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)); Signature.getInstance("SHA256withECDSA").apply { initVerify(key); update(domain.toByteArray(StandardCharsets.US_ASCII)); update(hash); verify(p1363ToDer(signature)) || throw Protocol("server_identity_changed") } }
    private fun mapToJson(fields: Map<String, Any>) = JsonObject(fields.mapValues { (_, v) -> when (v) { is String -> JsonPrimitive(v); is Int -> JsonPrimitive(v); is Long -> JsonPrimitive(v); is Boolean -> JsonPrimitive(v); else -> error("unsupported canonical field") } })
    private fun b64(value: String, url: Boolean) = if (url) Base64.decode(value, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP) else Base64.decode(value, Base64.NO_WRAP)
    private fun b64(value: ByteArray, url: Boolean) = Base64.encodeToString(value, if (url) Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP else Base64.NO_WRAP)
    private fun sha256(value: ByteArray) = sha256Bytes(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }
    private fun sha256Bytes(value: ByteArray) = MessageDigest.getInstance("SHA-256").digest(value)
    private fun identityKey(value: TrustedServerIdentity) = "${value.serverInstanceId}:${value.identityEpoch}:${value.identityThumbprintSha256}"
    private fun JsonObject.string(name: String) = requireNotNull(this[name]) { "missing $name" }.jsonPrimitive.content
    private fun JsonObject.int(name: String) = requireNotNull(this[name]).jsonPrimitive.int
    private fun JsonObject.long(name: String) = requireNotNull(this[name]).jsonPrimitive.long
    private fun JsonObject.bool(name: String) = requireNotNull(this[name]).jsonPrimitive.boolean
    private fun JsonObject.array(name: String): JsonArray = requireNotNull(this[name]).jsonArray
    private data class Envelope(val payload: JsonObject, val canonical: ByteArray, val hash: ByteArray, val signature: ByteArray)
    private class SecretBody(val bytes: ByteArray) : AutoCloseable { override fun close() = bytes.fill(0) }
    private class Protocol(val code: String, val retryAfterMs: Long? = null) : RuntimeException(code)
    private companion object { val JSON = "application/json".toMediaType(); const val MAX_DISCOVERY_BYTES = 16L * 1024; const val MAX_CAPABILITIES_BYTES = 64L * 1024; const val MAX_SECRET_RESPONSE_BYTES = 16L * 1024; const val MAX_LIST_BYTES = 64L * 1024; const val MAX_LIFECYCLE_BYTES = 4L * 1024; const val DISCOVERY_DOMAIN = "AutPlay discovery v1\n"; const val CAPABILITIES_DOMAIN = "AutPlay capabilities v1\n"; const val EXCHANGE_DOMAIN = "AutPlay enrollment exchange v1\n"; const val ROTATION_DOMAIN = "AutPlay session rotation v1\n"; val trustedKeys = ConcurrentHashMap<String, ByteArray>() }
}

private fun p1363ToDer(value: ByteArray): ByteArray { fun integer(offset: Int): ByteArray { val stripped = value.copyOfRange(offset, offset + 32).dropWhile { it == 0.toByte() }.toByteArray(); val raw = if (stripped.isEmpty()) byteArrayOf(0) else stripped; return if (raw[0].toInt() and 0x80 != 0) byteArrayOf(0) + raw else raw }; val r = integer(0); val s = integer(32); return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 0x02, r.size.toByte()) + r + byteArrayOf(0x02, s.size.toByte()) + s }
