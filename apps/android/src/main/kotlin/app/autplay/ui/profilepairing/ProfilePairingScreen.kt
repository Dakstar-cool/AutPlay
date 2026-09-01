package app.autplay.ui.profilepairing

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.WindowManager
import app.autplay.R
import app.autplay.application.profilepairing.PairingFailure
import app.autplay.application.profilepairing.PairingState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.application.publicaccess.AccountInvitation
import app.autplay.application.publicaccess.OwnerProvisioningUiState
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayTokens
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

/** Presentation-only state. Commands are delegated to the application layer through [ProfilePairingActions]. */
internal data class ProfilePairingUiState(
    val pairing: PairingState,
    val serverLabel: String? = null,
    val accountLabel: String? = null,
    val deviceLabel: String? = null,
    val pendingSyncCount: Int = 0,
    val devices: List<String> = emptyList(),
    val sessions: List<String> = emptyList(),
    /** Runtime-owned trust gate; no enrollment bearer entry is rendered before trust confirmation. */
    val trustConfirmed: Boolean = false,
    val localDataChoiceRequired: Boolean = false,
    /** A bounded, personal-data-free review projection supplied by the application layer. */
    val localDataReview: LocalDataReviewUiState? = null,
    /** Authenticated runtime projection; UI must not infer administrative authority itself. */
    val invitationManagement: InvitationManagementUiState? = null,
    /** Application-owned in-flight action; prevents duplicate destructive commands. */
    val pendingRemoteAction: ProfileRemoteAction? = null,
    /** S1B approval state is independent from local library/playback state. */
    val admission: AdmissionUiState? = null,
    /** Volatile PA2 first-account ceremony; invitation bytes are never saveable or persisted here. */
    val publicAccountRegistration: PublicAccountRegistrationState? = null,
    /** Server-authorized OWNER projection; absent/unauthorized users see no management controls. */
    val ownerProvisioning: OwnerProvisioningUiState? = null,
    /** Exact S1B checkpoint availability; PA2 USER first-bind creates no trusted-key recovery path. */
    val canReenrollTrustedDevice: Boolean = false,
)

internal data class PublicAccountTrustPresentation(
    val serverLabel: String,
    val fingerprint: String,
    val apiOrigin: String,
    val streamOrigin: String,
    val accountDisplayName: String,
    val accountRole: String,
    val expiresAt: String,
)

internal fun publicAccountTrustPresentation(
    state: PublicAccountRegistrationState.AwaitingTrust,
): PublicAccountTrustPresentation = PublicAccountTrustPresentation(
    serverLabel = state.serverLabel,
    fingerprint = state.fingerprint,
    apiOrigin = state.invitation.apiOrigin,
    streamOrigin = state.invitation.streamOrigin,
    accountDisplayName = state.invitation.accountDisplayName,
    accountRole = "USER",
    expiresAt = state.invitation.expiresAt.toString(),
)

internal fun publicRegistrationBlocksOrdinaryFirstBind(
    state: PublicAccountRegistrationState?,
): Boolean = when (state) {
    null, PublicAccountRegistrationState.Idle, PublicAccountRegistrationState.Connected -> false
    is PublicAccountRegistrationState.Blocked -> state.firstBindReserved
    else -> true
}

/** A safe local-intent row: it deliberately contains neither payloads nor server credentials. */
internal data class PendingLocalDataUiSummary(
    val localChangeId: String,
    val eventType: String,
    val occurredAtMs: Long,
)

/** Review work is application-owned; Compose only renders its supplied bounded projection. */
internal data class LocalDataReviewUiState(
    val items: List<PendingLocalDataUiSummary>,
    val applying: Boolean = false,
) {
    init {
        require(items.size <= MAX_ITEMS)
        require(items.map { it.localChangeId }.distinct().size == items.size)
    }

    companion object { const val MAX_ITEMS = 100 }
}

/** Volatile invitation-management projection. [createdSecret] must never be persisted or saveable. */
internal data class InvitationManagementUiState(
    val canCreate: Boolean,
    val minExpiryMinutes: Int,
    val maxExpiryMinutes: Int,
    val creating: Boolean = false,
    val createdSecret: String? = null,
    val cancelling: Boolean = false,
) {
    init {
        require(minExpiryMinutes in 1..maxExpiryMinutes)
        require(maxExpiryMinutes <= MAX_EXPIRY_MINUTES)
    }

    companion object { const val MAX_EXPIRY_MINUTES = 60 * 24 * 30 }
}

