package app.autplay.accountrecovery

import android.net.Uri
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.Button
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.platform.testTag
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.readBoundedRecoveryBytes
import java.security.MessageDigest
import java.util.HexFormat
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Physical proof that the production OpenDocument contract returns bytes through DocumentsUI. */
@RunWith(AndroidJUnit4::class)
class AccountRecoverySystemPickerE2eTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    @Test
    fun realSystemPickerReturnsTheSelectedBoundedRecoveryDocument() {
        val expectedSha256 = InstrumentationRegistry.getArguments()
            .getString("accountRecoveryPickerSha256")
        assumeTrue(
            "The physical picker harness supplies accountRecoveryPickerSha256",
            expectedSha256?.matches(Regex("[0-9a-f]{64}")) == true,
        )
        val result = AtomicReference<PickerResult?>()
        val failure = AtomicReference<Throwable?>()
        compose.setContent {
            val context = androidx.compose.ui.platform.LocalContext.current
            val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
                if (uri == null) {
                    failure.set(IllegalStateException("RECOVERY_PICKER_CANCELLED"))
                } else {
                    var bytes: ByteArray? = null
                    try {
                        bytes = requireNotNull(context.contentResolver.openInputStream(uri)).use(
                            ::readBoundedRecoveryBytes,
                        )
                        result.set(
                            PickerResult(
                                scheme = uri.scheme,
                                authority = uri.authority,
                                byteCount = requireNotNull(bytes).size,
                                sha256 = sha256(requireNotNull(bytes)),
                            ),
                        )
                    } catch (cause: Exception) {
                        failure.set(cause)
                    } finally {
                        bytes?.fill(0)
                    }
                }
            }
            Button(
                onClick = {
                    picker.launch(
                        arrayOf("text/plain", "application/json", "application/octet-stream"),
                    )
                },
                modifier = Modifier.testTag("account_recovery_system_picker"),
            ) {
                Text("Open recovery document")
            }
        }

        compose.onNodeWithTag("account_recovery_system_picker").performClick()
        compose.waitUntil(timeoutMillis = 120_000) {
            result.get() != null || failure.get() != null
        }
        failure.get()?.let { throw it }
        val selected = requireNotNull(result.get())

        assertEquals("content", selected.scheme)
        assertTrue(selected.authority.orEmpty().isNotBlank())
        assertNotEquals(compose.activity.packageName, selected.authority)
        assertTrue(selected.byteCount in 2..4096)
        assertEquals(expectedSha256, selected.sha256)
    }

    private data class PickerResult(
        val scheme: String?,
        val authority: String?,
        val byteCount: Int,
        val sha256: String,
    )

    private companion object {
        fun sha256(bytes: ByteArray): String = HexFormat.of().formatHex(
            MessageDigest.getInstance("SHA-256").digest(bytes),
        )
    }
}
