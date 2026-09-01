package app.autplay.application.profilepairing

import app.autplay.application.profilebinding.LocalDataBindingDecision
import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5MaterializationConsent
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.M5TrustEvidence
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import app.autplay.domain.LocalId
import java.nio.charset.StandardCharsets
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put

/**
 * Application-owned M5 ceremony and lifecycle runtime.  It deliberately keeps raw invitation and
 * token bytes out of UI state and follows the secret-store-before-settings crash ordering.
 */
class ProfilePairingRuntime(
    private val scope: CoroutineScope,
    private val settings: NonSecretSettingsStore,
    private val credentials: CredentialStore,
    private val deviceKeys: M5DeviceKeyStore,
    private val port: ProfilePairingPort,
    private val materialization: M5BindingMaterializationCoordinator,
    private val deviceName: String,
    private val reportSafeError: (String) -> Unit,
    private val registerOrigin: (ServerProfileId, String) -> Unit = { _, _ -> },
    private val allowUnsafeDevelopmentHttp: Boolean = false,
    private val firstBindGate: FirstBindCeremonyGate = FirstBindCeremonyGate(),
) {
    private val mutableState = MutableStateFlow(ProfilePairingRuntimeState())
    private var pendingEnrollment: PendingEnrollment? = null
    val state: StateFlow<ProfilePairingRuntimeState> = mutableState.asStateFlow()

    fun startDiscovery(rawOrigin: String) = scope.launch {
        if (!reserveM5FirstBind()) return@launch
        val origin = runCatching { OriginNormalizer.normalize(rawOrigin, allowUnsafeDevelopmentHttp) }.getOrElse {
            blocked(PairingFailure.SERVER_UNAVAILABLE); return@launch
        }
        val pending = pendingSnapshot(origin)
        mutableState.value = ProfilePairingRuntimeState(pairing = PairingState.CheckingDiscovery(pending))
        when (val result = port.discovery(origin)) {
            is PairingNetworkResult.Failure -> if (isCurrentDiscovery(pending)) blocked(result.code.failure())
            is PairingNetworkResult.Success -> {
                if (!isCurrentDiscovery(pending)) {
                    result.value.identityPublicKeySpki.fill(0)
                    return@launch
                }
                val document = result.value
                // The signed document is evidence only. The user still explicitly confirms it.
                val snapshot = PairingFlowSnapshot(
                    generationId = pending.generationId,
                    apiOrigin = document.apiOrigin,
                    streamOrigin = document.streamOrigin,
                    serverProfileId = pending.serverProfileId,
                    expectedServerInstanceId = document.identity.serverInstanceId,
                    expectedIdentityEpoch = document.identity.identityEpoch,
                    expectedIdentityThumbprintSha256 = document.identity.identityThumbprintSha256,
                    expectedUserId = null,
                    expectedDeviceId = null,
                    deviceKeyThumbprintSha256 = null,
                    operationId = null,
                    bindingCommitId = null,
                )
                mutableState.value = ProfilePairingRuntimeState(
                    pairing = PairingState.AwaitingTrust(snapshot),
                    serverLabel = document.labelHint,
                    identityPublicKeySpki = document.identityPublicKeySpki,
                )
            }
        }
    }

    /** Trust confirmation only advances the ceremony; the invitation is parsed at exchange time. */
    fun confirmTrust() {
        if (!firstBindGate.isReservedBy(FirstBindCeremonyOwner.M5)) return
        val current = mutableState.value.pairing as? PairingState.AwaitingTrust ?: return
        registerOrigin(current.snapshot.serverProfileId, current.snapshot.apiOrigin)
        mutableState.value = mutableState.value.copy(pairing = current, trustConfirmed = true)
    }

    /**
     * Re-establishes the explicitly accepted server identity for an interrupted first admission.
     * The checkpoint exists only after the user confirmed M5 discovery; a fresh signed discovery
     * still has to match every persisted identity/origin field before admission recovery continues.
     */
    suspend fun recoverAdmissionTrust(checkpoint: AdmissionCheckpoint): Boolean {
        if (!reserveM5FirstBind()) return false
        val pending = PairingFlowSnapshot(
            generationId = checkpoint.generationId,
            apiOrigin = checkpoint.apiOrigin,
            streamOrigin = checkpoint.streamOrigin,
            serverProfileId = checkpoint.serverProfileId,
            expectedServerInstanceId = checkpoint.serverInstanceId,
            expectedIdentityEpoch = checkpoint.identityEpoch,
            expectedIdentityThumbprintSha256 = checkpoint.identityThumbprintSha256,
            expectedUserId = null,
            expectedDeviceId = null,
            deviceKeyThumbprintSha256 = checkpoint.deviceKeyThumbprintSha256,
            operationId = null,
            bindingCommitId = null,
        )
        mutableState.value = ProfilePairingRuntimeState(
            pairing = PairingState.CheckingDiscovery(pending),
        )
        val document = when (val result = port.discovery(checkpoint.apiOrigin)) {
            is PairingNetworkResult.Failure -> {
                if (isCurrentDiscovery(pending)) blocked(result.code.failure())
                return false
            }
            is PairingNetworkResult.Success -> result.value
        }
        if (!isCurrentDiscovery(pending)) {
            document.identityPublicKeySpki.fill(0)
            return false
        }
        if (
            document.apiOrigin != checkpoint.apiOrigin ||
            document.streamOrigin != checkpoint.streamOrigin ||
            document.identity.serverInstanceId != checkpoint.serverInstanceId ||
            document.identity.identityEpoch != checkpoint.identityEpoch ||
            document.identity.identityThumbprintSha256 != checkpoint.identityThumbprintSha256
        ) {
            document.identityPublicKeySpki.fill(0)
            blocked(PairingFailure.SERVER_IDENTITY_CHANGED)
            return false
        }
        if (runCatching {
                port.seedTrustedIdentity(document.identity, document.identityPublicKeySpki.copyOf())
            }.isFailure
        ) {
            document.identityPublicKeySpki.fill(0)
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return false
        }
        registerOrigin(checkpoint.serverProfileId, checkpoint.apiOrigin)
        mutableState.value = ProfilePairingRuntimeState(
            pairing = PairingState.AwaitingTrust(pending),
            serverLabel = document.labelHint,
            identityPublicKeySpki = document.identityPublicKeySpki,
            trustConfirmed = true,
        )
        return true
    }

    /**
     * Stores a session minted by the S1B exact-key admission exchange through the ordinary M5
     * credential/binding path.  The caller has already shown and confirmed the Web account.
     */
    suspend fun completeAdmissionEnrollment(
        checkpoint: AdmissionCheckpoint,
        account: AdmissionAccount,
        bindingCommitId: String,
        session: EnrollmentSession,
    ): Boolean {
        if (!firstBindGate.isReservedBy(FirstBindCeremonyOwner.M5)) return false
        val awaiting = mutableState.value.pairing as? PairingState.AwaitingTrust ?: return false
        if (!mutableState.value.trustConfirmed || awaiting.snapshot.serverProfileId != checkpoint.serverProfileId ||
            awaiting.snapshot.expectedServerInstanceId != checkpoint.serverInstanceId ||
            awaiting.snapshot.expectedIdentityEpoch != checkpoint.identityEpoch ||
            awaiting.snapshot.expectedIdentityThumbprintSha256 != checkpoint.identityThumbprintSha256
        ) return false
        val snapshot = awaiting.snapshot.copy(
            generationId = checkpoint.generationId,
            expectedUserId = account.userId,
            expectedDeviceId = session.deviceId,
            deviceKeyThumbprintSha256 = checkpoint.deviceKeyThumbprintSha256,
            operationId = UUID.randomUUID().toString(),
            bindingCommitId = bindingCommitId,
            expectedDeviceName = deviceName.take(120),
        )
        mutableState.value = mutableState.value.copy(pairing = PairingState.ExchangingInvitation(snapshot))
        val sessionId = session.sessionId
        val stored = persistBinding(
            snapshot,
            keyAlias(checkpoint.serverProfileId),
            session,
            mutableState.value.identityPublicKeySpki,
            "KEEP_LOCAL",
        )
        if (stored) refreshCapabilities(snapshot, sessionId, false)
        return stored
    }

    /** Replaces a local trusted-key session using the same account and existing M5 binding. */
    suspend fun completeTrustedReenrollment(
        checkpoint: AdmissionCheckpoint,
        account: AdmissionAccount,
        bindingCommitId: String,
        session: EnrollmentSession,
    ): Boolean {
        val connected = mutableState.value.pairing as? PairingState.Connected ?: return false
        val current = connected.snapshot
        if (current.serverProfileId != checkpoint.serverProfileId || current.expectedUserId != account.userId ||
            current.expectedServerInstanceId != checkpoint.serverInstanceId || current.expectedIdentityEpoch != checkpoint.identityEpoch ||
            current.expectedIdentityThumbprintSha256 != checkpoint.identityThumbprintSha256) return false
        val snapshot = current.copy(generationId = UUID.randomUUID().toString(), expectedDeviceId = session.deviceId, bindingCommitId = bindingCommitId)
        val evidence = mutableState.value.identityPublicKeySpki ?: settings.settings.first().m5TrustEvidence?.identityPublicKeySpkiB64?.let {
            runCatching { Base64.getDecoder().decode(it) }.getOrNull()
        } ?: return false
        mutableState.value = mutableState.value.copy(pairing = PairingState.ExchangingInvitation(snapshot))
        val sessionId = session.sessionId
        val stored = persistBinding(
            snapshot,
            keyAlias(checkpoint.serverProfileId),
            session,
            evidence,
            "KEEP_LOCAL",
        )
        if (stored) refreshCapabilities(snapshot, sessionId, false)
        return stored
    }

    fun exchangeInvitation(rawEnvelope: String) = scope.launch {
        if (!firstBindGate.isReservedBy(FirstBindCeremonyOwner.M5)) return@launch
        val awaiting = mutableState.value.pairing as? PairingState.AwaitingTrust ?: return@launch
        if (!mutableState.value.trustConfirmed) return@launch
        val invitation = runCatching { parseInvitation(rawEnvelope) }.getOrElse {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED); return@launch
        }
        val prior = awaiting.snapshot
        if (invitation.apiOrigin != prior.apiOrigin || invitation.streamOrigin != prior.streamOrigin ||
            invitation.instanceId != prior.expectedServerInstanceId || invitation.identityEpoch != prior.expectedIdentityEpoch ||
            invitation.thumbprint != prior.expectedIdentityThumbprintSha256
        ) {
            blocked(PairingFailure.SERVER_IDENTITY_CHANGED)
            invitation.secret.fill(0)
            return@launch
        }
        val profile = prior.serverProfileId
        val alias = keyAlias(profile)
        val snapshot = prior.copy(
            expectedUserId = invitation.userId,
            operationId = uuid(),
            bindingCommitId = uuid(),
            expectedDeviceName = deviceName.take(120),
        )
        pendingEnrollment?.invitation?.secret?.fill(0)
        pendingEnrollment = PendingEnrollment(snapshot, alias, invitation)
        mutableState.value = mutableState.value.copy(
            pairing = PairingState.AwaitingConfirmation(snapshot),
            accountLabel = invitation.accountDisplayName,
            deviceLabel = deviceName.take(120),
            localDataChoiceRequired = true,
        )
    }

    private suspend fun executePendingEnrollment(
        localDecision: String,
        selectedLocalChangeIds: List<String>,
    ) {
        val pending = pendingEnrollment ?: return
        val confirmation = mutableState.value.pairing as? PairingState.AwaitingConfirmation ?: return
        if (confirmation.snapshot != pending.snapshot) return
        val snapshot = pending.snapshot.copy(
            localDataDecision = localDecision,
            selectedLocalChangeIds = selectedLocalChangeIds,
        )
        val invitation = pending.invitation
        mutableState.value = mutableState.value.copy(
            pairing = PairingState.ExchangingInvitation(snapshot),
            localDataChoiceRequired = false,
            applyingLocalReview = selectedLocalChangeIds.isNotEmpty(),
        )
        val refresh = newM5RefreshToken()
        val refreshHash = sha256(refresh)
        val nonce = ByteArray(16).also(SecureRandom()::nextBytes)
        val command = EnrollmentExchangeCommand(
            snapshot = snapshot,
            invitationId = invitation.id,
            invitationSecret = invitation.secret,
            deviceName = deviceName.take(120),
            nextRefreshToken = refresh,
            nextRefreshTokenSha256 = refreshHash,
            clientNonceB64Url = Base64.getUrlEncoder().withoutPadding().encodeToString(nonce),
        )
        nonce.fill(0)
        if (!persistPendingExchange(
                snapshot,
                pending.alias,
                invitation,
                command,
                localDecision,
                selectedLocalChangeIds,
            )
        ) {
            invitation.secret.fill(0)
            refresh.fill(0)
            pendingEnrollment = null
            return
        }
        when (val response = port.exchange(command)) {
            is PairingNetworkResult.Failure -> if (isCurrentExchange(snapshot)) blocked(response.code.failure())
            is PairingNetworkResult.Success -> {
                if (!isCurrentExchange(snapshot)) {
                    response.value.accessToken.fill(0); response.value.refreshToken.fill(0)
                    return
                }
                val session = response.value
                val bound = snapshot.copy(
                    expectedDeviceId = session.deviceId,
                    deviceKeyThumbprintSha256 = deviceKeys.publicKeyThumbprintSha256(pending.alias),
                )
                mutableState.value = mutableState.value.copy(
                    pairing = PairingState.ExchangingInvitation(bound),
                )
                if (!persistBinding(
                        bound,
                        pending.alias,
                        session,
                        mutableState.value.identityPublicKeySpki,
                        localDecision,
                    )
                ) return
                refreshCapabilities(bound, session.sessionId, false)
                if (selectedLocalChangeIds.isNotEmpty()) {
                    applyMaterialization(bound, session.sessionId, selectedLocalChangeIds)
                }
            }
        }
        pendingEnrollment = null
    }

    fun retry() = scope.launch { recoverAndRefresh() }

    suspend fun recoverAndRefresh() {
        var current = settings.settings.first()
        if (!clearPublicPendingAfterDurableBinding(current)) return
        current = settings.settings.first()
        if (
            current.m5PendingExchangeCheckpoint != null &&
            !reserveM5FirstBind()
        ) return
        current.m5PendingExchangeCheckpoint?.let { checkpoint ->
            if (recoverPendingExchange(current, checkpoint)) return
        }
        val profile = current.activeServerProfileId ?: run {
            mutableState.value = ProfilePairingRuntimeState(); return
        }
        when (val recovery = BindingRecovery(settings, credentials).recover(profile)) {
            is BindingRecoveryResult.Ready -> {
                val user = current.activeUserId ?: return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                val device = current.deviceId ?: return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                val checkpoint = recovery.checkpoint
                val evidence = current.m5TrustEvidence ?: return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                if (current.m5LocalDataDecision == null) {
                    return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                }
                val spki = runCatching { Base64.getDecoder().decode(evidence.identityPublicKeySpkiB64) }.getOrElse {
                    return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                }
                val identity = TrustedServerIdentity(checkpoint.serverInstanceId, checkpoint.identityEpoch, checkpoint.identityThumbprintSha256)
                if (runCatching { port.seedTrustedIdentity(identity, spki) }.isFailure) {
                    return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                }
                val snapshot = PairingFlowSnapshot(
                    generationId = uuid(), apiOrigin = current.serverBaseUrl ?: return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED),
                    streamOrigin = current.streamBaseUrl ?: return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED), serverProfileId = profile,
                    expectedServerInstanceId = checkpoint.serverInstanceId, expectedIdentityEpoch = checkpoint.identityEpoch,
                    expectedIdentityThumbprintSha256 = checkpoint.identityThumbprintSha256, expectedUserId = user,
                    expectedDeviceId = device, deviceKeyThumbprintSha256 = deviceKeys.publicKeyThumbprintSha256(checkpoint.deviceKeyAlias),
                    operationId = null, bindingCommitId = checkpoint.bindingCommitId,
                )
                val pendingMaterialization = runCatching {
                    loadPendingMaterialization(snapshot, checkpoint)
                }.getOrElse {
                    return blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                }
                val activeSnapshot = pendingMaterialization?.snapshot ?: snapshot
                mutableState.value = ProfilePairingRuntimeState(
                    pairing = PairingState.ExchangingInvitation(activeSnapshot),
                    serverLabel = evidence.serverLabelHint,
                )
                refreshCapabilities(activeSnapshot, checkpoint.sessionId, false)
                if (
                    pendingMaterialization != null &&
                    mutableState.value.pairing is PairingState.Connected
                ) {
                    applyMaterialization(
                        activeSnapshot,
                        checkpoint.sessionId,
                        pendingMaterialization.selectedLocalChangeIds,
                    )
                }
            }
            BindingRecoveryResult.NoM5Binding, BindingRecoveryResult.ClearedPartialBinding -> {
                firstBindGate.release(FirstBindCeremonyOwner.M5)
                mutableState.value = ProfilePairingRuntimeState()
            }
        }
    }

    fun chooseLocalData(review: Boolean) = scope.launch {
        if (mutableState.value.pairing !is PairingState.AwaitingConfirmation) return@launch
        if (review) reviewLocalData() else executePendingEnrollment("KEEP_LOCAL", emptyList())
    }

    fun reviewLocalData() = scope.launch {
        val pending = materialization.review(M5MaterializationConsent.MAX_SELECTION)
        mutableState.value = mutableState.value.copy(
            localReview = pending.map { RuntimeLocalDataSummary(it.localChangeId.value, it.eventType, it.occurredAtMs) },
            applyingLocalReview = false,
        )
    }

    fun cancelLocalDataReview() {
        mutableState.value = mutableState.value.copy(localReview = null, applyingLocalReview = false)
    }

    /** Cancelling the first-binding decision returns to local mode and removes only new M5 state. */
    fun cancelFirstBinding() = scope.launch {
        when (val pairing = mutableState.value.pairing) {
            is PairingState.AwaitingConfirmation -> cancel()
            is PairingState.Connected -> {
                val checkpoint = settings.settings.first().m5Binding
                clearLocal(pairing.snapshot.serverProfileId)
                checkpoint?.deviceKeyAlias?.let { alias -> runCatching { deviceKeys.delete(alias) } }
                mutableState.value = ProfilePairingRuntimeState(pairing = PairingState.Cancelled)
            }
            else -> Unit
        }
    }

    fun applyLocalDataSelection(localChangeIds: List<String>) = scope.launch {
        val review = mutableState.value.localReview ?: return@launch
        val selected = localChangeIds.distinct().take(M5MaterializationConsent.MAX_SELECTION)
        if (selected.isEmpty() || selected.size != localChangeIds.size || selected.any { id -> review.none { it.localChangeId == id } }) {
            reportSafeError("MATERIALIZATION_SELECTION_INVALID")
            return@launch
        }
        val confirmation = mutableState.value.pairing as? PairingState.AwaitingConfirmation
        if (confirmation != null) {
            executePendingEnrollment("REVIEW_SELECTED", selected)
            return@launch
        }
        val connected = mutableState.value.pairing as? PairingState.Connected ?: return@launch
        applyMaterialization(connected.snapshot, requireNotNull(settings.settings.first().m5Binding).sessionId, selected)
    }

    private suspend fun applyMaterialization(
        snapshot: PairingFlowSnapshot,
        sessionId: String,
        selected: List<String>,
    ) {
        mutableState.value = mutableState.value.copy(applyingLocalReview = true)
        val consent = M5MaterializationConsent(
            snapshot = snapshot,
            sessionId = sessionId,
            decision = LocalDataBindingDecision.REVIEW_SELECTED,
            selectedLocalChangeIds = selected.map(::LocalId),
        )
        runCatching {
            materialization.apply(consent, System.currentTimeMillis())
            clearPendingMaterialization(snapshot, sessionId)
        }
            .onSuccess {
                mutableState.value = mutableState.value.copy(localDataChoiceRequired = false, localReview = null, applyingLocalReview = false)
            }
            .onFailure {
                mutableState.value = mutableState.value.copy(applyingLocalReview = false)
                reportSafeError((it as? app.autplay.application.profilebinding.M5MaterializationException)?.code?.name ?: "MATERIALIZATION_UNAVAILABLE")
            }
    }

    fun performLifecycle(action: RuntimeLifecycleAction) = scope.launch {
        val connected = mutableState.value.pairing as? PairingState.Connected ?: return@launch
        if (action == RuntimeLifecycleAction.DISCONNECT_LOCAL) {
            clearLocal(connected.snapshot.serverProfileId)
            mutableState.value = ProfilePairingRuntimeState()
            return@launch
        }
        mutableState.value = mutableState.value.copy(pendingLifecycle = action)
        val requestedAction = when (action) {
                RuntimeLifecycleAction.LOGOUT_CURRENT -> LifecycleAction.LOGOUT_CURRENT
                RuntimeLifecycleAction.LOGOUT_ALL -> LifecycleAction.LOGOUT_ALL
                RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE -> LifecycleAction.REVOKE_DEVICE
                RuntimeLifecycleAction.DISCONNECT_LOCAL -> error("unreachable")
            }
        val command = mutableState.value.lastUnknownLifecycle
            ?.takeIf { it.action == requestedAction }
            ?: LifecycleCommand(
                action = requestedAction,
                operationId = uuid(),
                targetDeviceId = if (action == RuntimeLifecycleAction.REVOKE_CURRENT_DEVICE) connected.snapshot.expectedDeviceId else null,
            )
        when (val result = port.lifecycle(connected.snapshot.serverProfileId, command)) {
            is PairingNetworkResult.Success -> {
                clearLocal(connected.snapshot.serverProfileId)
                mutableState.value = ProfilePairingRuntimeState()
            }
            is PairingNetworkResult.Failure -> {
                mutableState.value = mutableState.value.copy(pendingLifecycle = null, lastUnknownLifecycle = command)
                // Timeout/network ambiguity must remain visible as ambiguity, not a local logout.
                reportSafeError(if (result.code == "server_unavailable") "REMOTE_OUTCOME_UNKNOWN" else result.code.uppercase())
            }
        }
    }

    fun createInvitation(expiryMinutes: Int) = scope.launch {
        val connected = mutableState.value.pairing as? PairingState.Connected ?: return@launch
        if (!mutableState.value.canCreateInvitation || expiryMinutes !in 1..30) return@launch
        mutableState.value = mutableState.value.copy(invitationPending = true)
        when (val result = port.createInvitation(connected.snapshot.serverProfileId, uuid(), expiryMinutes * 60)) {
            is PairingNetworkResult.Failure -> {
                mutableState.value = mutableState.value.copy(invitationPending = false)
                reportSafeError(result.code.uppercase())
            }
            is PairingNetworkResult.Success -> {
                val invitation = result.value
                // This string is an intentionally volatile display-once envelope. It is not
                // DataStore/Room/saved-state material and no error contains it.
                val envelope = invitation.envelopeJson
                invitation.secret.fill(0)
                mutableState.value = mutableState.value.copy(invitationPending = false, createdInvitationId = invitation.invitationId, createdInvitationEnvelope = envelope)
            }
        }
    }

    fun cancelCreatedInvitation() = scope.launch {
        val connected = mutableState.value.pairing as? PairingState.Connected ?: return@launch
        val invitation = mutableState.value.createdInvitationId ?: return@launch
        mutableState.value = mutableState.value.copy(invitationPending = true)
        when (val result = port.cancelInvitation(connected.snapshot.serverProfileId, invitation, uuid())) {
            is PairingNetworkResult.Success -> dismissCreatedInvitation()
            is PairingNetworkResult.Failure -> {
                mutableState.value = mutableState.value.copy(invitationPending = false)
                reportSafeError(result.code.uppercase())
            }
        }
    }

    fun dismissCreatedInvitation() {
        mutableState.value = mutableState.value.copy(createdInvitationId = null, createdInvitationEnvelope = null, invitationPending = false)
    }

    fun cancel() {
        firstBindGate.release(FirstBindCeremonyOwner.M5)
        val priorState = mutableState.value
        val cancellingSnapshot = when (val pairing = priorState.pairing) {
            is PairingState.CheckingDiscovery -> pairing.snapshot
            is PairingState.AwaitingTrust -> pairing.snapshot
            is PairingState.AwaitingConfirmation -> pairing.snapshot
            is PairingState.ExchangingInvitation -> pairing.snapshot
            else -> null
        }
        val generation = cancellingSnapshot?.generationId
        val inMemoryPending = pendingEnrollment
        pendingEnrollment = null
        inMemoryPending?.invitation?.secret?.fill(0)
        // Invalidate the in-memory generation synchronously so a delayed response cannot promote.
        mutableState.value = ProfilePairingRuntimeState(pairing = PairingState.Cancelled)
        scope.launch {
            val current = settings.settings.first()
            val pending = current.m5PendingExchangeCheckpoint
            val admissionCheckpoint = current.m5AdmissionCheckpoint?.let {
                runCatching { AdmissionCheckpointCodec.decode(it) }.getOrNull()
            }
            val parts = pending?.split('|').orEmpty()
            val effectiveGeneration = generation ?: parts.getOrNull(2)
                ?: admissionCheckpoint?.generationId
            val profile = inMemoryPending?.snapshot?.serverProfileId
                ?: cancellingSnapshot?.serverProfileId
                ?: parts.getOrNull(1)?.let { runCatching { ServerProfileId(it) }.getOrNull() }
                ?: admissionCheckpoint?.serverProfileId
            val committed = current.m5Binding?.takeIf {
                cancellingSnapshot?.bindingCommitId != null &&
                    it.bindingCommitId == cancellingSnapshot.bindingCommitId
            }
            val alias = inMemoryPending?.alias ?: parts.getOrNull(9) ?: committed?.deviceKeyAlias
            // DataStore serializes this tombstone with any racing checkpoint publication.
            settings.mutate { latest ->
                val latestPending = latest.m5PendingExchangeCheckpoint
                val latestGeneration = latestPending?.split('|')?.getOrNull(2)
                val clearPending = effectiveGeneration != null && latestGeneration == effectiveGeneration
                val cleared = latest.clearBinding(cancellingSnapshot)
                cleared.copy(
                    m5CancelledPairingGenerationId = effectiveGeneration ?: latest.m5CancelledPairingGenerationId,
                    m5PendingExchangeCheckpoint = if (clearPending) null else latestPending,
                    m5AdmissionCheckpoint = null,
                    m5TrustEvidence = if (clearPending && cleared.m5Binding == null) null else cleared.m5TrustEvidence,
                )
            }
            profile?.let { runCatching { credentials.clear(it) } }
            (alias ?: profile?.let(::keyAlias))?.let { runCatching { deviceKeys.delete(it) } }
        }
    }

    /**
     * PA2's only binding seam. It reuses this class's credential-first commit and never creates
     * an S1 trust record or an M5 enrollment invitation.
     */
    suspend fun completePublicAccountRegistration(
        snapshot: PairingFlowSnapshot,
        keyAlias: String,
        session: EnrollmentSession,
        verifiedIdentitySpki: ByteArray,
    ): Boolean {
        requireNotNull(snapshot.expectedUserId); requireNotNull(snapshot.expectedDeviceId); requireNotNull(snapshot.bindingCommitId)
        if (!firstBindGate.isReservedBy(FirstBindCeremonyOwner.PUBLIC_ACCESS)) return false
        registerOrigin(snapshot.serverProfileId, snapshot.apiOrigin)
        mutableState.value = ProfilePairingRuntimeState(pairing = PairingState.ExchangingInvitation(snapshot))
        val sessionId = session.sessionId
        val stored = persistBinding(
            snapshot,
            keyAlias,
            session,
            verifiedIdentitySpki,
            "KEEP_LOCAL",
            preservePublicRegistration = true,
        )
        if (stored) refreshCapabilities(snapshot, sessionId, false)
        val bindingIsDurable = settings.settings.first().m5Binding?.bindingCommitId == snapshot.bindingCommitId
        if (bindingIsDurable) firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
        return stored
    }

    private suspend fun persistBinding(
        snapshot: PairingFlowSnapshot,
        alias: String,
        session: EnrollmentSession,
        identitySpki: ByteArray?,
        localDecision: String,
        preservePublicRegistration: Boolean = false,
    ): Boolean {
        val commit = requireNotNull(snapshot.bindingCommitId)
        return try {
            val publicPending = if (preservePublicRegistration) {
                val encrypted = requireNotNull(credentials.read(snapshot.serverProfileId)) {
                    "ACCOUNT_REGISTRATION_PENDING_MISSING"
                }
                try {
                    SessionCredentialEnvelopeCodec.decode(encrypted).also { pending ->
                        require(pending.refreshPending)
                        requireNotNull(pending.publicAccessPendingRegistrationId)
                        requireNotNull(pending.publicAccessPendingCanonicalRequest)
                        requireNotNull(pending.publicAccessPendingSuccessorRefreshToken)
                    }
                } finally {
                    encrypted.fill(0)
                }
            } else {
                null
            }
            val envelope = SessionCredentialEnvelope(
                accessToken = session.accessToken.toString(StandardCharsets.UTF_8),
                refreshToken = session.refreshToken.toString(StandardCharsets.UTF_8),
                generation = session.sessionGeneration,
                refreshPending = publicPending != null,
                bindingCommitId = commit,
                sessionId = session.sessionId,
                sessionFamilyId = session.sessionFamilyId,
                sessionGeneration = session.sessionGeneration,
                m5PendingMaterializationRequest = if (localDecision == "REVIEW_SELECTED") {
                    materializationRequest(snapshot, session)
                } else {
                    null
                },
                publicAccessPendingRegistrationId = publicPending?.publicAccessPendingRegistrationId,
                publicAccessPendingCanonicalRequest = publicPending?.publicAccessPendingCanonicalRequest,
                publicAccessPendingSuccessorRefreshToken = publicPending?.publicAccessPendingSuccessorRefreshToken,
            )
            if (!isCurrentExchange(snapshot)) return false
            // Secret/session state is durable first. PA2 replay evidence stays in the same envelope
            // until the non-secret binding is durably committed below.
            val encodedEnvelope = SessionCredentialEnvelopeCodec.encode(envelope)
            try {
                credentials.write(snapshot.serverProfileId, encodedEnvelope)
            } finally {
                encodedEnvelope.fill(0)
            }
            if (!isCurrentExchange(snapshot)) {
                if (!preservePublicRegistration) credentials.clear(snapshot.serverProfileId)
                return false
            }
            val spki = requireNotNull(identitySpki) { "M5_IDENTITY_EVIDENCE_MISSING" }
            var stored = false
            settings.mutate { current ->
                if (
                    current.m5CancelledPairingGenerationId == snapshot.generationId ||
                    !isCurrentExchange(snapshot)
                ) {
                    current
                } else {
                    stored = true
                    current.copy(
                        activeServerProfileId = snapshot.serverProfileId,
                        activeUserId = requireNotNull(snapshot.expectedUserId),
                        deviceId = requireNotNull(snapshot.expectedDeviceId),
                        serverBaseUrl = snapshot.apiOrigin,
                        streamBaseUrl = snapshot.streamOrigin,
                        m5Binding = M5BindingCheckpoint(
                            commit,
                            snapshot.expectedServerInstanceId,
                            snapshot.expectedIdentityEpoch,
                            snapshot.expectedIdentityThumbprintSha256,
                            alias,
                            session.sessionId,
                            session.sessionFamilyId,
                            session.sessionGeneration,
                        ),
                        m5TrustEvidence = current.m5TrustEvidence?.let { existing -> existing.copy(
                            identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(spki),
                            serverLabelHint = mutableState.value.serverLabel ?: existing.serverLabelHint,
                        ) } ?: M5TrustEvidence(
                            identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(spki),
                            serverLabelHint = mutableState.value.serverLabel,
                        ),
                        m5LocalDataDecision = localDecision,
                        m5PendingExchangeCheckpoint = null,
                    )
                }
            }
            if (!stored && !preservePublicRegistration) credentials.clear(snapshot.serverProfileId)
            if (stored && !isCurrentExchange(snapshot)) {
                if (preservePublicRegistration) return false
                settings.mutate { current -> current.clearBinding(snapshot) }
                credentials.clear(snapshot.serverProfileId)
                runCatching { deviceKeys.delete(alias) }
                return false
            }
            if (stored && preservePublicRegistration) {
                val cleanEnvelope = envelope.copy(
                    refreshPending = false,
                    publicAccessPendingRegistrationId = null,
                    publicAccessPendingCanonicalRequest = null,
                    publicAccessPendingSuccessorRefreshToken = null,
                )
                val encodedClean = SessionCredentialEnvelopeCodec.encode(cleanEnvelope)
                try {
                    credentials.write(snapshot.serverProfileId, encodedClean)
                } finally {
                    encodedClean.fill(0)
                }
            }
            stored
        } catch (_: Exception) {
            if (!preservePublicRegistration) {
                runCatching { credentials.clear(snapshot.serverProfileId) }
            }
            blocked(PairingFailure.SERVER_UNAVAILABLE)
            false
        } finally {
            session.accessToken.fill(0)
            session.refreshToken.fill(0)
        }
    }

    private fun NonSecretSettings.clearBinding(snapshot: PairingFlowSnapshot?): NonSecretSettings {
        val commit = snapshot?.bindingCommitId ?: return this
        if (m5Binding?.bindingCommitId != commit) return this
        return copy(
            activeServerProfileId = null,
            activeUserId = null,
            deviceId = null,
            serverBaseUrl = null,
            streamBaseUrl = null,
            m5Binding = null,
            m5TrustEvidence = null,
            m5LocalDataDecision = null,
        )
    }

    private suspend fun refreshCapabilities(
        snapshot: PairingFlowSnapshot,
        sessionId: String,
        requireLocalDataChoice: Boolean = true,
        allowRejectedSessionRotation: Boolean = true,
    ) {
        registerOrigin(snapshot.serverProfileId, snapshot.apiOrigin)
        when (val response = port.capabilities(snapshot.serverProfileId, snapshot)) {
            is PairingNetworkResult.Failure -> {
                if (response.code == "authentication_required" && allowRejectedSessionRotation) {
                    rotateRejectedSession(snapshot, sessionId)?.let { rotated ->
                        refreshCapabilities(
                            rotated.snapshot,
                            rotated.sessionId,
                            requireLocalDataChoice,
                            allowRejectedSessionRotation = false,
                        )
                    }
                } else if (isCurrentExchange(snapshot)) {
                    blocked(response.code.failure())
                }
            }
            is PairingNetworkResult.Success -> {
                if (!isCurrentExchange(snapshot)) {
                    response.value.signedPayload.fill(0)
                    return
                }
                val current = settings.settings.first()
                val priorHighWater = current.m5TrustEvidence?.capabilityRevisionHighWater
                val next = PairingReducer.applyCapabilities(PairingState.ExchangingInvitation(snapshot), snapshot, response.value.state, priorHighWater, System.currentTimeMillis())
                if (next !is PairingState.Connected) { mutableState.value = mutableState.value.copy(pairing = next); return }
                settings.mutate { latest -> latest.copy(m5TrustEvidence = latest.m5TrustEvidence?.copy(
                    capabilitySignedPayloadB64 = Base64.getEncoder().encodeToString(response.value.signedPayload),
                    capabilityPayloadSha256 = response.value.payloadSha256,
                    capabilityRevisionHighWater = maxOf(priorHighWater ?: 0, response.value.state.revision),
                )) }
                response.value.signedPayload.fill(0)
                val devices = (port.devices(snapshot.serverProfileId) as? PairingNetworkResult.Success)?.value?.take(100).orEmpty()
                val sessions = (port.sessions(snapshot.serverProfileId) as? PairingNetworkResult.Success)?.value?.take(100).orEmpty()
                val deviceLabels = devices.associate { it.deviceId to it.label }
                val accountLabel = mutableState.value.accountLabel
                mutableState.value = ProfilePairingRuntimeState(
                    pairing = next,
                    devices = devices.map { it.label },
                    sessions = sessions.mapNotNull { deviceLabels[it.deviceId] },
                    localDataChoiceRequired = requireLocalDataChoice,
                    canCreateInvitation = "createEnrollmentInvitation" in response.value.state.supportedOperations,
                    canReenrollTrustedDevice = "reenrollTrustedDevice" in response.value.state.supportedOperations,
                    serverLabel = current.m5TrustEvidence?.serverLabelHint,
                    accountLabel = accountLabel,
                    deviceLabel = devices.firstOrNull { it.deviceId == snapshot.expectedDeviceId }?.label,
                )
                firstBindGate.release(FirstBindCeremonyOwner.M5)
                firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
            }
        }
    }

    /** A process-local owner is insufficient: uncertain PA2 evidence must exclude M5 after restart. */
    private suspend fun reserveM5FirstBind(): Boolean {
        if (!firstBindGate.reserve(FirstBindCeremonyOwner.M5)) return false
        val publicPending = runCatching {
            credentials.hasPublicAccessPendingRegistration()
        }.getOrElse {
            firstBindGate.release(FirstBindCeremonyOwner.M5)
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return false
        }
        if (publicPending) {
            firstBindGate.release(FirstBindCeremonyOwner.M5)
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return false
        }
        return true
    }

    /** Retry the secret-erasure write when PA2 binding committed before the prior process died. */
    private suspend fun clearPublicPendingAfterDurableBinding(current: NonSecretSettings): Boolean {
        val binding = current.m5Binding ?: return true
        val profile = current.activeServerProfileId ?: run {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return false
        }
        val material = runCatching { credentials.read(profile) }.getOrElse {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return false
        } ?: return true
        return try {
            val envelope = SessionCredentialEnvelopeCodec.decode(material)
            if (envelope.publicAccessPendingRegistrationId == null) return true
            if (
                envelope.bindingCommitId != binding.bindingCommitId ||
                envelope.sessionId != binding.sessionId ||
                envelope.sessionFamilyId != binding.sessionFamilyId ||
                envelope.sessionGeneration != binding.sessionGeneration ||
                envelope.refreshToken == null
            ) {
                blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                return false
            }
            val clean = envelope.copy(
                refreshPending = false,
                publicAccessPendingRegistrationId = null,
                publicAccessPendingCanonicalRequest = null,
                publicAccessPendingSuccessorRefreshToken = null,
            )
            val encoded = SessionCredentialEnvelopeCodec.encode(clean)
            try {
                credentials.write(profile, encoded)
                true
            } finally {
                encoded.fill(0)
            }
        } catch (_: Exception) {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            false
        } finally {
            material.fill(0)
        }
    }

    private suspend fun rotateRejectedSession(
        snapshot: PairingFlowSnapshot,
        rejectedSessionId: String,
    ): RotatedSession? {
        val current = settings.settings.first()
        val binding = current.m5Binding
        if (
            current.activeServerProfileId != snapshot.serverProfileId ||
            binding == null ||
            binding.bindingCommitId != snapshot.bindingCommitId ||
            binding.sessionId != rejectedSessionId
        ) {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return null
        }
        val evidence = current.m5TrustEvidence ?: run {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return null
        }
        val identitySpki = runCatching {
            Base64.getDecoder().decode(evidence.identityPublicKeySpkiB64)
        }.getOrElse {
            blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
            return null
        }
        val nextRefreshToken = Base64.getUrlEncoder().withoutPadding().encode(
            ByteArray(32).also(SecureRandom()::nextBytes),
        )
        val rotatedSnapshot = snapshot.copy(
            generationId = uuid(),
            operationId = uuid(),
            bindingCommitId = binding.bindingCommitId,
        )
        mutableState.value = mutableState.value.copy(
            pairing = PairingState.ExchangingInvitation(rotatedSnapshot),
            serverLabel = evidence.serverLabelHint,
        )
        return try {
            when (
                val result = port.rotate(
                    SessionRotationCommand(
                        snapshot = rotatedSnapshot,
                        parentSessionId = binding.sessionId,
                        parentGeneration = binding.sessionGeneration,
                        nextRefreshToken = nextRefreshToken,
                        nextRefreshTokenSha256 = sha256(nextRefreshToken),
                    ),
                )
            ) {
                is PairingNetworkResult.Failure -> {
                    blocked(result.code.failure())
                    null
                }
                is PairingNetworkResult.Success -> {
                    val session = result.value
                    if (
                        session.deviceId != snapshot.expectedDeviceId ||
                        session.sessionFamilyId != binding.sessionFamilyId ||
                        session.sessionGeneration != binding.sessionGeneration + 1
                    ) {
                        session.accessToken.fill(0)
                        session.refreshToken.fill(0)
                        blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
                        null
                    } else if (
                        persistBinding(
                            rotatedSnapshot,
                            binding.deviceKeyAlias,
                            session,
                            identitySpki,
                            requireNotNull(current.m5LocalDataDecision),
                        )
                    ) {
                        RotatedSession(rotatedSnapshot, session.sessionId)
                    } else {
                        null
                    }
                }
            }
        } finally {
            nextRefreshToken.fill(0)
            identitySpki.fill(0)
        }
    }

    private data class RotatedSession(
        val snapshot: PairingFlowSnapshot,
        val sessionId: String,
    )

    private suspend fun clearLocal(profile: ServerProfileId) {
        credentials.clear(profile)
        settings.mutate { current ->
            if (current.activeServerProfileId == profile) {
                current.copy(
                    activeServerProfileId = null,
                    activeUserId = null,
                    deviceId = null,
                    m5Binding = null,
                    m5TrustEvidence = null,
                    m5LocalDataDecision = null,
                    m5PendingExchangeCheckpoint = null,
                )
            } else {
                current.copy(m5PendingExchangeCheckpoint = null)
            }
        }
    }

    private suspend fun persistPendingExchange(
        snapshot: PairingFlowSnapshot,
        alias: String,
        invitation: Invitation,
        command: EnrollmentExchangeCommand,
        localDecision: String,
        selectedLocalChangeIds: List<String>,
    ): Boolean = try {
        if (!isCurrentExchange(snapshot)) return false
        val request = buildJsonObject {
            put("invitation_id", invitation.id)
            put(
                "invitation_secret",
                Base64.getUrlEncoder().withoutPadding().encodeToString(invitation.secret),
            )
            put("binding_commit_id", requireNotNull(snapshot.bindingCommitId))
            put("exchange_id", requireNotNull(snapshot.operationId))
            put("generation_id", snapshot.generationId)
            put("client_nonce_b64url", command.clientNonceB64Url)
            put("device_name", command.deviceName)
            put("next_refresh_token_sha256", command.nextRefreshTokenSha256)
            put("local_data_decision", localDecision)
            put("selected_local_change_ids", buildJsonArray {
                selectedLocalChangeIds.forEach { id ->
                    add(kotlinx.serialization.json.JsonPrimitive(id))
                }
            })
        }.toString()
        val encrypted = SessionCredentialEnvelope(
            accessToken = "pending", refreshToken = null, generation = 0, refreshPending = true,
            m5PendingExchangeId = requireNotNull(snapshot.operationId), m5PendingExchangeRequest = request,
            m5PendingExchangeSuccessorRefreshToken = command.nextRefreshToken.toString(StandardCharsets.US_ASCII),
        )
        val encoded = SessionCredentialEnvelopeCodec.encode(encrypted)
        try { credentials.write(snapshot.serverProfileId, encoded) } finally { encoded.fill(0) }
        if (!isCurrentExchange(snapshot)) {
            credentials.clear(snapshot.serverProfileId)
            return false
        }
        val checkpoint = "v1|${snapshot.serverProfileId.value}|${snapshot.generationId}|${snapshot.apiOrigin}|${snapshot.streamOrigin}|${snapshot.expectedServerInstanceId}|${snapshot.expectedIdentityEpoch}|${snapshot.expectedIdentityThumbprintSha256}|${snapshot.expectedUserId?.value}|$alias|${snapshot.bindingCommitId}|${snapshot.operationId}"
        val spki = requireNotNull(mutableState.value.identityPublicKeySpki) {
            "M5_IDENTITY_EVIDENCE_MISSING"
        }
        var stored = false
        settings.mutate { current ->
            if (
                current.m5CancelledPairingGenerationId == snapshot.generationId ||
                !isCurrentExchange(snapshot)
            ) {
                current
            } else {
                stored = true
                current.copy(
                    m5PendingExchangeCheckpoint = checkpoint,
                    m5TrustEvidence = M5TrustEvidence(
                        identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(spki),
                        serverLabelHint = mutableState.value.serverLabel,
                    ),
                )
            }
        }
        if (!stored) credentials.clear(snapshot.serverProfileId)
        stored
    } catch (_: Exception) {
        runCatching { credentials.clear(snapshot.serverProfileId) }
        settings.mutate { current ->
            val currentGeneration = current.m5PendingExchangeCheckpoint?.split('|')?.getOrNull(2)
            if (currentGeneration == snapshot.generationId) {
                current.copy(
                    m5PendingExchangeCheckpoint = null,
                    m5TrustEvidence = if (current.m5Binding == null) null else current.m5TrustEvidence,
                )
            } else {
                current
            }
        }
        if (isCurrentExchange(snapshot)) blocked(PairingFailure.SERVER_UNAVAILABLE)
        false
    }

    private suspend fun recoverPendingExchange(current: app.autplay.data.settings.NonSecretSettings, checkpoint: String): Boolean {
        var pendingProfile: ServerProfileId? = null
        return try {
        val p = checkpoint.split('|'); require(p.size == 12 && p[0] == "v1")
        val profile = ServerProfileId(p[1]); pendingProfile = profile
        if (current.m5CancelledPairingGenerationId == p[2]) return clearPending(profile)
        val material = credentials.read(profile) ?: return clearPending(profile)
        val pending = try { SessionCredentialEnvelopeCodec.decode(material) } finally { material.fill(0) }
        require(pending.refreshPending && pending.m5PendingExchangeId == p[11])
        val request = Json.parseToJsonElement(requireNotNull(pending.m5PendingExchangeRequest)).jsonObject
        fun value(name: String) = requireNotNull(request[name]).jsonPrimitive.content
        val localDecision = value("local_data_decision")
        require(localDecision in setOf("KEEP_LOCAL", "REVIEW_SELECTED"))
        val selected = requireNotNull(request["selected_local_change_ids"]).jsonArray
            .map { element -> element.jsonPrimitive.content.also(::LocalId) }
        require(selected.size <= M5MaterializationConsent.MAX_SELECTION)
        require(selected.distinct().size == selected.size)
        require((localDecision == "KEEP_LOCAL" && selected.isEmpty()) || (localDecision == "REVIEW_SELECTED" && selected.isNotEmpty()))
        val snapshot = PairingFlowSnapshot(
            generationId = p[2],
            apiOrigin = p[3],
            streamOrigin = p[4],
            serverProfileId = profile,
            expectedServerInstanceId = p[5],
            expectedIdentityEpoch = p[6].toLong(),
            expectedIdentityThumbprintSha256 = p[7],
            expectedUserId = UserId(p[8]),
            expectedDeviceId = null,
            deviceKeyThumbprintSha256 = null,
            operationId = p[11],
            bindingCommitId = p[10],
            expectedDeviceName = value("device_name"),
            localDataDecision = localDecision,
            selectedLocalChangeIds = selected,
        )
        val alias = p[9]; require(deviceKeys.publicKeyThumbprintSha256(alias).isNotBlank())
        require(value("binding_commit_id") == p[10] && value("exchange_id") == p[11])
        val evidence = requireNotNull(current.m5TrustEvidence)
        val identitySpki = Base64.getDecoder().decode(evidence.identityPublicKeySpkiB64)
        port.seedTrustedIdentity(
            TrustedServerIdentity(p[5], p[6].toLong(), p[7]),
            identitySpki.copyOf(),
        )
        val command = EnrollmentExchangeCommand(snapshot, value("invitation_id"), Base64.getUrlDecoder().decode(value("invitation_secret")), value("device_name"), requireNotNull(pending.m5PendingExchangeSuccessorRefreshToken).toByteArray(StandardCharsets.US_ASCII), value("next_refresh_token_sha256"), value("client_nonce_b64url"))
        mutableState.value = ProfilePairingRuntimeState(
            pairing = PairingState.ExchangingInvitation(snapshot),
            serverLabel = evidence.serverLabelHint,
        )
        when (val result = port.exchange(command)) {
            is PairingNetworkResult.Success -> {
                val session = result.value
                val bound = snapshot.copy(expectedDeviceId = session.deviceId, deviceKeyThumbprintSha256 = deviceKeys.publicKeyThumbprintSha256(alias))
                mutableState.value = mutableState.value.copy(
                    pairing = PairingState.ExchangingInvitation(bound),
                )
                if (!persistBinding(bound, alias, session, identitySpki, localDecision)) return true
                refreshCapabilities(bound, session.sessionId, false)
                if (selected.isNotEmpty()) applyMaterialization(bound, session.sessionId, selected)
                true
            }
            is PairingNetworkResult.Failure -> { blocked(result.code.failure()); true }
        }
        } catch (_: Exception) {
            clearPending(pendingProfile)
        }
    }

    private suspend fun clearPending(profile: ServerProfileId?): Boolean {
        profile?.let { runCatching { credentials.clear(it) } }
        settings.mutate {
            it.copy(
                m5PendingExchangeCheckpoint = null,
                m5TrustEvidence = if (it.m5Binding == null) null else it.m5TrustEvidence,
            )
        }
        blocked(PairingFailure.AUTH_ATTENTION_REQUIRED)
        return true
    }

    private fun materializationRequest(
        snapshot: PairingFlowSnapshot,
        session: EnrollmentSession,
    ): String {
        require(snapshot.localDataDecision == "REVIEW_SELECTED")
        require(snapshot.selectedLocalChangeIds.isNotEmpty())
        return buildJsonObject {
            put("marker_version", 1)
            put("generation_id", snapshot.generationId)
            put("api_origin", snapshot.apiOrigin)
            put("stream_origin", snapshot.streamOrigin)
            put("server_profile_id", snapshot.serverProfileId.value)
            put("server_instance_id", snapshot.expectedServerInstanceId)
            put("identity_epoch", snapshot.expectedIdentityEpoch)
            put("identity_thumbprint_sha256", snapshot.expectedIdentityThumbprintSha256)
            put("user_id", requireNotNull(snapshot.expectedUserId).value)
            put("device_id", requireNotNull(snapshot.expectedDeviceId).value)
            put("device_key_thumbprint_sha256", requireNotNull(snapshot.deviceKeyThumbprintSha256))
            put("device_name", requireNotNull(snapshot.expectedDeviceName))
            put("exchange_id", requireNotNull(snapshot.operationId))
            put("binding_commit_id", requireNotNull(snapshot.bindingCommitId))
            put("session_id", session.sessionId)
            put("session_family_id", session.sessionFamilyId)
            put("session_generation", session.sessionGeneration)
            put("local_data_decision", snapshot.localDataDecision)
            put("selected_local_change_ids", buildJsonArray {
                snapshot.selectedLocalChangeIds.forEach { id ->
                    add(kotlinx.serialization.json.JsonPrimitive(id))
                }
            })
        }.toString()
    }

    private suspend fun loadPendingMaterialization(
        base: PairingFlowSnapshot,
        checkpoint: M5BindingCheckpoint,
    ): PendingMaterialization? {
        val material = credentials.read(base.serverProfileId)
            ?: throw IllegalStateException("M5_CREDENTIAL_MISSING")
        val secret = try {
            SessionCredentialEnvelopeCodec.decode(material)
        } finally {
            material.fill(0)
        }
        val request = secret.m5PendingMaterializationRequest ?: return null
        require(!secret.refreshPending)
        require(secret.bindingCommitId == checkpoint.bindingCommitId)
        require(secret.sessionId == checkpoint.sessionId)
        require(secret.sessionFamilyId == checkpoint.sessionFamilyId)
        require(secret.sessionGeneration == checkpoint.sessionGeneration)
        val root = Json.parseToJsonElement(request).jsonObject
        fun value(name: String) = requireNotNull(root[name]).jsonPrimitive.content
        require(value("marker_version") == "1")
        require(value("api_origin") == base.apiOrigin)
        require(value("stream_origin") == base.streamOrigin)
        require(value("server_profile_id") == base.serverProfileId.value)
        require(value("server_instance_id") == base.expectedServerInstanceId)
        require(value("identity_epoch").toLong() == base.expectedIdentityEpoch)
        require(value("identity_thumbprint_sha256") == base.expectedIdentityThumbprintSha256)
        require(value("user_id") == requireNotNull(base.expectedUserId).value)
        require(value("device_id") == requireNotNull(base.expectedDeviceId).value)
        require(value("binding_commit_id") == checkpoint.bindingCommitId)
        require(value("session_id") == checkpoint.sessionId)
        require(value("session_family_id") == checkpoint.sessionFamilyId)
        require(value("session_generation").toLong() == checkpoint.sessionGeneration)
        require(value("local_data_decision") == "REVIEW_SELECTED")
        val selected = requireNotNull(root["selected_local_change_ids"]).jsonArray.map { element ->
            element.jsonPrimitive.content.also(::LocalId)
        }
        val recovered = base.copy(
            generationId = value("generation_id"),
            operationId = value("exchange_id"),
            expectedDeviceName = value("device_name"),
            deviceKeyThumbprintSha256 = value("device_key_thumbprint_sha256"),
            localDataDecision = "REVIEW_SELECTED",
            selectedLocalChangeIds = selected,
        )
        require(deviceKeys.publicKeyThumbprintSha256(checkpoint.deviceKeyAlias) == recovered.deviceKeyThumbprintSha256)
        return PendingMaterialization(recovered, selected)
    }

    private suspend fun clearPendingMaterialization(
        snapshot: PairingFlowSnapshot,
        sessionId: String,
    ) {
        val material = credentials.read(snapshot.serverProfileId)
            ?: throw IllegalStateException("M5_CREDENTIAL_MISSING")
        try {
            val current = SessionCredentialEnvelopeCodec.decode(material)
            if (current.m5PendingMaterializationRequest == null) return
            require(current.bindingCommitId == snapshot.bindingCommitId)
            require(current.sessionId == sessionId)
            val encoded = SessionCredentialEnvelopeCodec.encode(
                current.copy(m5PendingMaterializationRequest = null),
            )
            try {
                credentials.write(snapshot.serverProfileId, encoded)
            } finally {
                encoded.fill(0)
            }
        } finally {
            material.fill(0)
        }
    }

    private fun isCurrentDiscovery(snapshot: PairingFlowSnapshot): Boolean =
        (mutableState.value.pairing as? PairingState.CheckingDiscovery)?.snapshot?.generationId == snapshot.generationId

    private fun isCurrentExchange(snapshot: PairingFlowSnapshot): Boolean =
        (mutableState.value.pairing as? PairingState.ExchangingInvitation)?.snapshot == snapshot

    private fun blocked(failure: PairingFailure) { mutableState.value = mutableState.value.copy(pairing = PairingState.Blocked(failure), pendingLifecycle = null) }
    private fun pendingSnapshot(origin: String) = PairingFlowSnapshot(uuid(), origin, origin, ServerProfileId(uuid()), uuid(), 1, "0".repeat(64), null, null, null, null, null)
    private fun keyAlias(profile: ServerProfileId) = "autplay.m5.${profile.value}"
    private fun uuid() = UUID.randomUUID().toString()
    private fun sha256(value: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(value).joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun String.failure() = when (this) {
        "server_identity_changed" -> PairingFailure.SERVER_IDENTITY_CHANGED
        "incompatible_api_major" -> PairingFailure.INCOMPATIBLE_API_MAJOR
        "capability_rollback_detected" -> PairingFailure.CAPABILITY_ROLLBACK_DETECTED
        "authentication_required", "auth_attention_required", "device_revoked", "session_revoked" -> PairingFailure.AUTH_ATTENTION_REQUIRED
        else -> PairingFailure.SERVER_UNAVAILABLE
    }

    private fun parseInvitation(raw: String): Invitation {
        require(raw.length <= 4096) { "INVITATION_TOO_LARGE" }
        val root = Json.parseToJsonElement(raw).jsonObject
        fun string(name: String) = requireNotNull(root[name]) { "INVITATION_INVALID" }.jsonPrimitive.content
        require(string("contract_version") == "v1" && string("schema_version") == "1")
        require(string("account_display_name").length in 1..120)
        require(string("secret_handling") == "DISPLAY_ONCE_NO_CLIPBOARD_NO_LOG_NO_EXPORT")
        require(Instant.parse(string("expires_at")).isAfter(Instant.now())) { "INVITATION_EXPIRED" }
        val secret = Base64.getUrlDecoder().decode(string("invitation_secret"))
        require(secret.size == 32) { "INVITATION_INVALID" }
        return Invitation(
            id = string("invitation_id").also(::requireCanonicalUuid), userId = UserId(string("user_id")),
            accountDisplayName = string("account_display_name"),
            apiOrigin = OriginNormalizer.normalize(string("api_origin"), allowUnsafeDevelopmentHttp), streamOrigin = OriginNormalizer.normalize(string("stream_origin"), allowUnsafeDevelopmentHttp),
            instanceId = string("server_instance_id").also(::requireCanonicalUuid), identityEpoch = string("identity_epoch").toLong().also { require(it >= 1) },
            thumbprint = string("identity_thumbprint_sha256").also(::requireSha256), secret = secret,
        )
    }

    private data class PendingEnrollment(
        val snapshot: PairingFlowSnapshot,
        val alias: String,
        val invitation: Invitation,
    )

    private data class PendingMaterialization(
        val snapshot: PairingFlowSnapshot,
        val selectedLocalChangeIds: List<String>,
    )

    private data class Invitation(
        val id: String,
        val userId: UserId,
        val accountDisplayName: String,
        val apiOrigin: String,
        val streamOrigin: String,
        val instanceId: String,
        val identityEpoch: Long,
        val thumbprint: String,
        val secret: ByteArray,
    )
}

