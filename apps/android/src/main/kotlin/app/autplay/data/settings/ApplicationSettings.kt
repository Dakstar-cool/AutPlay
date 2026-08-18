package app.autplay.data.settings

import android.content.Context
import androidx.datastore.preferences.preferencesDataStore

private val Context.nonSecretPreferences by preferencesDataStore(name = "autplay_non_secret_settings")

/** Returns the process-singleton DataStore-backed non-secret settings adapter. */
fun applicationNonSecretSettingsStore(context: Context): NonSecretSettingsStore =
    DataStoreNonSecretSettingsStore(context.applicationContext.nonSecretPreferences)
