package app.autplay.application.securestate

import app.autplay.application.devicecompatibility.ProfileDeviceContractV2
import app.autplay.application.devicecompatibility.SelfDeviceContractV2
import app.autplay.application.devicecompatibility.RecoveryDeletionDeviceContractV2
import java.security.MessageDigest
import java.util.Base64
import kotlinx.serialization.json.JsonObject

/** Reconstructs verified typed *owners* of exact stored wire; never prepares or signs a retry. */
public object SecureJournalRestoration {
    public fun requireScope(prepared: ProfileDeviceContractV2.PreparedRequest, owner: SecureRecordOwner): Unit = safe {
        val context = prepared.context
        requireTrust(context.identity, owner)
        if (context.session.isNotEmpty()) secureRequire(context.session == listOf(owner.account, owner.device, owner.session, owner.family, owner.bindingCommit), SecureStateStatus.CONTEXT_CHANGED)
        val bytes = prepared.transientUnsignedCopy()
        try { requireFields(SecureJson.parse(bytes, 16384), owner) } finally { bytes.fill(0) }
    }
    public fun requireScope(prepared: SelfDeviceContractV2.PreparedRequest, owner: SecureRecordOwner): Unit = safe {
        requireTrust(prepared.context.identity, owner)
        secureRequire(prepared.context.ceremonyId == owner.ceremony && prepared.operation.role.name == owner.role.name && prepared.context.sourcePlatform.name == owner.selfSourcePlatform, SecureStateStatus.CONTEXT_CHANGED)
        val bytes = prepared.canonicalUnsignedCopy()
        try { requireFields(SecureJson.parse(bytes, 8192), owner) } finally { bytes.fill(0) }
    }
    public fun requireScope(prepared: RecoveryDeletionDeviceContractV2.PreparedRequest, owner: SecureRecordOwner): Unit = safe {
        requireTrust(prepared.context.identity, owner)
        val bytes = prepared.unsignedCopy()
        try { requireFields(SecureJson.parse(bytes, 8192), owner) } finally { bytes.fill(0) }
    }
    public fun requireScope(command: ProfileDeviceContractV2.SignedCommand, owner: SecureRecordOwner): Unit = safe {
        requireTrust(command.context.identity, owner)
        if (command.context.session.isNotEmpty()) secureRequire(command.context.session == listOf(owner.account, owner.device, owner.session, owner.family, owner.bindingCommit), SecureStateStatus.CONTEXT_CHANGED)
        val bytes = command.transientWireCopy()
        try { requireFields(SecureJson.parse(bytes, 16384), owner) } finally { bytes.fill(0) }
    }
    public fun requireScope(command: SelfDeviceContractV2.WireRequest, owner: SecureRecordOwner): Unit = safe {
        requireTrust(command.prepared.context.identity, owner)
        secureRequire(command.prepared.context.ceremonyId == owner.ceremony && command.platformRole.name == owner.role.name && command.prepared.context.sourcePlatform.name == owner.selfSourcePlatform, SecureStateStatus.CONTEXT_CHANGED)
        val bytes = command.canonicalDocumentCopy()
        try { requireFields(SecureJson.parse(bytes, 8192), owner) } finally { bytes.fill(0) }
    }
    public fun requireScope(command: RecoveryDeletionDeviceContractV2.SignedCommand, owner: SecureRecordOwner): Unit = safe {
        requireTrust(command.context.identity, owner)
        val bytes = command.fullOriginalWireCopy()
        try { requireFields(SecureJson.parse(bytes, 8192), owner) } finally { bytes.fill(0) }
    }
    private fun requireTrust(identity: app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity, owner: SecureRecordOwner) {
        secureRequire(owner.protocolVersion == 2 && identity.serverInstanceId == owner.server && identity.identityEpoch == owner.identityEpoch && identity.thumbprintSha256 == owner.identityThumbprint && identity.apiOrigin == owner.apiOrigin && identity.streamOrigin == owner.streamOrigin, SecureStateStatus.CONTEXT_CHANGED)
    }
    private fun requireFields(document: JsonObject, owner: SecureRecordOwner) {
        for (name in listOf("account_id", "confirmed_account_id", "approved_account_id", "expected_user_id"))
            if (name in document) secureRequire(document.string(name) == owner.account, SecureStateStatus.CONTEXT_CHANGED)
        if ("device_id" in document) secureRequire(document.string("device_id") == owner.device, SecureStateStatus.CONTEXT_CHANGED)
        if ("binding_commit_id" in document) secureRequire(document.string("binding_commit_id") == owner.bindingCommit, SecureStateStatus.CONTEXT_CHANGED)
        for (name in listOf("operation_id", "exchange_id", "rotation_id"))
            if (name in document) secureRequire(document.string(name) == owner.operation, SecureStateStatus.CONTEXT_CHANGED)
    }

