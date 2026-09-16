package app.autplay.data.settings

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** Exercises Android file replacement and DataStore recreation on the actual target filesystem. */
class DataStoreDeveloperModeDeviceTest {
    @get:Rule val temporaryFolder = TemporaryFolder()
    @Test
    fun developerModeDefaultsOffAndSurvivesRecreationAndUnrelatedUpdates() = runBlocking {
        val file = temporaryFolder.root.resolve("developer-mode.preferences_pb")
        val firstScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val firstStore = DataStoreNonSecretSettingsStore(PreferenceDataStoreFactory.create(scope = firstScope, produceFile = { file }))
        assertEquals(false, firstStore.settings.first().developerMode)
        firstStore.mutate { it.copy(developerMode = true) }
        firstScope.coroutineContext[Job]!!.cancelAndJoin()
        val secondScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        try {
            val secondStore = DataStoreNonSecretSettingsStore(PreferenceDataStoreFactory.create(scope = secondScope, produceFile = { file }))
            assertEquals(true, secondStore.settings.first().developerMode)
            secondStore.mutate { it.copy(appearanceMode = "LIGHT") }
            assertEquals(true, secondStore.settings.first().developerMode)
            secondStore.mutate { it.copy(developerMode = false) }
            assertEquals(false, secondStore.settings.first().developerMode)
        } finally {
            secondScope.coroutineContext[Job]!!.cancelAndJoin()
        }
    }

}
