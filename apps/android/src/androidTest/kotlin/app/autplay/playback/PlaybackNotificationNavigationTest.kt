package app.autplay.playback

import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.performClick
import androidx.media3.common.util.UnstableApi
import androidx.test.core.app.ActivityScenario
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.MainActivity
import app.autplay.R
import app.autplay.data.settings.CURRENT_ONBOARDING_REVISION
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test

/** Full Activity navigation, without replacing or starting a playback queue. */
@UnstableApi
class PlaybackNotificationNavigationTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val settings = applicationNonSecretSettingsStore(context)
    private lateinit var original: NonSecretSettings
    private var activity: ActivityScenario<MainActivity>? = null

    @Before fun prepare() = runBlocking {
        original = settings.settings.first()
        settings.update(NonSecretSettings(onboardingRevision = CURRENT_ONBOARDING_REVISION, pendingPublicId = "notification_fixture"))
    }

    @After fun finish() = runBlocking {
        activity?.close()
        settings.update(original)
    }

    @Test fun notificationOpensPlayerOnColdLaunchAndAgainAfterNavigatingAway() {
        activity = ActivityScenario.launch(PlaybackNotification.activityIntent(context))
        awaitPlayer()
        compose.onNodeWithContentDescription(context.getString(R.string.player_collapse)).performClick()
        compose.waitUntil(15_000) { compose.onAllNodesWithContentDescription(context.getString(R.string.player_collapse)).fetchSemanticsNodes().isEmpty() }
        // Sends the same immutable PendingIntent while the existing Activity is at the top.
        PlaybackNotification.sessionActivity(context).send()
        awaitPlayer()
        activity!!.recreate()
        awaitPlayer()
    }

    private fun awaitPlayer() {
        compose.waitUntil(15_000) { compose.onAllNodesWithContentDescription(context.getString(R.string.player_collapse)).fetchSemanticsNodes().isNotEmpty() }
        compose.onAllNodesWithText(context.getString(R.string.player_nothing_playing)).fetchSemanticsNodes().also { check(it.isNotEmpty()) }
        compose.waitForIdle()
    }
}
