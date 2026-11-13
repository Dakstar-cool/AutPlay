package app.autplay.desktop

import app.autplay.application.desktop.account.*
import app.autplay.application.securestate.SecureStateFailure
import app.autplay.application.securestate.SecureStateStatus
import app.autplay.desktop.account.*
import app.autplay.desktop.sync.*
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletionStage
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*

/**
 * Runs the production application graph in a separate signed app on a disposable Mac.
 * Reflection reads existing private owners; it never replaces an owner or changes a field.
 * Real TLS, HTTP, Keychain, SQLite, signatures and PostgreSQL remain on the delivery path.
 */
public object CommunityAccountProbe {
    private fun <T> CompletionStage<T>.settled(): T = toCompletableFuture().get(90, TimeUnit.SECONDS)
    private suspend fun <T> readProtectedState(read: () -> T): T {
        // CONNECTED also wakes the real host observer. Retry only admission contention on
        // these read-only assertions, never an account command or an ambiguous outcome.
        repeat(100) {
            try { return read() }
            catch (failure: ExecutionException) {
                if ((failure.cause as? SecureStateFailure)?.status != SecureStateStatus.IN_USE) throw failure
            }
            delay(100)
        }
        error("ACCOUNT_PROBE_PROTECTED_READ_BUSY_DEADLINE")
    }
    private inline fun <reified T> owner(parent: Any, name: String): T =
        parent.javaClass.getDeclaredField(name).run { isAccessible = true; get(parent) as T }
    private fun uuid(): AccountUuid = AccountUuid(UUID.randomUUID().toString())
    private fun writePrivate(path: Path, bytes: ByteArray) {
        Files.write(path, bytes)
        Files.setPosixFilePermissions(path, PosixFilePermissions.fromString("rw-------"))
    }

