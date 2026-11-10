package app.autplay.desktop.acceptance

import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopImportPort
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEditTarget
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.playback.ActiveQueueContext
import app.autplay.desktop.DesktopRuntime
import app.autplay.desktop.lifecycle.DesktopWindowLifecycle
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.desktop.settings.DesktopSettingsController
import app.autplay.desktop.systemmedia.DesktopSystemMediaApi
import app.autplay.desktop.systemmedia.DesktopSystemMediaCommand
import app.autplay.desktop.systemmedia.DesktopSystemMediaContract
import app.autplay.desktop.systemmedia.DesktopSystemMediaListener
import app.autplay.desktop.systemmedia.DesktopSystemMediaOwner
import app.autplay.desktop.systemmedia.DesktopSystemMediaPayload
import app.autplay.desktop.systemmedia.DesktopSystemMediaProjection
import app.autplay.desktop.systemmedia.DesktopSystemMediaSnapshot
import app.autplay.desktop.systemmedia.projectDesktopSystemMedia
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackSourcePresentation
import app.autplay.playback.presentation.PlaybackStatus
import java.lang.reflect.Proxy
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*

/** Called by the existing queue composite. No @Test, native load, media IO or database. */
internal suspend fun assertDesktopSystemMediaPolicy(): Unit {
    assertSystemMediaProjectionPolicy()
    assertSystemMediaOwnerPolicy()
    assertSystemMediaPublisherPolicy()
    assertSystemMediaHostPolicy(unavailable = false)
    assertSystemMediaHostPolicy(unavailable = true)
    assertSystemMediaHostPolicy(unavailable = false, reopenRequired = true)
}

private val mediaTarget = DesktopPlaybackTarget("queue-a", "entry-a", "listen-a", 1)
private val mediaTrackA = DesktopTrack("track-a", "Title A", "Artist A", 8_000, "a".repeat(64), 100)
private val mediaTrackB = DesktopTrack("track-b", "Title B", "Artist B", 8_000, "b".repeat(64), 100)
private val mediaLibrary = DesktopLibrarySnapshot(tracks = listOf(mediaTrackA, mediaTrackB))

private fun mediaPlayer(
    target: DesktopPlaybackTarget = mediaTarget,
    track: DesktopTrack = mediaTrackA,
): DesktopPlayerState = DesktopPlayerState(
    playback = PlaybackPresentationState(mediaId = target.entryId, title = "obsolete label", artist = "obsolete artist",
        positionMs = 1_000, durationMs = 8_000, isSeekable = true, isPlaying = false,
        connection = PlaybackConnectionState.Connected, playbackStatus = PlaybackStatus.Ready,
        source = PlaybackSourcePresentation.Local, context = ActiveQueueContext.Loaded(target.queueId, target.entryId, "LIBRARY"),
        controls = PlaybackControlGate.Allowed, seekEnabled = true, nextMediaId = "entry-next", localTrackRefId = track.id),
    target = target,
    queueEditor = DesktopQueueEditorState(DesktopQueueEditTarget(target.queueId, target.entryId, target.sessionId, target.generation, 1),
        listOf(DesktopQueueEntry(target.entryId, track.id), DesktopQueueEntry("entry-next", track.id)), target.entryId),
)

private fun mediaSnapshot(target: DesktopPlaybackTarget = mediaTarget): DesktopSystemMediaSnapshot =
    DesktopSystemMediaSnapshot(target, "Title A", "Artist A", 1_000, 8_000, false, DesktopSystemMediaContract.ALL_COMMANDS)

