package app.autplay.desktop

import app.autplay.application.desktop.account.*
import app.autplay.desktop.account.*
import app.autplay.desktop.sync.*
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletionStage
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*

/**
 * Runs the production application graph in a separate signed app on a disposable Mac.
 * Reflection reads existing private owners; it never replaces an owner or changes a field.
 * Real TLS, HTTP, Keychain, SQLite, signatures and PostgreSQL remain on the delivery path.
 */
public object CommunityAccountProbe {
    private fun <T> CompletionStage<T>.settled(): T = toCompletableFuture().get(90, TimeUnit.SECONDS)
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
        check(phase in setOf("register", "restart", "login", "pull"))
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
            val tls = AccountTlsTrust.ExactSpki(Base64.getDecoder().decode(value("tls_spki_b64")))
            val transport = ConfirmedAccountTransport.confirm(value("origin"), value("origin"), tls, tls, true, true)
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
                        val bytes = Files.readAllBytes(privateDir.resolve("recovery.private"))
                        try {
                            account.importPortable(bytes).settled()
                            account.acknowledgeRecoveryExport(bytes).settled()
                        } finally { bytes.fill(0) }
                        account.connectPortable(password, register = false).settled()
                    }
                    else -> account.restoreBinding(Files.readString(privateDir.resolve("profile-a.private"))).settled()
                }
            } finally { password.fill('\u0000') }
            check(account.state.value.phase == DesktopAccountPhase.CONNECTED)
            println("ACCOUNT_PROBE_CONNECTED $phase")
            if (phase == "register") writePrivate(privateDir.resolve("profile-a.private"),
                shared.authority.currentLease().settled().profileId.value.toByteArray())
            if (phase == "restart") {
                val before = shared.authority.currentLease().settled()
                controller.profiles.rotateSession().settled()
                val after = shared.authority.currentLease().settled()
                check(after.sessionFamily == before.sessionFamily && after.sessionGeneration > before.sessionGeneration)
                println("ACCOUNT_PROBE_KEYCHAIN_SESSION_ROTATED")
            }
            suspend fun synchronize() {
                repeat(4) {
                    val lease = shared.authority.currentLease().settled()
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
                val lease = shared.authority.currentLease().settled()
                val change = AccountCreateTrack(uuid(), uuid(), uuid(), "Acceptance $phase", "Synthetic acceptance", System.currentTimeMillis())
                check(services.domain.createTrack(lease, change).eventId != null)
                synchronize()
            }
            val rows = shared.store.readTracks(shared.authority.currentLease().settled(), null)
            check(rows.any { it.title == "Acceptance register" })
            if (phase in setOf("login", "pull")) check(rows.any { it.title == "Acceptance login" })
            println("ACCOUNT_PROBE_SYNC_ROUNDTRIP_PASSED $phase")
        } finally { runtime.close() }
        println("AUTPLAY_ACCOUNT_PROBE_PASSED $phase")
    }
}
