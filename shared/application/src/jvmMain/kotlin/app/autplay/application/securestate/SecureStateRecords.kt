package app.autplay.application.securestate

import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.security.MessageDigest
import java.util.UUID
import kotlinx.serialization.json.*

public enum class SecureStateStatus {
    IDENTITY_UNAVAILABLE, IN_USE, LOCKED, ACCESS_DENIED, KEY_MISSING, CONTEXT_CHANGED,
    OUTCOME_PENDING, CAPACITY_REQUIRED, CORRUPT, CLOSED, UNAVAILABLE,
}
public class SecureStateFailure(public val status: SecureStateStatus) :
    IllegalStateException("SECURE_STATE_$status") {
    override fun toString(): String = "SECURE_STATE_$status"
}
public fun secureRequire(value: Boolean, status: SecureStateStatus = SecureStateStatus.CORRUPT): Unit {
    if (!value) throw SecureStateFailure(status)
}
public object SecureStateLimits {
    public const val VALUE: Int = 1_048_576
    public const val HEADER: Int = 32_768
    public const val CONTROL: Int = 65_536
    public const val LOGICAL: Int = 32
    public const val REVISIONS: Int = 64
    public const val KEYS: Int = 32
    public const val TOTAL: Long = 16_777_216
    public const val INTEGER: Long = 9_007_199_254_740_991
    public fun next(value: Long): Long { secureRequire(value in 0 until INTEGER); return value + 1 }
}

/** Owns mutable material; neither generated equality/copy nor diagnostic output exposes it. */
public class SecretBytes(bytes: ByteArray, private val released: () -> Unit = {}) : AutoCloseable {
    init { secureRequire(bytes.size <= SecureStateLimits.VALUE) }
    private val value: ByteArray = bytes.copyOf()
    private var closed: Boolean = false
    @Synchronized public fun copy(): ByteArray { secureRequire(!closed, SecureStateStatus.CLOSED); return value.copyOf() }
    public val size: Int get() = value.size
    @Synchronized override fun close() { if (!closed) { value.fill(0); closed = true; released() } }
    override fun toString(): String = "SecretBytes(<redacted>)"
}

public enum class SecureRecordKind(public val reserved: String? = null) {
    SESSION,
    SELF_SOURCE("a89a7ef3-fb5a-46aa-a827-1bf120d55dab"),
    SELF_RECIPIENT("14c2edb0-f9ef-43d0-af74-63dc06373bfb"),
    RECOVERY_SOURCE("f5419a71-6b38-4cb1-a28d-f20d4d8ba780"),
    RECOVERY_RECIPIENT("b623bd90-e84b-4621-9c83-a3bd69ba8db1"),
    DELETION_SOURCE("3ca064af-046e-4fd7-af6d-b47ec651a7c7"),
    DELETION_RECIPIENT("bf7d0f66-35dd-409b-8330-49d582507daf"),
    PORTABLE_ROOT("65f4db6b-65f2-48e1-afdd-e1c637f5b79a"),
    PORTABLE_COMMAND("f04799aa-e226-4361-9a83-9fe1f6a4b60b"),
    PROFILE_COMMAND, TRAINING_CONSENT, LIFECYCLE_COMMAND,
}
public enum class SecureStage { PREPARED, DISPATCHED, OUTCOME_PENDING, BINDING_COMMIT_PENDING, ACTIVE, INACTIVE, RECORDED, TERMINAL }
public enum class SecureRole { LOCAL, SOURCE, RECIPIENT, AUTHENTICATED }

