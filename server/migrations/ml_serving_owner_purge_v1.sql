ALTER TABLE ml.control_step_up_credential DROP CONSTRAINT fk_ml_step_up_credential_challenge,
 ADD CONSTRAINT fk_ml_step_up_credential_challenge FOREIGN KEY(registration_challenge_id)
 REFERENCES ml.control_step_up_challenge(challenge_id) ON DELETE NO ACTION DEFERRABLE;
ALTER TABLE ml.control_step_up_challenge DROP CONSTRAINT control_step_up_challenge_credential_id_fkey,
 ADD CONSTRAINT control_step_up_challenge_credential_id_fkey FOREIGN KEY(credential_id)
 REFERENCES ml.control_step_up_credential(credential_id) ON DELETE NO ACTION DEFERRABLE;
CREATE FUNCTION app_private.fence_ml_step_up_privacy_delete() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE identifier uuid;
BEGIN
 IF TG_TABLE_NAME='control_step_up_credential' THEN identifier:=OLD.credential_id;
 ELSE identifier:=OLD.challenge_id; END IF;
 IF NOT app_private.privacy_owner_allowed(OLD.actor_user_id) OR NOT
  app_private.privacy_entity_allowed('ml.'||TG_TABLE_NAME,identifier) THEN
  RAISE EXCEPTION 'ml_step_up_privacy_delete_not_authorized' USING ERRCODE='55000'; END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER z_ml_credential_privacy_delete BEFORE DELETE ON ml.control_step_up_credential
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_ml_step_up_privacy_delete();
CREATE TRIGGER z_ml_challenge_privacy_delete BEFORE DELETE ON ml.control_step_up_challenge
 FOR EACH ROW EXECUTE FUNCTION app_private.fence_ml_step_up_privacy_delete();
CREATE FUNCTION app_private.purge_ml_serving_owner(target uuid) RETURNS bigint
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
DECLARE transaction_key xid8; step text; total bigint:=0; affected bigint;
BEGIN
 IF NOT app_private.privacy_owner_allowed(target) THEN
  RAISE EXCEPTION 'privacy_purge_not_authorized' USING ERRCODE='55000'; END IF;
 IF (EXISTS(SELECT 1 FROM ml.serving_consent_current WHERE owner_user_id=target)
  OR EXISTS(SELECT 1 FROM ml.serving_consent_receipt WHERE owner_user_id=target))
  AND NOT EXISTS(SELECT 1 FROM ml.serving_consent_current
   WHERE owner_user_id=target AND decision='WITHDRAWN') THEN
  RAISE EXCEPTION 'serving_withdrawal_required' USING ERRCODE='55000'; END IF;
 -- The independent executor binds its system-generated negative operation to
 -- the admitted deletion request by request_sha256; those UUIDs are distinct.
 transaction_key:=pg_current_xact_id();
 INSERT INTO app_private.privacy_purge_entity
 SELECT transaction_key,'ml.registration_session_binding',b.challenge_id
 FROM ml.registration_session_binding b JOIN ml.control_step_up_challenge c USING(challenge_id)
 WHERE c.actor_user_id=target
 UNION ALL SELECT transaction_key,'ml.android_grant_binding',b.challenge_id
 FROM ml.android_grant_binding b JOIN ml.control_step_up_challenge c USING(challenge_id)
 WHERE c.actor_user_id=target
 UNION ALL SELECT transaction_key,'ml.serving_consent_current',operation_id
 FROM ml.serving_consent_current WHERE owner_user_id=target
 UNION ALL SELECT transaction_key,'ml.serving_consent_receipt',operation_id
 FROM ml.serving_consent_receipt WHERE owner_user_id=target
 UNION ALL SELECT transaction_key,'ml.control_step_up_receipt',challenge_id
 FROM ml.control_step_up_receipt WHERE actor_user_id=target
 UNION ALL SELECT transaction_key,'ml.control_step_up_challenge',challenge_id
 FROM ml.control_step_up_challenge WHERE actor_user_id=target
 UNION ALL SELECT transaction_key,'ml.control_step_up_credential',credential_id
 FROM ml.control_step_up_credential WHERE actor_user_id=target;
 SET CONSTRAINTS ml.fk_ml_step_up_credential_challenge,
  ml.control_step_up_challenge_credential_id_fkey DEFERRED;
 FOREACH step IN ARRAY ARRAY[
 $q$DELETE FROM ml.serving_consent_current WHERE owner_user_id=$1$q$,
 $q$DELETE FROM ml.serving_consent_receipt WHERE owner_user_id=$1$q$,
 $q$DELETE FROM ml.android_grant_binding WHERE challenge_id IN
  (SELECT challenge_id FROM ml.control_step_up_challenge WHERE actor_user_id=$1)$q$,
 $q$DELETE FROM ml.registration_session_binding WHERE challenge_id IN
  (SELECT challenge_id FROM ml.control_step_up_challenge WHERE actor_user_id=$1)$q$,
 $q$DELETE FROM ml.control_step_up_receipt WHERE actor_user_id=$1$q$,
 $q$DELETE FROM ml.control_step_up_challenge WHERE actor_user_id=$1$q$,
 $q$DELETE FROM ml.control_step_up_credential WHERE actor_user_id=$1$q$
 ] LOOP
  EXECUTE step USING target; GET DIAGNOSTICS affected=ROW_COUNT; total:=total+affected;
 END LOOP;
 RETURN total;
END $$;
REVOKE ALL ON FUNCTION app_private.fence_ml_step_up_privacy_delete(),
 app_private.purge_ml_serving_owner(uuid) FROM PUBLIC;
