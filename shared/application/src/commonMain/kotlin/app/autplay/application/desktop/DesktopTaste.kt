package app.autplay.application.desktop

/** Opaque values stay persisted; only exact known values have a local interpretation. */
object DesktopTastePolicy {
    const val INCLUDED: String = "INCLUDED"
    const val EXCLUDED: String = "EXCLUDED"

    fun choice(excluded: Boolean): String = if (excluded) EXCLUDED else INCLUDED

    fun isKnown(value: String): Boolean = value == INCLUDED || value == EXCLUDED

    /** A new listen clears known listen choices; unsupported future meaning cannot be inferred. */
    fun forNewListen(value: String): String = if (isKnown(value)) INCLUDED else value

    /** Null means the effective inclusion is unsupported, with no normalization of raw choices. */
    fun isExcluded(sessionTasteState: String, listenTasteState: String): Boolean? = when {
        sessionTasteState == EXCLUDED || listenTasteState == EXCLUDED -> true
        sessionTasteState == INCLUDED && listenTasteState == INCLUDED -> false
        else -> null
    }
}

/** Confirmed controller projection. Native state and track preferences have independent owners. */
data class DesktopTasteState(
    val queueId: String? = null,
    val currentEntryId: String? = null,
    val listeningEventId: String? = null,
    val sessionTasteState: String = DesktopTastePolicy.INCLUDED,
    val listenTasteState: String = DesktopTastePolicy.INCLUDED,
    val listenCommandsEnabled: Boolean = false,
    val sessionCommandsEnabled: Boolean = false,
    val errorCode: String? = null,
)
