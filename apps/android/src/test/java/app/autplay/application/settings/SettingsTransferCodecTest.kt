package app.autplay.application.settings

import app.autplay.data.settings.NonSecretSettings
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class SettingsTransferCodecTest {
    @Test
    fun roundTripChangesOnlyPortableNonSecretPreferences() {
        val current = NonSecretSettings(
            activeServerProfileId = ServerProfileId("11111111-1111-4111-8111-111111111111"),
            activeUserId = UserId("22222222-2222-4222-8222-222222222222"),
            deviceId = DeviceId("33333333-3333-4333-8333-333333333333"),
            serverBaseUrl = "https://private.example",
            streamBaseUrl = "https://stream.private.example",
            libraryRootTreeUri = "content://provider/tree/music",
        )
        val portable = NonSecretSettings(
            appLanguage = "RU",
            appearanceMode = "DARK",
            accentPalette = "BLUE",
            syncOnMeteredNetwork = true,
            wavePrefetchMode = "NEXT_3",
        )

        val encoded = SettingsTransferCodec.encode(portable)
        val restored = SettingsTransferCodec.decode(encoded, current)

        assertEquals("RU", restored.appLanguage)
        assertEquals("DARK", restored.appearanceMode)
        assertEquals("BLUE", restored.accentPalette)
        assertEquals(true, restored.syncOnMeteredNetwork)
        assertEquals("NEXT_3", restored.wavePrefetchMode)
        assertEquals(current.activeServerProfileId, restored.activeServerProfileId)
        assertEquals(current.serverBaseUrl, restored.serverBaseUrl)
        assertEquals(current.streamBaseUrl, restored.streamBaseUrl)
        assertEquals(current.libraryRootTreeUri, restored.libraryRootTreeUri)
        assertNull(encoded.decodeToString().takeIf { "private.example" in it })
        assertNull(encoded.decodeToString().takeIf { "stream.private.example" in it })
    }

    @Test
    fun rejectsOversizedOrUnknownDocuments() {
        assertThrows(IllegalArgumentException::class.java) {
            SettingsTransferCodec.decode(ByteArray(64 * 1024 + 1), NonSecretSettings())
        }
        assertThrows(IllegalArgumentException::class.java) {
            SettingsTransferCodec.decode(
                """{"schema_version":1,"appearance_mode":"FUTURE","accent_palette":"CORAL","sync_on_metered_network":false,"wave_prefetch_mode":"NEXT"}""".encodeToByteArray(),
                NonSecretSettings(),
            )
        }
    }

    @Test
    fun preservesUnknownFutureLanguageValues() {
        val restored = SettingsTransferCodec.decode(
            """{"schema_version":1,"app_language":"KLINGON","appearance_mode":"SYSTEM","accent_palette":"CORAL","sync_on_metered_network":false,"wave_prefetch_mode":"NEXT"}""".encodeToByteArray(),
            NonSecretSettings(),
        )

        assertEquals("KLINGON", restored.appLanguage)
    }
}