internal enum class ExistingLocalDataChoice { KEEP_ON_PHONE, REVIEW_AND_CONNECT, CANCEL }
internal enum class ProfileRemoteAction { LOGOUT_CURRENT, LOGOUT_ALL, REVOKE_CURRENT_DEVICE, DISCONNECT_LOCAL }

/** UI callbacks only; they may start application-owned work but Compose never performs I/O itself. */
internal data class ProfilePairingActions(
    val startDiscovery: (String) -> Unit = {},
    val confirmTrust: () -> Unit = {},
    val cancelPairing: () -> Unit = {},
    val exchangeInvitation: (String) -> Unit = {},
    val chooseLocalData: (ExistingLocalDataChoice) -> Unit = {},
    val reviewLocalData: () -> Unit = {},
    val cancelLocalDataReview: () -> Unit = {},
    val applyLocalDataSelection: (List<String>) -> Unit = {},
    val createInvitation: (Int) -> Unit = {},
    val cancelCreatedInvitation: () -> Unit = {},
    val dismissCreatedInvitation: () -> Unit = {},
    val retry: () -> Unit = {},
    val openSync: () -> Unit = {},
    val performRemoteAction: (ProfileRemoteAction) -> Unit = {},
    val reenrollTrustedDevice: () -> Unit = {},
    val redeemPublicAccountInvitation: (String) -> Unit = {},
    val pickPublicAccountInvitation: () -> Unit = {},
    val scanPublicAccountInvitation: () -> Unit = {},
    val confirmPublicAccountTrust: () -> Unit = {},
    val confirmPublicAccountRegistration: () -> Unit = {},
    val cancelPublicAccountRegistration: () -> Unit = {},
    val refreshOwnerProvisioning: () -> Unit = {},
    val createOwnerAccountInvitation: (String, Int) -> Unit = { _, _ -> },
    val cancelOwnerAccountInvitation: (String) -> Unit = {},
    val disableProvisionedAccount: (String) -> Unit = {},
    val dismissOwnerAccountInvitation: () -> Unit = {},
    val shareOwnerAccountInvitation: (AccountInvitation) -> Unit = {},
    val admission: AdmissionActions = AdmissionActions(),
)

