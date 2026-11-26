package app.autplay.ui.downloads

import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import androidx.work.WorkManager
import app.autplay.R
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.CatalogDownloadAction
import app.autplay.download.CollectionDownloadStore
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class CatalogDownloadProgressTest {
    @get:Rule val compose = createComposeRule()

    @Test fun oneTapDiscographyShowsProgressAndFilledIconAtItsOriginalPosition() = verifyDiscography(partial = false)
    @Test fun unavailableRecordingsLeaveAnInlineRetry() = verifyDiscography(partial = true)

    private fun verifyDiscography(partial: Boolean): Unit = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = applicationNonSecretSettingsStore(context)
        val previous = store.settings.first()
        val profile = ServerProfileId(UUID.randomUUID().toString())
        val user = UserId(UUID.randomUUID().toString())
        val artist = UUID.randomUUID().toString()
        val storeKey = "${profile.value}:${user.value}:studio-discography:$artist:false"
        val stage = AtomicInteger()
        val credentials = AndroidKeystoreCredentialStore(context)
        val manager = WorkManager.getInstance(context)
        val batch = CollectionDownloadStore(context)
        val visible = mutableStateOf(true)
        MockWebServer().use { server ->
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.path?.contains("/music/discovery/downloads/") != true) return MockResponse().setResponseCode(404)
                    val (phase, saved, missing) = when {
                        request.path?.endsWith("/preview") == true -> Triple("discovery", 0, 0)
                        request.path?.endsWith("/advance") == true && stage.compareAndSet(-1, 0) -> Triple("preview", 0, 0)
                        stage.get() == 0 -> Triple("downloading", 1, 0)
                        stage.get() == 1 && partial -> Triple("downloading", 2, 1)
                        stage.get() == 1 -> Triple("downloading", 3, 0)
                        partial -> Triple("complete", 3, 1)
                        else -> Triple("complete", 4, 0)
                    }
                    return MockResponse().setBody("""{"phase":"$phase","total":4,"counts":{"vault":$saved,"missing":$missing,"queued":${4-saved-missing},"available":0}}""")
                }
            }
            server.start()
            val account = NonSecretSettings(activeServerProfileId = profile, activeUserId = user,
                deviceId = DeviceId(UUID.randomUUID().toString()), serverBaseUrl = server.url("/").toString().trimEnd('/'))
            fun waitText(text: String, visible: Boolean = true) = compose.waitUntil(15_000) {
                compose.onAllNodes(hasText(text)).fetchSemanticsNodes().isNotEmpty() == visible
            }
            try {
                credentials.write(profile, SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("qa-catalog-progress", null, 0)))
                store.update(account)
                stage.set(-1)
                compose.setContent { AutPlayTheme {
                    if (visible.value) CatalogDownloadAction("artist", artist, "Apashe")
                } }
                val tag = "download-artist-$artist"
                compose.waitUntil(10_000) { compose.onAllNodes(androidx.compose.ui.test.hasTestTag(tag)).fetchSemanticsNodes().isNotEmpty() }
                compose.onNodeWithTag(tag).performClick()
                waitText(context.getString(R.string.download_catalog_processed, 1, 4))
                val name = "catalog-download:${requireNotNull(batch.operation(storeKey))}:true"
                compose.runOnIdle { visible.value = false }
                compose.waitForIdle()
                stage.set(1)
                compose.runOnIdle { visible.value = true }
                waitText(context.getString(R.string.download_catalog_processed, 3, 4))
                stage.set(2)
                val result = withTimeout(15_000) { manager.getWorkInfosForUniqueWorkFlow(name).first { jobs -> jobs.any { it.state.isFinished } } }
                assertEquals(WorkInfo.State.SUCCEEDED, result.single().state)
                if (partial) {
                    waitText(context.getString(R.string.download_partial, 3, 4))
                    compose.onNodeWithTag("download-saved-$artist", useUnmergedTree = true).assertDoesNotExist()
                } else {
                    val saved = androidx.compose.ui.test.hasTestTag("download-saved-$artist")
                    compose.waitUntil(8_000) { compose.onAllNodes(saved, useUnmergedTree = true).fetchSemanticsNodes().isNotEmpty() }
                    store.update(account.copy(activeUserId = UserId(UUID.randomUUID().toString())))
                    compose.waitUntil(8_000) { compose.onAllNodes(saved, useUnmergedTree = true).fetchSemanticsNodes().isEmpty() }
                }
            } finally {
                batch.operation(storeKey)?.let { manager.cancelUniqueWork("catalog-download:$it:true").result.get() }
                store.update(previous)
                credentials.clear(profile)
            }
        }
    }
}
