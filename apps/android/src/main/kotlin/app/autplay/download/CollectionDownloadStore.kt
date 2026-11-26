package app.autplay.download

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate

/** Per-operation membership can exceed WorkManager's 10 KiB data limit. */
internal class CollectionDownloadStore(context: Context) {
    private val prefs = context.getSharedPreferences("catalog-download-operations", Context.MODE_PRIVATE)

    fun operation(key: String): String? = prefs.getString(key, null)
    fun begin(key: String, operation: String, refs: Set<String>, missing: Int) {
        val previous = this.operation(key)
        prefs.edit(commit = true) {
            if (previous != null && previous != operation) {
                for (field in listOf("requested", "missing", "device", "saved", "unavailable")) remove("$previous:$field")
            }
            putString(key, operation)
            putStringSet("$operation:requested", refs)
            putInt("$operation:missing", missing)
        }
    }

    fun requested(operation: String): Set<String> = prefs.getStringSet("$operation:requested", emptySet()).orEmpty()
    fun missing(operation: String) = prefs.getInt("$operation:missing", 0)

    fun finish(operation: String, refs: Set<String>, saved: Int, unavailable: Int) = prefs.edit(commit = true) {
        putStringSet("$operation:device", refs)
        putInt("$operation:saved", saved)
        putInt("$operation:unavailable", unavailable)
    }

    fun result(operation: String): DownloadBatchResult? {
        if (!prefs.contains("$operation:saved")) return null
        return DownloadBatchResult(prefs.getStringSet("$operation:device", emptySet()).orEmpty(),
            prefs.getInt("$operation:saved", 0), prefs.getInt("$operation:unavailable", 0))
    }

    fun changes(key: String) = callbackFlow {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, changed ->
            if (changed == key || changed == "${operation(key)}:saved") trySend(Unit)
        }
        prefs.registerOnSharedPreferenceChangeListener(listener)
        trySend(Unit)
        awaitClose { prefs.unregisterOnSharedPreferenceChangeListener(listener) }
    }.conflate()
}

internal data class DownloadBatchResult(val refs: Set<String>, val saved: Int, val unavailable: Int)
