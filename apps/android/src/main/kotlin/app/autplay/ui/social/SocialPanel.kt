package app.autplay.ui.social

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.R
import app.autplay.application.social.AggregatePresence
import app.autplay.application.social.ContactCard
import app.autplay.application.social.FriendSummary
import app.autplay.application.social.FriendshipStatus
import app.autplay.application.social.PresenceSettings
import app.autplay.application.social.PublicIdLookupState
import app.autplay.application.social.RoomInvitationStatus
import app.autplay.application.social.SocialRuntimeState
import app.autplay.application.social.normalizeSocialPublicId
import app.autplay.application.social.parseSocialContactCardInput
import app.autplay.ui.AutPlayCard
import app.autplay.ui.AutPlayIcon
import app.autplay.ui.AutPlayPlatformIcon
import app.autplay.ui.AutPlayTokens
import app.autplay.ui.statistics.FriendProfileStatisticsCard

/** Volatile server views and explicit user actions; no local graph or inferred friendship. */
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
    /** Persist the normalized candidate before trying a server registration. */
    val submitPublicId: (String) -> Unit = {},
    val lookupPublicId: (String) -> Unit = {},
    val clearPublicIdLookup: () -> Unit = {},
    val sendFoundFriendRequest: () -> Unit = {},
)

@Composable
fun SocialPanel(state: SocialRuntimeState, actions: SocialActions, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Text(stringResource(R.string.ui_friends_refresh_mutual_only), color = AutPlayTokens.colors.mutedText, style = MaterialTheme.typography.bodyMedium)
        OutlinedButton(onClick = actions.refresh, enabled = !state.loading, modifier = Modifier.touchTarget()) {
            Text(stringResource(R.string.social_refresh))
        }
        if (state.loading) LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
        state.errorCode?.let {
            Text(socialErrorText(it), color = MaterialTheme.colorScheme.error, modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite })
        }
        SocialPublicIdPanel(state, actions)
        PublicIdSearch(state, actions)
        val groups = state.snapshot.friends.groupBy { it.status }
        FriendsGroup(
            stringResource(R.string.ui_friends_refresh_incoming, groups[FriendshipStatus.PENDING_INBOUND].orEmpty().size),
            groups[FriendshipStatus.PENDING_INBOUND].orEmpty(), state, actions,
            "friends-incoming", R.string.ui_friends_refresh_no_incoming,
        )
        FriendsGroup(
            stringResource(R.string.ui_friends_refresh_outgoing, groups[FriendshipStatus.PENDING_OUTBOUND].orEmpty().size),
            groups[FriendshipStatus.PENDING_OUTBOUND].orEmpty(), state, actions,
            "friends-outgoing", R.string.ui_friends_refresh_no_outgoing,
        )
        FriendsGroup(
            stringResource(R.string.ui_friends_refresh_confirmed, groups[FriendshipStatus.FRIEND].orEmpty().size),
            groups[FriendshipStatus.FRIEND].orEmpty(), state, actions,
            "friends-confirmed", R.string.social_no_friends,
        )
        FriendProfileStatisticsCard(state.friendStatistics, actions.closeFriendStatistics)
        ContactCardTools(state, actions)
        AutPlayCard {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                SectionTitle(stringResource(R.string.ui_friends_refresh_privacy))
                PresenceToggles(state.snapshot.presence, !state.loading && state.snapshotLoaded, actions.setPresence)
            }
        }
        val blocked = groups[FriendshipStatus.BLOCKED].orEmpty()
        if (blocked.isNotEmpty()) FriendsGroup(
            stringResource(R.string.ui_friends_refresh_blocked, blocked.size), blocked, state, actions,
            "friends-blocked", R.string.ui_friends_refresh_no_incoming,
        )
        WaveInvitations(state, actions)
    }
}

