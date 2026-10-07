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
            onboardingRevision = 1,
            pendingPublicId = "local_listener",
        )
        val portable = NonSecretSettings(
            appLanguage = "RU",
            appearanceMode = "DARK",
            accentPalette = "BLUE",
            downloadOnMeteredNetwork = true,
            wavePrefetchMode = "NEXT_3",
            developerMode = true,
            smoothTrackTransitions = true,
            pendingPublicId = "portable_listener",
        )

        val encoded = SettingsTransferCodec.encode(portable)
        assertEquals(true, "download_on_metered_network" in encoded.decodeToString())
        assertEquals(false, "sync_on_metered_network" in encoded.decodeToString())
        val restored = SettingsTransferCodec.decode(encoded, current)

        assertEquals("RU", restored.appLanguage)
        assertEquals("DARK", restored.appearanceMode)
        assertEquals("BLUE", restored.accentPalette)
        assertEquals(true, restored.downloadOnMeteredNetwork)
        assertEquals("NEXT_3", restored.wavePrefetchMode)
        assertEquals(true, restored.smoothTrackTransitions)
        assertEquals(false, restored.developerMode)
        assertNull(encoded.decodeToString().takeIf { "developer" in it })
        assertEquals(current.activeServerProfileId, restored.activeServerProfileId)
        assertEquals(current.serverBaseUrl, restored.serverBaseUrl)
        assertEquals(current.streamBaseUrl, restored.streamBaseUrl)
        assertEquals(current.libraryRootTreeUri, restored.libraryRootTreeUri)
        assertEquals(1, restored.onboardingRevision)
        assertEquals("local_listener", restored.pendingPublicId)
        assertNull(encoded.decodeToString().takeIf { "public_id" in it || "portable_listener" in it })
        assertNull(encoded.decodeToString().takeIf { "private.example" in it })
        assertNull(encoded.decodeToString().takeIf { "stream.private.example" in it })
        assertNull(encoded.decodeToString().takeIf { "onboarding" in it })
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
        assertEquals(false, restored.downloadOnMeteredNetwork)
    }

    @Test fun legacySettingsImportKeepsTheExistingPlaybackPreference() {
        for (version in listOf(1, 2)) {
            val networkKey = if (version == 1) "sync_on_metered_network" else "download_on_metered_network"
            val restored = SettingsTransferCodec.decode(
                """{"schema_version":$version,"appearance_mode":"SYSTEM","accent_palette":"CORAL","$networkKey":false,"wave_prefetch_mode":"NEXT"}""".encodeToByteArray(),
                NonSecretSettings(smoothTrackTransitions = true),
            )
            assertEquals(true, restored.smoothTrackTransitions)
        }
    }

    @Test fun settingsImportCannotReplaceTheDeviceLocalPublicId() {
        for (version in 1..3) {
            val networkKey = if (version == 1) "sync_on_metered_network" else "download_on_metered_network"
            val restored = SettingsTransferCodec.decode(
                """{"schema_version":$version,"appearance_mode":"SYSTEM","accent_palette":"CORAL","$networkKey":false,"wave_prefetch_mode":"NEXT","pending_public_id":"imported_name"}""".encodeToByteArray(),
                NonSecretSettings(pendingPublicId = "local_listener"),
            )
            assertEquals("local_listener", restored.pendingPublicId)
        }
    }
}
