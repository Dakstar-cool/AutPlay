package app.autplay.work

import androidx.room3.useWriterConnection
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.await
import app.autplay.AutPlayRuntime
import app.autplay.data.local.entity.LocalAudioStateEntity
import app.autplay.data.local.entity.RemoteImportJobProjectionEntity
import app.autplay.data.local.entity.UserTrackRefEntity
import app.autplay.data.local.entity.VaultUploadIntentEntity
import app.autplay.data.security.AndroidKeystoreCredentialStore
import app.autplay.data.security.AndroidM5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.M5BindingCheckpoint
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.applicationNonSecretSettingsStore
import app.autplay.domain.DeviceId
import app.autplay.domain.ServerProfileId
import app.autplay.domain.UserId
import java.util.UUID
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Real WorkManager -> two-argument worker -> Room/settings/Keystore -> HTTP composition. */
@RunWith(AndroidJUnit4::class)
class ServerWorkersAuthenticationTest {
    @Test fun bothWorkersUseFreshM5CredentialEnvelope() = runBlocking { exercise(Mode.FRESH) }
    @Test fun bothWorkersRotateRejectedM5GenerationExactlyOnce() = runBlocking { exercise(Mode.ROTATE) }
    @Test fun bothWorkersStopAfterTerminalRotationFailure() = runBlocking { exercise(Mode.TERMINAL) }
    @Test fun bothWorkersStopAfterRepeatedUnauthorized() = runBlocking { exercise(Mode.REPEATED_401) }
    @Test fun bothWorkersRejectInactiveProfileBeforeHttp() = runBlocking { exercise(Mode.WRONG_PROFILE) }
    @Test fun bothWorkersReplayExactPendingRotationAfterTemporaryServerFailure() = runBlocking { exercise(Mode.SERVER_BUSY) }
    @Test fun bothWorkersFenceProfileReplacementBetweenRequestAndRefresh() = runBlocking { exercise(Mode.SWITCH_PROFILE) }

    private suspend fun exercise(mode: Mode) {
        for (upload in listOf(false, true)) {
            val fixture = Fixture(upload, mode)
            try { fixture.run() } finally { fixture.close() }
        }
    }

    private enum class Mode { FRESH, ROTATE, TERMINAL, REPEATED_401, WRONG_PROFILE, SERVER_BUSY, SWITCH_PROFILE }

    private class Fixture(private val upload: Boolean, private val mode: Mode) {
        private val context = InstrumentationRegistry.getInstrumentation().targetContext
        private val settings = applicationNonSecretSettingsStore(context)
        private val database = AutPlayRuntime.database(context)
        private val credentials = AndroidKeystoreCredentialStore(context)
        private val keys = AndroidM5DeviceKeyStore()
        private val workManager = WorkManager.getInstance(context)
        private val ids = List(22) { UUID.randomUUID().toString() }
        private val profile = ServerProfileId(ids[1])
        private val alias = "autplay-worker-test-${ids[2]}"
        private val access = "a".repeat(64)
        private val successorAccess = "s".repeat(64)
        private val checkpoint = M5BindingCheckpoint(ids[3], ids[4], 1, "a".repeat(64), alias, ids[5], ids[6], 0)
        private val server = MockWebServer()
        private val bearers = CopyOnWriteArrayList<String?>()
        private var rotations = 0
        private val rotationBodies = CopyOnWriteArrayList<String>()
        private lateinit var original: NonSecretSettings
        private val workName = if (upload) "vault-upload-${ids[14]}" else "remote-import-${ids[20]}"