@Composable
internal fun ProfilePairingScreen(
    state: ProfilePairingUiState,
    actions: ProfilePairingActions,
    modifier: Modifier = Modifier,
) {
    var origin by rememberSaveable { mutableStateOf("") }
    // An enrollment bearer is deliberately not saveable: process recreation must require a fresh entry.
    var invitation by remember { mutableStateOf("") }
    var publicInvitationDocument by remember { mutableStateOf("") }
    var pendingConfirmation by rememberSaveable { mutableStateOf<ProfileRemoteAction?>(null) }
    val publicFirstBindReserved = publicRegistrationBlocksOrdinaryFirstBind(
        state.publicAccountRegistration,
    )

    SecureWindowWhileVisible(
        enabled = requiresSecureProfileWindow(state, publicInvitationDocument.isNotBlank()),
    )

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        PersonalServerHero(state)
        when (val pairing = state.pairing) {
            PairingState.NotConnected, PairingState.Cancelled -> {
                if (!publicFirstBindReserved) Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = MaterialTheme.shapes.large,
                    color = AutPlayTokens.colors.raisedSurface,
                    tonalElevation = 1.dp,
                ) {
                    Column(
                        modifier = Modifier.padding(18.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Text(
                            stringResource(R.string.profile_server_origin),
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text(
                            stringResource(R.string.profile_server_origin_hint),
                            style = MaterialTheme.typography.bodyMedium,
                            color = AutPlayTokens.colors.mutedText,
                        )
                        OutlinedTextField(
                            value = origin,
                            onValueChange = { origin = it.take(2048) },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text(stringResource(R.string.profile_server_origin)) },
                            singleLine = true,
                        )
                        Button(
                            enabled = origin.isNotBlank(),
                            onClick = { actions.startDiscovery(origin.trim()) },
                            modifier = Modifier
                                .fillMaxWidth()
                                .heightIn(min = 52.dp)
                                .semantics { role = Role.Button },
                        ) { Text(stringResource(R.string.profile_check_server)) }
                        Text(
                            stringResource(R.string.profile_local_body),
                            style = MaterialTheme.typography.bodySmall,
                            color = AutPlayTokens.colors.mutedText,
                        )
                    }
                }
            }
            is PairingState.CheckingDiscovery -> PairingProgress(R.string.profile_connection_checking, actions.cancelPairing)
            is PairingState.AwaitingTrust -> {
                Text(stringResource(R.string.profile_connection_trust), style = MaterialTheme.typography.titleMedium)
                Text(stringResource(R.string.profile_trust_explanation))
                state.serverLabel?.let { Text(stringResource(R.string.profile_server_named, it)) }
                TrustEvidence(pairing.snapshot)
                Button(onClick = actions.confirmTrust) { Text(stringResource(R.string.profile_trust_server)) }
                OutlinedButton(onClick = { invitation = ""; actions.cancelPairing() }) { Text(stringResource(R.string.profile_cancel)) }
            }
            is PairingState.AwaitingConfirmation -> {
                Text(
                    stringResource(R.string.profile_connection_confirm),
                    style = MaterialTheme.typography.titleMedium,
                )
                state.serverLabel?.let { Text(stringResource(R.string.profile_server_named, it)) }
                TrustEvidence(pairing.snapshot)
                state.accountLabel?.let { Text(stringResource(R.string.profile_account_named, it)) }
                state.deviceLabel?.let { Text(stringResource(R.string.profile_device_named, it)) }
                Text(stringResource(R.string.profile_confirm_before_exchange))
                OutlinedButton(onClick = actions.cancelPairing) {
                    Text(stringResource(R.string.profile_cancel))
                }
            }
            is PairingState.ExchangingInvitation -> PairingProgress(R.string.profile_connection_exchanging, actions.cancelPairing)
            is PairingState.Connected -> ConnectedProfile(
                state = state,
                actions = actions,
                remoteActionPending = state.pendingRemoteAction,
                requestConfirmation = { pendingConfirmation = it },
            )
            is PairingState.Blocked -> {
                Text(stringResource(R.string.profile_connection_attention), style = MaterialTheme.typography.titleMedium)
                Text(stringResource(pairing.code.failureStringRes()))
                Text(stringResource(R.string.profile_local_data_preserved))
                Button(onClick = actions.retry) { Text(stringResource(R.string.profile_retry)) }
                OutlinedButton(onClick = actions.cancelPairing) { Text(stringResource(R.string.profile_cancel)) }
            }
        }
        if (!publicFirstBindReserved) {
            state.admission?.let { AdmissionPanel(it, actions.admission) }
        }

        if (state.pairing is PairingState.AwaitingTrust && state.trustConfirmed) {
            OutlinedTextField(
                value = invitation,
                onValueChange = { invitation = it.take(4096) },
                modifier = Modifier.fillMaxWidth(),
                label = { Text(stringResource(R.string.profile_enrollment_invitation)) },
                supportingText = { Text(stringResource(R.string.profile_invitation_hint)) },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            )
            Text(stringResource(R.string.profile_invitation_sensitive_warning))
            Button(enabled = invitation.isNotBlank(), onClick = {
                actions.exchangeInvitation(invitation)
                invitation = ""
            }) {
                Text(stringResource(R.string.profile_connect_device))
            }
        }
        state.publicAccountRegistration?.takeIf { state.pairing is PairingState.NotConnected }?.let { registration ->
            PublicAccountRegistrationPanel(
                state = registration,
                invitationDocument = publicInvitationDocument,
                onInvitationDocumentChanged = { publicInvitationDocument = it.take(16_384) },
                onImport = {
                    actions.redeemPublicAccountInvitation(publicInvitationDocument)
                    publicInvitationDocument = ""
                },
                onPickDocument = actions.pickPublicAccountInvitation,
                onScanQr = actions.scanPublicAccountInvitation,
                onConfirmTrust = actions.confirmPublicAccountTrust,
                onConfirmRegistration = actions.confirmPublicAccountRegistration,
                onCancel = {
                    publicInvitationDocument = ""
                    actions.cancelPublicAccountRegistration()
                },
            )
        }
        state.ownerProvisioning?.takeIf { it.available }?.let { owner ->
            OwnerProvisioningPanel(owner, actions)
        }
    }

    if (state.pairing is PairingState.AwaitingConfirmation && state.localDataChoiceRequired && state.localDataReview == null) {
        ExistingLocalDataChoiceDialog(
            onChoice = actions.chooseLocalData,
            onReview = actions.reviewLocalData,
        )
    }

    state.localDataReview?.let { review ->
        LocalDataReviewDialog(
            review = review,
            onCancel = actions.cancelLocalDataReview,
            onApply = actions.applyLocalDataSelection,
        )
    }

    state.invitationManagement?.takeIf { it.canCreate }?.let { invitationManagement ->
        InvitationManagement(
            state = invitationManagement,
            onCreate = actions.createInvitation,
            onCancelCreated = actions.cancelCreatedInvitation,
            onDismissCreated = actions.dismissCreatedInvitation,
        )
    }

    pendingConfirmation?.let { action ->
        ProfileActionConfirmation(
            action = action,
            accountLabel = state.accountLabel,
            deviceLabel = state.deviceLabel,
            onDismiss = { pendingConfirmation = null },
            onConfirm = {
                pendingConfirmation = null
                actions.performRemoteAction(action)
            },
        )
    }
}

