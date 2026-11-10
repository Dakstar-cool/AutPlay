package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as ProfileV2
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2 as RecoveryV2
import app.autplay.application.portableaccount.PortableAccountDocument
import app.autplay.application.portableaccount.PortableRecoveryFile
import app.autplay.application.desktop.accountflow.DesktopPendingCeremonyMaterial
import app.autplay.application.securestate.*
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.X509EncodedKeySpec
import java.util.Base64
import kotlinx.serialization.json.*

/** Validates only an authenticated K original. Never prepares, signs, refreshes expiry or learns trust. */
internal object DesktopFrozenPendingVerifier {
    fun verifyLifecycle(operation: PendingOperation, owner: SecureRecordOwner, wire: ByteArray): Boolean = try {
        owner.requireBinding()
        val body = SecureJson.parse(wire, operation.maximumWire)
        controlRequire(body.string("operation_id") == owner.operation)
        when (operation) {
            PendingOperation.CONSENT -> {
                body.exact("operation_id", "account_id", "expected_revision", "decision", "policy_version")
                controlRequire(body.string("account_id") == owner.account && body.number("expected_revision") >= 0 && body.number("policy_version") == 1L &&
                    body.string("decision") in setOf("GRANTED", "DENIED", "WITHDRAWN"))
            }
            PendingOperation.VISIBILITY -> {
                body.exact("operation_id", "expected_revision", "friends_can_view_statistics")
                controlRequire(body.number("expected_revision") >= 0); body.boolean("friends_can_view_statistics")
            }
            PendingOperation.LOGOUT_CURRENT, PendingOperation.LOGOUT_ALL, PendingOperation.REVOKE -> {
                controlRequire(body.keys == setOf("contract_version", "schema_version", "operation_id") ||
                    body.keys == setOf("contract_version", "schema_version", "operation_id", "portable_authorization"))
                controlRequire(body.string("contract_version") == "v1" && body.number("schema_version") == 1L)
                if (operation == PendingOperation.REVOKE) canonicalUuid(requireNotNull(owner.claim))
            }
            else -> invalid()
        }
        true
    } catch (_: Exception) { false }
    fun verifyRootRotation(owner: SecureRecordOwner, wire: ByteArray, previousArchive: ByteArray): Boolean {
        val digest = MessageDigest.getInstance("SHA-256").digest(wire)
        val proof = Base64.getDecoder().decode(SecureJson.parse(wire, 16_384).string("proof"))
        try {
            val pending = PendingPayload(SecureRecordKind.PORTABLE_COMMAND, PendingOperation.PORTABLE_ROTATE, PendingWireKind.PORTABLE_ROTATE,
                wire, digest, proof, previousBinding = previousArchive)
            return SecureRecord(requireNotNull(SecureRecordKind.PORTABLE_COMMAND.reserved), 1, requireNotNull(owner.operation), owner, pending).use { verify(it, null) }
        } finally { digest.fill(0); proof.fill(0) }
    }
    fun verify(record: SecureRecord, deviceKey: ByteArray?): Boolean = try {
        val pending = record.payload as? PendingPayload ?: invalid()
        controlRequire(pending.wireKind.family == pending.operation && record.owner.platform == "MACOS")
        val wire = pending.requestCopy(); val digest = pending.requestDigestCopy(); val proof = pending.signatureCopy()
        try {
            val document = SecureJson.parse(wire, pending.operation.maximumWire, canonical = false)
            if (deviceKey != null) controlRequire(secureSha256(deviceKey) == record.owner.keyThumbprint)
            when (pending.wireKind) {
                PendingWireKind.PORTABLE_REGISTER, PendingWireKind.PORTABLE_LOGIN, PendingWireKind.PORTABLE_ROTATE -> {
                    requireDigest(wire, digest)
                    val embedded = Base64.getDecoder().decode(document.string("proof"))
                    try { controlRequire(embedded.contentEquals(proof)) } finally { embedded.fill(0) }
                    verifyPortable(record, pending, document)
                }
                PendingWireKind.RECOVERY_CONFIGURE -> {
                    requireDigest(wire, digest); trust(document, record.owner); scope(document, record.owner)
                    controlRequire(record.owner.protocolVersion == 1 && proof.isEmpty())
                    verifyOwnHash(document, setOf("request_sha256"), null, byteArrayOf())
                    pending.secretContextCopy().use { verifyRecoverySecrets(record.owner, document, it, true) }
                }
                PendingWireKind.LOGOUT_CURRENT, PendingWireKind.LOGOUT_ALL, PendingWireKind.REVOKE,
                PendingWireKind.CONSENT, PendingWireKind.VISIBILITY -> {
                    requireDigest(wire, digest); controlRequire(proof.isEmpty()); scope(document, record.owner)
                    controlRequire(verifyLifecycle(pending.operation, record.owner, wire))
                    // These platform-neutral requests are authenticated by the exact retained bearer.
                    val auth = pending.authorizationCopy(); try { controlRequire(auth.isNotEmpty()) } finally { auth.fill(0) }
                }
                PendingWireKind.ENROLLMENT_INVITATION_CREATE, PendingWireKind.ENROLLMENT_INVITATION_CANCEL -> {
                    requireDigest(wire, digest); controlRequire(proof.isEmpty())
                    record.owner.requireBinding()
                    controlRequire(pending.kind == SecureRecordKind.PROFILE_COMMAND &&
                        record.owner.ceremony == record.owner.operation)
                    val request = SecureJson.parse(wire, 4096)
                    controlRequire(request.string("contract_version") == "v1" && request.number("schema_version") == 1L &&
                        request.string("operation_id") == record.owner.operation)
                    if (pending.wireKind == PendingWireKind.ENROLLMENT_INVITATION_CREATE) {
                        request.exact("contract_version", "schema_version", "operation_id", "expires_in_seconds")
                        controlRequire(request.number("expires_in_seconds") in 60..1800 && record.owner.claim == record.owner.operation)
                    } else {
                        request.exact("contract_version", "schema_version", "operation_id")
                        canonicalUuid(requireNotNull(record.owner.claim))
                    }
                    val auth = pending.authorizationCopy(); val successor = pending.successorCopy()
                    try { controlRequire(auth.isNotEmpty() && successor.isEmpty()) }
                    finally { auth.fill(0); successor.fill(0) }
                }
                PendingWireKind.SELF_START, PendingWireKind.SELF_DECISION, PendingWireKind.SELF_CLAIM,
                PendingWireKind.SELF_POLL, PendingWireKind.SELF_EXCHANGE -> {
                    trust(document, record.owner); scope(document, record.owner)
                    controlRequire(document.string("ceremony_id") == record.owner.ceremony)
                    val signed = pending.wireKind !in setOf(PendingWireKind.SELF_START, PendingWireKind.SELF_DECISION)
                    controlRequire((record.owner.role == SecureRole.RECIPIENT) == signed)
                    val domain = if (signed) "autplay:device-compatibility:self:${pending.wireKind.name.removePrefix("SELF_").lowercase()}:v2\n" else null
                    verifyOwnHash(document, setOf("request_sha256", "device_signature_b64url"), digest, proof, deviceKey, domain)
                    pending.secretContextCopy().use { secrets ->
                        val rendezvous = secrets.rendezvousCopy(); val poll = secrets.pollCopy()
                        try {
                            if ("rendezvous_secret_sha256" in document) controlRequire(document.string("rendezvous_secret_sha256") == secureSha256(rendezvous))
                            if ("poll_secret_sha256" in document) controlRequire(document.string("poll_secret_sha256") == secureSha256(poll))
                        } finally { rendezvous.fill(0); poll.fill(0) }
                    }
                }
                PendingWireKind.RECOVERY_PREVIEW, PendingWireKind.RECOVERY_COMMIT, PendingWireKind.DELETION_REQUEST,
                PendingWireKind.DELETION_PREVIEW, PendingWireKind.DELETION_CANCEL -> {
                    trust(document, record.owner); scope(document, record.owner)
                    val kind = RecoveryV2.BodyKind.valueOf(pending.wireKind.name)
                    verifyOwnHash(document, setOf("request_sha256", "device_signature_b64url"), digest, proof, deviceKey, kind.proofDomain)
                    pending.secretContextCopy().use { verifyRecoverySecrets(record.owner, document, it, false) }
                }
                PendingWireKind.PUBLIC_REGISTRATION -> invalid()
                else -> {
                    trust(document, record.owner); scope(document, record.owner)
                    val kind = ProfileV2.Operation.valueOf(pending.wireKind.name)
                    val ownHash = kind in setOf(ProfileV2.Operation.ENROLLMENT_EXCHANGE, ProfileV2.Operation.SESSION_ROTATE)
                    val field = if (ownHash) "device_signature_b64url" else "proof_b64url"
                    val unsigned = SecureJson.encode(JsonObject(document.filterKeys { it != field && (!ownHash || it != "request_sha256") }))
                    try { requireDigest(unsigned, digest) } finally { unsigned.fill(0) }
                    val embedded = DesktopAccountTrust.decode(document.string(field), true, 64)
                    try { controlRequire(embedded.contentEquals(proof)) } finally { embedded.fill(0) }
                    if (ownHash) controlRequire(document.string("request_sha256") == hex(digest))
                    verifySignature(requireNotNull(deviceKey), kind.proofDomain, digest, proof, true)
                }
            }
            true
        } finally { wire.fill(0); digest.fill(0); proof.fill(0) }
    } catch (_: Exception) { false }

