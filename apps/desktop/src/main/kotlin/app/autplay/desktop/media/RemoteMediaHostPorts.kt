package app.autplay.desktop.media

import app.autplay.desktop.media.RemoteMediaProtocol as Wire
import java.nio.file.Path
import java.util.UUID
import java.util.concurrent.CompletionStage

/**
 * Required production package verifier, supplied by the later reviewed packaging owner.
 * Path containment is checked separately and is NEVER a signature/architecture/identifier witness.
 * There is deliberately no unsigned/development/default-success implementation.
 */
internal interface RemoteMediaPackageIdentity {
    fun verify(canonicalBundle: Path, canonicalExecutable: Path): RemoteMediaPackageWitness?
    fun stillMatches(witness: RemoteMediaPackageWitness, process: Process, canonicalExecutable: Path): Boolean
}

internal interface RemoteMediaPackageWitness

/**
 * The future JVM media/file owners implement these ports. No base-K type or implementation is
 * implied. Every callback is required; an unavailable/throwing callback closes admission.
 * Calls run outside the playback actor mutex. None may perform queue/listen/timer decisions.
 */
internal interface RemoteMediaHostAuthority {
    /**
     * Nonblocking check immediately before command dispatch. Check the exact permit/attempt to
     * original source evidence/intent/target mapping, current J/LOCAL witness, lifecycle, resource
     * activation and deadline. NETWORK also checks M5 document/high-water separately from all
     * applicable D1/B1 guards; LOCAL must not depend on a remote session or M5 expiry.
     */
    fun commandIsCurrent(command: Wire.Command): Boolean

    /** Exact same-owner/family K old/new compare-and-publish/readback, still-current transition. */
    fun accessPublicationIsCurrent(command: Wire.ReplaceAccess, quiesced: Wire.AccessState): Boolean

    /** Sole host audio arbiter's identified old-owner drain and current lease, never path evidence. */
    fun audioLeaseIsCurrent(command: Wire.ActivateAudio): Boolean

    /** Latest actor I tuple/wantsPlay at dispatch; the host event owner separately fences facts. */
    fun intentIsCurrent(fence: Wire.IntentFence, wantsPlay: Boolean): Boolean

    /** Bounded asynchronous one-use decision from the actual current authority owner. */
    fun decideRead(request: Wire.ReadAdmission): CompletionStage<Wire.ReadResponse>

    /** Nonblocking final same-capture guard immediately before an ALLOW leaves the pipe writer. */
    fun readDecisionIsCurrent(request: Wire.ReadAdmission, response: Wire.ReadResponse): Boolean

    /**
     * One actual serial file write. The future file owner rechecks exact capture and space before
     * admission; the returned witness proves that admitted write has settled (not enqueued).
     * It must settle even after retirement. The bridge retains the frame until this future settles.
     * Exceptional completion also requires terminal actual IO. A hung/uncertain live write keeps
     * this future pending and owned; enqueue, cancellation and timeout cannot complete it early.
     */
    fun write(frame: Wire.DataFrame, transfer: RemoteMediaTransferCapture): CompletionStage<RemoteMediaWriteWitness>

    /**
     * Nonblocking final same-capture guard after receipt encoding, immediately before pipe IO.
     * Check exact file-owner/intent/attempt, current authority, retirement, quota and deadlines.
     * The bridge serializes its credit accounting with this guard. It must only read already
     * published admission state: no IO, locks that await an owner, bridge/actor calls or callbacks.
     * A Boolean is an admission observation, not a held lease or proof of a later file write.
     */
    fun creditIsCurrent(transfer: RemoteMediaTransferCapture): Boolean

    /** Synchronous admission fence. Must not await actor, IO, transition completion or native ACK. */
    fun closeAdmission(code: Wire.Code): Unit

    /** Captured facts only; a bounded coordinator callback, never an automatic play/queue action. */
    fun event(event: Wire.Event): Unit

    /** Fixed code only. In particular, no Throwable/message/frame/path is passed to diagnostics. */
    fun failed(code: Wire.Code): Unit

    /**
     * Only an explicit new host attempt, after visible failure and original J/file/audio owner
     * reconciliation. This permits releasing the failed process slot, never relabeling it DRAINED.
     * Inspect failureDelivery separately: settled work does not prove diagnostic delivery.
     */
    fun exceptionalRecoveryIsCurrent(runId: UUID, release: RemoteMediaRelease): Boolean
}

internal data class RemoteMediaTransferCapture(
    val source: Wire.SourceKey, val transferId: UUID, val attempt: Long,
    val intentRevision: Long, val attemptLeaseRevision: Long, val initialOffset: Long,
)

/** No bridge-made success witness: the future serial JVM file owner must supply this evidence. */
internal interface RemoteMediaWriteWitness {
    val transfer: RemoteMediaTransferCapture
    val sequence: Long
    val nextOffset: Long
    val disposition: Wire.Disposition
    /** Must remain true only for this exact settled actual write or retired discard. */
    fun isSettled(): Boolean
}

/** DRAINED means native + host callbacks + pipe writes, never server release or J publication. */
internal enum class RemoteMediaReleaseKind { DRAINED, FAILED_RETAINED }
/** Diagnostic outcome only. THREW/REJECTED cannot become native DRAINED or successful delivery. */
internal enum class RemoteMediaFailureDelivery { NOT_REQUIRED, RETURNED, THREW, REJECTED }
internal data class RemoteMediaRelease(
    val kind: RemoteMediaReleaseKind, val code: Wire.Code?,
    val processExitConfirmed: Boolean, val pipeReaderSettled: Boolean,
    val pipeWriterSettled: Boolean,
    /** Actual admitted work has ended; a known-unscheduled notification is not pending work. */
    val hostCallbacksSettled: Boolean,
    val failureDelivery: RemoteMediaFailureDelivery,
)

/** A per-source drain can finish while the same process continues a separate download. */
internal data class RemoteMediaSourceRelease(
    val kind: RemoteMediaReleaseKind, val code: Wire.Code?, val nativeDrained: Boolean,
    val sourcePipeWritesSettled: Boolean, val hostCallbacksSettled: Boolean,
    val exceptionalProcessExitConfirmed: Boolean,
)
