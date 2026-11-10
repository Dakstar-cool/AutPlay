package app.autplay.data.desktop

import app.autplay.application.desktop.DesktopImportedTrack
import app.autplay.application.desktop.DesktopImportJob
import app.autplay.application.desktop.DesktopImportJobAttempt
import app.autplay.application.desktop.DesktopImportJobCandidate
import app.autplay.application.desktop.DesktopImportJobCandidateState
import app.autplay.application.desktop.DesktopImportJobFailure
import app.autplay.application.desktop.DesktopImportJobLimits
import app.autplay.application.desktop.DesktopImportJobManifest
import app.autplay.application.desktop.DesktopImportJobOutcome
import app.autplay.application.desktop.DesktopImportJobPhase
import app.autplay.application.desktop.DesktopImportJobReselection
import app.autplay.application.desktop.DesktopImportJobSettlement
import app.autplay.application.desktop.DesktopImportJobStop
import app.autplay.application.desktop.DesktopImportJobStopReason
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Only called inside DAO transactions. No timestamp/UUID/rowid defines mutation order.
 * UUID keysets bound materialization; explicit complete sequences, revision groups and predecessor
 * links define history. Scalar guards may scan the frozen, unindexed outbox; no latency claim.
 * Every unknown, missing, ambiguous or over-budget history is retained and becomes read-only.
 */
internal object DesktopImportJobProjection {
    private const val PREFIX: String = "desktop-import-job:"
    private data class Event(val row: LocalFactRow, val revision: Long, val sequence: Long,
        val previous: String?, val type: String, val body: JsonObject)
    internal data class History(
        val job: DesktopImportJob, val manifest: DesktopImportJobManifest,
        val attempt: DesktopImportJobAttempt?, val facts: List<LocalFactRow>,
        val receipts: Map<String, List<JsonObject>>, val attempts: Set<String>, val previews: Set<String>,
    )

    fun aggregate(jobId: String): String = PREFIX + jobId
    fun jobId(aggregate: String): String = aggregate.removePrefix(PREFIX)
    fun requireUuid(value: String): Unit {
        if (value.length != 36 || !runCatching { UUID.fromString(value).toString() == value }.getOrDefault(false)) {
            throw DesktopStorageException("desktop_import_job_identity_invalid")
        }
    }
    private fun valid(condition: Boolean): Unit { if (!condition) throw DesktopStorageException("desktop_import_job_invalid") }
    private fun conflict(condition: Boolean): Unit { if (!condition) throw DesktopStorageException("desktop_import_job_conflict") }
    private fun label(value: String): Unit {
        valid(value.isNotBlank() && value.length <= DesktopImportJobLimits.TITLE_CHARS && '\u0000' !in value)
        unicode(value)
    }
    private fun unicode(value: String): Unit {
        var i = 0
        while (i < value.length) {
            val c = value[i++]
            if (c.isHighSurrogate()) valid(i < value.length && value[i++].isLowSurrogate())
            else valid(!c.isLowSurrogate())
        }
    }
    private fun fileSize(size: Long): Unit = valid(size in 1..DesktopImportJobLimits.FILE_BYTES)
    private fun batch(sizes: List<Long>): Unit {
        var total = 0L
        for (size in sizes) { fileSize(size); valid(size <= DesktopImportJobLimits.BATCH_BYTES - total); total += size }
    }
    fun freeze(manifest: DesktopImportJobManifest): DesktopImportJobManifest {
        requireUuid(manifest.jobId)
        val candidates = manifest.candidates.take(DesktopImportJobLimits.CANDIDATES + 1)
        valid(candidates.size in 1..DesktopImportJobLimits.CANDIDATES)
        candidates.forEach { requireUuid(it.id); label(it.title) }
        valid(candidates.map { it.id }.toSet().size == candidates.size)
        batch(candidates.map { it.byteSize })
        return manifest.copy(candidates = candidates)
    }
    fun freeze(attempt: DesktopImportJobAttempt): DesktopImportJobAttempt {
        requireUuid(attempt.jobId); requireUuid(attempt.commandId); requireUuid(attempt.attemptId); requireUuid(attempt.previewId)
        valid(attempt.expectedRevision >= 0)
        val selections = attempt.selections.take(DesktopImportJobLimits.CANDIDATES + 1)
        valid(selections.size in 1..DesktopImportJobLimits.CANDIDATES)
        selections.forEach { requireUuid(it.candidateId); requireUuid(it.selectionId); label(it.title) }
        valid(selections.map { it.candidateId }.toSet().size == selections.size &&
            selections.map { it.selectionId }.toSet().size == selections.size)
        batch(selections.map { it.byteSize })
        return attempt.copy(selections = selections)
    }

