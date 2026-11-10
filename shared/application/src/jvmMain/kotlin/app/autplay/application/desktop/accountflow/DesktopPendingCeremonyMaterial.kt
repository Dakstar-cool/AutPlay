package app.autplay.application.desktop.accountflow

import app.autplay.application.portableaccount.CreatedPortableAccount
import app.autplay.application.portableaccount.PortableAccountCrypto
import app.autplay.application.portableaccount.PortableRecoveryFile
import app.autplay.application.securestate.*

/** Named application projections of already authorized K records; no persistence or authority API. */
public object DesktopPendingCeremonyMaterial {
    public fun withAuthorization(payload: PendingPayload, authorization: ByteArray): PendingPayload {
        val fields = payload.fields()
        try {
            secureRequire(fields.size == 16)
            payload.secretContextCopy().use { secrets ->
                return PendingPayload(payload.kind, payload.operation, payload.wireKind, fields[2], fields[3], fields[4], authorization,
                    fields[6], fields[7], fields[8], fields[9], secrets)
            }
        } finally { fields.forEach { it.fill(0) } }
    }
    public fun sessionMaterialization(payload: SessionPayload): SecretBytes {
        val fields = payload.fields()
        try { secureRequire(fields.size == 5); return SecretBytes(fields[2]) }
        finally { fields.forEach { it.fill(0) } }
    }
    public fun preserveMaterialization(payload: SessionPayload, materialization: ByteArray): SessionPayload {
        val access = payload.accessCopy(); val refresh = payload.refreshCopy(); val local = payload.localGuardCopy()
        val transport = payload.transportPolicyCopy(); val m5 = payload.m5GuardCopy()
        try { return SessionPayload(access, refresh, payload.d1, payload.m5, local, transport, m5,
            payload.accessExpiresAt, payload.refreshExpiresAt, materialization) }
        finally { access.fill(0); refresh.fill(0); local.fill(0); transport.fill(0); m5.fill(0) }
    }
    public fun portableRotationArchive(pending: PendingPayload): SecretBytes {
        secureRequire(pending.kind == SecureRecordKind.PORTABLE_COMMAND && pending.operation == PendingOperation.PORTABLE_ROTATE)
        val fields = pending.fields()
        try { secureRequire(fields.size == 16 && fields[8].size in 2..65_536); return SecretBytes(fields[8]) }
        finally { fields.forEach { it.fill(0) } }
    }
    public fun previousPortableBinding(pending: PendingPayload): SecretBytes {
        secureRequire(pending.kind == SecureRecordKind.PORTABLE_COMMAND && pending.operation == PendingOperation.PORTABLE_ROTATE)
        val fields = pending.fields()
        try { secureRequire(fields.size == 16 && fields[9].size <= 32_768); return SecretBytes(fields[9]) }
        finally { fields.forEach { it.fill(0) } }
    }
    /** Exact archived successor, after proving its recovery code opens that same public ROOT. */
    public fun restorePortableSuccessor(archive: ByteArray): CreatedPortableAccount {
        PortableRecoveryFile.parse(archive).use { recovery ->
            PortableAccountCrypto.recover(recovery.document, recovery.code).close()
            return CreatedPortableAccount(recovery.document, recovery.code.copyOf())
        }
    }
}