internal fun requiresSecureProfileWindow(
    state: ProfilePairingUiState,
    hasTypedPublicInvitation: Boolean = false,
): Boolean =
    (state.pairing is PairingState.AwaitingTrust && state.trustConfirmed) ||
        state.pairing is PairingState.AwaitingConfirmation ||
        state.pairing is PairingState.ExchangingInvitation ||
        state.invitationManagement?.createdSecret != null ||
        state.ownerProvisioning?.shownInvitation != null ||
        state.admission != null || hasTypedPublicInvitation ||
        when (state.publicAccountRegistration) {
            is PublicAccountRegistrationState.Importing,
            is PublicAccountRegistrationState.CheckingDiscovery,
            is PublicAccountRegistrationState.AwaitingTrust,
            is PublicAccountRegistrationState.AwaitingConfirmation,
            is PublicAccountRegistrationState.Redeeming,
            -> true
            else -> false
        }

@Composable
private fun PublicAccountRegistrationPanel(
    state: PublicAccountRegistrationState,
    invitationDocument: String,
    onInvitationDocumentChanged: (String) -> Unit,
    onImport: () -> Unit,
    onPickDocument: () -> Unit,
    onScanQr: () -> Unit,
    onConfirmTrust: () -> Unit,
    onConfirmRegistration: () -> Unit,
    onCancel: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = AutPlayTokens.colors.raisedSurface,
        tonalElevation = 1.dp,
    ) {
        Column(
            modifier = Modifier.padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Join an AutPlay server", style = MaterialTheme.typography.titleMedium)
            when (state) {
                PublicAccountRegistrationState.Idle,
                is PublicAccountRegistrationState.Blocked,
                -> {
                    if (state is PublicAccountRegistrationState.Blocked) {
                        Text("Registration stopped: ${state.code}", color = MaterialTheme.colorScheme.error)
                    }
                    Text("Scan the invitation QR or open an .autplayinvite file. The secret is kept only for this registration.")
                    OutlinedTextField(
                        value = invitationDocument,
                        onValueChange = onInvitationDocumentChanged,
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Account invitation document") },
                        supportingText = { Text("Manual entry is volatile and is not saved.") },
                        visualTransformation = PasswordVisualTransformation(),
                    )
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        OutlinedButton(onClick = onScanQr) { Text("Scan QR") }
                        OutlinedButton(onClick = onPickDocument) { Text("Open file") }
                    }
                    Button(
                        enabled = invitationDocument.isNotBlank(),
                        onClick = onImport,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Check invitation")
                    }
                }
                PublicAccountRegistrationState.Importing,
                is PublicAccountRegistrationState.CheckingDiscovery,
                -> PairingProgress(R.string.profile_connection_checking, onCancel)
                is PublicAccountRegistrationState.AwaitingTrust -> {
                    val trust = publicAccountTrustPresentation(state)
                    Text("Verify this server before sending the invitation secret.")
                    Text("Server: ${trust.serverLabel}")
                    Text("Identity fingerprint: ${trust.fingerprint}")
                    Text("API origin: ${trust.apiOrigin}")
                    Text("Stream origin: ${trust.streamOrigin}")
                    Text("Account: ${trust.accountDisplayName}")
                    Text("Role: ${trust.accountRole}")
                    Text("Invitation expires: ${trust.expiresAt}")
                    Button(onClick = onConfirmTrust) { Text("Trust this server") }
                    OutlinedButton(onClick = onCancel) { Text("Cancel") }
                }
                is PublicAccountRegistrationState.AwaitingConfirmation -> {
                    Text("Create account “${state.invitation.accountDisplayName}” and bind this phone as its first device?")
                    Text("PA2 has no recovery or additional-device flow. Keep this device and its app data safe.")
                    Button(onClick = onConfirmRegistration) { Text("Create account and connect") }
                    OutlinedButton(onClick = onCancel) { Text("Cancel") }
                }
                PublicAccountRegistrationState.Redeeming -> {
                    Text(stringResource(R.string.profile_connection_exchanging), style = MaterialTheme.typography.titleMedium)
                    Text("Keep AutPlay open while the first account, device, and session are committed.")
                }
                PublicAccountRegistrationState.Connected -> Text("Account created and connected.")
            }
        }
    }
}

