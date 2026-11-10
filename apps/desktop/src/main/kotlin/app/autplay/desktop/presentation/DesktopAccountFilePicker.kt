package app.autplay.desktop.presentation

import app.autplay.desktop.account.AccountControlException
import app.autplay.desktop.account.AccountControlFailure
import app.autplay.desktop.account.AccountRecoveryDestination
import app.autplay.desktop.account.AccountRecoverySource
import java.awt.Dialog
import java.awt.EventQueue
import java.awt.FileDialog
import java.awt.Frame
import java.awt.Window
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/** One explicit native selection at a time. No path, bookmark or chooser directory is retained. */
public class DesktopAccountFilePicker(
    private val owner: () -> Window?,
    private val canChoose: () -> Boolean,
    private val russian: () -> Boolean,
) : DesktopAccountRecoveryPicker, AutoCloseable {
    private val gate = Any()
    private val closed = AtomicBoolean(false)
    private val busy = AtomicBoolean(false)
    private val visible = AtomicReference<FileDialog?>(null)
    private val retained = java.util.concurrent.ConcurrentHashMap.newKeySet<CompletableFuture<Unit>>()

    override fun chooseRecoveryDestination(): CompletionStage<AccountRecoveryDestination?> =
        choose(save = true) { AccountRecoveryDestination.fromExplicitSaveSelection(it) }

    override fun chooseRecoverySource(): CompletionStage<AccountRecoverySource?> =
        choose(save = false) { AccountRecoverySource.fromExplicitOpenSelection(it) }

    private fun <T> choose(save: Boolean, selected: (Path) -> T): CompletionStage<T?> {
        val result = CompletableFuture<T?>()
        val finished = CompletableFuture<Unit>()
        synchronized(gate) {
            if (closed.get() || !canChoose()) {
                result.completeExceptionally(AccountControlException(AccountControlFailure.CONTEXT_CHANGED))
                return result.minimalCompletionStage()
            }
            if (!busy.compareAndSet(false, true)) {
                result.completeExceptionally(AccountControlException(AccountControlFailure.BUSY))
                return result.minimalCompletionStage()
            }
            retained.add(finished)
        }
        try {
            EventQueue.invokeLater {
                var dialog: FileDialog? = null
                try {
                    if (closed.get() || !canChoose()) throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                    val window = owner()
                    if (window == null || !window.isDisplayable) throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                    val title = if (russian()) {
                        if (save) "Сохранить файл восстановления" else "Выбрать файл"
                    } else {
                        if (save) "Save recovery file" else "Choose file"
                    }
                    val mode = if (save) FileDialog.SAVE else FileDialog.LOAD
                    dialog = when (window) {
                        is Frame -> FileDialog(window, title, mode)
                        is Dialog -> FileDialog(window, title, mode)
                        else -> throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                    }
                    dialog.isMultipleMode = false
                    if (save) dialog.file = "AutPlay-recovery.json"
                    visible.set(dialog)
                    if (closed.get() || !canChoose()) throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                    dialog.isVisible = true
                    if (closed.get() || !canChoose()) throw AccountControlException(AccountControlFailure.CONTEXT_CHANGED)
                    val filename = dialog.file
                    val directory = dialog.directory
                    if (filename == null || directory == null) result.complete(null)
                    else result.complete(selected(Path.of(directory).resolve(filename).toAbsolutePath().normalize()))
                } catch (_: Exception) {
                    result.completeExceptionally(AccountControlException(AccountControlFailure.CONTEXT_CHANGED))
                } finally {
                    visible.compareAndSet(dialog, null)
                    try { dialog?.dispose() }
                    finally { busy.set(false); finished.complete(Unit); retained.remove(finished) }
                }
            }
        } catch (_: Exception) {
            busy.set(false)
            result.completeExceptionally(AccountControlException(AccountControlFailure.CLOSED))
            finished.complete(Unit); retained.remove(finished)
        }
        return result.minimalCompletionStage()
    }

    /** The native modal loop returns before its operation settles; an old selection is rejected. */
    override fun close(): Unit {
        synchronized(gate) {
            if (closed.compareAndSet(false, true)) EventQueue.invokeLater { visible.get()?.dispose() }
        }
    }

    /** Close first. Result/selection cancellation does not prove the modal callback has exited. */
    public fun drain(): CompletionStage<Unit> {
        check(closed.get()) { "ACCOUNT_PICKER_NOT_RETIRED" }
        return CompletableFuture.allOf(*retained.toTypedArray()).thenApply { Unit }
    }
}
