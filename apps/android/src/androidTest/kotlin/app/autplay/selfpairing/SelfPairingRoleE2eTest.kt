package app.autplay.selfpairing

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.OkHttpProfilePairingPort
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.application.selfpairing.OkHttpSelfPairingTransport
import app.autplay.application.selfpairing.ProfilePairingSelfBindingCommitter
import app.autplay.application.selfpairing.SelfPairingRecipientRuntime
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import java.util.Base64
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Joined physical proof for recipient QR self-pairing; the source approval stays server-side. */
@RunWith(AndroidJUnit4::class)
class SelfPairingRoleE2eTest {
    @Test fun pairsActiveRoleThroughProductionRuntimeAndAndroidKeystore() {
        val configuredBase = InstrumentationRegistry.getArguments()
            .getString("selfPairingE2eBaseUrl")
        assumeTrue(
            "The joined self-pairing harness supplies selfPairingE2eBaseUrl",
            !configuredBase.isNullOrBlank(),
        )

        runBlocking {
            val base = requireNotNull(configuredBase).trimEnd('/')
            val handoff = oneShotHandoff(base)
            val role = handoff.getString("role")
            require(role in setOf("OWNER", "ADMIN", "USER"))
            val qr = Base64.getDecoder().decode(handoff.getString("qr_payload_b64"))
            val context = InstrumentationRegistry.getInstrumentation().targetContext
            val settings = applicationNonSecretSettingsStore(context)
            val credentials = AndroidKeystoreCredentialStore(context)
            val keys = AndroidM5DeviceKeyStore()
            val gate = FirstBindCeremonyGate()
            val profilePort = OkHttpProfilePairingPort(
                originForProfile = { base },
                credentials = credentials,
                deviceKeys = keys,
                allowUnsafeDevelopmentHttp = true,
            )
            val materialization = M5BindingMaterializationCoordinator(
                settings,
                credentials,
                keys,
                object : M5LocalIntentMaterializer {
                    override suspend fun pending(limit: Int): List<PendingLocalIntentSummary> =
                        emptyList()

                    override suspend fun materialize(
                        binding: app.autplay.application.sync.ClientEventBinding,
                        localChangeId: LocalId,
                        eventId: LocalId,
                        materializedAtMs: Long,
                    ): LocalId = error("SELF_PAIRING_E2E_UNEXPECTED_MATERIALIZATION")
                },
            )
            val pairing = ProfilePairingRuntime(
                scope = this,
                settings = settings,
                credentials = credentials,
                deviceKeys = keys,
                port = profilePort,
                materialization = materialization,
                deviceName = "A55 self-pairing $role",
                reportSafeError = {},
                allowUnsafeDevelopmentHttp = true,
                firstBindGate = gate,
            )
            val runtime = SelfPairingRecipientRuntime(
                credentials = credentials,
                keys = keys,
                transport = OkHttpSelfPairingTransport(
                    sourceCredentials = { _, _ ->
                        error("SELF_PAIRING_E2E_SOURCE_CREDENTIALS_FORBIDDEN")
                    },
                    allowDevelopmentHttp = true,
                ),
                discovery = profilePort,
                binding = ProfilePairingSelfBindingCommitter(
                    pairing,
                    profilePort,
                    settings,
                    credentials,
                    keys,
                ),
                activeProfile = ActiveProfileGate {
                    settings.settings.first().activeServerProfileId != null
                },
                firstBind = gate,
                deviceName = "A55 self-pairing $role",
                appVersion = "physical-e2e",
                allowDevelopmentHttp = true,
            )

            settings.update(NonSecretSettings())
            try {
                runtime.inspectQr(qr)
                val trust = runtime.state.value as? SelfPairingRecipientState.AwaitingTrust
                    ?: error("SELF_PAIRING_E2E_TRUST_STATE_REQUIRED")
                runtime.confirmTrust()

                var approved: SelfPairingRecipientState.AwaitingAccountConfirmation? = null
                var attempts = 0
                while (approved == null && attempts < 12) {
                    val state = runtime.state.value
                    if (state is SelfPairingRecipientState.AwaitingAccountConfirmation) {
                        approved = state
                        break
                    }
                    delay(2_100)
                    runtime.poll()
                    attempts += 1
                }
                val confirmation = requireNotNull(approved) {
                    "SELF_PAIRING_E2E_APPROVAL_TIMEOUT_${runtime.state.value::class.simpleName}"
                }
                runtime.confirmAccount(requireNotNull(confirmation.status.accountId))
                assertEquals(SelfPairingRecipientState.Connected, runtime.state.value)

                val current = settings.settings.first()
                val profile = requireNotNull(current.activeServerProfileId)
                val binding = requireNotNull(current.m5Binding)
                assertEquals(confirmation.status.accountId, current.activeUserId?.value)
                assertEquals(trust.identity.serverInstanceId, binding.serverInstanceId)
                assertTrue(
                    keys.publicKeyThumbprintSha256(binding.deviceKeyAlias)
                        .matches(Regex("[0-9a-f]{64}")),
                )
                val encrypted = requireNotNull(credentials.read(profile))
                try {
                    val envelope = SessionCredentialEnvelopeCodec.decode(encrypted)
                    assertEquals(binding.bindingCommitId, envelope.bindingCommitId)
                    assertEquals(binding.sessionId, envelope.sessionId)
                    assertEquals(binding.sessionFamilyId, envelope.sessionFamilyId)
                    assertEquals(binding.sessionGeneration, envelope.sessionGeneration)
                    assertFalse(envelope.refreshToken.isNullOrBlank())
                    assertTrue(envelope.accessToken.isNotBlank())
                } finally {
                    encrypted.fill(0)
                }
            } finally {
                qr.fill(0)
                val current = settings.settings.first()
                current.activeServerProfileId?.let { credentials.clear(it) }
                current.m5Binding?.deviceKeyAlias?.let(keys::delete)
                settings.update(NonSecretSettings())
            }
        }
    }

    private fun oneShotHandoff(base: String): JSONObject = CLIENT.newCall(
        Request.Builder()
            .url("$base/self-pairing-e2e/one-shot-handoff")
            .header("Cache-Control", "no-store")
            .build(),
    ).execute().use { response ->
        check(response.isSuccessful) { "SELF_PAIRING_E2E_HANDOFF_${response.code}" }
        JSONObject(requireNotNull(response.body).string())
    }

    private companion object {
        val CLIENT = OkHttpClient()
    }
}
