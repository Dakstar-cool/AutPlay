package app.autplay.ui

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import app.autplay.application.wave.WaveHostTransferTarget
import app.autplay.application.wave.WaveUiState
import app.autplay.WaveHostTransferControls
import app.autplay.domain.wave.WaveRuntimeState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test

class WaveHostTransferControlsTest {
    @get:Rule val compose = createComposeRule()

    @Test fun hostConfirmsNamedTargetAndRoleChangesOnlyFromAuthoritativeState() {
        val target = WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room")
        var state by mutableStateOf(hostState(target))
        var transferred: WaveHostTransferTarget? = null
        compose.setContent {
            AutPlayTheme {
                WaveHostTransferControls(
                    state = state,
                    onTransfer = {
                        transferred = it
                        state = state.copy(isHost = false, hostTransferTargets = emptyList())
                    },
                    onError = {},
                )
            }
        }

        compose.onNodeWithTag("wave-transfer-target-0").performClick()
        compose.onNodeWithTag("wave-transfer-confirm").assertExists().performClick()
        compose.waitUntil { transferred != null }

        compose.runOnIdle {
            assertEquals(target, transferred)
            assertFalse(state.isHost)
        }
        compose.onNodeWithTag("wave-transfer-target-0").assertDoesNotExist()
    }

    @Test fun rejectedTransferReportsSafeErrorWithoutOptimisticRoleChange() {
        val target = WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room")
        val state = hostState(target)
        var error: String? = null
        compose.setContent {
            AutPlayTheme {
                WaveHostTransferControls(
                    state = state,
                    onTransfer = { error("rejected") },
                    onError = { error = it },
                )
            }
        }

        compose.onNodeWithTag("wave-transfer-target-0").performClick()
        compose.onNodeWithTag("wave-transfer-confirm").performClick()
        compose.waitUntil { error != null }

        compose.runOnIdle {
            assertEquals("WAVE_TRANSFER_UNAVAILABLE", error)
            assertEquals(true, state.isHost)
        }
        compose.onNodeWithTag("wave-transfer-target-0").assertExists()
    }

    @Test fun memberDoesNotSeeHostTransferAction() {
        compose.setContent {
            AutPlayTheme {
                WaveHostTransferControls(
                    state = hostState(WaveHostTransferTarget(TARGET_DEVICE_ID, "Living room"))
                        .copy(isHost = false),
                    onTransfer = {},
                    onError = {},
                )
            }
        }
        compose.onNodeWithTag("wave-transfer-target-0").assertDoesNotExist()
    }

    private fun hostState(target: WaveHostTransferTarget) = WaveUiState(
        roomId = ROOM_ID,
        state = WaveRuntimeState.PREFLIGHT,
        isHost = true,
        hostTransferTargets = listOf(target),
    )

    private companion object {
        const val ROOM_ID = "11111111-1111-4111-8111-111111111111"
        const val TARGET_DEVICE_ID = "22222222-2222-4222-8222-222222222222"
    }
}