@Composable
private fun OwnerProvisioningPanel(
    state: OwnerProvisioningUiState,
    actions: ProfilePairingActions,
) {
    var displayName by rememberSaveable { mutableStateOf("") }
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        color = AutPlayTokens.colors.raisedSurface,
        tonalElevation = 1.dp,
    ) {
        Column(
            modifier = Modifier.padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Invite friends", style = MaterialTheme.typography.titleMedium)
            Text("Each invitation creates one USER account and binds only its first Android device.")
            state.errorCode?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            OutlinedTextField(
                value = displayName,
                onValueChange = { displayName = it.take(120) },
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Friend account name") },
                singleLine = true,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(
                    enabled = !state.busy && displayName.isNotBlank(),
                    onClick = {
                        actions.createOwnerAccountInvitation(displayName, 1_800)
                        displayName = ""
                    },
                ) { Text("Create 30-minute invitation") }
                OutlinedButton(enabled = !state.busy, onClick = actions.refreshOwnerProvisioning) {
                    Text("Refresh")
                }
            }
            state.invitations.forEach { invitation ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("${invitation.displayName} · ${invitation.state}", modifier = Modifier.weight(1f))
                    if (invitation.state == "ACTIVE") {
                        OutlinedButton(
                            enabled = !state.busy,
                            onClick = { actions.cancelOwnerAccountInvitation(invitation.invitationId) },
                        ) { Text("Cancel") }
                    }
                }
            }
            state.accounts.forEach { account ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("${account.displayName} · ${account.status}", modifier = Modifier.weight(1f))
                    if (account.status == "ACTIVE") {
                        OutlinedButton(
                            enabled = !state.busy,
                            onClick = { actions.disableProvisionedAccount(account.userId) },
                        ) { Text("Disable") }
                    }
                }
            }
            state.shownInvitation?.let { invitation ->
                val document = remember(invitation.invitationId) { invitation.toDocumentJson() }
                Text("Shown once. Share directly or let your friend scan this QR; do not copy it into a URL.")
                InvitationQrCode(document)
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Button(onClick = { actions.shareOwnerAccountInvitation(invitation) }) { Text("Share invitation") }
                    OutlinedButton(onClick = actions.dismissOwnerAccountInvitation) { Text("Done") }
                }
            }
        }
    }
}

