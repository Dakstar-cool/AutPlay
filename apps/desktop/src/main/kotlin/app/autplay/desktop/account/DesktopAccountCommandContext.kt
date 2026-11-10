package app.autplay.desktop.account

import app.autplay.application.securestate.*
import java.nio.ByteBuffer
import kotlinx.serialization.json.*

/** Private K-only authority context: exact header bytes, separately confirmed TLS, explicit local choice. */
internal class AccountCommandContext(val authorization: SecretBytes, val policy: ProtectedTransportPolicy,
    val materialization: SecretBytes) : AutoCloseable {
    override fun close() { authorization.close(); materialization.close() }
    override fun toString(): String = "AccountCommandContext(<redacted>)"
}
internal object AccountCommandContextCodec {
    private val magic = "APFLOW1\n".toByteArray(Charsets.US_ASCII)
    fun isContext(bytes: ByteArray): Boolean = bytes.size >= 8 && bytes.copyOfRange(0, 8).contentEquals(magic)
    fun selection(choice: AccountLocalDataChoice): ByteArray = SecureJson.encode(buildJsonObject {
        put("format", "autplay-account-local-selection-1")
        put("decision", if (choice is AccountLocalDataChoice.ReviewSelected) "REVIEW_SELECTED" else "KEEP_LOCAL")
        put("change_ids", JsonArray((choice as? AccountLocalDataChoice.ReviewSelected)?.changeIds.orEmpty().map(::JsonPrimitive)))
    })
    fun encode(authorization: ByteArray, policy: ProtectedTransportPolicy, selection: ByteArray): ByteArray {
        val transport = SecureMediaGuardCodec.transport(policy)
        try {
            val fields = listOf(authorization, transport, selection)
            val size = 20 + fields.sumOf { it.size }
            controlRequire(size <= 8192, AccountControlFailure.CAPACITY)
            return ByteBuffer.allocate(size).put(magic).also { buffer -> fields.forEach { buffer.putInt(it.size).put(it) } }.array()
        } finally { transport.fill(0) }
    }
    fun replacingAuthorization(previous: PendingPayload, replacement: ByteArray): ByteArray {
        val bytes = previous.authorizationCopy()
        try {
            if (!isContext(bytes)) return replacement.copyOf()
            return read(bytes).use { context ->
                val selection = context.materialization.copy()
                try { encode(replacement, context.policy, selection) } finally { selection.fill(0) }
            }
        } finally { bytes.fill(0) }
    }
    fun read(bytes: ByteArray): AccountCommandContext {
        controlRequire(bytes.size in 20..8192 && isContext(bytes))
        val buffer = ByteBuffer.wrap(bytes); buffer.position(8)
        val fields = ArrayList<ByteArray>()
        try {
            repeat(3) {
                controlRequire(buffer.remaining() >= 4); val count = buffer.int
                controlRequire(count in 0..buffer.remaining()); fields.add(ByteArray(count).also(buffer::get))
            }
            controlRequire(!buffer.hasRemaining())
            val policy = SecureMediaGuardCodec.readTransport(fields[1]); controlRequire(policy.known)
            val selection = SecureJson.parse(fields[2], 8192)
            selection.exact("format", "decision", "change_ids")
            controlRequire(selection.string("format") == "autplay-account-local-selection-1")
            val ids = selection.array("change_ids").map { (it as JsonPrimitive).also { value -> controlRequire(value.isString) }.content }
            ids.forEach(::canonicalUuid)
            controlRequire(ids.size <= 100 && ids.distinct().size == ids.size &&
                ((selection.string("decision") == "KEEP_LOCAL" && ids.isEmpty()) ||
                    (selection.string("decision") == "REVIEW_SELECTED" && ids.isNotEmpty())))
            return AccountCommandContext(SecretBytes(fields[0]), policy, SecretBytes(fields[2]))
        } finally { fields.forEach { it.fill(0) } }
    }
}

internal fun PendingPayload.ceremonyAuthorizationCopy(): ByteArray {
    val bytes = authorizationCopy()
    try { return if (AccountCommandContextCodec.isContext(bytes)) AccountCommandContextCodec.read(bytes).use { it.authorization.copy() }
        else bytes.copyOf() }
    finally { bytes.fill(0) }
}
