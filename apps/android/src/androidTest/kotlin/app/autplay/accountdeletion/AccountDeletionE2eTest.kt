package app.autplay.accountdeletion

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.application.accountrecovery.AccountDeletionCancellationTransport
import app.autplay.application.accountrecovery.AccountDeletionSourceRuntime
import app.autplay.application.accountrecovery.AccountDeletionSourceState
import app.autplay.application.accountrecovery.AccountRecoveryRecipientRuntime
import app.autplay.application.accountrecovery.AccountRecoveryRecipientState
import app.autplay.application.accountrecovery.AccountRecoverySlot
import app.autplay.application.accountrecovery.AccountRestorationPurpose
import app.autplay.application.accountrecovery.OkHttpAccountDeletionTransport
import app.autplay.application.accountrecovery.ProfilePairingRecoveryBindingCommitter
import app.autplay.application.accountrecovery.SettingsAccountDeletionBindingPort
import app.autplay.application.profilebinding.M5BindingMaterializationCoordinator
import app.autplay.application.profilebinding.M5LocalIntentMaterializer
import app.autplay.application.profilebinding.PendingLocalIntentSummary
import app.autplay.application.profilepairing.FirstBindCeremonyGate
import app.autplay.application.profilepairing.FirstBindCeremonyOwner
import app.autplay.application.profilepairing.OkHttpProfilePairingPort
import app.autplay.application.profilepairing.ProfilePairingRuntime
import app.autplay.application.publicaccess.ActiveProfileGate
import app.autplay.application.selfpairing.SettingsSelfPairingSourceContext
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.M5SessionRotationClient
import app.autplay.data.security.RefreshingSessionCredentials
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.security.SettingsM5RotationContextResolver
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.Base64
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Physical deletion request and explicit lost-reply cancellation proof. */
@RunWith(AndroidJUnit4::class)
class AccountDeletionE2eTest {
    @Test fun requestDetachesThenCancellationCreatesOneNewBinding() {
        val configuredBase = InstrumentationRegistry.getArguments()
            .getString("accountDeletionE2eBaseUrl")
        assumeTrue(
            "The joined deletion harness supplies accountDeletionE2eBaseUrl",
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
            val recoveryCode = handoff.getString("recovery_code")
            val context = InstrumentationRegistry.getInstrumentation().targetContext
            val settings = applicationNonSecretSettingsStore(context)
            val credentials = AndroidKeystoreCredentialStore(context)
            val keys = AndroidM5DeviceKeyStore()
            val profile = ServerProfileId(serverId)
            val sourceAlias = "account-deletion-e2e-source"
            var replacementAlias: String? = null
            val checkpoint = M5BindingCheckpoint(
                bindingCommitId = bindingCommitId,
                serverInstanceId = serverId,
                identityEpoch = identityEpoch,
                identityThumbprintSha256 = identityThumbprint,
                deviceKeyAlias = sourceAlias,
                sessionId = sessionId,
                sessionFamilyId = familyId,
                sessionGeneration = 0,
            )
            val encoded = SessionCredentialEnvelopeCodec.encode(
                SessionCredentialEnvelope(
                    accessToken = handoff.getString("access_token"),
                    refreshToken = handoff.getString("refresh_token"),
                    generation = 0,
                    bindingCommitId = bindingCommitId,
                    sessionId = sessionId,
                    sessionFamilyId = familyId,
                    sessionGeneration = 0,
                ),
            )
            settings.update(NonSecretSettings())
            try {
                keys.ensure(sourceAlias)
                registerSourceKey(base, keys, sourceAlias)
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
                val rotation = M5SessionRotationClient(
                    SettingsM5RotationContextResolver(settings),
                    keys,
                )
                val deletionTransport = OkHttpAccountDeletionTransport(
                    credentials = { identity, selectedProfile ->
                        RefreshingSessionCredentials(
                            "${identity.apiOrigin}/api/v1",
                            credentials,
                            m5Rotation = rotation,
                        ).also { require(selectedProfile == profile) }
                    },
                    allowDevelopmentHttp = true,
                )
                val source = AccountDeletionSourceRuntime(
                    credentials = credentials,
                    contexts = SettingsSelfPairingSourceContext(settings, credentials),
                    binding = SettingsAccountDeletionBindingPort(settings, credentials),
                    keys = keys,
                    transport = deletionTransport,
                    deviceName = "A55 deletion source",
                    appVersion = "physical-e2e",
                    allowDevelopmentHttp = true,
                )
                source.load()
                val ready = source.state.value as? AccountDeletionSourceState.Ready
                    ?: error("DELETION_E2E_READY_REQUIRED")
                assertEquals(accountId, ready.status.accountId)
                assertTrue(ready.status.canRequest)
                assertEquals(1L, ready.status.codeGeneration)
                assertEquals(1L, ready.status.authorityGeneration)

                source.request(accountId, recoveryCode)
                val recorded = source.state.value as? AccountDeletionSourceState.Recorded
                    ?: error("DELETION_E2E_RECORDED_REQUIRED")
                assertEquals("PENDING", recorded.receipt.state)
                assertEquals(1L, recorded.receipt.revision)
                assertEquals(accountId, recorded.receipt.accountId)
                assertTrue(recorded.receipt.cancelBefore.isAfter(recorded.receipt.requestedAt))
                assertNull(settings.settings.first().activeServerProfileId)
                assertNull(credentials.read(profile))
                assertNotNull(
                    credentials.read(AccountRecoverySlot.DELETION_SOURCE.profile)
                        ?.also { it.fill(0) },
                )

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
                        ): LocalId = error("DELETION_E2E_UNEXPECTED_MATERIALIZATION")
                    },
                )
                val pairing = ProfilePairingRuntime(
                    scope = this,
                    settings = settings,
                    credentials = credentials,
                    deviceKeys = keys,
                    port = profilePort,
                    materialization = materialization,
                    deviceName = "A55 deletion cancellation",
                    reportSafeError = {},
                    allowUnsafeDevelopmentHttp = true,
                    firstBindGate = gate,
                )
                val cancellation = AccountRecoveryRecipientRuntime(
                    credentials = credentials,
                    keys = keys,
                    transport = AccountDeletionCancellationTransport(deletionTransport),
                    discovery = profilePort,
                    binding = ProfilePairingRecoveryBindingCommitter(
                        pairing,
                        profilePort,
                        settings,
                        credentials,
                        keys,
                        FirstBindCeremonyOwner.ACCOUNT_DELETE_CANCEL,
                    ),
                    activeProfile = ActiveProfileGate {
                        settings.settings.first().activeServerProfileId != null
                    },
                    firstBind = gate,
                    deviceName = "A55 deletion cancellation",
                    appVersion = "physical-e2e",
                    allowDevelopmentHttp = true,
                    purpose = AccountRestorationPurpose.DELETE_CANCEL,
                )
                cancellation.inspectManual(base, accountId, recoveryCode)
                assertTrue(cancellation.state.value is AccountRecoveryRecipientState.ConfirmServer)
                cancellation.confirmServer()
                val preview = cancellation.state.value as? AccountRecoveryRecipientState.ConfirmAccount
                    ?: error("DELETION_E2E_CANCEL_PREVIEW_REQUIRED")
                assertEquals(accountId, preview.preview.accountId)
                assertEquals(1L, preview.preview.generation)
                assertEquals("PENDING", preview.preview.deletion?.state)
                assertEquals(recorded.receipt.requestId, preview.preview.deletion?.requestId)

