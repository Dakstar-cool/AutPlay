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
import app.autplay.application.selfpairing.SelfPairingPendingStore
import app.autplay.application.selfpairing.SelfPairingRecipientRuntime
import app.autplay.application.selfpairing.SelfPairingRecipientState
import app.autplay.application.selfpairing.SelfPairingRole
import app.autplay.application.selfpairing.text
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
import java.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Physical proof that a sixth binding fails at the persisted five-device account limit. */
@RunWith(AndroidJUnit4::class)
class SelfPairingDeviceQuotaE2eTest {
    @Test fun sixthDeviceIsRejectedAndEncryptedRetryRemainsPending() {
        val configuredBase = InstrumentationRegistry.getArguments()
            .getString("selfPairingQuotaE2eBaseUrl")
        assumeTrue(
            "The joined quota harness supplies selfPairingQuotaE2eBaseUrl",
            !configuredBase.isNullOrBlank(),
        )

        runBlocking {
            val base = requireNotNull(configuredBase).trimEnd('/')
            val handoff = oneShotHandoff(base)
            assertEquals("OWNER", handoff.getString("role"))
            val qr = Base64.getDecoder().decode(handoff.getString("qr_payload_b64"))
            val harness = runtimeHarness(base, this)

            harness.settings.update(NonSecretSettings())
            try {
                harness.runtime.inspectQr(qr)
                assertTrue(harness.runtime.state.value is SelfPairingRecipientState.AwaitingTrust)
                harness.runtime.confirmTrust()

                var approved: SelfPairingRecipientState.AwaitingAccountConfirmation? = null
                var attempts = 0
                while (approved == null && attempts < 12) {
                    val state = harness.runtime.state.value
                    if (state is SelfPairingRecipientState.AwaitingAccountConfirmation) {
                        approved = state
                        break
                    }
                    delay(2_100)
                    harness.runtime.poll()
                    attempts += 1
                }
                val confirmation = requireNotNull(approved) {
                    "SELF_PAIRING_QUOTA_E2E_APPROVAL_TIMEOUT_" +
                        harness.runtime.state.value::class.simpleName
                }
                harness.runtime.confirmAccount(requireNotNull(confirmation.status.accountId))
                assertQuotaBlocked(harness.runtime.state.value)

                assertNull(harness.settings.settings.first().activeServerProfileId)
                assertNull(harness.settings.settings.first().m5Binding)
                assertTrue(harness.pendingStore.read(SelfPairingRole.RECIPIENT) != null)
            } finally {
                qr.fill(0)
            }
        }
    }

    @Test fun pendingQuotaRefusalSurvivesProcessRestartAndExactRetry() {
        val configuredBase = InstrumentationRegistry.getArguments()
            .getString("selfPairingQuotaE2eBaseUrl")
        assumeTrue(
            "The joined quota harness supplies selfPairingQuotaE2eBaseUrl",
            !configuredBase.isNullOrBlank(),
        )

        runBlocking {
            val harness = runtimeHarness(requireNotNull(configuredBase).trimEnd('/'), this)
            val pending = requireNotNull(harness.pendingStore.read(SelfPairingRole.RECIPIENT)) {
                "SELF_PAIRING_QUOTA_E2E_PENDING_REQUIRED_AFTER_RESTART"
            }
            val pendingAlias = pending.text("key_alias")
            try {
                harness.runtime.resume()
                assertQuotaBlocked(harness.runtime.state.value)
                assertNull(harness.settings.settings.first().activeServerProfileId)
                assertNull(harness.settings.settings.first().m5Binding)
                assertTrue(harness.pendingStore.read(SelfPairingRole.RECIPIENT) != null)
            } finally {
                harness.pendingStore.clear(SelfPairingRole.RECIPIENT)
                harness.keys.delete(pendingAlias)
                val current = harness.settings.settings.first()
                current.activeServerProfileId?.let { harness.credentials.clear(it) }
                current.m5Binding?.deviceKeyAlias?.let(harness.keys::delete)
                harness.settings.update(NonSecretSettings())
            }
        }
    }

    private fun runtimeHarness(base: String, scope: CoroutineScope): RuntimeHarness {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val settings = applicationNonSecretSettingsStore(context)
        val credentials = AndroidKeystoreCredentialStore(context)
        val keys = AndroidM5DeviceKeyStore()
        val pendingStore = SelfPairingPendingStore(credentials)
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
                ): LocalId = error("SELF_PAIRING_QUOTA_E2E_UNEXPECTED_MATERIALIZATION")
            },
        )
        val pairing = ProfilePairingRuntime(
            scope = scope,
            settings = settings,
            credentials = credentials,
            deviceKeys = keys,
            port = profilePort,
            materialization = materialization,
            deviceName = "A55 quota candidate",
            reportSafeError = {},
            allowUnsafeDevelopmentHttp = true,
            firstBindGate = gate,
        )
        return RuntimeHarness(
            runtime = SelfPairingRecipientRuntime(
                credentials = credentials,
                keys = keys,
                transport = OkHttpSelfPairingTransport(
                    sourceCredentials = { _, _ ->
                        error("SELF_PAIRING_QUOTA_E2E_SOURCE_CREDENTIALS_FORBIDDEN")
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
                deviceName = "A55 quota candidate",
                appVersion = "physical-e2e",
                allowDevelopmentHttp = true,
            ),
            settings = settings,
            credentials = credentials,
            keys = keys,
            pendingStore = pendingStore,
        )
    }

    private fun assertQuotaBlocked(state: SelfPairingRecipientState) {
        val blocked = state as? SelfPairingRecipientState.Blocked
            ?: error("SELF_PAIRING_QUOTA_E2E_BLOCK_REQUIRED")
        assertEquals("account_device_limit_reached", blocked.code)
        assertTrue(blocked.pending)
    }

    private fun oneShotHandoff(base: String): JSONObject = CLIENT.newCall(
        Request.Builder()
            .url("$base/self-pairing-e2e/one-shot-handoff")
            .header("Cache-Control", "no-store")
            .build(),
    ).execute().use { response ->
        check(response.isSuccessful) { "SELF_PAIRING_QUOTA_E2E_HANDOFF_${response.code}" }
        JSONObject(requireNotNull(response.body).string())
    }

    private companion object {
        val CLIENT = OkHttpClient()
    }

    private data class RuntimeHarness(
        val runtime: SelfPairingRecipientRuntime,
        val settings: NonSecretSettingsStore,
        val credentials: AndroidKeystoreCredentialStore,
        val keys: AndroidM5DeviceKeyStore,
        val pendingStore: SelfPairingPendingStore,
    )
}
