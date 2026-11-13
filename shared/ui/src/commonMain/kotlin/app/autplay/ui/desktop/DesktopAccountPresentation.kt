package app.autplay.ui.desktop

/** Opaque presentation identity, never an API origin, credential, path or authorization grant. */
public data class DesktopAccountScope(public val lifetime: String, public val revision: Long)

public enum class DesktopAccountAction {
    Refresh, CreatePortable, UnlockPortable, LockPortable, ImportRecoveryFile, ExportRecoveryFile,
    RestorePortable, PrepareRotation, RotateRoot, RegisterPortable, LoginPortable, Reauthenticate, Discover, ChooseQrFile, SubmitQr, ConfirmTransport,
    PreparePinnedTransport, ConfirmPinnedTransport,
    SubmitInvitation, ConfirmInvitation, RequestAdmission, ResumeAdmission, ConfirmAdmission, EnrollTrusted, ConfirmTrusted, Bind, KeepLocal, ReviewLocal,
    ConnectSelected, CancelConnection, StartSelf, ClaimSelf, ApproveSelf, ExchangeSelf, CancelSelf, ResumeSelf,
    ChooseServerRecoveryFile, ConfigureServerRecovery, ExportServerRecovery, PreviewRecovery, CommitRecovery, ResumeRecovery, RefreshDevices, LogoutCurrent, LogoutAll,
    RevokeDevice, Disconnect, CreateInvitation, CancelInvitation, ReadPolicy, DecideTraining,
    DecideVisibility, PreviewDeletion, RequestDeletion, CancelDeletion, ResumeDeletion,
    OpenArtist, OpenAlbum, OpenRecording, OpenRelease, OpenTrack, OpenPlaylist, CatalogPage, PlayTrack, QueueTrack,
    Download, CancelDownload, RetryDownload, PlayOffline, SetMetered, RefreshDownloads, DownloadPage,
    RefreshStatistics, ImportMatching, ResumeOperation, CancelOperation, DomainMutation,
}

/** Host-issued, single-state permission. Host must revalidate authority again at execution. */
public data class DesktopAccountPermit(
    public val id: String,
    public val action: DesktopAccountAction,
    public val targetId: String? = null,
    public val requiresPassword: Boolean = false,
)

public fun DesktopAccountPermit.acceptsCommand(command: DesktopAccountCommand): Boolean =
    action == command.action && targetId == command.targetId &&
        (!requiresPassword || command is DesktopAccountCommand.Secret || command is DesktopAccountCommand.LoginPortable)

/** One synchronous handoff. The receiver must consume/copy before dispatch returns. */
public class DesktopAccountSecret(text: String) {
    private var value: CharArray? = text.toCharArray()
    public fun <T> consume(block: (CharArray) -> T): T {
        val current = value ?: error("PRESENTATION_SECRET_RETIRED")
        value = null
        return try { block(current) } finally { current.fill('\u0000') }
    }
    public fun clear(): Unit { value?.fill('\u0000'); value = null }
    override fun toString(): String = "DesktopAccountSecret(<redacted>)"
}

public enum class DesktopAccountSensitiveOutputKind { SelfQr, EnrollmentInvitation, PortableRecoveryCode, ServerRecoveryCode }
/** Separate, process-memory-only output. The UI clears it when its reveal surface is disposed. */
public class DesktopAccountSensitiveOutput(
    public val scope: DesktopAccountScope,
    public val kind: DesktopAccountSensitiveOutputKind,
    characters: CharArray,
) {
    private var value: CharArray? = characters.copyOf()
    public fun reveal(): String? = value?.concatToString()
    public fun clear(): Unit { value?.fill('\u0000'); value = null }
    override fun toString(): String = "DesktopAccountSensitiveOutput(<redacted>)"
}

public sealed interface DesktopAccountCommand {
    public val action: DesktopAccountAction
    public val targetId: String? get() = null

