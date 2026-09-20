-- Frozen owner inventory for migration 0053. Shared catalog, Vault bytes and ML artifacts survive.
CREATE FUNCTION app_private.guard_purge_receipt() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog AS $$
BEGIN
 IF TG_OP<>'INSERT' OR NOT EXISTS(SELECT 1 FROM app_private.privacy_purge_context
    WHERE transaction_id=pg_current_xact_id() AND request_id=NEW.request_id) THEN
  RAISE EXCEPTION 'privacy_purge_receipt_immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_purge_receipt BEFORE INSERT OR UPDATE OR DELETE
 ON account.account_purge_receipt FOR EACH ROW EXECUTE FUNCTION app_private.guard_purge_receipt();

CREATE FUNCTION app_private.guard_catalog_actor_erasure() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='INSERT' AND NEW.actor_erased_at IS NOT NULL THEN
  RAISE EXCEPTION 'Attribution erasure requires accepted privacy deletion';
 END IF;
 IF TG_OP='UPDATE' AND ROW(NEW.actor_user_id,NEW.actor_erased_at) IS DISTINCT FROM
    ROW(OLD.actor_user_id,OLD.actor_erased_at) AND
    (NEW.actor_erased_at IS NOT NULL OR OLD.actor_erased_at IS NOT NULL) THEN
  IF NOT app_private.privacy_owner_allowed(OLD.actor_user_id)
     OR NEW.actor_user_id IS NOT NULL OR NEW.actor_erased_at IS NULL
     OR NEW.reason<>'PRIVACY_ERASED'
     OR to_jsonb(NEW)-ARRAY['actor_user_id','actor_erased_at','reason'] <>
        to_jsonb(OLD)-ARRAY['actor_user_id','actor_erased_at','reason'] THEN
   RAISE EXCEPTION 'Attribution erasure requires accepted privacy deletion';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER guard_catalog_actor_erasure BEFORE INSERT OR UPDATE ON audit.catalog_change_set
 FOR EACH ROW EXECUTE FUNCTION app_private.guard_catalog_actor_erasure();

