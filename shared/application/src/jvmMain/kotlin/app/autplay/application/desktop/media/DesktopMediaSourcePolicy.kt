package app.autplay.application.desktop.media

/** Pure comparisons only. Every successful comparison still requires the actual owner gate. */
object DesktopMediaSourcePolicy {
    fun requireIntent(capture: SelectionCapture, intent: SourceIntent): Unit {
        when (intent) {
            is SourceIntent.StandaloneSelection -> mediaRequire(capture is SelectionCapture.StandaloneLocal, MediaCode.INTENT_STALE)
            is SourceIntent.AccountCopy -> mediaRequire(capture !is SelectionCapture.StandaloneLocal, MediaCode.INTENT_STALE)
            is SourceIntent.VaultPlayback -> {
                val account = when (capture) {
                    is SelectionCapture.AccountLocal -> capture.account
                    is SelectionCapture.VaultNetwork -> {
                        mediaRequire(capture.network.projection == intent.projection, MediaCode.INTENT_STALE)
                        capture.network.local
                    }
                    else -> mediaFail(MediaCode.INTENT_STALE)
                }
                mediaRequire(account.storeId == intent.projection.storeId && account.scopeId == intent.projection.scopeId,
                    MediaCode.INTENT_STALE)
                mediaRequire(capture.fence.queueId != null, MediaCode.INTENT_STALE)
            }
            is SourceIntent.BoundVaultDownload -> {
                mediaRequire(capture is SelectionCapture.VaultNetwork && capture.network.projection == intent.currentProjection,
                    MediaCode.INTENT_STALE)
                mediaRequire(capture.fence.queueId == null, MediaCode.INTENT_STALE)
            }
        }
    }

    fun requireLocalGuard(capture: AccountLocalCapture, downloaded: Boolean): Unit {
        fun guard(value: LocalGuardCapture) {
            mediaRequire(value.state == LocalUseState.AVAILABLE, MediaCode.OWNER_UNAVAILABLE, MediaStage.LOCAL_OWNER)
            mediaRequire(value.veto == LocalMediaVeto.NONE, MediaCode.AUTHORIZATION_DENIED, MediaStage.LOCAL_OWNER)
        }
        guard(capture.localGuard)
        if (downloaded) guard(capture.originLocalGuard ?: mediaFail(MediaCode.RECONCILIATION_REQUIRED, MediaStage.LOCAL_OWNER))
    }

    fun requireSnapshot(capture: SelectionCapture.VaultNetwork, intent: SourceIntent?, snapshot: MediaSourceSnapshotV1): Unit {
        val network = capture.network
        val profile = network.m5Profile
        val projection = network.projection
        val bound = network.local.localOwner as? LocalOwnerKey.BoundLocal
        if (bound != null) mediaRequire(bound.serverInstanceId == network.serverInstanceId && bound.userId == network.userId &&
            bound.ownerBindingCommitId == network.bindingCommitId, MediaCode.OWNER_STALE)
        mediaRequire(network.remoteVeto == LocalMediaVeto.NONE, MediaCode.AUTHORIZATION_DENIED)
        mediaRequire(snapshot.serverInstanceId == network.serverInstanceId && snapshot.identityEpoch == network.identityEpoch &&
            snapshot.identityThumbprintSha256 == network.applicationKeyThumbprint && snapshot.userId == network.userId &&
            snapshot.deviceId == network.deviceId, MediaCode.OWNER_STALE)
        mediaRequire(profile.serverInstanceId == network.serverInstanceId && profile.identityEpoch == network.identityEpoch &&
            profile.applicationKeyThumbprint == network.applicationKeyThumbprint && profile.userId == network.userId &&
            profile.deviceId == network.deviceId && snapshot.m5CapabilityRevision == profile.m5CapabilityRevision,
            MediaCode.CAPABILITY_UNAVAILABLE)
        // Deliberately no comparison with D1B1BindingGuardCapture.globalCapabilityRevisionHighWater.
        mediaRequire(!projection.pendingTargetMutation && !projection.conflict && !projection.tombstone &&
            projection.userTrackRefId == snapshot.userTrackRefId &&
            projection.userTrackRefRowVersion == snapshot.userTrackRefRowVersion && projection.recordingId == snapshot.recordingId,
            MediaCode.RECONCILIATION_REQUIRED)
        when (intent) {
            null -> Unit
            is SourceIntent.VaultPlayback -> mediaRequire(intent.projection == projection, MediaCode.INTENT_STALE)
            is SourceIntent.BoundVaultDownload -> {
                val original = intent.original
                mediaRequire(intent.currentProjection == projection && original.owner == network.local.localOwner &&
                    original.storeId == network.local.storeId && original.scopeId == network.local.scopeId &&
                    original.bindingCommitId == network.bindingCommitId && original.localTrackRefId == projection.localTrackRefId &&
                    original.snapshot.sameAcquisitionTarget(snapshot), MediaCode.RECONCILIATION_REQUIRED)
            }
            else -> mediaFail(MediaCode.INTENT_STALE)
        }
    }

    fun requireEvidence(capture: SelectionCapture.VaultNetwork, intent: SourceIntent,
        observation: MediaSourceObservation, evidence: MediaSourceEvidence): Unit {
        requireSnapshot(capture, intent, observation.snapshot)
        val local = capture.network.local
        val projection = capture.network.projection
        mediaRequire(evidence.storeId == local.storeId && evidence.scopeId == local.scopeId && evidence.owner == local.localOwner &&
            evidence.bindingCommitId == capture.network.bindingCommitId && evidence.localTrackRefId == projection.localTrackRefId &&
            evidence.projectionRevision == projection.projectionRevision &&
            evidence.observedUtrServerRowVersion == projection.userTrackRefRowVersion &&
            evidence.observedRecordingId == projection.recordingId && evidence.snapshot.sameDocument(observation.snapshot),
            MediaCode.RECONCILIATION_REQUIRED)
        when (intent) {
            is SourceIntent.BoundVaultDownload -> mediaRequire(evidence.evidenceId == intent.admissionEvidenceId &&
                evidence.intentId == intent.intentId && evidence.kind == MediaObservationKind.CONTINUATION, MediaCode.INTENT_STALE)
            is SourceIntent.VaultPlayback -> mediaRequire(evidence.intentId == null && evidence.kind == MediaObservationKind.ACQUISITION,
                MediaCode.INTENT_STALE)
            else -> mediaFail(MediaCode.INTENT_STALE)
        }
    }
}

fun mediaRequire(value: Boolean, code: MediaCode, stage: MediaStage = MediaStage.RESOLVE): Unit {
    if (!value) mediaFail(code, stage)
}
fun mediaFail(code: MediaCode, stage: MediaStage = MediaStage.RESOLVE): Nothing = throw MediaSourceException(
    MediaFailure(code, stage, when (code) {
        MediaCode.RECONCILIATION_REQUIRED, MediaCode.OWNER_STALE, MediaCode.INTENT_STALE -> MediaRetryAction.RECONCILE
        MediaCode.OWNER_UNAVAILABLE -> MediaRetryAction.UNLOCK
        MediaCode.AUTH_REQUIRED, MediaCode.CAPABILITY_UNAVAILABLE -> MediaRetryAction.RECONNECT
        MediaCode.RESOURCE_WAITING, MediaCode.NETWORK_TIMEOUT, MediaCode.NETWORK_DROPPED -> MediaRetryAction.RETRY
        else -> MediaRetryAction.NONE
    }))
