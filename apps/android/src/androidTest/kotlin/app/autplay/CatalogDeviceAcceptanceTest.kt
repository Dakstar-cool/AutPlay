package app.autplay

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import app.autplay.ui.AutPlayTheme
import app.autplay.ui.InternetMetadataDiscoverySection
import app.autplay.ui.playlist.AddTrackToPlaylistDialog
import app.autplay.ui.playlist.ManualPlaylistActions
import app.autplay.application.search.LibrarySearchKind
import app.autplay.application.server.InternetMetadataBrowseTarget
import app.autplay.application.server.InternetMetadataDiscoveryEntity
import kotlinx.coroutines.launch
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.espresso.Espresso
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.importing.ContentUriInspector
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.local.entity.*
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.*
import app.autplay.domain.*
import app.autplay.playback.*
import app.autplay.ui.AppLanguage
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import java.io.File

/** Opt-in phases run in separate processes on a QA phone and a second Android client. */
class CatalogDeviceAcceptanceTest {
    @get:Rule val compose = createEmptyComposeRule()
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val db get() = AutPlayRuntime.database(context)
    private val fixture by lazy { Json.parseToJsonElement(File(context.filesDir, "catalog-qa.json").readText()).jsonObject }
    private fun value(key: String) = fixture.getValue(key).jsonPrimitive.content
    private val binding by lazy { ClientEventBinding(UserId(value("user")), DeviceId(value("device")),
        ServerProfileId(value("profile")), LocalId(value("epoch"))) }
    private val prefs by lazy { context.getSharedPreferences("catalog-qa-results", Context.MODE_PRIVATE) }
    private val strings get() = localizedAppContext(context, AppLanguage.English)
    private val playlistName = "QA evening"
    private val server = "http://127.0.0.1:18761"

