package app.autplay.accountrecovery

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.accountrecovery.AccountRecoveryRecipientRuntime
import app.autplay.application.accountrecovery.AccountRecoveryRecipientState
import app.autplay.application.accountrecovery.AccountRecoverySlot
import app.autplay.application.accountrecovery.OkHttpAccountRecoveryTransport
import app.autplay.application.accountrecovery.ProfilePairingRecoveryBindingCommitter
import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.OkHttpProfilePairingPort
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.LocalId
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

/** Physical recovery proof with a deliberately lost commit reply and exact outcome recovery. */
@RunWith(AndroidJUnit4::class)
class AccountRecoveryE2eTest {
    @Test fun manualRecoveryRotatesCodeAndCommitsThroughLostReply() {
        val configuredBase = InstrumentationRegistry.getArguments()
            .getString("accountRecoveryE2eBaseUrl")
        assumeTrue(
            "The joined recovery harness supplies accountRecoveryE2eBaseUrl",
            !configuredBase.isNullOrBlank(),
        )

        runBlocking {
            val base = requireNotNull(configuredBase).trimEnd('/')
            val handoff = oneShotHandoff(base)
            val accountId = handoff.getString("account_id")
            val recoveryCode = handoff.getString("recovery_code")
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
                    ): LocalId = error("RECOVERY_E2E_UNEXPECTED_MATERIALIZATION")
                },
            )
            val pairing = ProfilePairingRuntime(
                scope = this,
                settings = settings,
                credentials = credentials,
                deviceKeys = keys,
                port = profilePort,
                materialization = materialization,
                deviceName = "A55 recovered phone",
                reportSafeError = {},
                allowUnsafeDevelopmentHttp = true,
                firstBindGate = gate,
            )
            val runtime = AccountRecoveryRecipientRuntime(
                credentials = credentials,
                keys = keys,
                transport = OkHttpAccountRecoveryTransport(
                    credentials = { _, _ -> error("RECOVERY_E2E_SOURCE_CREDENTIALS_FORBIDDEN") },
                    allowDevelopmentHttp = true,
                ),
                discovery = profilePort,
                binding = ProfilePairingRecoveryBindingCommitter(
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
                deviceName = "A55 recovered phone",
                appVersion = "physical-e2e",
                allowDevelopmentHttp = true,
            )

            settings.update(NonSecretSettings())
            try {
                runtime.inspectManual(base, accountId, recoveryCode)
                val server = runtime.state.value as? AccountRecoveryRecipientState.ConfirmServer
                    ?: error("RECOVERY_E2E_SERVER_CONFIRMATION_REQUIRED")
                assertEquals(accountId, server.accountId)

                runtime.confirmServer()
                val account = runtime.state.value as? AccountRecoveryRecipientState.ConfirmAccount
                    ?: error("RECOVERY_E2E_ACCOUNT_CONFIRMATION_REQUIRED")
                assertEquals(accountId, account.preview.accountId)
                assertEquals("OWNER", account.preview.role)
                assertEquals(1L, account.preview.generation)

                runtime.confirmAccount(accountId)
                assertEquals(AccountRecoveryRecipientState.Connected, runtime.state.value)

                val current = settings.settings.first()
                val profile = requireNotNull(current.activeServerProfileId)
                val binding = requireNotNull(current.m5Binding)
                assertEquals(accountId, current.activeUserId?.value)
                assertEquals(server.identity.serverInstanceId, binding.serverInstanceId)
                assertTrue(
                    keys.publicKeyThumbprintSha256(binding.deviceKeyAlias)
                        .matches(Regex("[0-9a-f]{64}")),
                )

                val encryptedBinding = requireNotNull(credentials.read(profile))
                try {
                    val envelope = SessionCredentialEnvelopeCodec.decode(encryptedBinding)
                    assertEquals(binding.bindingCommitId, envelope.bindingCommitId)
                    assertEquals(binding.sessionId, envelope.sessionId)
                    assertFalse(envelope.refreshToken.isNullOrBlank())
                    assertTrue(envelope.accessToken.isNotBlank())
                } finally {
                    encryptedBinding.fill(0)
                }

                val encryptedNextCode = requireNotNull(
                    credentials.read(AccountRecoverySlot.SOURCE.profile),
                )
                try {
                    val envelope = SessionCredentialEnvelopeCodec.decode(encryptedNextCode)
                    assertEquals(AccountRecoverySlot.SOURCE.name, envelope.accountRecoveryRole)
                    assertTrue(envelope.refreshPending)
                    assertFalse(envelope.accountRecoveryPending.isNullOrBlank())
                } finally {
                    encryptedNextCode.fill(0)
                }
            } finally {
                val current = settings.settings.first()
                current.activeServerProfileId?.let { credentials.clear(it) }
                current.m5Binding?.deviceKeyAlias?.let(keys::delete)
                AccountRecoverySlot.entries.forEach { credentials.clear(it.profile) }
                settings.update(NonSecretSettings())
            }
        }
    }

    private fun oneShotHandoff(base: String): JSONObject = CLIENT.newCall(
        Request.Builder()
            .url("$base/account-recovery-e2e/one-shot-handoff")
            .header("Cache-Control", "no-store")
            .build(),
    ).execute().use { response ->
        check(response.isSuccessful) { "RECOVERY_E2E_HANDOFF_${response.code}" }
        JSONObject(requireNotNull(response.body).string())
    }

    private companion object {
        val CLIENT = OkHttpClient()
    }
}