    /** No additional input. IDs always come from the displayed host snapshot. */
    public class Invoke(override val action: DesktopAccountAction, override val targetId: String? = null) : DesktopAccountCommand {
        override fun toString(): String = "DesktopAccountCommand.Invoke($action)"
    }
    /** Process-only page query; the host freezes kind/lease from targetId, never from labels. */
    public class CatalogSearch(override val targetId: String, public val query: String) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.CatalogPage
        override fun toString(): String = "DesktopAccountCommand.CatalogSearch(<redacted>)"
    }
    public class DomainEdit(override val targetId: String, public val name: String? = null,
        public val description: String? = null) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.DomainMutation
        override fun toString(): String = "DesktopAccountCommand.DomainEdit(<redacted>)"
    }
    public class Secret(
        override val action: DesktopAccountAction,
        public val input: DesktopAccountSecret,
        public val secondInput: DesktopAccountSecret? = null,
        override val targetId: String? = null,
    ) : DesktopAccountCommand {
        public fun clear(): Unit { input.clear(); secondInput?.clear() }
        override fun toString(): String = "DesktopAccountCommand.Secret($action, <redacted>)"
    }
    public class CreatePortable(public val publicId: String, public val password: DesktopAccountSecret) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.CreatePortable
        override fun toString(): String = "DesktopAccountCommand.CreatePortable(<redacted>)"
    }
    public class LoginPortable(public val publicId: String, public val password: DesktopAccountSecret) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.LoginPortable
        override fun toString(): String = "DesktopAccountCommand.LoginPortable(<redacted>)"
    }
    public class Discover(public val enteredOrigin: DesktopAccountSecret) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.Discover
        override fun toString(): String = "DesktopAccountCommand.Discover(<redacted>)"
    }
    public class RecoveryPreview(public val accountId: DesktopAccountSecret, public val code: DesktopAccountSecret,
        public val cancelDeletion: Boolean, override val targetId: String? = null) : DesktopAccountCommand {
        override val action: DesktopAccountAction = if (cancelDeletion) DesktopAccountAction.PreviewDeletion else DesktopAccountAction.PreviewRecovery
        public fun clear(): Unit { accountId.clear(); code.clear() }
        override fun toString(): String = "DesktopAccountCommand.RecoveryPreview(<redacted>)"
    }
    public data class ConfirmTransport(
        override val targetId: String,
        public val apiConfirmed: Boolean,
        public val streamConfirmed: Boolean,
        public val identityConfirmed: Boolean,
    ) : DesktopAccountCommand { override val action: DesktopAccountAction = DesktopAccountAction.ConfirmTransport }
    public data class ConnectSelected(override val targetId: String, public val selectedFactIds: List<String>) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.ConnectSelected
    }
    public enum class TrainingDecision { Grant, Deny, Withdraw }
    public data class Training(public val decision: TrainingDecision, public val expectedRevision: Long) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.DecideTraining
    }
    public data class Visibility(public val visible: Boolean, public val expectedRevision: Long) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.DecideVisibility
    }
    public data class Metered(public val allowed: Boolean) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.SetMetered
    }
    public class PinnedTransport(public val apiOrigin: DesktopAccountSecret, public val streamOrigin: DesktopAccountSecret,
        public val apiSpki: DesktopAccountSecret, public val streamSpki: DesktopAccountSecret) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.PreparePinnedTransport
        public fun clear(): Unit { apiOrigin.clear(); streamOrigin.clear(); apiSpki.clear(); streamSpki.clear() }
        override fun toString(): String = "DesktopAccountCommand.PinnedTransport(<redacted>)"
    }
    public data class ConfirmPinnedTransport(override val targetId: String, public val apiConfirmed: Boolean,
        public val streamConfirmed: Boolean) : DesktopAccountCommand {
        override val action: DesktopAccountAction = DesktopAccountAction.ConfirmPinnedTransport
    }
}

public class DesktopAccountIntent(
    public val scope: DesktopAccountScope,
    public val permit: DesktopAccountPermit,
    public val command: DesktopAccountCommand,
) { override fun toString(): String = "DesktopAccountIntent(${command.action})" }

/** Dispatch admission is not confirmation of a remote, storage, file or playback effect. */
public fun interface DesktopAccountActionSink { public fun dispatch(intent: DesktopAccountIntent): Unit }

