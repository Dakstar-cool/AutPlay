package app.autplay.application.portableaccount

import app.autplay.data.security.BindingAuthorityWriteGate
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.application.accountrecovery.hasAccountRecoveryPendingRecipient
import app.autplay.application.selfpairing.hasSelfDevicePairingPendingRecipient
import java.util.Base64
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

data class PortableAccountOwnership(val accountId: String, val standaloneSinceMs: Long)

/** A local account survives disconnecting a server; no account replacement is implicit. */
class PortableAccountStore(private val credentials: CredentialStore, private val settings: NonSecretSettingsStore) {
    private val slot = CredentialJournalSlots.portableAccount

    suspend fun read(): PortableAccountDocument? {
        val envelope = readEnvelope() ?: return null
        return document(envelope)
    }

    suspend fun recoverIdentity(): PortableAccountDocument? = BindingAuthorityWriteGate.serialized {
        persistMissingOwnershipCutoff()
        val document = read() ?: return@serialized null
        publishIdentity(document)
        document
    }

    suspend fun standaloneOwnershipStart(): Long? = ownership()?.standaloneSinceMs

    suspend fun lineage(): PortableAccountLineage? {
        val envelope = readEnvelope() ?: return null
        return PortableAccountLineage.parse(document(envelope), envelope.portableAccountLineage)
    }

    suspend fun canRotateOffline(): Boolean = BindingAuthorityWriteGate.serialized {
        val envelope = readEnvelope() ?: return@serialized false
        val current = settings.settings.first()
        if (current.activeServerProfileId != null || current.m5Binding != null ||
            current.m5PendingExchangeCheckpoint != null || current.m5AdmissionCheckpoint != null ||
            credentials.hasPortableAccountPendingEnrollment() || credentials.hasPublicAccessPendingRegistration() ||
            credentials.hasSelfDevicePairingPendingRecipient() || credentials.hasAccountRecoveryPendingRecipient()) return@serialized false
        PortableAccountLineage.parse(document(envelope), envelope.portableAccountLineage).use { !it.everEnrolled }
    }

    /** Persist before contacting a server: an unknown result must never permit an offline reset. */
    suspend fun markServerEnrollmentAttempted(expectedDocument: PortableAccountDocument? = null) = BindingAuthorityWriteGate.serialized {
        val envelope = requireNotNull(readEnvelope()) { "ACCOUNT_SECRET_UNAVAILABLE" }
        val current = document(envelope)
        require(expectedDocument == null || current.fields == expectedDocument.fields) { "ACCOUNT_CONTEXT_CHANGED" }
        PortableAccountLineage.parse(current, envelope.portableAccountLineage).use { lineage ->
            writeEnvelope(envelope.copy(portableAccountLineage = PortableAccountLineage(true, lineage.predecessors).encode(current)))
        }
    }

    /** The exact historical document and its recovery key authorize propagation to an old server. */
    suspend fun findPredecessor(remoteDocument: PortableAccountDocument): PortableRecoveryFile? {
        val envelope = readEnvelope() ?: return null
        val current = document(envelope)
        if (current.accountId != remoteDocument.accountId || current.publicId != remoteDocument.publicId) return null
        return PortableAccountLineage.parse(current, envelope.portableAccountLineage).use { lineage ->
            lineage.predecessors.firstOrNull { it.document.fields == remoteDocument.fields }
                ?.let { PortableRecoveryFile.create(it.document, it.code) }
        }
    }

    suspend fun buildRotatedRecovery(previous: PortableRecoveryFile, replacement: CreatedPortableAccount): ByteArray =
        BindingAuthorityWriteGate.serialized { buildRotatedRecoveryLocked(previous, replacement) }

    private suspend fun buildRotatedRecoveryLocked(previous: PortableRecoveryFile, replacement: CreatedPortableAccount): ByteArray {
        require(previous.document.accountId == replacement.document.accountId && previous.document.publicId == replacement.document.publicId &&
            previous.document.keyThumbprint != replacement.document.keyThumbprint) { "ACCOUNT_IDENTITY_CONFLICT" }
        val envelope = readEnvelope()
        val current = envelope?.let(::document)
        require(current == null || current.accountId == previous.document.accountId && current.publicId == previous.document.publicId) { "ACCOUNT_IDENTITY_CONFLICT" }
        val lineage = current?.let { PortableAccountLineage.parse(it, envelope?.portableAccountLineage) }
        return try {
            val entries = mergePredecessors(previous.predecessors + lineage?.predecessors.orEmpty() +
                listOf(PortableRecoveryPredecessor(previous.document, previous.code)))
            PortableRecoveryFile.encode(replacement.document, replacement.recoveryCode, entries)
        } finally { lineage?.close() }
    }

