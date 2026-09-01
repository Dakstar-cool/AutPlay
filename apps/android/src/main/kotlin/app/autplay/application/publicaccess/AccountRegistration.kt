package app.autplay.application.publicaccess

import app.autplay.application.profilepairing.AdmissionProof
import app.autplay.application.profilepairing.TrustedServerIdentity
import app.autplay.application.profilepairing.requireCanonicalUuid
import app.autplay.application.profilepairing.requireSha256
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.M5RequestSigner
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

data class AccountRegistrationRequest(
    val registrationId: String,
    val bindingCommitId: String,
    val invitation: AccountInvitation,
    val deviceName: String,
    val appVersion: String,
    val keyAlias: String,
    val successorRefreshToken: ByteArray,
    val clientNonce: ByteArray,
) : AutoCloseable {
    init { requireCanonicalUuid(registrationId); requireCanonicalUuid(bindingCommitId); require(deviceName.length in 1..120); require(appVersion.length in 1..32); require(successorRefreshToken.size == 43 && successorRefreshToken.all { it.toInt().toChar().let { char -> char.isLetterOrDigit() || char == '-' || char == '_' } }); require(clientNonce.size == 16) }
    override fun close() { successorRefreshToken.fill(0); clientNonce.fill(0); invitation.close() }
}

data class AccountRegistrationResponse(
    val registrationId: String,
    val bindingCommitId: String,
    val serverInstanceId: String,
    val userId: String,
    val accountDisplayName: String,
    val deviceId: String,
    val sessionId: String,
    val refreshAbsoluteExpiresAtEpochMs: Long,
    val receiptExpiresAtEpochMs: Long,
    val accessExpiresAtEpochMs: Long,
    val accessToken: ByteArray,
)

fun interface AccountRegistrationPort {
    /** Must be unauthenticated and must add Cache-Control: no-store. */
    suspend fun redeem(request: SignedAccountRegistrationRequest): Result<AccountRegistrationResponse>
}

data class SignedAccountRegistrationRequest(
    val registration: AccountRegistrationRequest,
    val canonicalJson: ByteArray,
    val requestSha256: String,
    val deviceSignatureB64Url: String,
) : AutoCloseable {
    override fun close() { canonicalJson.fill(0); registration.close() }
}

/** RFC8785 request construction. The signing domain is intentionally not an S1/M5 domain. */
object AccountRegistrationProof {
    const val DOMAIN = "AutPlay account registration v1\n"

    fun create(invitation: AccountInvitation, deviceName: String, appVersion: String, keys: M5DeviceKeyStore): SignedAccountRegistrationRequest {
        val registrationId = UUID.randomUUID().toString()
        val keyAlias = "autplay.public.registration.$registrationId"
        keys.ensure(keyAlias)
        val refresh = Base64.getUrlEncoder().withoutPadding().encode(
            ByteArray(32).also(SecureRandom()::nextBytes),
        )
        val nonce = ByteArray(16).also(SecureRandom()::nextBytes)
        val request = AccountRegistrationRequest(registrationId, UUID.randomUUID().toString(), invitation, deviceName, appVersion, keyAlias, refresh, nonce)
        return sign(request, keys)
    }