    suspend fun read(dao: DesktopDao, id: String): DesktopImportJob? {
        requireUuid(id)
        if (dao.importJobFactCount(aggregate(id)) == 0L) return null
        return history(dao, id)?.job ?: DesktopImportJob(id, null, DesktopImportJobPhase.UNSUPPORTED,
            errorCode = "desktop_import_job_history_unsupported")
    }
    suspend fun required(dao: DesktopDao, id: String): History {
        requireUuid(id)
        return history(dao, id) ?: throw DesktopStorageException("desktop_import_job_history_unsupported")
    }
    private suspend fun history(dao: DesktopDao, id: String): History? {
        val aggregate = aggregate(id)
        val count = dao.importJobFactCount(aggregate)
        if (count !in 1..DesktopImportJobLimits.FACTS.toLong() ||
            dao.importJobFactBytes(aggregate) > DesktopImportJobLimits.HISTORY_BYTES ||
            dao.importJobUnsupportedFactCount(aggregate) != 0L) return null
        val rows = ArrayList<LocalFactRow>(count.toInt())
        var cursor: String? = null
        while (rows.size < count) {
            currentCoroutineContext().ensureActive()
            val page = dao.importJobFactPage(aggregate, cursor, DesktopImportJobLimits.FACT_PAGE)
            if (page.isEmpty()) return null
            rows.addAll(page)
            if (rows.size > count) return null
            cursor = page.last().changeId
        }
        return try { reconstruct(dao, id, rows) } catch (failure: CancellationException) { throw failure }
        catch (_: IllegalArgumentException) { null }
        catch (_: IllegalStateException) { null } catch (_: ClassCastException) { null }
        catch (_: NoSuchElementException) { null }
    }

