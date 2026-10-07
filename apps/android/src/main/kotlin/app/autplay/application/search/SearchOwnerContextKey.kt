package app.autplay.application.search

import app.autplay.application.sync.ClientEventBinding

/** A request identity only; database queries continue using the separate profile ID. */
internal fun ClientEventBinding?.searchOwnerContextKey(): String? = this?.let {
    "${it.serverProfileId.value}|${it.userId.value}|${it.deviceId.value}"
}
