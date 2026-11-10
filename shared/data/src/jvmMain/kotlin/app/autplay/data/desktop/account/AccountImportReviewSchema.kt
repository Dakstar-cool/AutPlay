package app.autplay.data.desktop.account

/** I1 additions to unpublished J schema1; installed only through the existing J owner. */
internal object AccountImportReviewSchema {
    const val COMPONENT: String = "account-import-review-v1"
    val tables: List<AccountTableShape> = listOf(
        AccountTableShape("j_import_review_command", "scope_id,operation_id",
            "scope_id,operation_id,format_version,work_id,review_id,local_job_id,local_entry_id,candidate_id,server_job_id,server_entry_id,mapping_rank,mapping_predecessor,mapping_operation,predecessor,action,selected_rank,evidence_sha256,idempotency_key,binding_commit,authority_revision,activation_revision,revision,state,exact_body,outcome,reference_kind,entry_context_id", 5,
            """CREATE TABLE j_import_review_command (
                scope_id TEXT NOT NULL,operation_id TEXT NOT NULL,format_version INTEGER NOT NULL,
                work_id TEXT NOT NULL,review_id TEXT,local_job_id TEXT NOT NULL,local_entry_id TEXT NOT NULL,
                candidate_id TEXT,server_job_id TEXT NOT NULL,server_entry_id TEXT NOT NULL,mapping_rank INTEGER,
                mapping_predecessor TEXT,mapping_operation TEXT NOT NULL,predecessor TEXT NOT NULL,action TEXT NOT NULL,
                selected_rank INTEGER,evidence_sha256 TEXT,idempotency_key TEXT NOT NULL,binding_commit TEXT NOT NULL,
                authority_revision INTEGER NOT NULL,activation_revision INTEGER NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,
                exact_body BLOB NOT NULL CHECK(length(exact_body)<=4096),outcome BLOB CHECK(length(outcome)<=4096),
                reference_kind TEXT NOT NULL DEFAULT 'MAPPED_CANDIDATE',entry_context_id TEXT,
                PRIMARY KEY(scope_id,operation_id),UNIQUE(scope_id,work_id),
                FOREIGN KEY(scope_id,review_id) REFERENCES j_import_review(scope_id,review_id) ON DELETE RESTRICT,
                FOREIGN KEY(scope_id,work_id) REFERENCES j_work(scope_id,work_id) ON DELETE RESTRICT)"""),
        AccountTableShape("j_import_review_state", "scope_id,server_job_id,server_entry_id",
            "scope_id,server_job_id,server_entry_id,operation_id,predecessor,status,recording_id", 2,
            """CREATE TABLE j_import_review_state (
                scope_id TEXT NOT NULL,server_job_id TEXT NOT NULL,server_entry_id TEXT NOT NULL,operation_id TEXT NOT NULL,
                predecessor TEXT NOT NULL,status TEXT NOT NULL,recording_id TEXT,
                PRIMARY KEY(scope_id,server_job_id,server_entry_id),
                FOREIGN KEY(scope_id,operation_id) REFERENCES j_import_review_command(scope_id,operation_id) ON DELETE RESTRICT)"""),
    )
    val create: List<String> = tables.map { it.ddl } + listOf(
        "CREATE UNIQUE INDEX j_import_review_one_pending ON j_import_review_command(scope_id,server_job_id,server_entry_id) WHERE state NOT IN ('COMMITTED','REFUSED')",
        "CREATE UNIQUE INDEX j_import_review_one_local_pending ON j_import_review_command(scope_id,local_job_id,local_entry_id) WHERE state NOT IN ('COMMITTED','REFUSED')",
        """CREATE TRIGGER j_import_review_command_exact BEFORE UPDATE ON j_import_review_command
            WHEN NEW.scope_id<>OLD.scope_id OR NEW.operation_id<>OLD.operation_id OR NEW.format_version<>OLD.format_version
            OR NEW.work_id<>OLD.work_id OR NEW.review_id IS NOT OLD.review_id OR NEW.local_job_id<>OLD.local_job_id
            OR NEW.local_entry_id<>OLD.local_entry_id OR NEW.candidate_id IS NOT OLD.candidate_id OR NEW.server_job_id<>OLD.server_job_id
            OR NEW.server_entry_id<>OLD.server_entry_id OR NEW.mapping_rank IS NOT OLD.mapping_rank
            OR NEW.mapping_predecessor IS NOT OLD.mapping_predecessor OR NEW.mapping_operation<>OLD.mapping_operation
            OR NEW.predecessor<>OLD.predecessor OR NEW.action<>OLD.action OR NEW.selected_rank IS NOT OLD.selected_rank
            OR NEW.evidence_sha256 IS NOT OLD.evidence_sha256 OR NEW.idempotency_key<>OLD.idempotency_key
            OR NEW.binding_commit<>OLD.binding_commit OR NEW.authority_revision<>OLD.authority_revision
            OR NEW.activation_revision<>OLD.activation_revision OR NEW.exact_body<>OLD.exact_body
            OR NEW.reference_kind<>OLD.reference_kind OR NEW.entry_context_id IS NOT OLD.entry_context_id
            OR OLD.state IN ('COMMITTED','REFUSED') OR NEW.revision<>OLD.revision+1
            BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
    ) + tables.map { "CREATE TRIGGER ${it.name}_retain BEFORE DELETE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END" }
}
