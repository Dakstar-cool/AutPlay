package app.autplay.desktop.account

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.devicecompatibility.ProfileDeviceContractV2 as ProfileV2
import app.autplay.application.portableaccount.*
import app.autplay.application.securestate.*
import app.autplay.application.social.normalizeSocialPublicId
import app.autplay.desktop.security.*
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*

public enum class DesktopAccountPhase { UNAVAILABLE, LOCAL, TRUST_REQUIRED, READY, CONNECTED, OUTCOME_PENDING, RETIRED }
public enum class DesktopAccountHealth { UNKNOWN, AVAILABLE, TRUST_REQUIRED, AUTH_REQUIRED, OFFLINE, PENDING, CLOSED }
/** Ordinary observable state contains no credential, origin, raw identifier, document or exception. */
public data class DesktopAccountViewState(
    public val phase: DesktopAccountPhase = DesktopAccountPhase.UNAVAILABLE,
    public val busy: Boolean = false, public val publicId: String? = null,
    public val recoveryExportRequired: Boolean = false, public val pendingCount: Int = 0,
    public val health: DesktopAccountHealth = DesktopAccountHealth.UNKNOWN,
    public val failure: AccountControlFailure? = null,
)
public sealed interface AccountLocalDataChoice {
    public data object KeepLocal : AccountLocalDataChoice
    public class ReviewSelected(changeIds: List<String>) : AccountLocalDataChoice {
        public val changeIds: List<String> = java.util.Collections.unmodifiableList(changeIds.toList())
        init { controlRequire(changeIds.size in 1..100 && changeIds.distinct().size == changeIds.size); changeIds.forEach(::canonicalUuid) }
        override fun toString(): String = "ReviewSelected(<redacted>)"
    }
}
/** Application wiring supplies the real J projection to custody. There is no volatile success default. */
public class DesktopAccountRuntime(
    internal val secure: DesktopSecureStateOwner,
    internal val custody: DesktopSecureCustody,
    internal val roots: DesktopPortableRootStore,
    internal val signer: DesktopDeviceSigner,
    internal val transport: DesktopControlTransport,
    private val deviceName: String,
    private val appVersion: String,
    internal val projection: SecureBindingProjection,
) : AutoCloseable {
    private val mutable = MutableStateFlow(DesktopAccountViewState())
    public val state: StateFlow<DesktopAccountViewState> = mutable.asStateFlow()
    private val busy = AtomicBoolean(false)
    private val epoch = AtomicLong(1)
    private val closed = AtomicBoolean(false)
    private val retired = AtomicBoolean(false)
    internal var selected: AccountSelectedServer? = null
        private set
    internal var binding: SecureRecordOwner? = null
        private set
    private var exportAcknowledged = false
    private val retainedIo = java.util.concurrent.ConcurrentHashMap<UUID, AccountIoTicket>()
    private val retainedCommands = java.util.concurrent.ConcurrentHashMap<UUID, CompletableFuture<*>>()
    internal val displayDeviceName: String get() = deviceName
    internal val applicationVersion: String get() = appVersion

    init { DesktopAccountTrust.display(deviceName, 120); DesktopAccountTrust.display(appVersion, 32) }

    /** Does not initialize a missing K. The host must separately establish virgin-install evidence. */
    public fun reload(): CompletionStage<Unit> = command {
        secure.snapshot().thenCompose { snapshot ->
            val candidates = snapshot.selected.map { it.slot }
            var pending = 0
            var publicId: String? = null
            var recoveryAvailable = false
            var active: SecureRecordOwner? = null
            fun read(index: Int): CompletableFuture<Unit> {
                if (index == candidates.size) return CompletableFuture.completedFuture(Unit)
                return secure.read(candidates[index], snapshot).thenCompose { record ->
                    try {
                        when (val payload = record?.payload) {
                            is PendingPayload -> if (record.owner.stage != SecureStage.TERMINAL) pending++
                            is PortableRootPayload -> {
                                recoveryAvailable = payload.hasRecoveryDocument
                                val bytes = payload.passportCopy()
                                try { publicId = PortableAccountDocument.parse(bytes).publicId } finally { bytes.fill(0) }
                            }
                            is SessionPayload -> if (snapshot.activeBinding == record.slot && record.owner.stage == SecureStage.ACTIVE &&
                                record.owner.boot == secure.boot && record.owner.authorityRevision == snapshot.authorityRevision &&
                                record.owner.accountProjection?.stage == app.autplay.application.desktop.account.AccountProtectedStage.READY &&
                                record.owner.accountProjection?.currentAuthorityRevision == snapshot.authorityRevision &&
                                snapshot.jInstallation?.stage == SecureJInstallationStage.READY &&
                                snapshot.jInstallation?.storeId == record.owner.accountProjection?.storeId?.value) active = record.owner
                            else -> Unit
                        }
                    } finally { record?.close() }
                    read(index + 1)
                }
            }
            read(0).thenApply {
                binding = active
                mutable.value = DesktopAccountViewState(
                    phase = if (pending > 0 || (snapshot.activeBinding != null && active == null)) DesktopAccountPhase.OUTCOME_PENDING
                        else if (active != null) DesktopAccountPhase.CONNECTED else DesktopAccountPhase.LOCAL,
                    busy = true, publicId = publicId, recoveryExportRequired = recoveryAvailable && !exportAcknowledged,
                    pendingCount = pending, health = if (pending > 0) DesktopAccountHealth.PENDING else DesktopAccountHealth.UNKNOWN)
                Unit
            }
        }
    }

    public fun pendingActions(): CompletionStage<List<DesktopPendingAction>> = command {
        secure.snapshot().thenCompose { snapshot ->
            val result = ArrayList<DesktopPendingAction>()
            fun read(index: Int): CompletableFuture<List<DesktopPendingAction>> {
                if (index == snapshot.selected.size) return CompletableFuture.completedFuture(result.toList())
                val reference = snapshot.selected[index]
                return secure.read(reference.slot, snapshot).thenCompose { record ->
                    try {
                        val pending = record?.payload as? PendingPayload
                        if (record != null && pending != null && record.owner.stage != SecureStage.TERMINAL) {
                            val purpose = when (pending.wireKind) {
                                PendingWireKind.ENROLLMENT_EXCHANGE, PendingWireKind.SESSION_ROTATE, PendingWireKind.PORTABLE_REGISTER,
                                PendingWireKind.PORTABLE_LOGIN, PendingWireKind.RECOVERY_COMMIT, PendingWireKind.DELETION_CANCEL -> DesktopPendingPurpose.SESSION_GRANT
                                PendingWireKind.PORTABLE_ROTATE -> DesktopPendingPurpose.ROOT_ROTATION
                                PendingWireKind.ADMISSION_SUBMIT, PendingWireKind.ADMISSION_POLL, PendingWireKind.ADMISSION_RECOVER,
                                PendingWireKind.ADMISSION_EXCHANGE -> DesktopPendingPurpose.ADMISSION
                                PendingWireKind.TRUSTED_CHALLENGE, PendingWireKind.TRUSTED_EXCHANGE -> DesktopPendingPurpose.TRUSTED
                                PendingWireKind.SELF_START, PendingWireKind.SELF_DECISION -> DesktopPendingPurpose.SELF_SOURCE
                                PendingWireKind.SELF_CLAIM, PendingWireKind.SELF_POLL, PendingWireKind.SELF_EXCHANGE -> DesktopPendingPurpose.SELF_RECIPIENT
                                PendingWireKind.RECOVERY_CONFIGURE -> DesktopPendingPurpose.RECOVERY_SOURCE
                                PendingWireKind.RECOVERY_PREVIEW -> DesktopPendingPurpose.RECOVERY_PREVIEW
                                PendingWireKind.DELETION_REQUEST -> DesktopPendingPurpose.DELETION_SOURCE
                                PendingWireKind.DELETION_PREVIEW -> DesktopPendingPurpose.DELETION_PREVIEW
                                PendingWireKind.LOGOUT_CURRENT, PendingWireKind.LOGOUT_ALL, PendingWireKind.REVOKE,
                                PendingWireKind.CONSENT, PendingWireKind.VISIBILITY -> DesktopPendingPurpose.PROFILE_COMMAND
                                else -> DesktopPendingPurpose.UNSUPPORTED
                            }
                            result.add(DesktopPendingAction(DesktopPendingHandle(record.slot, reference.valueId, record.owner.profile, purpose), record.owner.stage))
                        } else if (record?.payload is SessionPayload && !record.owner.detached && !record.owner.deletionVeto &&
                            (record.owner.stage == SecureStage.BINDING_COMMIT_PENDING || snapshot.activeBinding == record.slot && record.owner.boot != secure.boot))
                            result.add(DesktopPendingAction(DesktopPendingHandle(record.slot, reference.valueId, record.slot, DesktopPendingPurpose.BINDING), record.owner.stage))
                    } finally { record?.close() }
                    read(index + 1)
                }
            }
            read(0)
        }
    }
    internal fun requireCurrent(handle: DesktopPendingHandle): CompletableFuture<Unit> = command {
        secure.snapshot().thenApply { snapshot ->
            controlRequire(snapshot.selected.any { it.slot == handle.slot && it.valueId == handle.valueId }, AccountControlFailure.CONTEXT_CHANGED); Unit
        }
    }
    internal fun requireNoPendingForProfile(snapshot: SecureControl, profile: String): CompletableFuture<Unit> {
        fun read(index: Int): CompletableFuture<Unit> {
            if (index == snapshot.selected.size) return CompletableFuture.completedFuture(Unit)
            return secure.read(snapshot.selected[index].slot, snapshot).thenCompose { record ->
                try {
                    controlRequire(record == null || record.payload !is PendingPayload || record.owner.profile != profile ||
                        record.owner.stage == SecureStage.TERMINAL, AccountControlFailure.CONFLICT)
                } finally { record?.close() }
                read(index + 1)
            }
        }
        return read(0)
    }
    internal fun resumeProtected(snapshot: SecureControl, slot: String, owner: SecureRecordOwner): CompletableFuture<SecurePendingTicket> {
        val keyRead = owner.keyTag?.let { secure.publicKey(snapshot, it) } ?: CompletableFuture.completedFuture(byteArrayOf())
        return keyRead.thenCompose { key ->
            custody.resume(snapshot, slot, owner) { stored -> DesktopFrozenPendingVerifier.verify(stored, key.takeIf { it.isNotEmpty() }) }
                .whenComplete { _, _ -> key.fill(0) }
        }
    }
    internal fun prepareSelf(snapshot: SecureControl, owner: SecureRecordOwner,
        command: app.autplay.application.devicecompatibility.SelfDeviceContractV2.WireRequest,
        envelope: ByteArray, successor: ByteArray, originalQr: ByteArray = byteArrayOf()): CompletableFuture<SecurePendingTicket> {
        val retained = secure.transientSecret(envelope)
        val fields = SelfSecretEnvelope.read(envelope)
        val header = fields.header.copy(); val second = fields.second.copy()
        val secrets = if (owner.role == SecureRole.SOURCE) PendingSecretContext(rendezvousSecret = second)
            else PendingSecretContext(rendezvousSecret = header, pollSecret = second)
        val prepared = try { custody.prepareSelf(snapshot, owner, command, header, successor, secrets, originalQr) }
            finally { header.fill(0); second.fill(0); secrets.close(); fields.close() }
        return prepared.thenCompose { ticket ->
            val bytes = retained.copy()
            try { replacePreparedAuthorization(ticket, bytes) } finally { bytes.fill(0) }
        }.whenComplete { _, _ -> retained.close() }
    }
    private fun replacePreparedAuthorization(ticket: SecurePendingTicket, bytes: ByteArray): CompletableFuture<SecurePendingTicket> {
        val retained = secure.transientSecret(bytes)
        return secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            try {
                controlRequire(stored.owner == ticket.owner && stored.owner.stage == SecureStage.PREPARED)
                val authorization = retained.copy()
                val payload = try { app.autplay.application.desktop.accountflow.DesktopPendingCeremonyMaterial.withAuthorization(stored.payload as PendingPayload, authorization) }
                    finally { authorization.fill(0) }
                secure.publish(ticket.control, requireNotNull(ticket.owner.operation)) {
                    listOf(SecureRecord(stored.slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(), stored.owner, payload.duplicate()))
                }.thenApply { SecurePendingTicket(it, ticket.slot, ticket.owner) }.whenComplete { _, _ -> payload.close(); stored.close() }
            } catch (failure: Exception) { stored.close(); throw failure }
        }.whenComplete { _, _ -> retained.close() }
    }
    public fun cancelUnsent(handle: DesktopPendingHandle, confirmed: Boolean): CompletionStage<Unit> = command {
        controlRequire(confirmed)
        secure.snapshot().thenCompose { snapshot ->
            controlRequire(snapshot.selected.any { it.slot == handle.slot && it.valueId == handle.valueId }, AccountControlFailure.CONTEXT_CHANGED)
            secure.read(handle.slot, snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                try {
                    controlRequire(stored.payload is PendingPayload && stored.owner.stage == SecureStage.PREPARED, AccountControlFailure.CONFLICT)
                    val owner = stored.owner.copy(boot = secure.boot, authorityRevision = snapshot.authorityRevision)
                    resumeProtected(snapshot, stored.slot, owner).thenCompose(custody::cancelBeforeDispatch).thenApply { Unit }
                } finally { stored.close() }
            }
        }
    }

    /** Discovery bytes are returned to a dedicated trust review, never accepted as transport trust. */
    public fun inspectDiscovery(confirmed: ConfirmedAccountTransport): CompletionStage<AccountControlResponse> =
        read(confirmed, AccountEndpoint.M5_DISCOVERY)

    public fun selectServer(confirmed: ConfirmedAccountTransport, rawDiscovery: ByteArray,
        instance: String, identityEpoch: Long, spki: ByteArray, identityConfirmed: Boolean,
        localDataChoice: AccountLocalDataChoice): CompletionStage<Unit> {
        val raw = rawDiscovery.copyOf(); val key = spki.copyOf()
        return command {
            val identity = DesktopAccountTrust.confirmIdentity(raw, confirmed, instance, identityEpoch, key, identityConfirmed, Instant.now())
            read(confirmed, AccountEndpoint.D1_DISCOVERY).thenApply { response ->
                response.use {
                    val bytes = it.bodyCopy()
                    val discovery = try { verified(D1.discovery(bytes, identity, Instant.now())) } finally { bytes.fill(0) }
                    controlRequire(D1.Platform.MACOS in discovery.platforms, AccountControlFailure.UNSUPPORTED)
                    val selection = AccountSelectedServer(confirmed, identity, discovery, localDataChoice)
                    binding?.let { owner -> requireTarget(owner, selection) }
                    selected = selection
                    mutable.value = mutable.value.copy(phase = if (mutable.value.pendingCount > 0) DesktopAccountPhase.OUTCOME_PENDING
                        else if (binding != null) DesktopAccountPhase.CONNECTED else DesktopAccountPhase.READY,
                        health = DesktopAccountHealth.AVAILABLE)
                    Unit
                }
            }
        }.whenComplete { _, _ -> raw.fill(0); key.fill(0) }
    }

    public fun createPortable(publicId: String, password: CharArray): CompletionStage<Unit> {
        val secret = password.copyOf()
        return command {
            val login = normalizeSocialPublicId(publicId) ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
            secure.snapshot().thenCompose { snapshot ->
                val created = PortableAccountCrypto.create(login, secret)
                val operation = UUID.randomUUID().toString()
                val owner = localOwner(snapshot, created.document.accountId, operation)
                roots.create(snapshot, owner, created, System.currentTimeMillis()).thenCompose { projection.activateLocalRoot() }.thenApply {
                    exportAcknowledged = false
                    mutable.value = mutable.value.copy(phase = DesktopAccountPhase.LOCAL, publicId = created.document.publicId,
                        recoveryExportRequired = true)
                    Unit
                }.whenComplete { _, _ -> created.close() }
            }
        }.whenComplete { _, _ -> secret.fill('\u0000') }
    }

    public fun importPortable(recovery: ByteArray): CompletionStage<Unit> {
        val bytes = recovery.copyOf()
        return command {
            PortableRecoveryFile.parse(bytes).use { parsed ->
                secure.snapshot().thenCompose { snapshot ->
                    roots.importRecovery(snapshot, localOwner(snapshot, parsed.document.accountId, UUID.randomUUID().toString()),
                        bytes, System.currentTimeMillis()).thenCompose { projection.activateLocalRoot() }.thenApply {
                        exportAcknowledged = false
                        mutable.value = mutable.value.copy(phase = DesktopAccountPhase.LOCAL, publicId = parsed.document.publicId,
                            recoveryExportRequired = true)
                        Unit
                    }
                }
            }
        }.whenComplete { _, _ -> bytes.fill(0) }
    }

    /** Secret result belongs only to an explicit secure picker/export surface, never saved UI state. */
    public fun activatePortable(password: CharArray): CompletionStage<Unit> {
        val owned = password.copyOf()
        return command { secure.snapshot().thenCompose { roots.unlock(it, owned) }.thenCompose { unlocked ->
            projection.activateLocalRoot().whenComplete { _, _ -> unlocked.close() }
        } }.whenComplete { _, _ -> owned.fill('\u0000') }
    }

    public fun recoveryForExport(): CompletionStage<SecretBytes> = secure.snapshot().thenCompose(roots::recoveryForExplicitExport)
    internal fun requireRecoveryExport() {
        exportAcknowledged = false
        mutable.value = mutable.value.copy(recoveryExportRequired = true)
    }
    public fun acknowledgeRecoveryExport(exactReadback: ByteArray): CompletionStage<Unit> {
        val bytes = exactReadback.copyOf()
        return command { secure.snapshot().thenCompose { roots.acknowledgeVerifiedRecoveryExport(it, bytes) }.thenApply {
            exportAcknowledged = true; mutable.value = mutable.value.copy(recoveryExportRequired = false); Unit
        } }.whenComplete { _, _ -> bytes.fill(0) }
    }

    /** REGISTER is explicit. Authentication failures never silently select registration or v1. */
    public fun connectPortable(password: CharArray, register: Boolean): CompletionStage<Unit> {
        val passwordCopy = password.copyOf()
        return command { connectPortableActual(passwordCopy, register, null) }
            .whenComplete { _, _ -> passwordCopy.fill('\u0000') }
    }

    /** Login uses the selected trusted server's passport and never requires a recovery archive. */
    public fun loginPortable(publicId: String, password: CharArray): CompletionStage<Unit> {
        val passwordCopy = password.copyOf()
        return command {
            val normalized = normalizeSocialPublicId(publicId)
                ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_FAILED)
            connectPortableActual(passwordCopy, false, normalized)
        }.whenComplete { _, _ -> passwordCopy.fill('\u0000') }
    }

    private fun connectPortableActual(password: CharArray, register: Boolean, requestedId: String?): CompletableFuture<Unit> {
        val server = requireServer(D1.Family.PORTABLE)
        return secure.snapshot().thenCompose { snapshot ->
            controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
            secure.read(requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved), snapshot).thenCompose { record ->
                try {
                    val local = record?.let {
                        val root = it.payload as? PortableRootPayload ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                        val bytes = root.passportCopy()
                        try { PortableAccountDocument.parse(bytes) } finally { bytes.fill(0) }
                    }
                    controlRequire(!register || local != null, AccountControlFailure.AUTHENTICATION_REQUIRED)
                    val publicId = requestedId ?: local?.publicId ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
                    controlRequire(local == null || local.publicId == publicId, AccountControlFailure.CONFLICT)
                    val challengeBody = SecureJson.encode(buildJsonObject {
                        put("contract_version", "v2"); put("schema_version", 2); put("platform", "MACOS")
                        if (register) put("account", requireNotNull(local).fields) else put("public_id", publicId)
                    })
                    val challenge = if (register) AccountEndpoint.PORTABLE_REGISTER_CHALLENGE else AccountEndpoint.PORTABLE_LOGIN_CHALLENGE
                    val operation = UUID.randomUUID().toString(); val profile = UUID.randomUUID().toString()
                    val reservation = CompletionReservation(operation,
                        listOf(requireNotNull(SecureRecordKind.PORTABLE_COMMAND.reserved), profile, requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)),
                        4, 4L * SecureStateLimits.VALUE)
                    read(server.transport, challenge, challengeBody).exceptionallyCompose { failure ->
                        val code = safeFailure(failure)
                        CompletableFuture.failedFuture(AccountControlException(if (!register && code == AccountControlFailure.AUTHENTICATION_REQUIRED)
                            AccountControlFailure.AUTHENTICATION_FAILED else code))
                    }.thenCompose { response ->
                        val rawChallenge = response.bodyCopy(); response.close()
                        try {
                            val document = if (register) requireNotNull(local) else PortableDeviceContractV2.loginDocument(
                                rawChallenge, server.identity, PortableDeviceContractV2.Platform.MACOS, publicId, Instant.now())
                            controlRequire(local == null || local.fields == document.fields, AccountControlFailure.CONFLICT)
                            val adopted = if (local == null) roots.adoptFromLogin(snapshot,
                                localOwner(snapshot, document.accountId, UUID.randomUUID().toString()), document, password, System.currentTimeMillis())
                                .thenCompose { projection.activateLocalRoot() }.thenCompose { secure.snapshot() }.thenApply {
                                    mutable.value = mutable.value.copy(publicId = document.publicId, recoveryExportRequired = false)
                                    it
                                } else CompletableFuture.completedFuture(snapshot)
                            adopted.thenCompose { current ->
                                // Authenticate before reserving a binding or creating its device key.
                                roots.unlock(current, password).thenCompose { unlocked ->
                                    secure.reserveCompletion(current, reservation, FirstBindOwner(operation, operation, SecureRole.RECIPIENT)).thenCompose { reserved ->
                                        secure.createNewKey(reserved, operation).thenCompose { key ->
                                            secure.snapshot().thenCompose { preparedControl ->
                                                secure.publicKey(preparedControl, key.tag).thenCompose { spki ->
                                                    val successor = freshSecret(32)
                                                    try {
                                                        val prepared = PortableDeviceContractV2.prepare(rawChallenge, server.identity,
                                                            PortableDeviceContractV2.Platform.MACOS,
                                                            if (register) PortableDeviceContractV2.Purpose.REGISTER else PortableDeviceContractV2.Purpose.LOGIN,
                                                            document, spki, deviceName, appVersion, secureSha256(successor), operation, Instant.now())
                                                        val signed = try { unlocked.sign(prepared) } finally { prepared.close() }
                                                        val request = signed.transientWireCopy()
                                                        val expiry = try { Instant.parse(((SecureJson.parse(request, 16_384)["request"] as JsonObject)["challenge"] as JsonObject).string("expires_at")).epochSecond }
                                                            finally { request.fill(0) }
                                                        val owner = pendingOwner(preparedControl, server, document.accountId, profile, operation, key, expiry)
                                                        try { custody.preparePortable(preparedControl, owner, signed, !register, successor) }
                                                        finally { signed.close() }
                                                    } finally { successor.fill(0); spki.fill(0) }
                                                }
                                            }
                                        }
                                    }.whenComplete { _, _ -> unlocked.close() }
                                }
                            }.whenComplete { _, _ -> rawChallenge.fill(0) }
                        } catch (failure: Exception) { rawChallenge.fill(0); throw failure }
                    }.thenCompose { ticket -> executeSession(ticket, if (register) AccountEndpoint.PORTABLE_REGISTER else AccountEndpoint.PORTABLE_LOGIN) }
                        .whenComplete { _, _ -> challengeBody.fill(0); record?.close() }
                } catch (failure: Exception) { record?.close(); throw failure }
            }
        }
    }

    /** Exact K original is resumed before dispatch; no nonce, signature or successor is recreated. */
    public fun resumePending(slot: String): CompletionStage<Unit> = command {
        canonicalUuid(slot)
        secure.snapshot().thenCompose { snapshot ->
            secure.read(slot, snapshot).thenCompose { record ->
                val stored = record ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                try {
                    val pending = stored.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                    val server = requireNotNull(selected)
                    requireTarget(stored.owner, server)
                    val owner = stored.owner.copy(boot = secure.boot, authorityRevision = snapshot.authorityRevision)
                    controlRequire(pending.wireKind in sessionOperations || pending.wireKind == PendingWireKind.DELETION_REQUEST && owner.role == SecureRole.SOURCE,
                        AccountControlFailure.UNSUPPORTED)
                    val endpoint = replayEndpoint(pending.wireKind)
                    resumeProtected(snapshot, slot, owner).thenCompose { ticket ->
                        if (pending.wireKind in sessionOperations) executeSession(ticket, endpoint)
                        else executePending(ticket, endpoint).thenCompose { result ->
                            val bytes = result.response.copy()
                            try {
                                if (pending.operation == PendingOperation.DELETION && owner.role == SecureRole.SOURCE)
                                    recordDeletionOutcome(result.ticket, bytes).thenApply { Unit }
                                else throw AccountControlException(AccountControlFailure.CONFLICT)
                            } finally { bytes.fill(0); result.close() }
                        }
                    }
                } finally { stored.close() }
            }
        }
    }

    public fun connectionHealth(): CompletionStage<Unit> = command {
        val server = requireNotNull(selected)
        read(server.transport, AccountEndpoint.D1_DISCOVERY).thenApply { response -> response.use {
            val raw = it.bodyCopy()
            try { verified(D1.discovery(raw, server.identity, Instant.now())) } finally { raw.fill(0) }
            mutable.value = mutable.value.copy(health = DesktopAccountHealth.AVAILABLE); Unit
        } }
    }

    /** Revalidate the retained exact actor and real J marker before exposing an old-boot binding. */
    public fun restoreBinding(profile: String): CompletionStage<Unit> = command { restoreBindingActual(profile) }

    private fun restoreBindingActual(profile: String): CompletableFuture<Unit> {
        canonicalUuid(profile)
        val server = requireNotNull(selected)
        return secure.snapshot().thenCompose { snapshot -> secure.read(profile, snapshot).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
            val session = stored.payload as? SessionPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val original = stored.owner
            requireTarget(original, server)
            controlRequire(!original.detached && !original.deletionVeto && session.hasCredentials &&
                ((original.stage == SecureStage.ACTIVE && (snapshot.activeBinding == profile || snapshot.activeBinding == null &&
                    original.accountProjection?.stage == app.autplay.application.desktop.account.AccountProtectedStage.READY)) ||
                    original.stage == SecureStage.BINDING_COMMIT_PENDING),
                AccountControlFailure.AUTHENTICATION_REQUIRED)
            controlRequire(session.refreshExpiresAt > Instant.now().epochSecond, AccountControlFailure.AUTHENTICATION_REQUIRED)
            val policy = session.transportPolicyCopy()
            try { controlRequire(server.transport.matches(SecureMediaGuardCodec.readTransport(policy)), AccountControlFailure.TRUST_REQUIRED) }
            finally { policy.fill(0) }
            val access = session.accessCopy()
            secure.publicKey(snapshot, requireNotNull(original.keyTag)).thenCompose { publicKey ->
            try { controlRequire(secureSha256(publicKey) == original.keyThumbprint, AccountControlFailure.AUTHENTICATION_REQUIRED) }
            finally { publicKey.fill(0) }
            val reconciliation = if (session.accessExpiresAt <= Instant.now().epochSecond) {
                projection.prepareExpiredBinding(original).thenCompose { secure.snapshot() }.thenCompose { current ->
                    DesktopProfileController(this).prepareRenewal(current, profile, reconciledPending = true)
                }.thenCompose { executeSession(it, AccountEndpoint.ROTATE) }.thenCompose { secure.snapshot() }
            } else custody.resumeBindingAfterRevalidation(snapshot, original) { expected ->
                requireTarget(expected, server)
                read(server.transport, AccountEndpoint.D1_CAPABILITIES, access = access).thenCompose { d1Response ->
                    read(server.transport, AccountEndpoint.M5_CAPABILITIES, access = access).thenCompose { m5Response ->
                        try {
                            val d1Bytes = d1Response.bodyCopy(); val m5Bytes = m5Response.bodyCopy()
                            val d1 = try { verified(D1.capabilities(d1Bytes, server.identity,
                                D1.CapabilityScope(requireNotNull(expected.account), requireNotNull(expected.device), D1.Platform.MACOS), null, Instant.now())) }
                                finally { d1Bytes.fill(0) }
                            val digest = d1.nextObservation.effectivePayloadSha256
                            val guard = try { CapabilityGuard(d1.nextObservation.revision, digest.joinToString("") { "%02x".format(it.toInt() and 255) },
                                if (session.d1.revision == d1.nextObservation.revision) session.d1.b1ContentSha256 else null) }
                                finally { digest.fill(0) }
                            val m5 = try { DesktopAccountTrust.capabilities(m5Bytes, server.identity, requireNotNull(expected.account),
                                requireNotNull(expected.device), session.m5, Instant.now()) } finally { m5Bytes.fill(0) }
                            controlRequire(m5.requiredFeatures.isEmpty() && session.d1.accepts(guard), AccountControlFailure.CONTEXT_CHANGED)
                            if (guard == session.d1 && m5.guard == session.m5) CompletableFuture.completedFuture(true)
                            else publishRevalidatedGuards(snapshot, original, session, guard, m5.guard).thenApply { true }
                        } finally { d1Response.close(); m5Response.close() }
                    }.whenComplete { _, failure -> if (failure != null) d1Response.close() }
                }
            }
            reconciliation.thenCompose { current ->
                secure.read(profile, current).thenApply { selected ->
                    try {
                    val active = selected ?: throw AccountControlException(AccountControlFailure.BINDING_RECONCILIATION_REQUIRED)
                    controlRequire(active.payload is SessionPayload && current.activeBinding == profile && active.owner.stage == SecureStage.ACTIVE)
                    binding = active.owner
                    mutable.value = mutable.value.copy(phase = if (mutable.value.pendingCount > 0) DesktopAccountPhase.OUTCOME_PENDING else DesktopAccountPhase.CONNECTED,
                        health = DesktopAccountHealth.AVAILABLE)
                    Unit
                    } finally { selected?.close() }
                }
            } }.whenComplete { _, _ -> access.fill(0); stored.close() }
        } }
    }

    /** Only the exact fresh D1/M5 reads above call this; the selected policy stays byte-identical. */
    private fun publishRevalidatedGuards(snapshot: SecureControl, owner: SecureRecordOwner, payload: SessionPayload,
        d1: CapabilityGuard, m5: M5CapabilityGuard): CompletableFuture<SecureControl> {
        val operation = UUID.randomUUID().toString()
        val completion = snapshot.reservations.singleOrNull { it.operation == owner.operation && owner.profile in it.slots }
        val reserve = if (completion != null) CompletableFuture.completedFuture(snapshot) else secure.reserveCompletion(snapshot,
            CompletionReservation(operation, listOf(requireNotNull(owner.profile)), 1, SecureStateLimits.VALUE.toLong()))
        return reserve.thenCompose { current ->
            secure.publish(current, completion?.operation ?: operation, activeBinding = null, terminal = completion == null) {
                val transport = payload.transportPolicyCopy()
                try { listOf(SecureRecord(requireNotNull(owner.profile), SecureStateLimits.next(current.selected.single { it.slot == owner.profile }.slotRevision),
                    UUID.randomUUID().toString(), owner.copy(boot = secure.boot, authorityRevision = current.authorityRevision,
                        stage = SecureStage.BINDING_COMMIT_PENDING, accountProjection = owner.accountProjection?.copy(lifecycleAllowed = false)),
                    payload.withConnectionGuards(d1, m5, transport))) }
                finally { transport.fill(0) }
            }
        }
    }

    private fun finishLocalBindingMarker(snapshot: SecureControl, active: SecureRecordOwner): CompletableFuture<Unit> {
        val candidates = snapshot.selected.filter { it.slot != active.profile }.map { it.slot }
        fun next(index: Int): CompletableFuture<Unit> {
            if (index == candidates.size) return CompletableFuture.completedFuture(Unit)
            return secure.read(candidates[index], snapshot).thenCompose { stored ->
                if (stored == null) next(index + 1)
                else try {
                    val pending = stored.payload as? PendingPayload
                    if (pending == null || stored.owner.stage != SecureStage.BINDING_COMMIT_PENDING ||
                        stored.owner.profile != active.profile || stored.owner.operation != active.operation) next(index + 1)
                    else {
                        val receipt = pending.receiptCopy()
                        val value = try { SecureJson.parse(receipt, 65_536, canonical = false) } catch (failure: Exception) { receipt.fill(0); throw failure }
                        controlRequire(value.string("session_id") == active.session && value.string("platform") == "MACOS")
                        val owner = stored.owner.copy(boot = secure.boot, authorityRevision = snapshot.authorityRevision)
                        custody.durableBinding(snapshot, active).thenCompose { matched ->
                            controlRequire(matched, AccountControlFailure.CONFLICT)
                            resumeProtected(snapshot, stored.slot, owner)
                        }.thenCompose { ticket -> recordTerminal(ticket, receipt, { _, raw ->
                            SecureJson.parse(raw, 65_536, canonical = false).string("session_id") == active.session
                        }, false) }.thenApply { Unit }.whenComplete { _, _ -> receipt.fill(0) }
                    }
                } finally { stored.close() }
            }
        }
        return next(0)
    }

    /** Close admission before host switch/sleep. Durable pending evidence is deliberately retained. */
    public fun retire() {
        retired.set(true); epoch.incrementAndGet(); transport.retire(); binding = null
        mutable.value = mutable.value.copy(phase = DesktopAccountPhase.RETIRED, health = DesktopAccountHealth.CLOSED)
    }
    // K is shared with J/media. Its host must close J and release its canonical lifetime before K.close/drainSettled.
    override fun close() { if (closed.compareAndSet(false, true)) { retire(); transport.close() } }
    public fun drain(): CompletionStage<Unit> {
        controlRequire(retired.get() || closed.get(), AccountControlFailure.BUSY)
        return CompletableFuture.allOf(*(retainedCommands.values.toList() + transport.drain().toCompletableFuture()).toTypedArray()).thenApply { Unit }
    }
    public fun outstandingIo(): List<AccountIoTicket> = retainedIo.values.toList()

    internal fun requireServer(family: D1.Family): AccountSelectedServer {
        val server = selected ?: throw AccountControlException(AccountControlFailure.TRUST_REQUIRED)
        val discovery = verified(D1.discovery(server.discovery.canonicalEnvelope, server.identity, Instant.now()))
        controlRequire(discovery.advertises(D1.Platform.MACOS, family), AccountControlFailure.UNSUPPORTED)
        return server
    }
    internal fun requireTarget(owner: SecureRecordOwner, server: AccountSelectedServer) {
        controlRequire(owner.server == server.identity.serverInstanceId && owner.identityEpoch == server.identity.identityEpoch &&
            owner.identityThumbprint == server.identity.thumbprintSha256 && owner.apiOrigin == server.identity.apiOrigin &&
            owner.streamOrigin == server.identity.streamOrigin && owner.platform == "MACOS" && owner.protocolVersion in 1..2,
            AccountControlFailure.CONTEXT_CHANGED)
    }
    internal fun <T> command(block: () -> CompletableFuture<T>): CompletableFuture<T> {
        if (closed.get() || retired.get()) return failed(AccountControlFailure.CLOSED)
        if (!busy.compareAndSet(false, true)) return failed(AccountControlFailure.BUSY)
        val captured = epoch.get()
        mutable.value = mutable.value.copy(busy = true, failure = null)
        val actual = try { block() } catch (failure: Exception) { CompletableFuture.failedFuture(failure) }
        val commandId = UUID.randomUUID()
        retainedCommands[commandId] = actual
        val observed = CompletableFuture<T>()
        actual.whenComplete { value, failure ->
            retainedCommands.remove(commandId)
            busy.set(false)
            if (captured == epoch.get()) {
                val code = failure?.let(::safeFailure)
                mutable.value = mutable.value.copy(busy = false, failure = code,
                    phase = if (code != null && mutable.value.pendingCount > 0) DesktopAccountPhase.OUTCOME_PENDING else mutable.value.phase,
                    health = if (code == AccountControlFailure.AUTHENTICATION_REQUIRED) DesktopAccountHealth.AUTH_REQUIRED else mutable.value.health)
            } else {
                binding = null
                mutable.value = mutable.value.copy(busy = false, phase = DesktopAccountPhase.RETIRED, health = DesktopAccountHealth.CLOSED)
            }
            if (failure != null) observed.completeExceptionally(AccountControlException(safeFailure(failure)))
            else if (captured != epoch.get()) observed.completeExceptionally(AccountControlException(AccountControlFailure.CONTEXT_CHANGED))
            else observed.complete(value)
        }
        return observed
    }

    internal fun read(trust: ConfirmedAccountTransport, endpoint: AccountEndpoint, body: ByteArray = byteArrayOf(),
        access: ByteArray = byteArrayOf(), secret: ByteArray = byteArrayOf(), id: String? = null): CompletableFuture<AccountControlResponse> {
        val ticket = transport.submit(trust, endpoint, body, access, secret, id)
        retainedIo[ticket.id] = ticket
        val actual = CompletableFuture<AccountControlResponse>()
        ticket.observe().thenAccept { outcome -> if (outcome is AccountIoOutcome.Failed && outcome.code == AccountControlFailure.DEADLINE)
            mutable.value = mutable.value.copy(health = DesktopAccountHealth.PENDING, failure = AccountControlFailure.DEADLINE) }
        ticket.settled().whenComplete { outcome, failure ->
            retainedIo.remove(ticket.id)
            when {
                failure != null -> actual.completeExceptionally(AccountControlException(AccountControlFailure.TRANSPORT_UNAVAILABLE))
                outcome is AccountIoOutcome.Completed -> actual.complete(outcome.response)
                outcome is AccountIoOutcome.Failed -> actual.completeExceptionally(AccountControlException(outcome.code))
                else -> actual.completeExceptionally(AccountControlException(AccountControlFailure.TRANSPORT_UNAVAILABLE))
            }
        }
        return actual
    }

    private fun captureCommandContext(ticket: SecurePendingTicket): CompletableFuture<SecurePendingTicket> =
        secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val pending = stored.payload as? PendingPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val authorization = pending.authorizationCopy()
            try {
                val server = requireNotNull(selected)
                if (AccountCommandContextCodec.isContext(authorization)) {
                    AccountCommandContextCodec.read(authorization).use { context ->
                        controlRequire(server.transport.matches(context.policy), AccountControlFailure.TRUST_REQUIRED)
                    }
                    stored.close(); CompletableFuture.completedFuture(ticket)
                } else {
                    controlRequire(stored.owner == ticket.owner && stored.owner.stage == SecureStage.PREPARED, AccountControlFailure.CONFLICT)
                    val selection = AccountCommandContextCodec.selection(server.choice)
                    val wrapped = try { AccountCommandContextCodec.encode(authorization, server.transport.protectedPolicy(1), selection) }
                        finally { selection.fill(0) }
                    val prepared = try { app.autplay.application.desktop.accountflow.DesktopPendingCeremonyMaterial.withAuthorization(pending, wrapped) }
                        finally { wrapped.fill(0) }
                    secure.publish(ticket.control, requireNotNull(stored.owner.operation)) {
                        listOf(SecureRecord(stored.slot, SecureStateLimits.next(stored.revision), UUID.randomUUID().toString(), stored.owner, prepared.duplicate()))
                    }.thenApply { SecurePendingTicket(it, stored.slot, stored.owner) }.whenComplete { _, _ -> prepared.close(); stored.close() }
                }
            } catch (failure: Exception) { stored.close(); throw failure }
            finally { authorization.fill(0) }
        }

    internal fun executePending(ticket: SecurePendingTicket, endpoint: AccountEndpoint): CompletableFuture<SecureDispatchResult> =
        captureCommandContext(ticket).thenCompose { dispatchFrozen(it, endpoint) }

    private fun dispatchFrozen(ticket: SecurePendingTicket, endpoint: AccountEndpoint): CompletableFuture<SecureDispatchResult> {
        val server = requireNotNull(selected); requireTarget(ticket.owner, server)
        mutable.value = mutable.value.copy(phase = DesktopAccountPhase.OUTCOME_PENDING, pendingCount = maxOf(1, mutable.value.pendingCount))
        val outcome = endpoint in setOf(AccountEndpoint.RECOVERY_OUTCOME, AccountEndpoint.DELETION_OUTCOME)
        val headerOwner = if (outcome) secure.read(ticket.slot, ticket.control).thenApply { stored ->
            try { (stored?.payload as? PendingPayload)?.successorCopy() ?: throw AccountControlException(AccountControlFailure.CONFLICT) }
            finally { stored?.close() }
        } else CompletableFuture.completedFuture(byteArrayOf())
        return headerOwner.thenCompose { outcomeHeader ->
        val send: (SecureReplayMaterial) -> CompletableFuture<ByteArray> = { material ->
            controlRequire(endpointAllowed(material.wireKind, endpoint), AccountControlFailure.CONTEXT_CHANGED)
            val wire = material.request.copy(); val contextBytes = material.authorization.copy()
            val context = try { AccountCommandContextCodec.read(contextBytes) } finally { contextBytes.fill(0) }
            controlRequire(server.transport.matches(context.policy), AccountControlFailure.TRUST_REQUIRED)
            val authorization = context.authorization.copy(); context.close()
            val original = SecureJson.parse(wire, 32_768, canonical = false)
            val pathId = when (endpoint) {
                AccountEndpoint.SELF_CLAIM, AccountEndpoint.SELF_POLL, AccountEndpoint.SELF_EXCHANGE, AccountEndpoint.SELF_DECISION -> original.string("ceremony_id")
                AccountEndpoint.ADMISSION_POLL, AccountEndpoint.ADMISSION_RECOVER, AccountEndpoint.ADMISSION_EXCHANGE -> original.string("request_id")
                AccountEndpoint.REVOKE_DEVICE -> ticket.owner.claim
                AccountEndpoint.INVITATION_CANCEL -> ticket.owner.claim
                else -> null
            }
            val actualAuthorization = when {
                SelfSecretEnvelope.isEnvelope(authorization) -> SelfSecretEnvelope.read(authorization).use { it.header.copy() }
                AdmissionSecretEnvelope.isEnvelope(authorization) -> AdmissionSecretEnvelope.read(authorization).let { fields ->
                    try { fields.first.copy() } finally { fields.first.close(); fields.second.close() }
                }
                endpoint == AccountEndpoint.RECOVERY_CONFIGURE -> RecoverySecretEnvelope.header(authorization)
                DeletionSecretEnvelope.isEnvelope(authorization) -> DeletionSecretEnvelope.read(authorization).let { fields ->
                    try { fields.first.copy() } finally { fields.first.close(); fields.second.close() }
                }
                else -> authorization.copyOf()
            }
            val secret = if (outcome) outcomeHeader.copyOf()
                else if (DeletionSecretEnvelope.isEnvelope(authorization)) DeletionSecretEnvelope.read(authorization).let { fields ->
                    try { fields.second.copy() } finally { fields.first.close(); fields.second.close() }
                }
                else if (endpoint.secretHeader == "X-AutPlay-Recovery-Code") RecoverySecretEnvelope.code(authorization)
                else actualAuthorization.copyOf()
            try {
                read(server.transport, endpoint, wire,
                    if (endpoint.bearer) actualAuthorization else byteArrayOf(),
                    if (endpoint.secretHeader != null) secret else byteArrayOf(), pathId).thenApply { response ->
                    response.use { it.bodyCopy() }
                }
            } finally { wire.fill(0); authorization.fill(0); actualAuthorization.fill(0); secret.fill(0) }
        }
        val historical = ticket.owner.stage != SecureStage.PREPARED || endpoint in setOf(AccountEndpoint.RECOVERY_OUTCOME,
            AccountEndpoint.DELETION_OUTCOME, AccountEndpoint.DELETION_RECEIPT, AccountEndpoint.DELETION_RESOLVE)
        val effect = custody.dispatch(ticket, if (historical) SecurePendingDispatchMode.HISTORICAL_OUTCOME else SecurePendingDispatchMode.ORIGINAL, send)
        effect.whenComplete { _, _ -> outcomeHeader.fill(0) }
        }
    }

    internal fun recordTerminal(ticket: SecurePendingTicket, receipt: ByteArray,
        verify: (SecureRecordOwner, ByteArray) -> Boolean, inactive: Boolean): CompletableFuture<SecureControl> {
        val owned = receipt.copyOf()
        return custody.recordTerminal(ticket, owned, verify, inactive).thenApply { control ->
            if (inactive && binding?.profile == ticket.owner.profile) binding = null
            val remaining = (mutable.value.pendingCount - 1).coerceAtLeast(0)
            mutable.value = mutable.value.copy(pendingCount = remaining,
                phase = if (remaining > 0) DesktopAccountPhase.OUTCOME_PENDING else if (binding != null) DesktopAccountPhase.CONNECTED else DesktopAccountPhase.LOCAL,
                health = if (remaining > 0) DesktopAccountHealth.PENDING else DesktopAccountHealth.UNKNOWN)
            control
        }.whenComplete { _, _ -> owned.fill(0) }
    }
    internal fun recordDeletionOutcome(ticket: SecurePendingTicket, receipt: ByteArray): CompletableFuture<SecurePendingTicket> {
        val owned = receipt.copyOf()
        return custody.recordDeletionOutcome(ticket, owned).thenApply { resolved ->
            binding = null
            val remaining = if (resolved.owner.stage == SecureStage.TERMINAL) (mutable.value.pendingCount - 1).coerceAtLeast(0) else maxOf(1, mutable.value.pendingCount)
            mutable.value = mutable.value.copy(pendingCount = remaining,
                phase = if (remaining > 0) DesktopAccountPhase.OUTCOME_PENDING else DesktopAccountPhase.LOCAL,
                health = if (remaining > 0) DesktopAccountHealth.PENDING else DesktopAccountHealth.AUTH_REQUIRED)
            resolved
        }.whenComplete { _, _ -> owned.fill(0) }
    }

    /** Conservative history is durable before every grant POST, including a handoff from SELF. */
    private fun markRootBeforeGrant(ticket: SecurePendingTicket): CompletableFuture<SecurePendingTicket> {
        val slot = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)
        return secure.read(slot, ticket.control).thenCompose { nullable ->
            if (nullable == null) CompletableFuture.completedFuture(ticket)
            else {
                val root = nullable.payload as? PortableRootPayload ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                controlRequire(nullable.owner.account == ticket.owner.account, AccountControlFailure.CONTEXT_CHANGED)
                if (root.everEnrolled) { nullable.close(); CompletableFuture.completedFuture(ticket) }
                else {
                    val operation = UUID.randomUUID().toString()
                    secure.reserveCompletion(ticket.control, CompletionReservation(operation, listOf(slot), 1, SecureStateLimits.VALUE.toLong())).thenCompose { reserved ->
                        secure.publish(reserved, operation, terminal = true) {
                            val passport = root.passportCopy(); val recovery = root.recoveryCopy(); val history = root.lineageCopy(); val local = root.localGuardCopy()
                            try {
                                val lineage = SecureJson.encode(JsonObject(SecureJson.parse(history, 49_152) + ("ever_enrolled" to JsonPrimitive(true))))
                                try { listOf(SecureRecord(slot, SecureStateLimits.next(nullable.revision), UUID.randomUUID().toString(),
                                    nullable.owner.copy(boot = secure.boot, authorityRevision = reserved.authorityRevision),
                                    PortableRootPayload(passport, recovery, lineage, root.ownershipCutoffMs, true, local))) }
                                finally { lineage.fill(0) }
                            } finally { passport.fill(0); recovery.fill(0); history.fill(0); local.fill(0) }
                        }
                    }.thenCompose { current -> resumeProtected(current, ticket.slot, ticket.owner) }.whenComplete { _, _ -> nullable.close() }
                }
            }
        }
    }
    internal fun executeSession(ticket: SecurePendingTicket, endpoint: AccountEndpoint): CompletableFuture<Unit> =
        committedBeforeReplay(ticket).thenCompose { complete -> if (complete) CompletableFuture.completedFuture(Unit) else sendSessionAndCommit(ticket, endpoint) }

    private fun committedBeforeReplay(ticket: SecurePendingTicket): CompletableFuture<Boolean> =
        secure.read(requireNotNull(ticket.owner.profile), ticket.control).thenCompose { record ->
            if (record == null) CompletableFuture.completedFuture(false)
            else try {
                if (record.payload !is SessionPayload || record.owner.operation != ticket.owner.operation) CompletableFuture.completedFuture(false)
                else {
                    controlRequire(record.owner.account == ticket.owner.account && record.owner.bindingCommit == ticket.owner.bindingCommit &&
                        record.owner.keyThumbprint == ticket.owner.keyThumbprint, AccountControlFailure.CONTEXT_CHANGED)
                    if (record.owner.stage == SecureStage.BINDING_COMMIT_PENDING || record.owner.boot != secure.boot)
                        restoreBindingActual(record.slot).thenApply { true }
                    else custody.durableBinding(ticket.control, record.owner).thenCompose { matched ->
                        controlRequire(matched, AccountControlFailure.BINDING_RECONCILIATION_REQUIRED)
                        finishLocalBindingMarker(ticket.control, record.owner).thenApply {
                            binding = record.owner; mutable.value = mutable.value.copy(phase = DesktopAccountPhase.CONNECTED, health = DesktopAccountHealth.AVAILABLE); true
                        }
                    }
                }
            } finally { record.close() }
        }

    private fun sendSessionAndCommit(ticket: SecurePendingTicket, endpoint: AccountEndpoint): CompletableFuture<Unit> =
        markRootBeforeGrant(ticket).thenCompose { executePending(it, endpoint) }.thenCompose { result ->
            val server = requireNotNull(selected)
            val response = result.response.copy()
            val doc = try { SecureJson.parse(response, 65_536, canonical = false) } finally { response.fill(0) }
            val access = doc.string("access_token").toByteArray(Charsets.US_ASCII)
            val account = ticket.owner.account ?: doc.string("user_id")
            val device = if (endpoint == AccountEndpoint.ROTATE) requireNotNull(ticket.owner.device) else doc.string("device_id")
            captureSessionGuards(result.ticket).thenCompose { prior -> read(server.transport, AccountEndpoint.D1_CAPABILITIES, access = access).thenCompose { d1Response ->
                read(server.transport, AccountEndpoint.M5_CAPABILITIES, access = access).thenCompose { m5Response ->
                    try {
                        val d1raw = d1Response.bodyCopy(); val m5raw = m5Response.bodyCopy()
                        val transition = try { verified(D1.capabilities(d1raw, server.identity,
                            D1.CapabilityScope(account, device, D1.Platform.MACOS), null, Instant.now())) } finally { d1raw.fill(0) }
                        val effective = transition.nextObservation.effectivePayloadSha256
                        val d1 = try { CapabilityGuard(transition.nextObservation.revision, effective.joinToString("") { "%02x".format(it.toInt() and 255) },
                            if (prior.d1?.revision == transition.nextObservation.revision) prior.d1?.b1ContentSha256 else null) } finally { effective.fill(0) }
                        controlRequire(prior.d1 == null || prior.d1.accepts(d1), AccountControlFailure.CONTEXT_CHANGED)
                        val m5 = try { DesktopAccountTrust.capabilities(m5raw, server.identity, account, device, prior.m5, Instant.now()) } finally { m5raw.fill(0) }
                        controlRequire(m5.requiredFeatures.isEmpty(), AccountControlFailure.UNSUPPORTED)
                        secure.read(result.ticket.slot, result.ticket.control).thenCompose { pending ->
                            val stored = pending ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                            val exact = result.response.copy()
                            try {
                                val policy = prior.transport.copyOf()
                                val high = SecureMediaGuardCodec.m5(m5.guard)
                                try {
                                    val verified = SecureSessionResponseCodec.verify(exact, stored, Instant.now(), "no-store", "no-cache",
                                        d1, m5.guard, prior.local, policy, high)
                                    val payload = verified.payloadCopy()
                                    val materialized = try { app.autplay.application.desktop.accountflow.DesktopPendingCeremonyMaterial.preserveMaterialization(payload, prior.materialization) }
                                        finally { payload.close() }
                                    val grant = try { VerifiedSecureSession(verified.pendingOwner, verified.owner, materialized, exact) }
                                        finally { verified.close() }
                                    custody.commitSession(result.ticket, grant).thenCompose { current ->
                                    secure.read(requireNotNull(current.activeBinding), current).thenApply { selected ->
                                        try {
                                        val active = selected ?: throw AccountControlException(AccountControlFailure.BINDING_RECONCILIATION_REQUIRED)
                                        controlRequire(active.payload is SessionPayload && active.owner.stage == SecureStage.ACTIVE && active.owner.accountProjection != null)
                                        binding = active.owner
                                        mutable.value = mutable.value.copy(phase = DesktopAccountPhase.CONNECTED, pendingCount = (mutable.value.pendingCount - 1).coerceAtLeast(0),
                                            health = DesktopAccountHealth.AVAILABLE)
                                        Unit
                                        } finally { selected?.close() }
                                    }
                                    }
                                } finally { policy.fill(0); high.fill(0) }
                            } finally { exact.fill(0); stored.close() }
                        }
                    } finally { d1Response.close(); m5Response.close() }
                }
            }.whenComplete { _, _ -> prior.close() } }.whenComplete { _, _ -> access.fill(0); result.close() }
        }

    private fun captureSessionGuards(ticket: SecurePendingTicket): CompletableFuture<AccountPriorSession> =
        secure.read(ticket.slot, ticket.control).thenCompose { nullable ->
            val pending = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
            val bytes = (pending.payload as PendingPayload).authorizationCopy()
            val command = try { AccountCommandContextCodec.read(bytes) } finally { bytes.fill(0); pending.close() }
            val selected = command.materialization.copy()
            val policy = SecureMediaGuardCodec.transport(command.policy)
            command.close()
            secure.read(requireNotNull(ticket.owner.profile), ticket.control).thenApply { old ->
                try {
                    val value = old?.payload as? SessionPayload
                    if (value == null) AccountPriorSession(null, null, byteArrayOf(), policy.copyOf(), selected.copyOf())
                    else {
                        requireTarget(requireNotNull(old).owner, requireNotNull(this.selected))
                        val existing = value.transportPolicyCopy()
                        controlRequire(requireNotNull(this.selected).transport.matches(SecureMediaGuardCodec.readTransport(existing)), AccountControlFailure.TRUST_REQUIRED)
                        val materialization = app.autplay.application.desktop.accountflow.DesktopPendingCeremonyMaterial.sessionMaterialization(value)
                        val retained = materialization.use { it.copy() }
                        AccountPriorSession(value.d1, value.m5, value.localGuardCopy(), existing,
                            if (retained.isEmpty()) selected.copyOf().also { retained.fill(0) } else retained)
                    }
                } finally { old?.close(); policy.fill(0); selected.fill(0) }
            }
        }

    internal fun localOwner(snapshot: SecureControl, account: String, operation: String): SecureRecordOwner = SecureRecordOwner(
        secure.boot, snapshot.authorityRevision, null, account, null, null, null, null, null,
        device = null, keyTag = null, keyThumbprint = null, bindingCommit = null, session = null, family = null,
        sessionGeneration = null, role = SecureRole.LOCAL, ceremony = operation, operation = operation, claim = null,
        expiresAtEpochSeconds = null, stage = SecureStage.RECORDED)

    internal fun pendingOwner(snapshot: SecureControl, server: AccountSelectedServer, account: String?, profile: String,
        operation: String, key: DeviceKeyIntent, expiry: Long?, role: SecureRole = SecureRole.RECIPIENT,
        ceremony: String = operation, claim: String? = null): SecureRecordOwner = SecureRecordOwner(
        secure.boot, snapshot.authorityRevision, profile, account, server.identity.serverInstanceId, server.identity.identityEpoch,
        server.identity.thumbprintSha256, server.identity.apiOrigin, server.identity.streamOrigin,
        device = null, keyTag = key.tag, keyThumbprint = key.thumbprint, bindingCommit = operation, session = null, family = null,
        sessionGeneration = null, role = role, ceremony = ceremony, operation = operation, claim = claim,
        expiresAtEpochSeconds = expiry, stage = SecureStage.PREPARED)

    private fun replayEndpoint(kind: PendingWireKind): AccountEndpoint = when (kind) {
        PendingWireKind.PORTABLE_REGISTER -> AccountEndpoint.PORTABLE_REGISTER
        PendingWireKind.PORTABLE_LOGIN -> AccountEndpoint.PORTABLE_LOGIN
        PendingWireKind.SESSION_ROTATE -> AccountEndpoint.ROTATE
        PendingWireKind.ENROLLMENT_EXCHANGE -> AccountEndpoint.ENROLL
        PendingWireKind.ADMISSION_EXCHANGE -> AccountEndpoint.ADMISSION_EXCHANGE
        PendingWireKind.TRUSTED_EXCHANGE -> AccountEndpoint.TRUSTED_EXCHANGE
        PendingWireKind.SELF_EXCHANGE -> AccountEndpoint.SELF_EXCHANGE
        PendingWireKind.RECOVERY_COMMIT -> AccountEndpoint.RECOVERY_OUTCOME
        PendingWireKind.DELETION_CANCEL -> AccountEndpoint.DELETION_OUTCOME
        PendingWireKind.DELETION_REQUEST -> AccountEndpoint.DELETION_RECEIPT
        else -> throw AccountControlException(AccountControlFailure.CONFLICT)
    }
    private fun endpointAllowed(kind: PendingWireKind, endpoint: AccountEndpoint): Boolean = when (kind) {
        PendingWireKind.RECOVERY_COMMIT -> endpoint in setOf(AccountEndpoint.RECOVERY_COMMIT, AccountEndpoint.RECOVERY_OUTCOME)
        PendingWireKind.DELETION_CANCEL -> endpoint in setOf(AccountEndpoint.DELETION_CANCEL, AccountEndpoint.DELETION_OUTCOME)
        PendingWireKind.DELETION_REQUEST -> endpoint in setOf(AccountEndpoint.DELETION_REQUEST, AccountEndpoint.DELETION_RECEIPT, AccountEndpoint.DELETION_RESOLVE)
        PendingWireKind.SELF_START -> endpoint == AccountEndpoint.SELF_START
        PendingWireKind.SELF_CLAIM -> endpoint == AccountEndpoint.SELF_CLAIM
        PendingWireKind.SELF_POLL -> endpoint == AccountEndpoint.SELF_POLL
        PendingWireKind.SELF_DECISION -> endpoint == AccountEndpoint.SELF_DECISION
        PendingWireKind.SELF_EXCHANGE -> endpoint == AccountEndpoint.SELF_EXCHANGE
        PendingWireKind.RECOVERY_CONFIGURE -> endpoint == AccountEndpoint.RECOVERY_CONFIGURE
        PendingWireKind.RECOVERY_PREVIEW -> endpoint == AccountEndpoint.RECOVERY_PREVIEW
        PendingWireKind.DELETION_PREVIEW -> endpoint == AccountEndpoint.DELETION_PREVIEW
        PendingWireKind.ADMISSION_SUBMIT -> endpoint == AccountEndpoint.ADMISSION_SUBMIT
        PendingWireKind.ADMISSION_POLL -> endpoint == AccountEndpoint.ADMISSION_POLL
        PendingWireKind.ADMISSION_RECOVER -> endpoint == AccountEndpoint.ADMISSION_RECOVER
        PendingWireKind.TRUSTED_CHALLENGE -> endpoint == AccountEndpoint.TRUSTED_CHALLENGE
        PendingWireKind.ENROLLMENT_INVITATION_CREATE -> endpoint == AccountEndpoint.INVITATION_CREATE
        PendingWireKind.ENROLLMENT_INVITATION_CANCEL -> endpoint == AccountEndpoint.INVITATION_CANCEL
        PendingWireKind.PORTABLE_ROTATE -> endpoint == AccountEndpoint.PORTABLE_ROTATE
        PendingWireKind.LOGOUT_CURRENT -> endpoint == AccountEndpoint.LOGOUT_CURRENT
        PendingWireKind.LOGOUT_ALL -> endpoint == AccountEndpoint.LOGOUT_ALL
        PendingWireKind.REVOKE -> endpoint == AccountEndpoint.REVOKE_DEVICE
        PendingWireKind.CONSENT -> endpoint == AccountEndpoint.CONSENT_DECIDE
        PendingWireKind.VISIBILITY -> endpoint == AccountEndpoint.VISIBILITY_DECIDE
        PendingWireKind.PUBLIC_REGISTRATION -> false
        else -> endpoint == replayEndpoint(kind)
    }
    private companion object {
        val sessionOperations: Set<PendingWireKind> = setOf(PendingWireKind.PORTABLE_REGISTER, PendingWireKind.PORTABLE_LOGIN,
            PendingWireKind.SESSION_ROTATE, PendingWireKind.ENROLLMENT_EXCHANGE, PendingWireKind.RECOVERY_COMMIT,
            PendingWireKind.ADMISSION_EXCHANGE, PendingWireKind.TRUSTED_EXCHANGE, PendingWireKind.SELF_EXCHANGE, PendingWireKind.DELETION_CANCEL)
    }
}