                cancellation.confirmAccount(accountId)
                assertEquals(AccountRecoveryRecipientState.Connected, cancellation.state.value)
                val current = settings.settings.first()
                val replacement = requireNotNull(current.m5Binding)
                replacementAlias = replacement.deviceKeyAlias
                assertEquals(accountId, current.activeUserId?.value)
                assertFalse(replacement.bindingCommitId == bindingCommitId)
                assertNull(credentials.read(AccountRecoverySlot.DELETION_SOURCE.profile))
                assertNull(credentials.read(AccountRecoverySlot.DELETION_RECIPIENT.profile))
                val active = requireNotNull(credentials.read(profile))
                try {
                    val envelope = SessionCredentialEnvelopeCodec.decode(active)
                    assertEquals(replacement.bindingCommitId, envelope.bindingCommitId)
                    assertEquals(replacement.sessionId, envelope.sessionId)
                } finally {
                    active.fill(0)
                }
                val nextCode = requireNotNull(credentials.read(AccountRecoverySlot.SOURCE.profile))
                try {
                    val envelope = SessionCredentialEnvelopeCodec.decode(nextCode)
                    assertEquals(AccountRecoverySlot.SOURCE.name, envelope.accountRecoveryRole)
                    assertTrue(envelope.refreshPending)
                    assertFalse(envelope.accountRecoveryPending.isNullOrBlank())
                } finally {
                    nextCode.fill(0)
                }
            } finally {
                encoded.fill(0)
                val current = settings.settings.first()
                current.activeServerProfileId?.let { credentials.clear(it) }
                current.m5Binding?.deviceKeyAlias?.let(keys::delete)
                replacementAlias?.takeIf { it != current.m5Binding?.deviceKeyAlias }?.let(keys::delete)
                keys.delete(sourceAlias)
                AccountRecoverySlot.entries.forEach { credentials.clear(it.profile) }
                settings.update(NonSecretSettings())
            }
        }
    }

    private fun oneShotHandoff(base: String): JSONObject = CLIENT.newCall(
        Request.Builder()
            .url("$base/account-deletion-e2e/one-shot-handoff")
            .header("Cache-Control", "no-store")
            .build(),
    ).execute().use { response ->
        check(response.isSuccessful) { "DELETION_E2E_HANDOFF_${response.code}" }
        JSONObject(requireNotNull(response.body).string())
    }

    private fun registerSourceKey(base: String, keys: AndroidM5DeviceKeyStore, alias: String) {
        val spki = keys.publicKeySpki(alias)
        try {
            val body = JSONObject()
                .put("public_key_spki_b64", Base64.getEncoder().encodeToString(spki))
                .put("thumbprint_sha256", keys.publicKeyThumbprintSha256(alias))
                .toString()
                .toRequestBody("application/json".toMediaType())
            CLIENT.newCall(
                Request.Builder()
                    .url("$base/account-deletion-e2e/register-source-key")
                    .header("Cache-Control", "no-store")
                    .post(body)
                    .build(),
            ).execute().use { response ->
                check(response.isSuccessful) { "DELETION_E2E_REGISTER_KEY_${response.code}" }
            }
        } finally {
            spki.fill(0)
        }
    }

    private companion object {
        val CLIENT = OkHttpClient()
    }
}
