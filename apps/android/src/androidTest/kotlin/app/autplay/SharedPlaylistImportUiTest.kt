package app.autplay

import android.content.ContentValues
import android.provider.MediaStore
import android.view.accessibility.AccessibilityNodeInfo
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.filters.SdkSuppress
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.data.settings.CURRENT_ONBOARDING_REVISION
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import java.util.UUID
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.*
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
@SdkSuppress(minSdkVersion = 29)
class SharedPlaylistImportUiTest {
    @get:Rule val compose = createEmptyComposeRule()

    @Test fun pickFileSaveAndReopenInLibrary() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val settings = applicationNonSecretSettingsStore(context)
        val original = runBlocking { settings.settings.first() }
        Assume.assumeTrue(context.packageName == "app.autplay.qa" && original.activeServerProfileId == null &&
            InstrumentationRegistry.getArguments().getString("playlistShareUi") == "true")
        val name = "AutPlay-QA-${UUID.randomUUID().toString().take(8)}"
        val filename = "$name.autplaylist"
        val resolver = context.contentResolver
        val uri = checkNotNull(resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, filename)
            put(MediaStore.Downloads.MIME_TYPE, "application/vnd.autplay.playlist+json")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }))
        val repository = LibraryVerticalSliceRepository(AutPlayRuntime.database(context))
        try {
            checkNotNull(resolver.openOutputStream(uri)).use { it.write("""{"format":"autplay-playlist-v1",
                "token":"${UUID.randomUUID()}","name":"$name","description":null,"tracks":[
                {"title":"Phone track","artist":"Artist","duration_ms":36000,"recording_id":null}],
                "entries":[0,0]}""".toByteArray()) }
            resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            runBlocking { settings.update(original.copy(appLanguage = "EN", pendingPublicId = "qa_playlist",
                onboardingRevision = CURRENT_ONBOARDING_REVISION)) }
            ActivityScenario.launch(MainActivity::class.java).use {
                openPlaylists()
                compose.onNodeWithText("Open playlist file").performClick()
                val automation = instrumentation.uiAutomation
                fun selectFromPicker(label: String): Boolean {
                    val root = automation.rootInActiveWindow ?: return false
                    if (!root.packageName.toString().contains("documentsui")) return false
                    var node = root.findAccessibilityNodeInfosByText(label).firstOrNull {
                        it.text?.toString() == label || it.contentDescription?.toString() == label
                    }
                    while (node != null && !node.isClickable) node = node.parent
                    return node?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true
                }
                if (!runCatching { compose.waitUntil(4_000) { selectFromPicker(filename) } }.isSuccess) {
                    check(selectFromPicker("Show roots") || selectFromPicker("Показать корни") ||
                        selectFromPicker("Открыть меню навигации"))
                    compose.waitUntil(5_000) { selectFromPicker("Downloads") || selectFromPicker("Загрузки") }
                    compose.waitUntil(8_000) { selectFromPicker(filename) }
                }
                compose.waitUntil(10_000) { compose.onAllNodesWithText(name).fetchSemanticsNodes().isNotEmpty() }
                compose.onNodeWithText("Save a copy").performClick()
                compose.waitUntil(10_000) { runBlocking { repository.playlists().first().any { p -> p.name == name } } }
            }
            ActivityScenario.launch(MainActivity::class.java).use {
                openPlaylists()
                compose.onNodeWithText(name).performClick()
                compose.waitUntil(10_000) { compose.onAllNodesWithText("Phone track").fetchSemanticsNodes().size == 2 }
                compose.onAllNodesWithText("No audio available in your library", substring = true).assertCountEquals(2)
            }
        } finally {
            resolver.delete(uri, null, null)
            runBlocking {
                repository.playlists().first().singleOrNull { it.name == name }?.let {
                    repository.deletePlaylist(null, LocalId(it.localPlaylistId), LocalId.random(), System.currentTimeMillis())
                }
                settings.update(original)
            }
        }
    }

    @Test fun shareActionHandsFrozenLinkToAndroid() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        val settings = applicationNonSecretSettingsStore(context)
        val original = runBlocking { settings.settings.first() }
        val port = InstrumentationRegistry.getArguments().getString("playlistShareQaPort")?.toIntOrNull()
        Assume.assumeTrue(context.packageName == "app.autplay.qa" && original.activeServerProfileId == null &&
            InstrumentationRegistry.getArguments().getString("playlistShareUi") == "true" &&
            port != null && port in 1024..65535)
        val binding = app.autplay.application.sync.ClientEventBinding(app.autplay.domain.UserId(UUID.randomUUID().toString()),
            app.autplay.domain.DeviceId(UUID.randomUUID().toString()), app.autplay.domain.ServerProfileId(UUID.randomUUID().toString()))
        val credentials = app.autplay.data.security.AndroidKeystoreCredentialStore(context)
        val repository = LibraryVerticalSliceRepository(AutPlayRuntime.database(context))
        val id = LocalId.random()
        val name = "Share QA ${id.value.take(8)}"
        val origin = "http://127.0.0.1:$port"
        val sent = java.util.concurrent.atomic.AtomicReference<android.content.Intent>()
        val monitor = object : android.app.Instrumentation.ActivityMonitor() {
            override fun onStartActivity(intent: android.content.Intent): android.app.Instrumentation.ActivityResult? {
                if (intent.action != android.content.Intent.ACTION_CHOOSER) return null
                @Suppress("DEPRECATION")
                sent.set(intent.getParcelableExtra(android.content.Intent.EXTRA_INTENT))
                return android.app.Instrumentation.ActivityResult(android.app.Activity.RESULT_CANCELED, null)
            }
        }
        instrumentation.addMonitor(monitor)
        try {
            runBlocking {
                credentials.write(binding.serverProfileId, "qa-playlist-publisher".toByteArray())
                settings.update(original.copy(appLanguage = "EN", pendingPublicId = "qa_playlist",
                    onboardingRevision = CURRENT_ONBOARDING_REVISION, activeServerProfileId = binding.serverProfileId,
                    activeUserId = binding.userId, deviceId = binding.deviceId, serverBaseUrl = origin, streamBaseUrl = origin))
                repository.createPlaylist(binding, id, LocalId.random(), name, null, System.currentTimeMillis())
            }
            ActivityScenario.launch(MainActivity::class.java).use {
                openPlaylists()
                compose.onNodeWithText(name).performClick()
                compose.onNodeWithText("Share playlist").performClick()
                compose.onNodeWithText("Create link").performClick()
                compose.waitUntil(15_000) { sent.get() != null }
                val intent = sent.get()
                Assert.assertEquals(android.content.Intent.ACTION_SEND, intent.action)
                Assert.assertEquals("text/plain", intent.type)
                val link = checkNotNull(intent.getStringExtra(android.content.Intent.EXTRA_TEXT))
                Assert.assertTrue(link.startsWith("$origin/api/v1/playlist-shares/"))
                val file = app.autplay.application.library.SharedPlaylist.decode(java.net.URL("$link/file").readBytes())
                Assert.assertEquals(name, file.name)
                Assert.assertTrue(file.entries.isEmpty())
            }
        } finally {
            instrumentation.removeMonitor(monitor)
            runBlocking {
                if (AutPlayRuntime.database(context).playlistDao().playlist(id.value) != null)
                    repository.deletePlaylist(binding, id, LocalId.random(), System.currentTimeMillis())
                settings.update(original)
                credentials.clear(binding.serverProfileId)
            }
        }
    }

    private fun openPlaylists() {
        val label = localizedAppContext(InstrumentationRegistry.getInstrumentation().targetContext, app.autplay.ui.AppLanguage.English).getString(R.string.nav_library)
        compose.waitUntil(12_000) { compose.onAllNodesWithText(label).fetchSemanticsNodes().isNotEmpty() }
        compose.onNode(hasText(label) and hasClickAction()).performClick()
        compose.onNodeWithTag("library-sections").performScrollToNode(hasTestTag("library-tab-Playlists"))
        compose.onNodeWithTag("library-tab-Playlists").performClick()
    }
}