private fun assertSystemMediaProjectionPolicy(): Unit {
    val artworkA = DesktopArtwork("c".repeat(64), byteArrayOf(1), 1, 1)
    val artworkB = DesktopArtwork("d".repeat(64), byteArrayOf(2), 1, 1)
    val metadata = DesktopMetadataState(artworks = mapOf(mediaTrackA.id to artworkA, mediaTrackB.id to artworkB))
    val player = mediaPlayer()
    fun project(value: DesktopPlayerState = player, library: DesktopLibrarySnapshot = mediaLibrary,
        error: String? = null, hostError: String? = null): DesktopSystemMediaSnapshot? =
        projectDesktopSystemMedia(value, library, metadata, error, hostError)
    val projected = checkNotNull(project())
    assertEquals(mediaTarget, projected.target)
    assertEquals(mediaTrackA.title, projected.title)
    assertEquals(mediaTrackA.artist, projected.artist)
    assertSame(artworkA, projected.artwork)
    assertFalse(projected.isPlaying)
    assertEquals(1_000L, projected.positionMs)
    assertEquals(8_000L, projected.durationMs)
    assertEquals(27, projected.allowedCommands) // PLAY | PAUSE | NEXT | SEEK
    assertEquals(31, project(player.copy(playback = player.playback.copy(positionMs = 3_001)))?.allowedCommands)
    assertEquals(0, project(player.copy(playback = player.playback.copy(
        controls = PlaybackControlGate.Locked(PlaybackControlLockReason.COMMAND_UNAVAILABLE))))?.allowedCommands)
    val unknown = checkNotNull(project(player.copy(playback = player.playback.copy(isSeekable = false))))
    assertNull(unknown.durationMs) // Persisted/header duration is not current native duration.
    assertEquals(0, unknown.allowedCommands and DesktopSystemMediaContract.SEEK)
    assertEquals(0, checkNotNull(project(player.copy(playback = player.playback.copy(seekEnabled = false))))
        .allowedCommands and DesktopSystemMediaContract.SEEK)
    assertNull(project(player.copy(target = null)))
    assertNull(project(player.copy(target = mediaTarget.copy(entryId = "other"))))
    assertNull(project(player.copy(target = mediaTarget.copy(sessionId = "other"))))
    assertNull(project(player.copy(target = mediaTarget.copy(generation = 2))))
    assertNull(project(player.copy(queueEditor = player.queueEditor.copy(entries = listOf(DesktopQueueEntry(mediaTarget.entryId, "other"))))))
    assertNull(project(player.copy(queueEditor = player.queueEditor.copy(entries = player.queueEditor.entries + player.queueEditor.entries.first()))))
    assertNull(project(player.copy(playback = player.playback.copy(localTrackRefId = mediaTrackB.id))))
    assertNull(project(player.copy(playback = player.playback.copy(connection = PlaybackConnectionState.Disconnected))))
    assertNull(project(player.copy(playback = player.playback.copy(playbackStatus = PlaybackStatus.Buffering, isPlaying = true))))
    assertNull(project(player.copy(playback = player.playback.copy(positionMs = 8_001))))
    assertNull(project(library = mediaLibrary.copy(tracks = listOf(mediaTrackB))))
    assertNull(project(library = mediaLibrary.copy(tracks = listOf(mediaTrackA.copy(title = "\u0000")))))
    listOf("AUDIO_ERROR", "SOURCE_UNAVAILABLE", "PLAYBACK_OPERATION_FAILED", "desktop_snapshot_unavailable")
        .forEach { assertNull(project(error = it)) }
    assertNull(project(hostError = "desktop_snapshot_unavailable"))
    assertNotNull(project(error = "QUEUE_LIMIT"))
    val duplicate = mediaTarget.copy(entryId = "duplicate-entry")
    assertEquals(duplicate, project(mediaPlayer(duplicate))?.target)
    assertSame(artworkA, project(mediaPlayer(duplicate))?.artwork)
    assertSame(artworkB, project(mediaPlayer(mediaTarget.copy(entryId = "entry-b"), mediaTrackB))?.artwork)
    assertNull(projectDesktopSystemMedia(player, mediaLibrary,
        DesktopMetadataState(artworks = mapOf(mediaTrackB.id to artworkB)), null, null)?.artwork)
}

