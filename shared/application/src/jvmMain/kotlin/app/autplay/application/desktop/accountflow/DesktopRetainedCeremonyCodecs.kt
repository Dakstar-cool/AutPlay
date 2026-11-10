package app.autplay.application.desktop.accountflow

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as ProfileV2
import app.autplay.application.devicecompatibility.SelfDeviceContractV2 as SelfV2
import app.autplay.application.securestate.*
import java.time.Instant
import java.util.Base64
import kotlinx.serialization.json.*

/** Application restoration only. Existing D1 codecs and all original wire/proof bytes stay unchanged. */
public object DesktopRetainedCeremonyCodecs {
    public fun profileParent(operation: ProfileV2.Operation, original: ByteArray, context: ProfileV2.Context): ProfileV2.SignedCommand {
        secureRequire(operation in setOf(ProfileV2.Operation.ADMISSION_SUBMIT, ProfileV2.Operation.TRUSTED_CHALLENGE))
        val document = SecureJson.parse(original, 16_384)
        val signature = Base64.getUrlDecoder().decode(document.string("proof_b64url"))
        secureRequire(signature.size == 64 && Base64.getUrlEncoder().withoutPadding().encodeToString(signature) == document.string("proof_b64url"))
        val unsigned = SecureJson.encode(JsonObject(document - "proof_b64url"))
        try {
            val prepared = ProfileV2.rawBody(unsigned).use { ProfileV2.prepare(operation, it, context, Instant.now()) }
            val restored = try { ProfileV2.complete(prepared, signature) } finally { prepared.close() }
            try {
                ProfileV2.rawBody(original).use { ProfileV2.requireExactOriginal(restored, it, context) }
                return restored
            } catch (failure: Exception) { restored.close(); throw failure }
        } finally { unsigned.fill(0); signature.fill(0) }
    }

    /** No fresh CLAIM admission. The caller supplies an exact protected owner and retained QR. */
    public fun selfRecipientContext(rawQr: ByteArray, trusted: D1.TrustedM5Identity, platform: D1.Platform,
        publicKeySpki: ByteArray, ceremony: String, expiry: Instant): SelfV2.Context {
        canonicalUuid(ceremony)
        // Existing typed factory validates both trusted identity and the exact canonical P-256 SPKI.
        ProfileV2.admissionSubmitContext(trusted, platform, publicKeySpki, ceremony)
        val value = SecureJson.parse(rawQr, 4096)
        value.exact("expected_server_instance_id", "expected_identity_epoch", "expected_identity_thumbprint_sha256", "expected_api_origin", "expected_stream_origin",
            "format", "version", "contract_version", "schema_version", "ceremony_id", "rendezvous_secret", "expires_at", "source_platform")
        secureRequire(value.string("format") == "autplay-self-device-pairing" && value.number("version") == 2L &&
            value.string("contract_version") == "v2" && value.number("schema_version") == 2L && value.string("ceremony_id") == ceremony &&
            value.string("expected_server_instance_id") == trusted.serverInstanceId && value.number("expected_identity_epoch") == trusted.identityEpoch &&
            value.string("expected_identity_thumbprint_sha256") == trusted.thumbprintSha256 && value.string("expected_api_origin") == trusted.apiOrigin &&
            value.string("expected_stream_origin") == trusted.streamOrigin && Instant.parse(value.string("expires_at")) == expiry)
        val secret = Base64.getUrlDecoder().decode(value.string("rendezvous_secret"))
        try { secureRequire(secret.size == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(secret) == value.string("rendezvous_secret")) }
        finally { secret.fill(0) }
        val source = D1.Platform.entries.singleOrNull { it.name == value.string("source_platform") }
            ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        return SelfV2.Context(trusted, platform, source, ceremony, SelfV2.Operation.CLAIM, expiry, publicKeySpki)
    }
}
