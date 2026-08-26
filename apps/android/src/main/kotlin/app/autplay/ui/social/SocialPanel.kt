package app.autplay.ui.social

import app.autplay.R
import app.autplay.application.social.AggregatePresence
import app.autplay.application.social.ContactCard
import app.autplay.application.social.FriendSummary
import app.autplay.application.social.FriendshipStatus
import app.autplay.application.social.PresenceSettings
import app.autplay.application.social.RoomInvitationStatus
import app.autplay.application.social.SocialRuntimeState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import app.autplay.ui.statistics.FriendProfileStatisticsCard

/** UI-only S1C panel. It only receives volatile state and delegates every command to the runtime. */
data class SocialActions(
    val refresh: () -> Unit = {},
    val createContactCard: () -> Unit = {},
    val shareContactCard: (ContactCard) -> Unit = {},
    val importContactCard: (String) -> Unit = {},
    val acceptFriend: (String) -> Unit = {},
    val declineFriend: (String) -> Unit = {},
    val cancelFriendRequest: (String) -> Unit = {},
    val removeFriend: (String) -> Unit = {},
    val block: (String) -> Unit = {},
    val unblock: (String) -> Unit = {},
    val setPresence: (PresenceSettings) -> Unit = {},
    val setProfileStatisticsVisibility: (Boolean) -> Unit = {},
    val viewFriendStatistics: (String) -> Unit = {},
    val closeFriendStatistics: () -> Unit = {},
    val inviteFriend: (String) -> Unit = {},
    val acceptInvitation: (String) -> Unit = {},
    val cancelInvitation: (String) -> Unit = {},
)

@Composable
fun SocialPanel(state: SocialRuntimeState, actions: SocialActions, modifier: Modifier = Modifier) {
    var cardText by rememberSaveable { mutableStateOf("") }
    val settings = state.snapshot.presence
    Column(modifier = modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(stringResource(R.string.social_friends), style = MaterialTheme.typography.headlineSmall)
        OutlinedButton(onClick = actions.refresh, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_refresh)) }
        if (state.loading) Text(stringResource(R.string.social_updating))
        state.errorCode?.let { Text(socialErrorText(it), color = MaterialTheme.colorScheme.error) }
        OutlinedButton(onClick = actions.createContactCard, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_create_contact_card)) }
        state.contactCard?.let { card ->
            Text(stringResource(R.string.social_contact_card_ready))
            Button(onClick = { actions.shareContactCard(card) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_share_contact_card)) }
        }
        OutlinedTextField(value = cardText, onValueChange = { cardText = it.take(4096) }, modifier = Modifier.fillMaxWidth(), label = { Text(stringResource(R.string.social_friend_contact_card)) }, singleLine = false)
        val addFriendDescription = stringResource(R.string.social_add_friend_from_contact_card)
        Button(enabled = cardText.isNotBlank() && !state.loading, onClick = { actions.importContactCard(cardText) }, modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = addFriendDescription }) { Text(stringResource(R.string.social_add_friend)) }
        PresenceToggles(settings, actions.setPresence)
        if (state.snapshot.friends.isEmpty()) Text(stringResource(R.string.social_no_friends))
        state.snapshot.friends.forEach { friend -> FriendRow(friend, actions) }
        FriendProfileStatisticsCard(state.friendStatistics, actions.closeFriendStatistics)
        if (state.snapshot.receivedInvitations.isNotEmpty() || state.snapshot.sentInvitations.isNotEmpty()) {
            Text(stringResource(R.string.social_wave_invitations), style = MaterialTheme.typography.titleMedium)
            state.snapshot.receivedInvitations.forEach { invitation ->
                val status = invitation.status
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column(Modifier.weight(1f)) { Text(stringResource(R.string.social_wave_invite)); Text(invitationLabel(status), style = MaterialTheme.typography.bodySmall) }
                    if (status == RoomInvitationStatus.PENDING) Button(onClick = { actions.acceptInvitation(invitation.invitationId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_join)) }
                }
            }
            state.snapshot.sentInvitations.forEach { invitation ->
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text(stringResource(R.string.social_sent_wave_invite, invitationLabel(invitation.status)), modifier = Modifier.weight(1f))
                    if (invitation.status == RoomInvitationStatus.PENDING) OutlinedButton(onClick = { actions.cancelInvitation(invitation.invitationId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_cancel)) }
                }
            }
        }
        state.acceptedRoomId?.let { Text(stringResource(R.string.social_joined_wave_room)) }
    }
}

