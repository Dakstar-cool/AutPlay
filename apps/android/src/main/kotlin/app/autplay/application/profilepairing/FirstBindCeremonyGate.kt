package app.autplay.application.profilepairing

import app.autplay.application.portableaccount.PortableAccountDocument
import app.autplay.application.portableaccount.PortableAccountLineage
import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import java.util.concurrent.atomic.AtomicReference

enum class FirstBindCeremonyOwner { M5, PUBLIC_ACCESS, SELF_DEVICE_PAIRING, ACCOUNT_RECOVERY, ACCOUNT_DELETE_CANCEL, PORTABLE_ACCOUNT }

/** Process-local reservation spanning the complete first-bind ceremony, including user decisions. */
class FirstBindCeremonyGate {
    private val owner = AtomicReference<FirstBindCeremonyOwner?>(null)

    fun reserve(candidate: FirstBindCeremonyOwner): Boolean {
        val current = owner.get()
        return current == candidate || (current == null && owner.compareAndSet(null, candidate))
    }

    fun isReservedBy(candidate: FirstBindCeremonyOwner): Boolean = owner.get() == candidate

    /** Transfer a trusted discovery without exposing an unreserved first-bind interval. */
    fun transfer(from: FirstBindCeremonyOwner, to: FirstBindCeremonyOwner): Boolean =
        owner.compareAndSet(from, to)

    fun release(candidate: FirstBindCeremonyOwner) {
        owner.compareAndSet(candidate, null)
    }
}

/** Encrypted authority survives a crash before the non-secret account markers were published. */
internal suspend fun CredentialStore.encryptedPortableAccountDocument(): PortableAccountDocument? {
    val material = read(CredentialJournalSlots.portableAccount) ?: return null
    return try {
        val envelope = SessionCredentialEnvelopeCodec.decode(material)
        val document = requireNotNull(envelope.portableAccountDocument) { "ACCOUNT_SECRET_UNAVAILABLE" }
            .toByteArray(Charsets.UTF_8)
        try { PortableAccountDocument.parse(document) } finally { document.fill(0) }
    } finally { material.fill(0) }
}

/** A malformed encrypted journal is still pending; never turn a decode failure into permission. */
internal suspend fun CredentialStore.requireNoPortableEnrollment() {
    val pending = read(CredentialJournalSlots.portableEnrollment) ?: return
    pending.fill(0)
    error("ACCOUNT_CONNECTION_OUTCOME_PENDING")
}

/**
 * Read-only discovery/claim may learn the target account; a grant-minting exchange must name it.
 * PA2 creates an unknown new account, so it cannot run while this installation already owns a root.
 */
internal suspend fun CredentialStore.requirePortableAccountCompatible(
    expectedAccountId: String? = null,
    allowUnknownAccount: Boolean = false,
) {
    requireNoPortableEnrollment()
    val root = encryptedPortableAccountDocument() ?: return
    require(root.accountId == expectedAccountId || (expectedAccountId == null && allowUnknownAccount)) {
        "ACCOUNT_IDENTITY_CONFLICT"
    }
}

/** Must complete before any legacy grant mint; an uncertain remote result vetoes offline reset. */
internal suspend fun CredentialStore.markPortableServerEnrollmentAttempt(expectedAccountId: String) =
    BindingAuthorityWriteGate.serialized {
        requirePortableAccountCompatible(expectedAccountId)
        val material = read(CredentialJournalSlots.portableAccount) ?: return@serialized
        try {
            val envelope = SessionCredentialEnvelopeCodec.decode(material)
            val document = requireNotNull(encryptedPortableAccountDocument())
            require(document.accountId == expectedAccountId) { "ACCOUNT_IDENTITY_CONFLICT" }
            PortableAccountLineage.parse(document, envelope.portableAccountLineage).use {
                if (it.everEnrolled && envelope.portableAccountLineage != null) return@use
                val encoded = SessionCredentialEnvelopeCodec.encode(envelope.copy(
                    portableAccountLineage = PortableAccountLineage(true, it.predecessors).encode(document)))
                try { write(CredentialJournalSlots.portableAccount, encoded) } finally { encoded.fill(0) }
            }
        } finally { material.fill(0) }
    }
