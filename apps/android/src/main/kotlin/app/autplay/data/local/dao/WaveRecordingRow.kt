package app.autplay.data.local.dao

data class VariationOutcomeRow(val refId: String, val kind: String?, val surface: String?, val plays: Long, val skips: Long)

data class WaveRecordingRow(
    val refId: String,
    val title: String?,
    val artist: String?,
    val recordingKind: String?,
    val versionText: String?,
    val recordingMbid: String? = null,
    val workMbid: String? = null,
    val performanceRole: String? = null,
    val originalRecordingMbid: String? = null,
    val catalogRecordingId: String? = null,
    val livePerformance: Boolean = false,
)

internal const val WAVE_RECORDINGS = """
    SELECT u.local_user_track_ref_id AS refId,
           CASE json_type(m.payload_json, '$.fields.title')
               WHEN 'text' THEN json_extract(m.payload_json, '$.fields.title')
               WHEN 'null' THEN NULL ELSE COALESCE(r.title, u.raw_title) END AS title,
           CASE json_type(m.payload_json, '$.fields.artist')
               WHEN 'text' THEN json_extract(m.payload_json, '$.fields.artist')
               WHEN 'null' THEN NULL ELSE COALESCE(r.display_artist, u.raw_artist) END AS artist,
           r.recording_kind AS recordingKind, r.version_text AS versionText,
           json_extract(m.payload_json, '$.fields.mb_recording_id') AS recordingMbid,
           json_extract(m.payload_json, '$.musicbrainz_performance_v1.work_id') AS workMbid,
           CASE WHEN json_extract(m.payload_json, '$.fields.mb_recording_id') =
                     json_extract(m.payload_json, '$.musicbrainz_performance_v1.recording_id')
                THEN json_extract(m.payload_json, '$.musicbrainz_performance_v1.role') END AS performanceRole,
           json_extract(m.payload_json, '$.musicbrainz_performance_v1.original_recording_id') AS originalRecordingMbid,
           COALESCE(u.server_recording_id, r.server_recording_id, u.local_recording_id) AS catalogRecordingId,
           coalesce(json_extract(m.payload_json, '$.musicbrainz_performance_v1.live'), 0) AS livePerformance
    FROM user_track_ref u
    LEFT JOIN recording_projection r ON r.local_recording_id = u.local_recording_id
    LEFT JOIN track_metadata_projection m ON m.local_user_track_ref_id = u.local_user_track_ref_id
        AND m.server_profile_id = u.server_profile_id AND json_valid(m.payload_json)
    """