    public fun profile(record: SecureRecord, context: ProfileDeviceContractV2.Context): ProfileDeviceContractV2.SignedCommand = safe {
        val pending = record.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        secureRequire(pending.kind == SecureRecordKind.PROFILE_COMMAND && context.platform.name == record.owner.platform)
        secureRequire(pending.wireKind.name == context.operation.name)
        secureRequire(context.identity.serverInstanceId == record.owner.server && context.identity.identityEpoch == record.owner.identityEpoch &&
            context.identity.thumbprintSha256 == record.owner.identityThumbprint && context.identity.apiOrigin == record.owner.apiOrigin && context.identity.streamOrigin == record.owner.streamOrigin)
        val key = context.keyCopy()
        try { secureRequire(secureSha256(key) == record.owner.keyThumbprint) } finally { key.fill(0) }
        if (context.session.isNotEmpty()) secureRequire(context.session == listOf(record.owner.account, record.owner.device, record.owner.session, record.owner.family, record.owner.bindingCommit))
        val wire = pending.requestCopy(); val digest = pending.requestDigestCopy(); val proof = pending.signatureCopy()
        try {
            val document = SecureJson.parse(wire, ProfileDeviceContractV2.MAX_BODY_BYTES, canonical = false)
            val operation = context.operation
            val unsigned = JsonObject(document.filterKeys { it != operation.signatureField && (!operation.ownHash || it != "request_sha256") })
            val bytes = SecureJson.encode(unsigned)
            val expected = try { MessageDigest.getInstance("SHA-256").digest(bytes) } finally { bytes.fill(0) }
            try { secureRequire(expected.contentEquals(digest)) } finally { expected.fill(0) }
            val encoded = Base64.getUrlDecoder().decode(document.string(operation.signatureField))
            try { secureRequire(encoded.contentEquals(proof)) } finally { encoded.fill(0) }
            val original = ProfileDeviceContractV2.SignedCommand(context, wire, digest, proof)
            try {
                ProfileDeviceContractV2.rawBody(wire).use { ProfileDeviceContractV2.requireExactOriginal(original, it, context) }
                original
            } catch (failure: Exception) { original.close(); throw failure }
        } finally { wire.fill(0); digest.fill(0); proof.fill(0) }
    }

    public fun self(record: SecureRecord, operation: SelfDeviceContractV2.Operation,
        context: SelfDeviceContractV2.Context): SelfDeviceContractV2.WireRequest = safe {
        val pending = record.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        secureRequire(pending.operation == PendingOperation.SELF && record.owner.role.name == operation.role.name)
        secureRequire(pending.wireKind.name == "SELF_${operation.name}" && context.sourcePlatform.name == record.owner.selfSourcePlatform)
        val wire = pending.requestCopy()
        try { SelfDeviceContractV2.readRequest(operation, wire, context) } finally { wire.fill(0) }
    }

    public fun recoveryOrDeletion(record: SecureRecord,
        context: RecoveryDeletionDeviceContractV2.Context): RecoveryDeletionDeviceContractV2.SignedCommand = safe {
        val pending = record.payload as? PendingPayload ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        secureRequire(pending.operation in setOf(PendingOperation.RECOVERY, PendingOperation.DELETION))
        secureRequire(pending.wireKind.name == context.bodyKind.name)
        val wire = pending.requestCopy(); val digest = pending.requestDigestCopy(); val proof = pending.signatureCopy()
        try {
            val original = RecoveryDeletionDeviceContractV2.verifyOriginal(context, wire)
            val verifiedDigest = original.requestDigestCopy(); val verifiedProof = original.signatureCopy()
            try {
                secureRequire(verifiedDigest.contentEquals(digest) && verifiedProof.contentEquals(proof))
                original
            } catch (failure: Exception) { original.close(); throw failure }
            finally { verifiedDigest.fill(0); verifiedProof.fill(0) }
        } finally { wire.fill(0); digest.fill(0); proof.fill(0) }
    }
}