internal class AccountSelectedServer(val transport: ConfirmedAccountTransport, val identity: D1.TrustedM5Identity,
    val discovery: D1.Discovery, val choice: AccountLocalDataChoice) {
    override fun toString(): String = "AccountSelectedServer(<redacted>)"
}
private class AccountPriorSession(val d1: CapabilityGuard?, val m5: M5CapabilityGuard?, val local: ByteArray,
    val transport: ByteArray, val materialization: ByteArray) : AutoCloseable {
    override fun close() { local.fill(0); transport.fill(0); materialization.fill(0) }
    override fun toString(): String = "AccountPriorSession(<redacted>)"
}
internal fun freshSecret(size: Int): ByteArray {
    val bytes = ByteArray(size).also { SecureRandom().nextBytes(it) }
    return try { Base64.getUrlEncoder().withoutPadding().encode(bytes) } finally { bytes.fill(0) }
}
internal fun <T> verified(result: D1.Result<T>): T = (result as? D1.Result.Verified<T>)?.value
    ?: throw AccountControlException(AccountControlFailure.UNSUPPORTED)
internal fun <T> failed(code: AccountControlFailure): CompletableFuture<T> = CompletableFuture.failedFuture(AccountControlException(code))
internal fun safeFailure(error: Throwable): AccountControlFailure {
    var cause = error
    repeat(4) { if (cause is java.util.concurrent.CompletionException && cause.cause != null) cause = requireNotNull(cause.cause) }
    return when (cause) {
        is AccountControlException -> (cause as AccountControlException).code
        is IllegalArgumentException -> if (cause.message == "ACCOUNT_AUTHENTICATION_FAILED")
            AccountControlFailure.AUTHENTICATION_FAILED else AccountControlFailure.TRANSPORT_UNAVAILABLE
        is SecureStateFailure -> when ((cause as SecureStateFailure).status) {
            SecureStateStatus.OUTCOME_PENDING -> AccountControlFailure.CONFLICT
            SecureStateStatus.CAPACITY_REQUIRED -> AccountControlFailure.CAPACITY
            SecureStateStatus.CONTEXT_CHANGED -> AccountControlFailure.CONTEXT_CHANGED
            else -> AccountControlFailure.TRUST_REQUIRED
        }
        else -> AccountControlFailure.TRANSPORT_UNAVAILABLE
    }
}