private class MediaApiFixture : DesktopSystemMediaApi {
    var abi: Int = 1
    var acquireResult: Long = 7
    var acquireFailure: Boolean = false
    @Volatile var acceptPublication: Boolean = true
    @Volatile var releaseOnRetire: Boolean = true
    @Volatile var released: Boolean = false
    @Volatile var listener: DesktopSystemMediaListener? = null
    @Volatile var retainedCallback: DesktopSystemMediaListener? = null
    val acquisitions = AtomicInteger()
    val retirements = AtomicInteger()
    val publications = CopyOnWriteArrayList<DesktopSystemMediaPayload?>()
    override fun abiVersion(): Int = abi
    override fun acquire(expectedAbi: Int, listener: DesktopSystemMediaListener): Long {
        acquisitions.incrementAndGet()
        assertEquals(1, expectedAbi)
        if (acquireFailure) throw IllegalStateException("fixture_acquire_failed")
        if (acquireResult > 0) { this.listener = listener; retainedCallback = listener }
        return acquireResult
    }
    override fun publish(lease: Long, payload: DesktopSystemMediaPayload?): Boolean {
        assertEquals(7L, lease)
        if (!acceptPublication) return false
        publications.add(payload)
        return true
    }
    override fun retire(lease: Long): Unit {
        assertEquals(7L, lease)
        retirements.incrementAndGet()
        listener = null
        if (releaseOnRetire) released = true
    }
    override fun isReleased(lease: Long): Boolean = released
    fun latest(): DesktopSystemMediaPayload = checkNotNull(publications.last())
    fun command(id: Int, token: Long = latest().targetToken, position: Long = -1): Boolean =
        retainedCallback?.onCommand(id, token, position) == true
}

private suspend fun withMediaOwner(
    api: MediaApiFixture = MediaApiFixture(),
    onCommand: (DesktopSystemMediaCommand) -> Boolean = { true },
    block: suspend (DesktopSystemMediaOwner, MediaApiFixture) -> Unit,
): Unit {
    val owner = DesktopSystemMediaOwner(api, onCommand)
    try { block(owner, api) } finally { api.released = true; owner.close() }
}

