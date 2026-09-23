package app.autplay.application.face

/** Persist this per profile after authenticated online acceptance and after each successful selection. */
data class FaceLeaseClockAnchorV2(
    val serverProfileId: String,
    val projectionId: String,
    val serverInstanceId: String,
    val serverIdentityEpoch: Long,
    val serverIdentityThumbprintSha256: String,
    val signedIssuedAtMs: Long,
    val acceptedElapsedRealtimeMs: Long,
    val bootCount: Long,
    val maxSeenWallMs: Long,
)

data class FaceLeaseClockReadingV2(
    val effectiveNowMs: Long,
    val advancedAnchor: FaceLeaseClockAnchorV2,
)

/** Pure monotonic lease clock. The caller owns trusted online time and durable atomic persistence. */
object FaceLeaseClockV2 {
    private const val MAX_JSON_INTEGER = 9_007_199_254_740_991L

    fun acceptOnline(
        projection: VerifiedFaceProjectionV2,
        wallNowMs: Long,
        elapsedRealtimeMs: Long,
        bootCount: Long,
        previous: FaceLeaseClockAnchorV2? = null,
    ): FaceLeaseClockReadingV2? {
        if (!validClockInput(wallNowMs, elapsedRealtimeMs, bootCount)) return null
        val previousMaximum = if (previous?.serverProfileId == projection.serverProfileId)
            previous.maxSeenWallMs else 0L
        if (previousMaximum !in 0..MAX_JSON_INTEGER) return null
        val effective = maxOf(wallNowMs, projection.issuedAtMs, previousMaximum)
        if (effective >= projection.authorizedUntilMs) return null
        val anchor = FaceLeaseClockAnchorV2(
            projection.serverProfileId, projection.projectionId, projection.serverInstanceId,
            projection.serverIdentityEpoch, projection.serverIdentityThumbprintSha256,
            projection.issuedAtMs, elapsedRealtimeMs, bootCount, effective,
        )
        return FaceLeaseClockReadingV2(effective, anchor)
    }

    fun advance(
        projection: VerifiedFaceProjectionV2,
        anchor: FaceLeaseClockAnchorV2?,
        wallNowMs: Long,
        elapsedRealtimeMs: Long,
        bootCount: Long,
    ): FaceLeaseClockReadingV2? {
        if (anchor == null || !validClockInput(wallNowMs, elapsedRealtimeMs, bootCount) ||
            anchor.serverProfileId != projection.serverProfileId ||
            anchor.projectionId != projection.projectionId ||
            anchor.serverInstanceId != projection.serverInstanceId ||
            anchor.serverIdentityEpoch != projection.serverIdentityEpoch ||
            anchor.serverIdentityThumbprintSha256 != projection.serverIdentityThumbprintSha256 ||
            anchor.signedIssuedAtMs != projection.issuedAtMs ||
            anchor.bootCount != bootCount ||
            anchor.acceptedElapsedRealtimeMs !in 0..MAX_JSON_INTEGER ||
            anchor.maxSeenWallMs !in 0..MAX_JSON_INTEGER ||
            elapsedRealtimeMs < anchor.acceptedElapsedRealtimeMs
        ) return null
        val elapsed = elapsedRealtimeMs - anchor.acceptedElapsedRealtimeMs
        if (elapsed > MAX_JSON_INTEGER - anchor.signedIssuedAtMs) return null
        val effective = maxOf(wallNowMs, anchor.maxSeenWallMs, anchor.signedIssuedAtMs + elapsed)
        if (effective >= projection.authorizedUntilMs) return null
        return FaceLeaseClockReadingV2(effective, anchor.copy(maxSeenWallMs = effective))
    }

    private fun validClockInput(wallNowMs: Long, elapsedRealtimeMs: Long, bootCount: Long): Boolean =
        wallNowMs in 0..MAX_JSON_INTEGER && elapsedRealtimeMs in 0..MAX_JSON_INTEGER &&
            bootCount in 0..MAX_JSON_INTEGER
}
