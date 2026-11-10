package app.autplay.desktop.account

import app.autplay.application.desktop.accountflow.DesktopPendingCeremonyMaterial
import app.autplay.application.portableaccount.*
import app.autplay.application.securestate.*
import app.autplay.desktop.security.*
import java.time.Instant
import java.util.Base64
import java.util.UUID
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import kotlinx.serialization.json.*

/** Portable recovery rotates ROOT; it never silently treats a code as a password reset wrapper. */
public class DesktopPortableRecoveryController(private val runtime: DesktopAccountRuntime) {
    /** Explicit code recovery for the retained ROOT on the separately selected server. */
    public fun rotateOnSelectedServer(code: CharArray, newPassword: CharArray): CompletionStage<Unit> {
        val supplied = code.copyOf(); val password = newPassword.copyOf()
        return runtime.secure.snapshot().thenCompose { snapshot ->
            runtime.secure.read(requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved), snapshot).thenApply { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                try {
                    val root = stored.payload as? PortableRootPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                    val passport = root.passportCopy()
                    val document = try { PortableAccountDocument.parse(passport) } finally { passport.fill(0) }
                    val material = PortableRecoveryFile.decodeCode(supplied)
                    try {
                        PortableAccountCrypto.recover(document, material).close()
                        PortableRecoveryFile.encode(document, material)
                    } finally { material.fill(0) }
                } finally { stored.close() }
            }
        }.thenCompose { archive ->
            rotateOnSelectedServer(archive, password).whenComplete { _, _ -> archive.fill(0) }
        }.whenComplete { _, _ -> supplied.fill('\u0000'); password.fill('\u0000') }
    }
    public fun recoverOffline(code: CharArray, password: CharArray): CompletionStage<Unit> {
        val codeCopy = code.copyOf(); val passwordCopy = password.copyOf()
        return runtime.command {
            runtime.secure.snapshot().thenCompose { snapshot ->
                runtime.secure.read(requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved), snapshot).thenCompose { nullable ->
                    val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                    val root = stored.payload as? PortableRootPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                    controlRequire(!root.everEnrolled && snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
                    val passport = root.passportCopy()
                    val document = try { PortableAccountDocument.parse(passport) } finally { passport.fill(0) }
                    val material = PortableRecoveryFile.decodeCode(codeCopy)
                    try {
                        PortableAccountCrypto.recover(document, material).use { unlocked ->
                            val replacement = PortableAccountCrypto.rotate(unlocked, passwordCopy)
                            val owner = runtime.localOwner(snapshot, document.accountId, UUID.randomUUID().toString())
                            runtime.roots.rotateOffline(snapshot, owner, replacement).thenApply { runtime.requireRecoveryExport(); Unit }
                                .whenComplete { _, _ -> replacement.close(); stored.close() }
                        }
                    } finally { material.fill(0) }
                }
            }
        }.whenComplete { _, _ -> codeCopy.fill('\u0000'); passwordCopy.fill('\u0000') }
    }

    /** Explicit selected-server recovery. This source requires an imported/current full archive. */
    public fun rotateOnSelectedServer(recoveryFile: ByteArray, newPassword: CharArray): CompletionStage<Unit> {
        val file = recoveryFile.copyOf(); val password = newPassword.copyOf()
        return runtime.command {
            val server = requireNotNull(runtime.selected)
            val recovered = PortableRecoveryFile.parse(file)
                runtime.secure.snapshot().thenCompose { snapshot ->
                    controlRequire(snapshot.activeBinding == null && snapshot.firstBind == null && snapshot.reservations.isEmpty(), AccountControlFailure.CONFLICT)
                    runtime.secure.read(requireNotNull(SecureRecordKind.PORTABLE_ROOT.reserved), snapshot).thenCompose { nullable ->
                        val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                        val root = stored.payload as? PortableRootPayload ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                        controlRequire(stored.owner.account == recovered.document.accountId, AccountControlFailure.CONTEXT_CHANGED)
                        val passport = root.passportCopy()
                        try { controlRequire(PortableAccountDocument.parse(passport).fields == recovered.document.fields, AccountControlFailure.CONTEXT_CHANGED) }
                        finally { passport.fill(0) }
                        val challengeBody = SecureJson.encode(buildJsonObject { put("account", recovered.document.fields) })
                        runtime.read(server.transport, AccountEndpoint.PORTABLE_ROTATION_CHALLENGE, challengeBody).thenCompose { response ->
                            response.use {
                                val challenge = it.document()
                                requireChallenge(challenge, recovered.document, server, "ROTATE")
                                PortableAccountCrypto.recover(recovered.document, recovered.code).use { previous ->
                                    val replacement = PortableAccountCrypto.rotate(previous, password)
                                    PortableAccountCrypto.unlock(replacement.document, password).use { next ->
                                        val request = JsonObject(challenge + ("replacement_account" to replacement.document.fields))
                                        val canonical = SecureJson.encode(request)
                                        val proof = previous.sign("rotate", canonical)
                                        val replacementProof = try { next.sign("rotate", canonical) }
                                            catch (failure: Exception) { proof.fill(0); throw failure } finally { canonical.fill(0) }
                                        val authorization = try { SecureJson.encode(buildJsonObject {
                                            put("request", request); put("proof", Base64.getEncoder().encodeToString(proof))
                                            put("replacement_proof", Base64.getEncoder().encodeToString(replacementProof))
                                        }) } finally { proof.fill(0); replacementProof.fill(0) }
                                        val retainedRecovery = root.recoveryCopy()
                                        val archive = try { archiveSuccessor(retainedRecovery, recovered, replacement) } finally { retainedRecovery.fill(0) }
                                        val operation = UUID.randomUUID().toString()
                                        val owner = runtime.localOwner(snapshot, recovered.document.accountId, operation).copy(
                                            server = server.identity.serverInstanceId, identityEpoch = server.identity.identityEpoch,
                                            identityThumbprint = server.identity.thumbprintSha256, apiOrigin = server.identity.apiOrigin,
                                            streamOrigin = server.identity.streamOrigin, protocolVersion = 1, stage = SecureStage.PREPARED)
                                        try { runtime.custody.preparePortableRotation(snapshot, owner, authorization, archive, file) { frozenOwner, frozen ->
                                            DesktopFrozenPendingVerifier.verifyRootRotation(frozenOwner, frozen, file)
                                        } }
                                        finally { authorization.fill(0); archive.fill(0); replacement.close() }
                                    }
                                }
                            }
                        }.whenComplete { _, _ -> stored.close(); challengeBody.fill(0) }
                    }
                }.thenCompose(::finishRotation).whenComplete { _, _ -> recovered.close() }
        }.whenComplete { _, _ -> file.fill(0); password.fill('\u0000') }
    }

    public fun resumeRotation(): CompletionStage<Unit> = runtime.command {
        runtime.secure.snapshot().thenCompose { snapshot ->
            val slot = requireNotNull(SecureRecordKind.PORTABLE_COMMAND.reserved)
            runtime.secure.read(slot, snapshot).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                try {
                    controlRequire((stored.payload as? PendingPayload)?.operation == PendingOperation.PORTABLE_ROTATE)
                    runtime.requireTarget(stored.owner, requireNotNull(runtime.selected))
                    runtime.resumeProtected(snapshot, slot, stored.owner.copy(boot = runtime.secure.boot, authorityRevision = snapshot.authorityRevision))
                } finally { stored.close() }
            }
        }.thenCompose(::finishRotation)
    }

    private fun finishRotation(ticket: SecurePendingTicket): CompletableFuture<Unit> =
        runtime.executePending(ticket, AccountEndpoint.PORTABLE_ROTATE).thenCompose { result ->
            val raw = result.response.copy()
            val response = try { SecureJson.parse(raw, 16_384, canonical = false) } finally { raw.fill(0) }
            runtime.secure.read(result.ticket.slot, result.ticket.control).thenCompose { nullable ->
                val stored = nullable ?: throw AccountControlException(AccountControlFailure.CONFLICT)
                val pending = stored.payload as PendingPayload
                val archiveOwner = runtime.secure.retainTransient(DesktopPendingCeremonyMaterial.portableRotationArchive(pending))
                val previousOwner = runtime.secure.retainTransient(DesktopPendingCeremonyMaterial.previousPortableBinding(pending))
                val archive = archiveOwner.copy(); val previous = previousOwner.copy()
                try {
                    val replacement = DesktopPendingCeremonyMaterial.restorePortableSuccessor(archive)
                    controlRequire(response.string("account_id") == stored.owner.account && response.boolean("rotated") &&
                        response["account"] == replacement.document.fields && response.number("revision") > 0)
                    val local = runtime.localOwner(result.ticket.control, requireNotNull(stored.owner.account), requireNotNull(stored.owner.operation))
                    runtime.roots.commitServerRotation(result.ticket.control, local, replacement, previous, { it.operation == stored.owner.operation })
                        .thenCompose { current -> runtime.resumeProtected(current, result.ticket.slot, result.ticket.owner) }
                        .thenCompose { currentTicket ->
                            val exact = result.response.copy()
                            try { runtime.recordTerminal(currentTicket, exact, { owner, _ -> owner.account == response.string("account_id") }, true) }
                            finally { exact.fill(0) }
                        }.thenApply { runtime.requireRecoveryExport(); runtime.retire(); Unit }.whenComplete { _, _ ->
                            replacement.close(); archive.fill(0); previous.fill(0); archiveOwner.close(); previousOwner.close(); stored.close(); result.close()
                        }
                } catch (failure: Exception) {
                    archive.fill(0); previous.fill(0); archiveOwner.close(); previousOwner.close(); stored.close(); result.close(); throw failure
                }
            }
        }

    private fun archiveSuccessor(currentArchive: ByteArray, supplied: PortableRecoveryFile, replacement: CreatedPortableAccount): ByteArray {
        PortableRecoveryFile.parse(currentArchive).use { current ->
            controlRequire(current.document.fields == supplied.document.fields, AccountControlFailure.CONTEXT_CHANGED)
            val versions = (current.predecessors + supplied.predecessors + PortableRecoveryPredecessor(supplied.document, supplied.code))
                .groupBy { it.document.keyThumbprint }.values.map { group ->
                    val first = group.first(); controlRequire(group.all { it.document.fields == first.document.fields && it.code.contentEquals(first.code) })
                    PortableRecoveryPredecessor(first.document, first.code.copyOf())
                }
            try { controlRequire(versions.size <= 8, AccountControlFailure.CAPACITY); return PortableRecoveryFile.encode(replacement.document, replacement.recoveryCode, versions) }
            finally { versions.forEach { it.close() } }
        }
    }
    private fun requireChallenge(value: JsonObject, document: PortableAccountDocument, server: AccountSelectedServer, purpose: String) {
        value.exact("challenge", "account")
        val c = value["challenge"] as? JsonObject ?: throw AccountControlException(AccountControlFailure.INVALID_RESPONSE)
        val passport = document.encode()
        try { controlRequire(value["account"] == document.fields && c.string("purpose") == purpose && c.string("account_id") == document.accountId &&
            c.string("document_sha256") == secureSha256(passport) && c.string("server_instance_id") == server.identity.serverInstanceId &&
            c.number("identity_epoch") == server.identity.identityEpoch && c.string("identity_thumbprint_sha256") == server.identity.thumbprintSha256 &&
            c.string("api_origin") == server.identity.apiOrigin && c.string("stream_origin") == server.identity.streamOrigin) }
        finally { passport.fill(0) }
        val expiry = Instant.parse(c.string("expires_at")); controlRequire(expiry > Instant.now() && expiry < Instant.now().plusSeconds(180))
    }
}