CREATE FUNCTION account.verify_account_purge_ready(target uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN
 PERFORM job_id FROM jobs.job WHERE user_id=target ORDER BY job_id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM account.account_deletion_hold WHERE user_id=target) THEN
  RAISE EXCEPTION 'privacy_deletion_held' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM account.resource_io_execution e JOIN account.resource_admission a
       ON a.operation_id=e.operation_id WHERE a.user_id=target AND
       (e.state<>'CLOSED' OR e.closed_at IS NULL))
 OR EXISTS(SELECT 1 FROM vault.provider_staging WHERE user_id=target AND
       (closed_at IS NULL OR NOT (state='CLEANED' OR
        (state='HANDED_OFF' AND scratch_retired_at IS NOT NULL))))
 OR EXISTS(SELECT 1 FROM vault.provider_maintenance m WHERE
       (m.provider_execution_id IN (SELECT execution_id FROM vault.provider_staging WHERE user_id=target)
       OR m.upload_claim_id IN (SELECT upload_session_id FROM vault.upload_session WHERE user_id=target))
       AND (m.state<>'CLOSED' OR m.closed_at IS NULL))
 OR EXISTS(SELECT 1 FROM vault.ingest_execution e JOIN vault.upload_session u
       ON u.upload_session_id=e.upload_session_id WHERE u.user_id=target AND
       (e.state<>'CLOSED' OR e.closed_at IS NULL))
 OR EXISTS(SELECT 1 FROM vault.ingest_cleanup_execution e JOIN vault.upload_session u
       ON u.upload_session_id=e.claim_id WHERE u.user_id=target AND
       (e.state<>'CLOSED' OR e.closed_at IS NULL))
 OR EXISTS(SELECT 1 FROM library.metadata_execution WHERE user_id=target AND
       (state<>'CLOSED' OR closed_at IS NULL))
 OR EXISTS(SELECT 1 FROM jobs.job WHERE user_id=target AND state='RUNNING') THEN
  RAISE EXCEPTION 'privacy_process_closure_required' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM vault.upload_session u WHERE u.user_id=target AND
       u.state NOT IN ('CANCELLED','EXPIRED','COMMITTED','REUSED'))
 OR EXISTS(SELECT 1 FROM vault.upload_cleanup_claim c JOIN vault.upload_session u
       ON u.upload_session_id=c.claim_id LEFT JOIN vault.provider_maintenance e
       ON e.execution_id=c.completed_execution_id WHERE u.user_id=target AND
       (c.completed_at IS NULL OR e.state IS DISTINCT FROM 'CLOSED' OR
        e.closed_at IS NULL OR e.exit_code IS DISTINCT FROM 0 OR e.action<>'UPLOAD_CLEANUP'))
 OR EXISTS(SELECT 1 FROM vault.ingest_cleanup_claim c JOIN vault.upload_session u
       ON u.upload_session_id=c.claim_id LEFT JOIN vault.ingest_cleanup_execution e
       ON e.execution_id=c.completed_execution_id WHERE u.user_id=target AND
       (c.completed_at IS NULL OR e.state IS DISTINCT FROM 'CLOSED' OR
        e.closed_at IS NULL OR e.exit_code IS DISTINCT FROM 0))
 OR EXISTS(SELECT 1 FROM vault.upload_session u WHERE u.user_id=target AND
       u.actor_kind='DEVICE' AND u.state IN ('CANCELLED','EXPIRED') AND NOT EXISTS
       (SELECT 1 FROM vault.upload_cleanup_claim c WHERE c.claim_id=u.upload_session_id
        AND c.completed_at IS NOT NULL)) THEN
  RAISE EXCEPTION 'privacy_staging_cleanup_required' USING ERRCODE='55000';
 END IF;
END $$;