    suspend fun offlineRotate(previous: PortableAccountDocument, replacement: CreatedPortableAccount,
        inputRecoveryFile: PortableRecoveryFile? = null) = BindingAuthorityWriteGate.serialized {
        val fallbackBytes = if (inputRecoveryFile == null) recoveryExport() else null
        var ownedFile: PortableRecoveryFile? = null
        try {
            val file = inputRecoveryFile ?: fallbackBytes?.let { PortableRecoveryFile.parse(it).also { parsed -> ownedFile = parsed } }
                ?: error("ACCOUNT_RECOVERY_INVALID")
            require(file.document.fields == previous.fields) { "ACCOUNT_RECOVERY_INVALID" }
            val recovery = buildRotatedRecoveryLocked(file, replacement)
            try { commitOfflineRotationLocked(previous, replacement.document, recovery) } finally { recovery.fill(0) }
        } finally { ownedFile?.close(); fallbackBytes?.fill(0) }
    }

    suspend fun commitOfflineRotation(previous: PortableAccountDocument, replacement: PortableAccountDocument, recovery: ByteArray) =
        BindingAuthorityWriteGate.serialized { commitOfflineRotationLocked(previous, replacement, recovery) }

    private suspend fun commitOfflineRotationLocked(previous: PortableAccountDocument, replacement: PortableAccountDocument, recovery: ByteArray) {
        requireNoCompetingEnrollment()
        val current = settings.settings.first()
        require(current.activeServerProfileId == null && current.m5Binding == null && current.activeUserId == null) { "ACCOUNT_SERVER_ROTATION_REQUIRED" }
        val envelope = readEnvelope()
        val existing = envelope?.let(::document)
        require(existing == null || existing.fields == previous.fields) { "ACCOUNT_CONTEXT_CHANGED" }
        if (existing != null) PortableAccountLineage.parse(existing, envelope?.portableAccountLineage).use {
            require(!it.everEnrolled) { "ACCOUNT_SERVER_ROTATION_REQUIRED" }
        }
        commitRotationLocked(previous, replacement, recovery, existing == null, importedSinceMs = null)
    }

    /** Read identity and cutoff together from the encrypted authority, never from UI markers. */
    suspend fun ownership(): PortableAccountOwnership? = BindingAuthorityWriteGate.serialized {
        val envelope = persistMissingOwnershipCutoff() ?: return@serialized null
        val document = document(envelope)
        PortableAccountOwnership(document.accountId, requireNotNull(envelope.portableAccountStandaloneSinceMs))
    }