public enum class DesktopAccountCondition {
    Ready, Loading, Empty, Offline, Locked, RecoveryNeeded, Unsupported, Attention, Pending, Unavailable,
}
public enum class DesktopAccountIssue {
    None, Retry, Reconnect, Unlock, Authentication, Recover, Capacity, IdentityChanged, Expired, Conflict,
    ExportExists, ExportReadback, Unsupported, Storage, CancelPending,
}
public data class DesktopAccountOperation(
    public val id: String,
    public val action: DesktopAccountAction,
    public val condition: DesktopAccountCondition,
    public val issue: DesktopAccountIssue = DesktopAccountIssue.None,
    public val cancellable: Boolean = false,
    public val resumable: Boolean = false,
)
public enum class DesktopRecoveryFileStatus { None, Choosing, ReadbackRequired, Verified, TargetExists, Failed }
public class DesktopPortableAccountView(
    public val condition: DesktopAccountCondition,
    public val publicId: String?,
    public val rootVersion: Long?,
    public val recoveryFileStatus: DesktopRecoveryFileStatus,
    /** Opaque frozen proposal IDs, never recovery documents or keys. */
    public val rotationPreviewId: String? = null,
    public val reauthenticationId: String? = null,
    public val recoveryAvailable: Boolean = false,
) { override fun toString(): String = "DesktopPortableAccountView(<redacted>)" }

/** Exact decision data is rendered only in the transport confirmation surface. */
public class DesktopTransportReview(
    public val id: String,
    public val normalizedApiOrigin: String,
    public val normalizedStreamOrigin: String,
    public val serverIdentity: String,
    public val apiTlsFingerprint: String?,
    public val streamTlsFingerprint: String?,
    public val apiUsesSystemTrust: Boolean,
    public val streamUsesSystemTrust: Boolean,
) { override fun toString(): String = "DesktopTransportReview(<redacted>)" }
public data class DesktopLocalDataFact(public val id: String, public val label: String, public val eligible: Boolean)
public data class DesktopLocalDataReview(public val id: String, public val accountPublicId: String,
    public val facts: List<DesktopLocalDataFact>, public val hasMore: Boolean)
public class DesktopConnectionView(
    public val condition: DesktopAccountCondition,
    public val issue: DesktopAccountIssue = DesktopAccountIssue.None,
    public val transportReview: DesktopTransportReview? = null,
    public val localDataReview: DesktopLocalDataReview? = null,
    public val admissionId: String? = null,
    public val bindingId: String? = null,
    public val serverLabel: String? = null,
    public val accountPublicId: String? = null,
    public val pendingItems: Int? = null,
    public val admissionAccountLabel: String? = null,
    public val admissionConfirmationRequired: Boolean = false,
    public val trustedConfirmationRequired: Boolean = false,
    public val transportBootstrap: DesktopTransportBootstrap? = null,
    public val invitationReview: DesktopInvitationInputReview? = null,
    public val localChoiceSelected: Boolean = false,
    public val localChoiceSummary: String? = null,
) { override fun toString(): String = "DesktopConnectionView(<redacted>)" }
public class DesktopTransportBootstrap(public val id: String, public val apiOrigin: String, public val streamOrigin: String,
    public val apiFingerprint: String?, public val streamFingerprint: String?) {
    override fun toString(): String = "DesktopTransportBootstrap(<redacted>)"
}

public enum class DesktopSelfRole { Source, Recipient }
/** This model deliberately has no QR/token/pairing-secret fields. */
public class DesktopSelfPairingView(
    public val condition: DesktopAccountCondition,
    public val role: DesktopSelfRole,
    public val ceremonyId: String?,
    public val sas: String?,
    public val recipientLabel: String?,
    public val sourceApproved: Boolean?,
    public val expiresLabel: String?,
    public val accountLabel: String? = null,
    public val exchangeConfirmationRequired: Boolean = false,
) { override fun toString(): String = "DesktopSelfPairingView(<redacted>)" }
public data class DesktopDeviceView(public val id: String, public val label: String, public val current: Boolean,
    public val condition: DesktopAccountCondition, public val lastSeenLabel: String?)
public data class DesktopSessionView(public val id: String, public val deviceLabel: String,
    public val current: Boolean, public val condition: DesktopAccountCondition)
public data class DesktopInvitationView(public val id: String, public val expiresLabel: String,
    public val condition: DesktopAccountCondition)
public data class DesktopDevicesView(public val condition: DesktopAccountCondition,
    public val devices: List<DesktopDeviceView>, public val sessions: List<DesktopSessionView>,
    public val invitations: List<DesktopInvitationView>)