    @Before fun requireFixture() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("catalogQa") == "true")
        require(context.packageName == "app.autplay.qa" && Build.VERSION.SDK_INT >= 29)
    }

    private suspend fun setup(online: Boolean) {
        androidx.work.WorkManager.getInstance(context).cancelAllWork().result.get()
        AutPlayRuntime.closeDatabaseForTests()
        context.deleteDatabase("autplay.db")
        cleanupAudio()
        prefs.edit().clear().commit()
        val id = LocalId.random().value
        db.journalDao().insertLineage(JournalLineageEntity(id, value("user"), value("device"), value("epoch"), 1, 1))
        db.syncDao().upsertCursor(SyncCursorEntity(value("profile"), id, value("device"), value("epoch"),
            null, 0, 0, null, "NOT_STARTED", null, 1))
        applicationNonSecretSettingsStore(context).update(NonSecretSettings(
            activeServerProfileId = binding.serverProfileId, activeUserId = binding.userId,
            deviceId = binding.deviceId, serverBaseUrl = if (online) server else "https://offline.test",
            appLanguage = AppLanguage.English.storedValue, onboardingRevision = CURRENT_ONBOARDING_REVISION,
            pendingPublicId = "qa_catalog"))
        val envelope = SessionCredentialEnvelope(value("token"), null, 0)
        AndroidKeystoreCredentialStore(context).write(binding.serverProfileId, SessionCredentialEnvelopeCodec.encode(envelope))
    }

    private fun sample(): Uri {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Audio.Media.DISPLAY_NAME, "autplay-qa-${LocalId.random().value}.wav")
            put(MediaStore.Audio.Media.MIME_TYPE, "audio/wav")
            put(MediaStore.Audio.Media.IS_PENDING, 1)
        }
        val uri = requireNotNull(resolver.insert(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, values))
        val source = Uri.parse("content://${instrumentation.context.packageName}.readable/audio/process-home")
        resolver.openInputStream(source)!!.use { input -> resolver.openOutputStream(uri)!!.use(input::copyTo) }
        resolver.update(uri, ContentValues().apply { put(MediaStore.Audio.Media.IS_PENDING, 0) }, null, null)
        return uri
    }

    private fun node(matcher: SemanticsMatcher): SemanticsNodeInteraction {
        compose.waitUntil(20_000) {
            try {
                compose.onAllNodes(matcher).fetchSemanticsNodes().isNotEmpty()
            } catch (error: IllegalStateException) {
                if (error.message?.startsWith("No compose hierarchies found") != true) throw error
                false
            }
        }
        return compose.onNode(matcher)
    }
    private fun tapTag(tag: String) = node(hasTestTag(tag)).performClick()
    private fun action(label: Int) = node(hasText(strings.getString(label)) and hasClickAction()).performClick()
    private fun library() = action(R.string.nav_library)
    private fun tile(tab: String, id: String) {
        node(hasTestTag("library-sections")).performScrollToNode(hasTestTag("library-tab-$tab"))
        tapTag("library-tab-$tab")
        tapTag(id)
    }
    private suspend fun playlist() = db.playlistDao().activePlaylistsForProfile(value("profile"), 20).first().single { it.name == playlistName }
    private suspend fun entries() = db.playlistDao().activeEntries(playlist().localPlaylistId, 20).first()
    private fun editName() {
        action(R.string.playlist_create)
        compose.onAllNodes(hasSetTextAction())[0].performTextInput(playlistName)
        action(R.string.playlist_create_and_add)
    }
    private fun fromPlayer() {
        tapTag("player-more")
        action(R.string.playlist_add_selected)
    }

    @Test fun createAndReorderOnPhone(): Unit = runBlocking {
        setup(false)
        val titles = listOf("QA opening", "QA second track")
        val tracks = titles.map { title ->
            val id = LocalId.random()
            LibraryVerticalSliceRepository(db).importUri(binding, id, LocalId.random(), LocalId.random(), LocalId.random(),
                title, "SVRGN & RAGETRAIN", ContentUriInspector(context.contentResolver).inspect(sample().toString()),
                false, System.currentTimeMillis())
            id.value
        }
        val album = seedCollectionPages(context, db, value("profile"), tracks, titles,
            listOf("SVRGN", "RAGETRAIN"), seedPlaylist = false,
            serverRecordings = fixture.getValue("albumRecordings").jsonArray.map { it.jsonPrimitive.content })
        assertTrue(db.playlistDao().activePlaylists(20).first().isEmpty())
        ActivityScenario.launch(MainActivity::class.java).use {
            library()
            tile("Albums", "library-album:${album.album}")
            tapTag("album-track-${album.albumEntries[1]}")
            compose.waitUntil(15_000) { PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == tracks[1] } }
            compose.onNodeWithTag("mini-player-track", useUnmergedTree = true).performTouchInput { click(androidx.compose.ui.geometry.Offset(10f, center.y)) }
            fromPlayer()
            editName()
            compose.waitUntil(10_000) { runBlocking { runCatching { entries().size == 1 }.getOrDefault(false) } }
            val list = playlist()
            assertEquals("PRIVATE", list.visibility)
            val first = entries().single().localPlaylistEntryId
            fromPlayer()
            val radio = hasAnyAncestor(hasTestTag("playlist-target-${list.localPlaylistId}")) and hasClickAction()
            node(radio).performClick()
            action(R.string.playlist_add_selected)
            Espresso.pressBack()
            Espresso.pressBack()
            tile("Playlists", "library-playlist:${list.localPlaylistId}")
            val other = entries().last().localPlaylistEntryId
            assertNotEquals(first, other)
            assertEquals(listOf(tracks[1], tracks[1]), entries().map { it.localUserTrackRefId })
            tapTag("playlist-toggle-edit")
            val down = hasText(strings.getString(R.string.playlist_move_down)) and hasAnyAncestor(hasTestTag("playlist-entry-$first"))
            node(down).performClick()
            compose.waitUntil(10_000) { runBlocking { entries().map { it.localPlaylistEntryId } == listOf(other, first) } }
            prefs.edit().putString("first", first).putString("other", other).putString("track", tracks[1])
                .putString("artist", album.artistIds.last()).commit()
        }
    }

    private fun assertDisconnected() {
        val manager = context.getSystemService(android.net.ConnectivityManager::class.java)
        val resolver = context.contentResolver
        fun flag(name: String) = android.provider.Settings.Global.getInt(resolver, name, -1)
        val physicalInternet = manager.allNetworks.any { network ->
            val caps = manager.getNetworkCapabilities(network) ?: return@any false
            caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_NOT_VPN) &&
                caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        }
        val state = "airplane=${flag("airplane_mode_on")} wifi=${flag("wifi_on")} data=${flag("mobile_data")} validatedPhysical=$physicalInternet"
        assertTrue(state, flag("airplane_mode_on") == 1 && flag("wifi_on") == 0 && !physicalInternet)
        val reachable = runCatching {
            java.net.Socket().use { it.connect(java.net.InetSocketAddress("127.0.0.1", 18761), 500) }
        }.isSuccess
        assertFalse("Fixture server reachable while offline", reachable)
        android.util.Log.i("AutPlayCatalogQA", "$state fixtureReachable=$reachable")
    }

    @Test fun reopenOfflineOnPhone(): Unit = runBlocking {
        assertDisconnected()
        ActivityScenario.launch(MainActivity::class.java).use {
            library()
            tile("Playlists", "library-playlist:${playlist().localPlaylistId}")
            val saved = entries().map { it.localPlaylistEntryId }
            assertEquals(listOf(prefs.getString("other", null), prefs.getString("first", null)), saved)
            for (id in saved) node(hasTestTag("playlist-entry-$id")).assertIsDisplayed()
        }
    }

    @Test fun syncPhone(): Unit = runBlocking {
        applicationNonSecretSettingsStore(context).mutate { it.copy(serverBaseUrl = server) }
        val coordinator = AutPlayRuntime.syncCoordinator(context, binding)
        kotlinx.coroutines.withTimeout(120_000) {
            while (!coordinator.run(binding)) kotlinx.coroutines.delay(1_000)
        }
        val rows = entries()
        assertEquals(listOf(prefs.getString("other", null), prefs.getString("first", null)),
            rows.map { it.localPlaylistEntryId })
        val order = rows.map { requireNotNull(it.serverPlaylistEntryId) }
        assertEquals(2, order.distinct().size)
        File(context.filesDir, "qa-entry-order.txt").writeText(order.joinToString("\n"))
    }

    @Test fun playSecondOccurrenceOnSecondDevice(): Unit = runBlocking {
        setup(true)
        assertTrue(AutPlayRuntime.syncCoordinator(context, binding).run(binding))
        val list = playlist()
        val rows = entries()
        val expected = File(context.filesDir, "qa-entry-order.txt").readLines()
        assertEquals(expected, rows.map { it.serverPlaylistEntryId })
        assertEquals(1, rows.map { it.localUserTrackRefId }.distinct().size)
        val ref = rows.last().localUserTrackRefId
        // The media fixture is supplied independently on each device. Only the server supplies playlist entries.
        val now = System.currentTimeMillis()
        db.localAudioDao().upsertState(LocalAudioStateEntity(LocalId.random().value, ref, null, null, sample().toString(),
            false, null, null, null, null, null, null, null, null, null, null, "AVAILABLE", "USER_IMPORT", null, null, now, now, now))
        ActivityScenario.launch(MainActivity::class.java).use {
            library()
            tile("Playlists", "library-playlist:${list.localPlaylistId}")
            tapTag("playlist-entry-${rows.last().localPlaylistEntryId}")
            compose.waitUntil(15_000) { PlaybackRuntimeState.state.value.let { it.isPlaying && it.localUserTrackRefId == ref } }
            val queue = requireNotNull(db.queueDao().activeSnapshotOnce())
            assertEquals(list.localPlaylistId, queue.sourceContextId)
            val played = db.queueDao().entries(queue.queueSnapshotId, 10)
            assertEquals(2, played.size)
            assertEquals(played.last().queueEntryId, queue.currentEntryId)
        }
    }

    private fun catalogue(edition: Boolean): ActivityScenario<ComponentActivity> {
        val activity = ActivityScenario.launch(ComponentActivity::class.java)
        activity.onActivity { host ->
            host.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            host.setContent {
                CompositionLocalProvider(LocalContext provides strings) {
                    AutPlayTheme {
                        val scope = rememberCoroutineScope()
                        var selected by remember { mutableStateOf<String?>(null) }
                        Column(Modifier.verticalScroll(rememberScrollState())) {
                            InternetMetadataDiscoverySection("QA Quartet", LibrarySearchKind.Artist, 1,
                                onPlaylistTrack = { selected = it },
                                initialTarget = if (edition) InternetMetadataBrowseTarget(
                                    InternetMetadataDiscoveryEntity.RELEASE, value("release")) else null)
                        }
                        selected?.let { track ->
                            AddTrackToPlaylistDialog(track, emptyList(), ManualPlaylistActions(createWithTrack = { op, name, desc, ref ->
                                scope.launch { LibraryVerticalSliceRepository(db).createPlaylistWithTrack(binding,
                                    LocalId(op), name, desc, LocalId(ref), System.currentTimeMillis()) }
                            }), { selected = null })
                        }
                    }
                }
            }
        }
        return activity
    }

    @Test fun verifyDefaultArtistDownloadScope(): Unit = runBlocking {
        setup(true)
        catalogue(false).use {
            val artist = hasTestTag("catalogue-card-musicbrainz:artist:${value("artist")}")
            node(hasText(strings.getString(R.string.library_section_albums)) and hasAnyAncestor(artist)).performClick()
            action(R.string.catalog_download_artist)
            node(hasText(strings.getString(R.string.catalog_download_scope))).assertIsDisplayed()
            node(isToggleable()).assertIsOff()
            action(R.string.catalog_download_preview)
            node(hasText(strings.getString(R.string.catalog_download_counts, 1, 0, 0, 1))).assertExists()
        }
    }

    @Test fun browseNewProfileAndSaveTrack(): Unit = runBlocking {
        setup(true)
        catalogue(false).use {
            val artist = hasTestTag("catalogue-card-musicbrainz:artist:${value("artist")}")
            node(hasText(strings.getString(R.string.library_section_albums)) and hasAnyAncestor(artist)).performClick()
            tapTag("catalogue-browse-musicbrainz:release:${value("release")}")
            node(hasText("QA Tone")).assertExists()
            node(hasText("QA Silence")).assertExists()
            node(hasText(strings.getString(R.string.catalogue_vault_count, 0, 2))).assertExists()
            val track = hasTestTag("catalogue-card-musicbrainz:release_track:${value("track")}")
            node(hasText(strings.getString(R.string.catalogue_add_to_playlist)) and hasAnyAncestor(track)).performClick()
            editName()
            compose.waitUntil(10_000) { runBlocking { runCatching { entries().size == 1 }.getOrDefault(false) } }
            assertEquals("CATALOG_ONLY", db.libraryDao().trackRef(entries().single().localUserTrackRefId)?.resolutionStatus)
        }
        assertTrue(AutPlayRuntime.syncCoordinator(context, binding).run(binding))
    }

    @Test fun reopenEditionOffline(): Unit = runBlocking {
        assertDisconnected()
        val key = "${binding.userId.value}:RELEASE:${value("release")}:false:0"
        val page = requireNotNull(db.catalogProjectionDao().catalogPage(value("profile"), key))
        val count = Json.parseToJsonElement(page.document).jsonObject.getValue("vault_count").jsonPrimitive.int
        catalogue(true).use {
            node(hasText("QA Tone")).assertExists()
            node(hasText("QA Silence")).assertExists()
            node(hasText(strings.getString(R.string.catalogue_vault_count, count, 2))).assertExists()
        }
    }

    @Test fun startExplicitDownload(): Unit = runBlocking {
        catalogue(true).use {
            node(hasText("QA Tone")).assertExists()
            action(R.string.catalog_download_release)
            action(R.string.catalog_download_preview)
            node(hasText(strings.getString(R.string.catalog_source_missing), substring = true)).assertExists()
            action(R.string.catalog_download_start)
            node(hasText(strings.getString(R.string.catalog_download_counts, 0, 1, 0, 1))).assertExists()
        }
    }

    @Test fun verifyCompletedDownload(): Unit = runBlocking {
        catalogue(true).use {
            compose.waitUntil(85_000) { compose.onAllNodesWithText(strings.getString(R.string.catalogue_vault_count, 1, 2))
                .fetchSemanticsNodes().isNotEmpty() }
            node(hasText(strings.getString(R.string.catalogue_in_vault))).assertExists()
        }
        assertTrue(AutPlayRuntime.syncCoordinator(context, binding).run(binding))
        val row = entries().single()
        assertEquals("RESOLVED", db.libraryDao().trackRef(row.localUserTrackRefId)?.resolutionStatus)
        ActivityScenario.launch(MainActivity::class.java).use {
            library()
            tile("Playlists", "library-playlist:${playlist().localPlaylistId}")
            node(hasTestTag("playlist-entry-${row.localPlaylistEntryId}") and hasClickAction()).assertIsEnabled()
        }
    }

    @Test fun repeatExplicitDownload(): Unit = runBlocking {
        catalogue(true).use {
            node(hasText("QA Tone")).assertExists()
            action(R.string.catalog_download_release)
            action(R.string.catalog_download_preview)
            node(hasText(strings.getString(R.string.catalog_download_counts, 1, 0, 0, 1))).assertExists()
            action(R.string.catalog_download_start)
            node(hasText(strings.getString(R.string.catalog_download_preview)) and hasClickAction()).assertExists()
        }
    }

    @Test fun talkBackChoosesArtistAboveQueue(): Unit = runBlocking {
        val track = requireNotNull(prefs.getString("track", null))
        val automation = requireNotNull(instrumentation.getUiAutomation(android.app.UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES))
        fun shell(command: String) = android.os.ParcelFileDescriptor.AutoCloseInputStream(
            automation.executeShellCommand(command)).bufferedReader().use { it.readText().trim() }
        automation.serviceInfo = automation.serviceInfo.apply {
            flags = flags or android.accessibilityservice.AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
        }
        val old = shell("settings get secure enabled_accessibility_services")
        val reader = "com.google.android.marvin.talkback/.TalkBackService"
        val trace = File(context.filesDir, "qa-talkback-focus.txt")
        trace.writeText("")
        fun focus(label: String, heading: Boolean = false): android.view.accessibility.AccessibilityNodeInfo {
            val root = requireNotNull(automation.rootInActiveWindow)
            check(root.packageName == context.packageName) { "Foreground: ${root.packageName}" }
            fun descendants(n: android.view.accessibility.AccessibilityNodeInfo): List<android.view.accessibility.AccessibilityNodeInfo> =
                listOf(n) + (0 until n.childCount).mapNotNull(n::getChild).flatMap(::descendants)
            val nodes = descendants(root)
            val node = nodes.firstOrNull { it.isVisibleToUser &&
                (it.text?.contains(label) == true || it.contentDescription?.contains(label) == true) &&
                (!heading || it.isHeading) } ?: error("Missing $label in QA window: " +
                    nodes.mapNotNull { it.text ?: it.contentDescription }.joinToString(" | "))

            fun focused() = automation.findFocus(android.view.accessibility.AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
            fun text(n: android.view.accessibility.AccessibilityNodeInfo?): String {
                if (n == null) return ""
                return (listOfNotNull(n.text, n.contentDescription).joinToString(" ") + " " +
                    (0 until n.childCount).joinToString(" ") { text(n.getChild(it)) }).trim()
            }
            val target = generateSequence(node) { it.parent }.firstOrNull { it.isScreenReaderFocusable || it.isClickable || it.isHeading } ?: node
            target.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_ACCESSIBILITY_FOCUS)
            android.os.SystemClock.sleep(300)
            trace.appendText("requested=$label focus=${text(focused())} targetFocused=${target.refresh() && target.isAccessibilityFocused}\n")
            compose.waitUntil(5_000) { text(focused()).contains(label) || (target.refresh() && target.isAccessibilityFocused) }
            val current = focused()?.takeIf { text(it).contains(label) } ?: target
            trace.appendText("focus=${text(current)} heading=${node.isHeading} click=${current.isClickable} " +
                "actions=${current.actionList.mapNotNull { it.label }.joinToString()}\n")
            android.os.SystemClock.sleep(3_000)
            return current
        }
        try {
            shell("settings put secure enabled_accessibility_services $old:$reader")
            compose.waitUntil(15_000) { context.getSystemService(android.view.accessibility.AccessibilityManager::class.java)
                .isTouchExplorationEnabled }
            android.os.SystemClock.sleep(3_000)
            ActivityScenario.launch(MainActivity::class.java).use { screen ->
                screen.onActivity { it.window.addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) }
                library()
                tapTag("library-track-$track")
                compose.waitUntil(15_000) { PlaybackRuntimeState.state.value.localUserTrackRefId == track }
                tapTag("persistent-mini-player")
                tapTag("player-open-queue")
                node(hasTestTag("queue-open-artists")).assertIsDisplayed()
                val before = requireNotNull(db.queueDao().activeSnapshotOnce())
                trace.appendText("ready\n")
                val recordAck = File(context.filesDir, "qa-recording-started")
                compose.waitUntil(25_000) { recordAck.exists() }
                val queueAction = strings.getString(R.string.catalog_current_track_artists)
                val click = android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK
                check(focus(queueAction).performAction(click))
                node(hasTestTag("player-artist-picker")).assertIsDisplayed()
                focus(strings.getString(R.string.library_section_artists), heading = true)
                focus("SVRGN")
                check(focus("RAGETRAIN").performAction(click))
                node(hasText("RAGETRAIN") and isHeading()).assertIsDisplayed()
                focus("RAGETRAIN", heading = true)
                check(automation.performGlobalAction(android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK))
                node(hasTestTag("queue-open-artists")).assertIsDisplayed()
                focus(queueAction)
                val after = requireNotNull(db.queueDao().activeSnapshotOnce())
                assertEquals(before.queueSnapshotId, after.queueSnapshotId)
                assertEquals(before.currentEntryId, after.currentEntryId)
                trace.appendText("queue and current entry preserved\n")
            }
        } finally {
            trace.appendText("finished\n")
            shell("settings put secure enabled_accessibility_services $old")
        }
    }

    @Test fun cleanAcceptanceFixture(): Unit = runBlocking {
        cleanupAudio()
        AndroidKeystoreCredentialStore(context).clear(binding.serverProfileId)
    }

    private fun cleanupAudio() {
        val resolver = context.contentResolver
        val collection = MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
        val ids = mutableListOf<Long>()
        resolver.query(collection, arrayOf(MediaStore.Audio.Media._ID, MediaStore.Audio.Media.DISPLAY_NAME),
            "owner_package_name = ? AND _display_name LIKE ?", arrayOf(context.packageName, "autplay-qa-%.wav"), null)?.use { rows ->
            val filename = Regex("autplay-qa-[0-9a-f-]{36}\\.wav")
            while (rows.moveToNext()) if (filename.matches(rows.getString(1))) ids += rows.getLong(0)
        }
        ids.forEach { resolver.delete(android.content.ContentUris.withAppendedId(collection, it), null, null) }
        prefs.edit().remove("samples").commit()
    }
}
