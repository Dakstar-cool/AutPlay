package app.autplay.application.securestate

import java.nio.ByteBuffer
import kotlinx.serialization.json.*
import org.erdtman.jcs.JsonCanonicalizer

/** Closed local grammar: bounded depth, decoded duplicate names, exact integers, valid Unicode. */
public object SecureJson {
    public fun parse(bytes: ByteArray, maximum: Int, canonical: Boolean = true): JsonObject = safe {
        secureRequire(bytes.size in 2..maximum)
        val text = strictUtf8(bytes)
        Scanner(text).read()
        val element = Json.parseToJsonElement(text) as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        if (canonical) {
            val encoded = encode(element)
            try { secureRequire(encoded.contentEquals(bytes)) } finally { encoded.fill(0) }
        }
        element
    }
    public fun encode(value: JsonElement): ByteArray = safe { JsonCanonicalizer(value.toString()).encodedUTF8 }
    private class Scanner(private val text: String) {
        private var at: Int = 0
        private var nodes: Int = 0
        fun read() { value(0); whitespace(); secureRequire(at == text.length) }
        private fun whitespace() { while (at < text.length && text[at] in " \r\n\t") at++ }
        private fun take(c: Char): Boolean { whitespace(); if (at < text.length && text[at] == c) { at++; return true }; return false }
        private fun value(depth: Int) {
            secureRequire(depth <= 20 && ++nodes <= 8192); whitespace(); secureRequire(at < text.length)
            when (text[at]) {
                '{' -> { at++; val keys = HashSet<String>(); if (!take('}')) {
                    do { whitespace(); secureRequire(keys.add(string())); secureRequire(take(':')); value(depth + 1) } while (take(','))
                    secureRequire(take('}'))
                } }
                '[' -> { at++; if (!take(']')) { do { value(depth + 1) } while (take(',')); secureRequire(take(']')) } }
                '"' -> string()
                't' -> literal("true")
                'f' -> literal("false")
                'n' -> literal("null")
                else -> {
                    val start = at
                    if (text[at] == '-') at++
                    secureRequire(at < text.length && text[at] in '0'..'9')
                    if (text[at] == '0') at++ else while (at < text.length && text[at] in '0'..'9') at++
                    val number = text.substring(start, at)
                    secureRequire(number != "-0" && number.toLongOrNull()?.let { it in -SecureStateLimits.INTEGER..SecureStateLimits.INTEGER } == true)
                }
            }
        }
        private fun literal(value: String) { secureRequire(text.startsWith(value, at)); at += value.length }
        private fun string(): String {
            secureRequire(at < text.length && text[at++] == '"')
            val out = StringBuilder()
            while (true) {
                secureRequire(at < text.length)
                var c = text[at++]
                if (c == '"') break
                secureRequire(c >= ' ')
                if (c == '\\') {
                    secureRequire(at < text.length)
                    c = when (val escape = text[at++]) {
                        '"', '\\', '/' -> escape
                        'b' -> '\b'; 'f' -> '\u000c'; 'n' -> '\n'; 'r' -> '\r'; 't' -> '\t'
                        'u' -> { secureRequire(at + 4 <= text.length); val n = text.substring(at, at + 4).toIntOrNull(16); at += 4; secureRequire(n != null); n!!.toChar() }
                        else -> throw SecureStateFailure(SecureStateStatus.CORRUPT)
                    }
                }
                out.append(c); secureRequire(out.length <= 131072)
            }
            var i = 0
            while (i < out.length) {
                val c = out[i++]
                if (c.isHighSurrogate()) secureRequire(i < out.length && out[i++].isLowSurrogate())
                else secureRequire(!c.isLowSurrogate())
            }
            return out.toString()
        }
    }
}