    private suspend fun reconstruct(dao: DesktopDao, id: String, rows: List<LocalFactRow>): History {
        val events = rows.map { row ->
            requireUuid(row.changeId)
            val snapshot = snapshot(row, "IMPORT_JOB_FACT")
            valid(snapshot.keys == setOf("job_id", "revision", "sequence", "previous_fact_id", "event", "body"))
            valid(row.aggregateId == aggregate(id) && string(snapshot, "job_id") == id)
            Event(row, number(snapshot, "revision"), number(snapshot, "sequence"), nullable(snapshot, "previous_fact_id"),
                string(snapshot, "event"), snapshot.getValue("body") as JsonObject)
        }.sortedBy { it.sequence }
        var predecessor: String? = null
        events.forEachIndexed { index, event ->
            valid(event.sequence == index.toLong() + 1 && event.previous == predecessor)
            predecessor = event.row.changeId
        }
        val groups = events.groupBy { it.revision }.entries.toList()
        valid(groups.map { it.key } == groups.indices.map { it.toLong() })
        valid(groups.flatMap { it.value } == events)
        val creation = groups.first().value
        valid(creation.first().type == "CREATED")
        val count = number(creation.first().body, "candidate_count")
        valid(count in 1..DesktopImportJobLimits.CANDIDATES.toLong() && creation.size.toLong() == count + 1)
        valid(creation.first().body == obj("candidate_count" to num(count)))
        val manifest = freeze(DesktopImportJobManifest(id, creation.drop(1).mapIndexed { index, event ->
            valid(event.type == "CANDIDATE")
            val candidate = DesktopImportJobCandidate(string(event.body, "candidate_id"), string(event.body, "title"),
                number(event.body, "byte_size"))
            valid(event.body == candidateBody(candidate, index))
            candidate
        }))
        val candidates = manifest.candidates.associate { it.id to DesktopImportJobCandidateState(it) }.toMutableMap()
        var phase = DesktopImportJobPhase.REVIEW_REQUIRED
        var attempt: DesktopImportJobAttempt? = null
        val settledInAttempt = HashSet<String>()
        val receipts = LinkedHashMap<String, List<JsonObject>>()
        val attempts = HashSet<String>()
        val previews = HashSet<String>()
        for ((revision, group) in groups.drop(1)) {
            currentCoroutineContext().ensureActive()
            valid(phase != DesktopImportJobPhase.COMPLETED)
            val head = group.first()
            val request = head.body.getValue("request") as JsonObject
            val commandId = string(request, "command_id")
            requireUuid(commandId)
            valid(number(request, "expected_revision") == revision - 1 && commandId !in receipts)
            val signature = when (head.type) {
                "ATTEMPT" -> {
                    val mappingCount = number(head.body, "mapping_count")
                    valid(mappingCount in 1..DesktopImportJobLimits.CANDIDATES.toLong() && group.size.toLong() == mappingCount + 1)
                    val next = freeze(DesktopImportJobAttempt(id, revision - 1, commandId, string(request, "attempt_id"),
                        string(request, "preview_id"), group.drop(1).mapIndexed { index, event ->
                            valid(event.type == "MAPPING")
                            val selection = DesktopImportJobReselection(string(event.body, "candidate_id"),
                                string(event.body, "selection_id"), string(event.body, "title"), number(event.body, "byte_size"))
                            valid(event.body == mappingBody(selection, index))
                            valid(candidates.getValue(selection.candidateId).outcome !in committed)
                            selection
                        }))
                    valid(attempts.add(next.attemptId) && previews.add(next.previewId) && attempts.size <= DesktopImportJobLimits.ATTEMPTS)
                    valid(head.body == obj("request" to attemptRequest(next), "mapping_count" to num(mappingCount)))
                    attempt = next
                    settledInAttempt.clear()
                    phase = DesktopImportJobPhase.RESELECTION_REQUIRED
                    attemptSignature(next)
                }
                "SETTLED" -> {
                    valid(group.size == 1 && phase == DesktopImportJobPhase.RESELECTION_REQUIRED)
                    val settlement = settlement(request, id)
                    val active = checkNotNull(attempt)
                    valid(settlement.attemptId == active.attemptId)
                    val selection = active.selections.single { it.candidateId == settlement.candidateId }
                    valid(selection.selectionId == settlement.selectionId && settledInAttempt.add(selection.candidateId))
                    val previous = candidates.getValue(selection.candidateId)
                    valid(previous.outcome !in committed)
                    val outcome = DesktopImportJobOutcome.valueOf(string(head.body, "outcome"))
                    val failure = nullable(head.body, "failure")?.let { DesktopImportJobFailure.valueOf(it) }
                    val trackId = nullable(head.body, "track_id")
                    val hash = nullable(head.body, "sha256")
                    val size = if (head.body.getValue("byte_size") == JsonNull) null else number(head.body, "byte_size")
                    val link = nullable(head.body, "track_fact_id")
                    valid(head.body.keys == setOf("request", "outcome", "failure", "track_id", "sha256", "byte_size", "track_fact_id"))
                    if (outcome == DesktopImportJobOutcome.FAILED) {
                        valid(failure != null && trackId == null && hash == null && size == null && link == null)
                        valid(request == settlementRequest(settlement, null, failure))
                    } else {
                        valid(failure == null && trackId != null && hash != null && size == selection.byteSize)
                        val proposed = trackRequest(request)
                        valid(proposed.sha256 == hash && proposed.byteSize == size)
                        valid(request == settlementRequest(settlement, proposed, null))
                        val persistedId = checkNotNull(trackId)
                        valid(persistedId.matches(Regex("[A-Za-z0-9_-]{1,128}")))
                        val row = checkNotNull(dao.importJobTrackIdentity(persistedId))
                        valid(row.sha256 == hash && row.byteSize == size)
                        if (outcome == DesktopImportJobOutcome.IMPORTED) {
                            valid(trackId == proposed.id && link != null)
                            requireUuid(checkNotNull(link))
                            val linked = checkNotNull(dao.importJobLinkedFact(checkNotNull(link)))
                            val imported = snapshot(linked, "TRACK_IMPORTED")
                            // Compare the immutable before-image, never the track's editable labels.
                            valid(imported == obj("track_id" to text(proposed.id), "title" to text(proposed.title),
                                "artist" to text(proposed.artist), "duration_ms" to optional(proposed.durationMs?.toString()),
                                "sha256" to text(proposed.sha256), "byte_size" to num(proposed.byteSize)))
                            valid(linked.aggregateId == trackId)
                        } else valid(link == null)
                    }
                    candidates[selection.candidateId] = previous.copy(outcome = outcome, trackId = trackId,
                        sha256 = hash, byteSize = size, failure = failure, settledAttemptId = settlement.attemptId)
                    if (candidates.values.all { it.outcome in committed }) phase = DesktopImportJobPhase.COMPLETED
                    listOf(request)
                }
                "STOPPED" -> {
                    valid(group.size == 1)
                    val stop = DesktopImportJobStop(id, revision - 1, commandId,
                        DesktopImportJobStopReason.valueOf(string(request, "reason")))
                    valid(request == stopRequest(stop) && head.body == obj("request" to request))
                    phase = if (stop.reason == DesktopImportJobStopReason.CANCELLED) DesktopImportJobPhase.CANCELLED
                        else DesktopImportJobPhase.PAUSED
                    listOf(request)
                }
                else -> error("desktop_import_job_history_unsupported")
            }
            receipts[commandId] = signature
        }
        return History(DesktopImportJob(id, groups.last().key, phase, manifest.candidates.map { candidates.getValue(it.id) },
            attempt?.attemptId, confirmedAttempt = attempt), manifest, attempt, events.map { it.row }, receipts, attempts, previews)
    }

