package app.autplay.application.face

import app.autplay.domain.face.FaceTimelineIdentityV2
import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.erdtman.jcs.JsonCanonicalizer

class FaceProjectionV2Exception : IllegalArgumentException("ml.face.projection_invalid")

data class VerifiedFaceProjectionV2(
    val projectionId: String,
    val serverProfileId: String,
    val userId: String,
    val identity: FaceTimelineIdentityV2,
    val resultSha256: String,
    val byteSize: Long,
    val activationEpoch: Long,
    val policyGeneration: Long,
    val redirectGeneration: Long,
    val issuedAtMs: Long,
    val authorizedUntilMs: Long,
    val requiredArtifactSetSha256: String,
    val artifactPolicyListSha256: String,
    val serverInstanceId: String,
    val serverIdentityEpoch: Long,
    val serverIdentityThumbprintSha256: String,
    val serverKeyId: String,
)

/** Pure lease verifier. Playback still needs current authority and an exact local source proof. */
object FaceProjectionV2Codec {
    private val uuid = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    private val hash = Regex("[0-9a-f]{64}")
    private val integerToken = Regex("-?(0|[1-9][0-9]*)")
    private val signatureToken = Regex("[A-Za-z0-9_-]{86}")
    private val roles = setOf("ENCODER_WEIGHTS", "INTERPRETER_EXPORT", "CALIBRATION",
        "PREPROCESSING_EXECUTABLE", "DECODER_PROBE", "TIMELINE_CODEC")
    private val fields = setOf(
        "schema_version", "projection_id", "server_profile_id", "user_id", "timeline_identity",
        "semantic_key_sha256", "result_sha256", "byte_size", "activation_epoch",
        "policy_generation", "redirect_generation", "required_role_cardinality", "artifact_policy",
        "required_artifact_set_sha256", "artifact_policy_list_sha256", "offline_lease_ms",
        "derived_output_disposition", "source_presentation_map_sha256", "issued_at_ms",
        "authorized_until_ms", "server_instance_id", "server_identity_epoch",
        "server_identity_thumbprint_sha256", "server_key_id", "state",
        "signature_algorithm", "signature_b64url",
    )
    private val policyFields = setOf("role", "artifact_sha256", "decision_sequence",
        "decision_generation", "max_offline_revocation_lag_ms", "disposition")
    private val identityFields = setOf("schema_version", "timeline_codec_version", "source_timebase",
        "recording_id", "audio_variant_id", "source_sha256", "decoded_sample_rate",
        "decoded_sample_count", "source_presentation_map_sha256", "embedding_model_id",
        "embedding_manifest_sha256", "semantic_interpreter_id", "interpreter_manifest_sha256",
        "preprocessing_sha256", "calibration_sha256", "execution_profile_sha256")
    private val leaseDomain = "autplay.face.projection-lease.v2\u0000".toByteArray(Charsets.UTF_8)
    private val requiredDomain = "autplay.face.required-artifact-set.v1\u0000".toByteArray(Charsets.UTF_8)
    private val policyDomain = "autplay.face.artifact-policy-list.v1\u0000".toByteArray(Charsets.UTF_8)
    private const val MAX_JSON_INTEGER = 9_007_199_254_740_991L

