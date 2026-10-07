package app.autplay.application.server

import java.util.UUID

data class InternetSourceSearchAttempt(val generation: Long, val query: String, val operationId: String,
    val catalogueContextId: String?)

/** A transport replay retains the accepted operation; a terminal acquisition retry admits a new one. */
class InternetSourceSearchSession(query: String, catalogueContextId: String? = null,
    private val operationIdFactory: () -> String = { UUID.randomUUID().toString() }) {
    var current: InternetSourceSearchAttempt = InternetSourceSearchAttempt(0, query, operationIdFactory(), catalogueContextId)
        private set

    fun retryTransport(): InternetSourceSearchAttempt = current.copy(generation = current.generation + 1).also { current = it }
    fun retryTerminalAcquisition(): InternetSourceSearchAttempt = current.copy(generation = current.generation + 1,
        operationId = operationIdFactory()).also { current = it }
    fun accepts(attempt: InternetSourceSearchAttempt): Boolean = attempt == current
}