/** Nullable authority is explicit for an unfinished ceremony. Never infer it from wire data. */
public data class SecureRecordOwner(
    public val boot: String, public val authorityRevision: Long,
    public val profile: String?, public val account: String?, public val server: String?,
    public val identityEpoch: Long?, public val identityThumbprint: String?,
    public val apiOrigin: String?, public val streamOrigin: String?,
    public val platform: String = "MACOS", public val protocolVersion: Int = 2,
    public val device: String?, public val keyTag: String?, public val keyThumbprint: String?,
    public val bindingCommit: String?, public val session: String?, public val family: String?,
    public val sessionGeneration: Long?, public val role: SecureRole,
    public val ceremony: String?, public val operation: String?, public val claim: String?,
    public val expiresAtEpochSeconds: Long?, public val stage: SecureStage,
    public val deletionVeto: Boolean = false, public val detached: Boolean = false,
    public val selfSourcePlatform: String? = null,
    public val accountProjection: app.autplay.application.desktop.account.AccountProtectedWitness? = null,
) {
    init {
        canonicalUuid(boot); secureRequire(authorityRevision in 0..SecureStateLimits.INTEGER)
        listOf(profile, account, server, device, bindingCommit, session, family, ceremony, operation, claim)
            .filterNotNull().forEach(::canonicalUuid)
        secureRequire(platform == "MACOS" && protocolVersion in 1..2)
        secureRequire(selfSourcePlatform == null || selfSourcePlatform in setOf("ANDROID", "MACOS"))
        listOf(identityEpoch, sessionGeneration, expiresAtEpochSeconds).filterNotNull()
            .forEach { secureRequire(it in 0..SecureStateLimits.INTEGER) }
        listOf(identityThumbprint, keyThumbprint).filterNotNull().forEach(::sha256Text)
        listOf(apiOrigin, streamOrigin).filterNotNull().forEach {
            val uri = java.net.URI(it)
            secureRequire(it.length <= 2048 && uri.scheme == "https" && uri.host != null &&
                uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null && uri.rawPath.isNullOrEmpty())
        }
        keyTag?.let(::deviceKeyTag)
    }
    public fun requireBinding(): Unit = secureRequire(listOf(profile, account, server, identityEpoch,
        identityThumbprint, apiOrigin, streamOrigin, device, keyTag, keyThumbprint, bindingCommit,
        session, family, sessionGeneration).all { it != null }, SecureStateStatus.CONTEXT_CHANGED)
    override fun toString(): String = "SecureRecordOwner(<redacted>)"
}

/** These guards are independent; comparing their revision numbers to one another is forbidden. */
public data class CapabilityGuard(public val revision: Long, public val contentSha256: String,
    public val b1ContentSha256: String? = null) {
    init { secureRequire(revision in 1..SecureStateLimits.INTEGER); sha256Text(contentSha256); b1ContentSha256?.let(::sha256Text) }
    public fun accepts(next: CapabilityGuard): Boolean = next.revision > revision ||
        next.revision == revision && next.contentSha256 == contentSha256 && (b1ContentSha256 == null || next.b1ContentSha256 == b1ContentSha256)
    override fun toString(): String = "CapabilityGuard(<redacted>)"
}