    fun verify(
        data: ByteArray,
        expectedServerProfileId: String,
        expectedUserId: String,
        expectedServerInstanceId: String,
        expectedServerIdentityEpoch: Long,
        expectedServerKeyId: String,
        pinnedIdentitySpki: ByteArray,
        trustedNowMs: Long,
    ): VerifiedFaceProjectionV2 {
        if (data.isEmpty() || data.size > 65_536) fail()
        try {
            val root = boundedFaceJson(data) as? JsonObject ?: fail()
            if (root.keys != fields || integer(root.getValue("schema_version"), 2, 2) != 2L ||
                string(root.getValue("state")) != "ACTIVE" ||
                string(root.getValue("signature_algorithm")) != "ES256-P1363"
            ) fail()
            val projectionId = uuid(root.getValue("projection_id"))
            val profileId = uuid(root.getValue("server_profile_id"))
            val userId = uuid(root.getValue("user_id"))
            val serverId = uuid(root.getValue("server_instance_id"))
            if (profileId != expectedServerProfileId || userId != expectedUserId ||
                serverId != expectedServerInstanceId ||
                integer(root.getValue("server_identity_epoch"), 1) != expectedServerIdentityEpoch ||
                string(root.getValue("server_key_id")) != expectedServerKeyId
            ) fail()
            val key = KeyFactory.getInstance("EC")
                .generatePublic(X509EncodedKeySpec(pinnedIdentitySpki)) as? ECPublicKey ?: fail()
            val p256 = AlgorithmParameters.getInstance("EC").apply {
                init(ECGenParameterSpec("secp256r1"))
            }.getParameterSpec(ECParameterSpec::class.java)
            if (key.params.order != p256.order || key.params.generator != p256.generator ||
                key.params.curve != p256.curve || key.params.cofactor != p256.cofactor ||
                digest(root.getValue("server_identity_thumbprint_sha256")) != sha256(pinnedIdentitySpki)
            ) fail()
            val identityRaw = root.getValue("timeline_identity") as? JsonObject ?: fail()
            val identity = identity(identityRaw)
            if (digest(root.getValue("semantic_key_sha256")) != FaceV2IdentityCodec.semanticKey(identity) ||
                digest(root.getValue("source_presentation_map_sha256")) != identity.sourcePresentationMapSha256
            ) fail()
            val byteSize = integer(root.getValue("byte_size"), 1, 1_048_576)
            val activationEpoch = integer(root.getValue("activation_epoch"), 1)
            val policyGeneration = integer(root.getValue("policy_generation"), 1)
            val redirectGeneration = integer(root.getValue("redirect_generation"), 0)
            val issued = integer(root.getValue("issued_at_ms"), 0)
            val authorized = integer(root.getValue("authorized_until_ms"), 1)
            if (trustedNowMs !in 0..MAX_JSON_INTEGER || issued > trustedNowMs + 300_000 ||
                trustedNowMs >= authorized) fail()
            val cardinalities = root.getValue("required_role_cardinality") as? JsonObject ?: fail()
            if (cardinalities.keys != roles) fail()
            val counts = roles.associateWith { role -> integer(cardinalities.getValue(role), 0, 256) }
            if (counts.getValue("ENCODER_WEIGHTS") < 1 || counts.getValue("INTERPRETER_EXPORT") < 1) fail()
            val rawPolicy = root.getValue("artifact_policy") as? JsonArray ?: fail()
            if (rawPolicy.size !in 2..256) fail()
            val policy = rawPolicy.map(::policyEntry)
            val sorted = policy.sortedWith(compareBy<PolicyEntry>({ it.role }, { it.artifactHash }))
            if (policy != sorted || policy.map { it.role to it.artifactHash }.toSet().size != policy.size ||
                roles.any { role -> policy.count { it.role == role }.toLong() != counts.getValue(role) }
            ) fail()
            val requiredDocument = buildJsonObject {
                put("v", 1)
                put("entries", JsonArray(policy.map { entry -> buildJsonObject {
                    put("role", entry.role)
                    put("artifact_sha256", entry.artifactHash)
                } }))
            }
            val policyDocument = buildJsonObject {
                put("v", 1)
                put("entries", JsonArray(policy.map { entry -> buildJsonObject {
                    put("role", entry.role)
                    put("artifact_sha256", entry.artifactHash)
                    put("decision_sequence", entry.sequence)
                    put("decision_generation", entry.generation)
                    put("max_offline_revocation_lag_ms", entry.lagMs)
                    put("disposition", entry.disposition)
                } }))
            }
            val requiredHash = sha256(requiredDomain + canonical(requiredDocument))
            val policyHash = sha256(policyDomain + canonical(policyDocument))
            val lease = minOf(604_800_000L, policy.minOf { it.lagMs })
            val disposition = if (policy.any { it.disposition == "DELETE_AFTER_LEASE" })
                "DELETE_AFTER_LEASE" else "RETAIN_NON_DISTRIBUTABLE"
            if (digest(root.getValue("required_artifact_set_sha256")) != requiredHash ||
                digest(root.getValue("artifact_policy_list_sha256")) != policyHash ||
                integer(root.getValue("offline_lease_ms"), 1, 604_800_000) != lease ||
                string(root.getValue("derived_output_disposition")) != disposition ||
                authorized - issued != lease
            ) fail()
            val signatureText = string(root.getValue("signature_b64url"))
            if (!signatureText.matches(signatureToken)) fail()
            val signature = Base64.getUrlDecoder().decode(signatureText)
            if (signature.size != 64 || Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(signature) != signatureText) fail()
            val unsigned = JsonObject(root.filterKeys { it != "signature_b64url" })
            val valid = Signature.getInstance("SHA256withECDSA").apply {
                initVerify(key)
                update(leaseDomain)
                update(canonical(unsigned))
            }.verify(p1363ToDer(signature))
            if (!valid) fail()
            return VerifiedFaceProjectionV2(projectionId, profileId, userId, identity,
                digest(root.getValue("result_sha256")), byteSize, activationEpoch,
                policyGeneration, redirectGeneration, issued, authorized, requiredHash, policyHash,
                serverId, expectedServerIdentityEpoch,
                digest(root.getValue("server_identity_thumbprint_sha256")), expectedServerKeyId)
        } catch (_: Exception) {
            fail()
        }
    }