public fun JsonObject.exact(vararg names: String): Unit = secureRequire(keys == names.toSet())
public fun JsonObject.string(name: String): String = (get(name) as? JsonPrimitive)?.takeIf { it.isString }?.content ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
public fun JsonObject.nullableString(name: String): String? = if (get(name) == JsonNull) null else string(name)
public fun JsonObject.number(name: String): Long = (get(name) as? JsonPrimitive)?.takeUnless { it.isString }?.longOrNull?.also { secureRequire(it in 0..SecureStateLimits.INTEGER) } ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
public fun JsonObject.nullableNumber(name: String): Long? = if (get(name) == JsonNull) null else number(name)
public fun JsonObject.boolean(name: String): Boolean = (get(name) as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
public fun JsonObject.array(name: String): JsonArray = get(name) as? JsonArray ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
internal inline fun <T> safe(block: () -> T): T = try { block() } catch (failure: SecureStateFailure) { throw failure } catch (_: Exception) { throw SecureStateFailure(SecureStateStatus.CORRUPT) }

public object SecureRecordCodec {
    private val magic: ByteArray = "APKVAL1\n".toByteArray(Charsets.US_ASCII)
    public fun encode(record: SecureRecord): ByteArray = safe {
        val header = SecureJson.encode(buildJsonObject {
            put("kind", record.payload.kind.name); put("slot_id", record.slot); put("slot_revision", record.revision)
            put("transaction_id", record.transaction); put("owner", JsonObject(encodeOwner(record.owner) + ("media" to record.payload.mediaHeader()))); put("payload_format", "autplay-k1-typed-binary-1")
        })
        val fields = try { record.payload.fields() } catch (failure: Exception) { header.fill(0); throw failure }
        try {
            val length = fields.sumOf { it.size.toLong() + 4 }
            secureRequire(header.size <= SecureStateLimits.HEADER && 16L + header.size + length <= SecureStateLimits.VALUE)
            ByteBuffer.allocate(16 + header.size + length.toInt()).put(magic).putInt(header.size).putInt(length.toInt()).put(header).also { buffer ->
                fields.forEach { buffer.putInt(it.size).put(it) }
            }.array()
        } finally { header.fill(0); fields.forEach { it.fill(0) } }
    }
    public fun decode(bytes: ByteArray): SecureRecord = safe {
        secureRequire(bytes.size in 18..SecureStateLimits.VALUE)
        val buffer = ByteBuffer.wrap(bytes)
        val actual = ByteArray(8); buffer.get(actual); secureRequire(actual.contentEquals(magic))
        val headerLength = buffer.int; val payloadLength = buffer.int
        secureRequire(headerLength in 2..SecureStateLimits.HEADER && payloadLength >= 0 && 16L + headerLength + payloadLength == bytes.size.toLong())
        val headerBytes = ByteArray(headerLength); buffer.get(headerBytes)
        val header = try { SecureJson.parse(headerBytes, SecureStateLimits.HEADER) } finally { headerBytes.fill(0) }
        header.exact("kind", "slot_id", "slot_revision", "transaction_id", "owner", "payload_format")
        secureRequire(header.string("payload_format") == "autplay-k1-typed-binary-1")
        val kind = SecureRecordKind.valueOf(header.string("kind"))
        val ownerHeader = header["owner"] as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)
        secureRequire("media" in ownerHeader)
        val media = ownerHeader["media"]
        if (kind in setOf(SecureRecordKind.SESSION, SecureRecordKind.PORTABLE_ROOT)) {
            secureRequire(media is JsonObject); (media as JsonObject).exact("local_use", "transport", "m5_guard")
        } else secureRequire(media == JsonNull)
        fun metadata(name: String, maximum: Int): ByteArray {
            val element = (media as JsonObject).getValue(name)
            if (element == JsonNull) return byteArrayOf()
            val result = SecureJson.encode(element)
            if (result.size > maximum) { result.fill(0); throw SecureStateFailure(SecureStateStatus.CORRUPT) }; return result
        }
        val fields = ArrayList<ByteArray>()
        try {
            repeat(when (kind) { SecureRecordKind.SESSION -> 5; SecureRecordKind.PORTABLE_ROOT -> 4; else -> 16 }) {
                secureRequire(buffer.remaining() >= 4)
                val count = buffer.int; secureRequire(count >= 0 && count <= buffer.remaining())
                fields.add(ByteArray(count).also { buffer.get(it) })
            }
            secureRequire(!buffer.hasRemaining())
            val payload: SecurePayload = when (kind) {
                SecureRecordKind.SESSION -> {
                    secureRequire(fields[4].size == 16)
                    val times = ByteBuffer.wrap(fields[4]); val local = metadata("local_use", 512); val transport = metadata("transport", 1024); val m5 = metadata("m5_guard", 256)
                    try { SessionPayload(fields[0], fields[1], guard(fields[3]), if (m5.isEmpty()) null else SecureMediaGuardCodec.readM5(m5), local, transport, m5, times.long, times.long, fields[2]) }
                    finally { local.fill(0); transport.fill(0); m5.fill(0) }
                }
                SecureRecordKind.PORTABLE_ROOT -> {
                    secureRequire(fields[3].size == 9 && fields[3][8].toInt() in 0..1)
                    secureRequire((media as JsonObject)["transport"] == JsonNull && media["m5_guard"] == JsonNull)
                    val local = metadata("local_use", 512)
                    try { PortableRootPayload(fields[0], fields[1], fields[2], ByteBuffer.wrap(fields[3]).long, fields[3][8].toInt() == 1, local) } finally { local.fill(0) }
                }
                else -> {
                    val secrets = PendingSecretContext(fields[10], fields[11], fields[12], fields[13], fields[14], fields[15])
                    try { PendingPayload(kind, PendingOperation.valueOf(strictUtf8(fields[0])), PendingWireKind.valueOf(strictUtf8(fields[1])), fields[2], fields[3], fields[4], fields[5], fields[6], fields[7], fields[8], fields[9], secrets) }
                    finally { secrets.close() }
                }
            }
            try { SecureRecord(header.string("slot_id"), header.number("slot_revision"), header.string("transaction_id"), decodeOwner(JsonObject(ownerHeader - "media")), payload) }
            catch (failure: Exception) { payload.close(); throw failure }
        } finally { fields.forEach { it.fill(0) } }
    }
    private fun guard(bytes: ByteArray): CapabilityGuard {
        val value = SecureJson.parse(bytes, 512); value.exact("revision", "content_sha256", "b1_content_sha256")
        return CapabilityGuard(value.number("revision"), value.string("content_sha256"), value.nullableString("b1_content_sha256"))
    }
    public fun encodeOwner(owner: SecureRecordOwner): JsonObject = buildJsonObject {
        put("boot", owner.boot); put("authority_revision", owner.authorityRevision)
        put("profile", owner.profile?.let(::JsonPrimitive) ?: JsonNull); put("account", owner.account?.let(::JsonPrimitive) ?: JsonNull)
        put("server", owner.server?.let(::JsonPrimitive) ?: JsonNull); put("identity_epoch", owner.identityEpoch?.let(::JsonPrimitive) ?: JsonNull)
        put("identity_thumbprint", owner.identityThumbprint?.let(::JsonPrimitive) ?: JsonNull)
        put("api_origin", owner.apiOrigin?.let(::JsonPrimitive) ?: JsonNull); put("stream_origin", owner.streamOrigin?.let(::JsonPrimitive) ?: JsonNull)
        put("platform", owner.platform); put("protocol_version", owner.protocolVersion)
        put("device", owner.device?.let(::JsonPrimitive) ?: JsonNull); put("key_tag", owner.keyTag?.let(::JsonPrimitive) ?: JsonNull)
        put("key_thumbprint", owner.keyThumbprint?.let(::JsonPrimitive) ?: JsonNull)
        put("binding_commit", owner.bindingCommit?.let(::JsonPrimitive) ?: JsonNull); put("session", owner.session?.let(::JsonPrimitive) ?: JsonNull)
        put("family", owner.family?.let(::JsonPrimitive) ?: JsonNull); put("session_generation", owner.sessionGeneration?.let(::JsonPrimitive) ?: JsonNull)
        put("role", owner.role.name); put("ceremony", owner.ceremony?.let(::JsonPrimitive) ?: JsonNull)
        put("operation", owner.operation?.let(::JsonPrimitive) ?: JsonNull); put("claim", owner.claim?.let(::JsonPrimitive) ?: JsonNull)
        put("expires_at", owner.expiresAtEpochSeconds?.let(::JsonPrimitive) ?: JsonNull); put("stage", owner.stage.name)
        put("deletion_veto", owner.deletionVeto); put("detached", owner.detached)
        put("self_source_platform", owner.selfSourcePlatform?.let(::JsonPrimitive) ?: JsonNull)
        // Absence keeps every pre-amendment byte unchanged; explicit null is not an extension.
        owner.accountProjection?.let { put("account_projection", SecureAccountProjectionCodec.encode(it)) }
    }
    public fun decodeOwner(value: JsonObject): SecureRecordOwner {
        JsonObject(value - "account_projection").exact("boot", "authority_revision", "profile", "account", "server", "identity_epoch", "identity_thumbprint", "api_origin", "stream_origin", "platform", "protocol_version", "device", "key_tag", "key_thumbprint", "binding_commit", "session", "family", "session_generation", "role", "ceremony", "operation", "claim", "expires_at", "stage", "deletion_veto", "detached", "self_source_platform")
        val protocol = value.number("protocol_version"); secureRequire(protocol in 1L..2L)
        return SecureRecordOwner(value.string("boot"), value.number("authority_revision"), value.nullableString("profile"), value.nullableString("account"), value.nullableString("server"), value.nullableNumber("identity_epoch"), value.nullableString("identity_thumbprint"), value.nullableString("api_origin"), value.nullableString("stream_origin"), value.string("platform"), protocol.toInt(), value.nullableString("device"), value.nullableString("key_tag"), value.nullableString("key_thumbprint"), value.nullableString("binding_commit"), value.nullableString("session"), value.nullableString("family"), value.nullableNumber("session_generation"), SecureRole.valueOf(value.string("role")), value.nullableString("ceremony"), value.nullableString("operation"), value.nullableString("claim"), value.nullableNumber("expires_at"), SecureStage.valueOf(value.string("stage")), value.boolean("deletion_veto"), value.boolean("detached"), value.nullableString("self_source_platform"),
            value["account_projection"]?.let { SecureAccountProjectionCodec.decode(it as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)) })
    }
}