@Composable
private fun PersonalServerHero(state: ProfilePairingUiState) {
    val connected = state.pairing is PairingState.Connected
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        color = if (connected) {
            MaterialTheme.colorScheme.primaryContainer
        } else {
            AutPlayTokens.colors.softAccent
        },
    ) {
        Row(
            modifier = Modifier.padding(20.dp),
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Surface(
                shape = CircleShape,
                color = MaterialTheme.colorScheme.surface.copy(alpha = 0.78f),
            ) {
                AutPlayPlatformIcon(
                    icon = AutPlayIcon.Server,
                    contentDescription = null,
                    modifier = Modifier.padding(12.dp).size(28.dp),
                    tint = MaterialTheme.colorScheme.primary,
                )
            }
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    stringResource(R.string.profile_personal_server),
                    style = MaterialTheme.typography.titleLarge,
                )
                Text(
                    stringResource(
                        if (connected) R.string.profile_connection_connected else R.string.profile_connection_local,
                    ),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun TrustEvidence(snapshot: app.autplay.application.profilepairing.PairingFlowSnapshot) {
    Text(stringResource(R.string.profile_confirmed_origin, snapshot.apiOrigin))
    Text(
        stringResource(
            R.string.profile_identity_fingerprint,
            snapshot.expectedIdentityThumbprintSha256.chunked(4).joinToString(" "),
        ),
    )
}

/**
 * Temporarily protects the one-time enrollment bearer and an issuer's shown-once secret from
 * screenshots/recents previews. A pre-existing FLAG_SECURE is preserved for its original owner.
 */
@Composable
private fun SecureWindowWhileVisible(enabled: Boolean) {
    val window = LocalContext.current.findActivity()?.window
    DisposableEffect(window, enabled) {
        if (!enabled || window == null) return@DisposableEffect onDispose {}
        val flag = WindowManager.LayoutParams.FLAG_SECURE
        val alreadySecure = window.attributes.flags and flag != 0
        window.addFlags(flag)
        onDispose { if (!alreadySecure) window.clearFlags(flag) }
    }
}

private fun Context.findActivity(): Activity? = when (this) {
    is Activity -> this
    is ContextWrapper -> baseContext.findActivity()
    else -> null
}

@Composable
private fun ExistingLocalDataChoiceDialog(
    onChoice: (ExistingLocalDataChoice) -> Unit,
    onReview: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = { onChoice(ExistingLocalDataChoice.CANCEL) },
        title = { Text(stringResource(R.string.profile_local_data_choice_title)) },
        text = { Text(stringResource(R.string.profile_local_data_choice_body)) },
        confirmButton = {
            Button(onClick = onReview) {
                Text(stringResource(R.string.profile_review_and_connect))
            }
        },
        dismissButton = {
            Column {
                OutlinedButton(onClick = { onChoice(ExistingLocalDataChoice.KEEP_ON_PHONE) }) {
                    Text(stringResource(R.string.profile_keep_on_phone))
                }
                OutlinedButton(onClick = { onChoice(ExistingLocalDataChoice.CANCEL) }) {
                    Text(stringResource(R.string.profile_cancel))
                }
            }
        },
    )
}

@Composable
private fun LocalDataReviewDialog(
    review: LocalDataReviewUiState,
    onCancel: () -> Unit,
    onApply: (List<String>) -> Unit,
) {
    var selectedIds by remember(review.items) { mutableStateOf(emptySet<String>()) }
    var confirmationSelection by remember(review.items) { mutableStateOf<List<String>?>(null) }
    var applyRequested by remember(review.items) { mutableStateOf(false) }
    var observedRuntimeApply by remember(review.items) { mutableStateOf(false) }
    LaunchedEffect(review.applying) {
        if (review.applying) {
            observedRuntimeApply = true
        } else if (observedRuntimeApply) {
            // The application reports terminal success/failure by clearing its pending state.
            applyRequested = false
            observedRuntimeApply = false
        }
    }
    val applying = review.applying || applyRequested

    AlertDialog(
        onDismissRequest = { if (!applying) onCancel() },
        title = { Text(stringResource(R.string.profile_local_data_review_title)) },
        text = {
            Column(verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp)) {
                Text(stringResource(R.string.profile_local_data_review_body))
                review.items.forEach { item ->
                    val selected = item.localChangeId in selectedIds
                    OutlinedButton(
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !applying,
                        onClick = {
                            selectedIds = if (selected) selectedIds - item.localChangeId else selectedIds + item.localChangeId
                        },
                    ) {
                        val stateLabel = if (selected) R.string.profile_local_data_selected else R.string.profile_local_data_not_selected
                        Text("${item.eventType} · ${item.occurredAtMs} · ${stringResource(stateLabel)}")
                    }
                }
            }
        },
        confirmButton = {
            Button(
                enabled = selectedIds.isNotEmpty() && !applying,
                onClick = { confirmationSelection = selectedIds.toList() },
            ) { Text(stringResource(R.string.profile_apply_selected_changes)) }
        },
        dismissButton = {
            OutlinedButton(enabled = !applying, onClick = onCancel) { Text(stringResource(R.string.profile_cancel)) }
        },
    )

    confirmationSelection?.let { selection ->
        AlertDialog(
            onDismissRequest = { if (!applying) confirmationSelection = null },
            title = { Text(stringResource(R.string.profile_apply_local_data_title)) },
            text = {
                Text(
                    pluralStringResource(
                        R.plurals.profile_apply_local_data_body,
                        selection.size,
                        selection.size,
                    ),
                )
            },
            confirmButton = {
                Button(
                    enabled = !applying,
                    onClick = {
                        applyRequested = true
                        confirmationSelection = null
                        onApply(selection)
                    },
                ) { Text(stringResource(R.string.profile_apply)) }
            },
            dismissButton = {
                OutlinedButton(enabled = !applying, onClick = { confirmationSelection = null }) {
                    Text(stringResource(R.string.profile_cancel))
                }
            },
        )
    }
}

