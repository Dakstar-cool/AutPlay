package app.autplay.application.profilebinding

import app.autplay.application.profilepairing.PairingFlowSnapshot
import app.autplay.application.sync.ClientEventBinding
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.M5DeviceKeyStore
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.data.settings.NonSecretSettings
import app.autplay.data.settings.NonSecretSettingsStore
import app.autplay.domain.LocalId
import app.autplay.domain.ServerProfileId
import java.util.UUID
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** The explicit first-binding choice. Only [REVIEW_SELECTED] can create Journal facts. */
enum class LocalDataBindingDecision { KEEP_LOCAL, REVIEW_SELECTED }

/** Personal-data-free row shown by the bounded local-intent review surface. */
data class PendingLocalIntentSummary(
    val localChangeId: LocalId,
    val eventType: String,
    val occurredAtMs: Long,
)

/** Immutable consent evidence captured after the user reviews a bounded selection. */
data class M5MaterializationConsent(
    val snapshot: PairingFlowSnapshot,
    val sessionId: String,
    val decision: LocalDataBindingDecision,
    val selectedLocalChangeIds: List<LocalId>,
) {
    init {
        require(UUID.fromString(sessionId).toString() == sessionId)
        require(selectedLocalChangeIds.size <= MAX_SELECTION)
        require(selectedLocalChangeIds.distinct().size == selectedLocalChangeIds.size)
        if (decision == LocalDataBindingDecision.KEEP_LOCAL) {
            require(selectedLocalChangeIds.isEmpty())
        } else {
            require(selectedLocalChangeIds.isNotEmpty())
        }
    }

    companion object { const val MAX_SELECTION = 100 }
}

/** Persistence boundary that keeps the coordinator independent from Room details. */
interface M5LocalIntentMaterializer {
    suspend fun pending(limit: Int): List<PendingLocalIntentSummary>

    suspend fun materialize(
        binding: ClientEventBinding,
        localChangeId: LocalId,
        eventId: LocalId,
        materializedAtMs: Long,
    ): LocalId
}

enum class M5MaterializationErrorCode {
    MATERIALIZATION_BINDING_CHANGED,
    MATERIALIZATION_CREDENTIAL_MISSING,
    MATERIALIZATION_DEVICE_KEY_CHANGED,
}

/** Stable failure that deliberately carries no identity, origin, token, or payload. */
class M5MaterializationException(val code: M5MaterializationErrorCode) :
    IllegalStateException(code.name)

/**
 * Revalidates both sides of the M5 cross-store commit immediately before every Room transaction.
 * A partial batch is safe: completed items are immutable/idempotent and a changed binding stops the
 * next item without rewriting either the completed event or the remaining standalone intent.
 */
class M5BindingMaterializationCoordinator(
    private val settingsStore: NonSecretSettingsStore,
    private val credentialStore: CredentialStore,
    private val deviceKeys: M5DeviceKeyStore,
    private val materializer: M5LocalIntentMaterializer,
    private val eventIdFactory: () -> LocalId = { LocalId.random() },
) {
    private val mutex = Mutex()

    suspend fun review(limit: Int = M5MaterializationConsent.MAX_SELECTION): List<PendingLocalIntentSummary> {
        require(limit in 1..M5MaterializationConsent.MAX_SELECTION)
        return materializer.pending(limit)
    }

    suspend fun apply(consent: M5MaterializationConsent, nowMs: Long): List<LocalId> {
        require(nowMs >= 0)
        if (consent.decision == LocalDataBindingDecision.KEEP_LOCAL) return emptyList()
        return mutex.withLock {
            consent.selectedLocalChangeIds.map { localChangeId ->
                val binding = revalidate(consent)
                val eventId = eventIdFactory()
                require(eventId != localChangeId)
                materializer.materialize(binding, localChangeId, eventId, nowMs)
            }
        }
    }

    private suspend fun revalidate(consent: M5MaterializationConsent): ClientEventBinding {
        val snapshot = consent.snapshot
        val expectedUser = snapshot.expectedUserId ?: changed()
        val expectedDevice = snapshot.expectedDeviceId ?: changed()
        val expectedCommit = snapshot.bindingCommitId ?: changed()
        val expectedKeyThumbprint = snapshot.deviceKeyThumbprintSha256 ?: changed()
        val settings = settingsStore.settings.first()
        val checkpoint = settings.m5Binding ?: changed()

        if (!matches(settings, consent)) changed()
        if (
            checkpoint.bindingCommitId != expectedCommit ||
            checkpoint.serverInstanceId != snapshot.expectedServerInstanceId ||
            checkpoint.identityEpoch != snapshot.expectedIdentityEpoch ||
            checkpoint.identityThumbprintSha256 != snapshot.expectedIdentityThumbprintSha256 ||
            checkpoint.sessionId != consent.sessionId
        ) changed()

        val material = credentialStore.read(snapshot.serverProfileId) ?: missingCredential()
        try {
            val secret = runCatching { SessionCredentialEnvelopeCodec.decode(material) }
                .getOrElse { missingCredential() }
            if (
                secret.refreshPending ||
                secret.bindingCommitId != checkpoint.bindingCommitId ||
                secret.sessionId != checkpoint.sessionId ||
                secret.sessionFamilyId != checkpoint.sessionFamilyId ||
                secret.sessionGeneration != checkpoint.sessionGeneration
            ) missingCredential()
        } finally {
            material.fill(0)
        }

        val actualThumbprint = runCatching {
            deviceKeys.publicKeyThumbprintSha256(checkpoint.deviceKeyAlias)
        }.getOrElse { keyChanged() }
        if (actualThumbprint != expectedKeyThumbprint) keyChanged()

        return ClientEventBinding(expectedUser, expectedDevice, snapshot.serverProfileId)
    }

    private fun matches(settings: NonSecretSettings, consent: M5MaterializationConsent): Boolean {
        val snapshot = consent.snapshot
        return settings.activeServerProfileId == snapshot.serverProfileId &&
            settings.activeUserId == snapshot.expectedUserId &&
            settings.deviceId == snapshot.expectedDeviceId &&
            settings.serverBaseUrl == snapshot.apiOrigin &&
            settings.streamBaseUrl == snapshot.streamOrigin
    }

    private fun changed(): Nothing = throw M5MaterializationException(
        M5MaterializationErrorCode.MATERIALIZATION_BINDING_CHANGED,
    )

    private fun missingCredential(): Nothing = throw M5MaterializationException(
        M5MaterializationErrorCode.MATERIALIZATION_CREDENTIAL_MISSING,
    )

    private fun keyChanged(): Nothing = throw M5MaterializationException(
        M5MaterializationErrorCode.MATERIALIZATION_DEVICE_KEY_CHANGED,
    )
}
