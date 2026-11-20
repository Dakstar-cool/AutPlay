package app.autplay.desktop.presentation

import app.autplay.application.desktop.account.*
import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import app.autplay.desktop.security.DesktopUnlockedRoot
import app.autplay.desktop.sync.DesktopAccountServices
import app.autplay.desktop.sync.DesktopOwnedImportLinkAdmission
import app.autplay.desktop.sync.DesktopSyncException
import app.autplay.desktop.sync.DesktopSyncFailure
import app.autplay.ui.desktop.*
import java.nio.CharBuffer
import java.nio.charset.StandardCharsets
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.coroutines.suspendCoroutine
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonPrimitive

/** Queue/native media effects are owned by Main's actual playback composition. No success defaults. */
public interface DesktopAccountHostMedia {
    public val downloads: StateFlow<DesktopDownloadsView>
    public suspend fun play(target: DesktopAccountTrackTarget, enqueue: Boolean): Unit
    public suspend fun download(target: DesktopAccountTrackTarget): Unit
    public suspend fun cancelDownload(id: String): Unit
    public suspend fun retryDownload(id: String): Unit
    public suspend fun playOffline(id: String): Unit
    public suspend fun setMetered(allowed: Boolean): Unit
    public suspend fun refreshDownloads(target: String?): Unit
    /** Synchronous admission fence; actual native/file drain is awaited separately. */
    public fun retire(): Unit
    public suspend fun drain(): Unit
}

/** Explicit standalone capture belongs to the existing Room/source owner, never to UI labels. */
public interface DesktopAccountHostLocalData {
    public suspend fun reviewBeforeGrant(server: String, continuation: String?): DesktopLocalDataReview
    public suspend fun selectBeforeGrant(review: DesktopLocalDataReview, ids: List<String>): Unit
    public suspend fun sealBeforeGrant(server: String, ids: List<String>): Unit
    /** Caller has read current K/J after its owned ceremony settled: no pending request/grant/preparation. */
    public suspend fun releaseUnsentBeforeGrant(): Unit
    public suspend fun completeBeforeGrant(lease: AccountLease, intentId: AccountUuid): Unit
    public suspend fun review(lease: AccountLease, continuation: String?): DesktopLocalDataReview
    public suspend fun connectSelected(lease: AccountLease, review: DesktopLocalDataReview, selected: List<String>): Unit
}

/** Invoked only after the actual protected create/import/password-unlock command has settled. */
public interface DesktopAccountRootActivation {
    public suspend fun selected(): AccountLease
    public suspend fun lock(): Unit
}

/** Host admission is synchronous; the real external drain runs outside the presentation monitor. */
public interface DesktopAccountHostAuthorityLifecycle {
    public fun fence(action: DesktopAccountAction): Unit
    public suspend fun settle(action: DesktopAccountAction): Unit
    public suspend fun readback(action: DesktopAccountAction): Unit
}

/**
 * Actual presentation owner. currentLease/activate are supplied by the sole K/J reconciliation
 * owner; they are not a second binding store. J revalidates the captured lease on every read/write.
 * All secret commands are copied synchronously before dispatch returns to the UI's wiping finally.
 */
