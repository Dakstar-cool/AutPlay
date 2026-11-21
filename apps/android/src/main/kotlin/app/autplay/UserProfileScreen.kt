package app.autplay

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import app.autplay.application.profilepairing.PairingState
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayTokens
import app.autplay.ui.PreferenceSection
import app.autplay.ui.profilepairing.publicRegistrationBlocksOrdinaryFirstBind
import app.autplay.ui.profilepairing.ProfilePairingUiState
import app.autplay.application.publicaccess.PublicAccountRegistrationState
import app.autplay.ui.social.SocialPanel
import app.autplay.ui.social.SocialPublicIdPanel
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
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(AutPlayTokens.dimensions.sectionSpacing)) {
        PreferenceSection(stringResource(R.string.profile_friends), AutPlayIcon.Wave, stringResource(R.string.profile_friends_summary)) {
            if (state.socialAvailable) {
                SocialPanel(state.social, actions.social)
            } else {
                Text(stringResource(R.string.profile_friends_local), color = AutPlayTokens.colors.mutedText)
                SocialPublicIdPanel(state.social, actions.social, serverAvailable = false)
            }
        }
        OwnerProfileStatisticsCard(state.ownerStatisticsState, actions.refreshOwnerStatistics)
        if (connected && app.autplay.ui.LocalDeveloperMode.current) {
            SonaCollectionStatistics(state.settings, state.trainingConsent)
        }
    }
}