    private val committed: Set<DesktopImportJobOutcome> = setOf(DesktopImportJobOutcome.IMPORTED, DesktopImportJobOutcome.DUPLICATE)
    suspend fun create(dao: DesktopDao, input: DesktopImportJobManifest): DesktopImportJob {
        val manifest = freeze(input)
        if (dao.importJobFactCount(aggregate(manifest.jobId)) > 0) {
            val history = required(dao, manifest.jobId)
            conflict(history.manifest == manifest)
            return history.job
        }
        val bodies = listOf("CREATED" to obj("candidate_count" to num(manifest.candidates.size.toLong()))) +
            manifest.candidates.mapIndexed { index, candidate -> "CANDIDATE" to candidateBody(candidate, index) }
        append(dao, manifest.jobId, null, bodies)
        return required(dao, manifest.jobId).job
    }
    suspend fun confirm(dao: DesktopDao, input: DesktopImportJobAttempt): DesktopImportJob {
        val attempt = freeze(input)
        val history = required(dao, attempt.jobId)
        if (replay(history, attempt.commandId, attemptSignature(attempt))) return history.job
        fence(history, attempt.expectedRevision)
        conflict(attempt.attemptId !in history.attempts && attempt.previewId !in history.previews)
        check(history.attempts.size < DesktopImportJobLimits.ATTEMPTS) { "desktop_import_job_limit" }
        for (selection in attempt.selections) conflict(history.job.candidates.any {
            it.candidate.id == selection.candidateId && it.outcome !in committed
        })
        append(dao, attempt.jobId, history, listOf("ATTEMPT" to obj("request" to attemptRequest(attempt),
            "mapping_count" to num(attempt.selections.size.toLong()))) +
            attempt.selections.mapIndexed { index, selection -> "MAPPING" to mappingBody(selection, index) })
        return required(dao, attempt.jobId).job
    }
    fun replay(history: History, commandId: String, signature: List<JsonObject>): Boolean {
        val recorded = history.receipts[commandId] ?: return false
        conflict(recorded == signature)
        return true
    }
    private fun fence(history: History, revision: Long): Unit = conflict(
        history.job.revision == revision && history.job.phase != DesktopImportJobPhase.COMPLETED)

