package app.autplay.application.publicaccess

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.FirstBindCeremonyOwner
import app.autplay.domain.ServerProfileId
import java.util.Base64
import java.security.MessageDigest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Reuses verified M5 discovery and an explicit recipient trust decision before secret I/O. */
data class VerifiedAccountRegistrationContext(val snapshot: PairingFlowSnapshot, val identitySpki: ByteArray) : AutoCloseable { override fun close() = identitySpki.fill(0) }
interface AccountRegistrationDiscoveryGate { suspend fun verify(invitation: AccountInvitation): Boolean; fun takeVerifiedContext(): VerifiedAccountRegistrationContext? }

/** Explicit one-shot approval storage; UI must call [approve] only after displaying discovery. */
class ApprovedRegistrationContextGate : AccountRegistrationDiscoveryGate {
    private var proposed: VerifiedAccountRegistrationContext? = null
    private var approved = false
    fun propose(value: VerifiedAccountRegistrationContext) { proposed?.close(); proposed = value; approved = false }
    fun approve() { check(proposed != null); approved = true }
    fun cancel() { proposed?.close(); proposed = null; approved = false }
    override suspend fun verify(invitation: AccountInvitation): Boolean {
        val snapshot = proposed?.snapshot ?: return false
        return approved &&
            snapshot.expectedServerInstanceId == invitation.serverInstanceId &&
            snapshot.expectedIdentityEpoch == invitation.identityEpoch &&
            snapshot.expectedIdentityThumbprintSha256 == invitation.identityThumbprintSha256 &&
            snapshot.apiOrigin == invitation.apiOrigin &&
            snapshot.streamOrigin == invitation.streamOrigin
    }
    override fun takeVerifiedContext(): VerifiedAccountRegistrationContext? = if (approved) proposed.also { proposed = null; approved = false } else null
}

/** Ordinary binding is intentionally injected: PA2 must persist credentials before binding facts. */
fun interface AccountRegistrationBindingCommitter {
    suspend fun commitAfterCredential(profileId: ServerProfileId, registration: AccountRegistrationRequest, response: AccountRegistrationResponse, context: VerifiedAccountRegistrationContext): Boolean
}

/** Public account creation is first-bind only; it must never switch or merge an active profile. */
fun interface ActiveProfileGate { suspend fun hasActiveProfile(): Boolean }

/**
 * Crash-safe public account registration. Pending canonical request and refresh secret are kept in
 * the encrypted credential store only; Room/DataStore/WorkManager never see invitation material.
 */