data class ProfilePairingRuntimeState(
    val pairing: PairingState = PairingState.NotConnected,
    val devices: List<String> = emptyList(),
    val sessions: List<String> = emptyList(),
    val localDataChoiceRequired: Boolean = false,
    val pendingLifecycle: RuntimeLifecycleAction? = null,
    val serverLabel: String? = null,
    val accountLabel: String? = null,
    val deviceLabel: String? = null,
    val trustConfirmed: Boolean = false,
    val localReview: List<RuntimeLocalDataSummary>? = null,
    val applyingLocalReview: Boolean = false,
    val canCreateInvitation: Boolean = false,
    val canReenrollTrustedDevice: Boolean = false,
    val invitationPending: Boolean = false,
    val createdInvitationId: String? = null,
    /** Volatile display-once secret envelope; never persist, log, or save. */
    val createdInvitationEnvelope: String? = null,
    val lastUnknownLifecycle: LifecycleCommand? = null,
    /** In-memory discovery evidence, never sent to presentation. */
    val identityPublicKeySpki: ByteArray? = null,
)

data class RuntimeLocalDataSummary(val localChangeId: String, val eventType: String, val occurredAtMs: Long)

enum class RuntimeLifecycleAction { LOGOUT_CURRENT, LOGOUT_ALL, REVOKE_CURRENT_DEVICE, DISCONNECT_LOCAL }

/** Generates the exact 43-byte ASCII bearer whose SHA-256 is sent to the server. */
internal fun newM5RefreshToken(random: SecureRandom = SecureRandom()): ByteArray {
    val entropy = ByteArray(32).also(random::nextBytes)
    return try {
        java.util.Base64.getUrlEncoder().withoutPadding().encode(entropy)
    } finally {
        entropy.fill(0)
    }
}