@Composable
private fun PublicIdSearch(state: SocialRuntimeState, actions: SocialActions) {
    var query by rememberSaveable { mutableStateOf("") }
    val normalized = normalizeSocialPublicId(query)
    AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SectionTitle(stringResource(R.string.ui_friends_refresh_find_title))
            Text(stringResource(R.string.ui_friends_refresh_same_server), color = AutPlayTokens.colors.mutedText, style = MaterialTheme.typography.bodySmall)
            OutlinedTextField(
                value = query,
                onValueChange = { query = it.take(64); actions.clearPublicIdLookup() },
                label = { Text(stringResource(R.string.ui_friends_refresh_exact_id)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                isError = query.isNotBlank() && normalized == null,
            )
            Button(
                onClick = { normalized?.let(actions.lookupPublicId) },
                enabled = normalized != null && state.publicIdLookup !is PublicIdLookupState.Loading && !state.loading,
                modifier = Modifier.touchTarget(),
            ) { Text(stringResource(R.string.ui_friends_refresh_find)) }
            when (val result = state.publicIdLookup) {
                PublicIdLookupState.Idle -> Unit
                is PublicIdLookupState.Loading -> Text(stringResource(R.string.ui_friends_refresh_searching))
                is PublicIdLookupState.Unavailable -> Text(
                    stringResource(
                        when (result.errorCode) {
                            "rate_limited" -> R.string.ui_friends_refresh_rate_limited
                            "auth_attention_required" -> R.string.social_error_reconnect
                            "server_unavailable" -> R.string.ui_friends_refresh_lookup_connection_required
                            else -> R.string.ui_friends_refresh_lookup_unavailable
                        },
                    ),
                    color = AutPlayTokens.colors.mutedText,
                )
                is PublicIdLookupState.Found -> if (normalized == result.publicId) {
                    HorizontalDivider(color = AutPlayTokens.colors.border)
                    Text("@${result.publicId}", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
                    Text(result.contactCard.displayNameHint, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    val relationships = state.snapshot.friends.filter { it.accountId == result.contactCard.accountId }
                    when {
                        relationships.any { it.status == FriendshipStatus.FRIEND } -> Text(stringResource(R.string.ui_friends_refresh_already_friends))
                        relationships.any { it.status == FriendshipStatus.PENDING_OUTBOUND } -> Text(stringResource(R.string.social_request_pending))
                        relationships.any { it.status == FriendshipStatus.PENDING_INBOUND } -> Text(stringResource(R.string.ui_friends_refresh_incoming_below))
                        relationships.any { it.status == FriendshipStatus.BLOCKED } -> Text(stringResource(R.string.ui_friends_refresh_lookup_unavailable))
                        else -> Button(onClick = actions.sendFoundFriendRequest, enabled = !state.loading, modifier = Modifier.touchTarget()) {
                            Text(stringResource(R.string.ui_friends_refresh_send_request))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun FriendsGroup(title: String, people: List<FriendSummary>, state: SocialRuntimeState, actions: SocialActions, tag: String, emptyRes: Int) {
    AutPlayCard(Modifier.testTag(tag)) {
        Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
            SectionTitle(title)
            if (people.isEmpty()) Text(
                stringResource(if (state.snapshotLoaded) emptyRes else R.string.ui_friends_refresh_not_loaded),
                color = AutPlayTokens.colors.mutedText,
                style = MaterialTheme.typography.bodyMedium,
            )
            people.forEachIndexed { index, person ->
                if (index > 0) HorizontalDivider(color = AutPlayTokens.colors.border)
                FriendRow(person, actions, !state.loading)
            }
        }
    }
}

@Composable
private fun FriendRow(friend: FriendSummary, actions: SocialActions, enabled: Boolean) {
    var confirmation by remember(friend.accountId) { mutableStateOf<FriendshipConfirmation?>(null) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(shape = MaterialTheme.shapes.medium, color = AutPlayTokens.colors.raisedSurface) {
                AutPlayPlatformIcon(AutPlayIcon.Profile, null, Modifier.size(40.dp))
            }
            Column(Modifier.weight(1f)) {
                Text(friend.displayNameHint ?: stringResource(R.string.social_friend), style = MaterialTheme.typography.titleMedium, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(friendshipLabel(friend.status, friend.presence), style = MaterialTheme.typography.bodySmall, color = AutPlayTokens.colors.mutedText)
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            when (friend.status) {
                FriendshipStatus.PENDING_INBOUND -> {
                    Button(onClick = { actions.acceptFriend(friend.accountId) }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_accept)) }
                    OutlinedButton(onClick = { actions.declineFriend(friend.accountId) }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_decline)) }
                }
                FriendshipStatus.PENDING_OUTBOUND -> OutlinedButton(onClick = { actions.cancelFriendRequest(friend.accountId) }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_cancel_request)) }
                FriendshipStatus.FRIEND -> {
                    OutlinedButton(onClick = { actions.viewFriendStatistics(friend.accountId) }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.statistics_view)) }
                    if (friend.presence == AggregatePresence.AVAILABLE_TO_INVITE) Button(onClick = { actions.inviteFriend(friend.accountId) }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_invite_to_wave)) }
                    TextButton(onClick = { confirmation = FriendshipConfirmation.Remove }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_remove)) }
                    TextButton(onClick = { confirmation = FriendshipConfirmation.Block }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_block)) }
                }
                FriendshipStatus.BLOCKED -> OutlinedButton(onClick = { actions.unblock(friend.accountId) }, enabled = enabled, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_unblock)) }
            }
        }
    }
    confirmation?.let { selected ->
        AlertDialog(
            onDismissRequest = { confirmation = null },
            title = { Text(stringResource(if (selected == FriendshipConfirmation.Remove) R.string.ui_friends_refresh_remove_title else R.string.ui_friends_refresh_block_title)) },
            text = { Text(stringResource(if (selected == FriendshipConfirmation.Remove) R.string.ui_friends_refresh_remove_body else R.string.ui_friends_refresh_block_body)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmation = null
                    if (selected == FriendshipConfirmation.Remove) actions.removeFriend(friend.accountId) else actions.block(friend.accountId)
                }, enabled = enabled) { Text(stringResource(if (selected == FriendshipConfirmation.Remove) R.string.social_remove else R.string.social_block)) }
            },
            dismissButton = { TextButton(onClick = { confirmation = null }) { Text(stringResource(R.string.social_cancel)) } },
        )
    }
}

