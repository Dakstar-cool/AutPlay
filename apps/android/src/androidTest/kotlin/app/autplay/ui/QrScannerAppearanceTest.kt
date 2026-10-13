package app.autplay.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.performClick
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.AppLanguageProvider
import app.autplay.QrScannerOverlay
import app.autplay.R
import app.autplay.localizedAppContext
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import java.io.File

class QrScannerAppearanceTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Test fun scannerControlsUseSelectedLanguageInBothThemes() {
        val language = mutableStateOf(AppLanguage.English)
        val theme = mutableStateOf(AutPlayThemeMode.Light)
        var cancellations = 0
        compose.setContent {
            AppLanguageProvider(language.value.storedValue) {
                AutPlayTheme(AutPlayAppearance(theme.value)) {
                    Box(Modifier.fillMaxSize().background(Color.Black)) {
                        QrScannerOverlay(R.string.qr_scan_camera_permission_required) { cancellations++ }
                    }
                }
            }
        }
        for (selected in AppLanguage.entries) for (mode in AutPlayThemeMode.entries) {
            compose.runOnIdle { language.value = selected; theme.value = mode }
            val resources = localizedAppContext(context, selected).resources
            val opposite = if (selected == AppLanguage.Russian) AppLanguage.English else AppLanguage.Russian
            compose.onNodeWithText(resources.getString(R.string.qr_scan_camera_permission_required)).assertIsDisplayed()
            compose.onNodeWithText(localizedAppContext(context, opposite).getString(R.string.qr_scan_camera_permission_required)).assertDoesNotExist()
            File(context.getExternalFilesDir(null), "qr-overlay-${selected.storedValue}-${mode.name}.png").outputStream().use {
                compose.onRoot().captureToImage().asAndroidBitmap().compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it)
            }
            compose.onNodeWithText(resources.getString(R.string.qr_scan_cancel)).assertIsDisplayed().performClick()
        }
        compose.runOnIdle { assertEquals(4, cancellations) }
    }
}