class AccountRegistrationRuntime(
    private val credentials: CredentialStore,
    private val port: AccountRegistrationPort,
    private val binding: AccountRegistrationBindingCommitter,
    private val activeProfileGate: ActiveProfileGate,
    private val discoveryGate: AccountRegistrationDiscoveryGate,
    private val firstBindGate: FirstBindCeremonyGate = FirstBindCeremonyGate(),
) {
    /**
     * A re-imported exact invitation can recover an uncertain registration without a non-secret
     * pointer: `null` means no pending exchange, `true` exact match, and `false` fail-closed conflict.
     */
    suspend fun pendingMatches(profileId: ServerProfileId, invitation: AccountInvitation): Boolean? {
        val encrypted = credentials.read(profileId) ?: return null
        return try {
            val envelope = SessionCredentialEnvelopeCodec.decode(encrypted)
            val canonical = envelope.publicAccessPendingCanonicalRequest ?: return null
            val canonicalBytes = Base64.getUrlDecoder().decode(canonical)
            val root = try {
                Json.parseToJsonElement(canonicalBytes.toString(Charsets.UTF_8)).jsonObject
            } finally {
                canonicalBytes.fill(0)
            }
            fun text(name: String): String? = root[name]?.jsonPrimitive?.content
            val secretText = text("invitation_secret") ?: return false
            val storedSecret = runCatching {
                Base64.getUrlDecoder().decode(secretText)
            }.getOrElse { return false }
            try {
                text("invitation_id") == invitation.invitationId &&
                    text("expected_server_instance_id") == invitation.serverInstanceId &&
                    text("expected_identity_epoch") == invitation.identityEpoch.toString() &&
                    text("expected_identity_thumbprint_sha256") == invitation.identityThumbprintSha256 &&
                    text("expected_api_origin") == invitation.apiOrigin &&
                    text("expected_stream_origin") == invitation.streamOrigin &&
                    text("expected_account_display_name") == invitation.accountDisplayName &&
                    MessageDigest.isEqual(storedSecret, invitation.secret)
            } finally {
                storedSecret.fill(0)
            }
        } finally {
            encrypted.fill(0)
        }
    }

    suspend fun redeem(signed: SignedAccountRegistrationRequest): Result<Unit> {
        if (!firstBindGate.reserve(FirstBindCeremonyOwner.PUBLIC_ACCESS)) {
            signed.close()
            return Result.failure(IllegalStateException("FIRST_BIND_CEREMONY_BUSY"))
        }
        if (activeProfileGate.hasActiveProfile()) {
            firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
            signed.close()
            return Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_ACTIVE_PROFILE_FORBIDDEN"))
        }
        if (!discoveryGate.verify(signed.registration.invitation)) {
            firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
            signed.close()
            return Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_DISCOVERY_OR_TRUST_REQUIRED"))
        }
        val context = discoveryGate.takeVerifiedContext()
            ?: run {
                firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
                signed.close()
                return Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_DISCOVERY_CONTEXT_MISSING"))
        }
        val profile = ServerProfileId(signed.registration.invitation.serverInstanceId)
        val pendingCanonical = Base64.getUrlEncoder().withoutPadding().encodeToString(signed.canonicalJson)
        val pendingRefresh = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(signed.registration.successorRefreshToken)
        val pending = SessionCredentialEnvelope(
            accessToken = "pending-account-registration",
            refreshToken = null,
            generation = 0,
            refreshPending = true,
            publicAccessPendingRegistrationId = signed.registration.registrationId,
            publicAccessPendingCanonicalRequest = pendingCanonical,
            publicAccessPendingSuccessorRefreshToken = pendingRefresh,
        )
        return runCatching {
            val encodedPending = SessionCredentialEnvelopeCodec.encode(pending)
            try {
                credentials.write(profile, encodedPending)
            } finally {
                encodedPending.fill(0)
            }
            val response = port.redeem(signed).getOrThrow()
            try {
                // Secret-first: durable successor credentials precede every non-secret profile binding.
                val session = SessionCredentialEnvelope(
                    accessToken = response.accessToken.toString(Charsets.UTF_8),
                    refreshToken = signed.registration.successorRefreshToken.toString(Charsets.US_ASCII),
                    generation = 0,
                    refreshPending = true,
                    bindingCommitId = signed.registration.bindingCommitId,
                    sessionId = response.sessionId,
                    sessionFamilyId = response.sessionId,
                    sessionGeneration = 0,
                    publicAccessPendingRegistrationId = signed.registration.registrationId,
                    publicAccessPendingCanonicalRequest = pendingCanonical,
                    publicAccessPendingSuccessorRefreshToken = pendingRefresh,
                )
                val encodedSession = SessionCredentialEnvelopeCodec.encode(session)
                try {
                    credentials.write(profile, encodedSession)
                } finally {
                    encodedSession.fill(0)
                }
                check(binding.commitAfterCredential(profile, signed.registration, response, context)) { "ACCOUNT_REGISTRATION_BINDING_FAILED" }
            } finally {
                response.accessToken.fill(0)
            }
        }.also {
            context.close()
            signed.close()
        }
    }

    /** Replays an uncertain exchange only from encrypted PA2 state; it never creates a new key/id. */
    suspend fun resumePending(profileId: ServerProfileId, keys: M5DeviceKeyStore): Result<Unit> {
        val encrypted = credentials.read(profileId) ?: return Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_PENDING_MISSING"))
        return try {
            val envelope = SessionCredentialEnvelopeCodec.decode(encrypted)
            val canonical = envelope.publicAccessPendingCanonicalRequest ?: return Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_PENDING_MISSING"))
            val refresh = envelope.publicAccessPendingSuccessorRefreshToken ?: return Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_PENDING_MISSING"))
            val canonicalBytes = Base64.getUrlDecoder().decode(canonical)
            val refreshBytes = Base64.getUrlDecoder().decode(refresh)
            val signed = try { AccountRegistrationProof.resume(canonicalBytes, refreshBytes, keys) } finally { canonicalBytes.fill(0); refreshBytes.fill(0) }
            require(envelope.publicAccessPendingRegistrationId == signed.registration.registrationId) { "ACCOUNT_REGISTRATION_PENDING_MISMATCH" }
            redeem(signed)
        } finally { encrypted.fill(0) }
    }
}
