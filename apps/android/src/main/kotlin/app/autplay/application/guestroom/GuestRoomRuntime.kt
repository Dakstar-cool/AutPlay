package app.autplay.application.guestroom

import app.autplay.application.wave.WaveCoordinator
import app.autplay.application.wave.WaveTransport
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.GuestRoomProjectionEntity
import java.time.Instant
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class GuestRoomStage { IDLE, DOCUMENT_READY, REDEEMING, ACTIVE, TERMINAL, ERROR }

/** Secret-free state safe for Compose and diagnostics. */
data class GuestRoomRuntimeState(
    val stage: GuestRoomStage = GuestRoomStage.IDLE,
    val roomId: String? = null,
    val serverInstanceId: String? = null,
    val identityEpoch: Long? = null,
    val displayName: String? = null,
    val expiresAtMs: Long? = null,
    val errorCode: String? = null,
)

/**
 * Process-scoped S1D owner. It is deliberately impossible to reconstruct authority from Room;
 * process death retires only the sanitized projection and requires another invitation document.
 */
class GuestRoomRuntime(
    private val database: AutPlayDatabase,
    private val coordinatorFactory: (WaveTransport, String) -> WaveCoordinator,
    private val localMediaProfileResolver: suspend (GuestRoomDocument) -> String? = { null },
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
) {
    private val mutableState = MutableStateFlow(GuestRoomRuntimeState())
    val state: StateFlow<GuestRoomRuntimeState> = mutableState.asStateFlow()
    private val processStart = scope.async {
        database.guestRoomDao().retireProcessLostAuthority(System.currentTimeMillis())
        database.guestRoomDao().deleteTerminalBefore(
            System.currentTimeMillis() - TERMINAL_RETENTION_MS,
        )
    }
    private var document: GuestRoomDocument? = null
    private var transport: OkHttpGuestWaveTransport? = null
    private var coordinator: WaveCoordinator? = null
    private var activeSessionId: String? = null
    private var authorityGeneration = 0L

    @Synchronized
    fun acceptDocument(
        raw: String,
        now: Instant = Instant.now(),
        allowUnsafeDevelopmentHttp: Boolean = false,
    ) {
        val replacedSessionId = activeSessionId
        authorityGeneration++
        clearProcessSecrets()
        replacedSessionId?.let { sessionId ->
            scope.launch {
                database.guestRoomDao().markState(
                    sessionId,
                    "REPLACED",
                    System.currentTimeMillis(),
                )
            }
        }
        val parsed = runCatching {
            GuestRoomDocumentCodec.decode(raw, now, allowUnsafeDevelopmentHttp)
        }.getOrElse {
            mutableState.value = GuestRoomRuntimeState(
                stage = GuestRoomStage.ERROR,
                errorCode = "guest_document_invalid",
            )
            return
        }
        document = parsed
        mutableState.value = GuestRoomRuntimeState(
            stage = GuestRoomStage.DOCUMENT_READY,
            roomId = parsed.roomId,
            serverInstanceId = parsed.serverInstanceId,
            identityEpoch = parsed.identityEpoch,
            expiresAtMs = parsed.expiresAt.toEpochMilli(),
        )
    }

    suspend fun redeem(displayName: String) {
        processStart.await()
        val (pending, operationGeneration) = synchronized(this) {
            val current = document ?: throw IllegalStateException("GUEST_DOCUMENT_REQUIRED")
            mutableState.value = mutableState.value.copy(
                stage = GuestRoomStage.REDEEMING,
                errorCode = null,
            )
            current to authorityGeneration
        }
        runCatching {
            withContext(Dispatchers.IO) {
                val localMediaProfileId = localMediaProfileResolver(pending)
                val (capability, transport) = OkHttpGuestWaveTransport.redeem(
                    pending,
                    displayName,
                    localMediaProfileId = localMediaProfileId,
                    onAuthorityLost = { code ->
                        scope.launch {
                            terminalIfCurrent(
                                operationGeneration,
                                "REMOTE_TERMINATED",
                                code,
                            )
                        }
                    },
                )
                Triple(capability, transport, localMediaProfileId)
            }
        }.onSuccess { (capability, newTransport, localMediaProfileId) ->
            val installed = synchronized(this) {
                if (!isCurrentGuestRedemption(
                        authorityGeneration,
                        operationGeneration,
                        document,
                        pending,
                    )
                ) {
                    false
                } else {
                    document = null
                    pending.close()
                    transport = newTransport
                    activeSessionId = capability.guestSessionId
                    true
                }
            }
            if (!installed) {
                newTransport.close()
                return@onSuccess
            }
            database.guestRoomDao().upsert(
                GuestRoomProjectionEntity(
                    guestSessionId = capability.guestSessionId,
                    invitationId = capability.invitationId,
                    roomId = capability.roomId,
                    serverInstanceId = pending.serverInstanceId,
                    identityEpoch = pending.identityEpoch,
                    localMediaProfileId = localMediaProfileId,
                    roomEpoch = capability.roomEpoch,
                    queueVersion = 0,
                    roomState = "OPEN",
                    displayName = capability.displayName,
                    state = "ACTIVE",
                    expiresAtMs = capability.expiresAt.toEpochMilli(),
                    lastSequence = 0,
                    updatedAtMs = System.currentTimeMillis(),
                ),
            )
            if (!isCurrentCapability(operationGeneration, capability.guestSessionId)) {
                newTransport.close()
                database.guestRoomDao().markState(
                    capability.guestSessionId,
                    "STALE_OPERATION",
                    System.currentTimeMillis(),
                )
                return@onSuccess
            }
            val newCoordinator = coordinatorFactory(newTransport, capability.guestSessionId)
            val coordinatorInstalled = synchronized(this) {
                if (isCurrentCapabilityLocked(operationGeneration, capability.guestSessionId)) {
                    coordinator = newCoordinator
                    true
                } else {
                    false
                }
            }
            if (!coordinatorInstalled) {
                newCoordinator.close()
                database.guestRoomDao().markState(
                    capability.guestSessionId,
                    "STALE_OPERATION",
                    System.currentTimeMillis(),
                )
                return@onSuccess
            }
            val joinError = runCatching { newCoordinator.join(capability.roomId) }.exceptionOrNull()
            if (joinError != null) {
                terminalIfCurrent(
                    operationGeneration,
                    "JOIN_FAILED",
                    "guest_unavailable",
                    capability.guestSessionId,
                )
                return@onSuccess
            }
            val activated = synchronized(this) {
                if (
                    isCurrentCapabilityLocked(operationGeneration, capability.guestSessionId) &&
                    coordinator === newCoordinator
                ) {
                    mutableState.value = GuestRoomRuntimeState(
                        stage = GuestRoomStage.ACTIVE,
                        roomId = capability.roomId,
                        serverInstanceId = pending.serverInstanceId,
                        identityEpoch = pending.identityEpoch,
                        displayName = capability.displayName,
                        expiresAtMs = capability.expiresAt.toEpochMilli(),
                    )
                    true
                } else {
                    false
                }
            }
            if (!activated) {
                newCoordinator.close()
                database.guestRoomDao().markState(
                    capability.guestSessionId,
                    "STALE_OPERATION",
                    System.currentTimeMillis(),
                )
                return@onSuccess
            }
            scope.launch {
                val waitMs = capability.expiresAt.toEpochMilli() - System.currentTimeMillis()
                if (waitMs > 0) delay(waitMs)
                terminalIfCurrent(
                    operationGeneration,
                    "EXPIRED",
                    "guest_expired",
                    capability.guestSessionId,
                )
            }
        }.onFailure { error ->
            if (synchronized(this) { authorityGeneration != operationGeneration }) {
                return@onFailure
            }
            if (mutableState.value.stage != GuestRoomStage.TERMINAL) {
                val hasCapability = synchronized(this) { transport != null }
                val code = (error as? GuestRoomTransportException)?.code
                    ?: "guest_unavailable"
                if (hasCapability || code in TERMINAL_REDEMPTION_ERRORS) {
                    terminalIfCurrent(operationGeneration, "JOIN_FAILED", code)
                } else {
                    mutableState.value = mutableState.value.copy(
                        stage = GuestRoomStage.ERROR,
                        errorCode = code,
                    )
                }
            }
        }
    }

    @Synchronized
    fun activeCoordinator(): WaveCoordinator? = coordinator

    suspend fun leave() {
        val (active, operationGeneration, sessionId, roomId) = synchronized(this) {
            val current = coordinator ?: throw IllegalStateException("GUEST_SESSION_REQUIRED")
            val currentSession = activeSessionId
                ?: throw IllegalStateException("GUEST_SESSION_REQUIRED")
            val currentRoom = state.value.roomId
                ?: throw IllegalStateException("GUEST_ROOM_REQUIRED")
            LeaveOperation(current, authorityGeneration, currentSession, currentRoom)
        }
        runCatching { active.leave() }
        terminalIfCurrent(operationGeneration, "LEFT", null, sessionId)
    }

    suspend fun cancel() {
        val cancelledSessionId = synchronized(this) {
            authorityGeneration++
            val currentSessionId = activeSessionId
            clearProcessSecrets()
            mutableState.value = GuestRoomRuntimeState()
            currentSessionId
        }
        cancelledSessionId?.let {
            database.guestRoomDao().markState(it, "CANCELLED", System.currentTimeMillis())
        }
    }

    private suspend fun terminalIfCurrent(
        expectedGeneration: Long,
        stateCode: String,
        errorCode: String?,
        expectedSessionId: String? = null,
    ) {
        val terminalSessionId = synchronized(this) {
            if (
                authorityGeneration != expectedGeneration ||
                (expectedSessionId != null && activeSessionId != expectedSessionId)
            ) {
                return
            }
            authorityGeneration++
            val currentSessionId = activeSessionId
            clearProcessSecrets()
            mutableState.value = GuestRoomRuntimeState(
                stage = GuestRoomStage.TERMINAL,
                errorCode = errorCode,
            )
            currentSessionId
        }
        terminalSessionId?.let {
            database.guestRoomDao().markState(it, stateCode, System.currentTimeMillis())
        }
    }

    @Synchronized
    private fun isCurrentCapability(expectedGeneration: Long, expectedSessionId: String): Boolean =
        isCurrentCapabilityLocked(expectedGeneration, expectedSessionId)

    private fun isCurrentCapabilityLocked(
        expectedGeneration: Long,
        expectedSessionId: String,
    ): Boolean = authorityGeneration == expectedGeneration && activeSessionId == expectedSessionId

    @Synchronized
    private fun clearProcessSecrets() {
        coordinator?.close()
        coordinator = null
        transport?.close()
        transport = null
        activeSessionId = null
        document?.close()
        document = null
    }

    private companion object {
        const val TERMINAL_RETENTION_MS = 30L * 24 * 60 * 60 * 1_000
        val TERMINAL_REDEMPTION_ERRORS = setOf(
            "guest_expired",
            "guest_revoked",
            "guest_unavailable",
            "room_changed",
        )
    }

    private data class LeaveOperation(
        val coordinator: WaveCoordinator,
        val generation: Long,
        val sessionId: String,
        val roomId: String,
    )
}

internal fun isCurrentGuestRedemption(
    currentGeneration: Long,
    operationGeneration: Long,
    currentDocument: GuestRoomDocument?,
    operationDocument: GuestRoomDocument,
): Boolean = currentGeneration == operationGeneration && currentDocument === operationDocument