@Composable
private fun InvitationManagement(
    state: InvitationManagementUiState,
    onCreate: (Int) -> Unit,
    onCancelCreated: () -> Unit,
    onDismissCreated: () -> Unit,
) {
    var expiryInput by remember(state.minExpiryMinutes, state.maxExpiryMinutes) {
        mutableStateOf(state.minExpiryMinutes.toString())
    }
    val expiry = expiryInput.toIntOrNull()
    val validExpiry = expiry?.takeIf { it in state.minExpiryMinutes..state.maxExpiryMinutes }
    val busy = state.creating || state.cancelling

    Column(verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.profile_invitation_management_title), style = MaterialTheme.typography.titleMedium)
        Text(stringResource(R.string.profile_invitation_management_body))
        OutlinedTextField(
            value = expiryInput,
            onValueChange = { expiryInput = it.filter(Char::isDigit).take(6) },
            label = { Text(stringResource(R.string.profile_invitation_expiry_minutes)) },
            supportingText = {
                Text(stringResource(R.string.profile_invitation_expiry_bounds, state.minExpiryMinutes, state.maxExpiryMinutes))
            },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        )
        Button(enabled = validExpiry != null && !busy, onClick = { onCreate(requireNotNull(validExpiry)) }) {
            Text(stringResource(R.string.profile_create_invitation))
        }
    }

    state.createdSecret?.let { secret ->
        AlertDialog(
            onDismissRequest = { if (!busy) onDismissCreated() },
            title = { Text(stringResource(R.string.profile_invitation_created_title)) },
            text = {
                Column(verticalArrangement = androidx.compose.foundation.layout.Arrangement.spacedBy(8.dp)) {
                    // Text is intentionally non-selectable and has no secret accessibility semantics.
                    Text(
                        text = secret,
                        modifier = Modifier.clearAndSetSemantics {
                            contentDescription = CREATED_INVITATION_SECRET_DESCRIPTION
                        },
                    )
                    InvitationQrCode(secret)
                    Text(stringResource(R.string.profile_invitation_created_warning))
                }
            },
            confirmButton = {
                Button(enabled = !busy, onClick = onDismissCreated) {
                    Text(stringResource(R.string.profile_done))
                }
            },
            dismissButton = {
                OutlinedButton(enabled = !busy, onClick = onCancelCreated) {
                    Text(stringResource(R.string.profile_cancel_invitation))
                }
            },
        )
    }
}

private const val CREATED_INVITATION_SECRET_DESCRIPTION = "One-time invitation secret"

@Composable
private fun PairingProgress(message: Int, onCancel: () -> Unit) {
    Text(stringResource(message), style = MaterialTheme.typography.titleMedium)
    Text(stringResource(R.string.profile_local_data_preserved))
    OutlinedButton(onClick = onCancel) { Text(stringResource(R.string.profile_cancel)) }
}

