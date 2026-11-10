package app.autplay.application.desktop.account

import kotlinx.serialization.json.*

public enum class AccountImportReviewAction { ACCEPT, REJECT, KEEP_UNRESOLVED, CREATE_RECORDING }

/** Exact explicitly admitted local/owned-entry identity; no candidate is implied by this link. */
public sealed interface AccountImportReviewReference {
    public val reviewId: AccountUuid
    public val localJobId: AccountUuid
    public val localEntryId: AccountUuid
    public val serverJobId: AccountUuid
    public val serverEntryId: AccountUuid
    public val predecessor: AccountUuid?
    public val operationId: AccountUuid
}

/** I1 entry-context provenance for an initial zero-candidate decision, independent of legacy ranks. */
public data class AccountImportEntryReview(
    override val reviewId: AccountUuid, override val localJobId: AccountUuid,
    override val localEntryId: AccountUuid, override val serverJobId: AccountUuid,
    override val serverEntryId: AccountUuid, override val predecessor: AccountUuid,
    override val operationId: AccountUuid,
) : AccountImportReviewReference {
    override fun toString(): String = "AccountImportEntryReview"
}

/** The only persisted body is the existing import review POST, never a generic HTTP command. */
public data class AccountImportReviewCommand(
    public val operationId: AccountUuid,
    public val workId: AccountUuid,
    public val reference: AccountImportReviewReference,
    public val predecessor: AccountUuid,
    public val action: AccountImportReviewAction,
    public val selectedRank: Int?,
    public val selectedEvidenceSha256: String?,
    public val idempotencyKey: String,
    public val bindingCommit: AccountUuid,
    public val authorityRevision: Long,
    public val activationRevision: Long,
) {
    init {
        require(idempotencyKey.length in 1..120 && idempotencyKey.all { it.code in 33..126 })
        require(idempotencyKey == operationId.value)
        when (reference) {
            is AccountImportReview -> require(reference.rank in 1..100)
            is AccountImportEntryReview -> require(predecessor == reference.predecessor && selectedRank == null &&
                selectedEvidenceSha256 == null && action in setOf(AccountImportReviewAction.KEEP_UNRESOLVED, AccountImportReviewAction.CREATE_RECORDING))
        }
        require(authorityRevision in 1..DesktopAccountLimits.MAX_REVISION && activationRevision in 1..DesktopAccountLimits.MAX_REVISION)
        val selected = action in setOf(AccountImportReviewAction.ACCEPT, AccountImportReviewAction.REJECT)
        require(selected == (selectedRank != null) && selected == (selectedEvidenceSha256 != null))
        require(selectedRank == null || selectedRank in 1..100)
        require(selectedEvidenceSha256 == null || selectedEvidenceSha256.matches(Regex("[0-9a-f]{64}")))
    }
    public fun bodyCopy(): ByteArray = buildJsonObject {
        put("predecessor_decision_id", predecessor.value); put("action", action.name)
        put("selected_rank", selectedRank?.let(::JsonPrimitive) ?: JsonNull)
        put("idempotency_key", idempotencyKey)
    }.toString().toByteArray(Charsets.UTF_8).also { require(it.size <= 4096) }
    override fun toString(): String = "AccountImportReviewCommand"
}

public data class AccountImportReviewOutcome(public val decisionId: AccountUuid,
    public val importEntryId: AccountUuid, public val status: String,
    public val recordingId: AccountUuid?, public val replayed: Boolean) {
    init { require(status in setOf("MANUAL_MATCH", "MANUAL_UNRESOLVED", "REVIEW_REQUIRED")) }
    public fun bodyCopy(): ByteArray = buildJsonObject {
        put("decision_id", decisionId.value); put("import_entry_id", importEntryId.value)
        put("status", status); put("recording_id", recordingId?.value?.let(::JsonPrimitive) ?: JsonNull)
        put("replayed", replayed)
    }.toString().toByteArray(Charsets.UTF_8).also { require(it.size <= 4096) }
    override fun toString(): String = "AccountImportReviewOutcome"
}

public data class AccountImportReviewPending(public val command: AccountImportReviewCommand,
    public val revision: Long, public val rawState: String, public val outcome: AccountImportReviewOutcome?,
    public val rawWorkState: String, public val attempts: Int, public val nextAttemptAtMs: Long) {
    public val terminal: Boolean get() = rawState in setOf("COMMITTED", "REFUSED")
    public val retryExhausted: Boolean get() = attempts>=8 && rawWorkState in setOf("PENDING","UNCERTAIN","DEAD_LETTER")
    public val available: Boolean get() = rawState in setOf("PREPARED", "SENT", "OUTCOME_PENDING", "COMMITTED", "REFUSED") &&
        rawWorkState in setOf("PENDING","RUNNING","UNCERTAIN","DEAD_LETTER","COMPLETED")
    override fun toString(): String = "AccountImportReviewPending"
}

public interface DesktopAccountImportReviewCommands {
    /** Publishes the immutable command and IMPORT_REVIEW work in the same budgeted transaction. */
    public suspend fun prepare(lease: AccountLease, command: AccountImportReviewCommand): AccountImportReviewPending
    public suspend fun read(lease: AccountLease, operationId: AccountUuid): AccountImportReviewPending?
    public suspend fun readPage(lease: AccountLease, after: AccountUuid?, limit: Int = 100): List<AccountImportReviewPending>
    /** Explicit human retry only; opens a new eight-attempt window for the SAME command. */
    public suspend fun retryDeadLetter(lease: AccountLease, operationId: AccountUuid,
        commandId: AccountUuid, expectedRevision: Long): AccountImportReviewPending
    /** Exact claim CAS; restart may reclaim only the same retained command after the old boot. */
    public suspend fun claim(lease: AccountLease, operationId: AccountUuid,
        commandId: AccountUuid, expectedRevision: Long): AccountWorkClaim?
    /** Must commit SENT before invoking HTTP. Never changes the command or its idempotency key. */
    public suspend fun markSent(lease: AccountLease, operationId: AccountUuid,
        commandId: AccountUuid, claim: AccountWorkClaim, expectedRevision: Long): AccountImportReviewPending
    /** Drained response plus owned projection and work settlement are one SQL effect. */
    public suspend fun settle(lease: AccountLease, operationId: AccountUuid,
        commandId: AccountUuid, claim: AccountWorkClaim, expectedRevision: Long,
        outcome: AccountImportReviewOutcome?, refused: Boolean = false, retryAfterMs: Long = 0): AccountImportReviewPending
}