    /** A QR-authorized, pinned server binding may supply the encrypted passport without the password. */
    suspend fun adoptFromAuthenticatedBinding(document: PortableAccountDocument, profile: app.autplay.domain.ServerProfileId, commit: String) =
        BindingAuthorityWriteGate.serialized {
            val current = settings.settings.first()
            require(current.activeServerProfileId == profile && current.m5Binding?.bindingCommitId == commit &&
                current.activeUserId?.value == document.accountId) { "ACCOUNT_CONTEXT_CHANGED" }
            val previous = read()
            require(!credentials.hasPortableAccountPendingEnrollment()) { "ACCOUNT_CONNECTION_OUTCOME_PENDING" }
            require(previous == null || previous.accountId == document.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
            if (previous == null || !java.security.MessageDigest.isEqual(previous.encode(), document.encode())) {
                val old = readEnvelope()
                val ancestry = previous?.let { PortableAccountLineage.parse(it, old?.portableAccountLineage) }
                val encoded = document.encode()
                try {
                    require(ancestry?.predecessors?.none { it.document.keyThumbprint == document.keyThumbprint } != false) { "ACCOUNT_SERVER_ROOT_OUTDATED" }
                    writeEnvelope(SessionCredentialEnvelope("portable-account", null, 0,
                    portableAccountDocument = encoded.toString(Charsets.UTF_8),
                    portableAccountRecoveryDocument = old?.portableAccountRecoveryDocument?.takeIf { previous?.keyThumbprint == document.keyThumbprint },
                    portableAccountStandaloneSinceMs = old?.portableAccountStandaloneSinceMs ?: System.currentTimeMillis(),
                    portableAccountLineage = PortableAccountLineage(true, ancestry?.predecessors.orEmpty()).encode(document)))
                } finally { encoded.fill(0); ancestry?.close() }
            } else {
                val old = requireNotNull(readEnvelope())
                PortableAccountLineage.parse(document, old.portableAccountLineage).use {
                    writeEnvelope(old.copy(portableAccountLineage = PortableAccountLineage(true, it.predecessors).encode(document)))
                }
            }
            publishIdentity(document)
        }

    /** Downloaded documents become local authority only after the password proves the key. */
    suspend fun adopt(document: PortableAccountDocument, password: CharArray) = withContext(Dispatchers.Default) {
        PortableAccountCrypto.unlock(document, password).use {
            BindingAuthorityWriteGate.serialized {
                requireNoCompetingEnrollment()
                val current = read()
                require(current == null || (current.accountId == document.accountId && current.keyThumbprint == document.keyThumbprint)) { "ACCOUNT_IDENTITY_CONFLICT" }
                val settingsNow = settings.settings.first()
                require(settingsNow.activeUserId == null || settingsNow.activeUserId.value == document.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
                require(settingsNow.portableAccountId == null || settingsNow.portableAccountId == document.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
                if (current == null) {
                    val encoded = document.encode()
                    try { writeEnvelope(SessionCredentialEnvelope("portable-account", null, 0,
                        portableAccountDocument = encoded.toString(Charsets.UTF_8), portableAccountStandaloneSinceMs = System.currentTimeMillis(),
                        portableAccountLineage = PortableAccountLineage(true, emptyList()).encode(document))) }
                    finally { encoded.fill(0) }
                } else {
                    val envelope = requireNotNull(readEnvelope())
                    PortableAccountLineage.parse(current, envelope.portableAccountLineage).use {
                        writeEnvelope(envelope.copy(portableAccountLineage = PortableAccountLineage(true, it.predecessors).encode(current)))
                    }
                }
                publishIdentity(document)
            }
        }
    }

    private suspend fun publishIdentity(document: PortableAccountDocument) {
        settings.mutate { latest ->
            require(latest.portableAccountId == null || latest.portableAccountId == document.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
            require(latest.activeUserId == null || latest.activeUserId.value == document.accountId) { "ACCOUNT_IDENTITY_CONFLICT" }
            latest.copy(portableAccountId = document.accountId, portableAccountKeyThumbprint = document.keyThumbprint,
                pendingPublicId = document.publicId)
        }
    }

    suspend fun create(publicId: String, password: CharArray): PortableAccountDocument = withContext(Dispatchers.Default) {
        BindingAuthorityWriteGate.serialized {
            requireNoCompetingEnrollment()
            read()?.let { existing ->
                require(existing.publicId == publicId) { "ACCOUNT_ALREADY_EXISTS" }
                PortableAccountCrypto.unlock(existing, password).close()
                publishIdentity(existing)
                return@serialized existing
            }
            val current = settings.settings.first()
            require(current.portableAccountId == null) { "ACCOUNT_SECRET_UNAVAILABLE" }
            // An existing account keeps its account ID. A new installation generates one offline.
            PortableAccountCrypto.create(publicId, password, current.activeUserId?.value).use { created ->
                val document = created.document
                val encoded = document.encode()
                try {
                    val recovery = PortableRecoveryFile.encode(document, created.recoveryCode)
                    try { writeEnvelope(SessionCredentialEnvelope("portable-account", null, 0,
                        portableAccountDocument = encoded.toString(Charsets.UTF_8), portableAccountRecoveryDocument = recovery.toString(Charsets.UTF_8),
                        portableAccountStandaloneSinceMs = 0,
                        portableAccountLineage = PortableAccountLineage(current.activeServerProfileId != null || current.m5Binding != null ||
                            current.activeUserId != null, emptyList()).encode(document))) }
                    finally { recovery.fill(0) }
                    // Secrets are durable before any non-secret marker is published.
                    check(read()?.keyThumbprint == document.keyThumbprint) { "ACCOUNT_SAVE_FAILED" }
                    settings.mutate { latest ->
                        check(latest.activeUserId == current.activeUserId && latest.portableAccountId == null) { "ACCOUNT_CONTEXT_CHANGED" }
                        latest.copy(portableAccountId = document.accountId, portableAccountKeyThumbprint = document.keyThumbprint,
                            pendingPublicId = publicId)
                    }
                    document
                } finally { encoded.fill(0) }
            }
        }
    }

    suspend fun verify(login: String, password: CharArray): Boolean = withContext(Dispatchers.Default) {
        val document = read() ?: return@withContext false
        if (app.autplay.application.social.normalizeSocialPublicId(login) != document.publicId) return@withContext false
        try { PortableAccountCrypto.unlock(document, password).use { true } }
        catch (_: IllegalArgumentException) { false }
    }

    suspend fun recoveryExport(): ByteArray? = readEnvelope()?.portableAccountRecoveryDocument?.toByteArray(Charsets.UTF_8)

    suspend fun recoveryCodeExport(): CharArray? {
        val bytes = recoveryExport() ?: return null
        return try { PortableRecoveryFile.parse(bytes).use { Base64.getUrlEncoder().withoutPadding().encodeToString(it.code).toCharArray() } }
        finally { bytes.fill(0) }
    }

    suspend fun acknowledgeRecoveryCode(saved: CharArray) = BindingAuthorityWriteGate.serialized {
        val envelope = requireNotNull(readEnvelope())
        val bytes = requireNotNull(envelope.portableAccountRecoveryDocument).toByteArray(Charsets.UTF_8)
        val code = PortableRecoveryFile.decodeCode(saved)
        try {
            PortableRecoveryFile.parse(bytes).use {
                require(java.security.MessageDigest.isEqual(it.code, code)) { "ACCOUNT_RECOVERY_SAVE_FAILED" }
                // A short code restores only the current root; its ancestor archive still needs export.
                if (it.predecessors.isEmpty()) writeEnvelope(envelope.copy(portableAccountRecoveryDocument = null))
            }
        } finally { bytes.fill(0); code.fill(0) }
    }

    /** Only called after a verified server rotation receipt; the pending command remains until detach. */
    suspend fun commitRotation(previous: PortableAccountDocument, replacement: PortableAccountDocument, recovery: ByteArray?,
        importedSinceMs: Long? = null) = BindingAuthorityWriteGate.serialized {
        commitRotationLocked(previous, replacement, recovery, everEnrolled = true, importedSinceMs = importedSinceMs)
    }

    private suspend fun commitRotationLocked(previous: PortableAccountDocument, replacement: PortableAccountDocument, recovery: ByteArray?,
        everEnrolled: Boolean, importedSinceMs: Long?) {
        require(previous.accountId == replacement.accountId && previous.publicId == replacement.publicId &&
            previous.keyThumbprint != replacement.keyThumbprint) { "ACCOUNT_IDENTITY_CONFLICT" }
        require(importedSinceMs == null || importedSinceMs >= 0) { "ACCOUNT_CONTEXT_CHANGED" }
        val old = readEnvelope()
        val current = old?.let(::document)
        require(current == null || current.accountId == previous.accountId &&
            current.keyThumbprint in setOf(previous.keyThumbprint, replacement.keyThumbprint)) { "ACCOUNT_IDENTITY_CONFLICT" }
        val now = settings.settings.first()
        require((now.activeUserId == null || now.activeUserId.value == previous.accountId) &&
            (now.portableAccountId == null || now.portableAccountId == previous.accountId)) { "ACCOUNT_CONTEXT_CHANGED" }
        if (recovery == null) {
            require(current?.fields == replacement.fields && old != null) { "ACCOUNT_RECOVERY_INVALID" }
            PortableAccountLineage.parse(replacement, old.portableAccountLineage).use {
                require(it.predecessors.any { entry -> entry.document.fields == previous.fields }) { "ACCOUNT_RECOVERY_INVALID" }
                writeEnvelope(old.copy(portableAccountLineage = PortableAccountLineage(true, it.predecessors).encode(replacement)))
            }
            publishIdentity(replacement)
            return
        }
        PortableRecoveryFile.parse(recovery).use { file ->
            require(file.document.fields == replacement.fields && file.predecessors.any { it.document.fields == previous.fields }) { "ACCOUNT_RECOVERY_INVALID" }
            val saved = current?.let { PortableAccountLineage.parse(it, old?.portableAccountLineage) }
            try {
                val merged = mergePredecessors(file.predecessors + saved?.predecessors.orEmpty())
                val fullRecovery = PortableRecoveryFile.encode(replacement, file.code, merged)
                try {
                    val encoded = replacement.encode()
                    try { writeEnvelope(SessionCredentialEnvelope("portable-account", null, 0,
                        portableAccountDocument = encoded.toString(Charsets.UTF_8),
                        portableAccountRecoveryDocument = fullRecovery.toString(Charsets.UTF_8),
                        portableAccountStandaloneSinceMs = old?.portableAccountStandaloneSinceMs ?: importedSinceMs ?: System.currentTimeMillis(),
                        portableAccountLineage = PortableAccountLineage(everEnrolled || saved?.everEnrolled == true, merged).encode(replacement))) }
                    finally { encoded.fill(0) }
                } finally { fullRecovery.fill(0) }
            } finally { saved?.close() }
        }
        check(read()?.fields == replacement.fields) { "ACCOUNT_SAVE_FAILED" }
        publishIdentity(replacement)
    }

    suspend fun acknowledgeRecoveryExport(saved: ByteArray) = BindingAuthorityWriteGate.serialized {
        val envelope = requireNotNull(readEnvelope())
        val expected = requireNotNull(envelope.portableAccountRecoveryDocument).toByteArray(Charsets.UTF_8)
        try { check(java.security.MessageDigest.isEqual(expected, saved)) { "ACCOUNT_RECOVERY_SAVE_FAILED" } }
        finally { expected.fill(0) }
        writeEnvelope(envelope.copy(portableAccountRecoveryDocument = null))
    }

    private suspend fun readEnvelope(): SessionCredentialEnvelope? {
        val material = credentials.read(slot) ?: return null
        return try { SessionCredentialEnvelopeCodec.decode(material).also { require(it.portableAccountDocument != null) } }
        finally { material.fill(0) }
    }

    private fun document(envelope: SessionCredentialEnvelope): PortableAccountDocument {
        val bytes = requireNotNull(envelope.portableAccountDocument).toByteArray(Charsets.UTF_8)
        return try { PortableAccountDocument.parse(bytes) } finally { bytes.fill(0) }
    }

    /** Duplicates may collapse only when both the exact passport and recovery secret agree. */
    private fun mergePredecessors(entries: List<PortableRecoveryPredecessor>): List<PortableRecoveryPredecessor> {
        val roots = linkedMapOf<String, PortableRecoveryPredecessor>()
        for (entry in entries) {
            val existing = roots[entry.document.keyThumbprint]
            require(existing == null || existing.document.fields == entry.document.fields &&
                java.security.MessageDigest.isEqual(existing.code, entry.code)) { "ACCOUNT_RECOVERY_INVALID" }
            if (existing == null) roots[entry.document.keyThumbprint] = entry
        }
        require(roots.size <= PortableRecoveryFile.MAX_PREDECESSORS) { "ACCOUNT_RECOVERY_HISTORY_FULL" }
        return roots.values.toList()
    }

    private suspend fun requireNoCompetingEnrollment() {
        val current = settings.settings.first()
        require(!credentials.hasPublicAccessPendingRegistration() && !credentials.hasSelfDevicePairingPendingRecipient() &&
            !credentials.hasAccountRecoveryPendingRecipient() && !credentials.hasPortableAccountPendingEnrollment() &&
            current.m5PendingExchangeCheckpoint == null && current.m5AdmissionCheckpoint == null) { "FIRST_BIND_CEREMONY_BUSY" }
    }

    /** Older envelopes lack a cutoff; persist a conservative boundary once, never move it later. */
    private suspend fun persistMissingOwnershipCutoff(): SessionCredentialEnvelope? {
        val envelope = readEnvelope() ?: return null
        if (envelope.portableAccountStandaloneSinceMs != null) return envelope
        return envelope.copy(portableAccountStandaloneSinceMs = System.currentTimeMillis()).also { writeEnvelope(it) }
    }

    private suspend fun writeEnvelope(envelope: SessionCredentialEnvelope) {
        val material = SessionCredentialEnvelopeCodec.encode(envelope)
        try { credentials.write(slot, material) } finally { material.fill(0) }
    }
}