    @JvmStatic public fun main(args: Array<String>): Unit = runBlocking {
        check(args.size == 2 && System.getProperty("os.name") == "Mac OS X")
        val phase = args[0]
        check(phase in setOf("register", "restart", "login", "restart-login", "pull"))
        val configPath = Path.of(args[1])
        val config = Json.parseToJsonElement(Files.readString(configPath)).jsonObject
        fun value(name: String): String = config.getValue(name).jsonPrimitive.content
        val privateDir = configPath.parent
        val root = Path.of(System.getProperty("user.home"), "Library/Application Support/AutPlay")
        val runtime = DesktopRuntime.open(root, initializeAccounts = phase in setOf("register", "login"))
        try {
            val production: Any = owner(runtime, "productionAccounts")
            val controller: DesktopAccountController = owner(production, "account")
            val shared: DesktopAccountAuthorityOwners = owner(production, "owners")
            val services: DesktopAccountServices = owner(production, "services")
            val account = controller.accounts
            suspend fun currentLease(): AccountLease {
                // The real host observer also reads authority after CONNECTED publication.
                // Wait only for read contention; authority and reconciliation failures stay fatal.
                repeat(100) {
                    try { return shared.authority.currentLease().settled() }
                    catch (failure: ExecutionException) {
                        if ((failure.cause as? AccountStoreException)?.failure != AccountFailure.ACCOUNT_BUSY) throw failure
                    }
                    delay(100)
                }
                error("ACCOUNT_PROBE_AUTHORITY_BUSY_DEADLINE")
            }
            val tls = AccountTlsTrust.ExactSpki(Base64.getDecoder().decode(value("tls_spki_b64")))
            val streamTls = AccountTlsTrust.ExactSpki(Base64.getDecoder().decode(value("stream_tls_spki_b64")))
            val transport = ConfirmedAccountTransport.confirm(value("origin"), value("stream_origin"), tls, streamTls, true, true)
            val discovery = account.inspectDiscovery(transport).settled().use { it.bodyCopy() }
            try {
                account.selectServer(transport, discovery, value("instance"), value("identity_epoch").toLong(),
                    Base64.getDecoder().decode(value("identity_spki_b64")), true, AccountLocalDataChoice.KeepLocal).settled()
            } finally { discovery.fill(0) }
            val password = value("password").toCharArray()
            try {
                when (phase) {
                    "register" -> {
                        account.createPortable(value("public_id"), password).settled()
                        account.recoveryForExport().settled().use { secret ->
                            val bytes = secret.copy()
                            try {
                                writePrivate(privateDir.resolve("recovery.private"), bytes)
                                account.acknowledgeRecoveryExport(Files.readAllBytes(privateDir.resolve("recovery.private"))).settled()
                            } finally { bytes.fill(0) }
                        }
                        account.connectPortable(password, register = true).settled()
                    }
                    "login" -> {
                        check(!Files.exists(privateDir.resolve("recovery.private")))
                        val before = readProtectedState { account.secure.snapshot().settled() }
                        check(before.selected.none { it.slot == app.autplay.application.securestate.SecureRecordKind.PORTABLE_ROOT.reserved })
                        suspend fun rejectedLogin(id: String, secret: CharArray) {
                            try {
                                val failure = runCatching { account.loginPortable(id, secret).settled() }.exceptionOrNull()
                                check((failure as? ExecutionException)?.cause.let { it as? AccountControlException }?.code == AccountControlFailure.AUTHENTICATION_FAILED)
                                val after = readProtectedState { account.secure.snapshot().settled() }
                                check(after.selected == before.selected && after.keys == before.keys &&
                                    after.reservations.isEmpty() && after.firstBind == null && after.activeBinding == null)
                            } finally { secret.fill('\u0000') }
                        }
                        rejectedLogin(value("public_id"), "incorrect synthetic login password".toCharArray())
                        rejectedLogin("missing_" + UUID.randomUUID().toString().replace("-", "").take(12), password.copyOf())
                        account.loginPortable(value("public_id"), password).settled()
                        check(!account.state.value.recoveryExportRequired)
                        println("ACCOUNT_PROBE_PASSWORD_LOGIN_WITHOUT_RECOVERY_PASSED")
                    }
                    else -> account.restoreBinding(Files.readString(privateDir.resolve(
                        if (phase == "restart-login") "profile-b.private" else "profile-a.private"))).settled()
                }
            } finally { password.fill('\u0000') }
            check(account.state.value.phase == DesktopAccountPhase.CONNECTED)
            println("ACCOUNT_PROBE_CONNECTED $phase")
            if (phase == "register") writePrivate(privateDir.resolve("profile-a.private"),
                currentLease().profileId.value.toByteArray())
            if (phase == "login") writePrivate(privateDir.resolve("profile-b.private"), currentLease().profileId.value.toByteArray())
            if (phase in setOf("login", "restart-login")) {
                readProtectedState {
                    val control = account.secure.snapshot().settled()
                    account.secure.read(requireNotNull(app.autplay.application.securestate.SecureRecordKind.PORTABLE_ROOT.reserved), control).settled().use { record ->
                        val rootPayload = record?.payload as? app.autplay.application.securestate.PortableRootPayload ?: error("MISSING_PASSWORD_LOGIN_ROOT")
                        check(!rootPayload.hasRecoveryDocument && rootPayload.everEnrolled)
                    }
                }
            }
            if (phase == "restart") {
                val before = currentLease()
                controller.profiles.rotateSession().settled()
                val after = currentLease()
                check(after.sessionFamily == before.sessionFamily && after.sessionGeneration > before.sessionGeneration)
                println("ACCOUNT_PROBE_KEYCHAIN_SESSION_ROTATED")
            }
            suspend fun synchronize() {
                repeat(4) {
                    val lease = currentLease()
                    check(services.session.authorityReconciled(lease))
                    val result = services.sync.runPass(lease)
                    if (result.failure == null && result.phase in setOf(DesktopSyncPhase.SYNCED, DesktopSyncPhase.IDLE)) {
                        check(result.pendingCount == 0L && result.deadLetterCount == 0L && result.conflictCount == 0L)
                        return
                    }
                    check(result.failure == DesktopSyncFailure.CONTEXT_CHANGED) { "ACCOUNT_PROBE_SYNC_${result.failure}" }
                }
                error("ACCOUNT_PROBE_SYNC_DID_NOT_SETTLE")
            }
            synchronize()
            if (phase in setOf("register", "login")) {
                val lease = currentLease()
                val change = AccountCreateTrack(uuid(), uuid(), uuid(), "Acceptance $phase", "Synthetic acceptance", System.currentTimeMillis())
                check(services.domain.createTrack(lease, change).eventId != null)
                synchronize()
            }
            val rows = shared.store.readTracks(currentLease(), null)
            check(rows.any { it.title == "Acceptance register" })
            if (phase in setOf("login", "restart-login", "pull")) check(rows.any { it.title == "Acceptance login" })
            println("ACCOUNT_PROBE_SYNC_ROUNDTRIP_PASSED $phase")
        } finally { runtime.close() }
        println("AUTPLAY_ACCOUNT_PROBE_PASSED $phase")
    }
}
