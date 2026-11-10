package app.autplay.desktop.presentation

import app.autplay.application.securestate.*
import app.autplay.desktop.account.*
import app.autplay.ui.desktop.*
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.*

/** Process-only discovery review. An unsigned locator or a self signature never grants trust. */
internal class DesktopAccountHostTrust(private val runtime: DesktopAccountRuntime) : AutoCloseable {
    private class Review(val id: String, val api: String, val stream: String, val server: String,
        val epoch: Long, val spki: ByteArray, val raw: ByteArray, val expires: Instant,
        val apiPolicy: AccountTlsTrust, val streamPolicy: AccountTlsTrust) : AutoCloseable {
        override fun close(): Unit { spki.fill(0); raw.fill(0); clearPolicy(apiPolicy); clearPolicy(streamPolicy) }
    }
    private class Draft(val id: String, val api: String, val stream: String, val apiPolicy: AccountTlsTrust,
        val streamPolicy: AccountTlsTrust, val expires: Instant) : AutoCloseable {
        override fun close(): Unit { clearPolicy(apiPolicy); clearPolicy(streamPolicy) }
    }
    private var pending: Review? = null
    private var draft: Draft? = null
    private var generation = 0L
    val view: DesktopTransportReview? get() = synchronized(this) { pending?.takeIf { it.expires > Instant.now() }?.let {
        DesktopTransportReview(it.id, it.api, it.stream, it.server + ":" + it.epoch + ":" + secureSha256(it.spki),
            fingerprint(it.apiPolicy), fingerprint(it.streamPolicy), it.apiPolicy is AccountTlsTrust.SystemPki,
            it.streamPolicy is AccountTlsTrust.SystemPki)
    } }
    val bootstrap: DesktopTransportBootstrap? get() = synchronized(this) { draft?.takeIf { it.expires > Instant.now() }?.let {
        DesktopTransportBootstrap(it.id, it.api, it.stream, fingerprint(it.apiPolicy), fingerprint(it.streamPolicy))
    } }

    suspend fun discover(origin: CharArray): Unit {
        val entered = origin.concatToString()
        val captured = begin()
        // This transport is restricted to public M5 discovery by the HTTP owner. It cannot carry
        // bearer headers, sign, enroll or access a stream before both policies are confirmed.
        val probe = ConfirmedAccountTransport.discoveryProbe(entered)
        val response = runtime.inspectDiscovery(probe).hostAwait()
        response.use {
            val raw = it.bodyCopy()
            try { review(raw, entered, null, AccountTlsTrust.SystemPki, AccountTlsTrust.SystemPki, captured) } finally { raw.fill(0) }
        }
    }

    /** QR input is existing signed discovery metadata, not an invitation or a new wire format. */
    fun submitDiscovery(raw: ByteArray): Unit = review(raw, null, null, AccountTlsTrust.SystemPki, AccountTlsTrust.SystemPki, begin())

    fun preparePinned(api: CharArray, stream: CharArray, apiPin: CharArray, streamPin: CharArray): Unit {
        val captured = begin()
        val apiOrigin = api.concatToString(); val streamOrigin = stream.concatToString()
        ConfirmedAccountTransport.discoveryProbe(apiOrigin); ConfirmedAccountTransport.discoveryProbe(streamOrigin)
        val apiPolicy = pin(apiPin)
        val streamPolicy = try { pin(streamPin) } catch (error: Exception) { clearPolicy(apiPolicy); throw error }
        synchronized(this) {
            if (generation != captured) { clearPolicy(apiPolicy); clearPolicy(streamPolicy); fail(AccountControlFailure.CONTEXT_CHANGED) }
            draft = Draft(UUID.randomUUID().toString(), apiOrigin, streamOrigin, apiPolicy, streamPolicy, Instant.now().plusSeconds(120))
        }
    }

    suspend fun confirmPinned(command: DesktopAccountCommand.ConfirmPinnedTransport): Unit {
        val (captured, copy) = synchronized(this) {
            val value = draft?.takeIf { it.id == command.targetId && it.expires > Instant.now() } ?: fail(AccountControlFailure.EXPIRED)
            generation to Draft(value.id, value.api, value.stream, copyPolicy(value.apiPolicy), copyPolicy(value.streamPolicy), value.expires)
        }
        copy.use {
            val transport = ConfirmedAccountTransport.confirm(it.api, it.stream, copyPolicy(it.apiPolicy), copyPolicy(it.streamPolicy),
                command.apiConfirmed, command.streamConfirmed)
            runtime.inspectDiscovery(transport).hostAwait().use { response ->
                val raw = response.bodyCopy()
                try { review(raw, it.api, it.stream, it.apiPolicy, it.streamPolicy, captured) } finally { raw.fill(0) }
            }
        }
    }