public sealed class SecurePayload : AutoCloseable {
    public abstract val kind: SecureRecordKind
    internal abstract fun fields(): List<ByteArray>
    internal open fun mediaHeader(): JsonElement = JsonNull
    override fun toString(): String = "SecurePayload(<redacted>)"
}
public class SessionPayload(
    access: ByteArray, refresh: ByteArray,
    public val d1: CapabilityGuard, public val m5: M5CapabilityGuard?,
    localMediaGuard: ByteArray, transportPolicy: ByteArray, m5HighWater: ByteArray,
    public val accessExpiresAt: Long, public val refreshExpiresAt: Long,
    materialization: ByteArray = byteArrayOf(),
) : SecurePayload() {
    override val kind: SecureRecordKind = SecureRecordKind.SESSION
    init {
        if (access.isNotEmpty() || refresh.isNotEmpty()) { token(access); token(refresh) }
        secureRequire(localMediaGuard.size <= 512 && transportPolicy.size <= 1024 && m5HighWater.size <= 256 && materialization.size <= 24576)
        if (materialization.isNotEmpty()) secureRequire(strictUtf8(materialization).length <= 8192)
        secureRequire(accessExpiresAt in 1..SecureStateLimits.INTEGER && refreshExpiresAt in accessExpiresAt..SecureStateLimits.INTEGER)
        if (localMediaGuard.isNotEmpty()) SecureMediaGuardCodec.readLocal(localMediaGuard)
        if (transportPolicy.isNotEmpty()) SecureMediaGuardCodec.readTransport(transportPolicy)
        secureRequire(if (m5HighWater.isEmpty()) m5 == null else SecureMediaGuardCodec.readM5(m5HighWater) == m5)
    }
    private val values: List<SecretBytes> = listOf(access, refresh, localMediaGuard, transportPolicy, m5HighWater, materialization).map { SecretBytes(it) }
    public fun accessCopy(): ByteArray = values[0].copy()
    public fun refreshCopy(): ByteArray = values[1].copy()
    public fun mediaGuardCopy(): ByteArray = values[2].copy()
    public fun transportPolicyCopy(): ByteArray = values[3].copy()
    public fun localGuardCopy(): ByteArray = values[2].copy()
    public fun m5GuardCopy(): ByteArray = values[4].copy()
    public val hasCredentials: Boolean get() = values[0].size > 0 && values[1].size > 0
    public fun withoutCredentials(): SessionPayload {
        val copies = ownedCopies(values)
        try { return SessionPayload(byteArrayOf(), byteArrayOf(), d1, m5, copies[2], copies[3], copies[4], accessExpiresAt, refreshExpiresAt, copies[5]) }
        finally { copies.forEach { it.fill(0) } }
    }
    public fun withLocalGuard(guard: ByteArray): SessionPayload {
        val copies = ownedCopies(values)
        try { return SessionPayload(copies[0], copies[1], d1, m5, guard, copies[3], copies[4], accessExpiresAt, refreshExpiresAt, copies[5]) }
        finally { copies.forEach { it.fill(0) } }
    }
    public fun withConnectionGuards(d1: CapabilityGuard, m5: M5CapabilityGuard?, transport: ByteArray): SessionPayload {
        val copies = ownedCopies(values); val highWater = m5?.let(SecureMediaGuardCodec::m5) ?: byteArrayOf()
        try { return SessionPayload(copies[0], copies[1], d1, m5, copies[2], transport, highWater, accessExpiresAt, refreshExpiresAt, copies[5]) }
        finally { copies.forEach { it.fill(0) }; highWater.fill(0) }
    }
    internal override fun mediaHeader(): JsonElement = mediaHeader(values[2], values[3], values[4])
    internal override fun fields(): List<ByteArray> = ownedCopies(listOf(values[0], values[1], values[5])) + listOf(guardBytes(d1), ByteBuffer.allocate(16).putLong(accessExpiresAt).putLong(refreshExpiresAt).array())
    override fun close(): Unit = values.forEach { it.close() }
}

