package app.autplay.data.settings

import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.mutablePreferencesOf
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OnboardingRevisionMigrationTest {
    @Test
    fun existingInstallationSkipsNewFirstRunEducation() = runBlocking {
        val migration = OnboardingRevisionMigration(existingInstallation = true)
        val legacyPreferences = emptyPreferences()

        assertTrue(migration.shouldMigrate(legacyPreferences))
        assertEquals(
            CURRENT_ONBOARDING_REVISION,
            migration.migrate(legacyPreferences)[ONBOARDING_REVISION_KEY],
        )
    }

    @Test
    fun freshInstallationPersistsIncompleteCheckpoint() = runBlocking {
        val migration = OnboardingRevisionMigration(existingInstallation = false)
        val freshPreferences = emptyPreferences()

        assertTrue(migration.shouldMigrate(freshPreferences))
        assertEquals(0, migration.migrate(freshPreferences)[ONBOARDING_REVISION_KEY])
    }

    @Test
    fun explicitIncompleteCheckpointSurvivesLaterUpdates() = runBlocking {
        val migration = OnboardingRevisionMigration(existingInstallation = true)
        val incompletePreferences = mutablePreferencesOf(ONBOARDING_REVISION_KEY to 0)

        assertFalse(migration.shouldMigrate(incompletePreferences))
        assertEquals(0, incompletePreferences[ONBOARDING_REVISION_KEY])
    }
}
