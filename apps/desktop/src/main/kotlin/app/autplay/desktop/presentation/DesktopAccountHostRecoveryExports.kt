package app.autplay.desktop.presentation

import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import app.autplay.ui.desktop.*
import java.util.UUID

/** Each target is one exact retained protected result; no highest/orphan revision is selected. */
internal class DesktopAccountHostRecoveryExports(private val runtime: DesktopAccountRuntime,
    private val recovery: DesktopServerRecoveryController) {
    private class Target(val reference: SecureValueReference, val owner: SecureRecordOwner,
        val kind: DesktopServerRecoveryExportKind)
    private var targets: Map<String, Target> = emptyMap()
    var entries: List<DesktopServerRecoveryExport> = emptyList()
        private set
    fun retire(): Unit { targets = emptyMap(); entries = emptyList() }

    suspend fun refresh(bindingCommit: String?): Unit {
        val binding = runtime.binding?.takeIf { it.bindingCommit == bindingCommit && bindingCommit != null }
        if (binding == null) { retire(); return }
        val control = runtime.secure.snapshot().hostAwait()
        val next = mutableMapOf<String, Target>()
        val views = mutableListOf<DesktopServerRecoveryExport>()
        val kinds = mapOf(SecureRecordKind.RECOVERY_SOURCE to DesktopServerRecoveryExportKind.SourceConfiguration,
            SecureRecordKind.RECOVERY_RECIPIENT to DesktopServerRecoveryExportKind.RecipientRecovery,
            SecureRecordKind.DELETION_RECIPIENT to DesktopServerRecoveryExportKind.DeletionCancellation)
        for ((kind, display) in kinds) {
            val reference = control.selected.singleOrNull { it.slot == kind.reserved } ?: continue
            val record = runtime.secure.read(reference.slot, control).hostAwait() ?: changed()
            record.use {
                if (it.owner.stage != SecureStage.TERMINAL || it.owner.account != binding.account ||
                    it.owner.server != binding.server || it.owner.identityEpoch != binding.identityEpoch ||
                    it.owner.identityThumbprint != binding.identityThumbprint || it.owner.apiOrigin != binding.apiOrigin ||
                    it.owner.streamOrigin != binding.streamOrigin) return@use
                val pending = it.payload as? PendingPayload ?: changed()
                val document = pending.secretContextCopy().use { secrets -> secrets.recoveryDocumentCopy() }
                val present = try { document.isNotEmpty() } finally { document.fill(0) }
                if (!present) return@use
                val receipt = pending.receiptCopy()
                val generation = try { SecureJson.parse(receipt, 65_536, canonical = false).number("code_generation") }
                    finally { receipt.fill(0) }
                if (generation < 1) changed()
                val id = UUID.randomUUID().toString()
                next[id] = Target(reference, it.owner, display)
                views.add(DesktopServerRecoveryExport(id, display, generation))
            }
        }
        targets = next; entries = views
    }

    suspend fun document(targetId: String): SecretBytes {
        val target = targets[targetId] ?: changed()
        val control = runtime.secure.snapshot().hostAwait()
        if (control.selected.singleOrNull { it.slot == target.reference.slot } != target.reference) changed()
        val record = runtime.secure.read(target.reference.slot, control).hostAwait() ?: changed()
        val expected = record.use {
            if (it.owner != target.owner || it.owner.stage != SecureStage.TERMINAL) changed()
            (it.payload as? PendingPayload ?: changed()).secretContextCopy().use { secrets -> secrets.recoveryDocumentCopy() }
        }
        try {
            val document = recovery.documentForExplicitExport(target.kind == DesktopServerRecoveryExportKind.SourceConfiguration,
                target.kind == DesktopServerRecoveryExportKind.DeletionCancellation).hostAwait()
            try {
                val bytes = document.copy()
                try { if (expected.isEmpty() || !bytes.contentEquals(expected)) changed() } finally { bytes.fill(0) }
                val latest = runtime.secure.snapshot().hostAwait()
                if (latest.selected.singleOrNull { it.slot == target.reference.slot } != target.reference) changed()
                return document
            } catch (error: Exception) { document.close(); throw error }
        } finally { expected.fill(0) }
    }

    suspend fun acknowledge(targetId: String, readback: ByteArray): Unit {
        val target = targets[targetId] ?: changed()
        // Re-read the exact protected target before the controller's own full document comparison.
        document(targetId).use { secret ->
            val current = secret.copy()
            try { if (!current.contentEquals(readback)) changed() } finally { current.fill(0) }
        }
        recovery.acknowledgeExport(target.kind == DesktopServerRecoveryExportKind.SourceConfiguration, readback,
            target.kind == DesktopServerRecoveryExportKind.DeletionCancellation).hostAwait()
    }
    private fun changed(): Nothing = throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
}
