package app.autplay.desktop.settings

import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopPreferencesStore
import app.autplay.application.desktop.DesktopSettingsTransferState
import app.autplay.data.desktop.DesktopSettingsException
import app.autplay.data.desktop.DesktopSettingsTransferCodec
import java.awt.Component
import java.awt.EventQueue
import java.nio.file.Path
import java.util.UUID
import javax.swing.JFileChooser
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/** Owns settings only; library, native playback and the caller's scope have independent lifetimes. */
public class DesktopSettingsController(
    private val store: DesktopPreferencesStore,
    private val scope: CoroutineScope,
    private val applyMeteredPolicy: suspend (Boolean) -> Unit = {},
    private val meteredPreferenceCommitted: (Boolean) -> Unit = {},
) {
    public val preferences: StateFlow<DesktopPreferencesState> = store.state
    private val mutableTransfer: MutableStateFlow<DesktopSettingsTransferState> = MutableStateFlow(DesktopSettingsTransferState())
    public val transfer: StateFlow<DesktopSettingsTransferState> = mutableTransfer.asStateFlow()
    private val lifecycleLock: Any = Any()
    private val closeMutex: Mutex = Mutex()
    private val commands: Channel<Mutation> = Channel(32)
    private var closed: Boolean = false
    private var closeResult: Result<Unit>? = null
    private var generation: Long = 0
    private var transferJob: Job? = null
    private var mutationJob: Job? = null
    private var preview: Preview? = null
    private var confirmation: Preview? = null
    private var activeConfirmation: Preview? = null
    private val meteredReplies: MutableSet<CompletableDeferred<Unit>> = LinkedHashSet()
    private val mutationOwner: Job = scope.launch(Dispatchers.IO) {
        try { for (command in commands) execute(command) }
        finally {
            // The actor's active child has settled before queued/active callers are released.
            synchronized(lifecycleLock) {
                meteredReplies.forEach { it.completeExceptionally(IllegalStateException("desktop_settings_closed")) }
                meteredReplies.clear()
            }
        }
    }

    public fun setAppLanguage(value: String): Unit = enqueue(Mutation.Language(value))
    public fun setAppearanceMode(value: String): Unit = enqueue(Mutation.Appearance(value))
    public fun setAccentPalette(value: String): Unit = enqueue(Mutation.Accent(value))
    public fun setSmoothTrackTransitions(value: Boolean): Unit = enqueue(Mutation.SmoothTransitions(value))

    /** Returns only after the retained actor finishes the real settings write and policy request. */
    public suspend fun setDownloadOnMeteredNetwork(value: Boolean): Unit {
        val reply = CompletableDeferred<Unit>()
        synchronized(lifecycleLock) {
            check(!closed && mutationOwner.isActive) { "desktop_settings_closed" }
            check(store.state.value.writable) { "desktop_settings_read_only" }
            check(!busy()) { "desktop_settings_busy" }
            meteredReplies.add(reply)
            if (!commands.trySend(Mutation.Metered(value, reply)).isSuccess) {
                meteredReplies.remove(reply)
                error("desktop_settings_busy")
                throw IllegalStateException("desktop_settings_busy")
            }
        }
        // Caller cancellation does not detach an already admitted persistent effect.
        withContext(NonCancellable) { reply.await() }
    }

    public fun importSettings(owner: Component?): Unit = beginTransfer(importing = true) { chooseSettingsFile(owner, exporting = false) }
    public fun exportSettings(owner: Component?): Unit = beginTransfer(importing = false) { chooseSettingsFile(owner, exporting = true) }

    /** These capabilities must come from an explicit file selection; useful without opening AWT. */
    public fun importSelectedSettings(path: Path): Unit = beginTransfer(importing = true) { path }
    public fun exportSelectedSettings(path: Path): Unit = beginTransfer(importing = false) { path }

    public fun confirmImport(previewId: String): Unit {
        synchronized(lifecycleLock) {
            if (closed) return
            val picked = preview ?: return
            if (picked.id != previewId || picked.generation != generation || busy()) return
            if (!store.state.value.writable) { error("desktop_settings_read_only"); return }
            confirmation = picked
            if (!commands.trySend(Mutation.Apply(picked)).isSuccess) {
                confirmation = null
                error("desktop_settings_busy")
            } else mutableTransfer.value = mutableTransfer.value.copy(busy = true, errorCode = null, messageCode = null)
        }
    }

    public fun dismissImport(previewId: String): Unit {
        synchronized(lifecycleLock) {
            if (closed || preview?.id != previewId || busy()) return
            generation++
            preview = null
            mutableTransfer.value = DesktopSettingsTransferState()
        }
    }

    /** Immediately fences queued confirmations. Active IO/chooser cleanup keeps admission busy. */
    public fun cancelTransfer(): Unit {
        synchronized(lifecycleLock) {
            if (closed) return
            invalidateTransfer()
        }
    }

    /** Nonblocking host shutdown barrier, before the host begins other suspendable cleanup. */
    public fun beginClose(): Unit {
        synchronized(lifecycleLock) {
            if (closed) return
            closed = true
            invalidateTransfer()
            commands.cancel()
            mutationJob?.cancel()
            mutationOwner.cancel()
        }
    }

    public suspend fun close(): Unit = withContext(NonCancellable) {
        closeMutex.withLock {
            closeResult?.let { it.getOrThrow(); return@withLock }
            beginClose()
            val selectedOwner = synchronized(lifecycleLock) { transferJob }
            val result = runCatching {
                // Cancellation does not settle a chooser, native/file operation or actor child.
                selectedOwner?.join()
                mutationOwner.join()
                store.close()
            }
            closeResult = result
            result.getOrThrow()
        }
    }

    private fun enqueue(command: Mutation): Unit {
        synchronized(lifecycleLock) {
            if (closed) return
            if (!store.state.value.writable) { error("desktop_settings_read_only"); return }
            if (busy() || !commands.trySend(command).isSuccess) error("desktop_settings_busy")
        }
    }

    private suspend fun execute(command: Mutation): Unit = coroutineScope {
        var saved = false
        var failureCode: String? = null
        val admitted = synchronized(lifecycleLock) {
            if (closed) {
                finishMetered(command, "desktop_settings_closed")
                return@coroutineScope
            }
            if (command is Mutation.Apply && (confirmation !== command.preview || preview !== command.preview ||
                    command.preview.generation != generation)) return@coroutineScope
            if (!store.state.value.writable) {
                if (command is Mutation.Apply) confirmation = null
                error("desktop_settings_read_only")
                finishMetered(command, "desktop_settings_read_only")
                return@coroutineScope
            }
            val child = launch(start = CoroutineStart.LAZY) {
                try {
                    currentCoroutineContext().ensureActive()
                    val desiredMetered = when (command) {
                        is Mutation.Metered -> command.value
                        is Mutation.Apply -> command.preview.value.downloadOnMeteredNetwork
                        else -> null
                    }
                    // A restriction reaches the current native policy before its saved value.
                    // Expansion is sent only after the real retained settings write succeeds.
                    if (desiredMetered == false) applyMeteredPolicy(false)
                    when (command) {
                        is Mutation.Language -> store.setAppLanguage(command.value)
                        is Mutation.Appearance -> store.setAppearanceMode(command.value)
                        is Mutation.Accent -> store.setAccentPalette(command.value)
                        is Mutation.SmoothTransitions -> store.setSmoothTrackTransitions(command.value)
                        is Mutation.Metered -> store.setDownloadOnMeteredNetwork(command.value)
                        is Mutation.Apply -> store.applyPortable(command.preview.value)
                    }
                    if (desiredMetered != null) {
                        val actual = store.state.value.value.downloadOnMeteredNetwork
                        check(actual == desiredMetered) { "desktop_settings_save_failed" }
                        applyMeteredPolicy(actual)
                        meteredPreferenceCommitted(actual)
                    }
                    currentCoroutineContext().ensureActive()
                    saved = true
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    failureCode = safeSettingsFailure(failure)
                }
            }
            mutationJob = child
            if (command is Mutation.Apply) activeConfirmation = command.preview
            child.invokeOnCompletion {
                synchronized(lifecycleLock) {
                    if (mutationJob === child) mutationJob = null
                    if (command is Mutation.Apply) {
                        if (activeConfirmation === command.preview) activeConfirmation = null
                        if (confirmation === command.preview) confirmation = null
                        if (!closed && preview === command.preview && generation == command.preview.generation) {
                            if (saved) preview = null
                            mutableTransfer.value = mutableTransfer.value.copy(
                                previewId = preview?.id, preview = preview?.value,
                                errorCode = failureCode, messageCode = if (saved) "desktop_settings_saved" else null,
                            )
                        }
                    } else if (!closed && (saved || failureCode != null)) {
                        mutableTransfer.value = mutableTransfer.value.copy(
                            errorCode = failureCode, messageCode = if (saved) "desktop_settings_saved" else null,
                        )
                    }
                    mutableTransfer.value = mutableTransfer.value.copy(busy = busy())
                }
            }
            child
        }
        admitted.start()
        admitted.join()
        synchronized(lifecycleLock) {
            finishMetered(command, if (saved) null else failureCode ?: "desktop_settings_save_failed")
        }
    }

    private fun finishMetered(command: Mutation, failure: String?): Unit {
        if (command !is Mutation.Metered) return
        meteredReplies.remove(command.reply)
        if (failure == null) command.reply.complete(Unit)
        else command.reply.completeExceptionally(IllegalStateException(failure))
    }

    private fun beginTransfer(importing: Boolean, select: suspend () -> Path?): Unit {
        val admitted = synchronized(lifecycleLock) {
            if (closed) return
            // A second import/export cannot silently replace the pending preview.
            if (busy() || preview != null) { error("desktop_settings_busy"); return }
            if (importing && !store.state.value.writable) { error("desktop_settings_read_only"); return }
            val ticket = ++generation
            var decoded: DesktopPreferences? = null
            var exported = false
            var cancelled = false
            var failureCode: String? = null
            val child = scope.launch(Dispatchers.IO, start = CoroutineStart.LAZY) {
                try {
                    val picked = select()
                    currentCoroutineContext().ensureActive()
                    if (picked == null) cancelled = true
                    else if (importing) {
                        val bytes = runInterruptible { readSettingsImport(picked) }
                        currentCoroutineContext().ensureActive()
                        decoded = DesktopSettingsTransferCodec.decode(bytes, store.state.value.value)
                    } else {
                        // Capture confirmed preferences exactly once AFTER selection returns.
                        val bytes = DesktopSettingsTransferCodec.encode(store.state.value.value)
                        currentCoroutineContext().ensureActive()
                        runInterruptible { writeSettingsExportNew(picked, bytes) }
                        currentCoroutineContext().ensureActive()
                        exported = true
                    }
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    failureCode = if (!importing && failure is DesktopSettingsFileException &&
                        failure.code == "desktop_settings_export_target_exists") failure.code
                    else if (importing) "desktop_settings_import_failed" else "desktop_settings_export_failed"
                }
            }
            transferJob = child
            mutableTransfer.value = DesktopSettingsTransferState(busy = true)
            child.invokeOnCompletion { failure ->
                synchronized(lifecycleLock) {
                    if (transferJob === child) {
                        transferJob = null
                        if (!closed && ticket == generation) {
                            val wasCancelled = cancelled || failure is CancellationException
                            val frozen = decoded
                            if (!wasCancelled && frozen != null) preview = Preview(UUID.randomUUID().toString(), ticket, frozen)
                            mutableTransfer.value = DesktopSettingsTransferState(
                                previewId = preview?.id, preview = preview?.value,
                                errorCode = if (wasCancelled) null else failureCode,
                                messageCode = when {
                                    wasCancelled -> "desktop_settings_cancelled"
                                    exported -> "desktop_settings_exported"
                                    else -> null
                                },
                            )
                        }
                        mutableTransfer.value = mutableTransfer.value.copy(busy = busy())
                    }
                }
            }
            child
        }
        admitted.start()
    }

    private fun invalidateTransfer(): Unit {
        generation++
        preview = null
        confirmation = null
        transferJob?.cancel()
        if (activeConfirmation != null) mutationJob?.cancel()
        mutableTransfer.value = DesktopSettingsTransferState(busy = busy(), messageCode = "desktop_settings_cancelled")
    }

    private fun busy(): Boolean = transferJob != null || confirmation != null || activeConfirmation != null
    private fun error(code: String): Unit {
        mutableTransfer.value = mutableTransfer.value.copy(busy = busy(), errorCode = code, messageCode = null)
    }

    private fun safeSettingsFailure(failure: Exception): String = when (val code = (failure as? DesktopSettingsException)?.code) {
        "desktop_settings_reopen_required", "desktop_settings_atomic_unavailable", "desktop_settings_write_failed",
        "desktop_settings_file_changed", "desktop_settings_file_unsafe", "desktop_settings_refresh_failed",
        "desktop_settings_temp_cleanup_failed", "desktop_settings_read_only", "desktop_settings_closed",
        "desktop_settings_in_use", "desktop_settings_unavailable", "desktop_settings_read_failed",
        "desktop_settings_too_large", "desktop_settings_structure_limit", "desktop_settings_language_invalid",
        "desktop_settings_appearance_invalid", "desktop_settings_palette_invalid", "desktop_settings_prefetch_invalid",
        "desktop_settings_value_invalid" -> code
        else -> "desktop_settings_save_failed"
    }

    private data class Preview(val id: String, val generation: Long, val value: DesktopPreferences)
    private sealed interface Mutation {
        data class Language(val value: String) : Mutation
        data class Appearance(val value: String) : Mutation
        data class Accent(val value: String) : Mutation
        data class SmoothTransitions(val value: Boolean) : Mutation
        data class Metered(val value: Boolean, val reply: CompletableDeferred<Unit>) : Mutation
        data class Apply(val preview: Preview) : Mutation
    }
}