public class DesktopAccountHost(
    private val controller: DesktopAccountController,
    private val picker: DesktopAccountRecoveryPicker,
    private val store: DesktopAccountStore,
    private val services: DesktopAccountServices,
    private val currentLease: () -> AccountLease?,
    private val activate: suspend () -> AccountLease,
    private val localData: DesktopAccountHostLocalData,
    private val media: DesktopAccountHostMedia,
    private val language: () -> DesktopLanguage,
    private val rootActivation: DesktopAccountRootActivation,
    private val authorityLifecycle: DesktopAccountHostAuthorityLifecycle,
    private val importLinks: DesktopOwnedImportLinkAdmission? = null,
) : DesktopAccountActionSink, AutoCloseable {
    private val runtime = controller.accounts
    private val profile = controller.profiles
    private val admission = controller.admission
    private val self = controller.selfPairing
    private val recovery = controller.serverRecovery
    private val portableRecovery = controller.portableRecovery
    private val files = controller.recoveryFiles
    private val gate = Any()
    private val serial = Mutex()
    private val job = SupervisorJob()
    private val worker = CoroutineScope(job + Dispatchers.IO)
    private val sync get() = services.sync
    private val statisticsRuntime get() = services.statistics
    private val refreshes = Channel<String>(Channel.CONFLATED)
    private val catalogOwner = DesktopAccountHostCatalog(store, language, services.canonicalCatalog, services.catalogSearch)
    private val matchingOwner = DesktopAccountHostMatching(services.matching,services.sync,importLinks)
    private val trust = DesktopAccountHostTrust(runtime)
    private val deletion = DesktopAccountHostDeletion(runtime, recovery)
    private val portable = DesktopAccountHostPortable(runtime, portableRecovery)
    private val invitations = DesktopAccountHostInvitations(runtime, controller.invitations)
    private val invitationInput = DesktopAccountHostInvitationInput(runtime, profile)
    private val recoveryExports = DesktopAccountHostRecoveryExports(runtime, recovery)
    private val continuation = DesktopAccountHostContinuation(runtime, profile, admission, self, recovery, portableRecovery, invitations)
    private var lifetime = UUID.randomUUID().toString()
    private var revision = 0L
    private var attached = false
    private var closed = false
    private var busy = false
    private var used: MutableSet<String> = mutableSetOf()
    private var unlocked: DesktopUnlockedRoot? = null
    private var unlockedAuthorityRevision: Long? = null
    private var listConfirmed = false
    private var listOwner: String? = null
    private var policyOwner: String? = null
    private var policyProjection = DesktopProfileViewState()
    private var localReview: DesktopLocalDataReview? = null
    private var pregrantChoice: Pair<String, AccountLocalDataChoice>? = null
    private var failure: AccountControlFailure? = null
    private var operation: DesktopAccountOperation? = null
    private var facts: Facts? = null
    private var statistics: List<DesktopAccountStatisticsView> = emptyList()
    private var statisticsLease: AccountLease? = null
    private var lastLease: AccountLease? = null
    private var revealSelf = false
    private var selfExpiresAt: Long? = null
    private var displayedSelfSlot: String? = null
    private var cancellationPending = false
    private var matchingPending = false
    private var pickerCancelled = false
    private var pendingDisplay: SecretBytes? = null
    private var displayExpiresAt: Long? = null
    private var displayTarget: String? = null
    private val retained: MutableSet<CompletableFuture<Unit>> = java.util.concurrent.ConcurrentHashMap.newKeySet()
    private val mutable = MutableStateFlow(emptyState(DesktopAccountScope(lifetime, revision)))
    public val state: StateFlow<DesktopAccountWorkspaceState> = mutable.asStateFlow()
    private val secretMutable = MutableStateFlow<DesktopAccountSensitiveOutput?>(null)
    public val sensitiveOutput: StateFlow<DesktopAccountSensitiveOutput?> = secretMutable.asStateFlow()

    init {
        require(services.account === runtime && services.store === store)
        worker.launch {
            for (owner in refreshes) {
                val done = CompletableFuture<Unit>(); retained.add(done)
                try { serial.withLock { requireLifetime(owner); refreshFacts(); requireLifetime(owner) } }
                catch (error: Exception) { synchronized(gate) { if (lifetime == owner) failure = safeCode(error) } }
                finally {
                    synchronized(gate) { if (lifetime == owner && attached && !closed) publish() }
                    done.complete(Unit); retained.remove(done)
                }
            }
        }
        worker.launch {
            // A process timer only retires presentation output; it never polls or creates work.
            while (isActive) {
                delay(1_000)
                synchronized(gate) {
                    if (displayExpiresAt?.let { it <= System.currentTimeMillis() / 1_000 } == true) {
                        revealSelf = false; secretMutable.value?.clear(); secretMutable.value = null
                    }
                }
            }
        }
        listOf<Flow<*>>(runtime.state, profile.state, self.state, recovery.state, admission.state,
            sync.state, services.matching.state, services.matching.decisions.state, media.downloads).forEach { flow -> worker.launch {
            flow.drop(1).collect { synchronized(gate) {
                if (attached && !closed && !busy) scheduleRefresh(lifetime)
            } }
        } }
    }

    public fun attach(): Unit {
        val owner = synchronized(gate) {
            if (closed || attached) return
            attached = true; lifetime
        }
        scheduleRefresh(owner)
    }

    /** Closing a surface retires its permits/output, while admitted durable work keeps its owner. */
    public fun deactivate(): Unit = synchronized(gate) {
        attached = false; lifetime = UUID.randomUUID().toString(); used.clear(); displayedSelfSlot = null
        matchingOwner.invalidate(); catalogOwner.retire()
        retireSecrets(); trust.close(); mutable.value = emptyState(DesktopAccountScope(lifetime, ++revision))
    }

    public fun sleep(): Unit {
        deactivate(); importLinks?.retire(); media.retire(); runtime.retire(); services.retire()
    }

    public fun authorityChanged(): Unit {
        val owner = synchronized(gate) {
            lifetime = UUID.randomUUID().toString(); used.clear(); listConfirmed = false; listOwner = null; policyOwner = null
            localReview = null; facts = null; statistics = emptyList(); statisticsLease = null; displayedSelfSlot = null
            matchingOwner.invalidate(); catalogOwner.retire(); matchingPending=false
            statisticsRuntime.invalidate(); deletion.retire(); retireSecrets(); trust.close()
            mutable.value = emptyState(DesktopAccountScope(lifetime, ++revision)); lifetime
        }
        scheduleRefresh(owner)
    }

    override fun dispatch(intent: DesktopAccountIntent): Unit {
        val settled = CompletableFuture<Unit>()
        val owned = try { synchronized(gate) {
            val current = mutable.value
            if (closed || !attached || busy || current.scope != intent.scope ||
                current.permits.none { it === intent.permit } || !intent.permit.acceptsCommand(intent.command) ||
                !used.add(intent.permit.id)) {
                clearInput(intent.command); return
            }
            val command = own(intent.command)
            try { authorityAction(intent.command)?.let(authorityLifecycle::fence) }
            catch (failure: Exception) { command.close(); throw failure }
            retained.add(settled)
            busy = true; failure = null; cancellationPending = false; matchingPending=false; pickerCancelled = false
            operation = DesktopAccountOperation(UUID.randomUUID().toString(), intent.command.action, DesktopAccountCondition.Loading)
            publish(); command
        } } catch (_: Exception) { clearInput(intent.command); return }
        worker.launch {
            try {
                serial.withLock {
                    requireLifetime(intent.scope.lifetime)
                    execute(owned, intent.scope.lifetime)
                    requireLifetime(intent.scope.lifetime)
                    refreshFacts()
                }
            } catch (error: Exception) {
                try { serial.withLock { requireLifetime(intent.scope.lifetime); refreshFacts() } }
                catch (_: Exception) { /* Retain the original stable failure and protected command. */ }
                synchronized(gate) { if (lifetime == intent.scope.lifetime) failure = safeCode(error) }
            } finally {
                owned.close()
                synchronized(gate) {
                    busy = false
                    if (lifetime == intent.scope.lifetime && attached && !closed) {
                        operation = operation?.copy(condition = when {
                            failure != null -> DesktopAccountCondition.Attention
                            cancellationPending -> DesktopAccountCondition.Pending
                            matchingPending -> DesktopAccountCondition.Pending
                            intent.command.action == DesktopAccountAction.DomainMutation -> DesktopAccountCondition.Pending
                            pickerCancelled -> DesktopAccountCondition.Empty
                            intent.command.action in setOf(DesktopAccountAction.Download, DesktopAccountAction.RetryDownload,
                                DesktopAccountAction.CancelDownload, DesktopAccountAction.PlayTrack, DesktopAccountAction.PlayOffline) -> DesktopAccountCondition.Pending
                            intent.command.targetId?.let { it in facts?.pendingTargets.orEmpty() } == true -> DesktopAccountCondition.Pending
                            else -> DesktopAccountCondition.Ready
                        }, issue = if (failure == null && cancellationPending) DesktopAccountIssue.CancelPending else issue(failure)); publish()
                    } else { retireSecrets(); trust.close() }
                }
                settled.complete(Unit); retained.remove(settled)
            }
        }
    }

    private class Owned(val command: DesktopAccountCommand, val first: CharArray? = null,
        val second: CharArray? = null, val extra: List<CharArray> = emptyList(), val matching: (suspend () -> Unit)? = null,
        val invitation: DesktopAccountHostInvitationInput.Dispatch? = null) : AutoCloseable {
        override fun close(): Unit { first?.fill('\u0000'); second?.fill('\u0000'); extra.forEach { it.fill('\u0000') }; invitation?.close() }
    }
    private fun own(command: DesktopAccountCommand): Owned = try {
        when (command) {
            is DesktopAccountCommand.Invoke -> if (command.action == DesktopAccountAction.ConfirmInvitation)
                Owned(command, invitation = invitationInput.capture(requireNotNull(command.targetId), confirmed = true)) else Owned(command)
            is DesktopMatchingUiCommand -> Owned(command,matching=matchingOwner.capture(lease(),command.targetId,command.input))
            is DesktopAccountCommand.Secret -> {
                val first = command.input.consume { it.copyOf() }
                try { Owned(command, first, command.secondInput?.consume { it.copyOf() }) }
                catch (error: Exception) { first.fill('\u0000'); throw error }
            }
            is DesktopAccountCommand.CreatePortable -> Owned(command, command.password.consume { it.copyOf() })
            is DesktopAccountCommand.LoginPortable -> Owned(command, command.password.consume { it.copyOf() })
            is DesktopAccountCommand.Discover -> Owned(command, command.enteredOrigin.consume { it.copyOf() })
            is DesktopAccountCommand.RecoveryPreview -> {
                val account = command.accountId.consume { it.copyOf() }
                try { Owned(command, account, command.code.consume { it.copyOf() }) }
                catch (error: Exception) { account.fill('\u0000'); throw error }
            }
            is DesktopAccountCommand.PinnedTransport -> {
                val copies = mutableListOf<CharArray>()
                try {
                    listOf(command.apiOrigin, command.streamOrigin, command.apiSpki, command.streamSpki).forEach { secret ->
                        copies.add(secret.consume { it.copyOf() })
                    }
                    Owned(command, copies[0], copies[1], copies.drop(2))
                } catch (error: Exception) { copies.forEach { it.fill('\u0000') }; throw error }
            }
            else -> Owned(command)
        }
    } finally { clearInput(command) }

    private suspend fun execute(input: Owned, owner: String): Unit {
        val command = input.command
        val authorityAction = authorityAction(command)
        authorityAction?.let { authorityLifecycle.settle(it) }
        if (command.action in setOf(DesktopAccountAction.RegisterPortable, DesktopAccountAction.LoginPortable,
                DesktopAccountAction.ConfirmInvitation, DesktopAccountAction.RequestAdmission, DesktopAccountAction.EnrollTrusted,
                DesktopAccountAction.ConfirmAdmission, DesktopAccountAction.ConfirmTrusted, DesktopAccountAction.ClaimSelf,
                DesktopAccountAction.ExchangeSelf, DesktopAccountAction.PreviewRecovery, DesktopAccountAction.PreviewDeletion,
                DesktopAccountAction.CommitRecovery, DesktopAccountAction.CancelDeletion))
            sealLocalChoice()
        when (command) {
            is DesktopAccountCommand.CatalogSearch -> catalogOwner.search(command.targetId, command.query, lease())
            is DesktopAccountCommand.DomainEdit -> catalogOwner.mutate(lease(), command)
            is DesktopMatchingUiCommand -> {
                requireNotNull(input.matching).invoke()
                val decision=services.matching.decisions
                matchingPending=decision.hasRetainedOwner() || decision.state.value.pending.any { !it.terminal }
            }
            is DesktopAccountCommand.CreatePortable -> {
                runtime.createPortable(command.publicId, requireNotNull(input.first)).hostAwait()
                lastLease = rootActivation.selected()
            }
            is DesktopAccountCommand.LoginPortable -> {
                runtime.loginPortable(command.publicId, requireNotNull(input.first)).hostAwait()
            }
            is DesktopAccountCommand.Discover -> {
                pregrantChoice = null; localReview = null; trust.discover(requireNotNull(input.first))
            }
            is DesktopAccountCommand.ConfirmTransport -> {
                val choice = pregrantChoice?.takeIf { it.first == command.targetId }?.second ?: changed()
                trust.confirm(command, choice)
            }
            is DesktopAccountCommand.PinnedTransport -> trust.preparePinned(requireNotNull(input.first), requireNotNull(input.second), input.extra[0], input.extra[1])
            is DesktopAccountCommand.ConfirmPinnedTransport -> trust.confirmPinned(command)
            is DesktopAccountCommand.Secret -> secret(command, requireNotNull(input.first), input.second, owner)
            is DesktopAccountCommand.RecoveryPreview -> {
                val code = DesktopServerRecoveryCode.normalize(requireNotNull(input.second).concatToString())
                try { recovery.previewCode(requireNotNull(input.first).concatToString(), code, command.cancelDeletion).hostAwait() }
                finally { code.fill(0) }
            }
            is DesktopAccountCommand.Training -> mutateTraining(command)
            is DesktopAccountCommand.Visibility -> mutateVisibility(command)
            is DesktopAccountCommand.Metered -> media.setMetered(command.allowed)
            is DesktopAccountCommand.ConnectSelected -> {
                val review = localReview?.takeIf { it.id == command.targetId } ?: changed()
                if (command.selectedFactIds.isEmpty() || command.selectedFactIds.size > 100 ||
                    command.selectedFactIds.distinct().size != command.selectedFactIds.size ||
                    command.selectedFactIds.any { id -> review.facts.none { it.id == id && it.eligible } }) changed()
                val transport = trust.view
                if (transport != null) {
                    localData.selectBeforeGrant(review, command.selectedFactIds)
                    pregrantChoice = transport.id to AccountLocalDataChoice.ReviewSelected(command.selectedFactIds)
                } else localData.connectSelected(lease(), review, command.selectedFactIds)
                localReview = null
            }
            is DesktopAccountCommand.Invoke -> if (command.action == DesktopAccountAction.ConfirmInvitation)
                requireNotNull(input.invitation).submit().hostAwait() else invoke(command, owner)
            else -> throw AccountControlException(AccountControlFailure.UNSUPPORTED)
        }
        authorityAction?.let { authorityLifecycle.readback(it) }
    }

    private suspend fun secret(command: DesktopAccountCommand.Secret, first: CharArray, second: CharArray?, owner: String): Unit {
        when (command.action) {
            DesktopAccountAction.UnlockPortable -> {
                val control = runtime.secure.snapshot().hostAwait()
                val root = runtime.roots.unlock(control, first).hostAwait()
                synchronized(gate) { unlocked?.close(); unlocked = root }
                try {
                    lastLease = rootActivation.selected()
                    val current = runtime.secure.snapshot().hostAwait()
                    synchronized(gate) { unlockedAuthorityRevision = current.authorityRevision }
                } catch (failure: Exception) {
                    synchronized(gate) { if (unlocked === root) { root.close(); unlocked = null; unlockedAuthorityRevision = null } }
                    throw failure
                }
            }
            DesktopAccountAction.RestorePortable -> {
                portableRecovery.recoverOffline(first, requireNotNull(second)).hostAwait()
                lastLease = rootActivation.selected()
            }
            DesktopAccountAction.RotateRoot -> {
                portable.rotate(requireNotNull(command.targetId), first, requireNotNull(second))
                lastLease = rootActivation.selected()
            }
            DesktopAccountAction.RegisterPortable, DesktopAccountAction.LoginPortable ->
                runtime.connectPortable(first, command.action == DesktopAccountAction.RegisterPortable).hostAwait()
            DesktopAccountAction.SubmitInvitation -> utf8(first) { invitationInput.prepare(it) { requireLifetime(owner) } }
            DesktopAccountAction.SubmitQr -> {
                val bytes = utf8(first) { it.copyOf() }
                try { readQrInput(bytes, owner) } finally { bytes.fill(0) }
            }
            DesktopAccountAction.ClaimSelf -> {
                utf8(first) { self.claimTextOrFile(it) }.hostAwait()
                displayedSelfSlot = SecureRecordKind.SELF_RECIPIENT.reserved
            }
            DesktopAccountAction.RequestDeletion -> {
                val code = DesktopServerRecoveryCode.normalize(first.concatToString())
                try { deletion.request(requireNotNull(command.targetId), code) } finally { code.fill(0) }
            }
            DesktopAccountAction.LogoutCurrent, DesktopAccountAction.LogoutAll, DesktopAccountAction.RevokeDevice,
            DesktopAccountAction.Disconnect -> {
                val action = when (command.action) {
                    DesktopAccountAction.LogoutCurrent -> AccountExitAction.LOGOUT_CURRENT
                    DesktopAccountAction.LogoutAll -> AccountExitAction.LOGOUT_ALL
                    DesktopAccountAction.RevokeDevice -> AccountExitAction.REVOKE_DEVICE
                    else -> AccountExitAction.DISCONNECT_LOCAL
                }
                val target = if (action == AccountExitAction.REVOKE_DEVICE) profile.state.value.devices
                    .singleOrNull { it.handle.id == command.targetId }?.handle ?: changed() else null
                profile.exit(action, confirmed = true, password = first, target = target).hostAwait()
                retireSecrets(); listConfirmed = false
            }
            else -> throw AccountControlException(AccountControlFailure.UNSUPPORTED)
        }
    }

    private suspend fun invoke(command: DesktopAccountCommand.Invoke, owner: String): Unit {
        when (command.action) {
            DesktopAccountAction.Refresh -> runtime.reload().hostAwait()
            DesktopAccountAction.CancelConnection -> { trust.close(); localReview = null; retireSecrets() }
            DesktopAccountAction.LockPortable -> { rootActivation.lock(); retireSecrets() }
            DesktopAccountAction.PrepareRotation -> portable.prepare()
            DesktopAccountAction.ImportRecoveryFile -> chooseSource(owner)?.let {
                files.importPortable(it).hostAwait()
                lastLease = rootActivation.selected()
            }
            DesktopAccountAction.ExportRecoveryFile -> chooseDestination(owner)?.let {
                files.exportPortable(it).hostAwait()
            }
            DesktopAccountAction.RefreshDevices -> {
                profile.listDevicesAndSessions().hostAwait(); listOwner = runtime.binding?.bindingCommit; listConfirmed = true
            }
            DesktopAccountAction.ReadPolicy -> { profile.loadPrivacy().hostAwait(); confirmPolicyProjection() }
            DesktopAccountAction.RequestAdmission -> admission.requestAdmission(runtime.displayDeviceName).hostAwait()
            DesktopAccountAction.ResumeAdmission -> {
                val slot = facts?.admissionSlot ?: changed()
                if (facts?.admissionTarget != command.targetId) changed()
                admission.restoreProtectedRequest(slot).hostAwait()
                if (admission.state.value.state != "TRUSTED_CHALLENGE") admission.poll().hostAwait()
            }
            DesktopAccountAction.ConfirmAdmission -> {
                if (facts?.admissionTarget != command.targetId || admission.state.value.state != "APPROVED" ||
                    admission.state.value.accountLabel == null) changed()
                admission.confirmApprovedAccount(true).hostAwait()
            }
            DesktopAccountAction.EnrollTrusted -> admission.beginTrustedReenrollment(facts?.trustedProfile ?: changed()).hostAwait()
            DesktopAccountAction.ConfirmTrusted -> {
                if (facts?.admissionTarget != command.targetId || admission.state.value.state != "TRUSTED_CHALLENGE") changed()
                admission.completeTrustedReenrollment(true).hostAwait()
            }
            DesktopAccountAction.ChooseQrFile -> {
                val source = chooseSource(owner)
                if (source != null) {
                    val bytes = files.readBounded(source.path, DesktopPairingInput.MAX_FILE_BYTES)
                    try {
                        val decoded = DesktopPairingInput.decodeFile(bytes) { requireLifetime(owner) }
                        try { readQrInput(decoded, owner) } finally { decoded.fill(0) }
                    } finally { bytes.fill(0) }
                }
            }
            DesktopAccountAction.ChooseServerRecoveryFile -> {
                val source = chooseSource(owner)
                if (source != null) {
                    val bytes = files.readBounded(source.path, 4096)
                    try { DesktopServerRecoveryInput.parse(bytes).use {
                        sealLocalChoice()
                        recovery.previewFile(it, cancelDeletion = command.targetId != null).hostAwait()
                    } } finally { bytes.fill(0) }
                }
            }
            DesktopAccountAction.ConfigureServerRecovery -> {
                recovery.configureSourceCode(true).hostAwait()
            }
            DesktopAccountAction.CreateInvitation -> invitations.create().use { invitation ->
                synchronized(gate) {
                    pendingDisplay?.close(); pendingDisplay = invitation.documentForExplicitDisplay()
                    displayExpiresAt = invitation.expiresAt.epochSecond; displayTarget = invitation.handle.id
                }
            }
            DesktopAccountAction.CancelInvitation -> {
                val target = requireNotNull(command.targetId); invitations.cancel(target)
                if (displayTarget == target) { secretMutable.value?.clear(); secretMutable.value = null; displayTarget = null }
            }
            DesktopAccountAction.PreviewDeletion -> deletion.preview()
            DesktopAccountAction.ExportServerRecovery -> {
                val target = requireNotNull(command.targetId)
                val destination = chooseDestination(owner)
                if (destination != null) recoveryExports.document(target).use { secret ->
                    val bytes = secret.copy()
                    try {
                        files.writeNew(destination.path, bytes)
                        val readback = files.readBounded(destination.path, 4096)
                        try {
                            if (!readback.contentEquals(bytes)) changed()
                            recoveryExports.acknowledge(target, readback)
                        } finally { readback.fill(0) }
                    } finally { bytes.fill(0) }
                }
            }
            DesktopAccountAction.Bind -> {
                if (facts?.binding != command.targetId) changed()
                val active = activate(); lastLease = active
                val outcome = services.synchronize(active)
                val current = currentLease()
                if (current != null) lastLease = current
                statisticsLease = null
                if (current != active) changed()
                outcome.failure?.let { throw DesktopSyncException(it) }
                statisticsLease = current
            }
            DesktopAccountAction.KeepLocal -> {
                trust.view?.let { pregrantChoice = it.id to AccountLocalDataChoice.KeepLocal }
                localReview = null
            }
            DesktopAccountAction.ReviewLocal -> {
                val transport = trust.view
                localReview = if (transport != null) {
                    pregrantChoice = null
                    localData.reviewBeforeGrant(transport.serverIdentity.substringBefore(':'), command.targetId)
                } else localData.review(lease(), command.targetId)
            }
            DesktopAccountAction.StartSelf -> {
                self.startSource().hostAwait()
                synchronized(gate) { revealSelf = true; displayedSelfSlot = SecureRecordKind.SELF_SOURCE.reserved }
            }
            DesktopAccountAction.ApproveSelf -> {
                val shown = mutable.value.selfPairing
                if (shown.role != DesktopSelfRole.Source || shown.ceremonyId != command.targetId ||
                    shown.sas == null || shown.sas != self.state.value.comparisonCode) changed()
                self.decideSource("APPROVE", shown.sas).hostAwait()
            }
            DesktopAccountAction.ExchangeSelf -> {
                val shown = mutable.value.selfPairing
                if (shown.role != DesktopSelfRole.Recipient || shown.ceremonyId != command.targetId ||
                    !shown.exchangeConfirmationRequired || shown.accountLabel != self.state.value.accountLabel) changed()
                self.exchangeRecipient(true).hostAwait()
            }
            DesktopAccountAction.ResumeSelf -> {
                if (mutable.value.selfPairing.ceremonyId != command.targetId) changed()
                self.restoreProtectedCeremony(mutable.value.selfPairing.role == DesktopSelfRole.Source).hostAwait()
                if (mutable.value.selfPairing.role == DesktopSelfRole.Source) self.refreshSourceStatus().hostAwait()
                else self.pollRecipient().hostAwait()
            }
            DesktopAccountAction.CancelSelf -> {
                if (mutable.value.selfPairing.ceremonyId != command.targetId) changed()
                if (mutable.value.selfPairing.role == DesktopSelfRole.Source) self.decideSource("CANCEL").hostAwait()
                else cancelRecipient(requireNotNull(command.targetId))
                retireSecrets()
            }
            DesktopAccountAction.CommitRecovery, DesktopAccountAction.CancelDeletion -> {
                if (facts?.previewTarget != command.targetId) changed()
                recovery.confirmAccount(true).hostAwait()
            }
            DesktopAccountAction.CancelOperation -> runtime.cancelUnsent(
                facts?.cancelHandles?.get(command.targetId) ?: changed(), confirmed = true).hostAwait()
            DesktopAccountAction.ResumeRecovery, DesktopAccountAction.ResumeDeletion, DesktopAccountAction.ResumeOperation -> {
                val target = requireNotNull(command.targetId)
                val slot = facts?.pendingTargets?.get(target) ?: changed()
                val handle = facts?.pendingHandles?.get(target) ?: changed()
                runtime.requireCurrent(handle).hostAwait()
                if (handle.purpose == DesktopPendingPurpose.BINDING) {
                    controller.resume(handle).hostAwait()
                    return
                }
                if (slot in setOf(SecureRecordKind.SELF_SOURCE.reserved, SecureRecordKind.SELF_RECIPIENT.reserved)) displayedSelfSlot = slot
                continuation.resume(slot, target)?.use { invitation ->
                    pendingDisplay?.close(); pendingDisplay = invitation.documentForExplicitDisplay()
                    displayExpiresAt = invitation.expiresAt.epochSecond; displayTarget = invitation.handle.id
                }
                if (facts?.pendingActions?.get(target) in setOf(DesktopAccountAction.DecideTraining, DesktopAccountAction.DecideVisibility))
                    confirmPolicyProjection()
            }
            DesktopAccountAction.OpenArtist, DesktopAccountAction.OpenAlbum, DesktopAccountAction.OpenRecording,
            DesktopAccountAction.OpenRelease, DesktopAccountAction.OpenTrack, DesktopAccountAction.OpenPlaylist,
            DesktopAccountAction.CatalogPage -> catalogOwner.open(requireNotNull(command.targetId), lease())
            DesktopAccountAction.PlayTrack, DesktopAccountAction.QueueTrack -> media.play(
                catalogOwner.track(requireNotNull(command.targetId), lease()), command.action == DesktopAccountAction.QueueTrack)
            DesktopAccountAction.Download -> media.download(catalogOwner.track(requireNotNull(command.targetId), lease()))
            DesktopAccountAction.CancelDownload -> media.cancelDownload(requireNotNull(command.targetId))
            DesktopAccountAction.RetryDownload -> media.retryDownload(requireNotNull(command.targetId))
            DesktopAccountAction.PlayOffline -> media.playOffline(requireNotNull(command.targetId))
            DesktopAccountAction.RefreshDownloads, DesktopAccountAction.DownloadPage -> media.refreshDownloads(command.targetId)
            DesktopAccountAction.RefreshStatistics -> {
                val selected = lease(); statisticsRuntime.refresh(selected); statisticsLease = selected
                refreshStatistics(selected)
            }
            else -> throw AccountControlException(AccountControlFailure.UNSUPPORTED)
        }
    }

    private suspend fun readQrInput(bytes: ByteArray, owner: String): Unit {
        val input = SecureJson.parse(bytes, 16_384, canonical = false)
        if ("invitation_id" in input && "invitation_secret" in input) {
            invitationInput.prepare(bytes) { requireLifetime(owner) }
        } else if ((input["format"] as? JsonPrimitive)?.content == "autplay-self-device-pairing") {
            // The unsigned QR never selects an origin or establishes trust. The existing SELF
            // owner checks it against the independently confirmed selected server and fresh D1.
            if (runtime.selected == null) throw AccountControlException(AccountControlFailure.TRUST_REQUIRED)
            sealLocalChoice()
            self.claimTextOrFile(bytes).hostAwait(); displayedSelfSlot = SecureRecordKind.SELF_RECIPIENT.reserved
        } else {
            pregrantChoice = null; localReview = null; trust.submitDiscovery(bytes)
        }
    }

    private suspend fun sealLocalChoice(): Unit {
        val selected = runtime.selected ?: return
        (selected.choice as? AccountLocalDataChoice.ReviewSelected)?.let {
            localData.sealBeforeGrant(selected.identity.serverInstanceId, it.changeIds)
        }
    }

    private fun localChoiceSummary(): String? {
        val review = trust.view
        val server = review?.serverIdentity ?: runtime.selected?.identity?.serverInstanceId ?: return null
        val choice = if (review != null) pregrantChoice?.takeIf { it.first == review.id }?.second
            else runtime.selected?.choice
        val ru = language() == DesktopLanguage.Russian
        val description = when (choice) {
            AccountLocalDataChoice.KeepLocal -> if (ru) "Оставить данные локально; ничего не копировать." else "Keep data local; copy nothing."
            is AccountLocalDataChoice.ReviewSelected -> if (ru) "Проверено и выбрано записей: ${choice.changeIds.size}." else "Reviewed records selected: ${choice.changeIds.size}."
            null -> return null
        }
        return (if (ru) "Сервер: " else "Server: ") + server + "\n" + description + "\n" +
            (if (ru) "Аккаунт подтверждается в выбранной процедуре. Сохранённая операция сохраняет свой исходный выбор."
            else "Confirm the account in the selected ceremony. A retained operation keeps its original choice.")
    }

    private suspend fun mutateTraining(command: DesktopAccountCommand.Training): Unit {
        // Owner method must accept this exact captured revision; never substitute a newer read.
        profile.decideTraining(when (command.decision) {
            DesktopAccountCommand.TrainingDecision.Grant -> "GRANTED"
            DesktopAccountCommand.TrainingDecision.Deny -> "DENIED"
            DesktopAccountCommand.TrainingDecision.Withdraw -> "WITHDRAWN"
        }, command.expectedRevision).hostAwait()
        confirmPolicyProjection()
    }
    private suspend fun mutateVisibility(command: DesktopAccountCommand.Visibility): Unit {
        profile.decideStatisticsVisibility(command.visible, command.expectedRevision).hostAwait()
        confirmPolicyProjection()
    }

    private suspend fun confirmPolicyProjection(): Unit {
        val selected = lease()
        val binding = runtime.binding ?: changed()
        if (selected.profileId.value != binding.profile || selected.bindingCommit.value != binding.bindingCommit) changed()
        val domain = store.domain ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        val observed = profile.state.value
        val time = System.currentTimeMillis()
        observed.trainingRevision?.let { revision ->
            val decision = observed.trainingDecision ?: changed()
            // These are local observation identities, not invented server command/receipt IDs.
            domain.confirmPolicy(selected, AccountConfirmedPolicy(AccountPolicyKind.TRAINING_CONSENT, revision,
                decision == "GRANTED", time, AccountUuid.random(), null, decision))
        }
        observed.visibilityRevision?.let { revision ->
            val visible = observed.friendsCanViewStatistics ?: changed()
            domain.confirmPolicy(selected, AccountConfirmedPolicy(AccountPolicyKind.STATISTICS_VISIBILITY, revision,
                visible, time, AccountUuid.random(), null, if (visible) "ENABLED" else "DISABLED"))
        }
        readPolicyProjection(selected)
    }

    private suspend fun readPolicyProjection(selected: AccountLease): Unit {
        val rows = (store.domain ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)).readPolicies(selected)
        if (currentLease() != selected) changed()
        val training = rows.singleOrNull { it.kind == AccountPolicyKind.TRAINING_CONSENT }
            ?.takeIf { it.rawState in setOf("UNKNOWN", "GRANTED", "DENIED", "WITHDRAWN") }
        val visibility = rows.singleOrNull { it.kind == AccountPolicyKind.STATISTICS_VISIBILITY }
            ?.takeIf { it.rawState in setOf("ENABLED", "DISABLED") }
        policyProjection = DesktopProfileViewState(trainingDecision = training?.rawState, trainingRevision = training?.revision,
            friendsCanViewStatistics = visibility?.enabled, visibilityRevision = visibility?.revision)
        policyOwner = selected.bindingCommit.value
    }

    private suspend fun cancelRecipient(ceremony: String): Unit {
        val snapshot = runtime.secure.snapshot().hostAwait()
        val stored = runtime.secure.read(requireNotNull(SecureRecordKind.SELF_RECIPIENT.reserved), snapshot).hostAwait() ?: changed()
        stored.use {
            if (it.owner.ceremony != ceremony || it.owner.role != SecureRole.RECIPIENT ||
                it.owner.stage == SecureStage.TERMINAL || it.payload !is PendingPayload) changed()
            // SELF has a SOURCE decision endpoint, not a RECIPIENT remote-cancel endpoint.
            // Stop this exact local ceremony's admissions and transient secrets; retain K's
            // original claim/exchange for reconciliation. This is never a remote terminal receipt.
            runtime.retire(); self.close(); cancellationPending = true
        }
    }

    private data class Facts(val authorityRevision: Long, val binding: String?, val root: Boolean,
        val rootRevision: Long?, val selfRole: DesktopSelfRole, val selfTarget: String?,
        val previewTarget: String?, val pendingTargets: Map<String, String>, val pendingActions: Map<String, DesktopAccountAction>,
        val pendingHandles: Map<String, DesktopPendingHandle>, val cancelHandles: Map<String, DesktopPendingHandle>,
        val admissionSlot: String?, val admissionTarget: String?, val admissionAccount: String?, val trustedProfile: String?,
        val rootRecoveryAvailable: Boolean)
    private data class SelfFact(val slot: String, val target: String?, val role: DesktopSelfRole, val expires: Long?)

    private suspend fun refreshFacts(): Unit {
        val control = runtime.secure.snapshot().hostAwait()
        var hasRoot = false; var rootRevision: Long? = null; var rootRecoveryAvailable = false
        var activeBinding: String? = null
        var bindingIntent: AccountUuid? = null
        var selfTarget: String? = null; var role = DesktopSelfRole.Source
        val selfFacts = mutableListOf<SelfFact>()
        var previewTarget: String? = null
        var admissionSlot: String? = null; var admissionTarget: String? = null
        var admissionAccount: String? = null
        val trustedProfiles = mutableListOf<String>()
        val pending = mutableMapOf<String, String>()
        val pendingActions = mutableMapOf<String, DesktopAccountAction>()
        val pendingHandles = mutableMapOf<String, DesktopPendingHandle>()
        val cancelHandles = mutableMapOf<String, DesktopPendingHandle>()
        control.selected.forEach { reference ->
            val record = runtime.secure.read(reference.slot, control).hostAwait() ?: return@forEach
            record.use {
                val portableRoot = record.payload as? PortableRootPayload
                if (portableRoot != null) {
                    hasRoot = true; rootRevision = record.revision
                    rootRecoveryAvailable = portableRoot.hasRecoveryDocument
                }
                if (record.payload is SessionPayload && control.activeBinding == record.slot &&
                    record.owner.stage == SecureStage.ACTIVE && record.owner.boot == runtime.secure.boot &&
                    record.owner.authorityRevision == control.authorityRevision && !record.owner.detached && !record.owner.deletionVeto)
                    {
                        activeBinding = record.owner.bindingCommit
                        bindingIntent = record.owner.accountProjection?.preparation?.intentId
                    }
                if (record.payload is SessionPayload && record.owner.detached &&
                    record.owner.server == runtime.selected?.identity?.serverInstanceId &&
                    record.owner.identityThumbprint == runtime.selected?.identity?.thumbprintSha256)
                    record.owner.profile?.let(trustedProfiles::add)
                if (record.payload is SessionPayload && !record.owner.detached && !record.owner.deletionVeto &&
                    (record.owner.stage == SecureStage.BINDING_COMMIT_PENDING ||
                        control.activeBinding == record.slot && record.owner.boot != runtime.secure.boot)) {
                    val target = UUID.randomUUID().toString()
                    pending[target] = record.slot
                    pendingActions[target] = DesktopAccountAction.Bind
                    pendingHandles[target] = DesktopPendingHandle(record.slot, reference.valueId, record.slot, DesktopPendingPurpose.BINDING)
                }
                if (record.payload is PendingPayload && record.owner.stage != SecureStage.TERMINAL) {
                    val owner = record.owner
                    owner.operation?.let {
                        pending[it] = record.slot
                        val handle = DesktopPendingHandle(record.slot, reference.valueId, owner.profile, DesktopPendingPurpose.UNSUPPORTED)
                        pendingHandles[it] = handle
                        if (owner.stage == SecureStage.PREPARED) cancelHandles[it] = handle
                        pendingActions[it] = when ((record.payload as PendingPayload).operation) {
                            PendingOperation.CONSENT -> DesktopAccountAction.DecideTraining
                            PendingOperation.VISIBILITY -> DesktopAccountAction.DecideVisibility
                            PendingOperation.LOGOUT_CURRENT -> DesktopAccountAction.LogoutCurrent
                            PendingOperation.LOGOUT_ALL -> DesktopAccountAction.LogoutAll
                            PendingOperation.REVOKE -> DesktopAccountAction.RevokeDevice
                            PendingOperation.PORTABLE_REGISTER -> DesktopAccountAction.RegisterPortable
                            PendingOperation.PORTABLE_LOGIN -> DesktopAccountAction.LoginPortable
                            PendingOperation.PORTABLE_ROTATE -> DesktopAccountAction.RotateRoot
                            PendingOperation.SELF -> DesktopAccountAction.ResumeSelf
                            PendingOperation.RECOVERY -> DesktopAccountAction.ResumeRecovery
                            PendingOperation.DELETION -> DesktopAccountAction.ResumeDeletion
                            PendingOperation.ADMISSION -> DesktopAccountAction.RequestAdmission
                            PendingOperation.TRUSTED_EXCHANGE -> DesktopAccountAction.EnrollTrusted
                            PendingOperation.ENROLLMENT -> when ((record.payload as PendingPayload).wireKind) {
                                PendingWireKind.ENROLLMENT_INVITATION_CREATE -> DesktopAccountAction.CreateInvitation
                                PendingWireKind.ENROLLMENT_INVITATION_CANCEL -> DesktopAccountAction.CancelInvitation
                                else -> DesktopAccountAction.SubmitInvitation
                            }
                            else -> DesktopAccountAction.ResumeOperation
                        }
                    }
                    if ((record.payload as PendingPayload).operation in setOf(PendingOperation.ADMISSION, PendingOperation.TRUSTED_EXCHANGE)) {
                        admissionSlot = record.slot; admissionTarget = owner.ceremony; admissionAccount = owner.account
                    }
                    if (record.payload.kind in setOf(SecureRecordKind.SELF_SOURCE, SecureRecordKind.SELF_RECIPIENT)) {
                        selfFacts.add(SelfFact(record.slot, owner.ceremony,
                            if (owner.role == SecureRole.SOURCE) DesktopSelfRole.Source else DesktopSelfRole.Recipient,
                            owner.expiresAtEpochSeconds))
                    }
                    if (record.payload.kind in setOf(SecureRecordKind.RECOVERY_RECIPIENT, SecureRecordKind.DELETION_RECIPIENT) &&
                        recovery.state.value.confirmationRequired) previewTarget = owner.operation
                }
            }
        }
        val selectedSelf = selfFacts.singleOrNull { it.slot == displayedSelfSlot } ?: selfFacts.singleOrNull()
        if (selectedSelf != null) {
            selfTarget = selectedSelf.target; role = selectedSelf.role; selfExpiresAt = selectedSelf.expires
        } else { selfExpiresAt = null; revealSelf = false }
        val next = Facts(control.authorityRevision, activeBinding, hasRoot, rootRevision, role,
            selfTarget, previewTarget, pending, pendingActions, pendingHandles, cancelHandles,
            admissionSlot, admissionTarget, admissionAccount, trustedProfiles.singleOrNull(), rootRecoveryAvailable)
        if (facts?.let { it.authorityRevision != next.authorityRevision || it.binding != next.binding } == true) {
            listConfirmed = false; listOwner = null; policyOwner = null; localReview = null; statisticsLease = null
            statisticsRuntime.invalidate(); deletion.retire()
            retireSecrets(preserveRoot = unlocked != null && unlockedAuthorityRevision == next.authorityRevision)
        }
        facts = next
        if (next.binding == null && next.pendingTargets.isEmpty()) {
            val retained = store.reconciliationSnapshot()
            if (retained.unresolvedBindingIntent == null && retained.unresolvedPreparation == null)
                localData.releaseUnsentBeforeGrant()
        }
        deletion.refreshRetained()
        invitations.refresh(next.binding)
        recoveryExports.refresh(next.binding)
        val selected = currentLease()
        if (selected != null) {
            lastLease = selected
            val reconciliation = store.reconciliationSnapshot()
            if (reconciliation.storeId != selected.storeId || reconciliation.selectedScope != selected.scopeId ||
                reconciliation.activationRevision != selected.activationRevision || currentLease() != selected) changed()
            bindingIntent?.let { localData.completeBeforeGrant(selected, it) }
            catalogOwner.refresh(selected, lifetime); refreshStatistics(selected); readPolicyProjection(selected)
        } else { catalogOwner.retire(); statistics = emptyList(); policyOwner = null; policyProjection = DesktopProfileViewState() }
    }

    private suspend fun refreshStatistics(selected: AccountLease): Unit {
        val source = store.portableData ?: throw AccountStoreException(AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)
        val value = source.statistics(selected)
        if (currentLease() != selected) changed()
        statistics = buildList {
            add(desktopAccountStatisticsView(value, DesktopStatisticsOrigin.Portable, language()))
            if (statisticsLease == selected) statisticsRuntime.state.value.currentServer?.let { server ->
                add(desktopAccountStatisticsView(AccountPortableStatistics(0, server.throughMs, true, true, null,
                    server.listenedMs), DesktopStatisticsOrigin.Account, language())
                    .copy(serverDetails = desktopServerStatisticsDetails(server, language())))
            }
        }
    }

    private fun scheduleRefresh(owner: String): Unit {
        refreshes.trySend(owner)
    }

    private fun publish(): Unit {
        try { publishState() }
        catch (error: Exception) {
            failure = safeCode(error); retireSecrets()
            mutable.value = emptyState(DesktopAccountScope(lifetime, ++revision), listOfNotNull(operation?.copy(
                condition = DesktopAccountCondition.Attention, issue = issue(failure))))
        }
    }

    private fun publishState(): Unit {
        val captured = facts
        val scope = DesktopAccountScope(lifetime, ++revision)
        used.clear()
        val permits = mutableListOf<DesktopAccountPermit>()
        fun permit(action: DesktopAccountAction, target: String? = null, password: Boolean = false) {
            if (attached && !closed && !busy) permits.add(DesktopAccountPermit(UUID.randomUUID().toString(), action, target, password))
        }
        val matchingView=if(captured!=null && runtime.state.value.phase!=DesktopAccountPhase.RETIRED)
            matchingOwner.view(currentLease(),language()).let { it.copy(busy=it.busy || busy) } else null
        permit(DesktopAccountAction.Refresh)
        if (captured != null && runtime.state.value.phase != DesktopAccountPhase.RETIRED) {
            permit(DesktopAccountAction.Discover); permit(DesktopAccountAction.SubmitQr, password = true)
            permit(DesktopAccountAction.PreparePinnedTransport)
            trust.bootstrap?.let { permit(DesktopAccountAction.ConfirmPinnedTransport, it.id) }
            permit(DesktopAccountAction.ChooseQrFile)
            permit(DesktopAccountAction.CancelConnection)
            trust.view?.let {
                if (captured.pendingTargets.isEmpty()) {
                    permit(DesktopAccountAction.ReviewLocal)
                    permit(DesktopAccountAction.KeepLocal, localReview?.id)
                    localReview?.let { review ->
                        permit(DesktopAccountAction.ConnectSelected, review.id)
                        if (review.hasMore) permit(DesktopAccountAction.ReviewLocal, review.id)
                    }
                }
                if (pregrantChoice?.first == it.id) permit(DesktopAccountAction.ConfirmTransport, it.id)
            }
            if (!captured.root && captured.pendingTargets.isEmpty() && captured.binding == null) {
                permit(DesktopAccountAction.CreatePortable); permit(DesktopAccountAction.ImportRecoveryFile)
            }
            if (captured.root) {
                if (captured.rootRecoveryAvailable) permit(DesktopAccountAction.ExportRecoveryFile)
                permit(if (unlocked == null) DesktopAccountAction.UnlockPortable else DesktopAccountAction.LockPortable,
                    password = unlocked == null)
                if (captured.rootRecoveryAvailable && captured.binding == null && captured.pendingTargets.isEmpty()) permit(DesktopAccountAction.RestorePortable, password = true)
                if (captured.rootRecoveryAvailable && captured.pendingTargets.isEmpty()) {
                    permit(DesktopAccountAction.PrepareRotation)
                    portable.previewId?.let { permit(DesktopAccountAction.RotateRoot, it, true) }
                }
            }
            if (runtime.selected != null && captured.binding == null) {
                permit(DesktopAccountAction.SubmitInvitation, password = true)
                if (captured.pendingTargets.isEmpty()) invitationInput.view()?.let {
                    permit(DesktopAccountAction.ConfirmInvitation, it.id)
                }
                permit(DesktopAccountAction.RequestAdmission)
                if (SecureRecordKind.SELF_RECIPIENT.reserved !in captured.pendingTargets.values)
                    permit(DesktopAccountAction.ClaimSelf, password = true)
                permit(DesktopAccountAction.PreviewRecovery); permit(DesktopAccountAction.PreviewDeletion)
                permit(DesktopAccountAction.ChooseServerRecoveryFile)
                captured.trustedProfile?.let { permit(DesktopAccountAction.EnrollTrusted) }
                if (captured.pendingTargets.isEmpty()) permit(DesktopAccountAction.LoginPortable, password = true)
                if (captured.root) permit(DesktopAccountAction.RegisterPortable, password = true)
            }
            if (captured.binding != null) {
                permit(DesktopAccountAction.RefreshDevices); permit(DesktopAccountAction.ReadPolicy)
                permit(DesktopAccountAction.ConfigureServerRecovery)
                if (invitations.canCreate) permit(DesktopAccountAction.CreateInvitation)
                if (invitations.canCancel) invitations.entries.forEach { permit(DesktopAccountAction.CancelInvitation, it.id) }
                permit(DesktopAccountAction.PreviewDeletion)
                deletion.view?.takeIf { deletion.permitsRequest(it.id) }?.let { permit(DesktopAccountAction.RequestDeletion, it.id, true) }
                if (SecureRecordKind.SELF_SOURCE.reserved !in captured.pendingTargets.values) permit(DesktopAccountAction.StartSelf)
                permit(DesktopAccountAction.Bind, captured.binding)
                permit(DesktopAccountAction.LogoutCurrent, password = true); permit(DesktopAccountAction.LogoutAll, password = true)
                permit(DesktopAccountAction.Disconnect, password = true)
                if (listConfirmed && listOwner == captured.binding) profile.state.value.devices.forEach {
                    permit(DesktopAccountAction.RevokeDevice, it.handle.id, true)
                }
                if (policyOwner == captured.binding && profile.state.value.trainingRevision != null) permit(DesktopAccountAction.DecideTraining)
                if (policyOwner == captured.binding && profile.state.value.visibilityRevision != null) permit(DesktopAccountAction.DecideVisibility)
            }
            recoveryExports.entries.forEach { permit(DesktopAccountAction.ExportServerRecovery, it.targetId) }
            captured.admissionTarget?.let { target ->
                permit(DesktopAccountAction.ResumeAdmission, target)
                if (admission.state.value.state == "APPROVED" && admission.state.value.accountLabel != null)
                    permit(DesktopAccountAction.ConfirmAdmission, target)
                if (admission.state.value.state == "TRUSTED_CHALLENGE") permit(DesktopAccountAction.ConfirmTrusted, target)
            }
            captured.selfTarget?.let { target ->
                permit(DesktopAccountAction.ResumeSelf, target)
                permit(DesktopAccountAction.CancelSelf, target)
                if (self.state.value.state == "CLAIMED" && captured.selfRole == DesktopSelfRole.Source)
                    permit(DesktopAccountAction.ApproveSelf, target)
                if (self.state.value.state == "APPROVED" && captured.selfRole == DesktopSelfRole.Recipient)
                    permit(DesktopAccountAction.ExchangeSelf, target)
            }
            captured.previewTarget?.let { permit(if (recovery.state.value.deletionPending)
                DesktopAccountAction.CancelDeletion else DesktopAccountAction.CommitRecovery, it) }
            deletion.view?.takeIf { it.canCancel }?.let {
                permit(DesktopAccountAction.ChooseServerRecoveryFile, it.id)
                permit(DesktopAccountAction.PreviewDeletion, it.id)
            }
            captured.pendingTargets.forEach { (target, slot) ->
                permit(DesktopAccountAction.ResumeOperation, target)
                if (target in captured.cancelHandles) permit(DesktopAccountAction.CancelOperation, target)
                when (slot) {
                SecureRecordKind.RECOVERY_RECIPIENT.reserved -> permit(DesktopAccountAction.ResumeRecovery, target)
                SecureRecordKind.DELETION_RECIPIENT.reserved, SecureRecordKind.DELETION_SOURCE.reserved -> permit(DesktopAccountAction.ResumeDeletion, target)
                else -> Unit
            } }
            if (currentLease() != null) {
                matchingOwner.targetIds().forEach { permit(DesktopAccountAction.ImportMatching,it) }
                if (trust.view == null) {
                    permit(DesktopAccountAction.ReviewLocal, captured.binding)
                    permit(DesktopAccountAction.KeepLocal, localReview?.id ?: captured.binding)
                    localReview?.let {
                        permit(DesktopAccountAction.ConnectSelected, it.id)
                        if (it.hasMore) permit(DesktopAccountAction.ReviewLocal, it.id)
                    }
                }
                catalogOwner.actions().forEach { permit(it.first, it.second) }
                permit(DesktopAccountAction.RefreshStatistics); permit(DesktopAccountAction.RefreshDownloads)
                val downloads = media.downloads.value
                if (downloads.meteredAllowed != null) permit(DesktopAccountAction.SetMetered)
                downloads.entries.forEach {
                    if (it.offlinePlayable) permit(DesktopAccountAction.PlayOffline, it.id)
                    if (!it.retainedCopy) {
                        permit(DesktopAccountAction.CancelDownload, it.id); permit(DesktopAccountAction.RetryDownload, it.id)
                    }
                }
                downloads.previousTarget?.let { permit(DesktopAccountAction.DownloadPage, it) }
                downloads.nextTarget?.let { permit(DesktopAccountAction.DownloadPage, it) }
            }
        }
        val connection = desktopRuntimeConnectionView(runtime.state.value)
        val recoveryState = recovery.state.value
        val operations = listOfNotNull(operation) + captured?.pendingActions.orEmpty().map { (target, action) ->
            DesktopAccountOperation(target, action, DesktopAccountCondition.Pending,
                cancellable = target in captured?.cancelHandles.orEmpty(), resumable = true)
        }
        mutable.value = DesktopAccountWorkspaceState(scope, permits, operations,
            DesktopPortableAccountView(if (captured?.root == true) {
                if (unlocked != null) DesktopAccountCondition.Ready else DesktopAccountCondition.Locked
            } else DesktopAccountCondition.Unavailable, runtime.state.value.publicId, null,
                if (runtime.state.value.recoveryExportRequired) DesktopRecoveryFileStatus.ReadbackRequired else DesktopRecoveryFileStatus.None,
                rotationPreviewId = portable.previewId, recoveryAvailable = captured?.rootRecoveryAvailable == true),
            DesktopConnectionView(if (captured?.binding == null && captured?.pendingTargets?.isNotEmpty() == true)
                DesktopAccountCondition.Pending else connection.condition,
                issue(failure).takeIf { it != DesktopAccountIssue.None } ?: connection.issue,
                transportReview = trust.view, transportBootstrap = trust.bootstrap, localDataReview = localReview, bindingId = captured?.binding,
                admissionId = captured?.admissionTarget, accountPublicId = connection.accountPublicId ?: runtime.state.value.publicId, pendingItems = connection.pendingItems,
                admissionAccountLabel = admission.state.value.accountLabel ?: captured?.admissionAccount,
                admissionConfirmationRequired = admission.state.value.state == "APPROVED",
                trustedConfirmationRequired = admission.state.value.state == "TRUSTED_CHALLENGE",
                localChoiceSelected = pregrantChoice?.first == trust.view?.id && pregrantChoice != null,
                localChoiceSummary = localChoiceSummary(),
                invitationReview = invitationInput.view()),
            selfView(captured),
            DesktopServerRecoveryView(if (recoveryState.confirmationRequired) DesktopAccountCondition.Attention
                else if (recoveryExports.entries.isNotEmpty()) DesktopAccountCondition.Ready else DesktopAccountCondition.Unavailable,
                captured?.previewTarget, recoveryState.accountLabel, null, recoveryExports.entries),
            desktopProfileDevicesView(if (listConfirmed && listOwner == captured?.binding && captured?.binding != null)
                profile.state.value else DesktopProfileViewState(), listConfirmed && listOwner == captured?.binding, language())
                .copy(invitations = invitations.entries),
            desktopProfilePrivacyView(if (policyOwner == captured?.binding && captured?.binding != null)
                policyProjection else DesktopProfileViewState(), deletion.view?.let { value ->
                DesktopDeletionView(value.id, value.stage, value.role, value.accountPublicId, value.deadlineLabel,
                    value.canCancel, captured?.previewTarget?.takeIf { recoveryState.deletionPending })
            }), catalogOwner.view, media.downloads.value, statistics,matching=matchingView)
        val previousOutput = secretMutable.value
        if (previousOutput != null) {
            val characters = previousOutput.reveal()?.takeIf { previousOutput.scope.lifetime == scope.lifetime &&
                displayExpiresAt?.let { it > System.currentTimeMillis() / 1_000 } == true }?.toCharArray()
            try { secretMutable.value = characters?.let { DesktopAccountSensitiveOutput(scope, previousOutput.kind, it) } }
            finally { characters?.fill('\u0000'); previousOutput.clear() }
        }
        if (self.state.value.state in setOf("EXCHANGED", "REJECTED", "CANCELLED", "EXPIRED") &&
            secretMutable.value?.kind == DesktopAccountSensitiveOutputKind.SelfQr) {
            revealSelf = false; secretMutable.value?.clear(); secretMutable.value = null
        } else if (revealSelf && attached && !closed && !busy &&
            selfExpiresAt?.let { it > System.currentTimeMillis() / 1_000 } == true) {
            secretMutable.value?.clear()
            secretMutable.value = desktopSelfQrForPresentation(self, scope)
            displayExpiresAt = selfExpiresAt; displayTarget = captured?.selfTarget; revealSelf = false
        }
        if (!busy && pendingDisplay != null) {
            val secret = requireNotNull(pendingDisplay); pendingDisplay = null
            secret.use {
                val bytes = it.copy()
                val decoded = StandardCharsets.UTF_8.decode(java.nio.ByteBuffer.wrap(bytes))
                val characters = CharArray(decoded.remaining()); decoded.get(characters)
                try {
                    secretMutable.value?.clear()
                    secretMutable.value = DesktopAccountSensitiveOutput(scope, DesktopAccountSensitiveOutputKind.EnrollmentInvitation, characters)
                } finally { bytes.fill(0); characters.fill('\u0000'); if (decoded.hasArray()) decoded.array().fill('\u0000') }
            }
        }
    }

    private fun selfView(captured: Facts?): DesktopSelfPairingView {
        val value = desktopSelfPairingView(self.state.value, captured?.selfRole ?: DesktopSelfRole.Source, captured?.selfTarget)
        return if (captured?.selfTarget != null && self.state.value.state == "IDLE") DesktopSelfPairingView(
            DesktopAccountCondition.Pending, captured.selfRole, captured.selfTarget, null, null, null, null) else value
    }
    private fun lease(): AccountLease = currentLease() ?: throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
    private suspend fun chooseSource(owner: String): AccountRecoverySource? {
        val value = picker.chooseRecoverySource().hostAwait(); requireLifetime(owner)
        if (value == null) pickerCancelled = true
        return value
    }
    private suspend fun chooseDestination(owner: String): AccountRecoveryDestination? {
        val value = picker.chooseRecoveryDestination().hostAwait(); requireLifetime(owner)
        if (value == null) pickerCancelled = true
        return value
    }
    private fun requireLifetime(owner: String): Unit = synchronized(gate) { if (closed || !attached || lifetime != owner) changed() }
    private fun retireSecrets(preserveRoot: Boolean = false): Unit {
        invitationInput.retire()
        if (!preserveRoot) { unlocked?.close(); unlocked = null; unlockedAuthorityRevision = null }
        revealSelf = false; portable.retire(); secretMutable.value?.clear(); secretMutable.value = null
        pendingDisplay?.close(); pendingDisplay = null; displayExpiresAt = null; displayTarget = null
    }
    /** After deactivate: settle admitted commands before deciding which media owner was selected.
     * Do not await media quiescence here: the caller has not retired that actual owner yet.
     */
    public suspend fun drainAdmittedActions(): Unit = withContext(NonCancellable) {
        while (retained.isNotEmpty()) CompletableFuture.allOf(*retained.toTypedArray()).hostAwait()
        // A queued refresh can enter serial after its future snapshot; its lifetime is retired.
        serial.withLock { Unit }
        runtime.outstandingIo().forEach { it.settled().hostAwait() }
    }

    public suspend fun drain(): Unit = withContext(NonCancellable) {
        drainAdmittedActions()
        media.drain()
        lastLease?.let { services.drain(it) }
        // Includes actual IO completion; the runtime's observation deadline cannot establish drain.
        runtime.outstandingIo().forEach { it.settled().hostAwait() }
    }
    override fun close(): Unit {
        synchronized(gate) { if (closed) return; closed = true }
        deactivate(); refreshes.close(); importLinks?.retire(); media.retire(); services.retire()
        // Do not cancel admitted coroutines: their real CompletionStage settlement is retained.
        worker.launch { drain(); job.cancel() }
    }

    private companion object {
        fun authorityAction(command: DesktopAccountCommand): DesktopAccountAction? = when {
            command is DesktopAccountCommand.Secret && command.action == DesktopAccountAction.SubmitInvitation -> null
            command.action == DesktopAccountAction.ConfirmInvitation -> DesktopAccountAction.SubmitInvitation
            else -> command.action
        }
        fun changed(): Nothing = throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
        fun clearInput(command: DesktopAccountCommand): Unit { when (command) {
            is DesktopAccountCommand.Secret -> command.clear()
            is DesktopAccountCommand.CreatePortable -> command.password.clear()
            is DesktopAccountCommand.LoginPortable -> command.password.clear()
            is DesktopAccountCommand.Discover -> command.enteredOrigin.clear()
            is DesktopAccountCommand.RecoveryPreview -> command.clear()
            is DesktopAccountCommand.PinnedTransport -> command.clear()
            else -> Unit
        } }
        fun <T> utf8(characters: CharArray, block: (ByteArray) -> T): T {
            val encoded = StandardCharsets.UTF_8.encode(CharBuffer.wrap(characters))
            val bytes = ByteArray(encoded.remaining()); encoded.get(bytes)
            return try { block(bytes) } finally { bytes.fill(0); if (encoded.hasArray()) encoded.array().fill(0) }
        }
        fun safeCode(error: Throwable): AccountControlFailure {
            var current = error
            repeat(8) {
                if (current is AccountControlException) return (current as AccountControlException).code
                if(current is DesktopSyncException)return when((current as DesktopSyncException).failure) {
                    DesktopSyncFailure.AUTH_REQUIRED -> AccountControlFailure.AUTHENTICATION_REQUIRED
                    DesktopSyncFailure.TRUST_REQUIRED -> AccountControlFailure.TRUST_REQUIRED
                    DesktopSyncFailure.CAPACITY -> AccountControlFailure.CAPACITY
                    DesktopSyncFailure.CONTEXT_CHANGED,DesktopSyncFailure.CONFLICT -> AccountControlFailure.CONFLICT
                    DesktopSyncFailure.UNSUPPORTED -> AccountControlFailure.UNSUPPORTED
                    DesktopSyncFailure.RETRY_LATER,DesktopSyncFailure.DELIVERY_BLOCKED,DesktopSyncFailure.OUTCOME_PENDING -> AccountControlFailure.BUSY
                    else -> AccountControlFailure.TRANSPORT_UNAVAILABLE
                }
                if (current is SecureStateFailure) return when ((current as SecureStateFailure).status) {
                    SecureStateStatus.CAPACITY_REQUIRED -> AccountControlFailure.CAPACITY
                    SecureStateStatus.CONTEXT_CHANGED -> AccountControlFailure.CONTEXT_CHANGED
                    SecureStateStatus.CLOSED -> AccountControlFailure.CLOSED
                    SecureStateStatus.IDENTITY_UNAVAILABLE -> AccountControlFailure.UNSUPPORTED
                    SecureStateStatus.LOCKED, SecureStateStatus.ACCESS_DENIED, SecureStateStatus.KEY_MISSING -> AccountControlFailure.AUTHENTICATION_REQUIRED
                    else -> AccountControlFailure.CONFLICT
                }
                if (current is AccountStoreException) return when ((current as AccountStoreException).failure) {
                    AccountFailure.ACCOUNT_STORAGE_FULL -> AccountControlFailure.CAPACITY
                    AccountFailure.ACCOUNT_BUSY -> AccountControlFailure.BUSY
                    AccountFailure.ACCOUNT_SCOPE_CHANGED, AccountFailure.ACCOUNT_LEASE_STALE -> AccountControlFailure.CONTEXT_CHANGED
                    AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION, AccountFailure.ACCOUNT_FORMAT_UNSUPPORTED -> AccountControlFailure.UNSUPPORTED
                    else -> AccountControlFailure.CONFLICT
                }
                current = current.cause ?: return AccountControlFailure.TRANSPORT_UNAVAILABLE
            }
            return AccountControlFailure.TRANSPORT_UNAVAILABLE
        }
        fun issue(code: AccountControlFailure?): DesktopAccountIssue = when (code) {
            null -> DesktopAccountIssue.None
            AccountControlFailure.CONTEXT_CHANGED, AccountControlFailure.CONFLICT -> DesktopAccountIssue.Conflict
            AccountControlFailure.CAPACITY -> DesktopAccountIssue.Capacity
            AccountControlFailure.EXPIRED -> DesktopAccountIssue.Expired
            AccountControlFailure.UNSUPPORTED -> DesktopAccountIssue.Unsupported
            AccountControlFailure.AUTHENTICATION_REQUIRED -> DesktopAccountIssue.Reconnect
            AccountControlFailure.AUTHENTICATION_FAILED -> DesktopAccountIssue.Authentication
            AccountControlFailure.TRUST_REQUIRED -> DesktopAccountIssue.IdentityChanged
            else -> DesktopAccountIssue.Retry
        }
        fun emptyState(scope: DesktopAccountScope, operations: List<DesktopAccountOperation> = emptyList()): DesktopAccountWorkspaceState = DesktopAccountWorkspaceState(scope,
            emptyList(), operations, DesktopPortableAccountView(DesktopAccountCondition.Unavailable, null, null, DesktopRecoveryFileStatus.None),
            DesktopConnectionView(DesktopAccountCondition.Unavailable),
            DesktopSelfPairingView(DesktopAccountCondition.Unavailable, DesktopSelfRole.Source, null, null, null, null, null),
            DesktopServerRecoveryView(DesktopAccountCondition.Unavailable, null, null, null),
            DesktopDevicesView(DesktopAccountCondition.Unavailable, emptyList(), emptyList(), emptyList()),
            DesktopPrivacyView(DesktopAccountCondition.Unavailable, null, null, null, null, null),
            DesktopCatalogView(DesktopAccountCondition.Unavailable, null, null, emptyList()),
            DesktopDownloadsView(DesktopAccountCondition.Unavailable, emptyList(), null, false), emptyList())
    }
}

/** Actual completion only. Coroutine cancellation does not complete or release the external owner. */
internal suspend fun <T> CompletionStage<T>.hostAwait(): T = suspendCoroutine { continuation ->
    whenComplete { value, error -> if (error == null) continuation.resume(value) else continuation.resumeWithException(error) }
}
