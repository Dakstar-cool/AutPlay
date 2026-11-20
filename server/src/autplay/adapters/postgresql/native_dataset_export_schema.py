"""Fixed depth-two managed DATASET schema; historical definitions stay intact."""

from sqlalchemy import Connection, text

PRIOR_FUNCTIONS = (
    "app_private.guard_native_export()",
    "app_private.guard_native_export_link()",
    "app_private.invalidate_native_export_dependents()",
)

SCHEMA_SQL = r"""
ALTER TABLE ml.native_export
 DROP CONSTRAINT ck_native_export_kind,
 ADD CONSTRAINT ck_native_export_kind CHECK(kind IN ('PACKET','AGGREGATE','DATASET')),
 DROP CONSTRAINT ck_native_export_metadata,
 ADD CONSTRAINT ck_native_export_metadata CHECK(jsonb_typeof(allocation)='object' AND
  octet_length(allocation::text)<=524288 AND jsonb_typeof(inventory)='array' AND
  jsonb_array_length(inventory)<=CASE WHEN kind='DATASET' THEN 63 ELSE 11 END);
CREATE OR REPLACE FUNCTION app_private.guard_native_export() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'native_export_cleanup_obligation_retained' USING ERRCODE='55000';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'ALLOCATED' OR NEW.inventory<>'[]'::jsonb OR NEW.manifest_sha256 IS NOT NULL
   OR NEW.expires_at<=clock_timestamp() THEN
   RAISE EXCEPTION 'native_export_allocation_required' USING ERRCODE='55000';
  END IF;
 ELSE
  IF (NEW.export_id,NEW.generation,NEW.kind,NEW.allocation,NEW.allocation_sha256,NEW.expires_at)
    IS DISTINCT FROM
    (OLD.export_id,OLD.generation,OLD.kind,OLD.allocation,OLD.allocation_sha256,OLD.expires_at)
   OR NOT (NEW.state=OLD.state OR (OLD.state='ALLOCATED' AND NEW.state IN ('READY','INVALIDATED'))
    OR (OLD.state='READY' AND NEW.state='INVALIDATED')
    OR (OLD.state='INVALIDATED' AND NEW.state='DELETED'))
   OR (OLD.state<>'ALLOCATED' AND (NEW.inventory,NEW.manifest_sha256)
       IS DISTINCT FROM (OLD.inventory,OLD.manifest_sha256)) THEN
   RAISE EXCEPTION 'native_export_transition_invalid' USING ERRCODE='55000';
  END IF;
 END IF;
 IF NEW.state='READY' THEN
  IF NEW.expires_at<=clock_timestamp() OR
   (SELECT count(*) FROM ml.native_export_owner o WHERE
    (o.export_id,o.generation)=(NEW.export_id,NEW.generation))
     IS DISTINCT FROM jsonb_array_length(NEW.allocation->'owners') OR
   jsonb_array_length(NEW.allocation->'owners') NOT BETWEEN 1 AND 1024 OR
   jsonb_array_length(NEW.inventory)<>(CASE WHEN NEW.kind='PACKET' THEN 11
    WHEN NEW.kind='DATASET' THEN 63 ELSE 1 END) OR
   (SELECT count(*) FROM ml.native_export_dependency d WHERE
    (d.export_id,d.generation)=(NEW.export_id,NEW.generation))
     IS DISTINCT FROM jsonb_array_length(NEW.allocation->'dependencies') OR
   EXISTS(SELECT 1 FROM ml.native_export_dependency d JOIN ml.native_export p
    ON (p.export_id,p.generation)=(d.packet_id,d.packet_generation)
    WHERE (d.export_id,d.generation)=(NEW.export_id,NEW.generation) AND
     (p.kind<>(CASE WHEN NEW.kind='DATASET' THEN 'AGGREGATE' ELSE 'PACKET' END)
      OR p.state<>'READY' OR p.manifest_sha256<>d.manifest_sha256
      OR p.expires_at<NEW.expires_at)) THEN
   RAISE EXCEPTION 'native_export_authority_unavailable' USING ERRCODE='55000';
  END IF;
 END IF;

 IF NEW.kind='DATASET' THEN
  IF jsonb_typeof(NEW.allocation->'dependencies') IS DISTINCT FROM 'array'
   OR jsonb_array_length(NEW.allocation->'dependencies')<>1 THEN
   RAISE EXCEPTION 'native_dataset_dependency_shape' USING ERRCODE='55000';
  END IF;
  IF NEW.inventory<>'[]'::jsonb THEN
   IF jsonb_array_length(NEW.inventory)<>63 OR EXISTS(
    WITH expected(name,maximum) AS (VALUES
    ('catalog-manifest.json',2097152),
    ('dataset-provenance.json',1048576),
    ('parent-split-plan.json',2097152),
    ('policy-membership.json',2097152),
    ('source-approval.json',65536),
    ('source-manifest.json',2097152),
    ('source-rekey-plan.json',2097152),
    ('teacher-calibration.json',100663296),
    ('teacher-manifest.json',8192),
    ('test/candidate_mask.npy',8392704),
    ('test/candidate_sids.npy',25169920),
    ('test/cutoff_at_ms.npy',12288),
    ('test/example_sha256.npy',69632),
    ('test/history_actions.npy',4198400),
    ('test/history_age_buckets.npy',4198400),
    ('test/history_mask.npy',4198400),
    ('test/history_origins.npy',4198400),
    ('test/history_sids.npy',12587008),
    ('test/manifest.json',1048576),
    ('test/observed_at_ms.npy',12288),
    ('test/ranking_label_mask.npy',16781312),
    ('test/ranking_labels.npy',16781312),
    ('test/request_sha256.npy',69632),
    ('test/seed.npy',12288),
    ('test/target_sids.npy',28672),
    ('test/teacher_mask.npy',16781312),
    ('test/teacher_probabilities.npy',16781312),
    ('train/candidate_mask.npy',8392704),
    ('train/candidate_sids.npy',25169920),
    ('train/cutoff_at_ms.npy',12288),
    ('train/example_sha256.npy',69632),
    ('train/history_actions.npy',4198400),
    ('train/history_age_buckets.npy',4198400),
    ('train/history_mask.npy',4198400),
    ('train/history_origins.npy',4198400),
    ('train/history_sids.npy',12587008),
    ('train/manifest.json',1048576),
    ('train/observed_at_ms.npy',12288),
    ('train/ranking_label_mask.npy',16781312),
    ('train/ranking_labels.npy',16781312),
    ('train/request_sha256.npy',69632),
    ('train/seed.npy',12288),
    ('train/target_sids.npy',28672),
    ('train/teacher_mask.npy',16781312),
    ('train/teacher_probabilities.npy',16781312),
    ('validation/candidate_mask.npy',8392704),
    ('validation/candidate_sids.npy',25169920),
    ('validation/cutoff_at_ms.npy',12288),
    ('validation/example_sha256.npy',69632),
    ('validation/history_actions.npy',4198400),
    ('validation/history_age_buckets.npy',4198400),
    ('validation/history_mask.npy',4198400),
    ('validation/history_origins.npy',4198400),
    ('validation/history_sids.npy',12587008),
    ('validation/manifest.json',1048576),
    ('validation/observed_at_ms.npy',12288),
    ('validation/ranking_label_mask.npy',16781312),
    ('validation/ranking_labels.npy',16781312),
    ('validation/request_sha256.npy',69632),
    ('validation/seed.npy',12288),
    ('validation/target_sids.npy',28672),
    ('validation/teacher_mask.npy',16781312),
    ('validation/teacher_probabilities.npy',16781312)
    )
    SELECT 1 FROM jsonb_array_elements(NEW.inventory) item
    LEFT JOIN expected ON expected.name=item->>'name'
    WHERE expected.name IS NULL OR jsonb_typeof(item) IS DISTINCT FROM 'object'
     OR (SELECT count(*) FROM jsonb_object_keys(item))<>3
     OR jsonb_typeof(item->'name') IS DISTINCT FROM 'string'
     OR jsonb_typeof(item->'sha256') IS DISTINCT FROM 'string'
     OR COALESCE(item->>'sha256','') !~ '^[0-9a-f]{64}$'
     OR CASE WHEN jsonb_typeof(item->'size')='number'
       AND (item->>'size') ~ '^[0-9]+$'
      THEN (item->>'size')::numeric NOT BETWEEN 1 AND expected.maximum
      ELSE true END
   ) OR (SELECT count(DISTINCT item->>'name') FROM jsonb_array_elements(NEW.inventory) item)<>63
    OR (SELECT sum(CASE WHEN jsonb_typeof(item->'size')='number'
      AND (item->>'size') ~ '^[0-9]+$' THEN (item->>'size')::numeric ELSE 268435457 END)
     FROM jsonb_array_elements(NEW.inventory) item)>268435456 THEN
    RAISE EXCEPTION 'native_dataset_inventory_invalid' USING ERRCODE='55000';
   END IF;
  END IF;
  IF NEW.state='READY' THEN
   IF EXISTS(
    SELECT 1 FROM ml.native_export_dependency d JOIN ml.native_export a
     ON (a.export_id,a.generation)=(d.packet_id,d.packet_generation)
    WHERE (d.export_id,d.generation)=(NEW.export_id,NEW.generation) AND
     (a.allocation->'root' IS DISTINCT FROM NEW.allocation->'root'
      OR a.allocation->'owners' IS DISTINCT FROM NEW.allocation->'owners'
      OR a.expires_at<=clock_timestamp()
      OR (SELECT count(*) FROM ml.native_export_dependency x WHERE
       (x.export_id,x.generation)=(a.export_id,a.generation)) NOT BETWEEN 1 AND 16
      OR EXISTS(
       SELECT 1 FROM ml.native_export_dependency x JOIN ml.native_export p
        ON (p.export_id,p.generation)=(x.packet_id,x.packet_generation)
       WHERE (x.export_id,x.generation)=(a.export_id,a.generation) AND
        (p.kind<>'PACKET' OR p.state<>'READY' OR p.manifest_sha256<>x.manifest_sha256
         OR p.expires_at<NEW.expires_at OR p.expires_at<=clock_timestamp()
         OR p.allocation->'root' IS DISTINCT FROM NEW.allocation->'root'))
      OR NEW.allocation->'owners' IS DISTINCT FROM (
       SELECT jsonb_agg(value ORDER BY value->>'owner_tag') FROM (
        SELECT DISTINCT value FROM ml.native_export_dependency x
        JOIN ml.native_export p ON (p.export_id,p.generation)=(x.packet_id,x.packet_generation)
        CROSS JOIN LATERAL jsonb_array_elements(p.allocation->'owners') value
        WHERE (x.export_id,x.generation)=(a.export_id,a.generation)
       ) owner_union))) THEN
    RAISE EXCEPTION 'native_dataset_ancestor_authority_unavailable' USING ERRCODE='55000';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION app_private.guard_native_export_link()
 RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent ml.native_export; packet ml.native_export;
BEGIN
 IF TG_OP='UPDATE' THEN
  RAISE EXCEPTION 'native_export_membership_immutable' USING ERRCODE='55000';
 ELSIF TG_OP='DELETE' THEN
  IF TG_TABLE_NAME='native_export_dependency' THEN
   RAISE EXCEPTION 'native_export_membership_retained' USING ERRCODE='55000';
  END IF;
  -- A restored ACTIVE account is still visible during its BEFORE DELETE trigger.
  -- Only the existing owner- and transaction-bound private purge context authorizes removal.
  IF EXISTS(
   SELECT 1 FROM account.user_account WHERE user_id=OLD.user_id
    AND status='ACTIVE' AND deleted_at IS NULL
  ) AND NOT app_private.privacy_owner_allowed(OLD.user_id) THEN
   RAISE EXCEPTION 'native_export_membership_retained' USING ERRCODE='55000';
  END IF;
  RETURN OLD;
 END IF;
 SELECT * INTO parent FROM ml.native_export
  WHERE (export_id,generation)=(NEW.export_id,NEW.generation) FOR UPDATE;
 IF parent.state IS DISTINCT FROM 'ALLOCATED' THEN
  RAISE EXCEPTION 'native_export_allocation_required' USING ERRCODE='55000';
 END IF;
 IF TG_TABLE_NAME='native_export_owner' THEN
  IF NOT EXISTS(SELECT 1 FROM account.user_account WHERE user_id=NEW.user_id AND status='ACTIVE') OR
   NOT EXISTS(SELECT 1 FROM jsonb_array_elements(parent.allocation->'owners') item
     WHERE item->>'owner_tag'=NEW.owner_tag) OR EXISTS(
     SELECT 1 FROM ml.native_export_owner
      WHERE (export_id,generation)=(NEW.export_id,NEW.generation)
      AND owner_tag=NEW.owner_tag) THEN
   RAISE EXCEPTION 'native_export_owner_mismatch' USING ERRCODE='55000';
  END IF;
 ELSE
  SELECT * INTO packet FROM ml.native_export
   WHERE (export_id,generation)=(NEW.packet_id,NEW.packet_generation) FOR UPDATE;
  IF parent.kind NOT IN ('AGGREGATE','DATASET') OR packet.kind IS DISTINCT FROM
    (CASE WHEN parent.kind='DATASET' THEN 'AGGREGATE' ELSE 'PACKET' END) OR packet.state<>'READY'
   OR packet.manifest_sha256<>NEW.manifest_sha256 OR packet.expires_at<parent.expires_at OR
   packet.allocation->'root' IS DISTINCT FROM parent.allocation->'root' OR NOT EXISTS(
    SELECT 1 FROM jsonb_array_elements(parent.allocation->'dependencies') item
    WHERE item->'ref'->>'export_id'=NEW.packet_id::text AND
     item->'ref'->>'generation'=NEW.packet_generation::text AND
     item->>'manifest_sha256'=NEW.manifest_sha256) THEN
   RAISE EXCEPTION 'native_export_dependency_mismatch' USING ERRCODE='55000';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION app_private.invalidate_native_export_dependents()
 RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state IN ('INVALIDATED','DELETED') AND NEW.state<>OLD.state
  AND NEW.kind IN ('PACKET','AGGREGATE') THEN
  UPDATE ml.native_export e SET state='INVALIDATED',reason='DEPENDENCY_INVALIDATED'
   FROM ml.native_export_dependency d
   WHERE (d.packet_id,d.packet_generation)=(NEW.export_id,NEW.generation)
    AND (e.export_id,e.generation)=(d.export_id,d.generation) AND e.state IN ('ALLOCATED','READY')
    AND ((NEW.kind='PACKET' AND e.kind='AGGREGATE')
      OR (NEW.kind='AGGREGATE' AND e.kind='DATASET'));
 END IF;
 RETURN NEW;
END $$;
"""


