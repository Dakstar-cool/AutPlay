package app.autplay.trainingconsent

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.trainingconsent.OkHttpTrainingConsentPort
import app.autplay.application.trainingconsent.TrainingConsentJournalStore
import app.autplay.application.trainingconsent.TrainingConsentRuntime
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.CredentialJournalSlots
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.SettingsM5RotationContextResolver
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Physical grant/replay/withdrawal proof through the production consent runtime and transport. */
@RunWith(AndroidJUnit4::class)
class TrainingConsentE2eTest {
    @Test fun grantLostReplyReplayThenWithdraw() {
        val configuredBase = InstrumentationRegistry.getArguments()
            .getString("trainingConsentE2eBaseUrl")
        assumeTrue(
            "The joined consent harness supplies trainingConsentE2eBaseUrl",
            !configuredBase.isNullOrBlank(),
        )

        runBlocking {
            val base = requireNotNull(configuredBase).trimEnd('/')
            val handoff = oneShotHandoff(base)
            val accountId = handoff.getString("account_id")
            val deviceId = handoff.getString("device_id")
            val sessionId = handoff.getString("session_id")
            val familyId = handoff.getString("session_family_id")
            val serverId = handoff.getString("server_instance_id")
            val identityEpoch = handoff.getLong("identity_epoch")
            val identityThumbprint = handoff.getString("identity_thumbprint_sha256")
            val bindingCommitId = handoff.getString("binding_commit_id")
            val access = handoff.getString("access_token")
            val refresh = handoff.getString("refresh_token")
            val context = InstrumentationRegistry.getInstrumentation().targetContext
            val settings = applicationNonSecretSettingsStore(context)
            val credentials = AndroidKeystoreCredentialStore(context)
            val keys = AndroidM5DeviceKeyStore()
            val profile = ServerProfileId(serverId)
            val journalProfile = CredentialJournalSlots.trainingConsentProfile(
                profile,
                accountId,
                base,
            )
            val keyAlias = "training-consent-e2e"
            val checkpoint = M5BindingCheckpoint(
                bindingCommitId = bindingCommitId,
                serverInstanceId = serverId,
                identityEpoch = identityEpoch,
                identityThumbprintSha256 = identityThumbprint,
                deviceKeyAlias = keyAlias,
                sessionId = sessionId,
                sessionFamilyId = familyId,
                sessionGeneration = 0,
            )
            val bindingKey = "$serverId:$accountId:$deviceId:$bindingCommitId:$base"
            val encoded = SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = access,
                    refreshToken = refresh,
                    generation = 0,
                    bindingCommitId = bindingCommitId,
                    sessionId = sessionId,
                    sessionFamilyId = familyId,
                    sessionGeneration = 0,
                ),
            )
            settings.update(NonSecretSettings())
            try {
                keys.ensure(keyAlias)
                credentials.write(profile, encoded)
                settings.update(
                    NonSecretSettings(
                        activeServerProfileId = profile,
                        activeUserId = UserId(accountId),
                        deviceId = DeviceId(deviceId),
                        serverBaseUrl = base,
                        streamBaseUrl = base,
                        m5Binding = checkpoint,
                    ),
                )

                fun runtime() = TrainingConsentRuntime(
                    accountId = accountId,
                    bindingKey = bindingKey,
                    pendingStore = TrainingConsentJournalStore(credentials, journalProfile),
                    port = OkHttpTrainingConsentPort(
                        "$base/api/v1",
                        profile,
                        credentials,
                        m5Rotation = M5SessionRotationClient(
                            SettingsM5RotationContextResolver(settings),
                            keys,
                        ),
                    ),
                ) {
                    val current = settings.settings.first()
                    if (
                        current.activeServerProfileId != profile ||
                        current.activeUserId?.value != accountId ||
                        current.deviceId?.value != deviceId ||
                        current.m5Binding?.bindingCommitId != bindingCommitId ||
                        current.serverBaseUrl != base
                    ) {
                        false
                    } else {
                        val material = credentials.read(profile)
                        if (material == null) {
                            false
                        } else {
                            try {
                                SessionCredentialEnvelopeCodec.decode(material).bindingCommitId ==
                                    bindingCommitId
                            } finally {
                                material.fill(0)
                            }
                        }
                    }
                }

                val firstProcess = runtime()
                firstProcess.load()
                assertEquals("UNKNOWN", firstProcess.state.value.confirmed?.decision)
                assertEquals(0L, firstProcess.state.value.confirmed?.revision)

                firstProcess.choose("GRANTED")
                assertTrue(firstProcess.state.value.pending)
                assertTrue(firstProcess.state.value.error)
                assertNotNull(credentials.read(journalProfile)?.also { it.fill(0) })

                val restoredProcess = runtime()
                restoredProcess.load()
                assertEquals("GRANTED", restoredProcess.state.value.confirmed?.decision)
                assertEquals(1L, restoredProcess.state.value.confirmed?.revision)
                assertFalse(restoredProcess.state.value.pending)
                assertFalse(restoredProcess.state.value.error)
                assertEquals(null, credentials.read(journalProfile))

                restoredProcess.choose("WITHDRAWN")
                assertEquals("WITHDRAWN", restoredProcess.state.value.confirmed?.decision)
                assertEquals(2L, restoredProcess.state.value.confirmed?.revision)
                assertFalse(restoredProcess.state.value.pending)
                assertFalse(restoredProcess.state.value.error)
                assertEquals(null, credentials.read(journalProfile))

                val active = requireNotNull(credentials.read(profile))
                try {
                    val envelope = SessionCredentialEnvelopeCodec.decode(active)
                    assertEquals(bindingCommitId, envelope.bindingCommitId)
                    assertEquals(sessionId, envelope.sessionId)
                } finally {
                    active.fill(0)
                }
            } finally {
                encoded.fill(0)
                credentials.clear(journalProfile)
                credentials.clear(profile)
                keys.delete(keyAlias)
                settings.update(NonSecretSettings())
            }
        }
    }

    private fun oneShotHandoff(base: String): JSONObject = CLIENT.newCall(
        Request.Builder()
            .url("$base/training-consent-e2e/one-shot-handoff")
            .header("Cache-Control", "no-store")
            .build(),
    ).execute().use { response ->
        check(response.isSuccessful) { "TRAINING_CONSENT_E2E_HANDOFF_${response.code}" }
        JSONObject(requireNotNull(response.body).string())
    }

    private companion object {
        val CLIENT = OkHttpClient()
    }
}
