package app.autplay.desktop.presentation

import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import app.autplay.ui.desktop.*
import java.time.Instant
import java.util.UUID
import kotlinx.serialization.json.*

/** A preview is a frozen comparison descriptor, never deletion authority or durable truth. */
internal class DesktopAccountHostDeletion(private val runtime: DesktopAccountRuntime,
    private val recovery: DesktopServerRecoveryController) {
    private class Preview(val id: String, val review: DesktopDeletionReview) {
        val expires: Instant get() = review.observedAt.plusSeconds(120)
    }
    private var preview: Preview? = null
    var view: DesktopDeletionView? = null
        private set

    fun retire(): Unit { preview = null; view = null }

    suspend fun preview(): Unit {
        val review = recovery.reviewDeletion().hostAwait()
        if (!review.allowed) throw AccountControlException(AccountControlFailure.FORBIDDEN)
        val frozen = Preview(UUID.randomUUID().toString(), review)
        preview = frozen
        view = DesktopDeletionView(frozen.id, DesktopDeletionStage.Preview, DesktopDeletionRole.Source,
            runtime.state.value.publicId ?: requireNotNull(review.owner.account), null, false)
    }

    fun permitsRequest(target: String): Boolean = preview?.let { it.id == target && it.expires > Instant.now() &&
        it.review.owner == runtime.binding } == true

    suspend fun request(target: String, recoveryCode: ByteArray): Unit {
        val current = preview?.takeIf { permitsRequest(target) } ?: fail()
        recovery.requestDeletion(current.review, true, recoveryCode).hostAwait()
        preview = null
        refreshRetained()
    }

    suspend fun refreshRetained(): Unit {
        if (preview?.expires?.isAfter(Instant.now()) == true) return
        preview = null
        val snapshot = runtime.secure.snapshot().hostAwait()
        val record = runtime.secure.read(requireNotNull(SecureRecordKind.DELETION_SOURCE.reserved), snapshot).hostAwait() ?: return
        record.use {
            val payload = it.payload as? PendingPayload ?: fail()
            val receipt = payload.receiptCopy()
            try {
                if (receipt.isEmpty()) {
                    view = DesktopDeletionView(requireNotNull(it.owner.operation), DesktopDeletionStage.Unknown,
                        DesktopDeletionRole.Source, runtime.state.value.publicId ?: requireNotNull(it.owner.account), null, false)
                    return
                }
                val value = SecureJson.parse(receipt, 65_536, canonical = false)
                if (value.string("account_id") != it.owner.account || value.string("deletion_request_id") != it.owner.operation) fail()
                val stage = when (value.string("state")) {
                    "PENDING" -> DesktopDeletionStage.Pending
                    "CANCELLED", "NOT_ACCEPTED" -> DesktopDeletionStage.Cancelled
                    else -> DesktopDeletionStage.Unknown
                }
                val deadline = (value["cancel_before"] as? JsonPrimitive)?.takeIf { p -> p.isString }?.content
                val canCancel = stage == DesktopDeletionStage.Pending && deadline?.let(Instant::parse)?.isAfter(Instant.now()) == true
                view = DesktopDeletionView(requireNotNull(it.owner.operation), stage, DesktopDeletionRole.Source,
                    runtime.state.value.publicId ?: requireNotNull(it.owner.account), deadline, canCancel)
            } finally { receipt.fill(0) }
        }
    }

    private fun fail(): Nothing = throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
}