private suspend fun assertSystemMediaOwnerPolicy(): Unit {
    assertEquals(1, DesktopSystemMediaContract.ABI_VERSION)
    val unavailable = DesktopSystemMediaOwner(null) { false }
    try { assertEquals("system_media_unavailable", unavailable.state.errorCode) } finally { unavailable.close() }
    for (api in listOf(MediaApiFixture().apply { abi = 2 }, MediaApiFixture().apply { acquireResult = 0 },
        MediaApiFixture().apply { acquireFailure = true })) {
        withMediaOwner(api) { owner, fake ->
            assertFalse(owner.state.acquired)
            assertNull(fake.listener)
            assertFalse(owner.publish(mediaSnapshot()))
            if (fake.abi != 1) assertEquals(0, fake.acquisitions.get())
        }
    }
    val commands = CopyOnWriteArrayList<DesktopSystemMediaCommand>()
    withMediaOwner(onCommand = { commands.add(it) }) { owner, fake ->
        val duplicate = DesktopSystemMediaOwner(MediaApiFixture()) { false }
        try { assertEquals("system_media_owner_active", duplicate.state.errorCode) } finally { duplicate.close() }
        assertTrue(owner.publish(mediaSnapshot()))
        val first = fake.latest().targetToken
        assertTrue(first > 0)
        assertEquals("Title A", fake.latest().title)
        assertEquals(1_000L, fake.latest().positionMs)
        assertEquals(8_000L, fake.latest().durationMs)
        assertEquals(31, fake.latest().allowedCommands)
        assertFalse(fake.latest().isPlaying)
        assertTrue(fake.command(1))
        assertEquals(DesktopSystemMediaCommand(mediaTarget, DesktopTransportAction.PLAY), commands.last())
        assertTrue(owner.publish(mediaSnapshot().copy(title = "Renamed", artist = "Edited", positionMs = 2_000, durationMs = 9_000)))
        assertEquals(first, fake.latest().targetToken)
        val artwork = DesktopArtwork("e".repeat(64), byteArrayOf(3), 1, 1)
        assertTrue(owner.publish(mediaSnapshot().copy(artwork = artwork)))
        assertEquals(first, fake.latest().targetToken)
        assertSame(artwork, fake.latest().artwork)
        assertTrue(owner.publish(mediaSnapshot().copy(isPlaying = true)))
        val playing = fake.latest().targetToken
        assertTrue(playing > first)
        assertFalse(fake.command(1, first)) // Captured old toggle PLAY cannot pause the new meaning.
        assertTrue(fake.command(2, playing))
        assertEquals(DesktopTransportAction.PAUSE, commands.last().action)
        assertTrue(owner.publish(mediaSnapshot().copy(allowedCommands = DesktopSystemMediaContract.PLAY)))
        assertTrue(fake.latest().targetToken > playing)
        assertFalse(fake.command(2))
        assertFalse(fake.command(1, position = 0))
        assertFalse(fake.command(6))
        val targets = listOf(mediaTarget.copy(entryId = "duplicate-entry"), mediaTarget,
            mediaTarget.copy(sessionId = "listen-b"), mediaTarget.copy(sessionId = null),
            mediaTarget.copy(generation = 2), mediaTarget.copy(queueId = "queue-b"))
        for (target in targets) {
            val oldToken = fake.latest().targetToken
            assertTrue(owner.publish(mediaSnapshot(target)))
            assertTrue(fake.latest().targetToken > oldToken)
            assertFalse(fake.command(1, oldToken))
            assertTrue(fake.command(1))
            assertEquals(target, commands.last().target)
        }
        assertTrue(fake.command(5, position = 0))
        assertEquals(0L, commands.last().positionMs)
        assertTrue(fake.command(5, position = 8_000))
        assertFalse(fake.command(5, position = -1))
        assertFalse(fake.command(5, position = 8_001))
        assertFalse(fake.command(5, position = Long.MAX_VALUE))
        val beforeDuration = fake.latest().targetToken
        assertTrue(owner.publish(mediaSnapshot(targets.last()).copy(durationMs = 2_000)))
        assertEquals(beforeDuration, fake.latest().targetToken)
        assertFalse(fake.command(5, beforeDuration, 8_000))
        val beforeClear = fake.latest().targetToken
        assertTrue(owner.publish(null))
        assertNull(fake.publications.last())
        assertFalse(fake.command(1, beforeClear))
        assertTrue(owner.publish(mediaSnapshot()))
        assertTrue(fake.latest().targetToken > beforeClear)
        val beforeRetire = fake.latest().targetToken
        owner.retire()
        assertNull(fake.listener)
        assertFalse(fake.command(1, beforeRetire))
        assertFalse(owner.publish(mediaSnapshot()))
    }
    val invalid = listOf(mediaSnapshot().copy(title = ""), mediaSnapshot().copy(artist = "\u0000"),
        mediaSnapshot().copy(title = "x".repeat(4_097)), mediaSnapshot().copy(positionMs = -1),
        mediaSnapshot().copy(positionMs = 8_001), mediaSnapshot().copy(durationMs = null),
        mediaSnapshot().copy(durationMs = DesktopSystemMediaContract.MAX_POSITION_MS + 1),
        mediaSnapshot().copy(allowedCommands = 32), mediaSnapshot(mediaTarget.copy(generation = -1)))
    for (snapshot in invalid) withMediaOwner { owner, fake ->
        assertFalse(owner.publish(snapshot))
        assertEquals("system_media_invalid_publication", owner.state.errorCode)
        assertNull(fake.listener)
    }
    withMediaOwner { owner, fake ->
        assertTrue(owner.publish(mediaSnapshot()))
        val token = fake.latest().targetToken
        fake.acceptPublication = false
        assertFalse(owner.publish(mediaSnapshot()))
        assertEquals("system_media_publication_failed", owner.state.errorCode)
        assertFalse(fake.command(1, token))
        assertNull(fake.listener)
    }
    var acceptsCommand = false
    withMediaOwner(onCommand = { acceptsCommand }) { owner, fake ->
        assertTrue(owner.publish(mediaSnapshot()))
        val token = fake.latest().targetToken
        assertFalse(fake.command(1, token))
        acceptsCommand = true
        assertTrue(fake.command(1, token)) // Refusal did not retire or replace the captured target.
    }
    withMediaOwner(onCommand = { error("fixture_host_refusal") }) { owner, fake ->
        owner.publish(mediaSnapshot())
        assertFalse(fake.command(1))
    }
    coroutineScope {
        withMediaOwner(MediaApiFixture().apply { releaseOnRetire = false }) { owner, fake ->
            val closing = async(start = CoroutineStart.UNDISPATCHED) { owner.close() }
            eventually("retirement precedes cancelled cleanup") { fake.retirements.get() == 1 }
            closing.cancel()
            assertNull(fake.listener)
            fake.released = true
            withTimeout(2_000) { closing.join() }
            assertTrue(owner.state.closed)
        }
    }
    withMediaOwner(MediaApiFixture().apply { releaseOnRetire = false }) { owner, fake ->
        // The real bounded disposal loop is exercised only when this helper is later authorized.
        withTimeout(8_000) { owner.close() }
        assertFalse(owner.state.closed)
        assertEquals("system_media_teardown_timeout", owner.state.errorCode)
        val refused = DesktopSystemMediaOwner(MediaApiFixture()) { false }
        try { assertEquals("system_media_owner_active", refused.state.errorCode) } finally { refused.close() }
        fake.released = true
        owner.close()
        assertTrue(owner.state.closed)
    }
}

