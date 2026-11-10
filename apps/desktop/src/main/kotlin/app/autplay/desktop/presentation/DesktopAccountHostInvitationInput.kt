package app.autplay.desktop.presentation

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.securestate.SecretBytes
import app.autplay.application.securestate.SecureJson
import app.autplay.application.securestate.canonicalUuid
import app.autplay.application.securestate.exact
import app.autplay.application.securestate.number
import app.autplay.application.securestate.string
import app.autplay.desktop.account.AccountControlException
import app.autplay.desktop.account.AccountControlFailure
import app.autplay.desktop.account.AccountSelectedServer
import app.autplay.desktop.account.DesktopAccountRuntime
import app.autplay.desktop.account.DesktopAccountTrust
import app.autplay.desktop.account.DesktopProfileController
import app.autplay.desktop.account.controlRequire
import app.autplay.ui.desktop.DesktopInvitationInputReview
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean

/** Input is not enrollment: a distinct confirmed review must precede the credential-producing POST. */
internal class DesktopAccountHostInvitationInput(private val runtime: DesktopAccountRuntime,
    private val profile: DesktopProfileController) {
    private class Candidate(val server: AccountSelectedServer, val admission: Long,
        val preview: DesktopInvitationInputReview, val expires: Instant, val document: SecretBytes) : AutoCloseable {
        override fun close(): Unit = document.close()
    }
    private val gate = Any()
    private var generation: Long = 0
    private var candidate: Candidate? = null

    fun prepare(bytes: ByteArray, requireCurrent: () -> Unit): Unit {
        val stamp = synchronized(gate) { retireLocked(); generation }
        requireCurrent()
        val server = runtime.requireServer(D1.Family.ENROLLMENT)
        val admission = runtime.secure.admissionGeneration
        val value = SecureJson.parse(bytes, 16_384, canonical = false)
        value.exact("contract_version", "schema_version", "invitation_id", "server_instance_id", "identity_epoch",
            "identity_thumbprint_sha256", "api_origin", "stream_origin", "user_id", "account_display_name",
            "issued_at", "expires_at", "invitation_secret", "secret_handling")
        controlRequire(value.string("contract_version") == "v1" && value.number("schema_version") == 1L &&
            value.string("server_instance_id") == server.identity.serverInstanceId &&
            value.number("identity_epoch") == server.identity.identityEpoch &&
            value.string("identity_thumbprint_sha256") == server.identity.thumbprintSha256 &&
            value.string("api_origin") == server.identity.apiOrigin && value.string("stream_origin") == server.identity.streamOrigin &&
            value.string("secret_handling") == "DISPLAY_ONCE_NO_CLIPBOARD_NO_LOG_NO_EXPORT")
        canonicalUuid(value.string("invitation_id")); canonicalUuid(value.string("user_id"))
        val label = value.string("account_display_name"); DesktopAccountTrust.display(label, 120)
        DesktopAccountTrust.decode(value.string("invitation_secret"), true, 32).fill(0)
        val issued = Instant.parse(value.string("issued_at")); val expires = Instant.parse(value.string("expires_at"))
        val now = Instant.now()
        controlRequire(issued < expires && expires <= issued.plusSeconds(1800) && expires > now && expires <= now.plusSeconds(1800),
            AccountControlFailure.EXPIRED)
        val preview = DesktopInvitationInputReview(UUID.randomUUID().toString(), label, server.identity.thumbprintSha256, expires.toString())
        val secret = runtime.secure.transientSecret(bytes)
        try {
            requireCurrent(); runtime.secure.requireAdmission(admission)
            synchronized(gate) {
                controlRequire(generation == stamp && runtime.selected === server, AccountControlFailure.CONTEXT_CHANGED)
                candidate = Candidate(server, admission, preview, expires, secret)
            }
        } catch (failure: Exception) { secret.close(); throw failure }
    }

    fun view(): DesktopInvitationInputReview? = synchronized(gate) {
        val retained = candidate ?: return@synchronized null
        if (retained.expires <= Instant.now() || runtime.selected !== retained.server ||
            runtime.secure.admissionGeneration != retained.admission) {
            retireLocked(); null
        } else retained.preview
    }

    /** Consumed synchronously with the one-use UI permit, before authority retirement can wipe input. */
    fun capture(id: String, confirmed: Boolean): Dispatch {
        val retained = synchronized(gate) {
            val value = candidate ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            controlRequire(confirmed && value.preview.id == id, AccountControlFailure.CONTEXT_CHANGED)
            candidate = null; generation++
            value
        }
        try {
            controlRequire(retained.expires > Instant.now(), AccountControlFailure.EXPIRED)
            controlRequire(runtime.requireServer(D1.Family.ENROLLMENT) === retained.server, AccountControlFailure.CONTEXT_CHANGED)
            runtime.secure.requireAdmission(retained.admission)
            val bytes = retained.document.copy()
            return Dispatch(runtime, profile, retained.server, retained.expires, bytes)
        } finally { retained.close() }
    }

    /** Like the host's consumed character input: retained only by the admitted command, never UI/J. */
    internal class Dispatch(private val runtime: DesktopAccountRuntime, private val profile: DesktopProfileController,
        private val server: AccountSelectedServer, private val expires: Instant, private val document: ByteArray) : AutoCloseable {
        private val submitted = AtomicBoolean(false)
        private val closed = AtomicBoolean(false)
        fun submit(): CompletionStage<Unit> {
            controlRequire(!closed.get() && submitted.compareAndSet(false, true), AccountControlFailure.CONTEXT_CHANGED)
            controlRequire(expires > Instant.now(), AccountControlFailure.EXPIRED)
            controlRequire(runtime.requireServer(D1.Family.ENROLLMENT) === server, AccountControlFailure.CONTEXT_CHANGED)
            // The real controller copies and checks the document against current K/D1/J authority.
            // No earlier cached admission generation is reused across the intentional fence.
            return profile.enrollInvitation(document)
        }
        override fun close(): Unit { if (closed.compareAndSet(false, true)) document.fill(0) }
        override fun toString(): String = "DesktopInvitationInputDispatch(<redacted>)"
    }

    fun retire(): Unit = synchronized(gate) { retireLocked() }
    private fun retireLocked(): Unit { generation++; candidate?.close(); candidate = null }
}