    private fun review(raw: ByteArray, expectedApi: String?, expectedStream: String?, apiPolicy: AccountTlsTrust,
        streamPolicy: AccountTlsTrust, captured: Long): Unit {
        val envelope = SecureJson.parse(raw, 16_384, canonical = false)
        val payload = envelope["payload"] as? JsonObject ?: fail(AccountControlFailure.INVALID_RESPONSE)
        val key = DesktopAccountTrust.decode(payload.string("identity_public_key_spki_b64"), false, 91)
        try {
            val checked = DesktopAccountTrust.signed(raw, key, "AutPlay discovery v1\n", 16_384)
            val expires = DesktopAccountTrust.fresh(checked, Instant.now(), 600)
            val api = checked.string("api_origin"); val stream = checked.string("stream_origin")
            // Validate both origin shapes without treating either as user confirmation.
            ConfirmedAccountTransport.discoveryProbe(api); ConfirmedAccountTransport.discoveryProbe(stream)
            if (expectedApi != null && api != expectedApi) fail(AccountControlFailure.TRUST_REQUIRED)
            if (expectedStream != null && stream != expectedStream) fail(AccountControlFailure.TRUST_REQUIRED)
            val server = checked.string("server_instance_id"); canonicalUuid(server)
            if (checked.number("identity_epoch") < 1 || checked.string("identity_thumbprint_sha256") != secureSha256(key))
                fail(AccountControlFailure.INVALID_RESPONSE)
            val next = Review(UUID.randomUUID().toString(), api, stream, server, checked.number("identity_epoch"),
                key.copyOf(), raw.copyOf(), expires, copyPolicy(apiPolicy), copyPolicy(streamPolicy))
            synchronized(this) {
                if (generation != captured) { next.close(); fail(AccountControlFailure.CONTEXT_CHANGED) }
                pending?.close(); pending = next; draft?.close(); draft = null
            }
        } finally { key.fill(0) }
    }

    suspend fun confirm(command: DesktopAccountCommand.ConfirmTransport, choice: AccountLocalDataChoice): Unit {
        val (captured, review) = synchronized(this) {
            val value = pending?.takeIf { it.id == command.targetId && it.expires > Instant.now() } ?: fail(AccountControlFailure.EXPIRED)
            generation to Review(value.id, value.api, value.stream, value.server, value.epoch, value.spki.copyOf(),
                value.raw.copyOf(), value.expires, copyPolicy(value.apiPolicy), copyPolicy(value.streamPolicy))
        }
        review.use {
            val confirmed = ConfirmedAccountTransport.confirm(it.api, it.stream, copyPolicy(it.apiPolicy),
                copyPolicy(it.streamPolicy), command.apiConfirmed, command.streamConfirmed)
            runtime.selectServer(confirmed, it.raw, it.server, it.epoch, it.spki,
                command.identityConfirmed, choice).hostAwait()
            synchronized(this) {
                if (generation != captured) { runtime.retire(); fail(AccountControlFailure.CONTEXT_CHANGED) }
                close()
            }
        }
    }

    @Synchronized override fun close(): Unit { generation++; pending?.close(); pending = null; draft?.close(); draft = null }
    @Synchronized private fun begin(): Long { close(); return generation }
    private fun fail(code: AccountControlFailure): Nothing = throw AccountControlException(code)
    private fun pin(input: CharArray): AccountTlsTrust {
        if (input.isEmpty()) return AccountTlsTrust.SystemPki
        val text = input.concatToString()
        val bytes = try { Base64.getDecoder().decode(text) } catch (_: Exception) { fail(AccountControlFailure.TRUST_REQUIRED) }
        return try {
            if (bytes.size !in 32..4096 || Base64.getEncoder().encodeToString(bytes) != text) fail(AccountControlFailure.TRUST_REQUIRED)
            AccountTlsTrust.ExactSpki(bytes)
        } finally { bytes.fill(0) }
    }
    private companion object {
        fun copyPolicy(policy: AccountTlsTrust): AccountTlsTrust = when (policy) {
            AccountTlsTrust.SystemPki -> AccountTlsTrust.SystemPki
            is AccountTlsTrust.ExactSpki -> AccountTlsTrust.ExactSpki(policy.bytes)
        }
        fun clearPolicy(policy: AccountTlsTrust): Unit { if (policy is AccountTlsTrust.ExactSpki) policy.bytes.fill(0) }
        fun fingerprint(policy: AccountTlsTrust): String? = (policy as? AccountTlsTrust.ExactSpki)?.let { secureSha256(it.bytes) }
    }
}