private suspend fun assertSystemMediaPublisherPolicy(): Unit {
    val scopeJob = SupervisorJob()
    val scope = CoroutineScope(scopeJob + Dispatchers.Default)
    val player = MutableStateFlow(mediaPlayer())
    val library = MutableStateFlow(mediaLibrary)
    val onLibraryRead = AtomicReference<(() -> Unit)?>(null)
    val observedLibrary = object : StateFlow<DesktopLibrarySnapshot> by library {
        override val value: DesktopLibrarySnapshot get() { onLibraryRead.getAndSet(null)?.invoke(); return library.value }
    }
    val metadata = MutableStateFlow(DesktopMetadataState())
    val playbackError = MutableStateFlow<String?>(null)
    val hostError = MutableStateFlow<String?>(null)
    val commands = CopyOnWriteArrayList<DesktopSystemMediaCommand>()
    val fake = MediaApiFixture()
    val publisher = DesktopSystemMediaProjection(fake, scope, player, observedLibrary, metadata,
        playbackError, hostError, { true }, { commands.add(it) })
    try {
        eventually("first coherent media publication") { fake.publications.lastOrNull()?.title == mediaTrackA.title }
        val originalToken = fake.latest().targetToken
        val b = mediaPlayer(mediaTarget.copy(entryId = "entry-b", generation = 2), mediaTrackB)
        val offset = fake.publications.size
        // Replace target while an A sample joins metadata. Its final recheck must discard A.
        onLibraryRead.set { player.value = b }
        metadata.value = DesktopMetadataState(requestId = "trigger-stale-sample")
        eventually("metadata race publishes the newer target") { fake.publications.lastOrNull()?.title == mediaTrackB.title }
        assertTrue(fake.publications.drop(offset).filterNotNull().all { it.title == mediaTrackB.title })
        assertFalse(fake.command(1, originalToken))
        assertTrue(fake.command(1))
        assertEquals(b.target, commands.last().target)
        val bToken = fake.latest().targetToken
        library.value = mediaLibrary.copy(tracks = listOf(mediaTrackA.copy(title = "Edit unrelated to B"), mediaTrackB.copy(title = "Renamed B")))
        eventually("labels retain the same target token") { fake.publications.lastOrNull()?.title == "Renamed B" }
        assertEquals(bToken, fake.latest().targetToken)
        playbackError.value = "AUDIO_ERROR"
        eventually("terminal audio failure clears now playing") { fake.publications.lastOrNull() == null }
        playbackError.value = null
        eventually("later confirmed item can publish") { fake.publications.lastOrNull() != null }
        publisher.refuse()
        val refusedOffset = fake.publications.size
        assertNull(fake.publications.last())
        player.value = mediaPlayer(mediaTarget.copy(generation = 3))
        metadata.value = DesktopMetadataState(requestId = "after-refusal")
        publisher.retire()
        val oldCallback = checkNotNull(fake.retainedCallback)
        assertFalse(oldCallback.onCommand(1, bToken, -1))
        assertNull(fake.listener)
        publisher.close()
        assertTrue(publisher.state.closed)
        assertTrue(fake.publications.drop(refusedOffset).all { it == null })
    } finally { fake.released = true; publisher.close(); scopeJob.cancelAndJoin() }

    // Failed startup/cancellation before the collector dispatch must still unwind an acquired lease.
    val cancelledJob = SupervisorJob().also { it.cancel() }
    val cancelledApi = MediaApiFixture()
    val cancelled = DesktopSystemMediaProjection(cancelledApi, CoroutineScope(cancelledJob + Dispatchers.Default),
        player, library, metadata, playbackError, hostError, { true }, { false })
    try {
        withTimeout(2_000) { cancelled.close() }
        assertTrue(cancelled.state.closed)
        assertNull(cancelledApi.listener)
        assertTrue(cancelledApi.publications.isEmpty())
    } finally { cancelledApi.released = true; cancelled.close() }

    val failedJob = SupervisorJob()
    val failedApi = MediaApiFixture()
    val failedLibrary = object : StateFlow<DesktopLibrarySnapshot> by library {
        override val value: DesktopLibrarySnapshot get() = error("fixture_projection_startup_failure")
    }
    val failed = DesktopSystemMediaProjection(failedApi, CoroutineScope(failedJob + Dispatchers.Default),
        player, failedLibrary, metadata, playbackError, hostError, { true }, { false })
    try {
        eventually("failed publisher startup disposes its lease") { failed.state.closed }
        assertNull(failedApi.listener)
        assertTrue(failedApi.publications.isEmpty())
    } finally { failedApi.released = true; failed.close(); failedJob.cancelAndJoin() }
}