/** Exact portable formats remain unchanged. Archived predecessor secrets are never discarded. */
public class PortableRootPayload(
    passport: ByteArray, recovery: ByteArray, lineage: ByteArray,
    public val ownershipCutoffMs: Long, public val everEnrolled: Boolean,
    localMediaGuard: ByteArray = byteArrayOf(),
) : SecurePayload() {
    override val kind: SecureRecordKind = SecureRecordKind.PORTABLE_ROOT
    init {
        secureRequire(passport.size in 2..4096 && recovery.size in 2..65536 && lineage.size in 2..49152 && ownershipCutoffMs in 0..SecureStateLimits.INTEGER)
        secureRequire(localMediaGuard.size <= 512)
        if (localMediaGuard.isNotEmpty()) SecureMediaGuardCodec.readLocal(localMediaGuard)
        val document = app.autplay.application.portableaccount.PortableAccountDocument.parse(passport)
        // Structural validation here does not unlock ROOT or perform recovery cryptography.
        val history = SecureJson.parse(lineage, 49152)
        history.exact("version", "ever_enrolled", "predecessors")
        secureRequire(history.number("version") == 1L && history.boolean("ever_enrolled") == everEnrolled)
        val entries = history.array("predecessors")
        secureRequire(entries.size <= 8)
        val seen = HashSet<String>()
        entries.forEach {
            val entry = it as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
            entry.exact("account", "recovery_code")
            val bytes = SecureJson.encode(entry.getValue("account"))
            try {
                val old = app.autplay.application.portableaccount.PortableAccountDocument.parse(bytes)
                secureRequire(old.accountId == document.accountId && old.publicId == document.publicId && seen.add(old.keyThumbprint) && old.keyThumbprint != document.keyThumbprint)
                val code = java.util.Base64.getUrlDecoder().decode(entry.string("recovery_code"))
                try { secureRequire(code.size == 32 && java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(code) == entry.string("recovery_code")) } finally { code.fill(0) }
            } finally { bytes.fill(0) }
        }
        val recoveryObject = SecureJson.parse(recovery, 65536)
        recoveryObject.exact("format", "version", "account", "recovery_code", "predecessors")
        secureRequire(recoveryObject.string("format") == "autplay-portable-account-recovery" && recoveryObject.number("version") == 2L)
        val currentCode = java.util.Base64.getUrlDecoder().decode(recoveryObject.string("recovery_code"))
        try { secureRequire(currentCode.size == 32 && java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(currentCode) == recoveryObject.string("recovery_code")) } finally { currentCode.fill(0) }
        val current = SecureJson.encode(document.fields)
        val recoveryAccount = SecureJson.encode(recoveryObject.getValue("account"))
        try { secureRequire(current.contentEquals(recoveryAccount)) } finally { current.fill(0); recoveryAccount.fill(0) }
        secureRequire(recoveryObject["predecessors"] == history["predecessors"])
    }
    private val values: List<SecretBytes> = listOf(passport, recovery, lineage).map { SecretBytes(it) }
    private val localGuard: SecretBytes = SecretBytes(localMediaGuard)
    public fun passportCopy(): ByteArray = values[0].copy()
    public fun recoveryCopy(): ByteArray = values[1].copy()
    public fun lineageCopy(): ByteArray = values[2].copy()
    public fun localGuardCopy(): ByteArray = localGuard.copy()
    public fun markEnrollmentAttempted(): PortableRootPayload {
        val copies = ownedCopies(values + localGuard); val guard = copies[3]
        val history = SecureJson.parse(copies[2], 49152)
        val enrolled = SecureJson.encode(JsonObject(history + ("ever_enrolled" to JsonPrimitive(true))))
        try { return PortableRootPayload(copies[0], copies[1], enrolled, ownershipCutoffMs, true, guard) }
        finally { copies.forEach { it.fill(0) }; guard.fill(0); enrolled.fill(0) }
    }
    public fun withLocalGuard(guard: ByteArray): PortableRootPayload {
        val copies = ownedCopies(values)
        try { return PortableRootPayload(copies[0], copies[1], copies[2], ownershipCutoffMs, everEnrolled, guard) }
        finally { copies.forEach { it.fill(0) } }
    }
    internal override fun mediaHeader(): JsonElement = mediaHeader(localGuard, null, null)
    internal override fun fields(): List<ByteArray> = ownedCopies(values) + listOf(ByteBuffer.allocate(9).putLong(ownershipCutoffMs).put(if (everEnrolled) 1.toByte() else 0.toByte()).array())
    override fun close() { values.forEach { it.close() }; localGuard.close() }
}

public enum class PendingOperation(public val maximumWire: Int) {
    ENROLLMENT(16384), ADMISSION(16384), TRUSTED_EXCHANGE(16384), ROTATION(16384),
    SELF(8192), RECOVERY(8192), DELETION(8192), PORTABLE_REGISTER(16384), PORTABLE_LOGIN(16384),
    PORTABLE_ROTATE(16384), PUBLIC_REGISTRATION(98304), LOGOUT_CURRENT(16384), LOGOUT_ALL(16384),
    REVOKE(16384), CONSENT(4096), VISIBILITY(4096),
}

