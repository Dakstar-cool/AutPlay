package app.autplay.application.securestate

/** Named ceremony secrets, never a generic JSON/map secret store or an ordinary Journal entry. */
public class PendingSecretContext(
    rendezvousSecret: ByteArray = byteArrayOf(), pollSecret: ByteArray = byteArrayOf(),
    recoveryCode: ByteArray = byteArrayOf(), nextRecoveryCode: ByteArray = byteArrayOf(),
    deletionCode: ByteArray = byteArrayOf(), recoveryDocument: ByteArray = byteArrayOf(),
) : AutoCloseable {
    init { secureRequire(listOf(rendezvousSecret, pollSecret, recoveryCode, nextRecoveryCode, deletionCode, recoveryDocument).all { it.size <= 4096 }) }
    private val fields: List<SecretBytes> = listOf(rendezvousSecret, pollSecret, recoveryCode, nextRecoveryCode, deletionCode, recoveryDocument).map { SecretBytes(it) }
    public val totalBytes: Int get() = fields.sumOf { it.size }
    public fun rendezvousCopy(): ByteArray = fields[0].copy()
    public fun pollCopy(): ByteArray = fields[1].copy()
    public fun recoveryCodeCopy(): ByteArray = fields[2].copy()
    public fun nextRecoveryCodeCopy(): ByteArray = fields[3].copy()
    public fun deletionCodeCopy(): ByteArray = fields[4].copy()
    public fun recoveryDocumentCopy(): ByteArray = fields[5].copy()
    public fun duplicate(): PendingSecretContext {
        val copies = fieldCopies()
        try { return PendingSecretContext(copies[0], copies[1], copies[2], copies[3], copies[4], copies[5]) }
        finally { copies.forEach { it.fill(0) } }
    }
    internal fun fieldCopies(): List<ByteArray> {
        val result = ArrayList<ByteArray>()
        try { fields.forEach { result.add(it.copy()) }; return result }
        catch (failure: Exception) { result.forEach { it.fill(0) }; throw failure }
    }
    internal fun requireFor(wire: PendingWireKind) {
        when (wire.family) {
            PendingOperation.SELF -> {
                secureRequire(fields.drop(2).all { it.size == 0 })
                if (wire in setOf(PendingWireKind.SELF_START, PendingWireKind.SELF_CLAIM, PendingWireKind.SELF_DECISION)) secureRequire(fields[0].size > 0)
                if (wire in setOf(PendingWireKind.SELF_CLAIM, PendingWireKind.SELF_POLL, PendingWireKind.SELF_EXCHANGE)) secureRequire(fields[1].size > 0)
            }
            PendingOperation.RECOVERY -> {
                secureRequire(fields[0].size == 0 && fields[1].size == 0 && fields[4].size == 0)
                if (wire == PendingWireKind.RECOVERY_CONFIGURE) secureRequire(fields[3].size > 0 && fields[5].size > 0)
                else secureRequire(fields[2].size > 0)
                if (wire == PendingWireKind.RECOVERY_COMMIT) secureRequire(fields[3].size > 0)
            }
            PendingOperation.DELETION -> {
                secureRequire(fields[0].size == 0 && fields[1].size == 0)
                if (wire != PendingWireKind.DELETION_REQUEST) secureRequire(fields[4].size > 0)
                if (wire == PendingWireKind.DELETION_CANCEL) secureRequire(fields[3].size > 0)
            }
            else -> secureRequire(totalBytes == 0)
        }
    }
    internal fun requireRetains(previous: PendingSecretContext) {
        val old = previous.fieldCopies()
        val next = try { fieldCopies() } catch (failure: Exception) { old.forEach { it.fill(0) }; throw failure }
        try { old.indices.forEach { secureRequire(old[it].isEmpty() || old[it].contentEquals(next[it]), SecureStateStatus.OUTCOME_PENDING) } }
        finally { old.forEach { it.fill(0) }; next.forEach { it.fill(0) } }
    }
    override fun close(): Unit = fields.forEach { it.close() }
    override fun toString(): String = "PendingSecretContext(<redacted>)"
}
