package app.autplay.domain.face

class FaceV2IdentityException : IllegalArgumentException("ml.face.invalid_v2_identity")

/** Immutable identity value; verification also binds the separate presentation map. */
data class FaceTimelineIdentityV2(
    val recordingId: String,
    val audioVariantId: String,
    val sourceSha256: String,
    val decodedSampleRate: Long,
    val decodedSampleCount: Long,
    val sourcePresentationMapSha256: String,
    val embeddingModelId: String,
    val embeddingManifestSha256: String,
    val semanticInterpreterId: String,
    val interpreterManifestSha256: String,
    val preprocessingSha256: String,
    val calibrationSha256: String,
    val executionProfileSha256: String,
) {
    init {
        val uuid = Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        val hash = Regex("[0-9a-f]{64}")
        if (!listOf(recordingId, audioVariantId, embeddingModelId, semanticInterpreterId)
                .all { it.matches(uuid) } ||
            !listOf(sourceSha256, sourcePresentationMapSha256, embeddingManifestSha256,
                interpreterManifestSha256, preprocessingSha256, calibrationSha256,
                executionProfileSha256).all { it.matches(hash) } ||
            decodedSampleRate !in 8_000L..384_000L ||
            decodedSampleCount !in 1L..33_177_600_000L
        ) throw FaceV2IdentityException()
    }
}
