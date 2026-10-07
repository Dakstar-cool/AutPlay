package app.autplay.download

import androidx.media3.common.util.UnstableApi
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

@UnstableApi
class DownloadNetworkPolicyTest {
    @Test
    fun offlineAudioRequiresWifiUnlessMeteredDownloadsAreEnabled() {
        val restricted = downloadNetworkRequirements(allowMeteredNetwork = false)
        assertTrue(restricted.isNetworkRequired)
        assertTrue(restricted.isUnmeteredNetworkRequired)
        val allowed = downloadNetworkRequirements(allowMeteredNetwork = true)
        assertTrue(allowed.isNetworkRequired)
        assertFalse(allowed.isUnmeteredNetworkRequired)
    }
}
