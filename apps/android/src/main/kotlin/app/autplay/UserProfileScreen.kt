package app.autplay

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.autplay.application.profilepairing.PairingState
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayTokens
import app.autplay.ui.PreferenceSection
import app.autplay.ui.UiDestination
import app.autplay.ui.profilepairing.publicRegistrationBlocksOrdinaryFirstBind
import app.autplay.ui.profilepairing.ProfilePairingUiState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.ui.social.SocialPanel
import app.autplay.ui.statistics.OwnerProfileStatisticsCard

internal fun profileRequiresConnectionAttention(profile: ProfilePairingUiState): Boolean {
    val connected = profile.pairing is PairingState.Connected
    return profile.pairing !in listOf(PairingState.NotConnected, PairingState.Cancelled) && !connected ||
        publicRegistrationBlocksOrdinaryFirstBind(profile.publicAccountRegistration) ||
        profile.publicAccountRegistration is PublicAccountRegistrationState.Blocked ||
        profile.localDataChoiceRequired || profile.localDataReview != null ||
        profile.admission?.admission?.let {
            it !in listOf(
                app.autplay.application.profilepairing.AdmissionState.RequestReady,
                app.autplay.application.profilepairing.AdmissionState.Connected,
                app.autplay.application.profilepairing.AdmissionState.Cancelled,
            )
        } == true ||
        profile.invitationManagement?.createdSecret != null || profile.ownerProvisioning?.shownInvitation != null
}

@Composable
internal fun UserProfileScreen(state: LegacySecondaryRouteState, actions: LegacySecondaryRouteActions) {
    val profile = state.profilePairing
    val connected = profile.pairing is PairingState.Connected
    val activeFlow = profileRequiresConnectionAttention(profile)
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.primaryContainer) {
            Row(Modifier.fillMaxWidth().padding(22.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surface) {
                    AutPlayPlatformIcon(AutPlayIcon.Profile, null, Modifier.padding(16.dp).size(30.dp))
                }
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(profile.accountLabel ?: stringResource(R.string.profile_my_music), style = MaterialTheme.typography.headlineSmall)
                    Text(
                        if (connected) profile.serverLabel ?: stringResource(R.string.profile_connection_connected)
                        else stringResource(R.string.profile_connection_local),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
        if (activeFlow) ProfileFrontendScreen(profile, actions.profilePairing)
        OwnerProfileStatisticsCard(state.ownerStatistics)
        PreferenceSection(stringResource(R.string.profile_friends), AutPlayIcon.Wave, stringResource(R.string.profile_friends_summary)) {
            if (state.socialAvailable) SocialPanel(state.social, actions.social)
            else Text(stringResource(R.string.profile_friends_local), color = AutPlayTokens.colors.mutedText)
        }
        if (!activeFlow) PreferenceSection(
            stringResource(if (connected) R.string.profile_account_devices else R.string.profile_connect_optional),
            AutPlayIcon.Server,
            stringResource(if (connected) R.string.profile_account_devices_summary else R.string.profile_connect_optional_summary),
        ) {
            ProfileFrontendScreen(profile, actions.profilePairing)
        }
        OutlinedButton(onClick = { actions.navigate(UiDestination.Settings) }, modifier = Modifier.fillMaxWidth()) {
            Text(stringResource(R.string.nav_settings))
        }
    }
}