@Composable
private fun ContactCardTools(state: SocialRuntimeState, actions: SocialActions) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    // Signed contact-card input and preview stay out of saved instance state.
    var cardText by remember { mutableStateOf("") }
    var invalidCard by remember { mutableStateOf(false) }
    var preview by remember { mutableStateOf<ContactCard?>(null) }
    AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SectionTitle(stringResource(R.string.ui_friends_refresh_contact_tools))
            Text(stringResource(R.string.ui_friends_refresh_contact_tools_body), style = MaterialTheme.typography.bodySmall, color = AutPlayTokens.colors.mutedText)
            OutlinedButton(onClick = actions.createContactCard, enabled = !state.loading, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_create_contact_card)) }
            state.contactCard?.let { card ->
                Text(stringResource(R.string.social_contact_card_ready), style = MaterialTheme.typography.bodySmall)
                OutlinedButton(onClick = { actions.shareContactCard(card) }, enabled = !state.loading, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_share_contact_card)) }
            }
            TextButton(onClick = { expanded = !expanded; if (!expanded) cardText = "" }, modifier = Modifier.touchTarget()) {
                Text(stringResource(if (expanded) R.string.ui_friends_refresh_hide_card_input else R.string.social_add_friend_from_contact_card))
            }
            if (expanded) {
                OutlinedTextField(value = cardText, onValueChange = { cardText = it.take(4096); invalidCard = false }, modifier = Modifier.fillMaxWidth(), label = { Text(stringResource(R.string.social_friend_contact_card)) }, isError = invalidCard)
                if (invalidCard) Text(stringResource(R.string.ui_friends_refresh_invalid_card), color = MaterialTheme.colorScheme.error)
                Button(onClick = { preview = parseSocialContactCardInput(cardText); invalidCard = preview == null }, enabled = cardText.isNotBlank() && !state.loading, modifier = Modifier.touchTarget()) {
                    Text(stringResource(R.string.ui_friends_refresh_review_request))
                }
            }
        }
    }
    preview?.let { card ->
        AlertDialog(
            onDismissRequest = { preview = null },
            title = { Text(stringResource(R.string.ui_friends_refresh_card_preview, card.displayNameHint)) },
            text = { Text(stringResource(R.string.ui_friends_refresh_mutual_only)) },
            confirmButton = {
                TextButton(onClick = {
                    actions.importContactCard(card.asJson().toString())
                    cardText = ""
                    preview = null
                    expanded = false
                }, enabled = !state.loading) { Text(stringResource(R.string.ui_friends_refresh_send_request)) }
            },
            dismissButton = { TextButton(onClick = { preview = null }) { Text(stringResource(R.string.social_cancel)) } },
        )
    }
}