/** Original closed wire operation is persisted, including indistinguishable poll/recover bodies. */
public enum class PendingWireKind(public val family: PendingOperation) {
    ENROLLMENT_EXCHANGE(PendingOperation.ENROLLMENT),
    ENROLLMENT_INVITATION_CREATE(PendingOperation.ENROLLMENT),
    ENROLLMENT_INVITATION_CANCEL(PendingOperation.ENROLLMENT),
    ADMISSION_SUBMIT(PendingOperation.ADMISSION), ADMISSION_POLL(PendingOperation.ADMISSION),
    ADMISSION_RECOVER(PendingOperation.ADMISSION), ADMISSION_EXCHANGE(PendingOperation.ADMISSION),
    TRUSTED_CHALLENGE(PendingOperation.TRUSTED_EXCHANGE), TRUSTED_EXCHANGE(PendingOperation.TRUSTED_EXCHANGE),
    SESSION_ROTATE(PendingOperation.ROTATION),
    SELF_START(PendingOperation.SELF), SELF_CLAIM(PendingOperation.SELF), SELF_POLL(PendingOperation.SELF),
    SELF_DECISION(PendingOperation.SELF), SELF_EXCHANGE(PendingOperation.SELF),
    RECOVERY_CONFIGURE(PendingOperation.RECOVERY), RECOVERY_PREVIEW(PendingOperation.RECOVERY), RECOVERY_COMMIT(PendingOperation.RECOVERY),
    DELETION_REQUEST(PendingOperation.DELETION), DELETION_PREVIEW(PendingOperation.DELETION), DELETION_CANCEL(PendingOperation.DELETION),
    PORTABLE_REGISTER(PendingOperation.PORTABLE_REGISTER), PORTABLE_LOGIN(PendingOperation.PORTABLE_LOGIN),
    PORTABLE_ROTATE(PendingOperation.PORTABLE_ROTATE), PUBLIC_REGISTRATION(PendingOperation.PUBLIC_REGISTRATION),
    LOGOUT_CURRENT(PendingOperation.LOGOUT_CURRENT), LOGOUT_ALL(PendingOperation.LOGOUT_ALL),
    REVOKE(PendingOperation.REVOKE), CONSENT(PendingOperation.CONSENT), VISIBILITY(PendingOperation.VISIBILITY),
}