-- Verification may inspect schema metadata; deletion above/below never derives a cascade from it.
CREATE FUNCTION account.verify_owner_absent(target uuid, device_ids uuid[]) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE field record; present boolean; identifiers text[];
BEGIN
 SELECT array_agg(replace(value::text,'-','')) INTO identifiers
 FROM unnest(array_prepend(target,device_ids)) value;
 FOR field IN SELECT n.nspname,c.relname,a.attname FROM pg_attribute a
 JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped AND a.atttypid='uuid'::regtype
 AND n.nspname IN ('account','audit','discovery','identity','importing','jobs','library','ml',
                  'playlist','social','sync','vault','wave') LOOP
  EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I WHERE %I=ANY($1))',
                 field.nspname,field.relname,field.attname) INTO present
  USING array_prepend(target,device_ids);
  IF present THEN RAISE EXCEPTION 'privacy_owner_rows_remain' USING ERRCODE='55000'; END IF;
 END LOOP;
 -- Result documents and idempotency scopes can contain identifiers without foreign keys.
 FOR field IN SELECT n.nspname,c.relname,a.attname FROM pg_attribute a
 JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
 AND a.atttypid IN ('text'::regtype,'varchar'::regtype,'jsonb'::regtype,'json'::regtype,
                   'uuid[]'::regtype,'text[]'::regtype)
 AND n.nspname IN ('account','audit','discovery','identity','importing','jobs','library','ml',
                  'playlist','social','sync','vault','wave') LOOP
  EXECUTE format('SELECT EXISTS(SELECT 1 FROM %I.%I CROSS JOIN unnest($1) identifier '
    'WHERE strpos(replace(lower(%I::text),''-'',''''),identifier)>0)',
    field.nspname,field.relname,field.attname) INTO present USING identifiers;
  IF present THEN RAISE EXCEPTION 'privacy_owner_rows_remain' USING ERRCODE='55000'; END IF;
 END LOOP;
END $$;

CREATE FUNCTION account.purge_account(target uuid, accepted_request uuid) RETURNS bigint
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE intent account.account_deletion_request%ROWTYPE;
        account_row account.user_account%ROWTYPE;
        transaction_key xid8;
        step text;
        affected bigint;
        total bigint:=0;
        restore_authorized boolean;
        device_ids uuid[];
BEGIN
 -- The global lock serializes authority,
 -- retained executions, owner transitions and purge; the owner lock excludes late sync publish.
 PERFORM server_instance_id FROM account.server_instance ORDER BY server_instance_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(4707761689340236801);
 PERFORM pg_advisory_xact_lock(('x'||substr(encode(sha256(
    convert_to('autplay-sync-owner-publish-v1','UTF8')||uuid_send(target)), 'hex'),1,16))::bit(64)::bigint);
 SELECT EXISTS(SELECT 1 FROM app_private.privacy_restore_authorization WHERE
    transaction_id=pg_current_xact_id() AND owner_id=target AND request_id=accepted_request)
 INTO restore_authorized;
 SELECT * INTO account_row FROM account.user_account WHERE user_id=target FOR UPDATE;
 SELECT * INTO intent FROM account.account_deletion_request
 WHERE deletion_request_id=accepted_request AND user_id=target FOR UPDATE;
 IF account_row.user_id IS NULL OR (NOT restore_authorized AND
 (intent.deletion_request_id IS NULL OR intent.state<>'PURGING'
 OR account_row.status NOT IN ('DELETION_PENDING','DISABLED')
 OR intent.cancel_before>clock_timestamp())) THEN
  RAISE EXCEPTION 'privacy_purge_not_authorized' USING ERRCODE='55000';
 END IF;
 PERFORM account.verify_account_purge_ready(target);
 transaction_key:=pg_current_xact_id();
 -- Keep indirect identifiers until final verification; many audit/result links have no FK.
 SELECT coalesce(array_agg(identifier),'{}'::uuid[]) INTO device_ids FROM (
 SELECT device_id AS identifier FROM account.device WHERE user_id=target
 UNION SELECT session_id FROM account.user_session WHERE user_id=target
 UNION SELECT web_session_id FROM account.web_session WHERE user_id=target
 UNION SELECT passkey_id FROM account.web_passkey WHERE user_id=target
 UNION SELECT invitation_id FROM account.web_session_invitation WHERE user_id=target
 UNION SELECT invitation_id FROM account.enrollment_invitation WHERE user_id=target
 UNION SELECT invitation_id FROM account.account_provisioning_link WHERE user_id=target
 ) owned;
 INSERT INTO app_private.privacy_purge_context VALUES(transaction_key,target,accepted_request);
 INSERT INTO app_private.privacy_purge_entity
 SELECT transaction_key,'identity.match_decision',decision_id FROM identity.match_decision
 WHERE owner_user_id=target
 UNION ALL SELECT transaction_key,'identity.match_candidate_evidence',e.match_candidate_evidence_id
 FROM identity.match_candidate_evidence e JOIN identity.match_decision d ON d.decision_id=e.decision_id
 WHERE d.owner_user_id=target
 UNION ALL SELECT transaction_key,'vault.ingest_execution',e.execution_id FROM vault.ingest_execution e
 JOIN vault.upload_session u ON u.upload_session_id=e.upload_session_id WHERE u.user_id=target
 UNION ALL SELECT transaction_key,'vault.ingest_cleanup_execution',e.execution_id
 FROM vault.ingest_cleanup_execution e JOIN vault.upload_session u ON u.upload_session_id=e.claim_id
 WHERE u.user_id=target
 UNION ALL SELECT transaction_key,'vault.ingest_cleanup_claim',c.claim_id
 FROM vault.ingest_cleanup_claim c JOIN vault.upload_session u ON u.upload_session_id=c.claim_id
 WHERE u.user_id=target
 UNION ALL SELECT transaction_key,'vault.upload_cleanup_claim',c.claim_id
 FROM vault.upload_cleanup_claim c JOIN vault.upload_session u ON u.upload_session_id=c.claim_id
 WHERE u.user_id=target
 UNION ALL SELECT transaction_key,'vault.provider_maintenance',m.execution_id
 FROM vault.provider_maintenance m WHERE m.provider_execution_id IN
 (SELECT execution_id FROM vault.provider_staging WHERE user_id=target) OR m.upload_claim_id IN
 (SELECT upload_session_id FROM vault.upload_session WHERE user_id=target)
 UNION ALL SELECT transaction_key,'library.metadata_execution',execution_id
 FROM library.metadata_execution WHERE user_id=target
 UNION ALL SELECT transaction_key,'library.track_metadata_revision',user_track_ref_id
 FROM library.user_track_ref WHERE user_id=target
 UNION ALL SELECT transaction_key,'account.account_invitation',invitation_id
 FROM account.account_invitation WHERE issued_by_user_id=target OR invitation_id IN
 (SELECT invitation_id FROM account.account_provisioning_link WHERE user_id=target)
 UNION ALL SELECT transaction_key,'account.device_admission',request_id FROM account.device_admission
 WHERE approved_user_id=target OR review_web_session_id IN
 (SELECT web_session_id FROM account.web_session WHERE user_id=target) OR enrolled_device_id IN
 (SELECT device_id FROM account.device WHERE user_id=target) OR enrolled_session_id IN
 (SELECT session_id FROM account.user_session WHERE user_id=target);

 SET CONSTRAINTS library.fk_user_track_ref_current_match_decision,
 importing.fk_import_entry_current_match_decision,identity.fk_match_decision_reviewed_evidence,
 identity.match_decision_supersedes_decision_id_fkey,discovery.internet_acquisition_upload_id_fkey,
 vault.ingest_cleanup_claim_completed_execution_id_fkey,
 vault.upload_cleanup_claim_completed_execution_id_fkey DEFERRED;

 -- Explicit attribution redaction keeps shared history and other owners' decisions intact.
 UPDATE identity.match_decision SET actor_user_id=NULL,actor_erased_at=clock_timestamp(),
 idempotency_scope='privacy-erased-review:'||decision_id::text,idempotency_key=decision_id::text
 WHERE actor_user_id=target AND owner_user_id IS DISTINCT FROM target;
 UPDATE identity.match_policy_activation SET actor_user_id=NULL,actor_erased_at=clock_timestamp(),
 reason='PRIVACY_ERASED' WHERE actor_user_id=target;
 UPDATE ml.embedding_model_activation SET actor_user_id=NULL WHERE actor_user_id=target;
 UPDATE audit.catalog_change_set SET actor_user_id=NULL,actor_erased_at=clock_timestamp(),
 reason='PRIVACY_ERASED' WHERE actor_user_id=target;
 UPDATE vault.acquisition_record SET authorized_by_user_id=NULL,source_uri_encrypted=NULL
 WHERE authorized_by_user_id=target;
 UPDATE account.account_invitation SET issued_by_user_id=NULL WHERE issued_by_user_id=target;
 UPDATE account.account_provisioning_link SET issued_by_user_id=NULL WHERE issued_by_user_id=target;
 UPDATE account.enrollment_invitation SET issued_by_user_id=NULL WHERE issued_by_user_id=target;
 UPDATE library.metadata_provider_gate SET execution_id=NULL,request_id=NULL WHERE execution_id IN
 (SELECT execution_id FROM library.metadata_execution WHERE user_id=target);

 -- This frozen list is a deletion order, not recursive foreign-key cascade discovery.
 FOREACH step IN ARRAY ARRAY[
 $q$DELETE FROM account.resource_io_execution WHERE operation_id IN
     (SELECT operation_id FROM account.resource_admission WHERE user_id=$1)$q$,
 $q$DELETE FROM account.resource_io_permit WHERE operation_id IN
     (SELECT operation_id FROM account.resource_admission WHERE user_id=$1)$q$,
 $q$DELETE FROM account.resource_admission WHERE user_id=$1$q$,
 $q$DELETE FROM account.resource_grant_cursor WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_quota_override WHERE user_id=$1$q$,
 $q$DELETE FROM account.quota_operation_receipt WHERE actor_user_id=$1 OR target_user_id=$1$q$,
 $q$DELETE FROM ml.offline_recommendation_pack WHERE user_id=$1$q$,
 $q$DELETE FROM library.user_interaction_event WHERE user_id=$1$q$,
 $q$DELETE FROM library.listening_event WHERE user_id=$1$q$,
 $q$DELETE FROM library.user_track_preference WHERE user_track_ref_id IN
     (SELECT user_track_ref_id FROM library.user_track_ref WHERE user_id=$1)$q$,
 $q$DELETE FROM ml.recommendation_item WHERE recommendation_request_id IN
     (SELECT recommendation_request_id FROM ml.recommendation_request WHERE user_id=$1)$q$,
 $q$DELETE FROM ml.recommendation_request WHERE user_id=$1$q$,
 $q$DELETE FROM ml.recommendation_temporal_snapshot WHERE user_id=$1$q$,
 $q$DELETE FROM ml.recommendation_input_snapshot WHERE user_id=$1$q$,
 $q$DELETE FROM ml.recommendation_adaptive_profile WHERE user_id=$1$q$,
 $q$DELETE FROM ml.recommendation_temporal_event WHERE user_id=$1$q$,
 $q$DELETE FROM ml.taste_cluster_member WHERE taste_cluster_id IN
     (SELECT taste_cluster_id FROM ml.taste_cluster WHERE user_id=$1)$q$,
 $q$DELETE FROM ml.taste_cluster WHERE user_id=$1$q$,
 $q$DELETE FROM sync.tombstone WHERE user_id=$1$q$,
 $q$DELETE FROM sync.sync_event WHERE user_id=$1$q$,
 $q$DELETE FROM sync.device_event_inbox WHERE user_id=$1$q$,
 $q$DELETE FROM sync.bootstrap_snapshot_item WHERE snapshot_id IN
     (SELECT snapshot_id FROM sync.bootstrap_session WHERE user_id=$1)$q$,
 $q$DELETE FROM sync.bootstrap_session WHERE user_id=$1$q$,
 $q$DELETE FROM sync.device_sync_cursor WHERE user_id=$1$q$,
 $q$DELETE FROM sync.idempotency_record WHERE scope LIKE 'sync-event:'||$1::text||':%'$q$,
 $q$DELETE FROM social.guest_preflight WHERE room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM social.guest_timing_report WHERE room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM social.guest_operation_receipt WHERE actor_user_id=$1 OR result_room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM social.guest_session WHERE room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM social.guest_invitation WHERE host_user_id=$1$q$,
 $q$DELETE FROM social.operation_receipt WHERE actor_user_id=$1 OR result_target_id=$1
     OR result_json::jsonb->>'creator_account_id'=$1::text
     OR result_json::jsonb->>'target_account_id'=$1::text
     OR result_target_id IN (SELECT invitation_id FROM social.friend_room_invitation
        WHERE host_user_id=$1 OR target_user_id=$1)
     OR result_room_id IN (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM social.friend_room_invitation WHERE host_user_id=$1 OR target_user_id=$1$q$,
 $q$DELETE FROM social.presence_heartbeat WHERE user_id=$1$q$,
 $q$DELETE FROM social.presence_settings WHERE user_id=$1$q$,
 $q$DELETE FROM social.profile_statistics_settings WHERE user_id=$1$q$,
 $q$DELETE FROM social.friend_request WHERE requester_user_id=$1 OR target_user_id=$1$q$,
 $q$DELETE FROM social.friendship WHERE lower_user_id=$1 OR higher_user_id=$1$q$,
 $q$DELETE FROM social.user_block WHERE blocker_user_id=$1 OR blocked_user_id=$1$q$,
 $q$DELETE FROM wave.timing_report WHERE room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1) OR device_id IN
     (SELECT device_id FROM account.device WHERE user_id=$1)$q$,
 $q$DELETE FROM wave.preflight WHERE user_id=$1 OR room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM wave.command WHERE actor_user_id=$1 OR room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)
     OR command_document->>'target_device_id'=ANY($2::text[])$q$,
 $q$DELETE FROM wave.invitation WHERE user_id=$1 OR room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM wave.queue_entry WHERE room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM wave.member WHERE user_id=$1 OR room_id IN
     (SELECT room_id FROM wave.room WHERE host_user_id=$1)$q$,
 $q$DELETE FROM wave.room WHERE host_user_id=$1$q$,
 $q$DELETE FROM vault.provider_maintenance WHERE app_private.privacy_entity_allowed(
     'vault.provider_maintenance',execution_id)$q$,
 $q$DELETE FROM vault.provider_staging WHERE user_id=$1$q$,
 $q$DELETE FROM vault.upload_cleanup_claim WHERE app_private.privacy_entity_allowed(
     'vault.upload_cleanup_claim',claim_id)$q$,
 $q$DELETE FROM vault.ingest_cleanup_execution WHERE app_private.privacy_entity_allowed(
     'vault.ingest_cleanup_execution',execution_id)$q$,
 $q$DELETE FROM vault.ingest_cleanup_claim WHERE app_private.privacy_entity_allowed(
     'vault.ingest_cleanup_claim',claim_id)$q$,
 $q$DELETE FROM vault.ingest_execution WHERE app_private.privacy_entity_allowed(
     'vault.ingest_execution',execution_id)$q$,
 $q$DELETE FROM vault.upload_chunk WHERE upload_session_id IN
     (SELECT upload_session_id FROM vault.upload_session WHERE user_id=$1)$q$,
 $q$DELETE FROM vault.upload_session WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.internet_acquisition WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.internet_search WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.run_page WHERE run_id IN
     (SELECT run_id FROM discovery.run WHERE user_id=$1)$q$,
 $q$DELETE FROM discovery.run_candidate WHERE run_id IN
     (SELECT run_id FROM discovery.run WHERE user_id=$1)$q$,
 $q$DELETE FROM discovery.bulk_operation_item WHERE bulk_operation_id IN
     (SELECT bulk_operation_id FROM discovery.bulk_operation WHERE user_id=$1)$q$,
 $q$DELETE FROM discovery.candidate_action_receipt WHERE user_id=$1$q$,
 $q$UPDATE discovery.candidate SET current_acquisition_attempt_id=NULL WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.acquisition_attempt WHERE candidate_id IN
     (SELECT candidate_id FROM discovery.candidate WHERE user_id=$1)$q$,
 $q$DELETE FROM discovery.candidate WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.run WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.source_authorization WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.artist_policy_revision WHERE owner_user_id=$1$q$,
 $q$DELETE FROM discovery.artist_policy WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.bulk_operation WHERE user_id=$1$q$,
 $q$DELETE FROM identity.match_candidate_evidence WHERE app_private.privacy_entity_allowed(
     'identity.match_candidate_evidence',match_candidate_evidence_id)$q$,
 $q$DELETE FROM identity.match_decision WHERE owner_user_id=$1$q$,
 $q$DELETE FROM importing.web_import_operation_receipt WHERE user_id=$1$q$,
 $q$DELETE FROM importing.import_entry WHERE import_job_id IN
     (SELECT import_job_id FROM importing.import_job WHERE user_id=$1)$q$,
 $q$DELETE FROM importing.import_job WHERE user_id=$1$q$,
 $q$DELETE FROM playlist.playlist_entry WHERE added_by_user_id=$1 OR playlist_id IN
     (SELECT playlist_id FROM playlist.playlist WHERE owner_user_id=$1) OR user_track_ref_id IN
     (SELECT user_track_ref_id FROM library.user_track_ref WHERE user_id=$1)$q$,
 $q$DELETE FROM playlist.smart_playlist_rule WHERE playlist_id IN
     (SELECT playlist_id FROM playlist.playlist WHERE owner_user_id=$1)$q$,
 $q$DELETE FROM playlist.playlist WHERE owner_user_id=$1$q$,
 $q$DELETE FROM library.metadata_execution WHERE user_id=$1$q$,
 $q$DELETE FROM library.track_metadata_revision WHERE user_track_ref_id IN
     (SELECT user_track_ref_id FROM library.user_track_ref WHERE user_id=$1)$q$,
 $q$DELETE FROM library.track_metadata WHERE user_track_ref_id IN
     (SELECT user_track_ref_id FROM library.user_track_ref WHERE user_id=$1)$q$,
 $q$DELETE FROM library.user_track_ref_external_reference WHERE user_track_ref_id IN
     (SELECT user_track_ref_id FROM library.user_track_ref WHERE user_id=$1)$q$,
 $q$DELETE FROM library.library_entry WHERE user_id=$1$q$,
 $q$DELETE FROM library.user_track_ref WHERE user_id=$1$q$,
 $q$DELETE FROM jobs.job_dependency WHERE job_id IN (SELECT job_id FROM jobs.job WHERE user_id=$1)
     OR depends_on_job_id IN (SELECT job_id FROM jobs.job WHERE user_id=$1)$q$,
 $q$UPDATE jobs.job_attempt SET metrics='{}',error_code=NULL WHERE job_id IN
     (SELECT job_id FROM jobs.job WHERE user_id=$1)$q$,
 $q$UPDATE jobs.job SET user_id=NULL,idempotency_scope=NULL,idempotency_key=NULL,payload='{}',
     checkpoint=NULL,error_detail=NULL,error_code=NULL,resource_waiting=false,resource_wake_until=NULL,
     state=CASE WHEN state IN ('COMPLETED','FAILED','CANCELLED') THEN state ELSE 'CANCELLED' END,
     completed_at=coalesce(completed_at,clock_timestamp()) WHERE user_id=$1 AND
     (EXISTS(SELECT 1 FROM ml.enrichment_job e WHERE e.job_id=jobs.job.job_id) OR
      EXISTS(SELECT 1 FROM ml.recording_embedding e WHERE e.producing_job_id=jobs.job.job_id) OR
      EXISTS(SELECT 1 FROM ml.recording_tag_set e WHERE e.producing_job_id=jobs.job.job_id))$q$,
 $q$DELETE FROM jobs.job_attempt WHERE job_id IN (SELECT job_id FROM jobs.job WHERE user_id=$1)$q$,
 $q$DELETE FROM jobs.job WHERE user_id=$1$q$,
 $q$DELETE FROM account.self_pairing_command WHERE user_id=$1$q$,
 $q$DELETE FROM account.self_device_pairing WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_deletion_request WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_recovery_operation WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_recovery_credential WHERE user_id=$1$q$,
 $q$DELETE FROM account.enrollment_exchange_receipt WHERE device_id IN
     (SELECT device_id FROM account.device WHERE user_id=$1)$q$,
 $q$DELETE FROM account.enrollment_invitation WHERE user_id=$1$q$,
 $q$DELETE FROM account.device_admission_exchange_receipt WHERE device_id IN
     (SELECT device_id FROM account.device WHERE user_id=$1)$q$,
 $q$DELETE FROM account.session_rotation_receipt WHERE parent_session_id IN
     (SELECT session_id FROM account.user_session WHERE user_id=$1)$q$,
 $q$DELETE FROM account.profile_lifecycle_command WHERE actor_user_id=$1 OR target_id=$1 OR
     target_id IN (SELECT device_id FROM account.device WHERE user_id=$1) OR target_id IN
     (SELECT session_id FROM account.user_session WHERE user_id=$1)$q$,
 $q$DELETE FROM account.device_admission_web_operation_receipt WHERE actor_user_id=$1
     OR app_private.privacy_entity_allowed('account.device_admission',target_id)$q$,
 $q$DELETE FROM account.device_admission_nonce WHERE request_id IN
     (SELECT request_id FROM account.device_admission WHERE app_private.privacy_entity_allowed(
         'account.device_admission',request_id))$q$,
 $q$DELETE FROM account.device_admission WHERE app_private.privacy_entity_allowed(
     'account.device_admission',request_id)$q$,
 $q$DELETE FROM account.trusted_device_reenrollment_challenge WHERE user_id=$1$q$,
 $q$DELETE FROM account.trusted_device_key WHERE user_id=$1$q$,
 $q$DELETE FROM account.device_key_block WHERE user_id=$1$q$,
 $q$DELETE FROM account.web_terminal_receipt WHERE user_id=$1 OR target_id=$1 OR target_id IN
     (SELECT device_id FROM account.device WHERE user_id=$1)$q$,
 $q$DELETE FROM account.web_session_rotation_evidence WHERE web_session_id IN
     (SELECT web_session_id FROM account.web_session WHERE user_id=$1)$q$,
 $q$DELETE FROM account.web_passkey_revocation WHERE user_id=$1$q$,
 $q$DELETE FROM account.web_passkey_ceremony WHERE user_id=$1 OR result_id=ANY($2)$q$,
 $q$DELETE FROM account.web_session WHERE user_id=$1$q$,
 $q$DELETE FROM account.web_passkey WHERE user_id=$1$q$,
 $q$DELETE FROM account.web_session_invitation WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_registration_receipt WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_provisioning_operation_receipt WHERE actor_user_id=$1 OR target_id=$1
     OR target_id IN (SELECT invitation_id FROM account.account_provisioning_link WHERE user_id=$1)$q$,
 $q$DELETE FROM account.account_provisioning_link WHERE user_id=$1$q$,
 $q$DELETE FROM account.account_invitation i WHERE app_private.privacy_entity_allowed(
     'account.account_invitation',invitation_id) AND NOT EXISTS
     (SELECT 1 FROM account.account_provisioning_link l WHERE l.invitation_id=i.invitation_id)
     AND NOT EXISTS(SELECT 1 FROM account.account_registration_receipt r WHERE r.invitation_id=i.invitation_id)$q$,
 $q$DELETE FROM audit.audit_event WHERE actor_user_id=$1 OR target_id=$1 OR target_id=ANY($2)
     OR actor_device_id IN
     (SELECT device_id FROM account.device WHERE user_id=$1) OR target_id IN
     (SELECT device_id FROM account.device WHERE user_id=$1)$q$,
 $q$DELETE FROM account.user_session WHERE user_id=$1$q$,
 $q$DELETE FROM account.device WHERE user_id=$1$q$,
 $q$DELETE FROM account.user_account WHERE user_id=$1$q$
 ] LOOP
  EXECUTE step USING target,device_ids;
  GET DIAGNOSTICS affected=ROW_COUNT;
  total:=total+affected;
 END LOOP;
 -- Validate deferred match evidence while the protected exact-ID manifest still exists.
 SET CONSTRAINTS ALL IMMEDIATE;
 PERFORM account.verify_owner_absent(target,device_ids);
 INSERT INTO account.account_purge_receipt VALUES(accepted_request,clock_timestamp(),total);
 DELETE FROM app_private.privacy_purge_entity WHERE transaction_id=transaction_key;
 DELETE FROM app_private.privacy_purge_context WHERE transaction_id=transaction_key;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION app_private.guard_catalog_actor_erasure(),
 app_private.guard_purge_receipt(),
 account.verify_account_purge_ready(uuid),account.purge_account(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION account.verify_owner_absent(uuid,uuid[]) FROM PUBLIC;
