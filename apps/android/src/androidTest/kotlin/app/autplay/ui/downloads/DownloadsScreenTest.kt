package app.autplay.ui.downloads

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.mutableDoubleStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.unit.dp
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import app.autplay.application.download.DownloadIntentPresentation
import app.autplay.ui.AutPlayTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class DownloadsScreenTest {
    @get:Rule val compose = createComposeRule()

    @Test fun downloadArcFillsContinuouslyBetweenMeasuredUpdates() {
        val measured = mutableDoubleStateOf(.25)
        compose.mainClock.autoAdvance = false
        compose.setContent { AutPlayTheme {
            DownloadProgressRing(measured.doubleValue, Modifier.size(100.dp).testTag("ring"), showPercent = false)
        } }
        fun checkArc(filled: List<Double>, empty: List<Double>) {
            val pixels = compose.onNodeWithTag("ring").captureToImage().toPixelMap()
            fun sample(position: Double): androidx.compose.ui.graphics.Color {
                val angle = position * 2 * Math.PI
                val radius = pixels.width * .485
                return pixels[(pixels.width / 2.0 + radius * Math.sin(angle)).toInt(),
                    (pixels.height / 2.0 - radius * Math.cos(angle)).toInt()]
            }
            val fill = sample(.05)
            val track = sample(.94)
            org.junit.Assert.assertNotEquals(fill, track)
            filled.forEach { assertEquals("Gap at $it", fill, sample(it)) }
            empty.forEach { assertEquals("Fill beyond the measured arc at $it", track, sample(it)) }
        }
        checkArc(listOf(.10, .20), listOf(.30, .60))
        compose.runOnIdle { measured.doubleValue = .75 }
        compose.mainClock.advanceTimeBy(200)
        checkArc(listOf(.10, .20, .35), listOf(.60, .70))
        compose.mainClock.advanceTimeBy(450)
        checkArc((1..7).map { it / 10.0 }, listOf(.80, .90))
    }

    @Test fun allCoarseStatesRenderTogetherAndActionsUseIntentIdentity() {
        var action: String? = null
        val downloads = listOf(
            row("requested", "REQUESTED"),
            row("queued", "QUEUED"),
            row("downloading", "DOWNLOADING"),
            row("paused", "PAUSED"),
            row("completed", "COMPLETED"),
            row("failed", "FAILED", "NETWORK"),
            row("cancelled", "CANCELLED"),
        )
        compose.setContent {
            AutPlayTheme {
                DownloadsScreen(
                    downloads = downloads,
                    pendingIntentId = null,
                    canDownloadSelected = false,
                    actions = DownloadsUiActions(
                        play = { action = "play:${it.stableId}" },
                        cancel = { action = "cancel:${it.stableId}" },
                        retry = { action = "retry:${it.stableId}" },
                    ),
                    contentPadding = PaddingValues(),
                )
            }
        }

        downloads.forEach { download ->
            compose.onNodeWithTag("downloads-list")
                .performScrollToNode(hasTestTag("download-${download.stableId}"))
            compose.onNodeWithTag("download-${download.stableId}").assertExists()
        }
        compose.onNodeWithTag("downloads-list")
            .performScrollToNode(hasTestTag("download-play-completed"))
        compose.onNodeWithTag("download-play-completed").performClick()
        compose.runOnIdle { assertEquals("play:completed", action) }
        compose.onNodeWithTag("downloads-list")
            .performScrollToNode(hasTestTag("download-retry-failed"))
        compose.onNodeWithTag("download-retry-failed").performClick()
        compose.runOnIdle { assertEquals("retry:failed", action) }
        compose.onNodeWithTag("downloads-list")
            .performScrollToNode(hasTestTag("download-cancel-downloading"))
        compose.onNodeWithTag("download-cancel-downloading").performClick()
        compose.runOnIdle { assertEquals("cancel:downloading", action) }
    }

    private fun row(id: String, state: String, failure: String? = null) = DownloadIntentPresentation(
        stableId = id,
        localUserTrackRefId = "track-$id",
        state = state,
        title = "Track $id",
        artist = "Artist",
        failureCode = failure,
    )
}
