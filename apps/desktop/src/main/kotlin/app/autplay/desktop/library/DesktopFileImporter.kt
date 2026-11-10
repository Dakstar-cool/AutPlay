package app.autplay.desktop.library

import app.autplay.application.desktop.DesktopImportCandidateMapping
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobAttempt
import app.autplay.application.desktop.DesktopImportJobCandidate
import app.autplay.application.desktop.DesktopImportJobFailure
import app.autplay.application.desktop.DesktopImportJobManifest
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPermit
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportJobReselection
import app.autplay.application.desktop.DesktopImportJobSettlement
import app.autplay.application.desktop.DesktopImportJobStop
import app.autplay.application.desktop.DesktopImportJobStopReason
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportPreviewFile
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportReviewPort
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopLibraryStore
import app.autplay.application.desktop.DesktopLocalLimits
import app.autplay.application.sourceaccess.SourceImportBatch
import app.autplay.application.sourceaccess.SourceImportFile
import app.autplay.application.sourceaccess.SourceAccessFailure
import app.autplay.data.desktop.DesktopManagedMedia
import app.autplay.data.desktop.DesktopPcmWaveDurationProbe
import app.autplay.data.desktop.DesktopStorageException
import java.io.IOException
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.FileAlreadyExistsException
import java.nio.file.FileVisitResult
import java.nio.file.Files
import java.nio.file.InvalidPathException
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.nio.file.SimpleFileVisitor
import java.nio.file.StandardCopyOption.ATOMIC_MOVE
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.FileTime
import java.nio.file.attribute.PosixFilePermissions
import java.security.MessageDigest
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.runInterruptible
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Imports only explicit file/folder selections. The original is never written or removed.
 * One active-time budget covers preparation and confirmed copy verification, excluding review.
 * The store factory owns the process lock; publication is serialized across importer instances
 * inside that process. The owned data directory must not be writable by untrusted processes.
 */