    fun verifyRecoverySecrets(owner: SecureRecordOwner, body: JsonObject, secrets: PendingSecretContext, configure: Boolean): Boolean {
        val current = secrets.recoveryCodeCopy(); val deletion = secrets.deletionCodeCopy()
        val next = secrets.nextRecoveryCodeCopy(); val document = secrets.recoveryDocumentCopy()
        try {
            if (configure || "next_code_verifier_sha256" in body) {
                controlRequire(next.isNotEmpty() && document.isNotEmpty())
                controlRequire(body.string("next_code_verifier_sha256") == DesktopServerRecoveryCode.verifier(requireNotNull(owner.server), requireNotNull(owner.account), next))
                val archive = SecureJson.parse(document, 4096)
                controlRequire(archive.string("account_id") == owner.account && archive.string("server_instance_id") == owner.server &&
                    archive.number("identity_epoch") == owner.identityEpoch && archive.string("identity_thumbprint_sha256") == owner.identityThumbprint &&
                    archive.string("api_origin") == owner.apiOrigin && archive.string("stream_origin") == owner.streamOrigin &&
                    archive.string("code").toByteArray(Charsets.US_ASCII).contentEquals(next))
            }
            if (!configure) controlRequire(current.isNotEmpty() || deletion.isNotEmpty())
            return true
        } finally { current.fill(0); deletion.fill(0); next.fill(0); document.fill(0) }
    }