private suspend fun chooseSettingsFile(owner: Component?, exporting: Boolean): Path? {
    val finished = CompletableDeferred<Unit>()
    try {
        return suspendCancellableCoroutine { continuation ->
            val showChooser = Runnable {
                try {
                    if (!continuation.isActive) return@Runnable
                    val chooser = JFileChooser().apply {
                        isMultiSelectionEnabled = false
                        fileSelectionMode = JFileChooser.FILES_ONLY
                        dialogTitle = if (exporting) "AutPlay settings export" else "AutPlay settings import"
                    }
                    continuation.invokeOnCancellation { EventQueue.invokeLater { chooser.cancelSelection() } }
                    if (!continuation.isActive) return@Runnable
                    val result = if (exporting) chooser.showSaveDialog(owner) else chooser.showOpenDialog(owner)
                    val picked = if (result == JFileChooser.APPROVE_OPTION) chooser.selectedFile?.toPath() else null
                    if (continuation.isActive) continuation.resume(picked)
                } catch (failure: Exception) {
                    if (continuation.isActive) continuation.resumeWith(Result.failure(failure))
                } finally {
                    finished.complete(Unit)
                }
            }
            try { EventQueue.invokeLater(showChooser) }
            catch (failure: Exception) {
                finished.complete(Unit)
                if (continuation.isActive) continuation.resumeWith(Result.failure(failure))
            }
        }
    } finally {
        // Cancellation does not admit another dialog while this AWT call still owns one.
        withContext(NonCancellable) { finished.await() }
    }
}