class DesktopFileImporter(
    private val store: DesktopLibraryStore,
    dataRoot: Path,
) : DesktopImportReviewPort {
    private val root: Path = dataRoot.toAbsolutePath().normalize()
    private val planLock: Any = Any()
    private var pending: PreparedPlan? = null
    private var preparing: Boolean = false
    private var importing: Boolean = false

    override suspend fun prepareSelected(paths: List<String>): DesktopImportPreview = prepare(paths, null)

    override suspend fun prepareReselected(jobId: String, paths: List<String>): DesktopImportPreview = prepare(paths, jobId)

    /** Explicit G1 Rescan only. Takes ownership through review, confirmation and actual drain. */
    suspend fun prepareRemembered(batch: SourceImportBatch): DesktopImportPreview {
        val id = UUID.randomUUID().toString()
        synchronized(planLock) {
            if (preparing || importing) {
                batch.close()
                return DesktopImportPreview(id, emptyList(), 0, 1, "import_busy")
            }
            pending?.sourceBatch?.close()
            pending = null
            preparing = true
        }
        var transferred = false
        val started = System.nanoTime()
        try {
            val plan = withContext(Dispatchers.IO) {
                if (batch.files.size > MAX_FILES) throw SourceAccessFailure("source_limit")
                val budget = ByteBudget()
                val candidates = batch.files.map { file ->
                    currentCoroutineContext().ensureActive()
                    if (batch.preparationMs + (System.nanoTime() - started) / 1_000_000 > BATCH_TIMEOUT_MS) {
                        throw SourceAccessFailure("source_limit")
                    }
                    // A display-only name supplies the established extension/title rules. It is
                    // never resolved against cwd or opened: all bytes/attributes use the lease.
                    val name = Path.of(file.name)
                    if (name.nameCount != 1 || name.isAbsolute || !isSupportedFile(name)) throw SourceAccessFailure("source_selection_invalid")
                    val attrs = file.attributes()
                    if (!attrs.isRegularFile || attrs.size() <= 0 || attrs.size() > MAX_FILE_BYTES) throw SourceAccessFailure("source_limit")
                    budget.reserve(attrs.size())
                    PreparedCandidate(UUID.randomUUID().toString(), name, fallbackTitle(name),
                        SourceBaseline(attrs.size(), attrs.lastModifiedTime(), attrs.creationTime(), attrs.fileKey()), file)
                }
                val preview = DesktopImportPreview(id, candidates.map { DesktopImportPreviewFile(it.id, it.title, it.baseline.size) },
                    candidates.sumOf { it.baseline.size }, jobId = UUID.randomUUID().toString())
                PreparedPlan(preview, candidates, (BATCH_TIMEOUT_MS - batch.preparationMs -
                    (System.nanoTime() - started) / 1_000_000).coerceAtLeast(0), null, batch)
            }
            currentCoroutineContext().ensureActive()
            synchronized(planLock) { pending = plan; transferred = true }
            return plan.preview
        } finally {
            try { if (!transferred) batch.close() }
            finally { synchronized(planLock) { preparing = false } }
        }
    }

    private suspend fun prepare(paths: List<String>, resumeJobId: String?): DesktopImportPreview {
        val id = UUID.randomUUID().toString()
        synchronized(planLock) {
            if (preparing || importing) return DesktopImportPreview(id, emptyList(), 0, 1, "import_busy")
            pending?.sourceBatch?.close()
            preparing = true
            pending = null
        }
        val report = Report()
        val startedAtNanos = System.nanoTime()
        try {
            val plan = withContext(Dispatchers.IO) {
                val job = if (resumeJobId == null) null else try {
                    store.loadImportJob(resumeJobId).also {
                        when {
                            it == null -> report.error("import_job_missing")
                            it.phase == DesktopImportJobPhase.UNSUPPORTED || it.revision == null ->
                                report.error("import_job_history_unsupported")
                            it.phase == DesktopImportJobPhase.COMPLETED -> report.error("import_job_completed")
                        }
                    }
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    report.error(storeError(failure))
                    null
                }
                val selections = paths.take(DesktopLocalLimits.MAX_SELECTION_ROOTS + 1)
                val candidates = if (report.errorCode != null) emptyList() else if (selections.size > DesktopLocalLimits.MAX_SELECTION_ROOTS) {
                    report.failure("import_selection_limit")
                    emptyList()
                } else {
                    val elapsedMs = (System.nanoTime() - startedAtNanos) / 1_000_000
                    withTimeoutOrNull((BATCH_TIMEOUT_MS - elapsedMs).coerceAtLeast(0)) { collectSelections(selections, report) } ?: run {
                        report.failure("import_time_limit")
                        emptyList()
                    }
                }
                val preview = DesktopImportPreview(id, candidates.map {
                    DesktopImportPreviewFile(it.id, it.title, it.baseline.size)
                }, candidates.sumOf { it.baseline.size }, report.failed, report.errorCode,
                    resumeJobId ?: UUID.randomUUID().toString(), job)
                val elapsedMs = (System.nanoTime() - startedAtNanos) / 1_000_000
                PreparedPlan(preview, candidates, (BATCH_TIMEOUT_MS - elapsedMs).coerceAtLeast(0), resumeJobId)
            }
            currentCoroutineContext().ensureActive()
            synchronized(planLock) { pending = plan }
            return plan.preview
        } finally {
            synchronized(planLock) { preparing = false }
        }
    }

    override fun discardPrepared(previewId: String): Unit {
        synchronized(planLock) {
            if (pending?.preview?.id == previewId) {
                pending?.sourceBatch?.close()
                pending = null
            }
        }
    }

    // Compatibility for explicit callers. Production selection must expose review first.
    override suspend fun importSelected(paths: List<String>): DesktopImportResult {
        val preview = prepareSelected(paths)
        if (preview.errorCode == "import_busy") return DesktopImportResult(0, 0, preview.failed, preview.errorCode)
        return importPrepared(preview.id) { }
    }

    override suspend fun importPrepared(
        previewId: String,
        onProgress: (DesktopImportProgress) -> Unit,
    ): DesktopImportResult = consume(previewId, null, onProgress)

    override suspend fun importReselected(
        previewId: String,
        mappings: List<DesktopImportCandidateMapping>,
        onProgress: (DesktopImportProgress) -> Unit,
    ): DesktopImportResult = consume(previewId, mappings.take(MAX_FILES + 1), onProgress)

    private suspend fun consume(
        previewId: String,
        mappings: List<DesktopImportCandidateMapping>?,
        onProgress: (DesktopImportProgress) -> Unit,
    ): DesktopImportResult {
        // Consumption happens before dispatch and is never rolled back, including cancellation.
        val plan = synchronized(planLock) {
            val selected = pending
            if (selected == null || selected.preview.id != previewId || importing || preparing) {
                return DesktopImportResult(0, 0, 1, "import_preview_expired")
            }
            mappingError(selected, mappings)?.let { return DesktopImportResult(0, 0, selected.preview.failed, it) }
            pending = null
            importing = true
            selected
        }
        val report = Report(plan.preview.failed, plan.preview.errorCode)
        val selected = if (mappings == null) plan.candidates.map {
            ConfirmedCandidate(UUID.randomUUID().toString(), it)
        } else mappings.mapNotNull { mapping -> mapping.selectionId?.let { selectionId ->
            ConfirmedCandidate(mapping.candidateId, plan.candidates.single { it.id == selectionId })
        } }
        val active = ActiveAttempt(checkNotNull(plan.preview.jobId), UUID.randomUUID().toString()).also {
            it.job = plan.preview.job
        }
        var completedFiles = 0
        fun publish(title: String? = null): Unit {
            val progress = DesktopImportProgress(previewId, selected.size,
                completedFiles, report.imported, report.duplicates, report.failed, title,
                active.jobId, active.job?.revision, active.job?.phase, report.errorCode)
            try {
                onProgress(progress)
            } catch (failure: Exception) {
                // A UI observer cannot undo a confirmed commit or mask cleanup/settled counts.
            }
        }
        var thrown: Throwable? = null
        var cancelled = false
        try {
            publish()
            currentCoroutineContext().ensureActive()
            if (selected.isEmpty()) return report.result()
            val completed = withContext(Dispatchers.IO) {
                withTimeoutOrNull(plan.remainingTimeMs) {
                    try {
                        // Both writes happen only after this exact preview has been confirmed.
                        active.admitted = true
                        active.job = plan.preview.job ?: importStore { store.createImportJob(DesktopImportJobManifest(active.jobId,
                            selected.map { DesktopImportJobCandidate(it.candidateId, it.file.title, it.file.baseline.size) })) }
                        val attempt = DesktopImportJobAttempt(active.jobId,
                            checkNotNull(active.job?.revision), UUID.randomUUID().toString(), active.attemptId, previewId,
                            selected.map { DesktopImportJobReselection(it.candidateId, it.file.id, it.file.title, it.file.baseline.size) })
                        val authorization = importStore { store.confirmImportJobAttempt(attempt) }
                        active.job = authorization.job
                        active.permit = authorization.permit?.takeIf {
                            it.jobId == active.jobId && it.attemptId == active.attemptId && it.previewId == previewId &&
                                authorization.job.confirmedAttempt == attempt && authorization.job.revision != null &&
                                authorization.job.phase == DesktopImportJobPhase.RESELECTION_REQUIRED
                        } ?: throw ImportStoreFailure("import_job_reselection_required")
                        publish()
                        val media = prepareMediaDirectory()
                        val budget = ByteBudget()
                        for (candidate in selected) {
                            currentCoroutineContext().ensureActive()
                            publish(candidate.file.title)
                            var stop = false
                            try {
                                importOne(candidate, active, media, budget, report) {
                                    // Publish before any cancellable cleanup or dispatcher return.
                                    completedFiles++
                                    publish()
                                }
                            } catch (failure: ImportFailure) {
                                settleFailure(candidate, active, failure.code, report)
                                completedFiles++
                                stop = failure.stopBatch
                            } catch (failure: IOException) {
                                settleFailure(candidate, active, "import_io_failed", report)
                                completedFiles++
                            } catch (failure: SecurityException) {
                                settleFailure(candidate, active, "import_access_denied", report)
                                completedFiles++
                            } finally {
                                publish()
                            }
                            if (stop) break
                        }
                    } catch (failure: ImportFailure) {
                        report.error(failure.code)
                    } catch (failure: IOException) {
                        report.error("import_io_failed")
                    } catch (failure: SecurityException) {
                        report.error("import_access_denied")
                    } catch (failure: CancellationException) {
                        throw failure
                    } catch (failure: Exception) {
                        // Never fail or continue past an uncertain DB operation.
                        report.error(if (failure is ImportStoreFailure) failure.code else storeError(failure))
                    }
                    true
                }
            }
            if (completed == null) report.error("import_time_limit")
        } catch (failure: Throwable) {
            thrown = failure
            cancelled = failure is CancellationException
            throw failure
        } finally {
            // Preserve caller cancellation (including suppressed reopen markers). The host uses
            // this last settled snapshot when synthesizing its cancelled presentation result.
            try {
                // The host must join this drain before closing its store. Reload handles a lost
                // commit response; only our current attempt may be stopped, never a replacement.
                if (active.admitted) withContext(NonCancellable + Dispatchers.IO) {
                    try {
                        val current = store.loadImportJob(active.jobId)
                        active.job = current
                        val revision = current?.revision
                        if (current == null) report.error("import_job_reconciliation_required")
                        else if (revision == null || current.phase == DesktopImportJobPhase.UNSUPPORTED) {
                            report.error("import_job_history_unsupported")
                        }
                        if (current != null && revision != null && current.phase != DesktopImportJobPhase.COMPLETED &&
                            current.phase != DesktopImportJobPhase.UNSUPPORTED &&
                            (current.attemptId == active.attemptId || (current.attemptId == null && plan.resumeJobId == null))) {
                            active.job = store.stopImportJob(DesktopImportJobStop(active.jobId, revision,
                                UUID.randomUUID().toString(), if (cancelled) DesktopImportJobStopReason.CANCELLED else DesktopImportJobStopReason.PAUSED))
                        }
                    } catch (failure: Exception) {
                        if (thrown !== failure) thrown?.addSuppressed(failure)
                        report.error(storeError(failure))
                    }
                }
                publish()
            } finally {
                try { plan.sourceBatch?.close() }
                finally { synchronized(planLock) { importing = false } }
            }
        }
        return report.result()
    }

    private fun mappingError(plan: PreparedPlan, mappings: List<DesktopImportCandidateMapping>?): String? {
        if (plan.resumeJobId == null) return if (mappings == null) null else "import_job_mapping_invalid"
        val job = plan.preview.job
        if (job == null || job.revision == null ||
            job.phase == DesktopImportJobPhase.UNSUPPORTED || job.phase == DesktopImportJobPhase.COMPLETED) {
            return plan.preview.errorCode ?: "import_job_history_unsupported"
        }
        if (mappings == null || mappings.none { it.selectionId != null }) return "import_job_mapping_required"
        val mapped = mappings.filter { it.selectionId != null }
        if (mappings.size > MAX_FILES || mappings.map { it.candidateId }.toSet().size != mappings.size ||
            mapped.map { it.selectionId }.toSet().size != mapped.size || mappings.any { mapping ->
                job.candidates.none { it.candidate.id == mapping.candidateId &&
                    it.outcome != DesktopImportJobOutcome.IMPORTED && it.outcome != DesktopImportJobOutcome.DUPLICATE } ||
                    (mapping.selectionId != null && plan.candidates.none { it.id == mapping.selectionId })
            }) return "import_job_mapping_invalid"
        return null
    }

    private fun settlement(active: ActiveAttempt, candidate: ConfirmedCandidate): DesktopImportJobSettlement =
        DesktopImportJobSettlement(active.jobId, checkNotNull(active.job?.revision), UUID.randomUUID().toString(),
            active.attemptId, candidate.candidateId, candidate.file.id)

    private suspend fun settleFailure(candidate: ConfirmedCandidate, active: ActiveAttempt, code: String, report: Report): Unit {
        currentCoroutineContext().ensureActive()
        val failure = when (code) {
            "import_source_changed" -> DesktopImportJobFailure.SOURCE_CHANGED
            "import_source_unreadable", "import_access_denied" -> DesktopImportJobFailure.SOURCE_UNAVAILABLE
            "import_unsupported_format" -> DesktopImportJobFailure.UNSUPPORTED
            else -> DesktopImportJobFailure.COPY_FAILED
        }
        try {
            active.job = store.failImportJobCandidate(settlement(active, candidate), checkNotNull(active.permit), failure)
        } catch (error: CancellationException) {
            throw error
        } catch (error: Exception) {
            throw ImportStoreFailure(storeError(error))
        }
        val settled = active.job?.candidates?.singleOrNull { it.candidate.id == candidate.candidateId }
            ?: throw ImportStoreFailure("import_job_reconciliation_required")
        if (settled.outcome != DesktopImportJobOutcome.FAILED || settled.settledAttemptId != active.attemptId ||
            settled.failure != failure) throw ImportStoreFailure("import_job_reconciliation_required")
        report.failure(code)
    }

    private fun storeError(failure: Exception): String = when {
        (failure is DesktopStorageException && failure.code == "desktop_snapshot_unavailable") ||
            failure.suppressed.take(16).any { it is DesktopStorageException && it.code == "desktop_snapshot_unavailable" } ->
            "import_library_reopen_required"
        failure is DesktopStorageException -> when (failure.code) {
            "desktop_import_job_conflict" -> "import_job_conflict"
            "desktop_import_job_history_unsupported" -> "import_job_history_unsupported"
            "desktop_import_job_reselection_required" -> "import_job_reselection_required"
            "desktop_import_job_limit" -> "import_job_limit"
            "desktop_import_job_permit_limit" -> "import_job_permit_limit"
            "desktop_import_job_invalid", "desktop_import_job_identity_invalid" -> "import_job_invalid"
            else -> "import_job_reconciliation_required"
        }
        else -> "import_job_reconciliation_required"
    }

    private suspend fun <T> importStore(action: suspend () -> T): T = try {
        action()
    } catch (failure: CancellationException) {
        throw failure
    } catch (failure: Exception) {
        throw ImportStoreFailure(storeError(failure))
    }

    private suspend fun prepareMediaDirectory(): Path = runInterruptible {
        rejectSymlinkComponents(root)
        Files.createDirectories(root)
        val media = root.resolve("media")
        if (!Files.exists(media, NOFOLLOW_LINKS)) {
            try {
                if (root.fileSystem.supportedFileAttributeViews().contains("posix")) {
                    Files.createDirectory(media, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")))
                } else {
                    Files.createDirectory(media)
                }
            } catch (failure: FileAlreadyExistsException) {
                // Another importer may have created it; validate it before use below.
            }
        }
        validateMediaDirectory(media)
        media
    }

    private fun validateMediaDirectory(media: Path): Unit {
        rejectSymlinkComponents(media)
        if (!Files.isDirectory(media, NOFOLLOW_LINKS) || !Files.isReadable(media) || !Files.isWritable(media)) {
            throw ImportFailure("import_managed_directory_unavailable", true)
        }
        val realRoot = root.toRealPath()
        val realMedia = media.toRealPath()
        if (realMedia.parent != realRoot || realMedia.fileName.toString() != "media") {
            throw ImportFailure("import_managed_directory_unsafe", true)
        }
    }

    private suspend fun collectSelections(paths: List<String>, report: Report): List<PreparedCandidate> = runInterruptible {
        val candidates = linkedMapOf<Path, PreparedCandidate>()
        val budget = ByteBudget()
        var visited = 0
        var stop = false
        fun addCandidate(path: Path): Unit {
            val source = path.toAbsolutePath().normalize()
            if (source in candidates) return
            if (candidates.size >= MAX_FILES) throw ImportFailure("import_file_limit", true)
            try {
                rejectSymlinkComponents(source)
                val attrs = Files.readAttributes(source, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (!attrs.isRegularFile || !Files.isReadable(source)) throw ImportFailure("import_source_unreadable")
                if (attrs.size() <= 0) throw ImportFailure("import_empty_file")
                if (attrs.size() > MAX_FILE_BYTES) throw ImportFailure("import_file_size_limit")
                budget.reserve(attrs.size())
                candidates[source] = PreparedCandidate(UUID.randomUUID().toString(), source, fallbackTitle(source),
                    SourceBaseline(attrs.size(), attrs.lastModifiedTime(), attrs.creationTime(), attrs.fileKey()))
            } catch (failure: ImportFailure) {
                if (failure.stopBatch) throw failure
                report.failure(failure.code)
            } catch (failure: IOException) {
                report.failure("import_source_unreadable")
            } catch (failure: SecurityException) {
                report.failure("import_access_denied")
            }
        }
        for (raw in paths) {
            checkInterrupted()
            if (stop) break
            try {
                if (raw.isBlank() || raw.length > MAX_SELECTION_CHARS) throw ImportFailure("import_invalid_selection")
                val selected = Path.of(raw).toAbsolutePath().normalize()
                rejectSymlinkComponents(selected)
                val selectedAttributes = Files.readAttributes(selected, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (selectedAttributes.isRegularFile) {
                    if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                    if (!isSupportedFile(selected)) throw ImportFailure("import_unsupported_format")
                    addCandidate(selected)
                } else if (selectedAttributes.isDirectory) {
                    Files.walkFileTree(selected, emptySet(), MAX_DEPTH + 1, object : SimpleFileVisitor<Path>() {
                        override fun preVisitDirectory(dir: Path, attrs: BasicFileAttributes): FileVisitResult {
                            checkInterrupted()
                            if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                            rejectSymlinkComponents(dir)
                            return FileVisitResult.CONTINUE
                        }

                        override fun visitFile(file: Path, attrs: BasicFileAttributes): FileVisitResult {
                            checkInterrupted()
                            if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                            when {
                                attrs.isSymbolicLink -> report.failure("import_symlink_rejected")
                                attrs.isDirectory -> report.failure("import_depth_limit")
                                attrs.isOther -> report.failure("import_source_not_regular")
                                attrs.isRegularFile && isSupportedFile(file) -> addCandidate(file)
                            }
                            return FileVisitResult.CONTINUE
                        }

                        override fun visitFileFailed(file: Path, exc: IOException): FileVisitResult {
                            checkInterrupted()
                            if (++visited > MAX_ENTRIES) throw ImportFailure("import_entry_limit", true)
                            report.failure("import_source_unreadable")
                            return FileVisitResult.CONTINUE
                        }
                    })
                } else {
                    throw ImportFailure("import_source_not_regular")
                }
            } catch (failure: InvalidPathException) {
                report.failure("import_invalid_selection")
            } catch (failure: ImportFailure) {
                report.failure(failure.code)
                stop = failure.stopBatch
            } catch (failure: IOException) {
                report.failure("import_source_unreadable")
            } catch (failure: SecurityException) {
                report.failure("import_access_denied")
            }
        }
        candidates.values.toList()
    }

    private suspend fun importOne(
        confirmed: ConfirmedCandidate,
        active: ActiveAttempt,
        media: Path,
        budget: ByteBudget,
        report: Report,
        onCommitted: () -> Unit,
    ): Unit {
        val candidate = confirmed.file
        val source = candidate.source
        val extension = DesktopManagedMedia.canonicalSourceExtension(source.fileName.toString())
            ?: throw ImportFailure("import_unsupported_format")
        val title = candidate.title
        var temporary: Path? = null
        try {
            val copy = runInterruptible {
                validateMediaDirectory(media)
                val before = revalidateSource(candidate)
                budget.reserve(before.size())
                requireFreeSpace(media, before.size())
                val attributes = if (media.fileSystem.supportedFileAttributeViews().contains("posix")) {
                    arrayOf(PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))
                } else {
                    emptyArray()
                }
                val part = Files.createTempFile(media, ".import-", ".part", *attributes)
                temporary = part
                val digest = MessageDigest.getInstance("SHA-256")
                var copied = 0L
                val sourceChannel = try {
                    candidate.remembered?.open() ?: FileChannel.open(source, READ, NOFOLLOW_LINKS)
                } catch (failure: IOException) {
                    checkInterrupted()
                    throw ImportFailure("import_source_changed")
                } catch (failure: SecurityException) {
                    throw ImportFailure("import_source_changed")
                }
                sourceChannel.use { input ->
                    revalidateSource(candidate)
                    FileChannel.open(part, WRITE, NOFOLLOW_LINKS).use { output ->
                        val buffer = ByteBuffer.allocate(BUFFER_BYTES)
                        while (true) {
                            checkInterrupted()
                            buffer.clear()
                            val read = input.read(buffer)
                            if (read < 0) break
                            if (read == 0) continue
                            copied += read
                            if (copied > before.size() || copied > MAX_FILE_BYTES) throw ImportFailure("import_source_changed")
                            requireFreeSpace(media, 0)
                            buffer.flip()
                            digest.update(buffer.asReadOnlyBuffer())
                            while (buffer.hasRemaining()) {
                                checkInterrupted()
                                output.write(buffer)
                            }
                        }
                        output.force(true)
                    }
                }
                val after = revalidateSource(candidate)
                if (!after.isRegularFile || copied != before.size() || after.size() != before.size() ||
                    after.lastModifiedTime() != before.lastModifiedTime() || after.fileKey() != before.fileKey()
                ) {
                    throw ImportFailure("import_source_changed")
                }
                val hash = hex(digest.digest())
                val verifiedSnapshot = verifyFile(part, copied, hash)
                // Optional metadata belongs to this verified private copy, before publication
                // and outside its lock. It never reopens the user's original source.
                val durationMs = if (extension == "wav") {
                    DesktopPcmWaveDurationProbe.durationMs(part, verifiedSnapshot)
                } else {
                    null
                }
                VerifiedCopy(part, copied, hash, durationMs)
            }
            DesktopManagedMedia.withPublicationLock {
                currentCoroutineContext().ensureActive()
                val destination = media.resolve("${copy.hash}.$extension")
                runInterruptible {
                    validateMediaDirectory(media)
                    if (Files.exists(destination, NOFOLLOW_LINKS)) {
                        // Never overwrite a corrupt or unsafe managed file during deduplication.
                        verifyFile(destination, copy.size, copy.hash)
                        // It may be an orphan from an interrupted publication. Force its bytes
                        // as well as the directory before allowing a new database reference.
                        FileChannel.open(destination, WRITE, NOFOLLOW_LINKS).use { it.force(true) }
                    } else {
                        checkInterrupted()
                        try {
                            Files.move(copy.path, destination, ATOMIC_MOVE)
                        } catch (failure: AtomicMoveNotSupportedException) {
                            throw ImportFailure("import_atomic_publish_unavailable")
                        }
                        verifyFile(destination, copy.size, copy.hash)
                    }
                    // Persist the rename before committing a row on POSIX/macOS. Windows NIO
                    // does not support opening directories as channels; file force still applies.
                    if (media.fileSystem.supportedFileAttributeViews().contains("posix")) {
                        try {
                            FileChannel.open(media, READ, NOFOLLOW_LINKS).use { it.force(true) }
                        } catch (failure: IOException) {
                            throw ImportFailure("import_publish_sync_failed")
                        }
                    }
                }
                currentCoroutineContext().ensureActive()
                val settled = try {
                    store.commitImportJobCandidate(settlement(active, confirmed), checkNotNull(active.permit),
                        DesktopImportedTrack(
                            id = UUID.randomUUID().toString(),
                            title = title,
                            artist = "",
                            durationMs = copy.durationMs,
                            sha256 = copy.hash,
                            byteSize = copy.size,
                            managedFileName = destination.fileName.toString(),
                        ),
                    )
                } catch (failure: CancellationException) {
                    throw failure
                } catch (failure: Exception) {
                    // Retain the bytes and the unsettled/committed history for reconciliation.
                    // Never convert an uncertain commit to a failure outcome or start another file.
                    throw ImportStoreFailure(storeError(failure))
                }
                active.job = settled
                val outcome = settled.candidates.singleOrNull { it.candidate.id == confirmed.candidateId }
                    ?: throw ImportStoreFailure("import_job_reconciliation_required")
                if (outcome.settledAttemptId != active.attemptId) throw ImportStoreFailure("import_job_reconciliation_required")
                when (outcome.outcome) {
                    DesktopImportJobOutcome.IMPORTED -> report.imported++
                    DesktopImportJobOutcome.DUPLICATE -> report.duplicates++
                    else -> throw ImportStoreFailure("import_job_reconciliation_required")
                }
                onCommitted()
            }
        } finally {
            temporary?.let { part ->
                withContext(NonCancellable + Dispatchers.IO) {
                    try {
                        validateMediaDirectory(media)
                        Files.deleteIfExists(part)
                    } catch (failure: IOException) {
                        // Never mask the original failure or delete published managed media.
                    } catch (failure: SecurityException) {
                        // A later explicit import does not trust or enumerate these temp files.
                    } catch (failure: ImportFailure) {
                        // Refuse cleanup if the owned directory has become unsafe.
                    }
                }
            }
        }
    }

    private fun revalidateSource(candidate: PreparedCandidate): BasicFileAttributes {
        try {
            if (candidate.remembered == null) rejectSymlinkComponents(candidate.source)
            val attrs = candidate.remembered?.attributes()
                ?: Files.readAttributes(candidate.source, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
            val baseline = candidate.baseline
            if (!attrs.isRegularFile || (candidate.remembered == null && !Files.isReadable(candidate.source)) || attrs.size() != baseline.size ||
                attrs.size() <= 0 || attrs.size() > MAX_FILE_BYTES || attrs.lastModifiedTime() != baseline.modified ||
                attrs.creationTime() != baseline.created || attrs.fileKey() != baseline.fileKey
            ) throw ImportFailure("import_source_changed")
            return attrs
        } catch (failure: ImportFailure) {
            throw ImportFailure("import_source_changed")
        } catch (failure: IOException) {
            throw ImportFailure("import_source_changed")
        } catch (failure: SecurityException) {
            throw ImportFailure("import_source_changed")
        }
    }

    private fun verifyFile(path: Path, expectedSize: Long, expectedHash: String): BasicFileAttributes {
        checkInterrupted()
        rejectSymlinkComponents(path)
        val attrs = Files.readAttributes(path, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
        if (!attrs.isRegularFile || !Files.isReadable(path) || attrs.size() != expectedSize) {
            throw ImportFailure("import_managed_integrity_failed")
        }
        val digest = MessageDigest.getInstance("SHA-256")
        var readBytes = 0L
        FileChannel.open(path, READ, NOFOLLOW_LINKS).use { channel ->
            val buffer = ByteBuffer.allocate(BUFFER_BYTES)
            while (true) {
                checkInterrupted()
                buffer.clear()
                val read = channel.read(buffer)
                if (read < 0) break
                if (read == 0) continue
                readBytes += read
                if (readBytes > expectedSize) throw ImportFailure("import_managed_integrity_failed")
                buffer.flip()
                digest.update(buffer)
            }
        }
        checkInterrupted()
        rejectSymlinkComponents(path)
        val after = Files.readAttributes(path, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
        if (readBytes != expectedSize || hex(digest.digest()) != expectedHash || !after.isRegularFile ||
            after.size() != attrs.size() || after.lastModifiedTime() != attrs.lastModifiedTime() ||
            after.fileKey() != attrs.fileKey()
        ) {
            throw ImportFailure("import_managed_integrity_failed")
        }
        return after
    }

    private fun rejectSymlinkComponents(path: Path): Unit {
        val absolute = path.toAbsolutePath().normalize()
        var current = absolute.root
        for (component in absolute) {
            checkInterrupted()
            current = current.resolve(component)
            if (Files.isSymbolicLink(current)) throw ImportFailure("import_symlink_rejected")
            if (Files.exists(current, NOFOLLOW_LINKS)) {
                val attrs = Files.readAttributes(current, BasicFileAttributes::class.java, NOFOLLOW_LINKS)
                if (attrs.isOther) throw ImportFailure("import_source_not_regular")
            }
        }
    }

    private fun requireFreeSpace(media: Path, requestedBytes: Long): Unit {
        if (Files.getFileStore(media).usableSpace < requestedBytes + SPACE_RESERVE_BYTES) {
            throw ImportFailure("import_insufficient_space", true)
        }
    }

    private fun fallbackTitle(path: Path): String {
        val stem = path.fileName.toString().substringBeforeLast('.')
        return buildString {
            var offset = 0
            while (offset < stem.length && length < MAX_TITLE_CHARS) {
                val character = stem[offset++]
                if (character.isHighSurrogate()) {
                    if (offset < stem.length && stem[offset].isLowSurrogate()) {
                        if (length + 2 > MAX_TITLE_CHARS) break
                        append(character)
                        append(stem[offset++])
                    }
                } else if (!character.isLowSurrogate() && !character.isISOControl() &&
                    character != '/' && character != '\\' && character != ':' &&
                    Character.getType(character) != Character.FORMAT.toInt()
                ) {
                    append(character)
                }
            }
        }.ifBlank { "Imported audio" }
    }

    private fun isSupportedFile(path: Path): Boolean =
        DesktopManagedMedia.canonicalSourceExtension(path.fileName.toString()) != null

    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 0xff) }

    private fun checkInterrupted(): Unit {
        if (Thread.currentThread().isInterrupted) throw CancellationException("import_cancelled")
    }

    private data class VerifiedCopy(val path: Path, val size: Long, val hash: String, val durationMs: Long?)

    private data class PreparedPlan(
        val preview: DesktopImportPreview,
        val candidates: List<PreparedCandidate>,
        val remainingTimeMs: Long,
        val resumeJobId: String?,
        val sourceBatch: SourceImportBatch? = null,
    )
    private data class PreparedCandidate(val id: String, val source: Path, val title: String, val baseline: SourceBaseline,
        val remembered: SourceImportFile? = null)
    private data class ConfirmedCandidate(val candidateId: String, val file: PreparedCandidate)
    private class ActiveAttempt(val jobId: String, val attemptId: String) {
        var admitted: Boolean = false
        var job: DesktopImportJob? = null
        var permit: DesktopImportJobPermit? = null
    }

    // Attributes detect ordinary changes/replacements, not adversarial same-attribute rewrites.
    // Preparation does not read/hash content. SHA-256 verifies the confirmed private copy only.
    // Providers without creation time/file keys can expose weaker/default identity observations.
    private data class SourceBaseline(val size: Long, val modified: FileTime, val created: FileTime, val fileKey: Any?)

    private class ImportFailure(val code: String, val stopBatch: Boolean = false) : RuntimeException(code)
    private class ImportStoreFailure(val code: String) : RuntimeException(code)

    private class ByteBudget {
        private var bytes: Long = 0
        fun reserve(size: Long): Unit {
            if (size > MAX_BATCH_BYTES - bytes) throw ImportFailure("import_batch_size_limit", true)
            bytes += size
        }
    }

    private class Report(initialFailed: Int = 0, initialErrorCode: String? = null) {
        var imported: Int = 0
        var duplicates: Int = 0
        var failed: Int = initialFailed
            private set
        var errorCode: String? = initialErrorCode
            private set
        fun failure(code: String): Unit {
            failed++
            if (errorCode == null) errorCode = code
        }
        fun error(code: String): Unit {
            // Job errors leave candidates unsettled and take precedence over per-file errors.
            if (errorCode != "import_library_reopen_required") errorCode = code
        }
        fun result(): DesktopImportResult = DesktopImportResult(imported, duplicates, failed, errorCode)
    }

    private companion object {
        const val MAX_SELECTION_CHARS: Int = 4_096
        const val MAX_FILES: Int = 2_048
        const val MAX_ENTRIES: Int = 10_000
        const val MAX_DEPTH: Int = 16
        const val MAX_FILE_BYTES: Long = 2L * 1024 * 1024 * 1024
        const val MAX_BATCH_BYTES: Long = 8L * 1024 * 1024 * 1024
        const val SPACE_RESERVE_BYTES: Long = 128L * 1024 * 1024
        const val BATCH_TIMEOUT_MS: Long = 15L * 60 * 1000
        const val BUFFER_BYTES: Int = 256 * 1024
        const val MAX_TITLE_CHARS: Int = 512
    }
}
