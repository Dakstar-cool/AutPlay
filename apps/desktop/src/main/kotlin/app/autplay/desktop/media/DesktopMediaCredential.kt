package app.autplay.desktop.media

import app.autplay.application.desktop.media.*

/** One fresh protected readback, held only by its owning request/source. */
internal class DesktopMediaCredential(val capture: SelectionCapture.VaultNetwork,
    val bearer: TransientMediaBearer, val accessExpiresAtMs: Long) : AutoCloseable {
    val credentialGeneration: Long get() = capture.network.credentialGeneration
    val sessionGeneration: Long get() = capture.network.sessionGeneration
    val streamPolicy: MediaTransportPolicy get() = capture.network.streamPolicy
    override fun close(): Unit = bearer.close()
    override fun toString(): String = "DesktopMediaCredential(<redacted>)"
}

/** Implemented by the K/J adapter, including durable exact-parent rotation and one replay budget. */
internal interface DesktopMediaCredentialOwner {
    suspend fun prepare(capture: SelectionCapture.VaultNetwork): SelectionCapture.VaultNetwork
    suspend fun access(capture: SelectionCapture.VaultNetwork): DesktopMediaCredential
    suspend fun successor(capture: SelectionCapture.VaultNetwork,
        rejected: Boolean, executionAttempt: Long): DesktopMediaCredential
    fun publicationIsCurrent(old: SelectionCapture.VaultNetwork, successor: SelectionCapture.VaultNetwork): Boolean
    fun forget(operation: MediaId): Unit
    fun rejectedReplayUsed(capture: SelectionCapture.VaultNetwork, executionAttempt: Long): Boolean
}
