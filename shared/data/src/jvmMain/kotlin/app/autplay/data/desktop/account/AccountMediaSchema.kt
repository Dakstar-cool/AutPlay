package app.autplay.data.desktop.account

/** M1's named non-secret logical records, normalized inside J's existing schema1. */
internal object AccountMediaSchema {
    const val COMPONENT:String="remote-media-records-v1"
    private val common="format_version INTEGER NOT NULL CHECK(format_version=1),store_id TEXT NOT NULL,scope_id TEXT NOT NULL,revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 9007199254740991)"
    private fun table(name:String,keys:String,fields:String,constraints:String=""):AccountTableShape {
        val columns=("format_version,store_id,scope_id,revision,"+fields.split(',').joinToString(","){it.trim().substringBefore(' ')})
        return AccountTableShape(name,keys,columns,3,"CREATE TABLE $name ($common,$fields,PRIMARY KEY($keys),FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT$constraints)")
    }
    val tables:List<AccountTableShape> = listOf(
        table("j_media_projection","scope_id,track_id","track_id TEXT NOT NULL,projection_revision INTEGER NOT NULL"),
        table("j_media_redirect","scope_id,aggregate_type,alias_id","aggregate_type TEXT NOT NULL,alias_id TEXT NOT NULL,projection_revision INTEGER NOT NULL"),
        table("j_download_intent","scope_id,intent_id","""intent_id TEXT NOT NULL,local_track_id TEXT NOT NULL,
            user_track_id TEXT,utr_version INTEGER,recording_id TEXT,variant_id TEXT,source_evidence_id TEXT,
            storage_class TEXT NOT NULL,desired_state TEXT NOT NULL,coarse_state TEXT NOT NULL,continuation_mode TEXT NOT NULL,
            stop_reason TEXT,current_attempt INTEGER NOT NULL,next_eligible_ms INTEGER,reserved_bytes INTEGER NOT NULL,completed_copy_id TEXT""",
            ",FOREIGN KEY(scope_id,local_track_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,source_evidence_id) REFERENCES j_media_source_evidence(scope_id,evidence_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"),
        table("j_media_source_evidence","scope_id,evidence_id","""evidence_id TEXT NOT NULL,intent_id TEXT NOT NULL,observation_kind TEXT NOT NULL,
            binding_commit TEXT NOT NULL,local_track_id TEXT NOT NULL,projection_revision INTEGER NOT NULL,utr_version INTEGER NOT NULL,
            recording_id TEXT NOT NULL,owner_kind TEXT NOT NULL,portable_account_id TEXT,server_instance_id TEXT,server_user_id TEXT,
            owner_binding_commit TEXT,snapshot_document BLOB NOT NULL CHECK(length(snapshot_document)<=4096)""",
            ",FOREIGN KEY(scope_id,intent_id) REFERENCES j_download_intent(scope_id,intent_id) ON DELETE RESTRICT"),
        table("j_media_copy","scope_id,copy_id","""copy_id TEXT NOT NULL,local_track_id TEXT NOT NULL,source_user_track_id TEXT,source_utr_version INTEGER,
            source_recording_id TEXT,source_variant_id TEXT,source_evidence_id TEXT,etag TEXT,digest_sha256 TEXT NOT NULL,total_bytes INTEGER NOT NULL,
            media_type TEXT NOT NULL,format_suffix TEXT NOT NULL,storage_class TEXT NOT NULL,integrity_state TEXT NOT NULL,publication_state TEXT NOT NULL,
            locator_id TEXT NOT NULL,completion_revision INTEGER NOT NULL,publication_id TEXT NOT NULL""",
            ",FOREIGN KEY(scope_id,source_evidence_id) REFERENCES j_media_source_evidence(scope_id,evidence_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,publication_id) REFERENCES j_media_publication(scope_id,publication_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"),
        table("j_media_copy_attachment","scope_id,copy_id","""copy_id TEXT NOT NULL,admission_evidence_id TEXT,local_track_id TEXT,user_track_id TEXT,utr_version INTEGER,
            recording_id TEXT,projection_revision INTEGER,redirect_projection_revision INTEGER,state TEXT NOT NULL""",
            ",FOREIGN KEY(scope_id,copy_id) REFERENCES j_media_copy(scope_id,copy_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,admission_evidence_id) REFERENCES j_media_source_evidence(scope_id,evidence_id) ON DELETE RESTRICT"),
        table("j_media_copy_ownership","scope_id,copy_id","""copy_id TEXT NOT NULL,owner_kind TEXT NOT NULL,portable_account_id TEXT,
            server_instance_id TEXT,server_user_id TEXT,owner_binding_commit TEXT,k_owner_ref TEXT NOT NULL,origin_veto_ref TEXT,
            provenance_authority_revision INTEGER NOT NULL,acquisition_kind TEXT NOT NULL,acquisition_id TEXT NOT NULL,
            completion_revision INTEGER NOT NULL,publication_id TEXT NOT NULL,state TEXT NOT NULL""",
            ",FOREIGN KEY(scope_id,copy_id) REFERENCES j_media_copy(scope_id,copy_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,publication_id) REFERENCES j_media_publication(scope_id,publication_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"),
        table("j_media_prefix_owner","scope_id,prefix_id","prefix_id TEXT NOT NULL,intent_id TEXT NOT NULL,source_evidence_id TEXT NOT NULL",
            ",FOREIGN KEY(scope_id,intent_id) REFERENCES j_download_intent(scope_id,intent_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,source_evidence_id) REFERENCES j_media_source_evidence(scope_id,evidence_id) ON DELETE RESTRICT"),
        table("j_media_attempt","scope_id,intent_id,attempt","""intent_id TEXT NOT NULL,attempt INTEGER NOT NULL,boot_id TEXT NOT NULL,
            source_generation INTEGER NOT NULL,intent_revision INTEGER NOT NULL,attempt_lease_revision INTEGER NOT NULL,source_evidence_id TEXT NOT NULL,
            admission_evidence_id TEXT,operation_id TEXT NOT NULL,resource_activation_id TEXT,resource_generation INTEGER,etag TEXT,digest_sha256 TEXT,total_bytes INTEGER,
            prefix_id TEXT NOT NULL,confirmed_prefix_bytes INTEGER NOT NULL,started_ms INTEGER NOT NULL,deadline_ms INTEGER NOT NULL,
            settled_bytes INTEGER NOT NULL,state TEXT NOT NULL,stop_reason TEXT,replay_401_used INTEGER NOT NULL,reconnect_used INTEGER NOT NULL,no_progress INTEGER NOT NULL""",
            ",FOREIGN KEY(scope_id,intent_id) REFERENCES j_download_intent(scope_id,intent_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,prefix_id) REFERENCES j_media_prefix_owner(scope_id,prefix_id) ON DELETE RESTRICT,FOREIGN KEY(scope_id,admission_evidence_id) REFERENCES j_media_source_evidence(scope_id,evidence_id) ON DELETE RESTRICT"),
        table("j_media_prefix_block","scope_id,prefix_id,block_index","prefix_id TEXT NOT NULL,block_index INTEGER NOT NULL,offset INTEGER NOT NULL,length INTEGER NOT NULL,digest_sha256 TEXT NOT NULL",
            ",UNIQUE(scope_id,prefix_id,offset),FOREIGN KEY(scope_id,prefix_id) REFERENCES j_media_prefix_owner(scope_id,prefix_id) ON DELETE RESTRICT"),
        table("j_media_publication","scope_id,publication_id","""publication_id TEXT NOT NULL,copy_id TEXT NOT NULL,intent_id TEXT,attempt INTEGER,
            source_evidence_id TEXT,expected_intent_revision INTEGER,expected_attempt_lease_revision INTEGER,expected_owner_revision INTEGER NOT NULL,
            expected_origin_veto_revision INTEGER,expected_projection_revision INTEGER,attachment_outcome TEXT NOT NULL,materialization_revision INTEGER,
            locator_id TEXT NOT NULL,prefix_id TEXT,completion_revision INTEGER NOT NULL,stage TEXT NOT NULL""",
            ",FOREIGN KEY(scope_id,copy_id) REFERENCES j_media_copy(scope_id,copy_id) ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,FOREIGN KEY(scope_id,intent_id,attempt) REFERENCES j_media_attempt(scope_id,intent_id,attempt) ON DELETE RESTRICT"),
    )
    val integerColumns:Set<String> = setOf("format_version","revision","projection_revision","utr_version","source_utr_version","current_attempt","next_eligible_ms","reserved_bytes",
        "total_bytes","completion_revision","redirect_projection_revision","provenance_authority_revision","attempt","source_generation","intent_revision","attempt_lease_revision",
        "resource_generation","confirmed_prefix_bytes","started_ms","deadline_ms","settled_bytes","replay_401_used","reconnect_used","no_progress","block_index","offset","length",
        "expected_intent_revision","expected_attempt_lease_revision","expected_owner_revision","expected_origin_veto_revision","expected_projection_revision","materialization_revision")
    private val immutable=setOf("j_media_source_evidence","j_media_prefix_owner","j_media_prefix_block","j_media_redirect")
    val create:List<String> = tables.map{it.ddl}+tables.map{
        "CREATE TRIGGER ${it.name}_retain BEFORE DELETE ON ${it.name} BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"
    }+immutable.map{"CREATE TRIGGER ${it}_immutable BEFORE UPDATE ON $it BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END"}
}
