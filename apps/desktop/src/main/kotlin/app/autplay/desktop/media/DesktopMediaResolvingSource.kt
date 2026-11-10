package app.autplay.desktop.media

import app.autplay.application.desktop.media.*

/** Initial control-request refresh uses the same exact-parent owner and replay budget as native. */
internal class DesktopMediaResolvingSource(
    private val resolver: DesktopVaultSourceResolver,
    private val credentials: DesktopMediaProtectedCredentials,
    private val admission: DesktopMediaAccountAdmission,
    private val native: DesktopMediaNativeOwner,
    private val resources: DesktopMediaResourceOwner,
) : DesktopMediaSourcePort {
    override suspend fun resolve(capture: SelectionCapture, intent: SourceIntent): SourceResolution {
        var current = if (capture is SelectionCapture.VaultNetwork) credentials.prepare(capture) else capture
        admission.refresh(current, intent)
        val first = resolver.resolve(current, intent)
        if (current !is SelectionCapture.VaultNetwork || first !is SourceResolution.Unknown ||
            first.failure.code != MediaCode.AUTH_REQUIRED || first.failure.stage != MediaStage.RESOLVE) return finish(current, first)
        // The old resolution has returned only after its actual owned work settled. A failed
        // retained native/server owner blocks replay rather than allocating a second operation.
        mediaCheck(native.releaseOperation(current.fence.operationId) == MediaHandleRelease.RELEASED &&
            resources.releaseOperation(current.fence.operationId) == MediaHandleRelease.RELEASED,
            MediaCode.DRAIN_UNCONFIRMED, MediaStage.AUTH)
        current = credentials.successor(current, true, current.fence.playbackGeneration).use { it.capture }
        admission.refresh(current, intent)
        return finish(current, resolver.resolve(current, intent))
    }

    private fun finish(capture: SelectionCapture, result: SourceResolution): SourceResolution {
        if (result !is SourceResolution.Allow) admission.forget(capture.fence.operationId)
        return result
    }

    override suspend fun readSourceSnapshot(capture: SelectionCapture.VaultNetwork, userTrackRefId: MediaId): MediaSourceObservation {
        var current = credentials.prepare(capture)
        admission.refresh(current, null)
        try { return resolver.readSourceSnapshot(current, userTrackRefId) }
        catch (failure: MediaSourceException) {
            if (failure.failure.code != MediaCode.AUTH_REQUIRED) throw failure
            current = credentials.successor(current, true, current.fence.playbackGeneration).use { it.capture }
            admission.refresh(current, null)
            return resolver.readSourceSnapshot(current, userTrackRefId)
        }
    }
}
