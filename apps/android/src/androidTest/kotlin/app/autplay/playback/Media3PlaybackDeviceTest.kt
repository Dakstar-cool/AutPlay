package app.autplay.playback

import android.net.Uri
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.TransferListener
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.domain.ServerProfileId
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class Media3PlaybackDeviceTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val testPackageName = instrumentation.context.packageName

    @Test fun media3PreparesAndPlaysReadableLocalAudio() {
        assertMediaReady(
            DefaultMediaSourceFactory(DefaultDataSource.Factory(context)),
            MediaItem.fromUri("content://$testPackageName.readable/audio/1"),
        )
    }

    @Test fun untrustedExternalControllerIsRejectedBySessionPolicy() {
        val callback = AutPlaySessionCallback(context.packageName)
        assertTrue(callback.isControllerAllowed(context.packageName, false))
        assertTrue(callback.isControllerAllowed("android", true))
        assertFalse(callback.isControllerAllowed("example.attacker", false))
    }

    @Test fun media3PreparesVaultAudioThroughStableReferenceAndAuthorizedOpen() {
        val payload = context.contentResolver.openInputStream(
            Uri.parse("content://$testPackageName.readable/audio/1"),
        )!!.use { it.readBytes() }
        val profile = ServerProfileId(UUID(0, 41).toString())
        val variant = UUID(0, 42).toString()
        val http = ByteArrayHttpFactory(payload)
        val vault = VaultPlaybackDataSource.Factory(http, object : VaultAuthorizationProvider {
            override fun authorize(
                profileId: ServerProfileId,
                audioVariantId: String,
                rejectedGeneration: Long?,
            ): VaultAuthorization {
                assertEquals(profile, profileId)
                assertEquals(variant, audioVariantId)
                assertNull(rejectedGeneration)
                return VaultAuthorization(Uri.parse("https://vault.test/audio/$audioVariantId"), "test-token", 1)
            }
        })
        assertMediaReady(
            DefaultMediaSourceFactory(vault),
            MediaItem.fromUri("autplay-vault://${profile.value}/audio-variants/$variant"),
        )
        assertEquals("Bearer test-token", http.lastRequestHeaders["Authorization"])
        assertTrue(http.lastOpenedUri.toString().startsWith("https://vault.test/audio/"))
        assertTrue(!http.lastOpenedUri.toString().contains("token"))
    }

    private fun assertMediaReady(factory: DefaultMediaSourceFactory, item: MediaItem) {
        val ready = CountDownLatch(1)
        val failure = AtomicReference<PlaybackException?>()
        val player = AtomicReference<ExoPlayer>()
        instrumentation.runOnMainSync {
            player.set(ExoPlayer.Builder(context).setMediaSourceFactory(factory).build().also { instance ->
                instance.addListener(object : Player.Listener {
                    override fun onPlaybackStateChanged(playbackState: Int) {
                        if (playbackState == Player.STATE_READY) ready.countDown()
                    }

                    override fun onPlayerError(error: PlaybackException) {
                        failure.set(error)
                        ready.countDown()
                    }
                })
                instance.setMediaItem(item)
                instance.playWhenReady = true
                instance.prepare()
            })
        }
        assertTrue("Media3 did not reach READY", ready.await(10, TimeUnit.SECONDS))
        instrumentation.runOnMainSync { player.get().release() }
        assertNull(failure.get()?.message, failure.get())
    }

    private class ByteArrayHttpFactory(private val payload: ByteArray) : HttpDataSource.Factory {
        var lastOpenedUri: Uri? = null
        val lastRequestHeaders = linkedMapOf<String, String>()

        override fun createDataSource(): HttpDataSource = object : HttpDataSource {
            private var position = 0
            private var openedUri: Uri? = null

            override fun open(dataSpec: DataSpec): Long {
                openedUri = dataSpec.uri
                lastOpenedUri = dataSpec.uri
                lastRequestHeaders.clear()
                lastRequestHeaders += dataSpec.httpRequestHeaders
                position = dataSpec.position.toInt()
                return (payload.size - position).toLong()
            }

            override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                if (position >= payload.size) return -1
                val count = minOf(length, payload.size - position)
                payload.copyInto(buffer, offset, position, position + count)
                position += count
                return count
            }

            override fun getUri(): Uri? = openedUri
            override fun getResponseCode(): Int = 206
            override fun getResponseHeaders(): Map<String, List<String>> = emptyMap()
            override fun close() { openedUri = null }
            override fun addTransferListener(transferListener: TransferListener) = Unit
            override fun setRequestProperty(name: String, value: String) { lastRequestHeaders[name] = value }
            override fun clearRequestProperty(name: String) { lastRequestHeaders.remove(name) }
            override fun clearAllRequestProperties() { lastRequestHeaders.clear() }
        }

        override fun setDefaultRequestProperties(defaultRequestProperties: Map<String, String>): HttpDataSource.Factory = this
    }
}
