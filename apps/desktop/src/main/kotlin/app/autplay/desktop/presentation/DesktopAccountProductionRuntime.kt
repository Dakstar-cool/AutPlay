package app.autplay.desktop.presentation

import app.autplay.application.desktop.*
import app.autplay.application.desktop.account.*
import app.autplay.application.securestate.*
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation as D1
import app.autplay.application.syncbinding.SyncBindingContractV2 as B1
import app.autplay.data.desktop.requireDesktopStandaloneAccountOwner
import app.autplay.desktop.DesktopSignedBundle
import app.autplay.desktop.account.*
import app.autplay.desktop.media.DesktopMediaRuntime
import app.autplay.desktop.media.DesktopMediaOpenAttempt
import app.autplay.application.desktop.media.MediaHandleRelease
import app.autplay.desktop.playback.DesktopPlaybackController
import app.autplay.desktop.security.*
import app.autplay.desktop.sync.DesktopAccountServices
import app.autplay.desktop.sync.DesktopSyncGuardPublication
import app.autplay.ui.desktop.*
import java.awt.Window
import java.time.Instant
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** One retained root graph. Failed initialization/close never abandons its K/J/native owners. */
internal class DesktopAccountProductionRuntime private constructor(
    val signedBundle: DesktopSignedBundle,
    private val standalone: DesktopLibraryStore,
    private val parentScope: CoroutineScope,
    private val language: () -> DesktopLanguage,
    private val fenceHostSources: () -> Unit,
    private val drainHostSources: suspend () -> Unit,
    private val readMeteredPreference: () -> Boolean,
    private val saveMeteredPreference: suspend (Boolean) -> Unit,
) {
    private val supervisor = SupervisorJob()
    private val work = CoroutineScope(supervisor + Dispatchers.IO)
    private val closing = AtomicBoolean(false)
    private val shutdownStarted = AtomicBoolean(false)
    private val sleepStarted = AtomicBoolean(false)
    @Volatile private var sleepAttempt: CompletableFuture<Unit>? = null
    @Volatile private var accountSuspended = false
    private val closeMutex = Mutex()
    private val window = AtomicReference<Window?>(null)
    private val issued = AtomicReference<AccountLease?>(null)
    private val catalogAttachment = AtomicReference<Any?>(null)
    private val catalogGate = Any()
    private val selectionMutex = Mutex()
    private var navigationSettlement: CompletableFuture<Unit>? = null
    private var navigationExecuting = false
    private var navigationRequest: Any = Any()
    private var navigationWantsStandalone = true
    private val settlementMutable = MutableStateFlow(DesktopContextSettlement.Ready)
    private val beforeFence = AtomicReference<AccountLease?>(null)
    private val explicitRootUnlock = AtomicReference<SecureRecordOwner?>(null)
    private val drainLock = Any()
    private val retainedWork = java.util.concurrent.ConcurrentHashMap.newKeySet<CompletableFuture<*>>()
    private var mediaDrain: CompletableFuture<Unit>? = null
    private var secure: DesktopSecureStateOwner? = null
    private var owners: DesktopAccountAuthorityOwners? = null
    private var initialized = false
    private var account: DesktopAccountController? = null
    private var services: DesktopAccountServices? = null
    private var media: DesktopMediaRuntime? = null
    private var mediaAttempt: DesktopMediaOpenAttempt? = null
    private var hostMedia: DesktopAccountProductionMedia? = null
    private var localData: DesktopAccountLocalDataOwner? = null
    private var picker: DesktopAccountFilePicker? = null
    private var host: DesktopAccountHost? = null
    private var protectedMedia: DesktopProtectedMediaState? = null
    private var accountObserver: Job? = null
    @Volatile private var surfaceVisible = false
    @Volatile private var homeVisible = false
    @Volatile private var catalogVisible = false
    @Volatile private var windowVisible = true
    private var ended = false
    private val availabilityMutable = MutableStateFlow(DesktopAccountAvailability.Opening)
    val availability: StateFlow<DesktopAccountAvailability> = availabilityMutable
    var playbackController: DesktopPlaybackController? = null
        private set
    val libraryState: StateFlow<DesktopLibrarySnapshot>? get() = media?.library?.state
    var binding: DesktopAccountWorkspaceBinding = DesktopAccountWorkspaceBinding.unavailable(DesktopAccountAvailability.Opening)
        private set

    suspend fun applySavedDownloadPolicy(allowed: Boolean): Unit {
        // Device preferences remain writable after the account graph has actually retired.
        if (!closing.get() && !accountSuspended) media?.setMeteredDownloads(allowed)
    }

    fun meteredPreferenceCommitted(): Unit { hostMedia?.preferencesChanged() }

    private fun authority(): DesktopAccountAuthority = owners?.authority ?: unavailable()
    private fun keychain(): DesktopSecureStateOwner = secure ?: unavailable()

    private suspend fun start(initialLocal: DesktopAudioEngine, createLocal: () -> DesktopAudioEngine,
        allowMetered: Boolean, initializeVirgin: Boolean): Unit {
        requireDesktopStandaloneAccountOwner(standalone)
        val identity = signedBundle.identity ?: throw SecureStateFailure(SecureStateStatus.IDENTITY_UNAVAILABLE)
        val protected = DesktopSecureStateOwner(macSecureStateApi(identity))
        secure = protected
        val shared = DesktopAccountAuthorityOwners.create(protected, object : DesktopAccountExternalDrain {
            override fun quiesce(): CompletableFuture<Unit> = quiesceExternal()
            override fun quiesceRenewal(owner: SecureRecordOwner): CompletableFuture<Unit> {
                if (media?.acceptsRenewal(owner) != true) return quiesceExternal()
                issued.getAndSet(null)?.let { beforeFence.set(it) }
                fenceHostSources()
                return task {
                    drainHostSources()
                    services?.session?.drain()?.hostAwait()
                    if (closing.get() || media?.acceptsRenewal(owner) != true) unavailable()
                }
            }
        })
        owners = shared
        val snapshot = protected.open().hostAwait()
        if (snapshot == null) {
            if (!initializeVirgin) {
                // No J file has been opened. The explicit UI initialization reopens this graph.
                protected.close().hostAwait()
                secure = null
                availabilityMutable.value = DesktopAccountAvailability.InitializeRequired
                binding = DesktopAccountWorkspaceBinding.unavailable(DesktopAccountAvailability.InitializeRequired)
                return
            }
            shared.authority.initializeVirginProtectedNamespace().hostAwait()
        }
        // The authority publishes RESERVED before J creation, then exact READY readback.
        shared.authority.initialize().hostAwait()
        initialized = true
        val controller = DesktopAccountController.create(protected, shared.authority, "AutPlay Mac", "1.0.0")
        account = controller
        val serviceOwner = DesktopAccountServices(controller.accounts, shared.store, shared.authority, SyncGuardPublication())
        services = serviceOwner
        val guardedMedia = DesktopProtectedMediaState(protected, PublicationGate(), shared.authority)
        protectedMedia = guardedMedia
        val openingMedia = DesktopMediaOpenAttempt().also { mediaAttempt = it }
        val mediaOwner = DesktopMediaRuntime.open(openingMedia, signedBundle.bundle, identity, standalone, shared.store,
            controller.accounts, guardedMedia, initialLocal, createLocal, allowMetered,
            readCurrentLease = { authority().currentLease().hostAwait() }, publishLease = { lease ->
                if (lease != null && closing.get()) unavailable()
                issued.set(lease)
            })
        media = mediaOwner
        val playback = DesktopPlaybackController(standalone, initialLocal, parentScope,
            media = mediaOwner.playback, libraryState = mediaOwner.library.state)
        playbackController = playback
        val mediaAdapter = DesktopAccountProductionMedia(mediaOwner, shared.store, playback, issued::get,
            readMeteredPreference, saveMeteredPreference, ::prepareNetworkMediaLease)
        hostMedia = mediaAdapter
        val source = DesktopAccountLocalDataOwner(standalone, shared.store, { controller.accounts.state.value.publicId }, language)
        localData = source
        val chooser = DesktopAccountFilePicker(window::get,
            { !closing.get() && surfaceVisible && windowVisible }, { language() == DesktopLanguage.Russian })
        picker = chooser
        val accountHost = DesktopAccountHost(controller, chooser, shared.store, serviceOwner, issued::get,
            activate = { captureCurrentLease() }, localData = source, media = mediaAdapter, language = language,
            rootActivation = object : DesktopAccountRootActivation {
                override suspend fun selected(): AccountLease = selectRootAfterExplicitCommand()
                override suspend fun lock(): Unit { publishRootState("LOCKED") }
            }, authorityLifecycle = object : DesktopAccountHostAuthorityLifecycle {
                override fun fence(action: DesktopAccountAction): Unit = fenceAuthorityAction(action)
                override suspend fun settle(action: DesktopAccountAction): Unit {
                    if (action in authorityActions) fenceExternal().hostAwait()
                }
                override suspend fun readback(action: DesktopAccountAction): Unit {
                    if (action in authorityActions) try { captureCurrentLease() }
                    catch (_: Exception) { issued.set(null) }
                }
            }, importLinks = serviceOwner.importLinks(standalone))
        host = accountHost
        binding = DesktopAccountWorkspaceBinding(accountHost.state, accountHost, accountHost.sensitiveOutput,
            attachWindow = { attached ->
                val previous = window.getAndSet(attached)
                if (previous != null && previous !== attached) settleNavigation(true)
            },
            setVisible = ::setSurfaceVisible, availability = availability,
            setWindowVisible = { visible ->
                synchronized(catalogGate) {
                    val changed = windowVisible != visible
                    windowVisible = visible
                    if (!visible) {
                        accountHost.deactivate()
                        // Hide retains actual background playback; it retires presentation only.
                        // A different window owner or a later local route requests standalone.
                        if (changed && !closing.get()) settleNavigation(false)
                    } else if (!closing.get() && settlementMutable.value == DesktopContextSettlement.Ready) {
                        if (catalogVisible) setCatalogVisible(true)
                        else if (surfaceVisible || homeVisible) accountHost.attach()
                    }
                }
            }, setHomeVisible = ::setHomeVisible, setCatalogVisible = ::setCatalogVisible,
            settleNavigation = ::settleNavigation, contextSettlement = settlementMutable, accountOwner = issued::get)
        controller.accounts.reload().hostAwait()
        accountObserver = work.launch {
            controller.accounts.state.drop(1).collect { state ->
                if (!closing.get() && !state.busy && state.phase == DesktopAccountPhase.CONNECTED) {
                    try {
                        val previous = issued.get()
                        val current = captureCurrentLease()
                        if (previous != current) accountHost.authorityChanged()
                    } catch (_: Exception) { issued.set(null) }
                }
            }
        }
        availabilityMutable.value = DesktopAccountAvailability.Available
    }

    private suspend fun captureCurrentLease(): AccountLease {
        if (closing.get()) unavailable()
        val actual = authority().currentLease().hostAwait()
        if (closing.get()) unavailable()
        if (services?.session?.authorityReconciled(actual) == false) unavailable()
        issued.set(actual)
        return actual
    }

    /** Runs in the initiating B1 pass. External drain excludes that Host command and sync pass. */
    private inner class SyncGuardPublication : DesktopSyncGuardPublication {
        override suspend fun publish(lease: AccountLease, owner: SecureRecordOwner, guard: CapabilityGuard,
            m5: M5CapabilityGuard, policy: ProtectedTransportPolicy): Unit = try {
            val snapshot = requireGuardParent(lease, owner)
            requireNotNull(protectedMedia).publishConnection(snapshot, lease.profileId.value, guard, m5, policy).hostAwait()
            val actual = authority().currentLease().hostAwait()
            if (actual == lease || actual.storeId != lease.storeId || actual.scopeId != lease.scopeId ||
                actual.profileId != lease.profileId || actual.bindingCommit != lease.bindingCommit ||
                actual.sessionFamily != lease.sessionFamily || actual.sessionGeneration != lease.sessionGeneration ||
                actual.activationRevision <= lease.activationRevision || actual.authorityRevision <= lease.authorityRevision ||
                actual.capabilityRevision != guard.revision) unavailable()
            val current = keychain().snapshot().hostAwait()
            val record = keychain().read(actual.profileId.value, current).hostAwait() ?: unavailable()
            record.use {
                val payload = it.payload as? SessionPayload ?: unavailable()
                val bytes = payload.transportPolicyCopy()
                val selectedPolicy = try { SecureMediaGuardCodec.readTransport(bytes) } finally { bytes.fill(0) }
                if (current.activeBinding != it.slot || it.owner.stage != SecureStage.ACTIVE || payload.d1 != guard ||
                    payload.m5 != m5 || selectedPolicy != policy ||
                    it.owner.copy(authorityRevision = owner.authorityRevision, stage = owner.stage, accountProjection = null) !=
                        owner.copy(accountProjection = null)) unavailable()
            }
            if (closing.get() || authority().currentLease().hostAwait() != actual ||
                services?.session?.authorityReconciled(actual) != true) unavailable()
            issued.set(actual)
            // Only the actual authority-issued successor reaches readers. The old command must retry.
            host?.authorityChanged()
            Unit
        } catch (failure: Exception) {
            guardPublicationFailed()
            throw failure
        }

        override suspend fun deny(lease: AccountLease, owner: SecureRecordOwner): Unit = try {
            requireGuardParent(lease, owner)
            // A disable/conflict cannot leave the previous account admission live. This retained
            // graph needs its existing explicit reopen/revalidation path; no old lease is restored.
            fenceExternal().hostAwait()
            authority().suspend().hostAwait()
            accountSuspended = true
            availabilityMutable.value = DesktopAccountAvailability.RecoveryRequired
            host?.authorityChanged()
            Unit
        } catch (failure: Exception) {
            guardPublicationFailed()
            throw failure
        }

        private suspend fun requireGuardParent(lease: AccountLease, owner: SecureRecordOwner): SecureControl {
            if (closing.get() || authority().currentLease().hostAwait() != lease) unavailable()
            val snapshot = keychain().snapshot().hostAwait()
            val record = keychain().read(lease.profileId.value, snapshot).hostAwait() ?: unavailable()
            record.use { if (it.owner != owner || snapshot.activeBinding != it.slot || owner.stage != SecureStage.ACTIVE ||
                owner.authorityRevision != snapshot.authorityRevision) unavailable() }
            return snapshot
        }
    }

    /** A partial guard transition never restores the old lease or pretends its drain completed. */
    private fun guardPublicationFailed(): Unit {
        issued.set(null)
        if (!closing.get()) {
            availabilityMutable.value = DesktopAccountAvailability.RecoveryRequired
            host?.authorityChanged()
        }
        // Keep accountSuspended false unless suspend actually completed. Reopen/Quit must still
        // join retained work and publish the durable fence before the actual J/K close.
    }

    /** Explicit network intent preflight, outside every media actor/native callback. LOCAL bypasses it. */
    private suspend fun prepareNetworkMediaLease(expected: AccountLease): AccountLease {
        if (captureCurrentLease() != expected) unavailable()
        if (expected.sessionFamily == null) return expected
        val runtime = account?.accounts ?: unavailable()
        val server = runtime.selected ?: unavailable()
        var snapshot = keychain().snapshot().hostAwait()
        var record = keychain().read(expected.profileId.value, snapshot).hostAwait() ?: unavailable()
        var session = record.payload as? SessionPayload ?: run { record.close(); unavailable() }
        if (session.accessExpiresAt * 1_000 <= System.currentTimeMillis() + 30_000) {
            record.close()
            fenceExternal().hostAwait()
            account?.profiles?.rotateSession()?.hostAwait() ?: unavailable()
            runtime.reload().hostAwait()
            return captureCurrentLease()
        }
        try {
            runtime.requireTarget(record.owner, server)
            val secret = keychain().access(snapshot, record.owner).hostAwait()
            val access = secret.copy()
            val response = try { runtime.read(server.transport, AccountEndpoint.M5_CAPABILITIES, access = access).hostAwait() }
                finally { access.fill(0); secret.close() }
            val raw = try { response.bodyCopy() } finally { response.close() }
            val observed = try { DesktopAccountTrust.capabilities(raw, server.identity,
                requireNotNull(record.owner.account), requireNotNull(record.owner.device), session.m5, Instant.now()) }
                finally { raw.fill(0) }
            if (observed.requiredFeatures.isNotEmpty()) unavailable()
            if (observed.guard == session.m5) return captureCurrentLease()
            record.close()
            fenceExternal().hostAwait()
            snapshot = keychain().snapshot().hostAwait()
            record = keychain().read(expected.profileId.value, snapshot).hostAwait() ?: unavailable()
            session = record.payload as? SessionPayload ?: unavailable()
            val bytes = session.transportPolicyCopy()
            val policy = try { SecureMediaGuardCodec.readTransport(bytes) } finally { bytes.fill(0) }
            try {
                requireNotNull(protectedMedia).publishConnection(snapshot, expected.profileId.value,
                    session.d1, observed.guard, policy).hostAwait()
                runtime.reload().hostAwait()
                return captureCurrentLease()
            } catch (failure: Exception) {
                guardPublicationFailed()
                throw failure
            }
        } finally { record.close() }
    }

    private suspend fun selectRootAfterExplicitCommand(): AccountLease {
        val snapshot = keychain().snapshot().hostAwait()
        if (snapshot.activeBinding != null) return captureCurrentLease()
        if (publishRootState("AVAILABLE")) return captureCurrentLease()
        val actual = authority().activateRoot().hostAwait()
        issued.set(actual)
        return captureCurrentLease()
    }

    private suspend fun publishRootState(state: String): Boolean {
        val protected = keychain()
        fenceExternal().hostAwait()
        val snapshot = protected.snapshot().hostAwait()
        val slot = requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved)
        val record = protected.read(slot, snapshot).hostAwait() ?: unavailable()
        try {
            val root = record.payload as? PortableRootPayload ?: unavailable()
            val bytes = root.localGuardCopy()
            val old = try { if (bytes.isEmpty()) null else SecureMediaGuardCodec.readLocal(bytes) } finally { bytes.fill(0) }
            if (old?.state == state && old.veto == "NONE") return false
            if (old != null && old.veto != "NONE") unavailable()
            val guard = LocalUseGuard(SecureStateLimits.next(old?.revision ?: 0), state, "NONE")
            if (guard.available) explicitRootUnlock.set(record.owner)
            try { requireNotNull(protectedMedia).publishLocal(snapshot, slot, old, guard).hostAwait() }
            finally { explicitRootUnlock.set(null) }
            if (guard.available) captureCurrentLease()
            return true
        } finally {
            record.close()
        }
    }

    private fun setSurfaceVisible(visible: Boolean): Unit = synchronized(catalogGate) {
        surfaceVisible = visible
        if (closing.get()) return
        val actual = host ?: return
        if (visible && windowVisible && settlementMutable.value == DesktopContextSettlement.Ready) actual.attach()
        else actual.deactivate()
    }

    private fun setHomeVisible(visible: Boolean): Unit = synchronized(catalogGate) {
        homeVisible = visible
        if (closing.get()) return
        val actual = host ?: return
        if (windowVisible && settlementMutable.value == DesktopContextSettlement.Ready &&
            (surfaceVisible || homeVisible || catalogVisible)) actual.attach()
        else actual.deactivate()
        // Home has no media/file/secret commands. Its disposal does not pause local playback.
    }

    private fun setCatalogVisible(visible: Boolean): Unit {
        synchronized(catalogGate) {
            val attachment = Any()
            catalogAttachment.set(attachment)
            catalogVisible = visible
            if (closing.get()) return
            val actual = host ?: return
            if (settlementMutable.value != DesktopContextSettlement.Ready) { actual.deactivate(); return }
            // Every route/kind exit revokes old query/credit/page targets before reattachment.
            if (!visible) {
                actual.deactivate()
                if (windowVisible && (surfaceVisible || homeVisible)) actual.attach()
                return
            }
            task {
                val previous = issued.get()
                try {
                    val current = captureCurrentLease()
                    synchronized(catalogGate) {
                        if (catalogAttachment.get() !== attachment || !catalogVisible || !windowVisible || closing.get() ||
                            settlementMutable.value != DesktopContextSettlement.Ready) return@task
                        if (previous != current) actual.authorityChanged()
                        actual.attach()
                    }
                } catch (_: Exception) {
                    synchronized(catalogGate) {
                        if (catalogAttachment.get() === attachment && catalogVisible && windowVisible && !closing.get() &&
                            settlementMutable.value == DesktopContextSettlement.Ready) {
                            issued.set(null)
                            actual.authorityChanged()
                            actual.attach()
                        }
                    }
                }
            }
            // Browsing never selects account audio and never opens the account file chooser.
        }
    }

    /** Revoke Host permits now; drain admitted actions before inspecting actual selected media.
     * No task below calls Host.drain(), which waits for a media veto not yet issued here.
     */
    private fun settleNavigation(selectStandalone: Boolean): CompletableFuture<Unit> = synchronized(catalogGate) {
        if (closing.get()) return@synchronized if (accountSuspended && settlementMutable.value == DesktopContextSettlement.Ready)
            CompletableFuture.completedFuture(Unit) else sleepAttempt
                ?: CompletableFuture.failedFuture(AccountStoreException(AccountFailure.ACCOUNT_BUSY))
        navigationRequest = Any()
        navigationWantsStandalone = selectStandalone
        if (navigationExecuting) navigationSettlement?.let { return@synchronized it }
        val expectedOwner = issued.get()
        catalogAttachment.set(Any())
        surfaceVisible = false; homeVisible = false; catalogVisible = false
        host?.deactivate()
        navigationExecuting = true
        settlementMutable.value = DesktopContextSettlement.Settling
        val attempt = task {
            selectionMutex.withLock {
                host?.drainAdmittedActions()
                synchronized(drainLock) { mediaDrain }?.hostAwait()
                if (closing.get() || issued.get() != expectedOwner) unavailable()
                while (true) {
                    val requested = synchronized(catalogGate) { navigationRequest to navigationWantsStandalone }
                    // Browsing another catalog/full account surface retains media. Local routes
                    // recheck BOTH real owners after late admitted account selection has settled.
                    if (requested.second) hostMedia?.selectStandalone { !closing.get() && issued.get() == expectedOwner }
                    if (closing.get() || issued.get() != expectedOwner) unavailable()
                    val finished = synchronized(catalogGate) {
                        (navigationRequest === requested.first).also { if (it) navigationExecuting = false }
                    }
                    if (finished) break
                }
            }
        }
        navigationSettlement = attempt
        attempt.whenComplete { _, failure -> synchronized(catalogGate) {
            if (navigationSettlement === attempt && !closing.get()) {
                navigationExecuting = false
                settlementMutable.value = if (failure == null) DesktopContextSettlement.Ready else DesktopContextSettlement.Failed
            }
        } }
        attempt
    }

    /** Synchronous K/J/media admission fence. The task retains every real drain to completion. */
    private fun quiesceExternal(): CompletableFuture<Unit> {
        val selected = media?.admission?.let { it.hasSelectedLease() && !it.isRetired() } == true
        val actual = fenceExternal()
        // A resolver/rotation may be the current actor. It must unwind before its actor can drain.
        return if (selected && !actual.isDone) CompletableFuture.failedFuture(AccountStoreException(AccountFailure.ACCOUNT_BUSY)) else actual
    }

    private fun fenceAuthorityAction(action: DesktopAccountAction): Unit {
        if (action !in authorityActions) return
        synchronized(drainLock) {
            issued.getAndSet(null)?.let { beforeFence.set(it) }
            val selectedMedia = media
            mediaDrain = null
            playbackController?.beginContextFence()
            selectedMedia?.retireAdmissions()
        }
    }

    private fun fenceExternal(): CompletableFuture<Unit> = synchronized(drainLock) {
        val currentMedia = media
        val previous = mediaDrain
        issued.getAndSet(null)?.let { beforeFence.set(it) }
        if (previous != null && (currentMedia == null || currentMedia.admission.isRetired())) return@synchronized previous
        fenceHostSources()
        playbackController?.beginContextFence()
        currentMedia?.retireAdmissions()
        task {
            playbackController?.fenceSelectedContext()
            drainHostSources()
            currentMedia?.playback?.awaitQuiescent()
            currentMedia?.downloads?.awaitIdle()
            currentMedia?.playback?.awaitPersistence()
            services?.session?.drain()?.hostAwait()
            Unit
        }.also { mediaDrain = it }
    }

    /** A media resolver must unwind before its own actor can settle; never await itself. */
    private fun quiesceMediaPublication(): CompletableFuture<Unit> {
        return quiesceExternal()
    }

    fun beginClose(forSleep: Boolean = false): Unit {
        if (!forSleep) shutdownStarted.set(true)
        val first = closing.compareAndSet(false, true)
        synchronized(catalogGate) {
            catalogAttachment.set(Any())
            settlementMutable.value = DesktopContextSettlement.Settling
        }
        // Sleep may already have restored standalone. Quit must fence that actual successor,
        // rather than reuse the earlier account-drain receipt or reopen its controller epoch.
        synchronized(drainLock) {
            mediaDrain = null
            playbackController?.beginContextFence()
            media?.retireAdmissions()
        }
        if (first) {
            issued.getAndSet(null)?.let { beforeFence.set(it) }
            availabilityMutable.value = DesktopAccountAvailability.Retired
            // J remains writable for already admitted media/checkpoint settlement until playback closes.
            picker?.close(); host?.deactivate(); hostMedia?.retire(); media?.retireAdmissions(); fenceHostSources()
        }
    }

    fun sleep(): Unit {
        if (closing.get() || !sleepStarted.compareAndSet(false, true)) return
        beginClose(forSleep = true)
        // Persist the K fence after real external settlement, before retiring the shared K owner.
        sleepAttempt = task {
            // The navigation job never awaits this sleep task; its failed result is not drain.
            synchronized(catalogGate) { navigationSettlement }?.handle { _, _ -> Unit }?.hostAwait()
            host?.drainAdmittedActions()
            quiesceExternal().hostAwait()
            if (initialized) { authority().suspend().hostAwait(); accountSuspended = true }
            selectionMutex.withLock {
                if (!shutdownStarted.get()) {
                    val fence = playbackController?.fenceSelectedContext()
                    media?.selectStandalone()
                    playbackController?.restoreSelectedContextAfterDrain(fence) { !shutdownStarted.get() }
                }
            }
            account?.retire()
            Unit
        }.also { attempt -> attempt.whenComplete { _, failure ->
            if (failure != null) {
                availabilityMutable.value = DesktopAccountAvailability.RecoveryRequired
                settlementMutable.value = DesktopContextSettlement.Failed
            } else if (!shutdownStarted.get()) settlementMutable.value = DesktopContextSettlement.Ready
        } }
    }

    suspend fun drainExternal(): Unit = withContext(NonCancellable) {
        // Close retains and joins navigation before the controller/J lifetime can end.
        synchronized(catalogGate) { navigationSettlement }?.handle { _, _ -> Unit }?.hostAwait()
        sleepAttempt?.hostAwait()
        quiesceExternal().hostAwait()
        picker?.drain()?.hostAwait()
        host?.drain()
        hostMedia?.drain()
    }

    /** Playback/source owners must already be closed before the J -> K lifetime sequence. */
    suspend fun closeAfterPlaybackAndSources(): Unit = withContext(NonCancellable) {
        beginClose()
        closeMutex.withLock {
            if (ended) return@withLock
            drainExternal()
            accountObserver?.cancelAndJoin()
            if (mediaAttempt?.close()?.let { it != MediaHandleRelease.RELEASED } == true)
                throw AccountStoreException(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            hostMedia?.confirmMediaClosed()
            sleepAttempt?.hostAwait()
            localData?.close()
            while (retainedWork.isNotEmpty()) CompletableFuture.allOf(*retainedWork.toTypedArray()).hostAwait()
            account?.close()
            account?.drain()?.hostAwait()
            services?.session?.drain()?.hostAwait()
            services?.closeCatalogSearch()
            services?.closeHttpAfterDrain()
            owners?.authority?.drainSettled()?.hostAwait()
            // Runtime retirement leaves K live. Fence durably before services.retire retires J.
            if (initialized && !accountSuspended) {
                owners?.authority?.suspend()?.hostAwait()
                accountSuspended = true
            }
            host?.close()
            host?.drain()
            owners?.authority?.closeStore()?.hostAwait()
            secure?.drainSettled()?.hostAwait()
            ended = true
            supervisor.cancelAndJoin()
        }
    }

    private inner class PublicationGate : ProtectedMediaPublicationGate {
        override fun quiesce(owner: SecureRecordOwner): CompletableFuture<Unit> =
            if (owner.role == SecureRole.LOCAL) fenceExternal() else quiesceMediaPublication()
        override fun quiesceConnection(owner: SecureRecordOwner): CompletableFuture<Unit> =
            authority().currentLease().thenCompose { lease ->
                services?.session?.fenceGuardPublication(lease)
                authority().quiesceForGuardPublication(owner)
            }
        override fun confirmLocalTransition(owner: SecureRecordOwner, previous: LocalUseGuard?, next: LocalUseGuard): CompletableFuture<Unit> = task {
            val snapshot = keychain().snapshot().hostAwait()
            val record = keychain().read(owner.profile ?: requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved), snapshot).hostAwait() ?: unavailable()
            record.use {
                if (it.owner != owner || next.revision != SecureStateLimits.next(previous?.revision ?: 0) ||
                    previous?.veto?.let { veto -> veto != "NONE" && veto != next.veto } == true) unavailable()
                val bytes = when (val payload = it.payload) {
                    is PortableRootPayload -> payload.localGuardCopy()
                    is SessionPayload -> payload.localGuardCopy()
                    else -> unavailable()
                }
                val actual = try { if (bytes.isEmpty()) null else SecureMediaGuardCodec.readLocal(bytes) } finally { bytes.fill(0) }
                if (actual != previous) unavailable()
                if (next.available && previous?.state != "AVAILABLE") {
                    // This process permit is minted only after the actual explicit ROOT command.
                    // A copied guard/SQL witness cannot unlock a binding or clear its veto.
                    if (owner.role != SecureRole.LOCAL || explicitRootUnlock.getAndSet(null) != owner) unavailable()
                }
                if (owner.accountProjection?.currentAuthorityRevision?.let { revision -> revision != snapshot.authorityRevision } == true)
                    unavailable()
            }
        }
        override fun commitLocalProjection(publication: ProtectedLocalPublication): CompletableFuture<Unit> = task {
            if (publication.guard.available && !publication.owner.detached && !publication.owner.deletionVeto) {
                val lease = if (publication.owner.role == SecureRole.LOCAL) authority().activateRoot().hostAwait()
                    else authority().reactivateAfterGuardPublication(publication.owner).hostAwait()
                if (authority().currentLease().hostAwait() != lease) unavailable()
                issued.set(lease)
            } else {
                issued.set(null)
                beforeFence.getAndSet(null)?.let { owners?.store?.suspendScope(it) }
            }
        }
        override fun localProjectionMatches(publication: ProtectedLocalPublication): CompletableFuture<Boolean> = task {
            val snapshot = keychain().snapshot().hostAwait()
            val record = keychain().read(publication.value.slot, snapshot).hostAwait() ?: return@task false
            record.use {
                val bytes = when (val payload = it.payload) {
                    is PortableRootPayload -> payload.localGuardCopy()
                    is SessionPayload -> payload.localGuardCopy()
                    else -> return@task false
                }
                val guard = try { SecureMediaGuardCodec.readLocal(bytes) } finally { bytes.fill(0) }
                val witness = it.owner.accountProjection
                val canActivate = guard.available && !publication.owner.detached && !publication.owner.deletionVeto
                if (guard != publication.guard || snapshot.authorityRevision != publication.authorityRevision ||
                    it.owner.authorityRevision != publication.authorityRevision || it.revision < publication.value.slotRevision ||
                    it.owner.copy(stage = publication.owner.stage, accountProjection = null) != publication.owner.copy(accountProjection = null) ||
                    witness?.currentAuthorityRevision?.let { revision -> revision != snapshot.authorityRevision } == true) return@task false
                if (!canActivate) {
                    snapshot.selected.singleOrNull { selected -> selected.slot == publication.value.slot } == publication.value &&
                        it.transaction == publication.publicationCommit && witness?.lifecycleAllowed != true && issued.get() == null
                } else {
                    val lease = authority().currentLease().hostAwait()
                    witness != null && witness.lifecycleAllowed && witness.stage == AccountProtectedStage.READY &&
                        witness.storeId == lease.storeId && witness.preparation.scope.scopeId == lease.scopeId &&
                        witness.preparation.profileId == lease.profileId && lease.authorityRevision == snapshot.authorityRevision &&
                        lease == issued.get()
                }
            }
        }
        override fun confirmConnectionGuards(owner: SecureRecordOwner, d1: CapabilityGuard,
            m5: M5CapabilityGuard?, transport: ProtectedTransportPolicy): CompletableFuture<Unit> = task {
            val snapshot = keychain().snapshot().hostAwait()
            val record = keychain().read(requireNotNull(owner.profile), snapshot).hostAwait() ?: unavailable()
            record.use {
                val payload = it.payload as? SessionPayload ?: unavailable()
                val bytes = payload.transportPolicyCopy()
                val policy = try { SecureMediaGuardCodec.readTransport(bytes) } finally { bytes.fill(0) }
                // A DTO confers neither re-trust nor capability authority. Re-read each signed
                // owner-scoped fact below, preserving the protected high-water/content guards.
                if (it.owner != owner || !payload.d1.accepts(d1) || policy != transport || !transport.known ||
                    payload.m5?.let { old -> m5 == null || !old.accepts(m5) } == true) unavailable()
                val runtime = account?.accounts ?: unavailable()
                val selected = runtime.selected ?: unavailable()
                runtime.requireTarget(owner, selected)
                if (selected.transport.protectedPolicy(transport.revision) != transport) unavailable()
                val secret = keychain().access(snapshot, owner).hostAwait()
                val access = secret.copy()
                try {
                    val discoveryResponse = runtime.read(selected.transport, AccountEndpoint.B1_DISCOVERY).hostAwait()
                    val discoveryRaw = try { discoveryResponse.bodyCopy() } finally { discoveryResponse.close() }
                    val discovery = try { (B1.discovery(discoveryRaw, selected.identity, Instant.now()) as? B1.Result.Verified)?.value
                        ?: unavailable() } finally { discoveryRaw.fill(0) }
                    if (!discovery.bindingAvailable) unavailable()
                    val response = runtime.read(selected.transport, AccountEndpoint.M5_CAPABILITIES, access = access).hostAwait()
                    val raw = try { response.bodyCopy() } finally { response.close() }
                    val observed = try { DesktopAccountTrust.capabilities(raw, selected.identity,
                        requireNotNull(owner.account), requireNotNull(owner.device), payload.m5, Instant.now()) }
                        finally { raw.fill(0) }
                    if (observed.guard != m5 || observed.requiredFeatures.isNotEmpty()) unavailable()
                    val d1Response = runtime.read(selected.transport, AccountEndpoint.D1_CAPABILITIES, access = access).hostAwait()
                    val d1Raw = try { d1Response.bodyCopy() } finally { d1Response.close() }
                    val transition = try { verified(D1.capabilities(d1Raw, selected.identity,
                        D1.CapabilityScope(requireNotNull(owner.account), requireNotNull(owner.device), D1.Platform.MACOS), null, Instant.now())) }
                        finally { d1Raw.fill(0) }
                    if (D1.Family.SESSION !in transition.snapshot.families) unavailable()
                    val digest = transition.nextObservation.effectivePayloadSha256
                    try {
                        if (transition.nextObservation.revision != d1.revision ||
                            digest.joinToString("") { byte -> "%02x".format(byte.toInt() and 255) } != d1.contentSha256) unavailable()
                    } finally { digest.fill(0) }
                    d1.b1ContentSha256?.let { expected ->
                        val context = B1.OwnerCapture(selected.identity, requireNotNull(owner.account), requireNotNull(owner.device),
                            D1.Platform.MACOS, keychain().admissionGeneration)
                        val guard = (B1.revisionGuard(context, d1.revision, null, transition.nextObservation) as? B1.Result.Verified)?.value
                            ?: unavailable()
                        val b1Response = runtime.read(selected.transport, AccountEndpoint.B1_CAPABILITIES, access = access).hostAwait()
                        val b1Raw = try { b1Response.bodyCopy() } finally { b1Response.close() }
                        val accepted = try { (B1.capabilities(b1Raw, context, guard, Instant.now()) as? B1.Result.Verified)?.value ?: unavailable() }
                            finally { b1Raw.fill(0) }
                        if (!accepted.snapshot.bindingAvailable || accepted.snapshot.revision != transition.snapshot.revision ||
                            accepted.snapshot.expiresAt <= Instant.now()) unavailable()
                        val b1Digest = requireNotNull(accepted.next.previousB1).effectivePayloadSha256
                        try {
                            if (accepted.next.globalHighWater != d1.revision ||
                                b1Digest.joinToString("") { byte -> "%02x".format(byte.toInt() and 255) } != expected) unavailable()
                        } finally { b1Digest.fill(0) }
                    }
                    if (discovery.expiresAt <= Instant.now() || observed.expiresAt <= Instant.now() ||
                        transition.snapshot.expiresAt <= Instant.now() || runtime.selected != selected) unavailable()
                    val after = keychain().snapshot().hostAwait()
                    if (after != snapshot) unavailable()
                    runtime.requireTarget(owner, selected)
                } finally { access.fill(0); secret.close() }
            }
        }
    }

    private fun <T> task(action: suspend () -> T): CompletableFuture<T> {
        val result = CompletableFuture<T>()
        retainedWork.add(result)
        work.launch {
            try { val value = action(); retainedWork.remove(result); result.complete(value) }
            catch (failure: Throwable) { retainedWork.remove(result); result.completeExceptionally(failure) }
        }
        return result
    }

    companion object {
        private val authorityActions = setOf(
            DesktopAccountAction.CreatePortable, DesktopAccountAction.ImportRecoveryFile,
            DesktopAccountAction.UnlockPortable, DesktopAccountAction.LockPortable,
            DesktopAccountAction.RestorePortable, DesktopAccountAction.RotateRoot,
            DesktopAccountAction.RegisterPortable, DesktopAccountAction.LoginPortable,
            DesktopAccountAction.Reauthenticate, DesktopAccountAction.SubmitInvitation,
            DesktopAccountAction.ConfirmAdmission, DesktopAccountAction.EnrollTrusted, DesktopAccountAction.ConfirmTrusted,
            DesktopAccountAction.Bind, DesktopAccountAction.StartSelf, DesktopAccountAction.ClaimSelf,
            DesktopAccountAction.ApproveSelf, DesktopAccountAction.ExchangeSelf, DesktopAccountAction.CancelSelf,
            DesktopAccountAction.ResumeSelf, DesktopAccountAction.CommitRecovery, DesktopAccountAction.RequestDeletion,
            DesktopAccountAction.CancelDeletion, DesktopAccountAction.ResumeRecovery, DesktopAccountAction.ResumeDeletion,
            DesktopAccountAction.LogoutCurrent, DesktopAccountAction.LogoutAll, DesktopAccountAction.RevokeDevice,
            DesktopAccountAction.Disconnect, DesktopAccountAction.ResumeOperation,
        )
        suspend fun open(bundle: DesktopSignedBundle, standalone: DesktopLibraryStore, scope: CoroutineScope,
            initialLocal: DesktopAudioEngine, createLocal: () -> DesktopAudioEngine, allowMetered: Boolean,
            initializeVirgin: Boolean, language: () -> DesktopLanguage, fenceSources: () -> Unit,
            drainSources: suspend () -> Unit, readMeteredPreference: () -> Boolean,
            saveMeteredPreference: suspend (Boolean) -> Unit): DesktopAccountProductionRuntime {
            val owner = DesktopAccountProductionRuntime(bundle, standalone, scope, language, fenceSources, drainSources,
                readMeteredPreference, saveMeteredPreference)
            try { owner.start(initialLocal, createLocal, allowMetered, initializeVirgin) }
            catch (failure: Throwable) {
                owner.availabilityMutable.value = if (failure is SecureStateFailure && failure.status == SecureStateStatus.IDENTITY_UNAVAILABLE)
                    DesktopAccountAvailability.IdentityUnavailable else DesktopAccountAvailability.RecoveryRequired
                owner.binding = DesktopAccountWorkspaceBinding.unavailable(owner.availabilityMutable.value)
                if (failure is CancellationException) owner.beginClose()
                // The returned owner retains uncertain K/J startup. No replacement store/fake authority.
            }
            return owner
        }
        private fun unavailable(): Nothing = throw AccountStoreException(AccountFailure.ACCOUNT_AUTHORITY_UNAVAILABLE)
    }
}