        suspend fun run() {
            original = settings.settings.first()
            keys.ensure(alias)
            val material = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope(
                access, "r".repeat(64), 0, bindingCommitId = checkpoint.bindingCommitId,
                sessionId = checkpoint.sessionId, sessionFamilyId = checkpoint.sessionFamilyId, sessionGeneration = 0,
            ))
            try { credentials.write(profile, material) } finally { material.fill(0) }
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.requestUrl?.encodedPath == "/api/v1/account/sessions/rotate") {
                        rotations++
                        val body = request.body.readUtf8()
                        rotationBodies += body
                        if (mode == Mode.TERMINAL) return json("{}", 401)
                        if (mode == Mode.SERVER_BUSY && rotations == 1) return json("{}", 503)
                        val payload = Json.parseToJsonElement(body).jsonObject
                        val rotationId = requireNotNull(payload["rotation_id"]).jsonPrimitive.content
                        return json("""{"contract_version":"v1","schema_version":1,"rotation_id":"$rotationId","parent_session_id":"${ids[5]}","session_id":"${ids[9]}","family_id":"${ids[6]}","generation":1,"access_token":"$successorAccess"}""")
                    }
                    bearers += request.getHeader("Authorization")
                    if (mode == Mode.SWITCH_PROFILE) runBlocking {
                        settings.update(settings.settings.first().copy(activeServerProfileId = ServerProfileId(ids[0])))
                    }
                    if (mode != Mode.FRESH && (bearers.size == 1 || mode == Mode.REPEATED_401)) return json("{}", 401)
                    return if (upload) json("""{"upload_id":"${ids[15]}","offset":100,"expected_size":100,"state":"PROCESSING"}""")
                    else json("""{"import_job_id":"${ids[20]}","state":"COMPLETED","progress_current":1,"progress_total":1,"counts":{"AUTO_MATCH":1},"entries":[],"next_after":null}""")
                }
            }
            server.start()
            val origin = server.url("/").toString().trimEnd('/')
            settings.update(NonSecretSettings(
                activeServerProfileId = if (mode == Mode.WRONG_PROFILE) ServerProfileId(ids[0]) else profile,
                activeUserId = UserId(ids[7]), deviceId = DeviceId(ids[8]), serverBaseUrl = origin,
                streamBaseUrl = origin, syncOnMeteredNetwork = true, m5Binding = checkpoint,
            ))
            seed()
            if (upload) VaultUploadWorkScheduler.enqueue(context, ids[14])
            else RemoteImportWorkScheduler.enqueue(context, ids[20])
            val terminal = mode in setOf(Mode.TERMINAL, Mode.REPEATED_401, Mode.WRONG_PROFILE, Mode.SWITCH_PROFILE)
            val minimumAttempts = if (mode == Mode.SERVER_BUSY) 2 else 1
            val work = withTimeout(40_000) {
                var result: WorkInfo? = null
                while (result == null) {
                    result = workManager.getWorkInfosForUniqueWork(workName).get(2, TimeUnit.SECONDS).singleOrNull()
                        ?.takeIf { it.state.isFinished || (upload && !terminal && it.state == WorkInfo.State.ENQUEUED && it.runAttemptCount >= minimumAttempts) }
                    if (result == null) delay(50)
                }
                result
            }
            assertEquals(when { terminal -> WorkInfo.State.FAILED; upload -> WorkInfo.State.ENQUEUED; else -> WorkInfo.State.SUCCEEDED }, work.state)
            assertEquals(when (mode) { Mode.FRESH, Mode.WRONG_PROFILE, Mode.SWITCH_PROFILE -> 0; Mode.SERVER_BUSY -> 2; else -> 1 }, rotations)
            if (mode == Mode.SWITCH_PROFILE) assertEquals(1, server.requestCount)
            if (mode == Mode.SERVER_BUSY) assertEquals(1, rotationBodies.distinct().size)
            if (mode == Mode.WRONG_PROFILE) assertEquals(0, server.requestCount)
            else {
                assertEquals("Bearer $access", bearers.first())
                if (mode in setOf(Mode.ROTATE, Mode.REPEATED_401, Mode.SERVER_BUSY)) {
                    assertEquals(listOf("Bearer $access", "Bearer $successorAccess"), bearers.toList())
                    assertEquals(1L, settings.settings.first().m5Binding?.sessionGeneration)
                    assertEquals(ids[9], settings.settings.first().m5Binding?.sessionId)
                }
            }
            val error = if (upload) database.serverFeatureProjectionDao().vaultUploadIntent(ids[14])?.lastErrorCode
                else database.serverFeatureProjectionDao().remoteImportJobById(ids[20])?.lastErrorCode
            assertEquals(when { mode in setOf(Mode.WRONG_PROFILE, Mode.SWITCH_PROFILE) -> "SERVER_PROFILE_NOT_ACTIVE"; terminal -> "SESSION_REQUIRED"; else -> null }, error)
            if (!upload && !terminal) {
                val report = requireNotNull(database.serverFeatureProjectionDao().remoteImportJobById(ids[20]))
                assertEquals("COMPLETED", report.state)
                assertEquals(ids[21], report.deliveryJobId)
                assertEquals(1, report.resolvedCount)
            }
            if (upload && !terminal) assertEquals("PROCESSING", database.serverFeatureProjectionDao().vaultUploadIntent(ids[14])?.state)
            assertTrue(work.runAttemptCount <= minimumAttempts)
        }

        private suspend fun seed() {
            if (!upload) {
                database.serverFeatureProjectionDao().upsertRemoteImportJob(RemoteImportJobProjectionEntity(
                    profile.value, ids[20], ids[21], "RUNNING", 0, 1, 0, 0, 0, 0, 0, null, 1,
                ))
                return
            }
            database.libraryDao().upsertTrackRef(UserTrackRefEntity(
                ids[10], ids[11], null, ids[12], "RESOLVED", "Worker fixture", "Fixture", null,
                1_000, 1.0, "CLEAN", 1, 0, 1, 1, null, profile.value,
            ))
            database.localAudioDao().upsertState(LocalAudioStateEntity(
                ids[13], ids[10], null, null, "content://${context.packageName}.fixture/${ids[13]}", false,
                null, null, null, null, null, null, null, null, null, 1_000,
                "AVAILABLE", "USER_IMPORT", 100, null, null, 1, 1,
            ))
            database.serverFeatureProjectionDao().upsertVaultUploadIntent(VaultUploadIntentEntity(
                ids[14], profile.value, ids[13], ids[12], "b".repeat(64), 100, ids[15], 100, "PROCESSING", 0, null, 1, 1,
            ))
        }

        suspend fun close() {
            workManager.cancelUniqueWork(workName).await()
            if (::original.isInitialized) settings.update(original)
            credentials.clear(profile)
            keys.delete(alias)
            database.useWriterConnection { connection ->
                for ((table, column, id) in listOf(
                    Triple("vault_upload_intent", "upload_intent_id", ids[14]),
                    Triple("local_audio_state", "local_audio_state_id", ids[13]),
                    Triple("user_track_ref", "local_user_track_ref_id", ids[10]),
                    Triple("remote_import_job_projection", "import_job_id", ids[20]),
                )) {
                    connection.usePrepared("DELETE FROM $table WHERE $column = ?") { statement ->
                        statement.bindText(1, id)
                        statement.step()
                    }
                }
            }
            server.close()
        }

        private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code)
            .setHeader("Cache-Control", "no-store").setHeader("Pragma", "no-cache").setBody(body)
    }
}
