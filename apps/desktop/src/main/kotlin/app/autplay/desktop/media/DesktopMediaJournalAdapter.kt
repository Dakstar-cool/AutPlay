package app.autplay.desktop.media

import app.autplay.application.desktop.account.*
import app.autplay.application.desktop.media.*

/** Actual J typed APIs; playback observations remain process-only. No authority is stored in J. */
internal class DesktopMediaJournalAdapter(
    private val journal: DesktopAccountMedia,
    private val work: DesktopMediaWorkOwner,
    private val lease: (AccountLocalCapture) -> AccountLease,
    private val authority: DesktopMediaOwnerAdmissionPort,
) : DesktopMediaJournalEvidencePort {
    override fun startAcceptObservation(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        observation: MediaSourceObservation, admission: MediaLiveAdmission): MediaOwnedWork<MediaSourceEvidence> = work.start { cancelled ->
        val selected = lease(capture.network.local)
        val projected = journal.projection(selected, capture.network.projection.localTrackRefId)
        mediaCheck(projected == capture.network.projection && !cancelled.get(), MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        DesktopMediaSourcePolicy.requireSnapshot(capture, intent, observation.snapshot)
        val download = intent as? SourceIntent.BoundVaultDownload
        val evidence = MediaSourceEvidence(download?.admissionEvidenceId ?: MediaId.random(), download?.intentId,
            if (download == null) MediaObservationKind.ACQUISITION else MediaObservationKind.CONTINUATION,
            selected.storeId, selected.scopeId, capture.network.local.localOwner, capture.network.bindingCommitId,
            requireNotNull(projected).localTrackRefId, projected.projectionRevision, projected.userTrackRefRowVersion,
            projected.recordingId, observation.snapshot)
        authority.withCurrent(MediaAdmissionRequest(capture, intent, MediaAdmissionStep.SNAPSHOT_RESULT,
            observation, evidence, null, null)) { }
        if (download != null) {
            val stored = journal.readIntent(selected, download.intentId) ?: throw mediaFailure(MediaCode.INTENT_STALE, MediaStage.RESOLVE)
            val original = journal.readEvidence(selected, download.sourceEvidenceId)
                ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
            mediaCheck(stored.record.revision == download.intentRevision && stored.desiredState == "DOWNLOAD" &&
                stored.target?.sourceEvidenceId == original.evidenceId && original.snapshot.sha256 == evidence.snapshot.sha256 &&
                original.snapshot.audioVariantId == evidence.snapshot.audioVariantId &&
                original.snapshot.recordingId == evidence.snapshot.recordingId &&
                original.snapshot.userTrackRefId == evidence.snapshot.userTrackRefId,
                MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
            journal.retainObservation(selected, MediaId.random(), evidence)
            val readback = journal.readEvidence(selected, evidence.evidenceId)
                ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
            mediaCheck(readback.snapshot.sha256 == evidence.snapshot.sha256 &&
                readback.snapshot.audioVariantId == evidence.snapshot.audioVariantId && readback.scopeId == evidence.scopeId,
                MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        }
        evidence
    }

    suspend fun bindRequested(capture: SelectionCapture.VaultNetwork, requested: MediaDownloadIntent,
        observation: MediaSourceObservation): MediaDownloadIntent {
        val selected = lease(capture.network.local)
        val projection = journal.projection(selected, requested.localTrackRefId)
            ?: throw mediaFailure(MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        mediaCheck(requested.target == null && requested.coarseState == "REQUESTED" && projection == capture.network.projection,
            MediaCode.RECONCILIATION_REQUIRED, MediaStage.RESOLVE)
        DesktopMediaSourcePolicy.requireSnapshot(capture, SourceIntent.VaultPlayback(projection, MediaId.random()), observation.snapshot)
        val evidence = MediaSourceEvidence(MediaId.random(), requested.intentId, MediaObservationKind.ACQUISITION,
            selected.storeId, selected.scopeId, capture.network.local.localOwner, capture.network.bindingCommitId,
            projection.localTrackRefId, projection.projectionRevision, projection.userTrackRefRowVersion,
            projection.recordingId, observation.snapshot)
        return journal.bindDownload(selected, MediaId.random(), requested.record.revision, evidence, observation.snapshot.totalBytes)
    }
}

/** Closed mapping is source admission only; native decoder/container qualification stays open. */
internal object DesktopMediaFormats : DesktopMediaFormatAdmissionPort {
    override fun admit(codec: String, container: String, mediaType: String): MediaFormat = when {
        codec in setOf("mp3", "MP3") && container in setOf("mp3", "MP3", "mpeg") && mediaType == "audio/mpeg" -> MediaFormat.MP3
        codec in setOf("aac", "AAC") && container in setOf("adts", "ADTS", "aac") && mediaType == "audio/aac" -> MediaFormat.AAC
        codec in setOf("aac", "AAC") && container in setOf("m4a", "mp4", "M4A", "MP4") && mediaType in setOf("audio/mp4", "audio/x-m4a") -> MediaFormat.M4A
        codec in setOf("flac", "FLAC") && container in setOf("flac", "FLAC") && mediaType in setOf("audio/flac", "audio/x-flac") -> MediaFormat.FLAC
        codec in setOf("opus", "OPUS") && container in setOf("ogg", "OGG", "opus") && mediaType in setOf("audio/ogg", "audio/opus") -> MediaFormat.OPUS
        codec in setOf("pcm", "PCM", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le") &&
            container in setOf("wav", "WAV") && mediaType in setOf("audio/wav", "audio/x-wav", "audio/vnd.wave") -> MediaFormat.WAV
        else -> throw mediaFailure(MediaCode.UNSUPPORTED_MEDIA, MediaStage.RESOLVE)
    }
}
