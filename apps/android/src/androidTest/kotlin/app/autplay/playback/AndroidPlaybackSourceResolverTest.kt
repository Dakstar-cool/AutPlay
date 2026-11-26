package app.autplay.playback

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.UUID
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class AndroidPlaybackSourceResolverTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val testPackageName = InstrumentationRegistry.getInstrumentation().context.packageName
    private val name = "autplay-p08-source.db"
    private lateinit var database: AutPlayDatabase
    private lateinit var originalSettings: NonSecretSettings

    @Before fun setUp() {
        originalSettings = runBlocking { applicationNonSecretSettingsStore(context).settings.first() }
        context.deleteDatabase(name)
        database = AutPlayDatabase.open(context, name)
    }

    @After fun tearDown() = runBlocking {
        database.close()
        context.deleteDatabase(name)
        applicationNonSecretSettingsStore(context).update(originalSettings)
    }

    @Test fun readableLocalWinsBeforeConfiguredVault() = runBlocking {
        val track = track(1)
        database.libraryDao().upsertTrackRef(track)
        database.localAudioDao().upsertState(audio(track, "content://$testPackageName.readable/audio/1"))
        val resolver = AndroidPlaybackSourceResolver(context, database, applicationNonSecretSettingsStore(context))
        val result = resolver.resolve(LocalId(track.localUserTrackRefId), 10) as AndroidSourceResolution.Available
        assertEquals(SelectedAudioSource.LOCAL_URI, result.value.source)
    }

    @Test fun metadataRowPlaysTheImportedFileOnlyForTheSameProfileAndRecording() = runBlocking {
        val imported = track(31)
        val libraryRow = track(32).copy(serverRecordingId = imported.serverRecordingId)
        val dao = database.libraryDao()
        dao.upsertTrackRef(imported)
        dao.upsertTrackRef(libraryRow)
        database.localAudioDao().upsertState(audio(imported, "content://$testPackageName.readable/audio/1"))
        val resolver = AndroidPlaybackSourceResolver(context, database, applicationNonSecretSettingsStore(context))
        val id = LocalId(libraryRow.localUserTrackRefId)
        val local = resolver.resolve(id, 10) as AndroidSourceResolution.Available
        assertEquals(id, local.value.trackRefId)
        assertEquals(SelectedAudioSource.LOCAL_URI, local.value.source)
        dao.upsertTrackRef(libraryRow.copy(serverProfileId = uuid(7)))
        assertEquals(emptyList<LocalAudioStateEntity>(), database.localAudioDao().otherAudioForRecording(id.value, 32))
    }

    @androidx.test.filters.SdkSuppress(minSdkVersion = 29)
    @Test fun metadataOnlyLibraryRowFindsAndDecodesThePhoneFile() = runBlocking {
        val settings = applicationNonSecretSettingsStore(context)
        settings.update(NonSecretSettings())
        val files = context.contentResolver
        val media = android.provider.MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
        val title = UUID.randomUUID().toString().take("Metadata fixture".length)
        val values = android.content.ContentValues().apply {
            put("_display_name", "autplay-$title.mp3")
            put("mime_type", "audio/mpeg")
            put("relative_path", "Music/")
            put("is_pending", 1)
        }
        val source = requireNotNull(files.insert(media, values))
        try {
            val bytes = InstrumentationRegistry.getInstrumentation().context.assets.open("metadata/metadata-fixture.mp3").use { it.readBytes() }
            val titleOffset = bytes.toString(Charsets.ISO_8859_1).indexOf("Metadata fixture")
            require(titleOffset >= 0)
            title.toByteArray().copyInto(bytes, titleOffset)
            files.openOutputStream(source)!!.use { it.write(bytes) }
            files.update(source, android.content.ContentValues().apply { put("is_pending", 0) }, null, null)
            val track = track(45).copy(rawTitle = title, rawArtist = "AutPlay verification", rawDurationMs = 3000)
            database.libraryDao().upsertTrackRef(track)
            val resolver = AndroidPlaybackSourceResolver(context, database, settings)
            val result = resolver.resolve(LocalId(track.localUserTrackRefId), 10) as AndroidSourceResolution.Available
            assertEquals(SelectedAudioSource.LOCAL_URI, result.value.source)
            val player = android.media.MediaPlayer()
            try {
                player.setDataSource(context, result.value.runtimeUri)
                player.prepare()
                player.start()
                withTimeout(5000) { while (player.currentPosition < 150) delay(40) }
            } finally { player.release() }
        } finally { files.delete(source, null, null) }
        Unit
    }

    @Test fun slowProviderKeepsMainResponsiveAndReceivesCancellationWithoutPersistingMissing() = runBlocking {
        val uri = android.net.Uri.parse("content://$testPackageName.slow/audio/1")
        context.contentResolver.call(uri, "reset", null, null)
        val track = track(14)
        val local = audio(track, uri.toString())
        database.libraryDao().upsertTrackRef(track)
        database.localAudioDao().upsertState(local)
        val resolver = AndroidPlaybackSourceResolver(context, database, applicationNonSecretSettingsStore(context))
        val probe = launch(Dispatchers.Main) { resolver.resolve(LocalId(track.localUserTrackRefId), 77) }
        try {
            withTimeout(5_000) {
                while (context.contentResolver.call(uri, "status", null, null)?.getInt("started") != 1) delay(20)
            }
            withTimeout(1_000) { repeat(5) { withContext(Dispatchers.Main) { android.os.Looper.myLooper() }; delay(10) } }
            withTimeout(1_000) { probe.cancelAndJoin() }
            withTimeout(1_000) {
                while (context.contentResolver.call(uri, "status", null, null)?.getInt("cancelled") != 1) delay(20)
            }
            assertEquals("AVAILABLE", database.localAudioDao().state(local.localAudioStateId)?.status)
            assertEquals(null, database.localAudioDao().state(local.localAudioStateId)?.lastVerifiedAtMs)
        } finally { probe.cancelAndJoin() }
    }

    @Test fun slowProviderTimeoutIsTransientAndCancelsBinderProbe() = runBlocking {
        val uri = android.net.Uri.parse("content://$testPackageName.slow/audio/timeout")
        context.contentResolver.call(uri, "reset", null, null)
        val result = withTimeout(4_000) {
            app.autplay.application.importing.ContentUriInspector(context.contentResolver).inspectForPlayback(uri.toString())
        }
        assertEquals(null, result)
        withTimeout(1_000) {
            while (context.contentResolver.call(uri, "status", null, null)?.getInt("cancelled") != 1) delay(20)
        }
    }

    @Test fun revokedUriFallsBackToStableVaultReferenceWithoutDeletingTrack() = runBlocking {
        val profile = ServerProfileId(uuid(5))
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = profile,
                activeUserId = UserId(uuid(6)),
                deviceId = DeviceId(uuid(7)),
                serverBaseUrl = "https://api.test",
                streamBaseUrl = "https://vault.test",
            ),
        )
        val track = track(2)
        database.libraryDao().upsertTrackRef(track)
        val state = audio(track, "content://$testPackageName.revoked/audio/1")
        database.localAudioDao().upsertState(state)
        val resolver = AndroidPlaybackSourceResolver(context, database, applicationNonSecretSettingsStore(context))
        val result = resolver.resolve(LocalId(track.localUserTrackRefId), 20) as AndroidSourceResolution.Available

        assertEquals(SelectedAudioSource.VAULT_STREAM, result.value.source)
        assertEquals(AndroidPlaybackSourceResolver.VAULT_SCHEME, result.value.runtimeUri.scheme)
        assertEquals("PERMISSION_REVOKED", database.localAudioDao().state(state.localAudioStateId)?.status)
        assertNotNull(database.libraryDao().trackRef(track.localUserTrackRefId))
    }

    @Test fun serverOnlyTrackResolvesCanonicalVariantAtPlaybackTime() = runBlocking {
        val profile = ServerProfileId(uuid(8))
        applicationNonSecretSettingsStore(context).update(
            NonSecretSettings(
                activeServerProfileId = profile,
                activeUserId = UserId(uuid(9)),
                deviceId = DeviceId(uuid(10)),
                serverBaseUrl = "https://api.test",
                streamBaseUrl = "https://vault.test",
            ),
        )
        val serverTrackRefId = uuid(11)
        val variantId = uuid(12)
        val track = track(3).copy(
            serverUserTrackRefId = serverTrackRefId,
            serverProfileId = uuid(99),
        )
        database.libraryDao().upsertTrackRef(track)
        var lookupAttempts = 0
        val resolver = AndroidPlaybackSourceResolver(
            context,
            database,
            applicationNonSecretSettingsStore(context),
        ) { requestedProfile, requestedTrackRefId ->
            assertEquals(profile, requestedProfile)
            assertEquals(serverTrackRefId, requestedTrackRefId)
            if (++lookupAttempts == 1) throw java.net.SocketTimeoutException()
            variantId
        }

        val result = resolver.resolve(
            LocalId(track.localUserTrackRefId),
            30,
        ) as AndroidSourceResolution.Available

        assertEquals(SelectedAudioSource.VAULT_STREAM, result.value.source)
        assertEquals(profile.value, result.value.runtimeUri.authority)
        assertEquals(listOf("audio-variants", variantId), result.value.runtimeUri.pathSegments)
        assertEquals(2, lookupAttempts)
        suspend fun failure(error: Exception, attempts: Int, reason: PlaybackUnavailableReason) {
            var calls = 0
            val failing = AndroidPlaybackSourceResolver(context, database, applicationNonSecretSettingsStore(context)) { _, _ ->
                calls++
                throw error
            }
            val resolution = failing.resolve(LocalId(track.localUserTrackRefId), 31)
            assertEquals(AndroidSourceResolution.Unavailable(reason), resolution)
            assertEquals(attempts, calls)
        }
        failure(java.net.ConnectException(), 3, PlaybackUnavailableReason.VAULT_NETWORK_UNAVAILABLE)
        failure(app.autplay.data.security.SessionRequiredException(), 1, PlaybackUnavailableReason.VAULT_AUTHORIZATION_UNAVAILABLE)
    }

    private fun track(seed: Int) = UserTrackRefEntity(
        localUserTrackRefId = uuid(seed), serverUserTrackRefId = null, localRecordingId = null,
        serverRecordingId = uuid(seed + 20), resolutionStatus = "RESOLVED", rawTitle = "P08",
        rawArtist = "AutPlay", rawAlbum = null, rawDurationMs = 1_000, resolutionConfidence = 1.0,
        syncState = "CLEAN", serverRowVersion = 1, lastLocalSequence = 0, createdAtMs = 1,
        updatedAtMs = 1, deletedAtMs = null,
    )

    private fun audio(track: UserTrackRefEntity, uri: String) = LocalAudioStateEntity(
        localAudioStateId = uuid(100 + uri.hashCode()), localUserTrackRefId = track.localUserTrackRefId,
        localRecordingId = null, serverAudioVariantId = uuid(99), contentUri = uri,
        persistedUriPermission = false, localSha256 = null, fingerprintAlgorithm = null,
        fingerprintVersion = null, fingerprintPayload = null, codec = "pcm_s16le", container = "wav",
        bitrateBps = 128_000, sampleRateHz = 8_000, channels = 1, durationMs = 1_000,
        status = "AVAILABLE", storageClass = "USER_IMPORT", byteSize = null,
        lastAccessedAtMs = null, lastVerifiedAtMs = null, createdAtMs = 1, updatedAtMs = 1,
    )

    private fun uuid(seed: Int): String = UUID(0, seed.toLong()).toString()
}