def install(connection: Connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE app_private.native_dataset_prior_definition"
            "(name text PRIMARY KEY,ddl text NOT NULL)"
        )
    )
    connection.execute(
        text("REVOKE ALL ON app_private.native_dataset_prior_definition FROM PUBLIC")
    )
    for name in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT pg_get_functiondef(to_regprocedure(:name))"), {"name": name}
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_dataset_prior_function_missing")
        connection.execute(
            text(
                "INSERT INTO app_private.native_dataset_prior_definition(name,ddl) "
                "VALUES(:name,:ddl)"
            ),
            {"name": name, "ddl": definition},
        )
    connection.execute(text(SCHEMA_SQL))
    for name in PRIOR_FUNCTIONS:
        connection.execute(text(f"REVOKE ALL ON FUNCTION {name} FROM PUBLIC"))


def remove(connection: Connection) -> None:
    connection.execute(
        text("""
LOCK TABLE ml.native_export,ml.native_export_dependency,ml.native_export_owner
 IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN IF EXISTS(SELECT 1 FROM ml.native_export WHERE kind='DATASET') THEN
 RAISE EXCEPTION 'native_dataset_cleanup_obligation_retained' USING ERRCODE='55000';
END IF; END $$;
""")
    )
    for name in PRIOR_FUNCTIONS:
        definition = connection.scalar(
            text("SELECT ddl FROM app_private.native_dataset_prior_definition WHERE name=:name"),
            {"name": name},
        )
        if not isinstance(definition, str):
            raise RuntimeError("native_dataset_prior_function_missing")
        connection.execute(text(definition))
    connection.execute(
        text("""
ALTER TABLE ml.native_export
 DROP CONSTRAINT ck_native_export_kind,
 ADD CONSTRAINT ck_native_export_kind CHECK(kind IN ('PACKET','AGGREGATE')),
 DROP CONSTRAINT ck_native_export_metadata,
 ADD CONSTRAINT ck_native_export_metadata CHECK(jsonb_typeof(allocation)='object' AND
  octet_length(allocation::text)<=524288 AND jsonb_typeof(inventory)='array' AND
  jsonb_array_length(inventory)<=11);
DROP TABLE app_private.native_dataset_prior_definition;
""")
    )