@Composable private fun PresenceToggles(settings: PresenceSettings, update: (PresenceSettings) -> Unit) {
    Text(stringResource(R.string.social_presence_private_default), style = MaterialTheme.typography.titleMedium)
    Toggle(stringResource(R.string.social_presence_visible), settings.friendsCanSeePresence) { update(settings.copy(friendsCanSeePresence = it)) }
    Toggle(stringResource(R.string.social_share_wave_activity), settings.shareRoomActivity) { update(settings.copy(shareRoomActivity = it)) }
    Toggle(stringResource(R.string.social_available_for_wave_invites), settings.availableToInvite) { update(settings.copy(availableToInvite = it)) }
}
@Composable private fun Toggle(label: String, checked: Boolean, update: (Boolean) -> Unit) = Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text(label, modifier = Modifier.weight(1f)); Switch(checked = checked, onCheckedChange = update, modifier = Modifier.heightIn(min = 48.dp).semantics { contentDescription = label }) }
@Composable private fun FriendRow(friend: FriendSummary, actions: SocialActions) = Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
    Column(Modifier.weight(1f)) { Text(friend.displayNameHint ?: stringResource(R.string.social_friend)); Text(friendshipLabel(friend.status, friend.presence), style = MaterialTheme.typography.bodySmall) }
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        when (friend.status) {
            FriendshipStatus.PENDING_INBOUND -> { Button(onClick = { actions.acceptFriend(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_accept)) }; OutlinedButton(onClick = { actions.declineFriend(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_decline)) } }
            FriendshipStatus.PENDING_OUTBOUND -> OutlinedButton(onClick = { actions.cancelFriendRequest(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_cancel_request)) }
            FriendshipStatus.FRIEND -> { OutlinedButton(onClick = { actions.viewFriendStatistics(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.statistics_view)) }; if (friend.presence == AggregatePresence.AVAILABLE_TO_INVITE) Button(onClick = { actions.inviteFriend(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_invite_to_wave)) }; OutlinedButton(onClick = { actions.removeFriend(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_remove)) }; OutlinedButton(onClick = { actions.block(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_block)) } }
            FriendshipStatus.BLOCKED -> OutlinedButton(onClick = { actions.unblock(friend.accountId) }, modifier = Modifier.heightIn(min = 48.dp)) { Text(stringResource(R.string.social_unblock)) }
        }
    }
}
@Composable private fun friendshipLabel(status: FriendshipStatus, presence: AggregatePresence) = stringResource(when (status) { FriendshipStatus.PENDING_INBOUND -> R.string.social_friend_request_received; FriendshipStatus.PENDING_OUTBOUND -> R.string.social_request_pending; FriendshipStatus.BLOCKED -> R.string.social_blocked; FriendshipStatus.FRIEND -> when (presence) { AggregatePresence.OFFLINE -> R.string.social_offline; AggregatePresence.ONLINE -> R.string.social_online; AggregatePresence.AVAILABLE_TO_INVITE -> R.string.social_available_to_invite; AggregatePresence.IN_ROOM -> R.string.social_in_wave_room } })
@Composable private fun invitationLabel(status: RoomInvitationStatus) = stringResource(when (status) { RoomInvitationStatus.PENDING -> R.string.social_ready_to_join; RoomInvitationStatus.ACCEPTED -> R.string.social_accepted; RoomInvitationStatus.EXPIRED -> R.string.social_invitation_expired; RoomInvitationStatus.CANCELLED -> R.string.social_invitation_cancelled; RoomInvitationStatus.FULL -> R.string.social_room_full; RoomInvitationStatus.UNAVAILABLE -> R.string.social_invitation_unavailable })
@Composable private fun socialErrorText(code: String) = stringResource(when (code) { "room_full" -> R.string.social_error_room_full; "room_changed" -> R.string.social_error_room_changed; "presence_private" -> R.string.social_error_presence_private; "friendship_required" -> R.string.social_error_friendship_required; "user_blocked" -> R.string.social_error_unavailable; "auth_attention_required" -> R.string.social_error_reconnect; else -> R.string.social_error_generic })
