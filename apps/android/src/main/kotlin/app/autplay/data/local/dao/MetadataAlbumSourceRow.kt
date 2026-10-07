package app.autplay.data.local.dao

/** Profile-scoped active library evidence, paged independently of the visible track list. */
public data class MetadataAlbumSourceRow(
    public val localUserTrackRefId: String,
    public val payloadJson: String,
    public val localRecordingId: String?,
    public val rawTitle: String?,
    public val rawArtist: String?,
    public val rawDurationMs: Long?,
    public val addedAtMs: Long,
    public val metadataUpdatedAtMs: Long,
)