    suspend fun settle(
        dao: DesktopDao, input: DesktopImportJobSettlement, track: DesktopImportedTrack?, failure: DesktopImportJobFailure?,
    ): DesktopImportJob {
        val request = settlementRequest(input, track, failure)
        val history = required(dao, input.jobId)
        if (replay(history, input.commandId, listOf(request))) return history.job
        fence(history, input.expectedRevision)
        conflict(history.job.phase == DesktopImportJobPhase.RESELECTION_REQUIRED && history.attempt?.attemptId == input.attemptId)
        val selection = history.attempt?.selections?.singleOrNull { it.candidateId == input.candidateId }
        conflict(selection?.selectionId == input.selectionId)
        val candidate = history.job.candidates.single { it.candidate.id == input.candidateId }
        conflict(candidate.outcome !in committed)
        // A failure settles this attempt once; retry requires a new confirmed attempt, even if bytes later appear.
        conflict(candidate.settledAttemptId != input.attemptId)
        var imported: LocalFactRow? = null
        var row: TrackRow? = null
        val outcome = if (track == null) DesktopImportJobOutcome.FAILED else {
            conflict(track.byteSize == checkNotNull(selection).byteSize)
            val proposed = TrackRow(track.id, track.title, track.artist, track.durationMs, track.sha256, track.byteSize)
            val duplicate = dao.trackByHash(track.sha256)
            if (duplicate == null) {
                imported = DesktopLocalIntent.trackImported(proposed)
                row = proposed
                DesktopImportJobOutcome.IMPORTED
            } else {
                conflict(duplicate.byteSize == track.byteSize)
                row = duplicate
                DesktopImportJobOutcome.DUPLICATE
            }
        }
        val body = obj("request" to request, "outcome" to text(outcome.name), "failure" to optional(failure?.name),
            "track_id" to optional(row?.id), "sha256" to optional(row?.sha256), "byte_size" to optional(row?.byteSize?.toString()),
            "track_fact_id" to optional(imported?.changeId))
        val facts = prepare(input.jobId, history, listOf("SETTLED" to body))
        currentCoroutineContext().ensureActive()
        if (track != null) {
            if (imported != null) {
                dao.insertTrack(checkNotNull(row)); dao.putLocator(LocatorRow(track.id, track.managedFileName))
                dao.insertFact(imported)
            } else dao.addTrack(TrackRow(track.id, track.title, track.artist, track.durationMs, track.sha256, track.byteSize),
                LocatorRow(track.id, track.managedFileName))
        }
        for (fact in facts) { currentCoroutineContext().ensureActive(); dao.insertFact(fact) }
        return required(dao, input.jobId).job
    }
    suspend fun stop(dao: DesktopDao, stop: DesktopImportJobStop): DesktopImportJob {
        val request = stopRequest(stop)
        val history = required(dao, stop.jobId)
        if (replay(history, stop.commandId, listOf(request))) return history.job
        fence(history, stop.expectedRevision)
        append(dao, stop.jobId, history, listOf("STOPPED" to obj("request" to request)))
        return required(dao, stop.jobId).job
    }

    fun stopSignature(stop: DesktopImportJobStop): List<JsonObject> = listOf(stopRequest(stop))
    fun attemptSignature(attempt: DesktopImportJobAttempt): List<JsonObject> = listOf(attemptRequest(attempt)) +
        attempt.selections.mapIndexed { index, selection -> mappingBody(selection, index) }
    private fun attemptRequest(attempt: DesktopImportJobAttempt): JsonObject = obj(
        "command_id" to text(attempt.commandId), "expected_revision" to num(attempt.expectedRevision),
        "attempt_id" to text(attempt.attemptId), "preview_id" to text(attempt.previewId))
    private fun candidateBody(candidate: DesktopImportJobCandidate, ordinal: Int): JsonObject = obj(
        "candidate_id" to text(candidate.id), "title" to text(candidate.title), "byte_size" to num(candidate.byteSize),
        "ordinal" to num(ordinal.toLong()))
    private fun mappingBody(selection: DesktopImportJobReselection, ordinal: Int): JsonObject = obj(
        "candidate_id" to text(selection.candidateId), "selection_id" to text(selection.selectionId),
        "title" to text(selection.title), "byte_size" to num(selection.byteSize), "ordinal" to num(ordinal.toLong()))
    fun settlementRequest(
        input: DesktopImportJobSettlement, track: DesktopImportedTrack?, failure: DesktopImportJobFailure?,
    ): JsonObject {
        requireUuid(input.jobId); requireUuid(input.commandId); requireUuid(input.attemptId)
        requireUuid(input.candidateId); requireUuid(input.selectionId); valid(input.expectedRevision >= 0)
        valid((track == null) != (failure == null))
        if (track != null) {
            valid(track.id.matches(Regex("[A-Za-z0-9_-]{1,128}")) && track.sha256.matches(Regex("[0-9a-f]{64}")))
            fileSize(track.byteSize)
            valid(track.title.isNotBlank() && track.title.length <= 4096 && track.artist.length <= 4096 &&
                '\u0000' !in track.title && '\u0000' !in track.artist && (track.durationMs?.let { it >= 0 } != false))
            unicode(track.title); unicode(track.artist)
        }
        return obj("command_id" to text(input.commandId), "expected_revision" to num(input.expectedRevision),
            "attempt_id" to text(input.attemptId), "candidate_id" to text(input.candidateId),
            "selection_id" to text(input.selectionId), "failure" to optional(failure?.name),
            "track" to (track?.let { obj("track_id" to text(it.id), "title" to text(it.title), "artist" to text(it.artist),
                "duration_ms" to optional(it.durationMs?.toString()), "sha256" to text(it.sha256), "byte_size" to num(it.byteSize)) } ?: JsonNull))
    }
    private fun settlement(request: JsonObject, jobId: String): DesktopImportJobSettlement = DesktopImportJobSettlement(
        jobId, number(request, "expected_revision"), string(request, "command_id"), string(request, "attempt_id"),
        string(request, "candidate_id"), string(request, "selection_id"))
    private fun trackRequest(request: JsonObject): DesktopImportedTrack {
        val track = request.getValue("track") as JsonObject
        valid(track.keys == setOf("track_id", "title", "artist", "duration_ms", "sha256", "byte_size"))
        return DesktopImportedTrack(string(track, "track_id"), string(track, "title"), string(track, "artist"),
            nullable(track, "duration_ms")?.toLong(), string(track, "sha256"), number(track, "byte_size"), "")
    }
    private fun stopRequest(stop: DesktopImportJobStop): JsonObject {
        requireUuid(stop.jobId); requireUuid(stop.commandId); valid(stop.expectedRevision >= 0)
        return obj("command_id" to text(stop.commandId), "expected_revision" to num(stop.expectedRevision),
            "reason" to text(stop.reason.name))
    }
    private suspend fun append(dao: DesktopDao, id: String, history: History?, bodies: List<Pair<String, JsonObject>>): Unit {
        for (fact in prepare(id, history, bodies)) { currentCoroutineContext().ensureActive(); dao.insertFact(fact) }
    }
    private fun prepare(id: String, history: History?, bodies: List<Pair<String, JsonObject>>): List<LocalFactRow> {
        val previous = history?.facts.orEmpty()
        check(previous.size + bodies.size <= DesktopImportJobLimits.FACTS) { "desktop_import_job_limit" }
        var link = previous.lastOrNull()?.changeId
        val revision = history?.job?.revision?.plus(1) ?: 0
        val facts = bodies.mapIndexed { index, (type, body) ->
            DesktopLocalIntent.importJobFact(id, revision, previous.size.toLong() + index + 1, link, type, body)
                .also { link = it.changeId }
        }
        val bytes = previous.sumOf { it.payloadJson.toByteArray(Charsets.UTF_8).size.toLong() } +
            facts.sumOf { it.payloadJson.toByteArray(Charsets.UTF_8).size.toLong() }
        check(bytes <= DesktopImportJobLimits.HISTORY_BYTES) { "desktop_import_job_limit" }
        return facts
    }

