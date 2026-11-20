-- Integrated production catalogue, portable-account and search erasure clauses.
-- Frozen M11 native accounting schema. Source delta and SHA256 pins:

-- build/r15-m11-close-20261113/schema-capture/capture.json

SET LOCAL check_function_bodies = false;

-- purge_account(uuid, uuid); Type: FUNCTION; Schema: account; Owner: -
CREATE OR REPLACE FUNCTION account.purge_account(target uuid, accepted_request uuid) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE intent account.account_deletion_request%ROWTYPE;
        account_row account.user_account%ROWTYPE;
        transaction_key xid8;
        step text;
        affected bigint;
        total bigint:=0;
        restore_authorized boolean;
        device_ids uuid[];
BEGIN
 PERFORM pg_advisory_xact_lock_shared(4707761689340236883);
 -- The global lock serializes authority,
 -- retained executions, owner transitions and purge; the owner lock excludes late sync publish.
 PERFORM server_instance_id FROM account.server_instance ORDER BY server_instance_id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(4707761689340236801);
 PERFORM pg_advisory_xact_lock(4707761689339965745);
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
 total:=total+app_private.m11_retire_owner_accounting(target);
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
 UNION ALL SELECT transaction_key,'library.catalog_execution',execution_id
 FROM library.catalog_execution WHERE user_id=target
 UNION ALL SELECT transaction_key,'discovery.internet_catalogue_context',context_id
 FROM discovery.internet_catalogue_context WHERE user_id=target
 UNION ALL SELECT transaction_key,'discovery.internet_search_context',
 search_id FROM discovery.internet_search_context WHERE user_id=target
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
 UPDATE library.metadata_provider_gate SET catalog_execution_id=NULL,request_id=NULL
 WHERE catalog_execution_id IN (SELECT execution_id FROM library.catalog_execution
 WHERE user_id=target);
 UPDATE library.metadata_provider_gate SET execution_id=NULL,request_id=NULL WHERE execution_id IN
 (SELECT execution_id FROM library.metadata_execution WHERE user_id=target);

 total:=total+app_private.purge_ml_serving_owner(target);
 total:=total+app_private.purge_face_owner(target);
 total:=total+app_private.purge_native_served_owner(target);
 -- This frozen list is a deletion order, not recursive foreign-key cascade discovery.
 FOREACH step IN ARRAY ARRAY[
 $q$DELETE FROM account.portable_challenge c WHERE c.account_id=$1
 OR c.actor_device_id=ANY($2) OR c.actor_session_id=ANY($2)
 OR EXISTS(SELECT 1 FROM unnest(array_prepend($1,$2)) identifier WHERE
 strpos(replace(lower(c.document::text),'-',''),replace(identifier::text,'-',''))>0
 OR strpos(replace(lower(c.receipt::text),'-',''),replace(identifier::text,'-',''))>0)$q$,
 $q$DELETE FROM library.portable_taste_signal WHERE user_id=$1$q$,
 $q$DELETE FROM account.portable_record WHERE user_id=$1$q$,
 $q$DELETE FROM account.portable_identity WHERE user_id=$1$q$,

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
 $q$DELETE FROM library.favorite_artist_refresh_retry WHERE user_id=$1$q$,
 $q$DELETE FROM library.favorite_artist_projection WHERE user_id=$1$q$,
 $q$DELETE FROM library.favorite_artist_refresh WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.watch_auto_download_consent_revision WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.watch_auto_download_consent WHERE user_id=$1$q$,
 $q$DELETE FROM library.artist_watch_choice WHERE user_id=$1$q$,
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
 $q$DELETE FROM discovery.internet_search_context WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.internet_catalogue_context WHERE user_id=$1$q$,
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
 $q$DELETE FROM library.catalog_execution WHERE user_id=$1$q$,
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
 $q$DELETE FROM ml.training_participant WHERE user_id=$1$q$,
 $q$DELETE FROM account.training_consent_operation WHERE user_id=$1$q$,
 $q$DELETE FROM account.training_consent WHERE user_id=$1$q$,
 $q$DELETE FROM account.user_account WHERE user_id=$1$q$
 ] LOOP
  EXECUTE step USING target,device_ids;
  GET DIAGNOSTICS affected=ROW_COUNT;
  total:=total+affected;
 END LOOP;
 -- Validate deferred match evidence while the protected exact-ID manifest still exists.
 SELECT string_agg(DISTINCT format('%I.%I',n.nspname,c.conname),',') INTO step
 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
 WHERE c.condeferrable AND c.conname<>'z_face_lifecycle_independent_commit'
 AND n.nspname IN ('account','audit','discovery','identity','importing','jobs','library',
  'ml','playlist','social','sync','vault','wave','app_private');
 IF step IS NOT NULL THEN EXECUTE 'SET CONSTRAINTS '||step||' IMMEDIATE'; END IF;
 PERFORM account.verify_owner_absent(target,device_ids);
 INSERT INTO account.account_purge_receipt VALUES(accepted_request,clock_timestamp(),total);
 DELETE FROM app_private.privacy_purge_entity WHERE transaction_id=transaction_key;
 DELETE FROM app_private.privacy_purge_context WHERE transaction_id=transaction_key;
 RETURN total;
END $_$;

