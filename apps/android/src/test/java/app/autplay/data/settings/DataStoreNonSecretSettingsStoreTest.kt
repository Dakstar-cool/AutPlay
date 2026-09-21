package app.autplay.data.settings

import androidx.datastore.core.okio.OkioStorage
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.PreferencesSerializer
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.io.File
import java.util.Base64
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okio.FileSystem
import okio.Path.Companion.toOkioPath
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class DataStoreNonSecretSettingsStoreTest {
    @get:Rule
    val temporaryFolder = TemporaryFolder()

    @Test
    fun requiredRecoverySetupAndExactSaveEvidenceSurviveRecreationWithoutLegacyBackfill() = runBlocking {
        val file = temporaryFolder.root.resolve("recovery-setup.preferences_pb")
        val binding = M5BindingCheckpoint(
            "44444444-4444-4444-8444-444444444444", "11111111-1111-4111-8111-111111111111", 1,
            "a".repeat(64), "fixture-key", "66666666-6666-4666-8666-666666666666",
            "77777777-7777-4777-8777-777777777777", 0,
        )
        val pending = AccountRecoverySetupCheckpoint(binding.serverInstanceId,
            "22222222-2222-4222-8222-222222222222", binding.bindingCommitId)
        val original = NonSecretSettings(
            activeServerProfileId = ServerProfileId(binding.serverInstanceId), activeUserId = UserId(pending.accountId),
            deviceId = DeviceId("33333333-3333-4333-8333-333333333333"),
            serverBaseUrl = "https://api.example.test", streamBaseUrl = "https://stream.example.test",
            m5Binding = binding, accountRecoverySetup = pending,
        )
        val firstScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val store = hostPreferenceStore(firstScope, file)
            assertNull(store.settings.first().accountRecoverySetup)
            store.update(original)
        } finally { firstScope.coroutineContext[Job]?.cancelAndJoin() }

        val saved = pending.copy(savedCodeGeneration = 7, savedDocumentSha256 = "b".repeat(64))
        val secondScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val store = hostPreferenceStore(secondScope, file)
            assertEquals(original, store.settings.first())
            assertTrue(pending.matches(store.settings.first()))
            store.mutate { it.copy(accountRecoverySetup = saved) }
        } finally { secondScope.coroutineContext[Job]?.cancelAndJoin() }

        val thirdScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val store = hostPreferenceStore(thirdScope, file)
            assertEquals(original.copy(accountRecoverySetup = saved), store.settings.first())
            store.mutate { it.copy(accountRecoverySetup = null) }
            assertEquals(original.copy(accountRecoverySetup = null), store.settings.first())
            assertFalse(saved.matches(original.copy(activeUserId = UserId("99999999-9999-4999-8999-999999999999"))))
            assertFalse(saved.matches(original.copy(m5Binding = binding.copy(bindingCommitId = "88888888-8888-4888-8888-888888888888"))))
        } finally { thirdScope.coroutineContext[Job]?.cancelAndJoin() }
    }

    private fun hostPreferenceStore(scope: CoroutineScope, file: File): DataStoreNonSecretSettingsStore =
        DataStoreNonSecretSettingsStore(PreferenceDataStoreFactory.create(
            // Android's SDK stub selects File.renameTo on the host, which cannot replace a file
            // on Windows. Okio uses host NIO atomic replacement with the real preferences codec.
            storage = OkioStorage(FileSystem.SYSTEM, PreferencesSerializer) { file.toOkioPath() },
            scope = scope,
        ))

    @Test
    fun incompleteRecoverySetupCannotBeReadAsAnOptionalLegacyAccount() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val dataStore = PreferenceDataStoreFactory.create(scope = scope,
                produceFile = { temporaryFolder.root.resolve("incomplete-recovery-setup.preferences_pb") })
            dataStore.edit {
                it[stringPreferencesKey("account_recovery_setup_server_id")] = "11111111-1111-4111-8111-111111111111"
            }
            val store = DataStoreNonSecretSettingsStore(dataStore)
            assertTrue(runCatching { store.settings.first() }.exceptionOrNull() is IllegalArgumentException)
        } finally { scope.coroutineContext[Job]?.cancelAndJoin() }
    }

    @Test
    fun trustEvidenceWithoutCapabilitiesSurvivesInitialPersistence() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val dataStore = PreferenceDataStoreFactory.create(
            scope = scope,
            produceFile = { temporaryFolder.root.resolve("settings.preferences_pb") },
        )
        val store = DataStoreNonSecretSettingsStore(dataStore)
        val evidence = M5TrustEvidence(
            identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(ByteArray(32) { 7 }),
            serverLabelHint = "Local server",
        )
        try {
            store.update(
                NonSecretSettings(
                    activeServerProfileId = ServerProfileId("11111111-1111-4111-8111-111111111111"),
                    activeUserId = UserId("22222222-2222-4222-8222-222222222222"),
                    deviceId = DeviceId("33333333-3333-4333-8333-333333333333"),
                    serverBaseUrl = "https://api.example.test",
                    streamBaseUrl = "https://stream.example.test",
                    m5Binding = M5BindingCheckpoint(
                        bindingCommitId = "44444444-4444-4444-8444-444444444444",
                        serverInstanceId = "55555555-5555-4555-8555-555555555555",
                        identityEpoch = 1,
                        identityThumbprintSha256 = "a".repeat(64),
                        deviceKeyAlias = "autplay.m5.test",
                        sessionId = "66666666-6666-4666-8666-666666666666",
                        sessionFamilyId = "77777777-7777-4777-8777-777777777777",
                        sessionGeneration = 0,
                    ),
                    m5TrustEvidence = evidence,
                    m5LocalDataDecision = "KEEP_LOCAL",
                ),
            )

            assertEquals(evidence, store.settings.first().m5TrustEvidence)
        } finally {
            scope.cancel()
        }
    }

    @Test
    fun boundServerProfileSurvivesDataStoreRecreation() = runBlocking {
        val file = temporaryFolder.root.resolve("recreated-settings.preferences_pb")
        val expected = NonSecretSettings(
            activeServerProfileId = ServerProfileId("11111111-1111-4111-8111-111111111111"),
            activeUserId = UserId("22222222-2222-4222-8222-222222222222"),
            deviceId = DeviceId("33333333-3333-4333-8333-333333333333"),
            serverBaseUrl = "https://api.example.test",
            streamBaseUrl = "https://stream.example.test",
            m5Binding = M5BindingCheckpoint(
                bindingCommitId = "44444444-4444-4444-8444-444444444444",
                serverInstanceId = "55555555-5555-4555-8555-555555555555",
                identityEpoch = 2,
                identityThumbprintSha256 = "a".repeat(64),
                deviceKeyAlias = "autplay.m5.recreated",
                sessionId = "66666666-6666-4666-8666-666666666666",
                sessionFamilyId = "77777777-7777-4777-8777-777777777777",
                sessionGeneration = 3,
            ),
            m5TrustEvidence = M5TrustEvidence(
                identityPublicKeySpkiB64 = Base64.getEncoder().encodeToString(ByteArray(32) { 9 }),
                serverLabelHint = "Persistent server",
            ),
            m5LocalDataDecision = "KEEP_LOCAL",
        )

        val firstScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val firstStore = DataStoreNonSecretSettingsStore(
            PreferenceDataStoreFactory.create(scope = firstScope, produceFile = { file }),
        )
        firstStore.update(expected)
        firstScope.coroutineContext[Job]?.cancelAndJoin()

        val reopenedScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val reopenedStore = DataStoreNonSecretSettingsStore(
                PreferenceDataStoreFactory.create(scope = reopenedScope, produceFile = { file }),
            )
            assertEquals(expected, reopenedStore.settings.first())
        } finally {
            reopenedScope.cancel()
        }
    }
}
