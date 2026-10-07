package app.autplay

import kotlinx.coroutines.CancellationException

/** Local navigation is dispatched before feedback persistence, including when its write fails. */
internal suspend fun applyPlaybackDislike(
    advance: suspend () -> Unit,
    record: suspend () -> Unit,
    reportError: (String) -> Unit,
) {
    for ((action, errorCode) in listOf(advance to "QUEUE_NAVIGATION_UNAVAILABLE", record to "PREFERENCE_UNAVAILABLE")) {
        try {
            action()
        } catch (error: CancellationException) {
            throw error
        } catch (_: Exception) {
            reportError(errorCode)
        }
    }
}
