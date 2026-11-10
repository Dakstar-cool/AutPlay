package app.autplay.desktop.media

import app.autplay.application.desktop.media.*
import app.autplay.application.securestate.*
import app.autplay.desktop.account.AccountEndpoint
import app.autplay.desktop.account.DesktopAccountRuntime
import app.autplay.desktop.account.DesktopAccountTrust
import app.autplay.desktop.security.DesktopProtectedMediaState
import java.time.Duration
import java.time.Instant
import java.util.concurrent.ConcurrentHashMap
import kotlinx.serialization.json.*

/** Authenticated current M5 profile-v1, distinct from every D1/B1 content/revision guard. */
internal class DesktopMediaProfileOwner(private val runtime: DesktopAccountRuntime,
    private val protected: DesktopProtectedMediaState) {
    private val accepted = ConcurrentHashMap<MediaId, M5ProfileCapture>()

    suspend fun refresh(profileId: MediaId): M5ProfileCapture {
        val server = runtime.selected ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
        var snapshot = runtime.secure.snapshot().mediaAwait()
        val record = runtime.secure.read(profileId.value, snapshot).mediaAwait()
            ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
        try {
            val owner = record.owner
            val session = record.payload as? SessionPayload ?: throw mediaFailure(MediaCode.AUTH_REQUIRED, MediaStage.AUTH)
            mediaCheck(snapshot.activeBinding == profileId.value && owner.stage == SecureStage.ACTIVE && !owner.detached && !owner.deletionVeto &&
                owner.server == server.identity.serverInstanceId && owner.identityEpoch == server.identity.identityEpoch &&
                owner.identityThumbprint == server.identity.thumbprintSha256 && owner.apiOrigin == server.identity.apiOrigin &&
                owner.streamOrigin == server.identity.streamOrigin, MediaCode.OWNER_STALE, MediaStage.AUTH)
            val secret = runtime.secure.access(snapshot, owner).mediaAwait()
            val access = secret.copy()
            val response = try { runtime.read(server.transport, AccountEndpoint.M5_CAPABILITIES, access = access).mediaAwait() }
                finally { access.fill(0); secret.close() }
            val raw = try { response.bodyCopy() } finally { response.close() }
            try {
                val publicKey = server.identity.publicKeySpki
                val payload = try { DesktopAccountTrust.signed(raw, publicKey, "AutPlay capabilities v1\n", 65_536) }
                    finally { publicKey.fill(0) }
                val now = Instant.now()
                val issued = Instant.parse(payload.string("issued_at")); val expiry = Instant.parse(payload.string("expires_at"))
                val revision = payload.number("capability_revision")
                mediaCheck(payload.string("server_instance_id") == owner.server && payload.number("identity_epoch") == owner.identityEpoch &&
                    payload.string("user_id") == owner.account && payload.string("device_id") == owner.device && payload.number("api_major") == 1L &&
                    payload.string("issued_at").endsWith('Z') && payload.string("expires_at").endsWith('Z') &&
                    issued <= now && expiry > now && expiry > issued && Duration.between(issued, expiry) <= Duration.ofHours(1) &&
                    revision > 0 && (session.m5 == null || revision >= requireNotNull(session.m5).revision),
                    MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                val operations = payload.array("operations").map {
                    (it as? JsonPrimitive)?.takeIf { value -> value.isString }?.content
                        ?: throw mediaFailure(MediaCode.PROTOCOL_ERROR, MediaStage.AUTH)
                }
                mediaCheck(operations.size <= 64 && operations.distinct().size == operations.size && operations.all {
                    Regex("[A-Za-z][A-Za-z0-9_.-]{0,63}").matches(it) }, MediaCode.PROTOCOL_ERROR, MediaStage.AUTH)
                val required = payload["required_features"]
                mediaCheck(required == null || required is JsonArray && required.isEmpty(), MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                val limits = payload["limits"] as? JsonObject ?: throw mediaFailure(MediaCode.PROTOCOL_ERROR, MediaStage.AUTH)
                mediaCheck(limits.size <= 64 && limits.keys.all { limits.number(it) in 0..DesktopMediaLimits.MAX_INTEGER },
                    MediaCode.PROTOCOL_ERROR, MediaStage.AUTH)
                if (session.m5?.revision != revision) {
                    val transport = session.transportPolicyCopy()
                    val policy = try { SecureMediaGuardCodec.readTransport(transport) } finally { transport.fill(0) }
                    snapshot = protected.publishConnection(snapshot, profileId.value, session.d1, M5CapabilityGuard(revision), policy).mediaAwait()
                }
                val readback = runtime.secure.read(profileId.value, snapshot).mediaAwait()
                    ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                try {
                    val high = (readback.payload as? SessionPayload)?.m5
                        ?: throw mediaFailure(MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                    mediaCheck(high.revision == revision && snapshot.activeBinding == profileId.value,
                        MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
                    val envelope = SecureJson.parse(raw, 65_536, canonical = false)
                    val canonical = SecureJson.encode(envelope)
                    try {
                        mediaCheck(canonical.size <= 65_536, MediaCode.CAPACITY_EXCEEDED, MediaStage.AUTH)
                        val result = M5ProfileCapture(MediaId.random(), MediaId(requireNotNull(owner.server)), requireNotNull(owner.identityEpoch),
                            requireNotNull(owner.identityThumbprint), MediaId(requireNotNull(owner.account)), MediaId(requireNotNull(owner.device)),
                            M5CapabilityRevision(revision), M5CapabilityRevision(high.revision), envelope.string("payload_sha256"), issued, expiry,
                            canonical, operations.toSet())
                        accepted.entries.removeIf { it.value.userId == result.userId && it.value.deviceId == result.deviceId }
                        accepted[result.documentId] = result
                        return result
                    } finally { canonical.fill(0) }
                } finally { readback.close() }
            } finally { raw.fill(0) }
        } finally { record.close() }
    }

    fun requireCurrent(capture: M5ProfileCapture): Unit {
        mediaCheck(accepted[capture.documentId] === capture && capture.issuedAt <= Instant.now() && capture.expiresAt > Instant.now(),
            MediaCode.CAPABILITY_UNAVAILABLE, MediaStage.AUTH)
    }
    fun retire(): Unit { accepted.clear() }
}
