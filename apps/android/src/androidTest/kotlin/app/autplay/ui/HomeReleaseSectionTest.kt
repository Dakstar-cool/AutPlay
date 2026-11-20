package app.autplay.ui

import android.content.res.Configuration
import android.graphics.Bitmap
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.File
import java.util.Locale

class HomeReleaseSectionTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val opened = mutableListOf<String>()
    private val releases = (1..12).map {
        HomeReleaseUiItem("release-$it", if (it == 1) "Тихий свет" else "Новый альбом $it",
            if (it == 1) "Маяк" else "Исполнитель $it", "2026", inLibrary = it == 1)
    }

    private fun render(dark: Boolean = false, fontScale: Float = 1f, items: List<HomeReleaseUiItem> = releases) {
        val configuration = Configuration(context.resources.configuration).apply { setLocale(Locale.forLanguageTag("ru")) }
        val localized = context.createConfigurationContext(configuration)
        compose.setContent {
            CompositionLocalProvider(LocalContext provides localized, LocalConfiguration provides configuration,
                LocalResources provides localized.resources,
                LocalDensity provides Density(context.resources.displayMetrics.density, fontScale)) {
                AutPlayTheme(AutPlayAppearance(mode = if (dark) AutPlayThemeMode.Dark else AutPlayThemeMode.Light)) {
                    Surface(Modifier.width(360.dp), color = MaterialTheme.colorScheme.background) {
                        HomeProductScreen(
                            state = HomeScreenUiState(true, false, false, items, emptyList(),
                                recentlyAdded = (1..15).map { HomeTrackUiItem("track-$it", "Track $it", "Artist") }),
                            contentPadding = PaddingValues(), onOpenListenTogether = {},
                            onRecommendationVisible = {}, onLike = {}, onDislike = {},
                            onOpenRelease = { opened += it },
                        )
                    }
                }
            }
        }
        compose.onNodeWithTag("home-product-list").performScrollToNode(hasTestTag("home-releases-section"))
    }

    @Test fun stripBrowsesWithoutOpeningOrDismissingAndAllOpensExactRelease() {
        render()
        compose.onNodeWithTag("home-release:release-1").assertIsDisplayed()
        compose.onNodeWithTag("home-release:release-2").assertIsDisplayed()
        saveScreenshot("home-releases-light.png")
        val strip = compose.onNodeWithTag("home-release-strip")
        val initialOffset = strip.fetchSemanticsNode().config[SemanticsProperties.HorizontalScrollAxisRange].value()
        strip.performTouchInput { swipeLeft() }
        assertTrue(strip.fetchSemanticsNode().config[SemanticsProperties.HorizontalScrollAxisRange].value() > initialOffset)
        compose.runOnIdle { assertTrue(opened.isEmpty()) }
        compose.onNodeWithTag("home-releases-all").performClick()
        compose.onNodeWithTag("home-releases-sheet").assertIsDisplayed()
        saveScreenshot("home-releases-all.png")
        compose.onNodeWithTag("home-releases-list").performScrollToNode(hasTestTag("home-release:release-12"))
        compose.onNodeWithTag("home-release:release-12").performClick()
        compose.onNodeWithTag("home-releases-sheet").assertDoesNotExist()
        compose.runOnIdle { assertEquals(listOf("release-12"), opened) }
        compose.onNodeWithTag("home-releases-all").performClick()
        compose.onNodeWithTag("home-releases-list").performScrollToNode(hasTestTag("home-release:release-1"))
        compose.onNodeWithTag("home-release:release-1").assertIsDisplayed()
    }

    @Test fun verticalSwipeOverStripScrollsHomeWithoutOpeningRelease() {
        render()
        val home = compose.onNodeWithTag("home-product-list")
        val before = home.fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        compose.onNodeWithTag("home-release-strip").performTouchInput { swipeUp() }
        val after = home.fetchSemanticsNode().config[SemanticsProperties.VerticalScrollAxisRange].value()
        assertTrue("Vertical motion belongs to Home", after > before)
        compose.runOnIdle { assertTrue(opened.isEmpty()) }
    }

    @Test fun darkThemeAndLargeTextKeepAllActionAndReleaseReachable() {
        render(dark = true, fontScale = 2f)
        compose.onNodeWithTag("home-releases-all").assertIsDisplayed()
        compose.onNodeWithTag("home-release:release-1").assertIsDisplayed()
        saveScreenshot("home-releases-dark-large-text.png")
        compose.onNodeWithTag("home-release:release-1").performClick()
        compose.runOnIdle { assertEquals(listOf("release-1"), opened) }
    }

    @Test fun emptyFeedDoesNotOfferAnEmptyAllSheet() {
        render(items = emptyList())
        compose.onNodeWithTag("home-releases-empty").assertIsDisplayed()
        compose.onNodeWithTag("home-releases-all").assertDoesNotExist()
    }

    private fun saveScreenshot(name: String) {
        compose.waitForIdle()
        val screenshot = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot()
        File(context.cacheDir, name).outputStream().use { check(screenshot.compress(Bitmap.CompressFormat.PNG, 100, it)) }
    }
}
