package app.autplay.ui.social

import androidx.activity.ComponentActivity
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.social.AggregatePresence
import app.autplay.application.social.FriendSummary
import app.autplay.application.social.FriendProfileStatisticsState
import app.autplay.application.social.FriendshipStatus
import app.autplay.application.social.PresenceSettings
import app.autplay.application.social.RoomInvitationStatus
import app.autplay.application.social.RoomInvitationSummary
import app.autplay.application.social.SocialRuntimeState
import app.autplay.application.social.SocialSnapshot
import app.autplay.application.social.SharedProfileStatistics
import app.autplay.application.social.SharedProfileStatisticsWindow
import app.autplay.application.social.SharedStatisticsWindowKind
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SocialPanelTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun presenceIsPrivateByDefaultAndEachOptInIsExplicit() {
        val updated = AtomicReference<PresenceSettings?>()
        render(
            SocialRuntimeState(),
            SocialActions(setPresence = updated::set),
        )

        compose.onNodeWithText(context.getString(R.string.social_presence_private_default))
            .performScrollTo()
            .assertIsDisplayed()
        compose.onNodeWithContentDescription(context.getString(R.string.social_presence_visible))
            .performScrollTo()
            .performClick()
        compose.runOnIdle {
            assertEquals(PresenceSettings(true, false, false), updated.get())
        }
    }

    @Test
    fun inviteControlsExposeOnlyAllowedActionsAndTerminalStates() {
        val invitedAccount = AtomicReference<String?>()
        val acceptedInvitation = AtomicReference<String?>()
        val cancelledInvitation = AtomicReference<String?>()
        val pendingReceived = invitation("11111111-1111-4111-8111-111111111111", RoomInvitationStatus.PENDING)
        val fullReceived = invitation("22222222-2222-4222-8222-222222222222", RoomInvitationStatus.FULL)
        val pendingSent = invitation("33333333-3333-4333-8333-333333333333", RoomInvitationStatus.PENDING)
        render(
            SocialRuntimeState(
                snapshot = SocialSnapshot(
                    friends = listOf(
                        FriendSummary(AVAILABLE_FRIEND, "Available friend", FriendshipStatus.FRIEND, AggregatePresence.AVAILABLE_TO_INVITE),
                        FriendSummary(ONLINE_FRIEND, "Online friend", FriendshipStatus.FRIEND, AggregatePresence.ONLINE),
                    ),
                    sentInvitations = listOf(pendingSent),
                    receivedInvitations = listOf(pendingReceived, fullReceived),
                ),
            ),
            SocialActions(
                inviteFriend = invitedAccount::set,
                acceptInvitation = acceptedInvitation::set,
                cancelInvitation = cancelledInvitation::set,
            ),
        )

        compose.onAllNodesWithText(context.getString(R.string.social_invite_to_wave)).assertCountEquals(1)
        compose.onNodeWithText(context.getString(R.string.social_invite_to_wave)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.social_join)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.social_cancel)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.social_room_full)).performScrollTo().assertIsDisplayed()
        compose.runOnIdle {
            assertEquals(AVAILABLE_FRIEND, invitedAccount.get())
            assertEquals(pendingReceived.invitationId, acceptedInvitation.get())
            assertEquals(pendingSent.invitationId, cancelledInvitation.get())
        }
    }

    @Test
    fun friendStatisticsActionShowsVisibleAndUnavailableBoundedStates() {
        val requested = AtomicReference<String?>()
        val statistics = SharedProfileStatistics(
            throughUtcDate = "2026-08-24",
            windows = listOf(
                SharedProfileStatisticsWindow(
                    SharedStatisticsWindowKind.Last7CompleteDays,
                    playSessionCount = 4,
                    listenedMs = 3_600_000,
                    uniqueTrackCount = 2,
                ),
            ),
        )
        render(
            SocialRuntimeState(
                snapshot = SocialSnapshot(
                    friends = listOf(FriendSummary(ONLINE_FRIEND, "Online friend", FriendshipStatus.FRIEND)),
                ),
                friendStatistics = FriendProfileStatisticsState.Visible(ONLINE_FRIEND, statistics),
            ),
            SocialActions(viewFriendStatistics = requested::set),
        )

        compose.onNodeWithText(context.getString(R.string.statistics_view)).performScrollTo().performClick()
        compose.onNodeWithText(context.getString(R.string.statistics_friend_through, "2026-08-24"))
            .performScrollTo()
            .assertIsDisplayed()
        compose.runOnIdle { assertEquals(ONLINE_FRIEND, requested.get()) }
    }

    @Test
    fun friendStatisticsDenialIsNonDisclosing() {
        render(
            SocialRuntimeState(
                snapshot = SocialSnapshot(
                    friends = listOf(FriendSummary(ONLINE_FRIEND, "Online friend", FriendshipStatus.FRIEND)),
                ),
                friendStatistics = FriendProfileStatisticsState.Unavailable(ONLINE_FRIEND),
            ),
            SocialActions(),
        )
        compose.onNodeWithText(context.getString(R.string.statistics_unavailable))
            .performScrollTo()
            .assertIsDisplayed()
    }

    private fun render(state: SocialRuntimeState, actions: SocialActions) {
        compose.setContent {
            MaterialTheme {
                SocialPanel(state, actions, Modifier.verticalScroll(rememberScrollState()))
            }
        }
        compose.mainClock.advanceTimeByFrame()
        compose.waitForIdle()
    }

    private fun invitation(id: String, status: RoomInvitationStatus) = RoomInvitationSummary(
        invitationId = id,
        roomId = "44444444-4444-4444-8444-444444444444",
        roomEpoch = "7",
        status = status,
        expiresAt = "2026-08-25T12:00:00Z",
    )

    private companion object {
        const val AVAILABLE_FRIEND = "55555555-5555-4555-8555-555555555555"
        const val ONLINE_FRIEND = "66666666-6666-4666-8666-666666666666"
    }
}
