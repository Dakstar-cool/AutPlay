package app.autplay.application.portableaccount

import app.autplay.application.profilepairing.RuntimeLifecycleAction
import app.autplay.data.settings.NonSecretSettings
import java.security.KeyFactory
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject

/** Verify possession and the captured binding immediately before applying an account exit. */
internal fun verifyLifecycleAuthorization(document: PortableAccountDocument, settings: NonSecretSettings,
    action: RuntimeLifecycleAction, operationId: String, authorization: String) {
    require(authorization.length in 2..16_384)
    val envelope = Json.parseToJsonElement(authorization).jsonObject
    require(envelope.keys == setOf("request", "proof"))
    val request = envelope["request"]!!.jsonObject
    val challenge = request["challenge"]!!.jsonObject
    require(request["account"] == document.fields && document.accountId == settings.portableAccountId)
    require(challenge.string("account_id") == document.accountId && challenge.string("operation_id") == operationId &&
        challenge.string("action") == (if (action == RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE) "REVOKE_DEVICE" else action.name))
    val binding = requireNotNull(settings.m5Binding)
    if (action == RuntimeLifecycleAction.DISCONNECT_LOCAL) {
        require(challenge.string("binding_commit_id") == binding.bindingCommitId &&
            challenge.string("server_profile_id") == settings.activeServerProfileId?.value &&
            challenge.string("session_family_id") == binding.sessionFamilyId)
    } else {
        require(challenge.string("server_instance_id") == binding.serverInstanceId &&
            challenge.string("actor_device_id") == settings.deviceId?.value &&
            challenge.string("actor_session_id") == binding.sessionId &&
            challenge.number("identity_epoch") == binding.identityEpoch &&
            challenge.string("identity_thumbprint_sha256") == binding.identityThumbprintSha256)
    }
    val expiry = Instant.parse(challenge.string("expires_at"))
    require(expiry.isAfter(Instant.now()) && expiry.isBefore(Instant.now().plusSeconds(180)))
    val public = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(document.publicKeySpki))
    val bytes = canonicalAccountRequest(request)
    val proof = Base64.getDecoder().decode(envelope.string("proof"))
    try {
        require(proof.size in 8..72) { "ACCOUNT_AUTHENTICATION_FAILED" }
        require(Signature.getInstance("SHA256withECDSA").run {
            initVerify(public); update("autplay:portable-account:reauth:v1\n".toByteArray(Charsets.US_ASCII)); update(bytes)
            verify(proof)
        }) { "ACCOUNT_AUTHENTICATION_FAILED" }
    } finally { bytes.fill(0); proof.fill(0) }
}
