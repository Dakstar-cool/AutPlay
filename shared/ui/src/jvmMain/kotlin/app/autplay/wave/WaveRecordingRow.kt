package app.autplay.data.local.dao

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
