package app.autplay.desktop.presentation

import app.autplay.application.portableaccount.*
import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import java.time.Instant
import java.util.UUID

/** Only a comparison descriptor is retained between preview and explicit password confirmation. */
internal class DesktopAccountHostPortable(private val runtime: DesktopAccountRuntime,
    private val recovery: DesktopPortableRecoveryController) {
    private class Preview(val id: String, val reference: SecureValueReference,
        val selected: AccountSelectedServer?, val bindingCommit: String?, val expires: Instant)
    private var preview: Preview? = null
    val previewId: String? get() = preview?.takeIf { it.expires > Instant.now() }?.id
    fun retire(): Unit { preview = null }

    suspend fun prepare(): Unit {
        val control = runtime.secure.snapshot().hostAwait()
        if (control.firstBind != null || control.reservations.isNotEmpty()) conflict()
        val reference = control.selected.singleOrNull { it.slot == SecureRecordKind.PORTABLE_ROOT.reserved } ?: conflict()
        val stored = runtime.secure.read(reference.slot, control).hostAwait() ?: conflict()
        stored.use {
            val root = it.payload as? PortableRootPayload ?: conflict()
            if (root.everEnrolled && runtime.selected == null) throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
            preview = Preview(UUID.randomUUID().toString(), reference, runtime.selected,
                runtime.binding?.bindingCommit, Instant.now().plusSeconds(120))
        }
    }

    suspend fun rotate(id: String, password: CharArray, replacementPassword: CharArray): Unit {
        val frozen = preview?.takeIf { it.id == id && it.expires > Instant.now() &&
            it.selected === runtime.selected && it.bindingCommit == runtime.binding?.bindingCommit } ?: conflict()
        val control = runtime.secure.snapshot().hostAwait()
        if (control.selected.singleOrNull { it.slot == frozen.reference.slot } != frozen.reference ||
            control.firstBind != null || control.reservations.isNotEmpty()) conflict()
        val stored = runtime.secure.read(frozen.reference.slot, control).hostAwait() ?: conflict()
        stored.use {
            val root = it.payload as? PortableRootPayload ?: conflict()
            if (!root.everEnrolled && control.activeBinding == null && runtime.selected == null) {
                // Existing portable crypto owns the password/recovery format. No new key algorithm,
                // K record family, server proof or ancestor policy is introduced by presentation.
                val passport = root.passportCopy()
                val document = try { PortableAccountDocument.parse(passport) } finally { passport.fill(0) }
                val replacement = PortableAccountCrypto.unlock(document, password).use { unlocked ->
                    PortableAccountCrypto.rotate(unlocked, replacementPassword)
                }
                try {
                    runtime.command {
                        runtime.roots.rotateOffline(control,
                            runtime.localOwner(control, document.accountId, UUID.randomUUID().toString()), replacement).thenApply { Unit }
                    }.hostAwait()
                } finally { replacement.close() }
            } else {
                if (runtime.selected == null) throw AccountControlException(AccountControlFailure.AUTHENTICATION_REQUIRED)
                // Password confirmation is real ROOT authentication; the existing recovery owner
                // then retains the complete successor/archive and exact signed server rotation.
                runtime.roots.unlock(control, password).hostAwait().close()
                val archive = root.recoveryCopy()
                try { recovery.rotateOnSelectedServer(archive, replacementPassword).hostAwait() }
                finally { archive.fill(0) }
            }
        }
        preview = null
    }

    private fun conflict(): Nothing = throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
}
