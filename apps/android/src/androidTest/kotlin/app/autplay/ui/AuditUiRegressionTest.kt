package app.autplay.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.R
import app.autplay.application.library.*
import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class AuditUiRegressionTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    @Test fun failureArrivingWithNavigationSurvivesTheNextFrame() {
        val destination = mutableStateOf<UiDestination>(UiDestination.Home)
        lateinit var errors: CommandErrorState
        compose.setContent { AutPlayTheme {
            errors = rememberCommandErrorState("profile", destination.value, null)
            var error by errors
            AutPlayAdaptiveShell(destination.value, { destination.value = it },
                snackbarHost = { CommandErrorBanner(error) { error = null } }) { _, _, _ -> }
        } }
        compose.runOnIdle {
            var error by errors
            error = "PLAYBACK_UNAVAILABLE"
        }
        compose.onNodeWithTag("command-error").assertIsDisplayed()
        compose.runOnIdle {
            destination.value = UiDestination.NowPlaying
            var error by errors
            error = "PLAYBACK_UNAVAILABLE"
        }
        compose.onNodeWithTag("command-error").assertIsDisplayed()
        compose.runOnIdle { destination.value = UiDestination.Downloads }
        compose.onNodeWithTag("command-error").assertDoesNotExist()
    }
    @Test fun libraryLikedTrackCanBeUnlikedAndLikedAgain() {
        val loved = mutableStateOf(true)
        var calls = 0
        compose.setContent { AutPlayTheme {
            LibraryProductScreen(LibraryScreenUiState(true, listOf(CoreTrackUiItem("track", "Title", "Artist", selected = true, loved = loved.value))),
                PaddingValues(0.dp), {}, {}, {}, { assertEquals("track", it); calls++; loved.value = !loved.value })
        } }
        compose.onNode(hasText(context.getString(R.string.action_liked)) and SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Button)).performScrollTo().assertIsEnabled().performClick()
        compose.onNodeWithText(context.getString(R.string.action_like)).assertIsEnabled().performClick()
        assertEquals(2, calls)
    }
    @Test fun detailLikedTrackCanBeUnlikedAndLikedAgain() {
        val loved = mutableStateOf(true)
        var calls = 0
        compose.setContent { AutPlayTheme {
            CoreProductDetailScreen(CoreProductDetailUiState(DetailTarget(DetailKind.Track, "track"), track =
                CoreTrackDetail("track", null, null, "Title", "Artist", null, null, null,
                    CoreTrackPreferenceState(if (loved.value) "LIKED" else "NEUTRAL", false),
                    CoreTrackAvailability.SERVER_CANDIDATE, setOf(CoreTrackDetailCapability.LIKE),
                    CoreTechnicalDetails("FUTURE", null, null, null))),
                onLike = { assertEquals("track", it); calls++; loved.value = !loved.value })
        } }
        compose.onNodeWithText(context.getString(R.string.action_liked)).performScrollTo().assertIsEnabled().performClick()
        compose.onNodeWithText(context.getString(R.string.action_like)).assertIsEnabled().performClick()
        assertEquals(2, calls)
    }
    @Test fun failuresAreVisibleAndDismissibleOnPlayerAndDownloads() {
        val destination = mutableStateOf<UiDestination>(UiDestination.NowPlaying)
        val error = mutableStateOf<String?>("PRIVATE_INTERNAL_CODE")
        compose.setContent { AutPlayTheme {
            AutPlayAdaptiveShell(destination.value, { destination.value = it },
                snackbarHost = { CommandErrorBanner(error.value) { error.value = null } }) { _, _, _ -> }
        } }
        for (route in listOf(UiDestination.NowPlaying, UiDestination.Downloads)) {
            compose.runOnIdle { destination.value = route; error.value = "PRIVATE_INTERNAL_CODE" }
            compose.onNodeWithTag("command-error").assertIsDisplayed()
            compose.onNodeWithText("PRIVATE_INTERNAL_CODE").assertDoesNotExist()
            compose.onNodeWithText(context.getString(R.string.action_dismiss_error)).performClick()
            compose.onNodeWithTag("command-error").assertDoesNotExist()
        }
    }
}