/** Named binary fields preserve final wire bytes, proof, headers and successor verbatim. */
public class PendingPayload(
    override val kind: SecureRecordKind, public val operation: PendingOperation,
    public val wireKind: PendingWireKind,
    request: ByteArray, digest: ByteArray, signature: ByteArray,
    authorization: ByteArray = byteArrayOf(), successorRefresh: ByteArray = byteArrayOf(),
    receipt: ByteArray = byteArrayOf(), recoveryArchive: ByteArray = byteArrayOf(),
    previousBinding: ByteArray = byteArrayOf(),
    secrets: PendingSecretContext? = null,
) : SecurePayload() {
    init {
        secureRequire(wireKind.family == operation)
        if (secrets == null) secureRequire(operation !in setOf(PendingOperation.SELF, PendingOperation.RECOVERY, PendingOperation.DELETION))
        else secrets.requireFor(wireKind)
        secureRequire(kind !in setOf(SecureRecordKind.SESSION, SecureRecordKind.PORTABLE_ROOT))
        secureRequire(request.size in 2..operation.maximumWire && digest.size == 32 && signature.size <= 128 && authorization.size <= 8192 && successorRefresh.size <= 8192 && receipt.size <= 65536 && recoveryArchive.size <= 65536 && previousBinding.size <= 32768)
        SecureJson.parse(request, operation.maximumWire, canonical = false)
        if (successorRefresh.isNotEmpty()) token(successorRefresh)
        secureRequire(when (kind) {
            SecureRecordKind.SELF_SOURCE, SecureRecordKind.SELF_RECIPIENT -> operation == PendingOperation.SELF
            SecureRecordKind.RECOVERY_SOURCE, SecureRecordKind.RECOVERY_RECIPIENT -> operation == PendingOperation.RECOVERY
            SecureRecordKind.DELETION_SOURCE, SecureRecordKind.DELETION_RECIPIENT -> operation == PendingOperation.DELETION
            SecureRecordKind.PORTABLE_COMMAND -> operation in setOf(PendingOperation.PORTABLE_REGISTER, PendingOperation.PORTABLE_LOGIN, PendingOperation.PORTABLE_ROTATE)
            SecureRecordKind.TRAINING_CONSENT -> operation == PendingOperation.CONSENT
            SecureRecordKind.PROFILE_COMMAND -> operation in setOf(PendingOperation.ENROLLMENT, PendingOperation.ADMISSION, PendingOperation.TRUSTED_EXCHANGE, PendingOperation.ROTATION, PendingOperation.PUBLIC_REGISTRATION)
            SecureRecordKind.LIFECYCLE_COMMAND -> operation in setOf(PendingOperation.LOGOUT_CURRENT, PendingOperation.LOGOUT_ALL, PendingOperation.REVOKE, PendingOperation.VISIBILITY)
            else -> false
        })
        val command = listOf(request, recoveryArchive, previousBinding).sumOf { it.size.toLong() }
        secureRequire(command <= when { operation == PendingOperation.PUBLIC_REGISTRATION -> 98_304; kind == SecureRecordKind.PORTABLE_COMMAND -> 98_304; kind == SecureRecordKind.TRAINING_CONSENT -> 4096; else -> 65_536 })
        if (operation == PendingOperation.PUBLIC_REGISTRATION) secureRequire(strictUtf8(request).length <= 32_768)
    }
    private val secretContext: PendingSecretContext = secrets?.duplicate() ?: PendingSecretContext()
    private val values: List<SecretBytes> = listOf(request, digest, signature, authorization, successorRefresh, receipt, recoveryArchive, previousBinding).map { SecretBytes(it) }
    public fun requestCopy(): ByteArray = values[0].copy()
    public fun requestDigestCopy(): ByteArray = values[1].copy()
    public fun signatureCopy(): ByteArray = values[2].copy()
    public fun successorCopy(): ByteArray = values[4].copy()
    public fun authorizationCopy(): ByteArray = values[3].copy()
    public fun receiptCopy(): ByteArray = values[5].copy()
    public fun secretContextCopy(): PendingSecretContext = secretContext.duplicate()
    public fun requireRetainsSecrets(previous: PendingPayload): Unit = secretContext.requireRetains(previous.secretContext)
    public fun duplicate(receipt: ByteArray? = null): PendingPayload {
        val copies = ownedCopies(values)
        try { return PendingPayload(kind, operation, wireKind, copies[0], copies[1], copies[2], copies[3], copies[4], receipt ?: copies[5], copies[6], copies[7], secretContext) }
        finally { copies.forEach { it.fill(0) } }
    }
    internal override fun fields(): List<ByteArray> {
        val copies = ownedCopies(values)
        try { return listOf(operation.name.toByteArray(Charsets.US_ASCII), wireKind.name.toByteArray(Charsets.US_ASCII)) + copies + secretContext.fieldCopies() }
        catch (failure: Exception) { copies.forEach { it.fill(0) }; throw failure }
    }
    override fun close() { values.forEach { it.close() }; secretContext.close() }
}

