package app.autplay.playback

import android.net.Uri
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.HttpDataSource
import androidx.media3.datasource.TransferListener
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.autplay.domain.ServerProfileId
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith

@UnstableApi
@RunWith(AndroidJUnit4::class)
class VaultPlaybackDataSourceTest {
    @Test fun rejectedGenerationRefreshesOnceAndNeverPlacesTokenInUri() {
        val provider = RecordingAuthorizationProvider()
        val factory = SequencedHttpFactory()
        val source = VaultPlaybackDataSource(factory, provider)
        val stableUri = Uri.parse("autplay-vault://${provider.profile.value}/audio-variants/${provider.variantId}")

        assertEquals(4L, source.open(DataSpec(stableUri)))
        val bytes = ByteArray(4)
        assertEquals(4, source.read(bytes, 0, bytes.size))

        assertEquals(listOf<Long?>(null, 1), provider.rejectedGenerations)
        assertEquals(2, factory.sources.size)
        assertEquals("Bearer fresh-secret", factory.sources.last().requestHeaders["Authorization"])
        assertEquals("https://vault.test/api/v1/stream/audio-variants/${provider.variantId}", source.uri.toString())
        assertFalse(source.uri.toString().contains("secret"))
        source.close()
    }

    private class RecordingAuthorizationProvider : VaultAuthorizationProvider {
        val profile = ServerProfileId(UUID(0, 8).toString())
        val variantId = UUID(0, 9).toString()
        val rejectedGenerations = mutableListOf<Long?>()

        override fun authorize(
            profileId: ServerProfileId,
            audioVariantId: String,
            rejectedGeneration: Long?,
        ): VaultAuthorization {
            assertEquals(profile, profileId)
            assertEquals(variantId, audioVariantId)
            rejectedGenerations += rejectedGeneration
            return VaultAuthorization(
                Uri.parse("https://vault.test/api/v1/stream/audio-variants/$audioVariantId"),
                if (rejectedGeneration == null) "stale-secret" else "fresh-secret",
                if (rejectedGeneration == null) 1 else 2,
            )
        }
    }

    private class SequencedHttpFactory : HttpDataSource.Factory {
        val sources = mutableListOf<FakeHttpDataSource>()
        override fun createDataSource(): HttpDataSource = FakeHttpDataSource(sources.isEmpty()).also(sources::add)
        override fun setDefaultRequestProperties(defaultRequestProperties: Map<String, String>): HttpDataSource.Factory = this
    }

    private class FakeHttpDataSource(private val reject: Boolean) : HttpDataSource {
        val requestHeaders = linkedMapOf<String, String>()
        private var openedUri: Uri? = null
        private var offset = 0

        override fun open(dataSpec: DataSpec): Long {
            openedUri = dataSpec.uri
            requestHeaders += dataSpec.httpRequestHeaders
            if (reject) throw HttpDataSource.InvalidResponseCodeException(
                401,
                "Unauthorized",
                null,
                emptyMap(),
                dataSpec,
                ByteArray(0),
            )
            return PAYLOAD.size.toLong()
        }

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            if (this.offset == PAYLOAD.size) return -1
            val count = minOf(length, PAYLOAD.size - this.offset)
            PAYLOAD.copyInto(buffer, offset, this.offset, this.offset + count)
            this.offset += count
            return count
        }

        override fun getUri(): Uri? = openedUri
        override fun getResponseCode(): Int = if (reject) 401 else 206
        override fun getResponseHeaders(): Map<String, List<String>> = emptyMap()
        override fun close() { openedUri = null }
        override fun addTransferListener(transferListener: TransferListener) = Unit
        override fun setRequestProperty(name: String, value: String) { requestHeaders[name] = value }
        override fun clearRequestProperty(name: String) { requestHeaders.remove(name) }
        override fun clearAllRequestProperties() { requestHeaders.clear() }

        private companion object { val PAYLOAD = byteArrayOf(1, 2, 3, 4) }
    }
}
