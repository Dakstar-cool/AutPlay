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
import kotlinx.coroutines.runBlocking
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

    @Before fun setUp() {
        context.deleteDatabase(name)
        database = AutPlayDatabase.open(context, name)
    }

    @After fun tearDown() = runBlocking {
        database.close()
        context.deleteDatabase(name)
        applicationNonSecretSettingsStore(context).update(NonSecretSettings())
    }

    @Test fun readableLocalWinsBeforeConfiguredVault() = runBlocking {
        val track = track(1)
        database.libraryDao().upsertTrackRef(track)
        database.localAudioDao().upsertState(audio(track, "content://$testPackageName.readable/audio/1"))
        val resolver = AndroidPlaybackSourceResolver(context, database, applicationNonSecretSettingsStore(context))
        val result = resolver.resolve(LocalId(track.localUserTrackRefId), 10) as AndroidSourceResolution.Available
        assertEquals(SelectedAudioSource.LOCAL_URI, result.value.source)
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
