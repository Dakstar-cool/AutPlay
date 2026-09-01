package app.autplay.application.publicaccess

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.FirstBindCeremonyOwner

sealed interface PublicAccountRegistrationState {
    data object Idle : PublicAccountRegistrationState
    data object Importing : PublicAccountRegistrationState
    data class CheckingDiscovery(val invitation: AccountInvitation) : PublicAccountRegistrationState
    data class AwaitingTrust(val invitation: AccountInvitation, val serverLabel: String, val fingerprint: String) : PublicAccountRegistrationState
    data class AwaitingConfirmation(val invitation: AccountInvitation) : PublicAccountRegistrationState
    data object Redeeming : PublicAccountRegistrationState
    data object Connected : PublicAccountRegistrationState
    data class Blocked(val code: String, val firstBindReserved: Boolean = false) : PublicAccountRegistrationState
}

/** Volatile PA2 ceremony state. No member is Room/DataStore/saved-state material. */
class PublicAccountRegistrationCoordinator(
    private val scope: CoroutineScope,
    private val gate: ApprovedRegistrationContextGate,
    private val discovery: M5AccountRegistrationDiscoveryProducer,
    private val runtime: AccountRegistrationRuntime,
    private val activeProfileGate: ActiveProfileGate,
    private val keys: app.autplay.data.security.M5DeviceKeyStore,
    private val deviceName: String,
    private val appVersion: String,
    private val firstBindGate: FirstBindCeremonyGate = FirstBindCeremonyGate(),
) {
    private val mutable = MutableStateFlow<PublicAccountRegistrationState>(PublicAccountRegistrationState.Idle)
    val state: StateFlow<PublicAccountRegistrationState> = mutable
    private var work: Job? = null
    private var redemptionStarted = false

    fun importDocument(mime: String, bytes: ByteArray) {
        if (mutable.value !is PublicAccountRegistrationState.Idle && mutable.value !is PublicAccountRegistrationState.Blocked) return
        val ownedBytes = bytes.copyOf()
        work = scope.launch {
            try {
                if (!firstBindGate.reserve(FirstBindCeremonyOwner.PUBLIC_ACCESS)) {
                    mutable.value = PublicAccountRegistrationState.Blocked("FIRST_BIND_CEREMONY_BUSY")
                    return@launch
                }
                if (activeProfileGate.hasActiveProfile()) {
                    firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
                    mutable.value = PublicAccountRegistrationState.Blocked(
                        "ACCOUNT_REGISTRATION_ACTIVE_PROFILE_FORBIDDEN",
                    )
                    return@launch
                }
                mutable.value = PublicAccountRegistrationState.Importing
                val invitation = runCatching {
                    AccountInvitationParser.parseDocument(mime, ownedBytes)
                }.getOrElse {
                    firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
                    mutable.value = PublicAccountRegistrationState.Blocked("ACCOUNT_INVITATION_INVALID")
                    return@launch
                }
                mutable.value = PublicAccountRegistrationState.CheckingDiscovery(invitation)
                val label = discovery.discoverAndPropose(invitation, gate) ?: run {
                    firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
                    invitation.close()
                    mutable.value = PublicAccountRegistrationState.Blocked("SERVER_IDENTITY_CHANGED")
                    return@launch
                }
                if ((mutable.value as? PublicAccountRegistrationState.CheckingDiscovery)?.invitation !== invitation) {
                    invitation.close()
                    return@launch
                }
                mutable.value = PublicAccountRegistrationState.AwaitingTrust(
                    invitation,
                    label,
                    invitation.identityThumbprintSha256,
                )
            } finally {
                ownedBytes.fill(0)
            }
        }
    }
    fun confirmTrust() {
        val current = mutable.value as? PublicAccountRegistrationState.AwaitingTrust ?: return
        gate.approve()
        mutable.value = PublicAccountRegistrationState.AwaitingConfirmation(current.invitation)
    }
    fun confirmAndRedeem() {
        work = scope.launch {
        val current = mutable.value as? PublicAccountRegistrationState.AwaitingConfirmation ?: return@launch
        if (!firstBindGate.isReservedBy(FirstBindCeremonyOwner.PUBLIC_ACCESS)) return@launch
        redemptionStarted = true
        mutable.value = PublicAccountRegistrationState.Redeeming
        // Re-importing the exact app document after process death resumes the encrypted canonical
        // request/key. A different invitation can never overwrite uncertain pending evidence.
        val profile = app.autplay.domain.ServerProfileId(current.invitation.serverInstanceId)
        val pendingMatch = runCatching { runtime.pendingMatches(profile, current.invitation) }
            .getOrElse {
                current.invitation.close()
                gate.cancel()
                mutable.value = PublicAccountRegistrationState.Blocked(
                    "ACCOUNT_REGISTRATION_PENDING_INVALID",
                    firstBindReserved = true,
                )
                return@launch
            }
        val result = when (pendingMatch) {
            true -> {
                current.invitation.close()
                runtime.resumePending(profile, keys)
            }
            false -> {
                current.invitation.close()
                gate.cancel()
                Result.failure(IllegalStateException("ACCOUNT_REGISTRATION_PENDING_CONFLICT"))
            }
            null -> {
                val signed = AccountRegistrationProof.create(current.invitation, deviceName, appVersion, keys)
                runtime.redeem(signed)
            }
        }
        result.onSuccess {
            redemptionStarted = false
            mutable.value = PublicAccountRegistrationState.Connected
        }.onFailure {
            mutable.value = PublicAccountRegistrationState.Blocked(
                "ACCOUNT_REGISTRATION_FAILED",
                firstBindReserved = firstBindGate.isReservedBy(
                    FirstBindCeremonyOwner.PUBLIC_ACCESS,
                ),
            )
        }
        }
    }
    fun cancel() {
        if (mutable.value is PublicAccountRegistrationState.Redeeming) return
        if (redemptionStarted) {
            mutable.value = PublicAccountRegistrationState.Blocked(
                "ACCOUNT_REGISTRATION_EXACT_REPLAY_REQUIRED",
                firstBindReserved = true,
            )
            return
        }
        work?.cancel()
        work = null
        gate.cancel()
        firstBindGate.release(FirstBindCeremonyOwner.PUBLIC_ACCESS)
        (mutable.value as? PublicAccountRegistrationState.CheckingDiscovery)?.invitation?.close()
        (mutable.value as? PublicAccountRegistrationState.AwaitingTrust)?.invitation?.close()
        (mutable.value as? PublicAccountRegistrationState.AwaitingConfirmation)?.invitation?.close()
        mutable.value = PublicAccountRegistrationState.Idle
    }
}