public class SecureRecord(public val slot: String, public val revision: Long, public val transaction: String,
    public val owner: SecureRecordOwner, public val payload: SecurePayload,
    private val released: () -> Unit = {}) : AutoCloseable {
    init {
        canonicalUuid(slot); canonicalUuid(transaction); secureRequire(revision in 1..SecureStateLimits.INTEGER)
        val reserved = SecureRecordKind.entries.mapNotNull { it.reserved }
        if (payload.kind.reserved != null) secureRequire(slot == payload.kind.reserved)
        else secureRequire(slot !in reserved)
        if (payload.kind == SecureRecordKind.TRAINING_CONSENT) {
            secureRequire(slot == consentSlot(requireNotNull(owner.profile), requireNotNull(owner.account), requireNotNull(owner.apiOrigin)))
        } else if (payload.kind.reserved == null) secureRequire(!slot.startsWith("ad5c0e5e-"))
        if (payload is SessionPayload) {
            owner.requireBinding(); secureRequire(slot == owner.profile)
            secureRequire(owner.protocolVersion == 2)
            secureRequire(payload.hasCredentials || owner.stage == SecureStage.INACTIVE && owner.detached)
        }
        if (payload is PendingPayload) {
            if (payload.operation == PendingOperation.SELF) secureRequire(owner.selfSourcePlatform != null)
            when (payload.operation) {
                PendingOperation.PORTABLE_ROTATE, PendingOperation.PUBLIC_REGISTRATION -> secureRequire(owner.protocolVersion == 1)
                PendingOperation.LOGOUT_CURRENT, PendingOperation.LOGOUT_ALL, PendingOperation.REVOKE, PendingOperation.CONSENT, PendingOperation.VISIBILITY -> Unit
                else -> secureRequire(owner.protocolVersion == if (payload.wireKind == PendingWireKind.RECOVERY_CONFIGURE) 1 else 2)
            }
        }
        when (payload.kind) {
            SecureRecordKind.SELF_SOURCE, SecureRecordKind.RECOVERY_SOURCE, SecureRecordKind.DELETION_SOURCE -> secureRequire(owner.role == SecureRole.SOURCE)
            SecureRecordKind.SELF_RECIPIENT, SecureRecordKind.RECOVERY_RECIPIENT, SecureRecordKind.DELETION_RECIPIENT -> secureRequire(owner.role == SecureRole.RECIPIENT)
            else -> Unit
        }
    }
    override fun close() { payload.close(); released() }
    override fun toString(): String = "SecureRecord(<redacted>)"
}

public fun consentSlot(serverProfile: String, account: String, origin: String): String {
    canonicalUuid(serverProfile); canonicalUuid(account)
    val raw = "autplay:training-consent-journal:v1\n$serverProfile\n$account\n$origin".toByteArray(Charsets.UTF_8)
    val bytes = try { MessageDigest.getInstance("SHA-256").digest(raw) } finally { raw.fill(0) }
    try {
        bytes[0] = 0xad.toByte(); bytes[1] = 0x5c; bytes[2] = 0x0e; bytes[3] = 0x5e
        bytes[6] = ((bytes[6].toInt() and 15) or 128).toByte(); bytes[8] = ((bytes[8].toInt() and 63) or 128).toByte()
        val buffer = ByteBuffer.wrap(bytes); return UUID(buffer.long, buffer.long).toString()
    } finally { bytes.fill(0) }
}
public fun canonicalUuid(value: String): Unit = secureRequire(value.length == 36 && runCatching { UUID.fromString(value).toString() == value }.getOrDefault(false))
public fun deviceKeyTag(value: String): Unit { secureRequire(value.startsWith("app.autplay.desktop.device-key.v1.")); canonicalUuid(value.removePrefix("app.autplay.desktop.device-key.v1.")) }
public fun sha256Text(value: String): Unit = secureRequire(value.length == 64 && value.all { it in '0'..'9' || it in 'a'..'f' })
public fun secureSha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it.toInt() and 255) }
internal fun token(bytes: ByteArray): Unit = secureRequire(bytes.size in 1..8192 && bytes.all { it.toInt() in 0x21..0x7e })
private fun ownedCopies(values: List<SecretBytes>): List<ByteArray> {
    val copies = ArrayList<ByteArray>()
    try { values.forEach { copies.add(it.copy()) }; return copies }
    catch (failure: Exception) { copies.forEach { it.fill(0) }; throw failure }
}
internal fun guardBytes(guard: CapabilityGuard): ByteArray = SecureJson.encode(buildJsonObject { put("revision", guard.revision); put("content_sha256", guard.contentSha256); put("b1_content_sha256", guard.b1ContentSha256?.let(::JsonPrimitive) ?: JsonNull) })
private fun mediaHeader(local: SecretBytes, transport: SecretBytes?, m5: SecretBytes?): JsonElement = buildJsonObject {
    fun content(value: SecretBytes?): JsonElement {
        if (value == null) return JsonNull
        val bytes = value.copy()
        return try { if (bytes.isEmpty()) JsonNull else SecureJson.parse(bytes, 1024) } finally { bytes.fill(0) }
    }
    put("local_use", content(local)); put("transport", content(transport)); put("m5_guard", content(m5))
}
internal fun strictUtf8(bytes: ByteArray): String = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