public enum class DesktopTrainingPolicy { Unknown, Granted, Denied, Withdrawn }
public data class DesktopPrivacyView(
    public val condition: DesktopAccountCondition,
    public val training: DesktopTrainingPolicy?,
    public val trainingRevision: Long?,
    public val statisticsVisible: Boolean?,
    public val visibilityRevision: Long?,
    public val deletion: DesktopDeletionView?,
)
public enum class DesktopDeletionRole { Source, Recipient }
public enum class DesktopDeletionStage { Preview, Pending, Suspended, Cancelling, Cancelled, Purging, Purged, Unknown }
public class DesktopDeletionView(
    public val id: String,
    public val stage: DesktopDeletionStage,
    public val role: DesktopDeletionRole,
    public val accountPublicId: String,
    public val deadlineLabel: String?,
    public val canCancel: Boolean,
    public val recoveryPreviewId: String? = null,
) { override fun toString(): String = "DesktopDeletionView(<redacted>)" }
public class DesktopServerRecoveryView(
    public val condition: DesktopAccountCondition,
    public val previewId: String?,
    public val accountLabel: String?,
    public val consequences: DesktopRecoveryConsequences?,
    public val exports: List<DesktopServerRecoveryExport> = emptyList(),
) { override fun toString(): String = "DesktopServerRecoveryView(<redacted>)" }
public enum class DesktopServerRecoveryExportKind { SourceConfiguration, RecipientRecovery, DeletionCancellation }
public data class DesktopServerRecoveryExport(public val targetId: String, public val kind: DesktopServerRecoveryExportKind,
    public val codeGeneration: Long)
public data class DesktopRecoveryConsequences(public val sessionsRevoked: Int?, public val devicesRevoked: Int?)

public enum class DesktopCatalogKind { Artist, Album, Recording, Release, Track, Playlist }
public data class DesktopCatalogKey(public val kind: DesktopCatalogKind, public val id: String)
public data class DesktopCreditView(public val artistId: String, public val name: String,
    public val position: Int, public val joinPhrase: String, public val role: String,
    public val actionTargetId: String? = null)
public enum class DesktopCatalogField { StableId, SortName, ArtistType, Country, IdentityStatus,
    Provider, ProviderEdition, ReleaseDate, DiscNumber, TrackNumber, RecordingId, DurationMs,
    MetadataState, Source, Availability, OccurrenceId, CreditRole, ProjectionState, Preference, TasteExclusion,
    CanonicalDate, DatePrecision, VersionText, Disambiguation, RecordingKind, ReleaseStatus, RowVersion }
public data class DesktopCatalogFact(public val field: DesktopCatalogField, public val value: String)
public data class DesktopCatalogLink(public val kind: DesktopCatalogKind, public val targetId: String)
public data class DesktopCatalogEntry(
    public val key: DesktopCatalogKey,
    public val title: String?,
    public val subtitle: String?,
    public val condition: DesktopAccountCondition,
    public val credits: List<DesktopCreditView> = emptyList(),
    public val identityResolved: Boolean? = null,
    public val ownerProofConfirmed: Boolean? = null,
    public val metadataAlbumOnly: Boolean = false,
    public val pending: Boolean = false,
    /** Playlist occurrence IDs remain distinct even for duplicate track references. */
    public val occurrenceId: String? = null,
    /** Frozen host target retains occurrence/account attribution across play/queue/download. */
    public val actionTargetId: String? = null,
    public val openTargetId: String? = null,
    public val facts: List<DesktopCatalogFact> = emptyList(),
    public val links: List<DesktopCatalogLink> = emptyList(),
    /** The existing credit projection has no canonical title or ReleaseTrack/medium sequence. */
    public val creditProjectionOnly: Boolean = false,
    public val availabilityUnconfirmed: Boolean = false,
    public val partialContext: Boolean = false,
    public val mutations: List<DesktopDomainControl> = emptyList(),
)
public enum class DesktopDomainEditKind { Apply, PlaylistMetadata, Delete, BeginAdd, Retry }
public data class DesktopDomainControl(public val targetId: String, public val label: String,
    public val kind: DesktopDomainEditKind, public val name: String? = null, public val description: String? = null)
public data class DesktopCatalogPage(public val id: String, public val entries: List<DesktopCatalogEntry>,
    public val previousTarget: String?, public val nextTarget: String?)