    private fun snapshot(row: LocalFactRow, kind: String): JsonObject {
        valid(row.version == 1 && row.kind == kind && row.payloadJson.length <= DesktopImportJobLimits.FACT_BYTES &&
            row.payloadJson.toByteArray(Charsets.UTF_8).size <= DesktopImportJobLimits.FACT_BYTES)
        var depth = 0
        var quoted = false
        var escaped = false
        for (c in row.payloadJson) {
            if (quoted) {
                if (escaped) escaped = false else if (c == '\\') escaped = true else if (c == '"') quoted = false
            } else when (c) {
                '"' -> quoted = true
                '{', '[' -> { depth++; valid(depth <= 8) }
                '}', ']' -> { depth--; valid(depth >= 0) }
            }
        }
        valid(depth == 0 && !quoted)
        val document = Json.parseToJsonElement(row.payloadJson) as JsonObject
        valid(document.toString() == row.payloadJson && document.keys == setOf("version", "mutation", "snapshot") &&
            document.getValue("version") == JsonPrimitive(1) && string(document, "mutation") == kind)
        return document.getValue("snapshot") as JsonObject
    }
    private fun string(value: JsonObject, key: String): String {
        val primitive = value.getValue(key) as JsonPrimitive
        valid(primitive.isString)
        unicode(primitive.content)
        return primitive.content
    }
    private fun nullable(value: JsonObject, key: String): String? =
        if (value.getValue(key) == JsonNull) null else string(value, key)
    private fun number(value: JsonObject, key: String): Long {
        val text = string(value, key)
        val number = text.toLong()
        valid(number >= 0 && number.toString() == text)
        return number
    }
    private fun obj(vararg fields: Pair<String, JsonElement>): JsonObject = JsonObject(fields.toMap().toSortedMap())
    private fun text(value: String): JsonPrimitive = JsonPrimitive(value)
    private fun num(value: Long): JsonPrimitive = text(value.toString())
    private fun optional(value: String?): JsonElement = value?.let(::text) ?: JsonNull
}
