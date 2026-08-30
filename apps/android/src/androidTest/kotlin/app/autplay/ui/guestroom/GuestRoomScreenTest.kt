package app.autplay.ui.guestroom

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.guestroom.GuestRoomRuntimeState
import app.autplay.application.guestroom.GuestRoomStage
import app.autplay.domain.wave.WaveRuntimeState
import app.autplay.ui.AutPlayTheme
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class GuestRoomScreenTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun invitationCollectsTrimmedDisplayNameBeforeJoining() {
        val joinedName = AtomicReference<String?>()
        render(
            GuestRoomRuntimeState(
                stage = GuestRoomStage.DOCUMENT_READY,
                roomId = ROOM_ID,
                expiresAtMs = 1_800_000_000_000,
            ),
            actions = GuestRoomScreenActions(join = joinedName::set),
        )

        compose.onNodeWithTag("guest-display-name").performTextInput("  Listener  ")
        compose.onNodeWithText(context.getString(R.string.guest_join)).performClick()

        compose.runOnIdle { assertEquals("Listener", joinedName.get()) }
    }

    @Test
    fun invalidDocumentCannotAttemptRedemption() {
        val returned = AtomicBoolean()
        render(
            GuestRoomRuntimeState(
                stage = GuestRoomStage.ERROR,
                errorCode = "guest_document_invalid",
            ),
            actions = GuestRoomScreenActions(cancel = { returned.set(true) }),
        )

        compose.onNodeWithText(context.getString(R.string.guest_state_invalid_document))
            .assertIsDisplayed()
        assertEquals(
            0,
            compose.onAllNodesWithText(context.getString(R.string.guest_join))
                .fetchSemanticsNodes().size,
        )
        compose.onNodeWithText(context.getString(R.string.guest_return_to_app)).performClick()
        compose.runOnIdle { assertTrue(returned.get()) }
    }

    @Test
    fun activeGuestSeesGuestBoundaryAndCanLeave() {
        val left = AtomicBoolean()
        render(
            GuestRoomRuntimeState(
                stage = GuestRoomStage.ACTIVE,
                roomId = ROOM_ID,
                displayName = "Listener",
                expiresAtMs = 1_800_000_000_000,
            ),
            waveState = WaveRuntimeState.PLAYING,
            actions = GuestRoomScreenActions(leave = { left.set(true) }),
        )

        compose.onNodeWithText(context.getString(R.string.guest_active_title, "Listener"))
            .assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.guest_host_controls_playback))
            .assertIsDisplayed()
        assertEquals(
            0,
            compose.onAllNodesWithText(context.getString(R.string.wave_host_controls))
                .fetchSemanticsNodes().size,
        )
        compose.onNodeWithText(context.getString(R.string.wave_leave_room)).performClick()
        compose.runOnIdle { assertTrue(left.get()) }
    }

    private fun render(
        state: GuestRoomRuntimeState,
        waveState: WaveRuntimeState = WaveRuntimeState.IDLE,
        actions: GuestRoomScreenActions = GuestRoomScreenActions(),
    ) {
        compose.setContent {
            AutPlayTheme {
                GuestRoomScreen(state, waveState, actions)
            }
        }
        compose.waitForIdle()
    }

    private companion object {
        const val ROOM_ID = "11111111-1111-4111-8111-111111111111"
    }
}