@Composable
private fun ConnectedProfile(
    state: ProfilePairingUiState,
    actions: ProfilePairingActions,
    remoteActionPending: ProfileRemoteAction?,
    requestConfirmation: (ProfileRemoteAction) -> Unit,
) {
    Text(stringResource(if (state.pendingSyncCount > 0) R.string.profile_connection_sync_pending else R.string.profile_connection_connected), style = MaterialTheme.typography.titleMedium)
    state.accountLabel?.let { Text(stringResource(R.string.profile_account_named, it)) }
    state.deviceLabel?.let { Text(stringResource(R.string.profile_device_named, it)) }
    if (state.pendingSyncCount > 0) {
        Text(
            pluralStringResource(
                R.plurals.profile_sync_pending_count,
                state.pendingSyncCount,
                state.pendingSyncCount,
            ),
        )
        Button(onClick = actions.openSync) { Text(stringResource(R.string.profile_open_sync)) }
    }
    if (state.devices.isNotEmpty()) {
        Text(stringResource(R.string.profile_connected_devices), style = MaterialTheme.typography.titleMedium)
        state.devices.forEach { Text(it) }
    }
    if (state.sessions.isNotEmpty()) {
        Text(stringResource(R.string.profile_connected_sessions), style = MaterialTheme.typography.titleMedium)
        state.sessions.forEach { Text(it) }
    }
    Text(stringResource(R.string.profile_local_data_preserved))
    if (state.canReenrollTrustedDevice) {
        OutlinedButton(
            enabled = remoteActionPending == null,
            onClick = actions.reenrollTrustedDevice,
        ) { Text("Re-enroll trusted device") }
    }
    ProfileActionButton(ProfileRemoteAction.LOGOUT_CURRENT, remoteActionPending, requestConfirmation)
    ProfileActionButton(ProfileRemoteAction.LOGOUT_ALL, remoteActionPending, requestConfirmation)
    ProfileActionButton(ProfileRemoteAction.REVOKE_CURRENT_DEVICE, remoteActionPending, requestConfirmation)
    ProfileActionButton(ProfileRemoteAction.DISCONNECT_LOCAL, remoteActionPending, requestConfirmation)
}

@Composable
private fun ProfileActionButton(action: ProfileRemoteAction, pending: ProfileRemoteAction?, onClick: (ProfileRemoteAction) -> Unit) {
    OutlinedButton(enabled = pending == null, onClick = { onClick(action) }) {
        Text(stringResource(action.labelRes()))
    }
}

@Composable
private fun ProfileActionConfirmation(
    action: ProfileRemoteAction,
    accountLabel: String?,
    deviceLabel: String?,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(action.confirmTitleRes())) },
        text = {
            Column {
                Text(stringResource(action.confirmBodyRes()))
                accountLabel?.let { Text(stringResource(R.string.profile_account_named, it)) }
                deviceLabel?.let { Text(stringResource(R.string.profile_device_named, it)) }
                Text(stringResource(R.string.profile_local_data_preserved))
            }
        },
        confirmButton = { Button(onClick = onConfirm) { Text(stringResource(R.string.profile_confirm)) } },
        dismissButton = { OutlinedButton(onClick = onDismiss) { Text(stringResource(R.string.profile_cancel)) } },
    )
}

private fun PairingFailure.failureStringRes(): Int = when (this) {
    PairingFailure.SERVER_UNAVAILABLE -> R.string.profile_server_unavailable
    PairingFailure.AUTH_ATTENTION_REQUIRED -> R.string.profile_auth_attention
    PairingFailure.INCOMPATIBLE_API_MAJOR, PairingFailure.CAPABILITY_ROLLBACK_DETECTED -> R.string.profile_incompatible
    PairingFailure.SERVER_IDENTITY_CHANGED -> R.string.profile_identity_changed
    PairingFailure.STALE_FLOW_GENERATION -> R.string.profile_flow_replaced
}

private fun ProfileRemoteAction.labelRes(): Int = when (this) {
    ProfileRemoteAction.LOGOUT_CURRENT -> R.string.profile_logout
    ProfileRemoteAction.LOGOUT_ALL -> R.string.profile_logout_all
    ProfileRemoteAction.REVOKE_CURRENT_DEVICE -> R.string.profile_revoke_device
    ProfileRemoteAction.DISCONNECT_LOCAL -> R.string.profile_disconnect
}

private fun ProfileRemoteAction.confirmTitleRes(): Int = when (this) {
    ProfileRemoteAction.LOGOUT_CURRENT -> R.string.profile_confirm_logout_title
    ProfileRemoteAction.LOGOUT_ALL -> R.string.profile_confirm_logout_all_title
    ProfileRemoteAction.REVOKE_CURRENT_DEVICE -> R.string.profile_confirm_revoke_title
    ProfileRemoteAction.DISCONNECT_LOCAL -> R.string.profile_confirm_disconnect_title
}

private fun ProfileRemoteAction.confirmBodyRes(): Int = when (this) {
    ProfileRemoteAction.LOGOUT_CURRENT -> R.string.profile_confirm_logout_body
    ProfileRemoteAction.LOGOUT_ALL -> R.string.profile_confirm_logout_all_body
    ProfileRemoteAction.REVOKE_CURRENT_DEVICE -> R.string.profile_confirm_revoke_body
    ProfileRemoteAction.DISCONNECT_LOCAL -> R.string.profile_confirm_disconnect_body
}
