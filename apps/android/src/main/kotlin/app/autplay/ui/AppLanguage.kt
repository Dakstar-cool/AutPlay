package app.autplay.ui

import androidx.annotation.StringRes
import app.autplay.R

/** Supported application-local UI languages. Unknown stored values fall back without being rewritten. */
internal enum class AppLanguage(
    val storedValue: String,
    val languageTag: String?,
    @StringRes val labelRes: Int,
) {
    System("SYSTEM", null, R.string.settings_language_system),
    Russian("RU", "ru", R.string.settings_language_russian),
    English("EN", "en", R.string.settings_language_english),
    ;

    companion object {
        fun knownFromStoredValue(value: String): AppLanguage? =
            entries.firstOrNull { it.storedValue == value }

        fun fromStoredValue(value: String): AppLanguage =
            knownFromStoredValue(value) ?: System
    }
}