    private fun verifyOwnHash(body: JsonObject, omitted: Set<String>, expected: ByteArray?, proof: ByteArray,
        key: ByteArray? = null, domain: String? = null) {
        val unsigned = SecureJson.encode(JsonObject(body.filterKeys { it !in omitted }))
        val digest = try { MessageDigest.getInstance("SHA-256").digest(unsigned) } finally { unsigned.fill(0) }
        try {
            if (expected != null) controlRequire(digest.contentEquals(expected))
            controlRequire(body.string("request_sha256") == hex(digest))
            if (domain == null) controlRequire(proof.isEmpty())
            else {
                val embedded = DesktopAccountTrust.decode(body.string("device_signature_b64url"), true, 64)
                try { controlRequire(embedded.contentEquals(proof)) } finally { embedded.fill(0) }
                verifySignature(requireNotNull(key), domain, digest, proof, true)
            }
        } finally { digest.fill(0) }
    }
    private fun verifyPortable(record: SecureRecord, pending: PendingPayload, body: JsonObject) {
        val request = body["request"] as? JsonObject ?: invalid()
        val challenge = request["challenge"] as? JsonObject ?: invalid()
        controlRequire(challenge.string("server_instance_id") == record.owner.server && challenge.number("identity_epoch") == record.owner.identityEpoch &&
            challenge.string("identity_thumbprint_sha256") == record.owner.identityThumbprint && challenge.string("api_origin") == record.owner.apiOrigin &&
            challenge.string("stream_origin") == record.owner.streamOrigin)
        val rotation = pending.wireKind == PendingWireKind.PORTABLE_ROTATE
        val document = if (rotation) DesktopPendingCeremonyMaterial.previousPortableBinding(pending).use { secret ->
            val archive = secret.copy(); try { PortableRecoveryFile.parse(archive).use { it.document } } finally { archive.fill(0) }
        } else {
            val passport = SecureJson.encode(request.getValue("account"))
            try { PortableAccountDocument.parse(passport) } finally { passport.fill(0) }
        }
        controlRequire(document.accountId == record.owner.account)
        val purpose = if (rotation) "rotate" else if (pending.wireKind == PendingWireKind.PORTABLE_LOGIN) "login" else "register"
        controlRequire(challenge.string("purpose") == purpose.uppercase())
        if (!rotation) {
            controlRequire(record.owner.protocolVersion == 2 && request.string("platform") == "MACOS" && request.string("binding_commit_id") == record.owner.bindingCommit)
            val device = DesktopAccountTrust.decode(request.string("device_public_key_spki_b64"), false, 91)
            try { controlRequire(secureSha256(device) == record.owner.keyThumbprint) } finally { device.fill(0) }
        } else controlRequire(record.owner.protocolVersion == 1)
        val canonical = SecureJson.encode(request); val proof = Base64.getDecoder().decode(body.string("proof")); val key = document.publicKeySpki
        try {
            verifySignature(key, "autplay:portable-account:$purpose:v${if (rotation) 1 else 2}\n", canonical, proof, false)
            if (rotation) {
                val replacementBytes = SecureJson.encode(request.getValue("replacement_account"))
                val replacement = try { PortableAccountDocument.parse(replacementBytes) } finally { replacementBytes.fill(0) }
                controlRequire(replacement.accountId == record.owner.account)
                val nextKey = replacement.publicKeySpki; val nextProof = Base64.getDecoder().decode(body.string("replacement_proof"))
                try { verifySignature(nextKey, "autplay:portable-account:rotate:v1\n", canonical, nextProof, false) }
                finally { nextKey.fill(0); nextProof.fill(0) }
            }
        } finally { canonical.fill(0); proof.fill(0); key.fill(0) }
    }
    private fun trust(body: JsonObject, owner: SecureRecordOwner) {
        controlRequire(owner.protocolVersion == 2 || body.string("contract_version") == "v1")
        controlRequire(body.string("expected_server_instance_id") == owner.server && body.number("expected_identity_epoch") == owner.identityEpoch &&
            body.string("expected_identity_thumbprint_sha256") == owner.identityThumbprint && body.string("expected_api_origin") == owner.apiOrigin &&
            body.string("expected_stream_origin") == owner.streamOrigin)
        if (owner.protocolVersion == 2) controlRequire(body.string("contract_version") == "v2" && body.number("schema_version") == 2L && body.string("platform") == "MACOS")
    }
    private fun scope(body: JsonObject, owner: SecureRecordOwner) {
        for (field in listOf("account_id", "confirmed_account_id", "approved_account_id", "expected_user_id")) if (field in body) controlRequire(body.string(field) == owner.account)
        for (field in listOf("operation_id", "exchange_id", "rotation_id")) if (field in body) controlRequire(body.string(field) == owner.operation)
        if ("binding_commit_id" in body) controlRequire(body.string("binding_commit_id") == owner.bindingCommit)
        if ("device_id" in body) controlRequire(body.string("device_id") == owner.device)
    }
    private fun requireDigest(bytes: ByteArray, expected: ByteArray) {
        val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
        try { controlRequire(digest.contentEquals(expected)) } finally { digest.fill(0) }
    }
    private fun verifySignature(key: ByteArray, domain: String, body: ByteArray, proof: ByteArray, p1363: Boolean) {
        val publicKey = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(key))
        controlRequire(Signature.getInstance(if (p1363) "SHA256withECDSAinP1363Format" else "SHA256withECDSA").run {
            initVerify(publicKey); update(domain.toByteArray(Charsets.US_ASCII)); update(body); verify(proof)
        })
    }
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    private fun invalid(): Nothing = throw AccountControlException(AccountControlFailure.CONFLICT)
}
