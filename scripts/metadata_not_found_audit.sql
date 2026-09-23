-- Aggregate-only, read-only audit for owner-reachable track metadata.
-- Run through psql against the production database; output contains no ref IDs or titles.
BEGIN;
SET TRANSACTION READ ONLY;

SELECT 'captured_at_utc' AS metric, (now() AT TIME ZONE 'UTC')::text AS value;

SELECT 'state' AS metric, state, count(*) AS rows
FROM library.track_metadata
GROUP BY state
ORDER BY state;

SELECT 'coverage' AS metric,
       count(*) AS rows,
       count(*) FILTER (WHERE nullif(document #>> '{fields,album}', '') IS NOT NULL) AS album,
       count(*) FILTER (WHERE nullif(document #>> '{fields,release_date}', '') IS NOT NULL) AS release_date,
       count(*) FILTER (WHERE nullif(document #>> '{fields,original_release_date}', '') IS NOT NULL) AS original_release_date,
       count(*) FILTER (WHERE artwork_sha256 IS NOT NULL) AS artwork,
       count(*) FILTER (WHERE document->'fields' IS NULL OR document->'fields' = '{}'::jsonb) AS no_fields
FROM library.track_metadata;

WITH refs AS (
    SELECT m.state, m.error_code, m.document, m.artwork_sha256,
           r.raw_title, r.raw_artist, r.raw_album, r.raw_duration_ms,
           v.audio_variant_id, e.availability_status
    FROM library.track_metadata AS m
    JOIN library.user_track_ref AS r USING (user_track_ref_id)
    LEFT JOIN vault.recording_canonical_variant AS v ON v.recording_id = r.recording_id
    LEFT JOIN library.library_entry AS e
      ON e.user_track_ref_id = r.user_track_ref_id
     AND e.user_id = r.user_id AND e.removed_at IS NULL
    WHERE r.deleted_at IS NULL
)
SELECT 'quality' AS metric, state, availability_status, count(*) AS rows,
       count(*) FILTER (WHERE nullif(btrim(raw_title), '') IS NULL) AS missing_title,
       count(*) FILTER (WHERE nullif(btrim(raw_artist), '') IS NULL) AS missing_artist,
       count(*) FILTER (WHERE nullif(btrim(raw_album), '') IS NULL) AS missing_album,
       count(*) FILTER (WHERE raw_duration_ms IS NULL) AS missing_duration,
       count(*) FILTER (WHERE audio_variant_id IS NOT NULL) AS has_canonical_audio,
       count(*) FILTER (WHERE audio_variant_id IS NOT NULL
         AND document->>'audio_variant_id' = audio_variant_id::text) AS processed_current_audio,
       count(*) FILTER (WHERE nullif(document #>> '{fields,album}', '') IS NOT NULL) AS embedded_or_provider_album,
       count(*) FILTER (WHERE artwork_sha256 IS NOT NULL) AS artwork
FROM refs
GROUP BY state, availability_status
ORDER BY state, availability_status;

SELECT 'failed' AS metric, m.error_code, j.state AS job_state,
       j.attempt_count, count(*) AS rows
FROM library.track_metadata AS m
LEFT JOIN jobs.job AS j ON j.job_id = m.job_id
WHERE m.state = 'FAILED'
GROUP BY m.error_code, j.state, j.attempt_count
ORDER BY rows DESC;

SELECT 'policy' AS metric, 'resource' AS kind,
       count(*) AS rows,
       count(*) FILTER (WHERE playback_ceiling IS NOT NULL AND transfer_ceiling IS NOT NULL)
         AS configured
FROM account.resource_quota_policy
UNION ALL
SELECT 'policy', 'internal_io', count(*),
       count(*) FILTER (WHERE active_limit IS NOT NULL AND workload_version >= 2)
FROM account.internal_io_policy;

COMMIT;