    private data class PolicyEntry(
        val role: String,
        val artifactHash: String,
        val sequence: Long,
        val generation: Long,
        val lagMs: Long,
        val disposition: String,
    )

    private fun policyEntry(value: JsonElement): PolicyEntry {
        val row = value as? JsonObject ?: fail()
        if (row.keys != policyFields) fail()
        val role = string(row.getValue("role"))
        if (role !in roles) fail()
        val disposition = string(row.getValue("disposition"))
        if (disposition !in setOf("DELETE_AFTER_LEASE", "RETAIN_NON_DISTRIBUTABLE")) fail()
        return PolicyEntry(role, digest(row.getValue("artifact_sha256")),
            integer(row.getValue("decision_sequence"), 1),
            integer(row.getValue("decision_generation"), 1),
            integer(row.getValue("max_offline_revocation_lag_ms"), 1), disposition)
    }

    private fun identity(row: JsonObject): FaceTimelineIdentityV2 {
        if (row.keys != identityFields || integer(row.getValue("schema_version"), 2, 2) != 2L ||
            integer(row.getValue("timeline_codec_version"), 2, 2) != 2L ||
            string(row.getValue("source_timebase")) != "DECODED_SAMPLE_INDEX_V2") fail()
        val result = FaceTimelineIdentityV2(
            uuid(row.getValue("recording_id")), uuid(row.getValue("audio_variant_id")),
            digest(row.getValue("source_sha256")),
            integer(row.getValue("decoded_sample_rate"), 8_000, 384_000),
            integer(row.getValue("decoded_sample_count"), 1, 33_177_600_000),
            digest(row.getValue("source_presentation_map_sha256")),
            uuid(row.getValue("embedding_model_id")), digest(row.getValue("embedding_manifest_sha256")),
            uuid(row.getValue("semantic_interpreter_id")),
            digest(row.getValue("interpreter_manifest_sha256")),
            digest(row.getValue("preprocessing_sha256")), digest(row.getValue("calibration_sha256")),
            digest(row.getValue("execution_profile_sha256")),
        )
        if (!FaceV2IdentityCodec.encode(result).contentEquals(canonical(row))) fail()
        return result
    }

    private fun canonical(value: JsonElement): ByteArray =
        JsonCanonicalizer(value.toString()).encodedUTF8

    private fun uuid(value: JsonElement): String = string(value).also { if (!it.matches(uuid)) fail() }
    private fun digest(value: JsonElement): String = string(value).also { if (!it.matches(hash)) fail() }
    private fun string(value: JsonElement): String = (value as? JsonPrimitive ?: fail()).let {
        if (!it.isString) fail()
        it.content
    }
    private fun integer(value: JsonElement, minimum: Long, maximum: Long = MAX_JSON_INTEGER): Long =
        (value as? JsonPrimitive ?: fail()).let {
            if (it.isString || !it.content.matches(integerToken)) fail()
            (it.content.toLongOrNull() ?: fail()).also { number ->
                if (number !in minimum..maximum) fail()
            }
        }
    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun p1363ToDer(signature: ByteArray): ByteArray {
        fun component(start: Int): ByteArray {
            val raw = signature.copyOfRange(start, start + 32)
            val first = raw.indexOfFirst { it.toInt() != 0 }.let { if (it < 0) 31 else it }
            val trimmed = raw.copyOfRange(first, 32)
            val positive = if ((trimmed[0].toInt() and 0x80) != 0) byteArrayOf(0) + trimmed else trimmed
            return byteArrayOf(0x02, positive.size.toByte()) + positive
        }
        val body = component(0) + component(32)
        return byteArrayOf(0x30, body.size.toByte()) + body
    }

    private fun fail(): Nothing = throw FaceProjectionV2Exception()
}