public data class SecureValueReference(public val slot: String, public val valueId: String, public val length: Int,
    public val sha256: String, public val slotRevision: Long) {
    init { canonicalUuid(slot); canonicalUuid(valueId); secureRequire(length in 18..SecureStateLimits.VALUE && slotRevision in 1..SecureStateLimits.INTEGER); sha256Text(sha256) }
    override fun toString(): String = "SecureValueReference(<redacted>)"
}
public enum class DeviceKeyState { INTENT, CREATING, BOUND, RETIRED }
public data class DeviceKeyIntent(public val tag: String, public val ownerOperation: String,
    public val state: DeviceKeyState, public val thumbprint: String?) {
    init { deviceKeyTag(tag); canonicalUuid(ownerOperation); thumbprint?.let(::sha256Text); secureRequire(state != DeviceKeyState.BOUND || thumbprint != null) }
    override fun toString(): String = "DeviceKeyIntent(<redacted>)"
}
public data class CompletionReservation(public val operation: String, public val slots: List<String>,
    public val revisions: Int, public val bytes: Long) {
    init { canonicalUuid(operation); secureRequire(slots.size in 1..4 && slots.distinct().size == slots.size && revisions in 0..64 && bytes in 0..SecureStateLimits.TOTAL && (revisions == 0) == (bytes == 0L)); slots.forEach(::canonicalUuid) }
    override fun toString(): String = "CompletionReservation(<redacted>)"
}
public data class FirstBindOwner(public val ceremony: String, public val operation: String, public val role: SecureRole) {
    init { canonicalUuid(ceremony); canonicalUuid(operation) }
    override fun toString(): String = "FirstBindOwner(<redacted>)"
}
public data class SecureControl(
    public val revision: Long, public val authorityRevision: Long,
    public val selected: List<SecureValueReference>, public val activeBinding: String?,
    public val firstBind: FirstBindOwner?, public val keys: List<DeviceKeyIntent>,
    public val reservations: List<CompletionReservation>, public val cleanup: List<SecureValueReference>,
    public val jInstallation: SecureJInstallation? = null,
) {
    init {
        secureRequire(revision in 0..SecureStateLimits.INTEGER && authorityRevision in 0..SecureStateLimits.INTEGER)
        secureRequire(selected.size <= 32 && selected.map { it.slot }.distinct().size == selected.size && keys.size <= 32 && keys.map { it.tag }.distinct().size == keys.size)
        secureRequire((selected + cleanup).size <= 64 && (selected + cleanup).map { it.valueId }.distinct().size == selected.size + cleanup.size)
        secureRequire(reservations.map { it.operation }.distinct().size == reservations.size && reservations.size <= 24)
        secureRequire(activeBinding == null || selected.any { it.slot == activeBinding })
    }
    override fun toString(): String = "SecureControl(<redacted>)"
}
public object SecureControlCodec {
    public fun encode(control: SecureControl): ByteArray {
        val bytes = SecureJson.encode(buildJsonObject {
            put("format", "autplay-secure-control-1"); put("revision", control.revision); put("authority_revision", control.authorityRevision)
            put("selected_slots", JsonArray(control.selected.map(::reference)))
            put("active_binding", control.activeBinding?.let(::JsonPrimitive) ?: JsonNull)
            put("first_bind_owner", control.firstBind?.let { buildJsonObject { put("ceremony", it.ceremony); put("operation", it.operation); put("role", it.role.name) } } ?: JsonNull)
            put("key_intents", JsonArray(control.keys.map { buildJsonObject { put("tag", it.tag); put("owner_operation", it.ownerOperation); put("state", it.state.name); put("thumbprint", it.thumbprint?.let(::JsonPrimitive) ?: JsonNull) } }))
            put("reservations", JsonArray(control.reservations.map { buildJsonObject { put("operation", it.operation); put("slots", JsonArray(it.slots.map(::JsonPrimitive))); put("revisions", it.revisions); put("bytes", it.bytes) } }))
            put("cleanup", JsonArray(control.cleanup.map(::reference)))
            control.jInstallation?.let { put("j_installation", SecureJInstallationCodec.encode(it)) }
        })
        if (bytes.size > SecureStateLimits.CONTROL) { bytes.fill(0); throw SecureStateFailure(SecureStateStatus.CAPACITY_REQUIRED) }
        return bytes
    }
    public fun decode(bytes: ByteArray): SecureControl = safe {
        val value = SecureJson.parse(bytes, SecureStateLimits.CONTROL)
        JsonObject(value - "j_installation").exact("format", "revision", "authority_revision", "selected_slots", "active_binding", "first_bind_owner", "key_intents", "reservations", "cleanup")
        secureRequire(value.string("format") == "autplay-secure-control-1")
        val first = value["first_bind_owner"]?.takeUnless { it == JsonNull }?.let {
            val obj = it as JsonObject; obj.exact("ceremony", "operation", "role")
            FirstBindOwner(obj.string("ceremony"), obj.string("operation"), SecureRole.valueOf(obj.string("role")))
        }
        SecureControl(value.number("revision"), value.number("authority_revision"), value.array("selected_slots").map { readReference(it as JsonObject) }, value.nullableString("active_binding"), first,
            value.array("key_intents").map { val obj = it as JsonObject; obj.exact("tag", "owner_operation", "state", "thumbprint"); DeviceKeyIntent(obj.string("tag"), obj.string("owner_operation"), DeviceKeyState.valueOf(obj.string("state")), obj.nullableString("thumbprint")) },
            value.array("reservations").map { val obj = it as JsonObject; obj.exact("operation", "slots", "revisions", "bytes"); CompletionReservation(obj.string("operation"), obj.array("slots").map { field -> (field as JsonPrimitive).also { secureRequire(it.isString) }.content }, obj.number("revisions").also { secureRequire(it <= 64) }.toInt(), obj.number("bytes")) },
            value.array("cleanup").map { readReference(it as JsonObject) },
            value["j_installation"]?.let { SecureJInstallationCodec.decode(it as? JsonObject ?: throw SecureStateFailure(SecureStateStatus.CORRUPT)) })
    }
    private fun reference(value: SecureValueReference): JsonObject = buildJsonObject { put("slot", value.slot); put("value_id", value.valueId); put("length", value.length); put("sha256", value.sha256); put("slot_revision", value.slotRevision) }
    private fun readReference(value: JsonObject): SecureValueReference {
        value.exact("slot", "value_id", "length", "sha256", "slot_revision")
        val length = value.number("length"); secureRequire(length <= SecureStateLimits.VALUE)
        return SecureValueReference(value.string("slot"), value.string("value_id"), length.toInt(), value.string("sha256"), value.number("slot_revision"))
    }
}

