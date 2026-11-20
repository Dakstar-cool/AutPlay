IF s.snapshot_document->'tracks' IS DISTINCT FROM (SELECT coalesce(jsonb_agg(to_jsonb(current_tracks) ORDER BY current_tracks.recording_id),'[]'::jsonb) FROM (

WITH history AS (
    SELECT recording_id,
           count(*) FILTER (WHERE NOT excluded_from_taste) AS play_count,
           count(*) FILTER (
               WHERE NOT excluded_from_taste AND event_origin <> 'RECOMMENDED'
           ) AS organic_play_count,
           count(*) FILTER (
               WHERE NOT excluded_from_taste AND event_origin = 'RECOMMENDED'
           ) AS recommended_play_count,
           max(started_at) FILTER (WHERE NOT excluded_from_taste) AS last_played_at
    FROM library.listening_event AS listen
    WHERE user_id = c.owner_user_id AND recording_id IS NOT NULL
      -- Dormant normalized ingress must not alter the legacy v1 snapshot.
      AND NOT EXISTS (
          SELECT 1 FROM sync.device_event_inbox AS inbox
          WHERE inbox.event_id = listen.listening_event_id
            AND inbox.ingress_validation_version = 2
      )
    GROUP BY recording_id
), release_metadata AS (
    SELECT DISTINCT ON (rt.recording_id)
           rt.recording_id, rel.release_id, rel.release_date
    FROM catalog.release_track rt
    JOIN catalog.medium medium ON medium.medium_id = rt.medium_id
    JOIN catalog.release rel ON rel.release_id = medium.release_id
    WHERE rt.deleted_at IS NULL AND medium.deleted_at IS NULL AND rel.deleted_at IS NULL
    ORDER BY rt.recording_id, rel.release_date DESC NULLS LAST, rel.release_id
), library_candidates AS (
    SELECT recording.recording_id, ref.user_track_ref_id,
           recording.artist_credit_id::text AS artist_key,
           release.release_id::text AS release_key,
           ARRAY[
               recording.recording_kind,
               coalesce(recording.version_text, ''),
               recording.normalized_title
           ]::text[] AS metadata_tokens,
           entry.availability_status AS availability,
           true AS authorized,
           recording.identity_status,
           coalesce(preference.preference, 'NEUTRAL') AS preference,
           coalesce(preference.excluded_from_taste, false) AS excluded,
           coalesce(history.play_count, 0)::bigint AS play_count,
           coalesce(history.organic_play_count, 0)::bigint AS organic_play_count,
           coalesce(history.recommended_play_count, 0)::bigint AS recommended_play_count,
           CASE WHEN history.last_played_at IS NULL THEN NULL ELSE
               floor(extract(epoch FROM history.last_played_at) * 1000)::bigint
           END AS last_played_at_ms,
           floor(extract(epoch FROM entry.added_at) * 1000)::bigint AS added_at_ms,
           CASE WHEN release.release_date IS NULL THEN NULL ELSE
               release.release_date - DATE '0001-01-01' + 1
           END::integer AS release_date_ordinal,
           0 AS source_priority
    FROM library.library_entry entry
    JOIN library.user_track_ref ref ON ref.user_track_ref_id = entry.user_track_ref_id
    JOIN catalog.recording recording ON recording.recording_id = ref.recording_id
    LEFT JOIN library.user_track_preference preference
        ON preference.user_track_ref_id = ref.user_track_ref_id
    LEFT JOIN history ON history.recording_id = recording.recording_id
    LEFT JOIN release_metadata release ON release.recording_id = recording.recording_id
    WHERE entry.user_id = c.owner_user_id
      AND entry.removed_at IS NULL
      AND ref.deleted_at IS NULL
      AND recording.deleted_at IS NULL
), vault_candidates AS (
    SELECT recording.recording_id, owned_ref.user_track_ref_id,
           recording.artist_credit_id::text AS artist_key,
           release.release_id::text AS release_key,
           ARRAY[
               recording.recording_kind,
               coalesce(recording.version_text, ''),
               recording.normalized_title
           ]::text[] AS metadata_tokens,
           'VAULT'::text AS availability,
           true AS authorized,
           recording.identity_status,
           coalesce(owned_ref.preference, 'NEUTRAL') AS preference,
           coalesce(owned_ref.excluded, false) AS excluded,
           coalesce(history.play_count, 0)::bigint AS play_count,
           coalesce(history.organic_play_count, 0)::bigint AS organic_play_count,
           coalesce(history.recommended_play_count, 0)::bigint AS recommended_play_count,
           CASE WHEN history.last_played_at IS NULL THEN NULL ELSE
               floor(extract(epoch FROM history.last_played_at) * 1000)::bigint
           END AS last_played_at_ms,
           floor(extract(epoch FROM variant.created_at) * 1000)::bigint AS added_at_ms,
           CASE WHEN release.release_date IS NULL THEN NULL ELSE
               release.release_date - DATE '0001-01-01' + 1
           END::integer AS release_date_ordinal,
           1 AS source_priority
    FROM vault.acquisition_record acquisition
    JOIN vault.audio_variant variant ON variant.audio_variant_id = acquisition.audio_variant_id
    JOIN vault.vault_object object ON object.vault_object_id = variant.vault_object_id
    JOIN catalog.recording recording ON recording.recording_id = variant.recording_id
    LEFT JOIN history ON history.recording_id = recording.recording_id
    LEFT JOIN release_metadata release ON release.recording_id = recording.recording_id
    LEFT JOIN LATERAL (
        SELECT ref.user_track_ref_id, preference.preference,
               preference.excluded_from_taste AS excluded
        FROM library.user_track_ref ref
        LEFT JOIN library.user_track_preference preference
            ON preference.user_track_ref_id = ref.user_track_ref_id
        WHERE ref.user_id = c.owner_user_id
          AND ref.recording_id = recording.recording_id
          AND ref.deleted_at IS NULL
        ORDER BY ref.updated_at DESC, ref.user_track_ref_id
        LIMIT 1
    ) owned_ref ON true
    WHERE acquisition.authorized_by_user_id = c.owner_user_id
      AND variant.validation_status = 'VALID'
      AND variant.deleted_at IS NULL
      AND object.commit_status = 'COMMITTED'
      AND EXISTS (
          SELECT 1 FROM vault.vault_replica replica
          WHERE replica.vault_object_id = object.vault_object_id
            AND replica.replica_status = 'AVAILABLE'
      )
      AND recording.deleted_at IS NULL
), combined AS (
    SELECT * FROM library_candidates
    UNION ALL
    SELECT * FROM vault_candidates
), deduped AS (
    SELECT DISTINCT ON (recording_id)
           recording_id, user_track_ref_id, artist_key, release_key, metadata_tokens,
           availability, authorized, identity_status, preference, excluded,
           play_count, organic_play_count, recommended_play_count, last_played_at_ms,
           added_at_ms, release_date_ordinal
    FROM combined
    ORDER BY recording_id, source_priority, added_at_ms DESC
), selected AS (
    SELECT *
    FROM deduped
    ORDER BY added_at_ms DESC, recording_id
    LIMIT 5000
)
SELECT
       recording_id, user_track_ref_id, artist_key, release_key, metadata_tokens,
       availability, authorized, identity_status, preference, excluded,
       play_count, organic_play_count, recommended_play_count, last_played_at_ms,
       added_at_ms, release_date_ordinal
FROM selected
ORDER BY recording_id
) current_tracks) OR r.interaction_watermark IS DISTINCT FROM (SELECT coalesce(max(server_sequence),0) FROM sync.sync_event WHERE user_id=c.owner_user_id AND ingress_validation_version=1 AND event_type IN ('LISTENING_EVENT_RECORDED','RECOMMENDATION_IMPRESSION_RECORDED','RECOMMENDATION_FEEDBACK_RECORDED','USER_TRACK_PREFERENCE_SET')) THEN
 RAISE EXCEPTION 'NATIVE_ATOMIC_INITIAL_SNAPSHOT_INCOMPLETE' USING ERRCODE='23514'; END IF;