-- verify_account_purge_ready(uuid); Type: FUNCTION; Schema: account; Owner: -
CREATE OR REPLACE FUNCTION account.verify_account_purge_ready(target uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM app_private.m11_assert_owner_retirable(target);
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
 OR EXISTS(SELECT 1 FROM library.catalog_execution WHERE user_id=target AND (state<>'CLOSED' OR closed_at IS NULL))
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

-- m11_assert_epoch_accounted(uuid, text, bigint); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_epoch_accounted(uuid, text, bigint) RETURNS void
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $_$
 SELECT app_private.m11_epoch_catalog_ready($1,$2,$3,false)
$_$;

-- m11_assert_epoch_no_start(uuid, text, bigint); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_epoch_no_start(uuid, text, bigint) RETURNS void
    LANGUAGE sql
    SET search_path TO 'pg_catalog'
    AS $_$
 SELECT app_private.m11_epoch_catalog_ready($1,$2,$3,true)
$_$;

-- m11_allocation_slot; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_allocation_slot (
    execution_id uuid NOT NULL,
    permit_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    job_id uuid NOT NULL,
    worker_id text NOT NULL,
    attempt_no bigint NOT NULL,
    owner_run_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    activation_id uuid NOT NULL,
    generation bigint NOT NULL,
    actual_target_id uuid NOT NULL,
    ceiling_bytes bigint NOT NULL,
    jamendo_max_bytes bigint NOT NULL,
    vault_max_bytes bigint NOT NULL,
    config_sha256 bytea NOT NULL,
    priority_epoch bigint NOT NULL,
    capability_sha256 bytea NOT NULL,
    phase text NOT NULL,
    reserved_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    terminal_at timestamp with time zone,
    source_evidence_sha256 bytea,
    no_start_proof_id uuid,
    CONSTRAINT ck_m11_allocation_slot_digests CHECK (((octet_length(config_sha256) = 32) AND (octet_length(capability_sha256) = 32) AND ((source_evidence_sha256 IS NULL) OR (octet_length(source_evidence_sha256) = 32)))),
    CONSTRAINT ck_m11_allocation_slot_limits CHECK ((((ceiling_bytes >= 1) AND (ceiling_bytes <= 157286400)) AND (jamendo_max_bytes >= 1) AND (vault_max_bytes >= 1) AND (ceiling_bytes <= jamendo_max_bytes) AND (ceiling_bytes <= vault_max_bytes))),
    CONSTRAINT ck_m11_allocation_slot_phase CHECK ((((phase = 'RESERVED'::text) AND (consumed_at IS NULL) AND (terminal_at IS NULL) AND (source_evidence_sha256 IS NULL) AND (no_start_proof_id IS NULL)) OR ((phase = 'CONSUMED'::text) AND (consumed_at IS NOT NULL) AND (terminal_at IS NULL) AND (source_evidence_sha256 IS NULL) AND (no_start_proof_id IS NULL)) OR ((phase = 'PREPARED'::text) AND (consumed_at IS NOT NULL) AND (terminal_at IS NOT NULL) AND (source_evidence_sha256 IS NULL) AND (no_start_proof_id IS NULL)) OR ((phase = 'NEVER_SPAWNED'::text) AND (consumed_at IS NOT NULL) AND (terminal_at IS NOT NULL) AND (source_evidence_sha256 IS NOT NULL)) OR ((phase = 'SEALED_NO_FILE'::text) AND (terminal_at IS NOT NULL) AND (source_evidence_sha256 IS NOT NULL)))),
    CONSTRAINT ck_m11_allocation_slot_times CHECK ((isfinite(reserved_at) AND ((consumed_at IS NULL) OR (isfinite(consumed_at) AND (consumed_at >= reserved_at))) AND ((terminal_at IS NULL) OR (isfinite(terminal_at) AND (terminal_at >= COALESCE(consumed_at, reserved_at)))))),
    CONSTRAINT ck_m11_allocation_slot_versions CHECK (((attempt_no >= 1) AND (generation >= 1) AND (priority_epoch >= 1) AND ((length(worker_id) >= 1) AND (length(worker_id) <= 300))))
);

-- m11_assert_native_slot(discovery.m11_allocation_slot); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_native_slot(s discovery.m11_allocation_slot) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE fact discovery.m11_native_seal%ROWTYPE;
BEGIN
 SELECT * INTO fact FROM discovery.m11_native_seal WHERE execution_id=s.execution_id;
 IF NOT FOUND OR s.phase<>fact.discriminator OR s.no_start_proof_id IS NOT NULL
  OR s.terminal_at IS DISTINCT FROM fact.sealed_at
  OR s.source_evidence_sha256 IS DISTINCT FROM fact.native_evidence_sha256
  OR fact.sealed_at>clock_timestamp()
  OR EXISTS(SELECT 1 FROM discovery.m11_priority_execution WHERE execution_id=s.execution_id)
  OR EXISTS(SELECT 1 FROM discovery.m11_never_spawned WHERE execution_id=s.execution_id)
  OR EXISTS(SELECT 1 FROM account.resource_io_execution WHERE execution_id=s.execution_id)
  OR EXISTS(SELECT 1 FROM vault.provider_staging WHERE execution_id=s.execution_id) THEN
  RAISE EXCEPTION 'm11_native_seal_original_required' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_same_allocation_binding(to_jsonb(s),to_jsonb(fact),ARRAY['execution_id','permit_id','owner_id','job_id','worker_id','attempt_no','owner_run_id','operation_id','activation_id','generation','actual_target_id','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch','capability_sha256','reserved_at','consumed_at']);
END $$;

-- m11_assert_owner_retirable(uuid); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_owner_retirable(target uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE snapshot jsonb; observed timestamptz; cutoff timestamptz; owned uuid[];
 no_go discovery.m11_positive_no_go%ROWTYPE;
 allocation discovery.m11_allocation_slot%ROWTYPE;
 ledger text; stored_revision bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(4707761689340236801);
 PERFORM pg_advisory_xact_lock(4707761689339965745);
 SELECT state,revision INTO STRICT snapshot,stored_revision FROM discovery.m11_priority_control
  WHERE singleton_id=1 FOR UPDATE;
 observed:=clock_timestamp(); cutoff:=observed-INTERVAL '24 hours';
 IF snapshot#>>'{as_of,$datetime}' IS NULL
  OR (snapshot->>'revision')::bigint IS DISTINCT FROM stored_revision
  OR (snapshot#>>'{as_of,$datetime}')::timestamptz>observed THEN
  RAISE EXCEPTION 'm11_privacy_clock_invalid' USING ERRCODE='55000';
 END IF;
 -- Never turn a malformed/missing owner field into a filtered-away quota fact.
 FOREACH ledger IN ARRAY ARRAY['promotions','reservations','actual_charges',
   'provider_starts','retry_tokens','upper_bound_holds','no_start_receipts'] LOOP
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->ledger) p WHERE
   CASE WHEN ledger='promotions' THEN p#>>'{target,owner_id,$uuid}'
    WHEN ledger='no_start_receipts' THEN p#>>'{proof,job_id,$uuid}'
    ELSE p#>>'{owner_id,$uuid}' END IS NULL
   OR CASE ledger
    WHEN 'promotions' THEN p#>>'{selected_at,$datetime}'
    WHEN 'reservations' THEN p#>>'{opened_at,$datetime}'
    WHEN 'actual_charges' THEN p#>>'{settled_at,$datetime}'
    WHEN 'provider_starts' THEN p#>>'{started_at,$datetime}'
    WHEN 'retry_tokens' THEN p#>>'{reserved_at,$datetime}'
    WHEN 'upper_bound_holds' THEN p#>>'{closure,closed_at,$datetime}'
    ELSE p#>>'{proof,checked_at,$datetime}' END IS NULL) THEN
   RAISE EXCEPTION 'm11_privacy_snapshot_invalid' USING ERRCODE='55000';
  END IF;
 END LOOP;
 owned:=app_private.m11_owner_jobs(target);
 IF EXISTS(SELECT 1 FROM discovery.m11_priority_execution e WHERE e.owner_id=target
   AND (e.settled_revision IS NULL OR e.quarantined OR e.closed_at IS NULL
    OR e.closure_sha256 IS NULL OR e.closed_at>cutoff OR e.outcome IS NULL))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'reservations') p
   WHERE p#>>'{owner_id,$uuid}'=target::text)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'retry_tokens') p
   WHERE p#>>'{owner_id,$uuid}'=target::text AND p#>>'{state,value}'='PENDING') THEN
  RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
 END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'promotions') p
   WHERE p#>>'{target,owner_id,$uuid}'=target::text
    AND (p#>>'{selected_at,$datetime}')::timestamptz>cutoff)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'actual_charges') p
   WHERE p#>>'{owner_id,$uuid}'=target::text
    AND (p#>>'{settled_at,$datetime}')::timestamptz>cutoff)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'provider_starts') p
   WHERE p#>>'{owner_id,$uuid}'=target::text
    AND (p#>>'{started_at,$datetime}')::timestamptz>cutoff)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'retry_tokens') p
   WHERE p#>>'{owner_id,$uuid}'=target::text AND
    greatest((p#>>'{reserved_at,$datetime}')::timestamptz,
             (p#>>'{settled_at,$datetime}')::timestamptz)>cutoff)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'upper_bound_holds') p
   WHERE p#>>'{owner_id,$uuid}'=target::text
    AND (p#>>'{closure,closed_at,$datetime}')::timestamptz>cutoff)
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(snapshot->'no_start_receipts') p
   WHERE (p#>>'{proof,job_id,$uuid}')::uuid=ANY(owned)
    AND (p#>>'{proof,checked_at,$datetime}')::timestamptz>cutoff)
 OR EXISTS(SELECT 1 FROM discovery.m11_priority_no_start p WHERE p.job_id=ANY(owned)
   AND p.checked_at>cutoff) THEN
  RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
 END IF;
 -- A seal is not an exit timestamp and cannot shorten the original quota window.
 IF EXISTS(SELECT 1 FROM discovery.m11_never_spawned n WHERE n.owner_id=target
   AND (n.sealed_at>observed OR n.sealed_at>cutoff OR NOT EXISTS(
    SELECT 1 FROM discovery.m11_priority_no_start p
    WHERE p.proof_id=n.no_start_proof_id AND p.job_id=n.job_id
     AND p.worker_id=n.worker_id AND p.attempt_no=n.attempt_no
     AND p.disposition='RESOURCE_WAIT' AND p.checked_at<=observed))) THEN
  RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
 END IF;

 IF EXISTS(SELECT 1 FROM discovery.m11_allocation_slot s WHERE s.owner_id=target
   AND (s.phase IN ('RESERVED','CONSUMED') OR s.terminal_at IS NULL
    OR s.terminal_at>cutoff)) THEN
  RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
 END IF;
 FOR allocation IN SELECT * FROM discovery.m11_allocation_slot WHERE owner_id=target LOOP
  PERFORM app_private.m11_assert_slot_replacement(allocation);
 END LOOP;

 IF EXISTS(SELECT 1 FROM discovery.m11_native_seal f WHERE f.owner_id=target
   AND (f.sealed_at>cutoff OR (NOT app_private.privacy_owner_allowed(target) AND
    NOT EXISTS(SELECT 1 FROM discovery.m11_allocation_slot s
     WHERE s.execution_id=f.execution_id)))) THEN
  RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
 END IF;

 IF EXISTS(SELECT 1 FROM discovery.m11_positive_no_go WHERE owner_id=target
   AND verified_at>cutoff) THEN
  RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
 END IF;
 FOR no_go IN SELECT * FROM discovery.m11_positive_no_go WHERE owner_id=target LOOP
  IF EXISTS(SELECT 1 FROM discovery.m11_allocation_slot WHERE execution_id=no_go.execution_id) THEN
   PERFORM app_private.m11_assert_positive_no_go(no_go);
  ELSIF NOT app_private.privacy_owner_allowed(target) THEN
   -- Retiring earlier slots in the same authorized owner purge is expected.
   RAISE EXCEPTION 'm11_privacy_accounting_retained' USING ERRCODE='55000';
  END IF;
 END LOOP;

END $_$;

-- m11_positive_no_go; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_positive_no_go (
    execution_id uuid NOT NULL,
    permit_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    job_id uuid NOT NULL,
    worker_id text NOT NULL,
    attempt_no bigint NOT NULL,
    owner_run_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    activation_id uuid NOT NULL,
    generation bigint NOT NULL,
    actual_target_id uuid NOT NULL,
    ceiling_bytes bigint NOT NULL,
    jamendo_max_bytes bigint NOT NULL,
    vault_max_bytes bigint NOT NULL,
    config_sha256 bytea NOT NULL,
    priority_epoch bigint NOT NULL,
    capability_sha256 bytea NOT NULL,
    reserved_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone NOT NULL,
    receipt_id uuid NOT NULL,
    pin_device numeric(39,0) NOT NULL,
    pin_inode numeric(39,0) NOT NULL,
    nonce bytea NOT NULL,
    binding_sha256 bytea NOT NULL,
    protocol_version integer NOT NULL,
    meter_scope text NOT NULL,
    prepared_at timestamp with time zone NOT NULL,
    closed_at timestamp with time zone NOT NULL,
    closure_sha256 bytea NOT NULL,
    child_pid bigint NOT NULL,
    child_identity_sha256 bytea NOT NULL,
    closure_kind text NOT NULL,
    exit_code bigint NOT NULL,
    verified_at timestamp with time zone NOT NULL,
    association_sha256 bytea NOT NULL,
    CONSTRAINT ck_m11_positive_no_go_digests CHECK (((octet_length(config_sha256) = 32) AND (octet_length(capability_sha256) = 32) AND (octet_length(nonce) = 32) AND (octet_length(binding_sha256) = 32) AND (octet_length(closure_sha256) = 32) AND (octet_length(child_identity_sha256) = 32) AND (octet_length(association_sha256) = 32))),
    CONSTRAINT ck_m11_positive_no_go_identity CHECK (((attempt_no >= 1) AND (generation >= 1) AND (priority_epoch >= 1) AND ((length(worker_id) >= 1) AND (length(worker_id) <= 300)) AND (child_pid >= 1))),
    CONSTRAINT ck_m11_positive_no_go_limits CHECK ((((ceiling_bytes >= 1) AND (ceiling_bytes <= 157286400)) AND (jamendo_max_bytes >= 1) AND (vault_max_bytes >= 1) AND (ceiling_bytes <= jamendo_max_bytes) AND (ceiling_bytes <= vault_max_bytes))),
    CONSTRAINT ck_m11_positive_no_go_pin CHECK ((((pin_device >= (0)::numeric) AND (pin_device <= '340282366920938463463374607431768211455'::numeric)) AND ((pin_inode >= (1)::numeric) AND (pin_inode <= '340282366920938463463374607431768211455'::numeric)))),
    CONSTRAINT ck_m11_positive_no_go_protocol CHECK (((protocol_version = 2) AND (meter_scope = 'JAMENDO_MEDIA_RESPONSE_BODY_V1'::text) AND (closure_kind = 'PROCESS_EXIT'::text))),
    CONSTRAINT ck_m11_positive_no_go_times CHECK ((isfinite(reserved_at) AND isfinite(consumed_at) AND isfinite(prepared_at) AND isfinite(closed_at) AND isfinite(verified_at) AND (reserved_at <= consumed_at) AND (consumed_at <= prepared_at) AND (prepared_at <= closed_at) AND (closed_at <= verified_at)))
);

-- m11_assert_positive_no_go(discovery.m11_positive_no_go); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_positive_no_go(f discovery.m11_positive_no_go) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE s discovery.m11_allocation_slot%ROWTYPE; p discovery.m11_priority_execution%ROWTYPE;
BEGIN
 PERFORM app_private.m11_require_allocation_prefix();
 SELECT * INTO s FROM discovery.m11_allocation_slot WHERE execution_id=f.execution_id;
 IF NOT FOUND OR s.phase<>'PREPARED' OR s.terminal_at IS DISTINCT FROM f.prepared_at
  OR s.no_start_proof_id IS NOT NULL OR s.source_evidence_sha256 IS NOT NULL THEN
  RAISE EXCEPTION 'm11_positive_no_go_original_required' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_same_allocation_binding(to_jsonb(s),to_jsonb(f),ARRAY['execution_id','permit_id','owner_id','job_id','worker_id','attempt_no','owner_run_id','operation_id','activation_id','generation','actual_target_id','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch','capability_sha256','reserved_at','consumed_at']);
 SELECT * INTO p FROM discovery.m11_priority_execution WHERE execution_id=f.execution_id;
 IF NOT FOUND OR p.started_at IS NOT NULL OR p.actual_bytes IS NOT NULL OR p.evidence IS NOT NULL
  OR p.outcome IS DISTINCT FROM 'NO_START' OR p.quarantined OR p.settled_revision IS NULL
  OR f.verified_at>clock_timestamp()
  OR f.association_sha256 IS DISTINCT FROM app_private.m11_positive_no_go_association(f)
  OR EXISTS(SELECT 1 FROM discovery.m11_native_seal WHERE execution_id=f.execution_id)
  OR EXISTS(SELECT 1 FROM discovery.m11_never_spawned WHERE execution_id=f.execution_id) THEN
  RAISE EXCEPTION 'm11_positive_no_go_original_required' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_same_allocation_binding(to_jsonb(p),to_jsonb(f),ARRAY['execution_id','owner_id','job_id','worker_id','attempt_no','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch','receipt_id','pin_device','pin_inode','nonce','binding_sha256','protocol_version','meter_scope','prepared_at','closed_at','closure_sha256']);
END $$;

-- m11_assert_slot_replacement(discovery.m11_allocation_slot); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_slot_replacement(s discovery.m11_allocation_slot) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF s.phase IN ('NEVER_SPAWNED','SEALED_NO_FILE') AND s.no_start_proof_id IS NULL THEN
  PERFORM app_private.m11_assert_native_slot(s);
 ELSE
  PERFORM app_private.m11_assert_slot_replacement_v1(s);
 END IF;
END $$;

-- m11_assert_slot_replacement_v1(discovery.m11_allocation_slot); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_assert_slot_replacement_v1(s discovery.m11_allocation_slot) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE replacement jsonb; ticket jsonb; proof discovery.m11_priority_no_start%ROWTYPE;
BEGIN
 IF s.phase='PREPARED' THEN
  SELECT to_jsonb(p) INTO replacement FROM discovery.m11_priority_execution p
   WHERE execution_id=s.execution_id AND prepared_at=s.terminal_at;
  IF replacement IS NULL OR EXISTS(SELECT 1 FROM discovery.m11_never_spawned
    WHERE execution_id=s.execution_id) THEN
   RAISE EXCEPTION 'm11_allocation_replacement_required' USING ERRCODE='55000';
  END IF;
  PERFORM app_private.m11_same_allocation_binding(to_jsonb(s),replacement,ARRAY['execution_id','owner_id','job_id','worker_id','attempt_no','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch']);
  SELECT to_jsonb(e) INTO ticket FROM account.resource_io_execution e
   WHERE e.execution_id=s.execution_id;
  IF ticket IS NULL THEN
   SELECT to_jsonb(p)||jsonb_build_object('actual_target_id',p.acquisition_id,
    'owner_id',p.user_id,'worker_id',p.job_worker_id,'attempt_no',p.job_attempt)
    INTO ticket FROM vault.provider_staging p WHERE p.execution_id=s.execution_id;
   IF ticket IS NOT NULL THEN
    PERFORM app_private.m11_same_allocation_binding(to_jsonb(s),ticket,
     ARRAY['owner_id','job_id','worker_id','attempt_no']);
   END IF;
  END IF;
  IF ticket IS NULL THEN
   RAISE EXCEPTION 'm11_allocation_replacement_required' USING ERRCODE='55000';
  END IF;
  PERFORM app_private.m11_same_allocation_binding(to_jsonb(s),ticket,ARRAY['execution_id','permit_id','owner_run_id','operation_id','activation_id','generation','actual_target_id']);
 ELSIF s.phase IN ('NEVER_SPAWNED','SEALED_NO_FILE') THEN
  SELECT * INTO proof FROM discovery.m11_priority_no_start p
   WHERE p.proof_id=s.no_start_proof_id AND p.job_id=s.job_id AND p.worker_id=s.worker_id
    AND p.attempt_no=s.attempt_no AND p.disposition='RESOURCE_WAIT'
    AND p.checked_at<=clock_timestamp();
  IF NOT FOUND OR EXISTS(SELECT 1 FROM discovery.m11_priority_execution
    WHERE execution_id=s.execution_id) OR EXISTS(SELECT 1 FROM account.resource_io_execution
    WHERE execution_id=s.execution_id) OR EXISTS(SELECT 1 FROM vault.provider_staging
    WHERE execution_id=s.execution_id) THEN
   RAISE EXCEPTION 'm11_allocation_replacement_required' USING ERRCODE='55000';
  END IF;
  IF s.phase='NEVER_SPAWNED' THEN
   SELECT to_jsonb(n) INTO replacement FROM discovery.m11_never_spawned n
    WHERE execution_id=s.execution_id AND sealed_at=s.terminal_at
     AND native_evidence_sha256=s.source_evidence_sha256
     AND no_start_proof_id=s.no_start_proof_id;
   IF replacement IS NULL THEN
    RAISE EXCEPTION 'm11_allocation_replacement_required' USING ERRCODE='55000';
   END IF;
   PERFORM app_private.m11_same_allocation_binding(to_jsonb(s),replacement,
     ARRAY['execution_id','owner_id','job_id','worker_id','attempt_no','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch']||ARRAY['execution_id','permit_id','owner_run_id','operation_id','activation_id','generation','actual_target_id']);
  ELSIF EXISTS(SELECT 1 FROM discovery.m11_never_spawned WHERE execution_id=s.execution_id) THEN
   RAISE EXCEPTION 'm11_allocation_replacement_required' USING ERRCODE='55000';
  END IF;
 ELSE
  RAISE EXCEPTION 'm11_allocation_pending_retained' USING ERRCODE='55000';
 END IF;
END $$;

-- m11_epoch_catalog_ready(uuid, text, bigint, boolean); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_epoch_catalog_ready(target_job uuid, target_worker text, target_attempt bigint, require_no_start boolean) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE p discovery.m11_priority_execution%ROWTYPE; f discovery.m11_positive_no_go%ROWTYPE;
BEGIN
 -- Original four-member census and epoch no-start denial remain authoritative.
 PERFORM app_private.m11_epoch_catalog_ready_v2(
  target_job,target_worker,target_attempt,require_no_start);
 FOR p IN SELECT * FROM discovery.m11_priority_execution WHERE job_id=target_job
  AND (target_worker IS NULL OR worker_id=target_worker)
  AND (target_attempt IS NULL OR attempt_no=target_attempt) AND outcome='NO_START' LOOP
  SELECT * INTO f FROM discovery.m11_positive_no_go WHERE execution_id=p.execution_id;
  IF FOUND THEN
   PERFORM app_private.m11_assert_positive_no_go(f);
  ELSIF NOT EXISTS(
   SELECT 1 FROM discovery.m11_priority_no_start proof,
    LATERAL jsonb_array_elements(proof.execution_bindings) entry
   WHERE proof.job_id=p.job_id AND proof.worker_id=p.worker_id AND proof.attempt_no=p.attempt_no
    AND p.started_at IS NULL AND p.actual_bytes IS NULL AND p.settled_revision IS NOT NULL
    AND proof.checked_at>=p.closed_at AND proof.checked_at<=clock_timestamp()
    AND entry->>'execution_id'=p.execution_id::text
    AND entry->>'binding_sha256'=encode(p.binding_sha256,'hex')
    AND entry->>'closure_sha256'=encode(p.closure_sha256,'hex')
    AND entry->>'closed_at'=CASE
     WHEN extract(microseconds from p.closed_at)::bigint%1000000=0
      THEN to_char(p.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
     ELSE to_char(p.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') END
    AND entry->>'attestation_sha256' ~ '^[0-9a-f]{64}$'
  ) THEN
   RAISE EXCEPTION 'm11_positive_no_go_original_required' USING ERRCODE='55000';
  END IF;
 END LOOP;
END $_$;

-- m11_epoch_catalog_ready_v2(uuid, text, bigint, boolean); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_epoch_catalog_ready_v2(target_job uuid, target_worker text, target_attempt bigint, require_no_start boolean) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE member uuid; member_count integer:=0; slot discovery.m11_allocation_slot%ROWTYPE;
 positive discovery.m11_priority_execution%ROWTYPE; original jsonb; staging jsonb;
BEGIN
 PERFORM app_private.m11_require_allocation_prefix();
 IF (target_worker IS NULL)<>(target_attempt IS NULL) THEN
  RAISE EXCEPTION 'm11_allocation_binding_divergence' USING ERRCODE='55000';
 END IF;
 FOR member IN
  SELECT execution_id FROM discovery.m11_allocation_slot WHERE job_id=target_job
   AND (target_worker IS NULL OR worker_id=target_worker)
   AND (target_attempt IS NULL OR attempt_no=target_attempt)
  UNION SELECT execution_id FROM discovery.m11_priority_execution WHERE job_id=target_job
   AND (target_worker IS NULL OR worker_id=target_worker)
   AND (target_attempt IS NULL OR attempt_no=target_attempt)
  UNION SELECT execution_id FROM discovery.m11_never_spawned WHERE job_id=target_job
   AND (target_worker IS NULL OR worker_id=target_worker)
   AND (target_attempt IS NULL OR attempt_no=target_attempt)
  UNION SELECT execution_id FROM discovery.m11_native_seal WHERE job_id=target_job
   AND (target_worker IS NULL OR worker_id=target_worker)
   AND (target_attempt IS NULL OR attempt_no=target_attempt)
  UNION SELECT i.execution_id FROM account.resource_io_execution i
   JOIN account.resource_admission a ON a.operation_id=i.operation_id
   WHERE a.job_id=target_job AND (target_worker IS NULL OR a.job_worker_id=target_worker)
    AND (target_attempt IS NULL OR a.job_attempt=target_attempt)
    AND NOT EXISTS(SELECT 1 FROM discovery.m11_priority_execution h
     WHERE h.execution_id=i.execution_id AND
      (h.job_id<>target_job OR h.worker_id IS DISTINCT FROM target_worker
       OR h.attempt_no IS DISTINCT FROM target_attempt))
 LOOP
  member_count:=member_count+1;
  IF member_count>4 OR target_worker IS NULL THEN
   RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
  END IF;
  IF EXISTS(SELECT 1 FROM account.resource_io_execution i WHERE i.execution_id=member
    AND (i.state<>'CLOSED' OR i.closed_at IS NULL OR i.closure_evidence_sha256 IS NULL))
   OR NOT (EXISTS(SELECT 1 FROM discovery.m11_allocation_slot WHERE execution_id=member)
    OR EXISTS(SELECT 1 FROM discovery.m11_priority_execution WHERE execution_id=member)
    OR EXISTS(SELECT 1 FROM discovery.m11_never_spawned WHERE execution_id=member)
    OR EXISTS(SELECT 1 FROM discovery.m11_native_seal WHERE execution_id=member)) THEN
   RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
  END IF;
  SELECT * INTO slot FROM discovery.m11_allocation_slot WHERE execution_id=member;
  IF FOUND THEN
   IF slot.phase IN ('RESERVED','CONSUMED') THEN
    RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
   END IF;
   PERFORM app_private.m11_assert_slot_replacement(slot);
  ELSIF EXISTS(SELECT 1 FROM discovery.m11_native_seal WHERE execution_id=member) THEN
   RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
  END IF;
  SELECT * INTO positive FROM discovery.m11_priority_execution WHERE execution_id=member;
  IF FOUND THEN
   IF positive.quarantined OR (require_no_start AND positive.started_at IS NOT NULL) THEN
    RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
   END IF;
   IF NOT require_no_start THEN
    IF positive.settled_revision IS NULL OR positive.outcome IS NULL
     OR positive.closed_at IS NULL OR positive.closure_sha256 IS NULL THEN
     RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
    END IF;
   ELSIF positive.outcome IS DISTINCT FROM 'NO_START' OR positive.settled_revision IS NULL THEN
    -- This is only a catalog-ready candidate. Core still verifies the actual
    -- trusted waiting/no-GO source and original pin before issuing its proof.
    SELECT to_jsonb(i) INTO original FROM account.resource_io_execution i
     WHERE execution_id=member AND state='CLOSED' AND started_at IS NULL
      AND closed_at IS NOT NULL AND closure_evidence_sha256 IS NOT NULL;
    SELECT to_jsonb(p) INTO staging FROM vault.provider_staging p
     WHERE execution_id=member AND state<>'OWNED' AND closed_at IS NOT NULL;
    IF original IS NULL OR staging IS NULL
     OR original->'closed_at' IS DISTINCT FROM staging->'closed_at'
     OR original->'closure_evidence_sha256' IS DISTINCT FROM staging->'closure_evidence_sha256'
     OR staging->>'user_id'<>positive.owner_id::text
     OR staging->>'job_id'<>positive.job_id::text
     OR staging->>'job_worker_id'<>positive.worker_id
     OR (staging->>'job_attempt')::bigint<>positive.attempt_no THEN
     RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
    END IF;
    PERFORM app_private.m11_same_allocation_binding(original,staging,
     ARRAY['execution_id','permit_id','owner_run_id','operation_id','activation_id','generation']);
    IF original->>'actual_target_id' IS DISTINCT FROM staging->>'acquisition_id' THEN
     RAISE EXCEPTION 'm11_allocation_binding_divergence' USING ERRCODE='55000';
    END IF;
   END IF;
  ELSIF EXISTS(SELECT 1 FROM discovery.m11_never_spawned n WHERE n.execution_id=member
    AND NOT EXISTS(SELECT 1 FROM discovery.m11_priority_no_start p
     WHERE p.proof_id=n.no_start_proof_id AND p.job_id=n.job_id AND p.worker_id=n.worker_id
      AND p.attempt_no=n.attempt_no AND p.disposition='RESOURCE_WAIT'
      AND p.checked_at<=clock_timestamp())) THEN
   RAISE EXCEPTION 'm11_epoch_catalog_retained' USING ERRCODE='55000';
  END IF;
 END LOOP;
END $$;

-- m11_guard_allocation_slot(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_guard_allocation_slot() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE existing_slots bigint; context_owner uuid; original_fields text[];
BEGIN
 PERFORM app_private.m11_require_allocation_prefix();
 IF TG_OP='DELETE' THEN
  SELECT owner_id INTO context_owner FROM app_private.privacy_purge_context
   WHERE transaction_id=pg_current_xact_id();
  IF context_owner IS NULL OR context_owner<>OLD.owner_id THEN
   RAISE EXCEPTION 'm11_allocation_evidence_retained' USING ERRCODE='55000';
  END IF;
  PERFORM app_private.m11_assert_owner_retirable(context_owner);
  PERFORM app_private.m11_assert_slot_replacement(OLD);
  RETURN OLD;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.phase<>'RESERVED' OR NEW.reserved_at>clock_timestamp()
   OR EXISTS(SELECT 1 FROM discovery.m11_priority_execution WHERE execution_id=NEW.execution_id)
   OR EXISTS(SELECT 1 FROM discovery.m11_never_spawned WHERE execution_id=NEW.execution_id) THEN
   RAISE EXCEPTION 'm11_allocation_reservation_required' USING ERRCODE='55000';
  END IF;
  SELECT count(*) INTO existing_slots FROM (
   SELECT execution_id FROM discovery.m11_priority_execution WHERE job_id=NEW.job_id
    AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
   UNION SELECT execution_id FROM discovery.m11_never_spawned WHERE job_id=NEW.job_id
    AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
   UNION SELECT execution_id FROM discovery.m11_allocation_slot WHERE job_id=NEW.job_id
    AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
   UNION SELECT execution_id FROM discovery.m11_native_seal WHERE job_id=NEW.job_id
    AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
  ) ids WHERE execution_id<>NEW.execution_id;
  IF existing_slots>=4 THEN
   RAISE EXCEPTION 'm11_allocation_slots_exhausted' USING ERRCODE='55000';
  END IF;
 ELSE
  original_fields:=ARRAY['execution_id','permit_id','owner_id','job_id','worker_id','attempt_no',
   'owner_run_id','operation_id','activation_id','generation','actual_target_id','ceiling_bytes',
   'jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch','capability_sha256',
   'reserved_at'];
  PERFORM app_private.m11_same_allocation_binding(to_jsonb(OLD),to_jsonb(NEW),original_fields);
  IF OLD.phase NOT IN ('RESERVED','CONSUMED') OR OLD.phase=NEW.phase
   OR (OLD.phase='RESERVED' AND NEW.phase NOT IN ('CONSUMED','SEALED_NO_FILE'))
   OR (OLD.phase='CONSUMED' AND NEW.phase NOT IN ('PREPARED','NEVER_SPAWNED','SEALED_NO_FILE'))
   OR (OLD.phase='RESERVED' AND NEW.phase='SEALED_NO_FILE' AND NEW.consumed_at IS NOT NULL)
   OR (OLD.consumed_at IS NOT NULL AND OLD.consumed_at IS DISTINCT FROM NEW.consumed_at)
   OR NEW.consumed_at>clock_timestamp() OR NEW.terminal_at>clock_timestamp() THEN
   RAISE EXCEPTION 'm11_allocation_transition_invalid' USING ERRCODE='55000';
  END IF;
  IF NEW.phase<>'CONSUMED' THEN
   PERFORM app_private.m11_assert_slot_replacement(NEW);
  END IF;
 END IF;
 RETURN NEW;
END $$;

-- m11_guard_attested_positive(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_guard_attested_positive() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 IF NEW IS DISTINCT FROM OLD AND EXISTS(SELECT 1 FROM discovery.m11_positive_no_go
   WHERE execution_id=OLD.execution_id) THEN
  RAISE EXCEPTION 'm11_positive_no_go_immutable' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

-- m11_guard_epoch_no_start(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_guard_epoch_no_start() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
BEGIN
 PERFORM app_private.m11_assert_epoch_no_start(NEW.job_id,NEW.worker_id,NEW.attempt_no);
 RETURN NEW;
END $$;

-- m11_guard_execution_slots(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_guard_execution_slots() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE existing_slots bigint; slot discovery.m11_allocation_slot%ROWTYPE; other jsonb;
BEGIN
 PERFORM app_private.m11_require_allocation_prefix();
 IF EXISTS(SELECT 1 FROM discovery.m11_native_seal WHERE execution_id=NEW.execution_id) THEN
  RAISE EXCEPTION 'm11_allocation_binding_divergence' USING ERRCODE='55000';
 END IF;
 SELECT * INTO slot FROM discovery.m11_allocation_slot WHERE execution_id=NEW.execution_id;
 IF FOUND THEN
  IF slot.phase<>'CONSUMED' THEN
   RAISE EXCEPTION 'm11_allocation_consumed_slot_required' USING ERRCODE='55000';
  END IF;
  PERFORM app_private.m11_same_allocation_binding(to_jsonb(slot),to_jsonb(NEW),ARRAY['execution_id','owner_id','job_id','worker_id','attempt_no','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch']);
  IF TG_TABLE_NAME='m11_never_spawned' THEN
   PERFORM app_private.m11_same_allocation_binding(to_jsonb(slot),to_jsonb(NEW),ARRAY['execution_id','permit_id','owner_run_id','operation_id','activation_id','generation','actual_target_id']);
  END IF;
 END IF;
 IF TG_TABLE_NAME='m11_never_spawned' THEN
  IF NOT EXISTS(SELECT 1 FROM discovery.m11_priority_no_start p
   WHERE p.proof_id=NEW.no_start_proof_id AND p.job_id=NEW.job_id
    AND p.worker_id=NEW.worker_id AND p.attempt_no=NEW.attempt_no
    AND p.disposition='RESOURCE_WAIT' AND p.checked_at<=clock_timestamp()) THEN
   RAISE EXCEPTION 'm11_never_spawned_original_proof_required' USING ERRCODE='55000';
  END IF;
  SELECT to_jsonb(p) INTO other FROM discovery.m11_priority_execution p
   WHERE execution_id=NEW.execution_id;
 ELSE
  SELECT to_jsonb(n) INTO other FROM discovery.m11_never_spawned n
   WHERE execution_id=NEW.execution_id;
 END IF;
 IF other IS NOT NULL THEN
  RAISE EXCEPTION 'm11_allocation_binding_divergence' USING ERRCODE='55000';
 END IF;
 SELECT count(*) INTO existing_slots FROM (
  SELECT execution_id FROM discovery.m11_priority_execution WHERE job_id=NEW.job_id
   AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
  UNION SELECT execution_id FROM discovery.m11_never_spawned WHERE job_id=NEW.job_id
   AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
  UNION SELECT execution_id FROM discovery.m11_allocation_slot WHERE job_id=NEW.job_id
   AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
   UNION SELECT execution_id FROM discovery.m11_native_seal WHERE job_id=NEW.job_id
    AND worker_id=NEW.worker_id AND attempt_no=NEW.attempt_no
 ) ids WHERE execution_id<>NEW.execution_id;
 IF existing_slots>=4 THEN
  RAISE EXCEPTION 'm11_allocation_slots_exhausted' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END $$;

-- m11_guard_native_seal(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_guard_native_seal() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE slot discovery.m11_allocation_slot%ROWTYPE; context_owner uuid;
BEGIN
 PERFORM app_private.m11_require_allocation_prefix();
 IF TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'm11_native_seal_immutable' USING ERRCODE='55000';
 ELSIF TG_OP='DELETE' THEN
  SELECT owner_id INTO context_owner FROM app_private.privacy_purge_context
   WHERE transaction_id=pg_current_xact_id();
  -- Original owner readiness was rechecked under the lane before slot deletion.
  -- Facts remain until slots are retired; final owner inventory is transactional.
  IF context_owner IS NULL OR context_owner<>OLD.owner_id
   OR OLD.sealed_at>clock_timestamp()-INTERVAL '24 hours'
   OR EXISTS(SELECT 1 FROM discovery.m11_allocation_slot WHERE execution_id=OLD.execution_id) THEN
   RAISE EXCEPTION 'm11_native_seal_retained' USING ERRCODE='55000';
  END IF;
  RETURN OLD;
 END IF;
 SELECT * INTO slot FROM discovery.m11_allocation_slot WHERE execution_id=NEW.execution_id;
 IF NOT FOUND OR NEW.sealed_at>clock_timestamp()
  OR (NEW.discriminator='NEVER_SPAWNED' AND slot.phase<>'CONSUMED')
  OR (NEW.discriminator='SEALED_NO_FILE' AND slot.phase NOT IN ('RESERVED','CONSUMED'))
  OR EXISTS(SELECT 1 FROM discovery.m11_priority_execution WHERE execution_id=NEW.execution_id)
  OR EXISTS(SELECT 1 FROM discovery.m11_never_spawned WHERE execution_id=NEW.execution_id)
  OR EXISTS(SELECT 1 FROM account.resource_io_execution WHERE execution_id=NEW.execution_id)
  OR EXISTS(SELECT 1 FROM vault.provider_staging WHERE execution_id=NEW.execution_id) THEN
  RAISE EXCEPTION 'm11_native_seal_original_required' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_same_allocation_binding(to_jsonb(slot),to_jsonb(NEW),ARRAY['execution_id','permit_id','owner_id','job_id','worker_id','attempt_no','owner_run_id','operation_id','activation_id','generation','actual_target_id','ceiling_bytes','jamendo_max_bytes','vault_max_bytes','config_sha256','priority_epoch','capability_sha256','reserved_at','consumed_at']);
 RETURN NEW;
END $$;

-- m11_guard_positive_no_go(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_guard_positive_no_go() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE p discovery.m11_priority_execution%ROWTYPE; i account.resource_io_execution%ROWTYPE;
 staging vault.provider_staging%ROWTYPE; context_owner uuid; current_revision bigint;
BEGIN
 PERFORM app_private.m11_require_allocation_prefix();
 IF TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'm11_positive_no_go_immutable' USING ERRCODE='55000';
 ELSIF TG_OP='DELETE' THEN
  SELECT owner_id INTO context_owner FROM app_private.privacy_purge_context
   WHERE transaction_id=pg_current_xact_id();
  IF context_owner IS NULL OR context_owner<>OLD.owner_id
   OR OLD.verified_at>clock_timestamp()-INTERVAL '24 hours'
   OR EXISTS(SELECT 1 FROM discovery.m11_allocation_slot WHERE execution_id=OLD.execution_id) THEN
   RAISE EXCEPTION 'm11_positive_no_go_retained' USING ERRCODE='55000';
  END IF;
  RETURN OLD;
 END IF;
 PERFORM app_private.m11_assert_positive_no_go(NEW);
 SELECT * INTO p FROM discovery.m11_priority_execution WHERE execution_id=NEW.execution_id;
 SELECT revision INTO current_revision FROM discovery.m11_priority_control WHERE singleton_id=1;
 IF p.settled_revision IS DISTINCT FROM current_revision THEN
  RAISE EXCEPTION 'm11_positive_no_go_revision_invalid' USING ERRCODE='55000';
 END IF;
 SELECT * INTO i FROM account.resource_io_execution WHERE execution_id=NEW.execution_id;
 IF NOT FOUND OR i.state<>'CLOSED' OR i.started_at IS NULL OR i.started_at>i.closed_at
  OR i.closed_at IS DISTINCT FROM NEW.closed_at
  OR i.closure_evidence_sha256 IS DISTINCT FROM NEW.closure_sha256
  OR i.closure_kind IS DISTINCT FROM NEW.closure_kind OR i.exit_code IS DISTINCT FROM NEW.exit_code
  OR i.child_pid IS DISTINCT FROM NEW.child_pid
  OR i.child_identity_sha256 IS DISTINCT FROM NEW.child_identity_sha256
  OR i.kind<>'PROVIDER' OR i.target_id IS DISTINCT FROM NEW.actual_target_id
  OR i.actual_target_id IS DISTINCT FROM NEW.actual_target_id THEN
  RAISE EXCEPTION 'm11_positive_no_go_closed_io_required' USING ERRCODE='55000';
 END IF;
 -- i.started_at is native child registration, not the provider GO marker p.started_at.
 PERFORM app_private.m11_same_allocation_binding(to_jsonb(i),to_jsonb(NEW),ARRAY['execution_id','permit_id','owner_run_id','operation_id','activation_id','generation']);
 SELECT * INTO staging FROM vault.provider_staging WHERE execution_id=NEW.execution_id;
 IF NOT FOUND OR staging.state<>'EXITED' OR staging.resource_type<>'DISCOVERY_ACQUISITION'
  OR staging.sealed_at IS NOT NULL OR staging.byte_size IS NOT NULL OR staging.sha256 IS NOT NULL
  OR staging.upload_session_id IS NOT NULL OR staging.handed_off_at IS NOT NULL
  OR staging.user_id IS DISTINCT FROM NEW.owner_id OR staging.job_id IS DISTINCT FROM NEW.job_id
  OR staging.job_worker_id IS DISTINCT FROM NEW.worker_id
  OR staging.job_attempt IS DISTINCT FROM NEW.attempt_no
  OR staging.acquisition_id IS DISTINCT FROM NEW.actual_target_id
  OR staging.closed_at IS DISTINCT FROM NEW.closed_at
  OR staging.closure_evidence_sha256 IS DISTINCT FROM NEW.closure_sha256
  OR staging.closure_kind IS DISTINCT FROM NEW.closure_kind
  OR staging.child_pid IS DISTINCT FROM NEW.child_pid
  OR staging.child_identity_sha256 IS DISTINCT FROM NEW.child_identity_sha256
  OR staging.exit_code IS DISTINCT FROM NEW.exit_code THEN
  RAISE EXCEPTION 'm11_positive_no_go_closed_staging_required' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_same_allocation_binding(to_jsonb(staging),to_jsonb(NEW),ARRAY['execution_id','permit_id','owner_run_id','operation_id','activation_id','generation']);
 RETURN NEW;
END $$;

-- m11_owner_jobs(uuid); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_owner_jobs(target uuid) RETURNS uuid[]
    LANGUAGE sql STABLE
    SET search_path TO 'pg_catalog'
    AS $_$
 SELECT ARRAY(
  SELECT job_id FROM jobs.job WHERE user_id=target
  UNION SELECT job_id FROM discovery.m11_priority_execution WHERE owner_id=target
  UNION SELECT job_id FROM discovery.m11_never_spawned WHERE owner_id=target
  UNION SELECT job_id FROM discovery.m11_allocation_slot WHERE owner_id=target
  UNION SELECT job_id FROM discovery.m11_native_seal WHERE owner_id=target
  UNION SELECT job_id FROM discovery.m11_positive_no_go WHERE owner_id=target
  UNION SELECT (p#>>'{target,job_id,$uuid}')::uuid
   FROM discovery.m11_priority_control c,
    jsonb_array_elements(c.state->'promotions') p
   WHERE p#>>'{target,owner_id,$uuid}'=target::text
 )
$_$;

-- m11_positive_no_go_association(discovery.m11_positive_no_go); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_positive_no_go_association(f discovery.m11_positive_no_go) RETURNS bytea
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE part text; encoded bytea; document bytea:=''::bytea;
BEGIN
 FOREACH part IN ARRAY ARRAY['AUTPLAY_M11_POSITIVE_NO_GO_ASSOCIATION_V1',CASE WHEN f.execution_id IS NULL THEN 'N' ELSE 'V'||f.execution_id::text END,CASE WHEN f.permit_id IS NULL THEN 'N' ELSE 'V'||f.permit_id::text END,CASE WHEN f.owner_id IS NULL THEN 'N' ELSE 'V'||f.owner_id::text END,CASE WHEN f.job_id IS NULL THEN 'N' ELSE 'V'||f.job_id::text END,CASE WHEN f.worker_id IS NULL THEN 'N' ELSE 'V'||f.worker_id::text END,CASE WHEN f.attempt_no IS NULL THEN 'N' ELSE 'V'||f.attempt_no::text END,CASE WHEN f.owner_run_id IS NULL THEN 'N' ELSE 'V'||f.owner_run_id::text END,CASE WHEN f.operation_id IS NULL THEN 'N' ELSE 'V'||f.operation_id::text END,CASE WHEN f.activation_id IS NULL THEN 'N' ELSE 'V'||f.activation_id::text END,CASE WHEN f.generation IS NULL THEN 'N' ELSE 'V'||f.generation::text END,CASE WHEN f.actual_target_id IS NULL THEN 'N' ELSE 'V'||f.actual_target_id::text END,CASE WHEN f.ceiling_bytes IS NULL THEN 'N' ELSE 'V'||f.ceiling_bytes::text END,CASE WHEN f.jamendo_max_bytes IS NULL THEN 'N' ELSE 'V'||f.jamendo_max_bytes::text END,CASE WHEN f.vault_max_bytes IS NULL THEN 'N' ELSE 'V'||f.vault_max_bytes::text END,CASE WHEN f.config_sha256 IS NULL THEN 'N' ELSE 'V'||encode(f.config_sha256,'hex') END,CASE WHEN f.priority_epoch IS NULL THEN 'N' ELSE 'V'||f.priority_epoch::text END,CASE WHEN f.capability_sha256 IS NULL THEN 'N' ELSE 'V'||encode(f.capability_sha256,'hex') END,CASE WHEN f.reserved_at IS NULL THEN 'N' ELSE 'V'||to_char(f.reserved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') END,CASE WHEN f.consumed_at IS NULL THEN 'N' ELSE 'V'||to_char(f.consumed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') END,CASE WHEN f.receipt_id IS NULL THEN 'N' ELSE 'V'||f.receipt_id::text END,CASE WHEN f.pin_device IS NULL THEN 'N' ELSE 'V'||f.pin_device::text END,CASE WHEN f.pin_inode IS NULL THEN 'N' ELSE 'V'||f.pin_inode::text END,CASE WHEN f.nonce IS NULL THEN 'N' ELSE 'V'||encode(f.nonce,'hex') END,CASE WHEN f.binding_sha256 IS NULL THEN 'N' ELSE 'V'||encode(f.binding_sha256,'hex') END,CASE WHEN f.protocol_version IS NULL THEN 'N' ELSE 'V'||f.protocol_version::text END,CASE WHEN f.meter_scope IS NULL THEN 'N' ELSE 'V'||f.meter_scope::text END,CASE WHEN f.prepared_at IS NULL THEN 'N' ELSE 'V'||to_char(f.prepared_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') END,CASE WHEN f.closed_at IS NULL THEN 'N' ELSE 'V'||to_char(f.closed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') END,CASE WHEN f.closure_sha256 IS NULL THEN 'N' ELSE 'V'||encode(f.closure_sha256,'hex') END,CASE WHEN f.child_pid IS NULL THEN 'N' ELSE 'V'||f.child_pid::text END,CASE WHEN f.child_identity_sha256 IS NULL THEN 'N' ELSE 'V'||encode(f.child_identity_sha256,'hex') END,CASE WHEN f.closure_kind IS NULL THEN 'N' ELSE 'V'||f.closure_kind::text END,CASE WHEN f.exit_code IS NULL THEN 'N' ELSE 'V'||f.exit_code::text END,CASE WHEN f.verified_at IS NULL THEN 'N' ELSE 'V'||to_char(f.verified_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') END] LOOP
  encoded:=convert_to(part,'UTF8');
  document:=document||int8send(octet_length(encoded)::bigint)||encoded;
 END LOOP;
 RETURN sha256(document);
END $$;

-- m11_protect_accounting_retirement(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_protect_accounting_retirement() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE context_owner uuid;
BEGIN
 -- A purge context is private, transaction-bound SQL authorization created by
 -- account.purge_account after its original accepted/restore authority checks.
 SELECT owner_id INTO context_owner FROM app_private.privacy_purge_context
  WHERE transaction_id=pg_current_xact_id();
 IF TG_OP<>'DELETE' OR context_owner IS NULL THEN
  RAISE EXCEPTION 'm11_privacy_evidence_retained' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='m11_priority_execution' THEN
  IF OLD.owner_id<>context_owner OR OLD.quarantined OR OLD.settled_revision IS NULL
   OR OLD.closed_at IS NULL OR OLD.closure_sha256 IS NULL OR OLD.outcome IS NULL
   OR OLD.closed_at>clock_timestamp()-INTERVAL '24 hours' THEN
   RAISE EXCEPTION 'm11_privacy_evidence_retained' USING ERRCODE='55000';
  END IF;
 ELSE
  IF NOT (OLD.job_id=ANY(app_private.m11_owner_jobs(context_owner)))
   OR OLD.checked_at>clock_timestamp()-INTERVAL '24 hours' THEN
   RAISE EXCEPTION 'm11_privacy_evidence_retained' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN OLD;
END $$;

-- m11_protect_never_spawned(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_protect_never_spawned() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE context_owner uuid;
BEGIN
 SELECT owner_id INTO context_owner FROM app_private.privacy_purge_context
  WHERE transaction_id=pg_current_xact_id();
 IF TG_OP<>'DELETE' OR context_owner IS NULL OR OLD.owner_id<>context_owner THEN
  RAISE EXCEPTION 'm11_never_spawned_evidence_retained' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_assert_owner_retirable(context_owner);
 RETURN OLD;
END $$;

-- m11_require_allocation_prefix(); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_require_allocation_prefix() RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE required_lock bigint;
BEGIN
 FOREACH required_lock IN ARRAY ARRAY[4707761689340236801::bigint,4707761689339965745::bigint] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
   AND granted AND mode='ExclusiveLock' AND objsubid=1
   AND classid=(required_lock>>32)::oid AND objid=(required_lock&4294967295)::oid) THEN
   RAISE EXCEPTION 'm11_allocation_lock_prefix_required' USING ERRCODE='55000';
  END IF;
 END LOOP;
END $$;

-- m11_retire_owner_accounting(uuid); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_retire_owner_accounting(target uuid) RETURNS bigint
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $_$
DECLARE control discovery.m11_priority_control%ROWTYPE; snapshot jsonb;
 owned uuid[]; ledger text; kept jsonb; observed timestamptz;
 removed bigint:=0; affected bigint;
BEGIN
 IF NOT app_private.privacy_owner_allowed(target) THEN
  RAISE EXCEPTION 'm11_privacy_purge_context_required' USING ERRCODE='55000';
 END IF;
 PERFORM app_private.m11_assert_owner_retirable(target);
 owned:=app_private.m11_owner_jobs(target);
 SELECT * INTO STRICT control FROM discovery.m11_priority_control
  WHERE singleton_id=1 FOR UPDATE;
 snapshot:=control.state; observed:=clock_timestamp();
 DELETE FROM discovery.m11_allocation_slot WHERE owner_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM discovery.m11_native_seal WHERE owner_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM discovery.m11_never_spawned WHERE owner_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 -- Exact settled evidence is retired only in the actual account purge after
 -- its entire rolling exposure has elapsed. Recovery never calls this function.
 DELETE FROM discovery.m11_priority_no_start WHERE job_id=ANY(owned);
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM discovery.m11_priority_queue_clock WHERE job_id=ANY(owned);
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM discovery.m11_positive_no_go WHERE owner_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 DELETE FROM discovery.m11_priority_execution WHERE owner_id=target;
 GET DIAGNOSTICS affected=ROW_COUNT; removed:=removed+affected;
 FOREACH ledger IN ARRAY ARRAY['promotions','reservations','actual_charges',
   'provider_starts','retry_tokens','upper_bound_holds','no_start_receipts'] LOOP
  SELECT coalesce(jsonb_agg(p.value ORDER BY p.ordinality),'[]'::jsonb) INTO kept
  FROM jsonb_array_elements(snapshot->ledger) WITH ORDINALITY p(value,ordinality)
  WHERE CASE
   WHEN ledger='promotions' THEN p.value#>>'{target,owner_id,$uuid}'<>target::text
   WHEN ledger='no_start_receipts' THEN
    NOT ((p.value#>>'{proof,job_id,$uuid}')::uuid=ANY(owned))
   ELSE p.value#>>'{owner_id,$uuid}'<>target::text END;
  snapshot:=jsonb_set(snapshot,ARRAY[ledger],kept,false);
 END LOOP;
 IF snapshot#>>'{promoted_last_owner,$uuid}'=target::text THEN
  snapshot:=jsonb_set(snapshot,'{promoted_last_owner}','null'::jsonb,false);
 END IF;
 IF snapshot#>>'{aged_last_owner,$uuid}'=target::text THEN
  snapshot:=jsonb_set(snapshot,'{aged_last_owner}','null'::jsonb,false);
 END IF;
 IF snapshot IS DISTINCT FROM control.state THEN
  snapshot:=jsonb_set(snapshot,'{revision}',to_jsonb(control.revision+1),false);
  snapshot:=jsonb_set(snapshot,'{as_of}',jsonb_build_object('$datetime',
    to_char(observed AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')),false);
  UPDATE discovery.m11_priority_control SET revision=revision+1,state=snapshot
   WHERE singleton_id=1;
 END IF;
 RETURN removed;
END $_$;

-- m11_same_allocation_binding(jsonb, jsonb, text[]); Type: FUNCTION; Schema: app_private; Owner: -
CREATE FUNCTION app_private.m11_same_allocation_binding(a jsonb, b jsonb, fields text[]) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog'
    AS $$
DECLARE field text;
BEGIN
 FOREACH field IN ARRAY fields LOOP
  IF a->field IS NULL OR b->field IS NULL OR a->field IS DISTINCT FROM b->field THEN
   RAISE EXCEPTION 'm11_allocation_binding_divergence' USING ERRCODE='55000';
  END IF;
 END LOOP;
END $$;

-- m11_native_seal; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_native_seal (
    execution_id uuid NOT NULL,
    permit_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    job_id uuid NOT NULL,
    worker_id text NOT NULL,
    attempt_no bigint NOT NULL,
    owner_run_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    activation_id uuid NOT NULL,
    generation bigint NOT NULL,
    actual_target_id uuid NOT NULL,
    ceiling_bytes bigint NOT NULL,
    jamendo_max_bytes bigint NOT NULL,
    vault_max_bytes bigint NOT NULL,
    config_sha256 bytea NOT NULL,
    priority_epoch bigint NOT NULL,
    capability_sha256 bytea NOT NULL,
    reserved_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    sealed_at timestamp with time zone NOT NULL,
    native_evidence_sha256 bytea NOT NULL,
    association_sha256 bytea NOT NULL,
    discriminator text NOT NULL,
    receipt_id uuid,
    pin_device numeric(39,0),
    pin_inode numeric(39,0),
    nonce bytea,
    binding_sha256 bytea,
    protocol_version integer,
    meter_scope text,
    CONSTRAINT ck_m11_native_seal_digests CHECK (((octet_length(config_sha256) = 32) AND (octet_length(capability_sha256) = 32) AND (octet_length(native_evidence_sha256) = 32) AND (octet_length(association_sha256) = 32))),
    CONSTRAINT ck_m11_native_seal_discriminator CHECK ((((discriminator = 'NEVER_SPAWNED'::text) AND (consumed_at IS NOT NULL) AND (receipt_id IS NOT NULL) AND (pin_device IS NOT NULL) AND (pin_inode IS NOT NULL) AND (nonce IS NOT NULL) AND (binding_sha256 IS NOT NULL) AND (protocol_version IS NOT NULL) AND (meter_scope IS NOT NULL) AND ((pin_device >= (0)::numeric) AND (pin_device <= '340282366920938463463374607431768211455'::numeric)) AND ((pin_inode >= (1)::numeric) AND (pin_inode <= '340282366920938463463374607431768211455'::numeric)) AND (octet_length(nonce) = 32) AND (octet_length(binding_sha256) = 32) AND (protocol_version = 2) AND (meter_scope = 'JAMENDO_MEDIA_RESPONSE_BODY_V1'::text)) OR ((discriminator = 'SEALED_NO_FILE'::text) AND (receipt_id IS NULL) AND (pin_device IS NULL) AND (pin_inode IS NULL) AND (nonce IS NULL) AND (binding_sha256 IS NULL) AND (protocol_version IS NULL) AND (meter_scope IS NULL)))),
    CONSTRAINT ck_m11_native_seal_limits CHECK ((((ceiling_bytes >= 1) AND (ceiling_bytes <= 157286400)) AND (jamendo_max_bytes >= 1) AND (vault_max_bytes >= 1) AND (ceiling_bytes <= jamendo_max_bytes) AND (ceiling_bytes <= vault_max_bytes))),
    CONSTRAINT ck_m11_native_seal_times CHECK ((isfinite(reserved_at) AND isfinite(sealed_at) AND ((consumed_at IS NULL) OR (isfinite(consumed_at) AND (consumed_at >= reserved_at))) AND (sealed_at >= COALESCE(consumed_at, reserved_at)))),
    CONSTRAINT ck_m11_native_seal_versions CHECK (((attempt_no >= 1) AND (generation >= 1) AND (priority_epoch >= 1) AND ((length(worker_id) >= 1) AND (length(worker_id) <= 300))))
);

-- m11_never_spawned; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_never_spawned (
    execution_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    job_id uuid NOT NULL,
    worker_id text NOT NULL,
    attempt_no bigint NOT NULL,
    owner_run_id uuid NOT NULL,
    operation_id uuid NOT NULL,
    activation_id uuid NOT NULL,
    generation bigint NOT NULL,
    permit_id uuid NOT NULL,
    actual_target_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    ceiling_bytes bigint NOT NULL,
    jamendo_max_bytes bigint NOT NULL,
    vault_max_bytes bigint NOT NULL,
    config_sha256 bytea NOT NULL,
    priority_epoch bigint NOT NULL,
    pin_device numeric(39,0) NOT NULL,
    pin_inode numeric(39,0) NOT NULL,
    nonce bytea NOT NULL,
    binding_sha256 bytea NOT NULL,
    protocol_version integer NOT NULL,
    meter_scope text NOT NULL,
    sealed_at timestamp with time zone NOT NULL,
    native_evidence_sha256 bytea NOT NULL,
    no_start_proof_id uuid NOT NULL,
    association_sha256 bytea NOT NULL,
    discriminator text NOT NULL,
    CONSTRAINT ck_m11_never_spawned_digests CHECK (((octet_length(nonce) = 32) AND (octet_length(binding_sha256) = 32) AND (octet_length(config_sha256) = 32) AND (octet_length(native_evidence_sha256) = 32) AND (octet_length(association_sha256) = 32))),
    CONSTRAINT ck_m11_never_spawned_limits CHECK ((((ceiling_bytes >= 1) AND (ceiling_bytes <= 157286400)) AND (jamendo_max_bytes >= 1) AND (vault_max_bytes >= 1) AND (ceiling_bytes <= jamendo_max_bytes) AND (ceiling_bytes <= vault_max_bytes))),
    CONSTRAINT ck_m11_never_spawned_pin CHECK ((((pin_device >= (0)::numeric) AND (pin_device <= '340282366920938463463374607431768211455'::numeric)) AND ((pin_inode >= (1)::numeric) AND (pin_inode <= '340282366920938463463374607431768211455'::numeric)))),
    CONSTRAINT ck_m11_never_spawned_protocol CHECK (((protocol_version = 2) AND (meter_scope = 'JAMENDO_MEDIA_RESPONSE_BODY_V1'::text) AND (discriminator = 'NEVER_SPAWNED'::text))),
    CONSTRAINT ck_m11_never_spawned_time CHECK (isfinite(sealed_at)),
    CONSTRAINT ck_m11_never_spawned_versions CHECK (((attempt_no >= 1) AND (generation >= 1) AND (priority_epoch >= 1))),
    CONSTRAINT ck_m11_never_spawned_worker CHECK (((length(worker_id) >= 1) AND (length(worker_id) <= 300)))
);

-- m11_priority_control; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_priority_control (
    singleton_id integer NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    epoch bigint DEFAULT 1 NOT NULL,
    revision bigint DEFAULT 0 NOT NULL,
    state jsonb NOT NULL,
    CONSTRAINT ck_m11_control_actual_charges_bound CHECK (((state ? 'actual_charges'::text) AND (jsonb_typeof((state -> 'actual_charges'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'actual_charges'::text)) <= 256))),
    CONSTRAINT ck_m11_control_no_start_receipts_bound CHECK (((state ? 'no_start_receipts'::text) AND (jsonb_typeof((state -> 'no_start_receipts'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'no_start_receipts'::text)) <= 256))),
    CONSTRAINT ck_m11_control_promotions_bound CHECK (((state ? 'promotions'::text) AND (jsonb_typeof((state -> 'promotions'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'promotions'::text)) <= 256))),
    CONSTRAINT ck_m11_control_provider_starts_bound CHECK (((state ? 'provider_starts'::text) AND (jsonb_typeof((state -> 'provider_starts'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'provider_starts'::text)) <= 256))),
    CONSTRAINT ck_m11_control_reservations_bound CHECK (((state ? 'reservations'::text) AND (jsonb_typeof((state -> 'reservations'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'reservations'::text)) <= 4))),
    CONSTRAINT ck_m11_control_retry_tokens_bound CHECK (((state ? 'retry_tokens'::text) AND (jsonb_typeof((state -> 'retry_tokens'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'retry_tokens'::text)) <= 256))),
    CONSTRAINT ck_m11_control_singleton CHECK ((singleton_id = 1)),
    CONSTRAINT ck_m11_control_state_type CHECK (((jsonb_typeof(state) = 'object'::text) AND (state ? '$type'::text) AND ((state ->> '$type'::text) = 'PriorityState'::text))),
    CONSTRAINT ck_m11_control_upper_bound_holds_bound CHECK (((state ? 'upper_bound_holds'::text) AND (jsonb_typeof((state -> 'upper_bound_holds'::text)) = 'array'::text) AND (jsonb_array_length((state -> 'upper_bound_holds'::text)) <= 256))),
    CONSTRAINT ck_m11_control_versions CHECK (((epoch >= 1) AND (revision >= 0)))
);

-- m11_priority_control_singleton_id_seq; Type: SEQUENCE; Schema: discovery; Owner: -
CREATE SEQUENCE discovery.m11_priority_control_singleton_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- m11_priority_control_singleton_id_seq; Type: SEQUENCE OWNED BY; Schema: discovery; Owner: -
ALTER SEQUENCE discovery.m11_priority_control_singleton_id_seq OWNED BY discovery.m11_priority_control.singleton_id;

-- m11_priority_execution; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_priority_execution (
    execution_id uuid NOT NULL,
    job_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    receipt_id uuid NOT NULL,
    attempt_no bigint NOT NULL,
    worker_id text NOT NULL,
    ceiling_bytes bigint NOT NULL,
    jamendo_max_bytes bigint NOT NULL,
    vault_max_bytes bigint NOT NULL,
    config_sha256 bytea NOT NULL,
    priority_epoch bigint NOT NULL,
    pin_device numeric(39,0) NOT NULL,
    pin_inode numeric(39,0) NOT NULL,
    nonce bytea NOT NULL,
    binding_sha256 bytea NOT NULL,
    protocol_version integer DEFAULT 2 NOT NULL,
    meter_scope text DEFAULT 'JAMENDO_MEDIA_RESPONSE_BODY_V1'::text NOT NULL,
    prepared_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    closed_at timestamp with time zone,
    closure_sha256 bytea,
    actual_bytes bigint,
    outcome text,
    settled_revision bigint,
    quarantined boolean DEFAULT false NOT NULL,
    evidence bytea,
    ceiling_enforced boolean DEFAULT false NOT NULL,
    CONSTRAINT ck_m11_execution_actual CHECK (((actual_bytes IS NULL) OR ((actual_bytes >= 0) AND (actual_bytes <= ceiling_bytes)))),
    CONSTRAINT ck_m11_execution_attempt CHECK ((attempt_no >= 1)),
    CONSTRAINT ck_m11_execution_binding CHECK (((octet_length(nonce) = 32) AND (octet_length(binding_sha256) = 32))),
    CONSTRAINT ck_m11_execution_ceiling CHECK (((ceiling_bytes >= 1) AND (ceiling_bytes <= 157286400))),
    CONSTRAINT ck_m11_execution_closed_pair CHECK (((closed_at IS NULL) = (closure_sha256 IS NULL))),
    CONSTRAINT ck_m11_execution_closure_digest CHECK (((closure_sha256 IS NULL) OR (octet_length(closure_sha256) = 32))),
    CONSTRAINT ck_m11_execution_config CHECK (((jamendo_max_bytes >= 1) AND (vault_max_bytes >= 1) AND (priority_epoch >= 1) AND (ceiling_bytes <= jamendo_max_bytes) AND (ceiling_bytes <= vault_max_bytes) AND (octet_length(config_sha256) = 32))),
    CONSTRAINT ck_m11_execution_evidence CHECK (((evidence IS NULL) OR (octet_length(evidence) = 4096))),
    CONSTRAINT ck_m11_execution_no_start CHECK (((outcome IS DISTINCT FROM 'NO_START'::text) OR ((actual_bytes IS NULL) AND (closed_at IS NOT NULL) AND (closure_sha256 IS NOT NULL)))),
    CONSTRAINT ck_m11_execution_outcome CHECK (((outcome IS NULL) OR (outcome = ANY (ARRAY['SUCCESS'::text, 'PROVIDER_FAILURE'::text, 'CANCELLED'::text, 'REVOKED'::text, 'CLOSED_UNMETERED'::text, 'NO_START'::text])))),
    CONSTRAINT ck_m11_execution_outcome_closed CHECK (((outcome IS NULL) OR (closed_at IS NOT NULL))),
    CONSTRAINT ck_m11_execution_pin CHECK ((((pin_device >= (0)::numeric) AND (pin_device <= '340282366920938463463374607431768211455'::numeric)) AND ((pin_inode >= (1)::numeric) AND (pin_inode <= '340282366920938463463374607431768211455'::numeric)))),
    CONSTRAINT ck_m11_execution_protocol CHECK (((protocol_version = 2) AND (meter_scope = 'JAMENDO_MEDIA_RESPONSE_BODY_V1'::text))),
    CONSTRAINT ck_m11_execution_settled_revision CHECK (((settled_revision IS NULL) OR ((settled_revision >= 0) AND (outcome IS NOT NULL)))),
    CONSTRAINT ck_m11_execution_settlement CHECK (((actual_bytes IS NULL) OR ((closed_at IS NOT NULL) AND (outcome IS NOT NULL) AND (evidence IS NOT NULL)))),
    CONSTRAINT ck_m11_execution_times CHECK ((((started_at IS NULL) OR (started_at >= prepared_at)) AND ((closed_at IS NULL) OR (closed_at >= COALESCE(started_at, prepared_at))))),
    CONSTRAINT ck_m11_execution_unmetered CHECK (((outcome IS DISTINCT FROM 'CLOSED_UNMETERED'::text) OR ((actual_bytes IS NULL) AND ceiling_enforced AND (closed_at IS NOT NULL) AND (closure_sha256 IS NOT NULL)))),
    CONSTRAINT ck_m11_execution_worker CHECK (((length(worker_id) >= 1) AND (length(worker_id) <= 300)))
);

-- m11_priority_no_start; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_priority_no_start (
    proof_id uuid NOT NULL,
    job_id uuid NOT NULL,
    worker_id text,
    attempt_no bigint,
    provider_ordinal integer NOT NULL,
    disposition text NOT NULL,
    checked_at timestamp with time zone NOT NULL,
    execution_bindings jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT ck_m11_no_start_attempt CHECK (((attempt_no IS NULL) OR (attempt_no >= 1))),
    CONSTRAINT ck_m11_no_start_disposition CHECK ((disposition = ANY (ARRAY['RESOURCE_WAIT'::text, 'CANCELLED'::text, 'RECOVERY'::text, 'DEMOTION'::text]))),
    CONSTRAINT ck_m11_no_start_execution_bindings CHECK (((jsonb_typeof(execution_bindings) = 'array'::text) AND (jsonb_array_length(execution_bindings) <= 4))),
    CONSTRAINT ck_m11_no_start_fence_pair CHECK (((attempt_no IS NULL) = (worker_id IS NULL))),
    CONSTRAINT ck_m11_no_start_ordinal CHECK (((provider_ordinal >= 1) AND (provider_ordinal <= 5))),
    CONSTRAINT ck_m11_no_start_resource_fence CHECK (((disposition <> 'RESOURCE_WAIT'::text) OR (attempt_no IS NOT NULL))),
    CONSTRAINT ck_m11_no_start_worker CHECK (((worker_id IS NULL) OR ((length(worker_id) >= 1) AND (length(worker_id) <= 300))))
);

-- m11_priority_queue_clock; Type: TABLE; Schema: discovery; Owner: -
CREATE TABLE discovery.m11_priority_queue_clock (
    job_id uuid NOT NULL,
    waiting_since timestamp with time zone NOT NULL,
    eligible boolean DEFAULT true NOT NULL
);

-- m11_priority_control singleton_id; Type: DEFAULT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_control ALTER COLUMN singleton_id SET DEFAULT nextval('discovery.m11_priority_control_singleton_id_seq'::regclass);

-- m11_allocation_slot m11_allocation_slot_permit_id_key; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_allocation_slot
    ADD CONSTRAINT m11_allocation_slot_permit_id_key UNIQUE (permit_id);

-- m11_allocation_slot m11_allocation_slot_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_allocation_slot
    ADD CONSTRAINT m11_allocation_slot_pkey PRIMARY KEY (execution_id);

-- m11_native_seal m11_native_seal_permit_id_key; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_native_seal
    ADD CONSTRAINT m11_native_seal_permit_id_key UNIQUE (permit_id);

-- m11_native_seal m11_native_seal_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_native_seal
    ADD CONSTRAINT m11_native_seal_pkey PRIMARY KEY (execution_id);

-- m11_never_spawned m11_never_spawned_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_never_spawned
    ADD CONSTRAINT m11_never_spawned_pkey PRIMARY KEY (execution_id);

-- m11_positive_no_go m11_positive_no_go_permit_id_key; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_positive_no_go
    ADD CONSTRAINT m11_positive_no_go_permit_id_key UNIQUE (permit_id);

-- m11_positive_no_go m11_positive_no_go_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_positive_no_go
    ADD CONSTRAINT m11_positive_no_go_pkey PRIMARY KEY (execution_id);

-- m11_priority_control m11_priority_control_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_control
    ADD CONSTRAINT m11_priority_control_pkey PRIMARY KEY (singleton_id);

-- m11_priority_execution m11_priority_execution_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_execution
    ADD CONSTRAINT m11_priority_execution_pkey PRIMARY KEY (execution_id);

-- m11_priority_execution m11_priority_execution_receipt_id_key; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_execution
    ADD CONSTRAINT m11_priority_execution_receipt_id_key UNIQUE (receipt_id);

-- m11_priority_no_start m11_priority_no_start_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_no_start
    ADD CONSTRAINT m11_priority_no_start_pkey PRIMARY KEY (proof_id);

-- m11_priority_queue_clock m11_priority_queue_clock_pkey; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_queue_clock
    ADD CONSTRAINT m11_priority_queue_clock_pkey PRIMARY KEY (job_id);

-- m11_priority_no_start uq_m11_no_start_fence_disposition; Type: CONSTRAINT; Schema: discovery; Owner: -
ALTER TABLE ONLY discovery.m11_priority_no_start
    ADD CONSTRAINT uq_m11_no_start_fence_disposition UNIQUE (job_id, attempt_no, worker_id, disposition);

-- ix_m11_allocation_slot_fence; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_allocation_slot_fence ON discovery.m11_allocation_slot USING btree (job_id, worker_id, attempt_no);

-- ix_m11_allocation_slot_owner; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_allocation_slot_owner ON discovery.m11_allocation_slot USING btree (owner_id, phase);

-- ix_m11_allocation_slot_pending_job; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_allocation_slot_pending_job ON discovery.m11_allocation_slot USING btree (job_id) WHERE (phase = ANY (ARRAY['RESERVED'::text, 'CONSUMED'::text]));

-- ix_m11_execution_job_fence; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_execution_job_fence ON discovery.m11_priority_execution USING btree (job_id, attempt_no);

-- ix_m11_execution_unsettled; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_execution_unsettled ON discovery.m11_priority_execution USING btree (closed_at, execution_id);

-- ix_m11_execution_unsettled_job; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_execution_unsettled_job ON discovery.m11_priority_execution USING btree (job_id) WHERE ((settled_revision IS NULL) OR quarantined);

-- ix_m11_native_seal_fence; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_native_seal_fence ON discovery.m11_native_seal USING btree (job_id, worker_id, attempt_no);

-- ix_m11_native_seal_owner; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_native_seal_owner ON discovery.m11_native_seal USING btree (owner_id, sealed_at);

-- ix_m11_never_spawned_owner; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_never_spawned_owner ON discovery.m11_never_spawned USING btree (owner_id, sealed_at);

-- ix_m11_never_spawned_proof; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_never_spawned_proof ON discovery.m11_never_spawned USING btree (no_start_proof_id);

-- ix_m11_no_start_job; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_no_start_job ON discovery.m11_priority_no_start USING btree (job_id, attempt_no);

-- ix_m11_positive_no_go_fence; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_positive_no_go_fence ON discovery.m11_positive_no_go USING btree (job_id, worker_id, attempt_no);

-- ix_m11_positive_no_go_owner; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_positive_no_go_owner ON discovery.m11_positive_no_go USING btree (owner_id, verified_at);

-- ix_m11_queue_clock_eligible_wait; Type: INDEX; Schema: discovery; Owner: -
CREATE INDEX ix_m11_queue_clock_eligible_wait ON discovery.m11_priority_queue_clock USING btree (eligible, waiting_since, job_id);

-- ix_m11_fair_job_owner; Type: INDEX; Schema: jobs; Owner: -
CREATE INDEX ix_m11_fair_job_owner ON jobs.job USING btree (((resource_wake_until IS NOT NULL)) DESC, resource_waiting, user_id, priority, scheduled_at, created_at, job_id) WHERE ((state = ANY (ARRAY['QUEUED'::text, 'RETRY_WAIT'::text])) AND (cancel_requested_at IS NULL));

-- m11_allocation_slot m11_allocation_slot_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_allocation_slot_guard BEFORE INSERT OR DELETE OR UPDATE ON discovery.m11_allocation_slot FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_allocation_slot();

-- m11_priority_execution m11_attested_positive_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_attested_positive_guard BEFORE UPDATE ON discovery.m11_priority_execution FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_attested_positive();

-- m11_priority_no_start m11_epoch_no_start_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_epoch_no_start_guard AFTER INSERT ON discovery.m11_priority_no_start FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_epoch_no_start();

-- m11_priority_execution m11_execution_joint_slot_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_execution_joint_slot_guard BEFORE INSERT ON discovery.m11_priority_execution FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_execution_slots();

-- m11_priority_execution m11_execution_retirement_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_execution_retirement_guard BEFORE DELETE ON discovery.m11_priority_execution FOR EACH ROW EXECUTE FUNCTION app_private.m11_protect_accounting_retirement();

-- m11_native_seal m11_native_seal_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_native_seal_guard BEFORE INSERT OR DELETE OR UPDATE ON discovery.m11_native_seal FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_native_seal();

-- m11_never_spawned m11_never_spawned_retirement_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_never_spawned_retirement_guard BEFORE DELETE OR UPDATE ON discovery.m11_never_spawned FOR EACH ROW EXECUTE FUNCTION app_private.m11_protect_never_spawned();

-- m11_never_spawned m11_never_spawned_slot_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_never_spawned_slot_guard BEFORE INSERT ON discovery.m11_never_spawned FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_execution_slots();

-- m11_priority_no_start m11_no_start_retirement_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_no_start_retirement_guard BEFORE DELETE OR UPDATE ON discovery.m11_priority_no_start FOR EACH ROW EXECUTE FUNCTION app_private.m11_protect_accounting_retirement();

-- m11_positive_no_go m11_positive_no_go_guard; Type: TRIGGER; Schema: discovery; Owner: -
CREATE TRIGGER m11_positive_no_go_guard BEFORE INSERT OR DELETE OR UPDATE ON discovery.m11_positive_no_go FOR EACH ROW EXECUTE FUNCTION app_private.m11_guard_positive_no_go();

-- FUNCTION m11_assert_epoch_accounted(uuid, text, bigint); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_epoch_accounted(uuid, text, bigint) FROM PUBLIC;

-- FUNCTION m11_assert_epoch_no_start(uuid, text, bigint); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_epoch_no_start(uuid, text, bigint) FROM PUBLIC;

-- FUNCTION m11_assert_native_slot(s discovery.m11_allocation_slot); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_native_slot(s discovery.m11_allocation_slot) FROM PUBLIC;

-- FUNCTION m11_assert_owner_retirable(target uuid); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_owner_retirable(target uuid) FROM PUBLIC;

-- FUNCTION m11_assert_positive_no_go(f discovery.m11_positive_no_go); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_positive_no_go(f discovery.m11_positive_no_go) FROM PUBLIC;

-- FUNCTION m11_assert_slot_replacement(s discovery.m11_allocation_slot); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_slot_replacement(s discovery.m11_allocation_slot) FROM PUBLIC;

-- FUNCTION m11_assert_slot_replacement_v1(s discovery.m11_allocation_slot); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_assert_slot_replacement_v1(s discovery.m11_allocation_slot) FROM PUBLIC;

-- FUNCTION m11_epoch_catalog_ready(target_job uuid, target_worker text, target_attempt bigint, require_no_start boolean); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_epoch_catalog_ready(target_job uuid, target_worker text, target_attempt bigint, require_no_start boolean) FROM PUBLIC;

-- FUNCTION m11_epoch_catalog_ready_v2(target_job uuid, target_worker text, target_attempt bigint, require_no_start boolean); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_epoch_catalog_ready_v2(target_job uuid, target_worker text, target_attempt bigint, require_no_start boolean) FROM PUBLIC;

-- FUNCTION m11_guard_allocation_slot(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_guard_allocation_slot() FROM PUBLIC;

-- FUNCTION m11_guard_attested_positive(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_guard_attested_positive() FROM PUBLIC;

-- FUNCTION m11_guard_epoch_no_start(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_guard_epoch_no_start() FROM PUBLIC;

-- FUNCTION m11_guard_execution_slots(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_guard_execution_slots() FROM PUBLIC;

-- FUNCTION m11_guard_native_seal(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_guard_native_seal() FROM PUBLIC;

-- FUNCTION m11_guard_positive_no_go(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_guard_positive_no_go() FROM PUBLIC;

-- FUNCTION m11_owner_jobs(target uuid); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_owner_jobs(target uuid) FROM PUBLIC;

-- FUNCTION m11_positive_no_go_association(f discovery.m11_positive_no_go); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_positive_no_go_association(f discovery.m11_positive_no_go) FROM PUBLIC;

-- FUNCTION m11_protect_accounting_retirement(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_protect_accounting_retirement() FROM PUBLIC;

-- FUNCTION m11_protect_never_spawned(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_protect_never_spawned() FROM PUBLIC;

-- FUNCTION m11_require_allocation_prefix(); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_require_allocation_prefix() FROM PUBLIC;

-- FUNCTION m11_retire_owner_accounting(target uuid); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_retire_owner_accounting(target uuid) FROM PUBLIC;

-- FUNCTION m11_same_allocation_binding(a jsonb, b jsonb, fields text[]); Type: ACL; Schema: app_private; Owner: -
REVOKE ALL ON FUNCTION app_private.m11_same_allocation_binding(a jsonb, b jsonb, fields text[]) FROM PUBLIC;

INSERT INTO app_private.privacy_prior_definition(name,definition) VALUES ('0107:m11:account.purge_account', $m11_prior$CREATE OR REPLACE FUNCTION account.purge_account(target uuid, accepted_request uuid)
 RETURNS bigint
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE intent account.account_deletion_request%ROWTYPE;
        account_row account.user_account%ROWTYPE;
        transaction_key xid8;
        step text;
        affected bigint;
        total bigint:=0;
        restore_authorized boolean;
        device_ids uuid[];
BEGIN
 PERFORM pg_advisory_xact_lock_shared(4707761689340236883);
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

 total:=total+app_private.purge_ml_serving_owner(target);
 total:=total+app_private.purge_face_owner(target);
 total:=total+app_private.purge_native_served_owner(target);
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
 $q$DELETE FROM library.favorite_artist_refresh_retry WHERE user_id=$1$q$,
 $q$DELETE FROM library.favorite_artist_projection WHERE user_id=$1$q$,
 $q$DELETE FROM library.favorite_artist_refresh WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.watch_auto_download_consent_revision WHERE user_id=$1$q$,
 $q$DELETE FROM discovery.watch_auto_download_consent WHERE user_id=$1$q$,
 $q$DELETE FROM library.artist_watch_choice WHERE user_id=$1$q$,
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
 $q$DELETE FROM ml.training_participant WHERE user_id=$1$q$,
 $q$DELETE FROM account.training_consent_operation WHERE user_id=$1$q$,
 $q$DELETE FROM account.training_consent WHERE user_id=$1$q$,
 $q$DELETE FROM account.user_account WHERE user_id=$1$q$
 ] LOOP
  EXECUTE step USING target,device_ids;
  GET DIAGNOSTICS affected=ROW_COUNT;
  total:=total+affected;
 END LOOP;
 -- Validate deferred match evidence while the protected exact-ID manifest still exists.
 SELECT string_agg(DISTINCT format('%I.%I',n.nspname,c.conname),',') INTO step
 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
 WHERE c.condeferrable AND c.conname<>'z_face_lifecycle_independent_commit'
 AND n.nspname IN ('account','audit','discovery','identity','importing','jobs','library',
  'ml','playlist','social','sync','vault','wave','app_private');
 IF step IS NOT NULL THEN EXECUTE 'SET CONSTRAINTS '||step||' IMMEDIATE'; END IF;
 PERFORM account.verify_owner_absent(target,device_ids);
 INSERT INTO account.account_purge_receipt VALUES(accepted_request,clock_timestamp(),total);
 DELETE FROM app_private.privacy_purge_entity WHERE transaction_id=transaction_key;
 DELETE FROM app_private.privacy_purge_context WHERE transaction_id=transaction_key;
 RETURN total;
END $function$
$m11_prior$);

INSERT INTO app_private.privacy_prior_definition(name,definition) VALUES ('0107:m11:account.verify_account_purge_ready', $m11_prior$CREATE OR REPLACE FUNCTION account.verify_account_purge_ready(target uuid)
 RETURNS void
 LANGUAGE plpgsql
 SET search_path TO 'pg_catalog'
AS $function$
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
END $function$
$m11_prior$);

INSERT INTO discovery.m11_priority_control(singleton_id,enabled,epoch,revision,state) VALUES (1,false,1,0,jsonb_set('{"$type": "PriorityState", "as_of": null, "revision": 0, "promotions": [], "reservations": [], "retry_tokens": [], "actual_charges": [], "aged_last_owner": null, "provider_starts": [], "no_start_receipts": [], "upper_bound_holds": [], "promoted_last_owner": null, "noninteractive_claims": 0}'::jsonb,'{as_of}',jsonb_build_object('$datetime',to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00')));

SET LOCAL check_function_bodies = true;

-- Every producer gets a prospective due clock, including HTTP producers.
CREATE FUNCTION app_private.m11_record_queue_clock() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, discovery AS $m11$
BEGIN
 IF TG_OP = 'DELETE' THEN
  DELETE FROM discovery.m11_priority_queue_clock WHERE job_id=OLD.job_id;
  RETURN OLD;
 END IF;
 INSERT INTO discovery.m11_priority_queue_clock(job_id,waiting_since,eligible)
 VALUES(NEW.job_id,GREATEST(NEW.created_at,NEW.scheduled_at),true);
 RETURN NEW;
END $m11$;
REVOKE ALL ON FUNCTION app_private.m11_record_queue_clock() FROM PUBLIC;
CREATE TRIGGER m11_record_queue_clock AFTER INSERT OR DELETE ON jobs.job
FOR EACH ROW EXECUTE FUNCTION app_private.m11_record_queue_clock();
INSERT INTO discovery.m11_priority_queue_clock(job_id,waiting_since,eligible)
SELECT job_id,GREATEST(clock_timestamp(),scheduled_at),true FROM jobs.job
WHERE state IN ('QUEUED','RETRY_WAIT') AND cancel_requested_at IS NULL;