public enum class DesktopCatalogRelation { ArtistAppearances, PlaylistOccurrences, MetadataAlbumTracks, RecordingReferences, ReleaseCredits }
public data class DesktopCatalogView(public val condition: DesktopAccountCondition,
    public val page: DesktopCatalogPage?, public val detail: DesktopCatalogEntry?,
    public val homeSections: List<DesktopCatalogHomeSection>,
    public val destinations: List<DesktopCatalogDestination> = emptyList(),
    public val selectedKind: DesktopCatalogKind? = null,
    public val query: String = "",
    public val searchTargetId: String? = null,
    public val pageScopedSearch: Boolean = false,
    public val navigationRequested: Boolean = false,
    public val relation: DesktopCatalogRelation? = null,
    public val mutations: List<DesktopDomainControl> = emptyList(),
    public val mutationNotice: String? = null,
    public val canonicalPositions: DesktopCanonicalPositionsView? = null,
    /** Exact active J scope supplied from the issued lease, never inferred from display labels. */
    public val currentAccountScopeId: String? = null)
/** C1 positions describe only the selected owner's subset; they never expose a play target. */
public data class DesktopCanonicalPositionView(public val id: String, public val title: String,
    public val releaseTitle: String, public val releaseId: String, public val recordingId: String,
    public val mediumId: String, public val mediumPosition: Int, public val sequence: Int,
    public val number: String?, public val durationMs: Long?, public val releaseDate: String?,
    public val datePrecision: String?, public val credits: List<DesktopCreditView>,
    public val releaseTarget: String, public val recordingTarget: String)
public data class DesktopCanonicalPositionsView(public val condition: DesktopAccountCondition,
    public val entries: List<DesktopCanonicalPositionView>, public val previousTarget: String?,
    public val nextTarget: String?, public val exhausted: Boolean, public val notice: String? = null)
public data class DesktopCatalogDestination(public val targetId: String, public val kind: DesktopCatalogKind)
public data class DesktopCatalogHomeSection(public val id: String, public val title: String,
    public val entries: List<DesktopCatalogEntry>, public val browseTargetId: String? = null)

public enum class DesktopMediaStorage { Cache, Pinned, UserDownload, Unknown }
public data class DesktopDownloadView(public val id: String, public val trackId: String, public val title: String?,
    public val storage: DesktopMediaStorage, public val condition: DesktopAccountCondition,
    public val confirmedBytes: Long?, public val totalBytes: Long?, public val offlinePlayable: Boolean,
    public val issue: DesktopAccountIssue = DesktopAccountIssue.None,
    /** Prepared using the selected UI locale, never a raw source URL/path. */
    public val progressLabel: String? = null,
    public val retainedCopy: Boolean = false)
public data class DesktopDownloadsView(public val condition: DesktopAccountCondition,
    public val entries: List<DesktopDownloadView>, public val meteredAllowed: Boolean?, public val hasMore: Boolean,
    public val previousTarget: String? = null, public val nextTarget: String? = null)
public enum class DesktopStatisticsOrigin { Account, Portable }
/** Already reconciled by the data owner. Never combine server totals in presentation. */
public data class DesktopAccountStatisticsView(public val origin: DesktopStatisticsOrigin,
    public val condition: DesktopAccountCondition, public val listens: String?, public val playedTime: String?,
    public val checkedThroughLabel: String?, public val complete: Boolean,
    public val serverDetails: DesktopServerStatisticsDetails? = null)

/** All collections are bounded prepared pages; no SQL, network, work scheduling or normalization in UI. */
public class DesktopAccountWorkspaceState(
    public val scope: DesktopAccountScope,
    public val permits: List<DesktopAccountPermit>,
    public val operations: List<DesktopAccountOperation>,
    public val portable: DesktopPortableAccountView,
    public val connection: DesktopConnectionView,
    public val selfPairing: DesktopSelfPairingView,
    public val serverRecovery: DesktopServerRecoveryView,
    public val devices: DesktopDevicesView,
    public val privacy: DesktopPrivacyView,
    public val catalog: DesktopCatalogView,
    public val downloads: DesktopDownloadsView,
    public val statistics: List<DesktopAccountStatisticsView>,
    public val matching: DesktopMatchingView? = null,
) { override fun toString(): String = "DesktopAccountWorkspaceState(<redacted>)" }
