package app.autplay.data.desktop.account

/**
 * First bounded J1 foundation, NOT the complete account projection schema.
 * No migration from Room, no guessed future tables, no arbitrary JSON/secret/source/media columns.
 * Future schema changes need numbered preserving migrations; unknown/downgrade opens are refused.
 * All immutable evidence is RESTRICTed; no TTL, LRU, ACK purge or DELETE API is provided.
 */
internal object DesktopAccountSchema {
    const val FORMAT: String = "autplay.desktop.account"
    const val VERSION: Long = 1
    const val APPLICATION_ID: Long = 0x41504A31

    val create: List<String> = listOf(
        """CREATE TABLE j_store (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), store_id TEXT NOT NULL UNIQUE,
            format TEXT NOT NULL, schema_version INTEGER NOT NULL,
            last_wall_ms INTEGER NOT NULL CHECK(last_wall_ms>=0))""",
        """CREATE TABLE j_scope (
            scope_id TEXT PRIMARY KEY, account_id TEXT, server_instance_id TEXT, user_id TEXT,
            catalog_owner_scope TEXT, state TEXT NOT NULL,
            CHECK(account_id IS NOT NULL OR server_instance_id IS NOT NULL),
            CHECK((server_instance_id IS NULL AND user_id IS NULL AND catalog_owner_scope IS NULL)
               OR (server_instance_id IS NOT NULL AND user_id IS NOT NULL AND catalog_owner_scope IS NULL)),
            UNIQUE(scope_id,server_instance_id,user_id), UNIQUE(server_instance_id,user_id))""",
        "CREATE UNIQUE INDEX j_root_owner ON j_scope(account_id) WHERE server_instance_id IS NULL",
        """CREATE TABLE j_lineage (
            scope_id TEXT NOT NULL, lineage_id TEXT NOT NULL, server_instance_id TEXT NOT NULL,
            user_id TEXT NOT NULL, device_id TEXT NOT NULL, journal_epoch TEXT NOT NULL,
            next_sequence INTEGER NOT NULL CHECK(next_sequence BETWEEN 1 AND 9007199254740991),
            ack_checkpoint INTEGER NOT NULL CHECK(ack_checkpoint>=0 AND ack_checkpoint<next_sequence),
            PRIMARY KEY(scope_id,lineage_id), UNIQUE(server_instance_id,device_id),
            UNIQUE(server_instance_id,user_id,device_id,journal_epoch),
            UNIQUE(scope_id,lineage_id,user_id,device_id),
            FOREIGN KEY(scope_id,server_instance_id,user_id)
                REFERENCES j_scope(scope_id,server_instance_id,user_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_profile_alias (
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL UNIQUE, lineage_id TEXT,
            PRIMARY KEY(scope_id,profile_id), UNIQUE(scope_id,profile_id,lineage_id),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,lineage_id) REFERENCES j_lineage(scope_id,lineage_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_binding_intent (
            intent_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, pending_slot TEXT NOT NULL,
            authority_revision INTEGER NOT NULL, prior_activation_revision INTEGER NOT NULL,
            exact_intent BLOB NOT NULL CHECK(length(exact_intent) BETWEEN 2 AND 4096),
            stage TEXT NOT NULL CHECK(stage IN ('PREPARED','FINALIZED','CANCELLED_UNSENT')),
            descendant_activation TEXT UNIQUE)""",
        "CREATE UNIQUE INDEX j_one_binding_intent ON j_binding_intent((1)) WHERE stage='PREPARED'",
        """CREATE TABLE j_scope_prepare (
            activation_id TEXT PRIMARY KEY, binding_commit TEXT NOT NULL, intent_id TEXT UNIQUE,
            scope_id TEXT NOT NULL, profile_id TEXT NOT NULL,
            device_id TEXT, journal_epoch TEXT, identity_epoch INTEGER, server_thumbprint TEXT,
            device_key_thumbprint TEXT, authority_revision INTEGER NOT NULL,
            prior_activation_revision INTEGER NOT NULL, stage TEXT NOT NULL,
            CHECK(authority_revision BETWEEN 1 AND 9007199254740991),
            CHECK(prior_activation_revision BETWEEN 0 AND 9007199254740990),
            CHECK((device_id IS NULL AND journal_epoch IS NULL AND identity_epoch IS NULL
                AND server_thumbprint IS NULL AND device_key_thumbprint IS NULL)
              OR (device_id IS NOT NULL AND journal_epoch IS NOT NULL AND identity_epoch IS NOT NULL
                AND server_thumbprint IS NOT NULL AND device_key_thumbprint IS NOT NULL)),
            FOREIGN KEY(scope_id) REFERENCES j_scope(scope_id) ON DELETE RESTRICT,
            FOREIGN KEY(intent_id) REFERENCES j_binding_intent(intent_id) ON DELETE RESTRICT)""",
        "CREATE UNIQUE INDEX j_one_preparation ON j_scope_prepare((1)) WHERE stage='PREPARED'",
        """CREATE TABLE j_activation (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope_id TEXT, profile_id TEXT,
            binding_commit TEXT, activation_id TEXT,
            revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 9007199254740991),
            work_revision INTEGER NOT NULL CHECK(work_revision BETWEEN 0 AND 9007199254740991),
            authority_revision INTEGER, boot_id TEXT, state TEXT NOT NULL,
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT,
            FOREIGN KEY(activation_id) REFERENCES j_scope_prepare(activation_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_event (
            scope_id TEXT NOT NULL, event_id TEXT NOT NULL, lineage_id TEXT NOT NULL,
            profile_id TEXT NOT NULL, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 9007199254740991),
            idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 200),
            event_type TEXT NOT NULL, aggregate_type TEXT NOT NULL, aggregate_local_id TEXT NOT NULL,
            canonical_bytes BLOB NOT NULL CHECK(length(canonical_bytes) BETWEEN 1 AND 8388608),
            request_hash BLOB NOT NULL CHECK(length(request_hash)=32),
            PRIMARY KEY(scope_id,event_id), UNIQUE(scope_id,lineage_id,sequence),
            UNIQUE(scope_id,lineage_id,idempotency_key),
            FOREIGN KEY(scope_id,profile_id,lineage_id)
                REFERENCES j_profile_alias(scope_id,profile_id,lineage_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,lineage_id,user_id,device_id)
                REFERENCES j_lineage(scope_id,lineage_id,user_id,device_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_event_delivery (
            scope_id TEXT NOT NULL, event_id TEXT NOT NULL, state TEXT NOT NULL,
            attempt INTEGER NOT NULL CHECK(attempt BETWEEN 0 AND 8), next_retry_ms INTEGER NOT NULL,
            lease_boot TEXT, lease_token TEXT, lease_revision INTEGER NOT NULL DEFAULT 0,
            lease_expires_ms INTEGER, last_error TEXT,
            PRIMARY KEY(scope_id,event_id),
            FOREIGN KEY(scope_id,event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT)""",
        "CREATE INDEX j_delivery_due ON j_event_delivery(scope_id,state,next_retry_ms)",
        """CREATE TABLE j_track_ref (
            scope_id TEXT NOT NULL, track_ref_id TEXT NOT NULL, operation_id TEXT NOT NULL,
            profile_id TEXT NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL,
            occurred_at_ms INTEGER NOT NULL, server_track_ref_id TEXT, observed_version INTEGER,
            PRIMARY KEY(scope_id,track_ref_id), UNIQUE(scope_id,operation_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_library_entry (
            scope_id TEXT NOT NULL, entry_id TEXT NOT NULL, track_ref_id TEXT NOT NULL,
            PRIMARY KEY(scope_id,entry_id), UNIQUE(scope_id,track_ref_id),
            FOREIGN KEY(scope_id,track_ref_id) REFERENCES j_track_ref(scope_id,track_ref_id) ON DELETE RESTRICT)""",
        """CREATE TABLE j_local_intent (
            scope_id TEXT NOT NULL, change_id TEXT NOT NULL, profile_id TEXT NOT NULL,
            aggregate_local_id TEXT NOT NULL, event_type TEXT NOT NULL, aggregate_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL, occurred_at_ms INTEGER NOT NULL,
            canonical_payload BLOB NOT NULL CHECK(length(canonical_payload)<=262144),
            state TEXT NOT NULL, materialized_event_id TEXT,
            PRIMARY KEY(scope_id,change_id), UNIQUE(scope_id,materialized_event_id),
            FOREIGN KEY(scope_id,profile_id) REFERENCES j_profile_alias(scope_id,profile_id) ON DELETE RESTRICT,
            FOREIGN KEY(scope_id,materialized_event_id) REFERENCES j_event(scope_id,event_id) ON DELETE RESTRICT)""",
        """CREATE TRIGGER j_store_identity BEFORE UPDATE OF singleton,store_id,format,schema_version
            ON j_store BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_scope_identity BEFORE UPDATE OF scope_id,account_id,server_instance_id,user_id,catalog_owner_scope
            ON j_scope BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_lineage_identity BEFORE UPDATE OF scope_id,lineage_id,server_instance_id,user_id,device_id,journal_epoch
            ON j_lineage BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_lineage_counter BEFORE UPDATE OF next_sequence,ack_checkpoint ON j_lineage
            WHEN NEW.next_sequence<OLD.next_sequence OR NEW.ack_checkpoint<OLD.ack_checkpoint
            BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_binding_intent_identity BEFORE UPDATE OF intent_id,operation_id,pending_slot,
            authority_revision,prior_activation_revision,exact_intent ON j_binding_intent
            BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_binding_descendant BEFORE UPDATE OF descendant_activation,stage ON j_binding_intent
            WHEN OLD.stage<>'PREPARED' OR NOT ((NEW.stage='FINALIZED' AND NEW.descendant_activation IS NOT NULL)
                OR (NEW.stage='CANCELLED_UNSENT' AND NEW.descendant_activation IS NULL))
            BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_preparation_identity BEFORE UPDATE OF activation_id,binding_commit,intent_id,scope_id,profile_id,device_id,journal_epoch,
            identity_epoch,server_thumbprint,device_key_thumbprint,authority_revision,prior_activation_revision
            ON j_scope_prepare BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
        """CREATE TRIGGER j_intent_identity BEFORE UPDATE OF scope_id,change_id,profile_id,aggregate_local_id,
            event_type,aggregate_type,schema_version,occurred_at_ms,canonical_payload
            ON j_local_intent BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END""",
    ) + listOf("j_store", "j_scope", "j_lineage", "j_profile_alias", "j_binding_intent", "j_scope_prepare", "j_event", "j_local_intent").map { table ->
        // Static schema-owned names only, never user input or an exposed SQL API.
        "CREATE TRIGGER ${table}_retain BEFORE DELETE ON $table BEGIN SELECT RAISE(ABORT,'ACCOUNT_RETAINED'); END"
    } + listOf("j_profile_alias", "j_event").map { table ->
        "CREATE TRIGGER ${table}_immutable BEFORE UPDATE ON $table BEGIN SELECT RAISE(ABORT,'ACCOUNT_IMMUTABLE'); END"
    }
}