/** Only this typed owner uses the native port. No raw store/query is exposed to controllers. */
public interface SecureNativePort {
    public fun open(): Long
    public fun inventory(handle: Long): SecureNamespaceInventory
    public fun readControl(handle: Long): ByteArray?
    public fun initializeControl(handle: Long, bytes: ByteArray): Unit
    public fun publishControl(handle: Long, expected: ByteArray, next: ByteArray): Unit
    public fun readValue(handle: Long, valueId: String): ByteArray?
    public fun stageValue(handle: Long, valueId: String, bytes: ByteArray): Unit
    public fun deleteValue(handle: Long, valueId: String): Unit
    public fun createNewDeviceKey(handle: Long, tag: String): ByteArray
    public fun publicDeviceKey(handle: Long, tag: String): ByteArray
    public fun signDeviceDigest(handle: Long, tag: String, operation: DeviceProofOperation, digest: ByteArray): ByteArray
    public fun deleteRetiredDeviceKey(handle: Long, tag: String): Unit
    public fun close(handle: Long): Unit
}
public data class SecureNamespaceInventory(public val values: Map<String, Int>, public val keyTags: Set<String>, public val hasControl: Boolean) {
    override fun toString(): String = "SecureNamespaceInventory(<redacted>)"
}
/** Ordinals are ABI1 IDs; append only after an explicit protocol decision. Never accept a domain string. */
public enum class DeviceProofOperation {
    ENROLLMENT_EXCHANGE, ADMISSION_SUBMIT, ADMISSION_POLL, ADMISSION_RECOVER, ADMISSION_EXCHANGE,
    TRUSTED_CHALLENGE, TRUSTED_EXCHANGE, SESSION_ROTATE, SELF_CLAIM, SELF_POLL, SELF_EXCHANGE,
    RECOVERY_PREVIEW, RECOVERY_COMMIT, DELETION_REQUEST, DELETION_PREVIEW, DELETION_CANCEL,
}