@Composable
private fun PresenceToggles(settings: PresenceSettings, enabled: Boolean, update: (PresenceSettings) -> Unit) {
    Text(stringResource(R.string.social_presence_private_default), style = MaterialTheme.typography.bodyMedium, color = AutPlayTokens.colors.mutedText)
    Toggle(stringResource(R.string.social_presence_visible), settings.friendsCanSeePresence, enabled) { update(settings.copy(friendsCanSeePresence = it)) }
    Toggle(stringResource(R.string.social_share_wave_activity), settings.shareRoomActivity, enabled) { update(settings.copy(shareRoomActivity = it)) }
    Toggle(stringResource(R.string.social_available_for_wave_invites), settings.availableToInvite, enabled) { update(settings.copy(availableToInvite = it)) }
}

@Composable
private fun Toggle(label: String, checked: Boolean, enabled: Boolean, update: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
        Switch(checked = checked, onCheckedChange = update, enabled = enabled, modifier = Modifier.touchTarget().semantics { contentDescription = label })
    }
}

@Composable
private fun WaveInvitations(state: SocialRuntimeState, actions: SocialActions) {
    if (state.snapshot.receivedInvitations.isNotEmpty() || state.snapshot.sentInvitations.isNotEmpty()) AutPlayCard {
        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
            SectionTitle(stringResource(R.string.social_wave_invitations))
            state.snapshot.receivedInvitations.forEach { invitation ->
                Text(stringResource(R.string.social_wave_invite), style = MaterialTheme.typography.titleSmall)
                Text(invitationLabel(invitation.status), style = MaterialTheme.typography.bodySmall)
                if (invitation.status == RoomInvitationStatus.PENDING) Button(onClick = { actions.acceptInvitation(invitation.invitationId) }, enabled = !state.loading, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_join)) }
            }
            state.snapshot.sentInvitations.forEach { invitation ->
                Text(stringResource(R.string.social_sent_wave_invite, invitationLabel(invitation.status)), style = MaterialTheme.typography.bodySmall)
                if (invitation.status == RoomInvitationStatus.PENDING) OutlinedButton(onClick = { actions.cancelInvitation(invitation.invitationId) }, enabled = !state.loading, modifier = Modifier.touchTarget()) { Text(stringResource(R.string.social_cancel)) }
            }
        }
    }
    state.acceptedRoomId?.let { Text(stringResource(R.string.social_joined_wave_room)) }
}

@Composable private fun Modifier.touchTarget(): Modifier = heightIn(min = AutPlayTokens.dimensions.minimumTouchTarget)
@Composable private fun SectionTitle(title: String) { Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.semantics { heading() }) }
private enum class FriendshipConfirmation { Remove, Block }
@Composable private fun friendshipLabel(status: FriendshipStatus, presence: AggregatePresence) = stringResource(when (status) { FriendshipStatus.PENDING_INBOUND -> R.string.social_friend_request_received; FriendshipStatus.PENDING_OUTBOUND -> R.string.social_request_pending; FriendshipStatus.BLOCKED -> R.string.social_blocked; FriendshipStatus.FRIEND -> when (presence) { AggregatePresence.OFFLINE -> R.string.social_offline; AggregatePresence.ONLINE -> R.string.social_online; AggregatePresence.AVAILABLE_TO_INVITE -> R.string.social_available_to_invite; AggregatePresence.IN_ROOM -> R.string.social_in_wave_room } })
@Composable private fun invitationLabel(status: RoomInvitationStatus) = stringResource(when (status) { RoomInvitationStatus.PENDING -> R.string.social_ready_to_join; RoomInvitationStatus.ACCEPTED -> R.string.social_accepted; RoomInvitationStatus.EXPIRED -> R.string.social_invitation_expired; RoomInvitationStatus.CANCELLED -> R.string.social_invitation_cancelled; RoomInvitationStatus.FULL -> R.string.social_room_full; RoomInvitationStatus.UNAVAILABLE -> R.string.social_invitation_unavailable })
@Composable private fun socialErrorText(code: String) = stringResource(when (code) { "room_full" -> R.string.social_error_room_full; "room_changed" -> R.string.social_error_room_changed; "presence_private" -> R.string.social_error_presence_private; "friendship_required" -> R.string.social_error_friendship_required; "user_blocked" -> R.string.social_error_unavailable; "active_room_exit_required" -> R.string.ui_friends_refresh_exit_wave_first; "friend_request_unavailable" -> R.string.ui_friends_refresh_request_unavailable; "rate_limited" -> R.string.ui_friends_refresh_rate_limited; "auth_attention_required" -> R.string.social_error_reconnect; else -> R.string.social_error_generic })