    fun sign(request: AccountRegistrationRequest, keys: M5DeviceKeyStore): SignedAccountRegistrationRequest {
        val fields = linkedMapOf<String, kotlinx.serialization.json.JsonElement>(
            "contract_version" to JsonPrimitive("v1"), "schema_version" to JsonPrimitive(1),
            "registration_id" to JsonPrimitive(request.registrationId), "binding_commit_id" to JsonPrimitive(request.bindingCommitId),
            "invitation_id" to JsonPrimitive(request.invitation.invitationId), "invitation_secret" to JsonPrimitive(b64(request.invitation.secret)),
            "expected_server_instance_id" to JsonPrimitive(request.invitation.serverInstanceId), "expected_identity_epoch" to JsonPrimitive(request.invitation.identityEpoch),
            "expected_identity_thumbprint_sha256" to JsonPrimitive(request.invitation.identityThumbprintSha256),
            "expected_api_origin" to JsonPrimitive(request.invitation.apiOrigin), "expected_stream_origin" to JsonPrimitive(request.invitation.streamOrigin),
            "expected_account_display_name" to JsonPrimitive(request.invitation.accountDisplayName), "device_name" to JsonPrimitive(request.deviceName),
            "platform" to JsonPrimitive("ANDROID"), "app_version" to JsonPrimitive(request.appVersion),
            "device_public_key_spki_b64" to JsonPrimitive(Base64.getEncoder().encodeToString(keys.publicKeySpki(request.keyAlias))),
            "device_key_thumbprint_sha256" to JsonPrimitive(keys.publicKeyThumbprintSha256(request.keyAlias)),
            "next_refresh_token_sha256" to JsonPrimitive(sha256(request.successorRefreshToken)), "client_nonce_b64url" to JsonPrimitive(b64(request.clientNonce)),
            "signature_algorithm" to JsonPrimitive("ES256-P1363"),
        )
        val unsigned = JsonCanonicalizer(JsonObject(fields).toString()).encodedString.toByteArray(Charsets.UTF_8)
        val signed = M5RequestSigner.sign(keys, request.keyAlias, DOMAIN, unsigned)
        unsigned.fill(0)
        val final = JsonObject(fields + mapOf("request_sha256" to JsonPrimitive(signed.requestSha256), "device_signature_b64url" to JsonPrimitive(signed.signatureB64Url)))
        return SignedAccountRegistrationRequest(request, JsonCanonicalizer(final.toString()).encodedString.toByteArray(Charsets.UTF_8), signed.requestSha256, signed.signatureB64Url)
    }

    /** Reconstructs an exact lost-response request from encrypted PA2-only state and signs it afresh. */
    fun resume(canonicalRequest: ByteArray, successorRefreshToken: ByteArray, keys: M5DeviceKeyStore): SignedAccountRegistrationRequest {
        val root = Json.parseToJsonElement(canonicalRequest.toString(Charsets.UTF_8)).jsonObject
        fun text(name: String) = root[name]?.jsonPrimitive?.content ?: error("ACCOUNT_REGISTRATION_PENDING_INVALID")
        val registrationId = text("registration_id")
        requireCanonicalUuid(registrationId)
        val alias = "autplay.public.registration.$registrationId"
        keys.ensure(alias)
        require(Base64.getEncoder().encodeToString(keys.publicKeySpki(alias)) == text("device_public_key_spki_b64")) { "ACCOUNT_REGISTRATION_KEY_CHANGED" }
        require(keys.publicKeyThumbprintSha256(alias) == text("device_key_thumbprint_sha256")) { "ACCOUNT_REGISTRATION_KEY_CHANGED" }
        val invitation = AccountInvitation(
            text("invitation_id"), text("expected_server_instance_id"), text("expected_identity_epoch").toLong(),
            text("expected_identity_thumbprint_sha256"), text("expected_api_origin"), text("expected_stream_origin"),
            text("expected_account_display_name"), java.time.Instant.EPOCH, java.time.Instant.MAX,
            Base64.getUrlDecoder().decode(text("invitation_secret")),
        )
        return sign(AccountRegistrationRequest(registrationId, text("binding_commit_id"), invitation, text("device_name"), text("app_version"), alias, successorRefreshToken, Base64.getUrlDecoder().decode(text("client_nonce_b64url"))), keys)
    }

    fun expectedIdentity(invitation: AccountInvitation) = TrustedServerIdentity(invitation.serverInstanceId, invitation.identityEpoch, invitation.identityThumbprintSha256)
    private fun b64(value: ByteArray) = Base64.getUrlEncoder().withoutPadding().encodeToString(value)
    private fun sha256(value: ByteArray) = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }
}