private suspend fun assertSystemMediaHostPolicy(unavailable: Boolean, reopenRequired: Boolean = false): Unit {
    val scopeJob = SupervisorJob()
    val scope = CoroutineScope(scopeJob + Dispatchers.Default)
    val initialQueue = DesktopQueue("host-queue", listOf(DesktopQueueEntry("host-a", mediaTrackA.id),
        DesktopQueueEntry("host-duplicate", mediaTrackA.id)), "host-a", sessionId = "host-listen", sessionStartedAtMs = 1)
    val library = MutableStateFlow(mediaLibrary.copy(queue = initialQueue))
    val writes = AtomicInteger()
    val storeClosed = AtomicInteger()
    val api = MediaApiFixture()
    val stalledSave = AtomicReference<CompletableDeferred<Unit>?>(null)
    val releaseSave = CountDownLatch(1)
    // A strict in-memory port: no Room, filesystem, import, settings file or source permission.
    val store = Proxy.newProxyInstance(DesktopLibraryStore::class.java.classLoader, arrayOf(DesktopLibraryStore::class.java)) { _, method, args ->
        when (method.name) {
            "getState" -> library
            "resolveManagedFile" -> "fixture-only-capability"
            "saveQueue" -> {
                stalledSave.getAndSet(null)?.let { entered ->
                    entered.complete(Unit)
                    check(releaseSave.await(5, TimeUnit.SECONDS)) { "fixture_system_media_save_timeout" }
                }
                writes.incrementAndGet()
                library.value = library.value.copy(queue = args!![0] as DesktopQueue)
                Unit
            }
            "updateTrackDuration" -> Unit
            "close" -> { assertNull(api.listener); storeClosed.incrementAndGet(); Unit }
            else -> error("unexpected_system_media_store_call")
        }
    } as DesktopLibraryStore
    val preferences = object : DesktopPreferencesStore {
        override val state: StateFlow<DesktopPreferencesState> = MutableStateFlow(DesktopPreferencesState())
        override suspend fun setAppLanguage(value: String): Unit = error("unexpected_system_media_settings_write")
        override suspend fun setAppearanceMode(value: String): Unit = error("unexpected_system_media_settings_write")
        override suspend fun setAccentPalette(value: String): Unit = error("unexpected_system_media_settings_write")
        override suspend fun setSmoothTrackTransitions(value: Boolean): Unit = error("unexpected_system_media_settings_write")
        override suspend fun applyPortable(value: DesktopPreferences): Unit = error("unexpected_system_media_settings_write")
        override suspend fun close(): Unit = Unit
    }
    val engine = PolicyEngine()
    val controller = DesktopPlaybackController(store, engine, scope, periodicObservations = emptyFlow())
    val settings = DesktopSettingsController(preferences, scope)
    var runtime: DesktopRuntime? = null
    suspend fun fence(): Unit {
        val expected = if (controller.taste.value.errorCode == "TASTE_SESSION_STALE") "TASTE_LISTEN_STALE" else "TASTE_SESSION_STALE"
        eventually("system media actor fence") {
            if (controller.taste.value.errorCode == expected) true else {
                // The full-queue case may refuse the first marker; retry within eventually's bound.
                if (expected == "TASTE_SESSION_STALE") controller.setQueueSessionTasteExcluded("absent", false)
                else controller.setCurrentListenTasteExcluded("absent", "absent", "absent", false)
                false
            }
        }
    }
    try {
        val constructor = DesktopRuntime::class.java.declaredConstructors.single { it.parameterCount == 5 }.apply { isAccessible = true }
        val importer = object : DesktopImportPort {
            override suspend fun importSelected(paths: List<String>): DesktopImportResult = error("unexpected_system_media_import")
        }
        val host = constructor.newInstance(store, importer, controller, scope, settings) as DesktopRuntime
        runtime = host
        val factories = AtomicInteger()
        host.installSystemMedia {
            factories.incrementAndGet()
            if (unavailable) throw UnsatisfiedLinkError("fixture_missing_bridge") else api
        }
        host.installSystemMedia { factories.incrementAndGet(); error("duplicate_system_media_factory") }
        assertEquals(1, factories.get())
        eventually("host confirms paused restoration") { host.playerState.value.playback.playbackStatus == PlaybackStatus.Ready }
        assertFalse(engine.opens.single().autoplay)
        val lifecycle = DesktopWindowLifecycle(host::beginClose)
        lifecycle.installed(reopen = true, quit = true)
        val beforeHide = writes.get()
        lifecycle.closeWindow()
        assertFalse(lifecycle.state.value.visible)
        lifecycle.showWindow()
        assertTrue(lifecycle.state.value.visible)
        host.installSystemMedia { error("reopen_must_retain_system_media_owner") }
        fence()
        assertEquals(beforeHide, writes.get())
        assertEquals(1, engine.opens.size)
        assertFalse(host.playerState.value.playback.isPlaying)
        if (unavailable) {
            assertEquals(0, api.acquisitions.get())
            assertTrue(api.publications.isEmpty())
            host.transport(checkNotNull(host.playerState.value.target), DesktopTransportAction.PLAY)
        } else {
            eventually("host publishes confirmed paused restoration") { api.publications.lastOrNull() != null }
            assertFalse(api.latest().isPlaying)
            assertTrue(api.command(1))
        }
        eventually("system PLAY uses the existing controller") { host.playerState.value.playback.isPlaying }
        if (!unavailable) eventually("bridge follows confirmed playing") { api.publications.lastOrNull()?.isPlaying == true }
        val oldTarget = checkNotNull(host.playerState.value.target)
        val oldToken = api.publications.lastOrNull()?.targetToken ?: 0
        controller.selectEntry("host-duplicate")
        eventually("duplicate track has another confirmed command identity") {
            val current = host.playerState.value
            current.target?.entryId == "host-duplicate" && current.playback.playbackStatus == PlaybackStatus.Ready
        }
        fence()
        if (!unavailable) eventually("bridge replaces the duplicate-entry token") { (api.publications.lastOrNull()?.targetToken ?: 0) > oldToken }
        val beforeStale = writes.get()
        val seeks = engine.seeks.size
        for (action in DesktopTransportAction.entries) host.transport(oldTarget, action, 2_000)
        fence()
        assertEquals(beforeStale, writes.get())
        assertEquals(seeks, engine.seeks.size)
        assertFalse(api.command(2, oldToken))

        val currentTarget = checkNotNull(host.playerState.value.target)
        val currentToken = api.publications.lastOrNull()?.targetToken ?: oldToken
        val beforeFull = writes.get()
        val errorBeforeFull = controller.error.value
        val enteredSave = CompletableDeferred<Unit>()
        stalledSave.set(enteredSave)
        try {
            controller.setRepeat("ALL")
            withTimeout(2_000) { enteredSave.await() }
            // Hold the actor inside an in-memory save: no command can drain its real Channel(64).
            // Stale targets fill the queue without timer/native/checkpoint effects after release.
            val enqueued = List(66) { host.tryTransport(oldTarget, DesktopTransportAction.SEEK, 2_000) }
            assertTrue(enqueued.any { it })
            assertFalse(enqueued.last())
            assertFalse(controller.tryTransport(currentTarget, DesktopTransportAction.PAUSE))
            assertFalse(host.tryTransport(currentTarget, DesktopTransportAction.PAUSE))
            if (!unavailable) assertFalse(api.command(2, currentToken))
            host.transport(currentTarget, DesktopTransportAction.PAUSE) // Unit compatibility path stays silent.
            assertEquals(errorBeforeFull, controller.error.value)
            assertEquals(beforeFull, writes.get())
            assertEquals(seeks, engine.seeks.size)
        } finally { releaseSave.countDown() }
        fence()
        assertEquals(beforeFull + 1, writes.get()) // Only the deliberate repeat-mode save.
        assertEquals(seeks, engine.seeks.size)

        if (unavailable) {
            // receive() has captured the target before this callback starts closing the host.
            withMediaOwner(onCommand = { command ->
                host.beginClose()
                host.tryTransport(command.target, command.action, command.positionMs)
            }) { owner, fake ->
                assertTrue(owner.publish(mediaSnapshot(currentTarget)))
                assertFalse(fake.command(1))
            }
        }
        if (reopenRequired) {
            controller.reportSnapshotUnavailable()
            assertFalse(controller.tryTransport(currentTarget, DesktopTransportAction.PAUSE))
            assertFalse(host.tryTransport(currentTarget, DesktopTransportAction.PAUSE))
            assertFalse(api.command(2, currentToken))
        }
        host.beginClose()
        assertNull(api.listener)
        val closeToken = api.publications.lastOrNull()?.targetToken ?: oldToken
        assertFalse(api.command(1, closeToken))
        val beforeClosing = writes.get()
        assertFalse(host.tryTransport(currentTarget, DesktopTransportAction.SEEK, 2_000))
        host.transport(currentTarget, DesktopTransportAction.SEEK, 2_000)
        if (!reopenRequired) fence()
        assertEquals(beforeClosing, writes.get())
        withTimeout(8_000) { host.close() }
        assertFalse(controller.tryTransport(currentTarget, DesktopTransportAction.PLAY))
        assertFalse(host.tryTransport(currentTarget, DesktopTransportAction.PLAY))
        assertEquals(1, storeClosed.get())
        assertEquals(if (unavailable) 0 else 1, api.retirements.get())
        host.installSystemMedia { error("closed_host_must_not_load_bridge") }
    } finally {
        releaseSave.countDown()
        api.released = true
        if (runtime != null) runtime.close() else { controller.close(); settings.close() }
        scopeJob.cancelAndJoin()
    }
}
