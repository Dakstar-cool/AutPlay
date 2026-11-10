package app.autplay.desktop.media

import app.autplay.desktop.media.RemoteMediaProtocol.AccessState
import app.autplay.desktop.media.RemoteMediaProtocol.Ack
import app.autplay.desktop.media.RemoteMediaProtocol.AckOutcome
import app.autplay.desktop.media.RemoteMediaProtocol.AuthNeeded
import app.autplay.desktop.media.RemoteMediaProtocol.Code
import app.autplay.desktop.media.RemoteMediaProtocol.Command
import app.autplay.desktop.media.RemoteMediaProtocol.DataFrame
import app.autplay.desktop.media.RemoteMediaProtocol.Disposition
import app.autplay.desktop.media.RemoteMediaProtocol.Event
import app.autplay.desktop.media.RemoteMediaProtocol.Fields
import app.autplay.desktop.media.RemoteMediaProtocol.Hello
import app.autplay.desktop.media.RemoteMediaProtocol.Incoming
import app.autplay.desktop.media.RemoteMediaProtocol.Inspected
import app.autplay.desktop.media.RemoteMediaProtocol.IntentFence
import app.autplay.desktop.media.RemoteMediaProtocol.Invalid
import app.autplay.desktop.media.RemoteMediaProtocol.MAX_BYTES
import app.autplay.desktop.media.RemoteMediaProtocol.MAX_DATA
import app.autplay.desktop.media.RemoteMediaProtocol.MAX_FRAME
import app.autplay.desktop.media.RemoteMediaProtocol.MAX_INTEGER
import app.autplay.desktop.media.RemoteMediaProtocol.MAX_POSITION
import app.autplay.desktop.media.RemoteMediaProtocol.MAX_RANGE
import app.autplay.desktop.media.RemoteMediaProtocol.NAME
import app.autplay.desktop.media.RemoteMediaProtocol.Network
import app.autplay.desktop.media.RemoteMediaProtocol.NetworkStatus
import app.autplay.desktop.media.RemoteMediaProtocol.Playback
import app.autplay.desktop.media.RemoteMediaProtocol.PlaybackStatus
import app.autplay.desktop.media.RemoteMediaProtocol.Purpose
import app.autplay.desktop.media.RemoteMediaProtocol.ReadAdmission
import app.autplay.desktop.media.RemoteMediaProtocol.ReadDecision
import app.autplay.desktop.media.RemoteMediaProtocol.ReadKind
import app.autplay.desktop.media.RemoteMediaProtocol.Ready
import app.autplay.desktop.media.RemoteMediaProtocol.RenewalReason
import app.autplay.desktop.media.RemoteMediaProtocol.Representation
import app.autplay.desktop.media.RemoteMediaProtocol.ResourceType
import app.autplay.desktop.media.RemoteMediaProtocol.RetireReason
import app.autplay.desktop.media.RemoteMediaProtocol.SUFFIXES
import app.autplay.desktop.media.RemoteMediaProtocol.Scalar
import app.autplay.desktop.media.RemoteMediaProtocol.SeekOutcome
import app.autplay.desktop.media.RemoteMediaProtocol.SeekResult
import app.autplay.desktop.media.RemoteMediaProtocol.SourceFailure
import app.autplay.desktop.media.RemoteMediaProtocol.SourceKey
import app.autplay.desktop.media.RemoteMediaProtocol.TransferEnd
import app.autplay.desktop.media.RemoteMediaProtocol.TransferOutcome
import app.autplay.desktop.media.RemoteMediaProtocol.TransportMode
import app.autplay.desktop.media.RemoteMediaProtocol.commandFields
import app.autplay.desktop.media.RemoteMediaProtocol.replyFields
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.URI
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import java.util.UUID

/** Strict flat scalar codec; neither the parser nor validation failures echo input. */
internal object RemoteMediaCodec {
    private val positive = Rule { it is Scalar.Integer && it.value in 1..MAX_INTEGER }
    private val natural = Rule { it is Scalar.Integer && it.value in 0..MAX_INTEGER }
    private val boolean = Rule { it is Scalar.BooleanValue }
    private val uuid = Rule { value -> value is Scalar.Text && canonicalUuid(value.value) }
    private val position = number(0, MAX_POSITION)
    private val offset = number(0, MAX_BYTES)
    private val digest = Rule { it is Scalar.Text && it.value.matches(Regex("[0-9a-f]{64}")) }
    private val etag = Rule { it is Scalar.Text && it.value.matches(Regex("\"sha256-[0-9a-f]{64}\"")) }
    private val code = enumeration<Code>().nullable()
    private val intent = rules("desiredIntentRevision" to positive, "selectionRevision" to positive,
        "sleepFenceRevision" to natural, "timerFenceRevision" to natural)
    private val source = rules("runId" to uuid, "permitId" to uuid, "generation" to positive)
    private val lifetime = rules("authorityExpiresAtMs" to positive, "remainingLifetimeMs" to number(1, 10_000))
    private val representation = rules("etag" to etag, "totalBytes" to number(1, MAX_BYTES),
        "mediaType" to text(128), "formatSuffix" to oneOf(SUFFIXES))
    private val command = rules("v" to number(1, 1), "type" to oneOf(setOf("command")),
        "id" to positive, "op" to text(32), "runId" to uuid)
    private val event = rules("v" to number(1, 1), "type" to text(32)) + source + rules("eventSeq" to positive)
    private val access = rules("renewalId" to positive, "renewalReason" to enumeration<RenewalReason>(),
        "permitRevision" to positive, "credentialGeneration" to natural, "sessionGeneration" to natural)
    private val hello = rules("v" to number(1, 1), "type" to oneOf(setOf("hello")),
        "protocol" to oneOf(setOf(NAME)), "maxFrameBytes" to number(65_536, 65_536))

    private val commands: Map<String, Map<String, Rule>> = mapOf(
        "setNetworkPolicy" to rules("revision" to positive, "allowMeteredDownloads" to boolean),
        "installSource" to source + lifetime + rules(
            "purpose" to enumeration<Purpose>(), "executionAttempt" to positive, "copyId" to uuid,
            "variantId" to uuid, "streamOrigin" to text(4096), "streamUrl" to text(4096),
            "transportMode" to enumeration<TransportMode>(), "transportPolicyRevision" to positive,
            "tlsSpkiSha256" to digest.nullable(), "accessToken" to Rule { it is Scalar.Secret },
            "credentialGeneration" to natural, "sessionGeneration" to natural,
            "accessExpiresAtMs" to positive, "permitRevision" to positive),
        "installRetained" to source + lifetime + representation + rules(
            "etag" to etag.nullable(), "copyId" to uuid, "managedPath" to text(4096),
            "fileIdentity" to Rule { it is Scalar.Text && it.value.matches(Regex("(0|[1-9][0-9]{0,19}):(0|[1-9][0-9]{0,19})")) },
            "localLeaseRevision" to positive, "permitRevision" to positive),
        "authorizeBytes" to source + lifetime + representation + rules("permitRevision" to positive,
            "resourceType" to enumeration<ResourceType>(), "operationId" to uuid, "activationId" to uuid,
            "resourceGeneration" to positive, "attachmentRevision" to natural.nullable()),
        "renewPermit" to source + lifetime + rules("expectedPermitRevision" to positive, "permitRevision" to positive),
        "beginAccessRenewal" to source + rules("renewalId" to positive, "renewalReason" to enumeration<RenewalReason>(),
            "expectedPermitRevision" to positive, "expectedCredentialGeneration" to natural,
            "expectedSessionGeneration" to natural, "renewalDeadlineAtMs" to positive, "remainingRenewalMs" to number(1, 30_000)),
        "replaceAccess" to source + lifetime + access + rules("expectedPermitRevision" to positive,
            "expectedCredentialGeneration" to natural, "expectedSessionGeneration" to natural,
            "accessToken" to Rule { it is Scalar.Secret }, "accessExpiresAtMs" to positive),
        "updateIntent" to intent + rules("wantsPlay" to boolean),
        "activateAudio" to source + intent + rules("audioLeaseId" to uuid, "positionMs" to position,
            "autoplay" to Rule { it is Scalar.BooleanValue && !it.value }),
        "play" to source + intent + rules("audioLeaseId" to uuid),
        "pause" to source + intent + rules("audioLeaseId" to uuid),
        "seek" to source + intent + rules("audioLeaseId" to uuid, "seekId" to positive, "positionMs" to position),
        "startTransfer" to source + rules("transferId" to uuid, "attempt" to positive, "intentRevision" to positive,
            "attemptLeaseRevision" to positive, "offset" to offset),
        "retireSource" to source + rules("reason" to enumeration<RetireReason>()),
        "close" to emptyMap(),
    )
    private val events: Map<String, Map<String, Rule>> = mapOf(
        "representation" to event + representation + rules("permitRevision" to positive,
            "credentialGeneration" to natural, "transportPolicyRevision" to positive),
        "playback" to event + intent + rules("audioLeaseId" to uuid, "status" to enumeration<PlaybackStatus>(),
            "positionMs" to position, "durationMs" to position.nullable(), "isPlaying" to boolean, "code" to code),
        "seekResult" to event + intent + rules("audioLeaseId" to uuid, "seekId" to positive,
            "outcome" to enumeration<SeekOutcome>(), "positionMs" to position, "code" to code),
        "authNeeded" to event + rules("executionAttempt" to positive, "renewalId" to positive,
            "permitRevision" to positive, "rejectedCredentialGeneration" to natural, "sessionGeneration" to natural),
        "accessQuiesced" to event + access,
        "accessApplied" to event + access,
        "transferEnd" to event + rules("transferId" to uuid, "attempt" to positive, "nextOffset" to offset,
            "outcome" to enumeration<TransferOutcome>(), "code" to code),
        "sourceFailure" to event + rules("code" to enumeration<Code>()),
        "readAdmission" to event + rules("readId" to positive, "permitRevision" to positive,
            "credentialGeneration" to natural.nullable(), "transportPolicyRevision" to positive.nullable(),
            "kind" to enumeration<ReadKind>(), "start" to offset.nullable(), "end" to number(0, MAX_BYTES - 1).nullable()),
        "network" to rules("v" to number(1, 1), "type" to oneOf(setOf("network")), "runId" to uuid,
            "revision" to positive, "status" to enumeration<NetworkStatus>(),
            "expensive" to boolean.nullable(), "constrained" to boolean.nullable()),
        "ack" to rules("v" to number(1, 1), "type" to oneOf(setOf("ack")), "id" to positive,
            "outcome" to enumeration<AckOutcome>(), "code" to code),
        "hello" to hello,
        "ready" to rules("v" to number(1, 1), "type" to oneOf(setOf("ready"))),
    )
    private val receipt = rules("v" to number(1, 1), "type" to oneOf(setOf("dataConsumed"))) + source + rules(
        "transferId" to uuid, "attempt" to positive, "sequence" to natural, "nextOffset" to offset,
        "creditBytes" to number(0, MAX_DATA.toLong()), "disposition" to enumeration<Disposition>())
    private val decision = rules("v" to number(1, 1), "type" to oneOf(setOf("readDecision"))) + source + rules(
        "readId" to positive, "permitRevision" to positive, "credentialGeneration" to natural.nullable(),
        "transportPolicyRevision" to positive.nullable(), "decision" to enumeration<ReadDecision>(),
        "authorityExpiresAtMs" to positive.nullable(), "remainingLifetimeMs" to number(1, 10_000).nullable(), "code" to code)

    fun validate(commandValue: Command, runId: UUID, id: Long): Unit = validateHost(commandFields(runId, id, commandValue))
    fun validate(reply: RemoteMediaProtocol.Reply): Unit = validateHost(replyFields(reply))

    /** Writer owns this buffer and wipes it; there is no intermediate JSON String or JSON byte array. */
    fun write(output: DataOutputStream, fields: Fields, admittedAtNanos: Long = System.nanoTime()): Unit {
        val elapsedNanos = System.nanoTime() - admittedAtNanos
        if (elapsedNanos < 0) throw Invalid()
        val elapsedMs = (elapsedNanos + 999_999) / 1_000_000
        for (name in listOf("remainingLifetimeMs", "remainingRenewalMs")) {
            val remaining = (fields[name] as? Scalar.Integer)?.value ?: continue
            if (remaining <= elapsedMs) throw Invalid()
            fields.putValue(name, remaining - elapsedMs)
        }
        validateHost(fields)
        val bytes = ByteArray(MAX_FRAME + 4)
        try {
            val buffer = ByteBuffer.wrap(bytes)
            buffer.position(4); buffer.put(1)
            buffer.put('{'.code.toByte())
            fields.entries.forEachIndexed { index, (key, value) ->
                if (index != 0) buffer.put(','.code.toByte())
                quoted(buffer, key); buffer.put(':'.code.toByte())
                when (value) {
                    is Scalar.Text -> quoted(buffer, value.value)
                    is Scalar.Integer -> ascii(buffer, value.value.toString())
                    is Scalar.BooleanValue -> ascii(buffer, if (value.value) "true" else "false")
                    is Scalar.Secret -> value.value.useBytes { token ->
                        buffer.put('"'.code.toByte())
                        token.forEach { byte ->
                            if (byte == '"'.code.toByte() || byte == '\\'.code.toByte()) buffer.put('\\'.code.toByte())
                            buffer.put(byte)
                        }
                        buffer.put('"'.code.toByte())
                    }
                    Scalar.Null -> ascii(buffer, "null")
                }
            }
            buffer.put('}'.code.toByte())
            val count = buffer.position() - 4
            if (count !in 1..MAX_FRAME) throw Invalid()
            buffer.putInt(0, count)
            output.write(bytes, 0, count + 4); output.flush()
        } catch (_: java.nio.BufferOverflowException) {
            throw Invalid()
        } finally { bytes.fill(0) }
    }

    /** First header byte is read separately by the bridge's partial-frame watchdog. */
    fun read(input: DataInputStream, first: Int): Incoming {
        val length = ((first.toLong() and 255) shl 24) or
            (input.readUnsignedByte().toLong() shl 16) or (input.readUnsignedByte().toLong() shl 8) or input.readUnsignedByte().toLong()
        if (length !in 1..MAX_FRAME.toLong()) throw Invalid()
        val bytes = ByteArray(length.toInt())
        try {
            input.readFully(bytes)
            return when (bytes[0].toInt()) {
                1 -> decodeEvent(FlatParser(bytes, 1).parse())
                2 -> {
                    if (bytes.size !in 42..(41 + MAX_DATA)) throw Invalid()
                    val buffer = ByteBuffer.wrap(bytes); buffer.get()
                    val id = UUID(buffer.long, buffer.long)
                    val attempt = buffer.long; val sequence = buffer.long; val offset = buffer.long
                    if (attempt !in 1..MAX_INTEGER || sequence !in 1..MAX_INTEGER || offset !in 0 until MAX_BYTES ||
                        buffer.remaining().toLong() > MAX_BYTES - offset) throw Invalid()
                    DataFrame(id, attempt, sequence, offset, ByteArray(buffer.remaining()).also { buffer.get(it) })
                }
                else -> throw Invalid()
            }
        } finally { bytes.fill(0) }
    }

    private fun validateHost(fields: Fields): Unit {
        when (fields.string("type")) {
            "hello" -> checkFields(fields, hello)
            "command" -> {
                val op = fields.string("op")
                checkFields(fields, command + (commands[op] ?: throw Invalid()))
                if (op == "installSource") {
                    RemoteMediaUrls.validate(fields.string("streamOrigin"), fields.string("streamUrl"), fields.uuid("variantId"))
                    val pinned = fields.string("transportMode") == "CONFIRMED_SPKI"
                    if (pinned == (fields["tlsSpkiSha256"] === Scalar.Null)) throw Invalid()
                }
                if ((op == "installSource" || op == "replaceAccess") &&
                    fields.long("authorityExpiresAtMs") > fields.long("accessExpiresAtMs")) throw Invalid()
                if (op == "installRetained") {
                    val path = fields.string("managedPath")
                    if (!path.startsWith('/') || path.contains('\\') || path.contains('\u0000') ||
                        path.split('/').any { it == "." || it == ".." } || path.contains("//")) throw Invalid()
                }
                if (op == "authorizeBytes" &&
                    ((fields.string("resourceType") == "DOWNLOAD_INTENT") != (fields["attachmentRevision"] === Scalar.Null))) throw Invalid()
                if ("formatSuffix" in fields) checkRepresentation(fields)
                if (op == "renewPermit" || op == "replaceAccess") {
                    if (fields.long("permitRevision") <= fields.long("expectedPermitRevision")) throw Invalid()
                }
                if (op == "replaceAccess" && (fields.long("credentialGeneration") <= fields.long("expectedCredentialGeneration") ||
                    fields.long("sessionGeneration") <= fields.long("expectedSessionGeneration"))) throw Invalid()
            }
            "dataConsumed" -> {
                checkFields(fields, receipt)
                val start = fields.string("disposition") == "START"
                if ((fields.long("sequence") == 0L) != start ||
                    (fields.string("disposition") == "DISCARDED" && fields.long("creditBytes") != 0L)) throw Invalid()
            }
            "readDecision" -> {
                checkFields(fields, decision)
                val allow = fields.string("decision") == "ALLOW"
                if (allow != (fields["authorityExpiresAtMs"] !== Scalar.Null) ||
                    allow != (fields["remainingLifetimeMs"] !== Scalar.Null) || allow != (fields["code"] === Scalar.Null)) throw Invalid()
                if ((fields["credentialGeneration"] === Scalar.Null) != (fields["transportPolicyRevision"] === Scalar.Null)) throw Invalid()
            }
            else -> throw Invalid()
        }
    }

    private fun decodeEvent(fields: Fields): Event {
        val type = fields.string("type")
        checkFields(fields, events[type] ?: throw Invalid())
        if ("formatSuffix" in fields) checkRepresentation(fields)
        fun sourceKey(): SourceKey = SourceKey(fields.uuid("runId"), fields.uuid("permitId"), fields.long("generation"))
        fun fence(): IntentFence = IntentFence(fields.long("desiredIntentRevision"), fields.long("selectionRevision"),
            fields.long("sleepFenceRevision"), fields.long("timerFenceRevision"))
        val seq = if ("eventSeq" in fields) fields.long("eventSeq") else 0
        val code = fields.enumOrNull<Code>("code")
        return when (type) {
            "hello" -> Hello()
            "ready" -> Ready()
            "ack" -> {
                val outcome = fields.enum<AckOutcome>("outcome")
                if ((outcome == AckOutcome.REJECTED) != (code != null)) throw Invalid()
                Ack(fields.long("id"), outcome, code)
            }
            "representation" -> Inspected(sourceKey(), seq, fields.long("permitRevision"), fields.long("credentialGeneration"),
                fields.long("transportPolicyRevision"), fields.representation())
            "playback" -> {
                val status = fields.enum<PlaybackStatus>("status")
                if ((status == PlaybackStatus.ERROR) != (code != null) ||
                    (fields.bool("isPlaying") && status != PlaybackStatus.READY)) throw Invalid()
                Playback(sourceKey(), seq, fields.uuid("audioLeaseId"), fence(), status,
                    fields.long("positionMs"), fields.longOrNull("durationMs"), fields.bool("isPlaying"), code)
            }
            "seekResult" -> SeekResult(sourceKey(), seq, fields.uuid("audioLeaseId"), fence(), fields.long("seekId"),
                fields.enum("outcome"), fields.long("positionMs"), code)
            "authNeeded" -> AuthNeeded(sourceKey(), seq, fields.long("executionAttempt"), fields.long("renewalId"),
                fields.long("permitRevision"), fields.long("rejectedCredentialGeneration"), fields.long("sessionGeneration"))
            "accessQuiesced", "accessApplied" -> AccessState(sourceKey(), seq, type == "accessApplied", fields.long("renewalId"),
                fields.enum("renewalReason"), fields.long("permitRevision"), fields.long("credentialGeneration"), fields.long("sessionGeneration"))
            "transferEnd" -> TransferEnd(sourceKey(), seq, fields.uuid("transferId"), fields.long("attempt"),
                fields.long("nextOffset"), fields.enum("outcome"), code)
            "sourceFailure" -> SourceFailure(sourceKey(), seq, fields.enum("code"))
            "readAdmission" -> {
                val kind = fields.enum<ReadKind>("kind")
                val start = fields.longOrNull("start"); val end = fields.longOrNull("end")
                val credential = fields.longOrNull("credentialGeneration"); val policy = fields.longOrNull("transportPolicyRevision")
                if ((kind == ReadKind.FILE) != (credential == null) || (kind == ReadKind.FILE) != (policy == null) ||
                    (kind == ReadKind.HEAD) != (start == null) || (kind == ReadKind.HEAD) != (end == null)) throw Invalid()
                if (start != null && end != null && (end < start || end - start + 1 > MAX_RANGE)) throw Invalid()
                ReadAdmission(sourceKey(), seq, fields.long("readId"), fields.long("permitRevision"), credential, policy, kind, start, end)
            }
            "network" -> Network(fields.uuid("runId"), fields.long("revision"), fields.enum("status"),
                fields.boolOrNull("expensive"), fields.boolOrNull("constrained"))
            else -> throw Invalid()
        }
    }

    private fun checkRepresentation(fields: Fields): Unit {
        val allowed = when (fields.string("formatSuffix")) {
            "mp3" -> setOf("audio/mpeg")
            "aac" -> setOf("audio/aac")
            "m4a" -> setOf("audio/mp4", "audio/x-m4a")
            "flac" -> setOf("audio/flac", "audio/x-flac")
            "opus" -> setOf("audio/ogg")
            "wav" -> setOf("audio/wav", "audio/x-wav", "audio/vnd.wave")
            else -> throw Invalid()
        }
        if (fields.string("mediaType") !in allowed) throw Invalid()
    }
    private fun Fields.representation(): Representation = Representation(
        (this["etag"] as? Scalar.Text)?.value, long("totalBytes"), string("mediaType"), string("formatSuffix"))
    private fun Fields.string(key: String): String = (this[key] as? Scalar.Text)?.value ?: throw Invalid()
    private fun Fields.long(key: String): Long = (this[key] as? Scalar.Integer)?.value ?: throw Invalid()
    private fun Fields.longOrNull(key: String): Long? = (this[key] as? Scalar.Integer)?.value
    private fun Fields.bool(key: String): Boolean = (this[key] as? Scalar.BooleanValue)?.value ?: throw Invalid()
    private fun Fields.boolOrNull(key: String): Boolean? = (this[key] as? Scalar.BooleanValue)?.value
    private fun Fields.uuid(key: String): UUID = UUID.fromString(string(key))
    private inline fun <reified T : Enum<T>> Fields.enum(key: String): T = enumValueOf(string(key))
    private inline fun <reified T : Enum<T>> Fields.enumOrNull(key: String): T? = (this[key] as? Scalar.Text)?.value?.let { enumValueOf<T>(it) }
    private fun canonicalUuid(value: String): Boolean = value.matches(Regex("[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
    private class Rule(val accepts: (Scalar) -> Boolean) {
        fun nullable(): Rule = Rule { it === Scalar.Null || accepts(it) }
    }
    private fun rules(vararg pairs: Pair<String, Rule>): Map<String, Rule> = mapOf(*pairs)
    private fun number(min: Long, max: Long): Rule = Rule { it is Scalar.Integer && it.value in min..max }
    private fun text(max: Int): Rule = Rule { it is Scalar.Text && it.value.toByteArray(StandardCharsets.UTF_8).size in 1..max &&
        it.value.none { character -> character.code < 32 || character.code == 127 } }
    private fun oneOf(values: Set<String>): Rule = Rule { it is Scalar.Text && it.value in values }
    private inline fun <reified T : Enum<T>> enumeration(): Rule = oneOf(enumValues<T>().map { it.name }.toSet())
    private fun checkFields(fields: Fields, schema: Map<String, Rule>): Unit {
        if (fields.keys != schema.keys || fields.any { (key, value) -> !schema.getValue(key).accepts(value) }) throw Invalid()
    }
    private fun ascii(buffer: ByteBuffer, text: String): Unit { text.forEach { buffer.put(it.code.toByte()) } }
    private fun quoted(buffer: ByteBuffer, value: String): Unit {
        buffer.put('"'.code.toByte())
        var index = 0
        while (index < value.length) {
            val c = value[index++]
            when {
                c == '"' || c == '\\' -> { buffer.put('\\'.code.toByte()); buffer.put(c.code.toByte()) }
                c.code < 32 -> ascii(buffer, "\\u" + c.code.toString(16).padStart(4, '0'))
                c.isHighSurrogate() -> {
                    if (index >= value.length || !value[index].isLowSurrogate()) throw Invalid()
                    val cp = Character.toCodePoint(c, value[index++])
                    buffer.put((0xf0 or (cp shr 18)).toByte()); buffer.put((0x80 or ((cp shr 12) and 63)).toByte())
                    buffer.put((0x80 or ((cp shr 6) and 63)).toByte()); buffer.put((0x80 or (cp and 63)).toByte())
                }
                c.isLowSurrogate() -> throw Invalid()
                c.code < 128 -> buffer.put(c.code.toByte())
                c.code < 2048 -> { buffer.put((0xc0 or (c.code shr 6)).toByte()); buffer.put((0x80 or (c.code and 63)).toByte()) }
                else -> {
                    buffer.put((0xe0 or (c.code shr 12)).toByte()); buffer.put((0x80 or ((c.code shr 6) and 63)).toByte())
                    buffer.put((0x80 or (c.code and 63)).toByte())
                }
            }
        }
        buffer.put('"'.code.toByte())
    }

    private class FlatParser(bytes: ByteArray, offset: Int) {
        private val text: String = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes, offset, bytes.size - offset)).toString()
        private var index: Int = 0
        fun parse(): Fields {
            val result = Fields(); ws(); take('{'); ws()
            if (!consume('}')) {
                while (true) {
                    val key = string()
                    if (key in result || result.size >= 40) throw Invalid()
                    ws(); take(':'); ws(); result[key] = scalar(); ws()
                    if (consume('}')) break
                    take(','); ws()
                }
            }
            ws(); if (index != text.length) throw Invalid()
            return result
        }
        private fun scalar(): Scalar {
            if (index >= text.length) throw Invalid()
            if (text[index] == '"') return Scalar.Text(string())
            if (word("true")) return Scalar.BooleanValue(true)
            if (word("false")) return Scalar.BooleanValue(false)
            if (word("null")) return Scalar.Null
            val start = index
            if (text[index] !in '0'..'9') throw Invalid()
            if (text[index++] != '0') while (index < text.length && text[index] in '0'..'9') index++
            val value = text.substring(start, index).toLongOrNull() ?: throw Invalid()
            if (value > MAX_INTEGER) throw Invalid()
            return Scalar.Integer(value)
        }
        private fun string(): String {
            take('"'); val result = StringBuilder()
            while (index < text.length) {
                var c = text[index++]
                if (c == '"') return result.toString()
                if (c.code < 32) throw Invalid()
                if (c == '\\') {
                    if (index >= text.length) throw Invalid()
                    c = when (val escape = text[index++]) {
                        '"', '\\', '/' -> escape
                        'b' -> '\b'; 'f' -> '\u000c'; 'n' -> '\n'; 'r' -> '\r'; 't' -> '\t'
                        'u' -> hex()
                        else -> throw Invalid()
                    }
                    if (c.isHighSurrogate()) {
                        take('\\'); take('u'); val low = hex()
                        if (!low.isLowSurrogate()) throw Invalid()
                        result.append(c); result.append(low); continue
                    }
                    if (c.isLowSurrogate()) throw Invalid()
                } else if (c.isHighSurrogate()) {
                    if (index >= text.length || !text[index].isLowSurrogate()) throw Invalid()
                    result.append(c); result.append(text[index++]); continue
                } else if (c.isLowSurrogate()) throw Invalid()
                result.append(c)
            }
            throw Invalid()
        }
        private fun hex(): Char {
            if (text.length - index < 4) throw Invalid()
            val digits = text.substring(index, index + 4)
            if (digits.any { it !in '0'..'9' && it !in 'a'..'f' && it !in 'A'..'F' }) throw Invalid()
            val value = digits.toInt(16)
            index += 4; return value.toChar()
        }
        private fun ws(): Unit { while (index < text.length && text[index] in " \t\r\n") index++ }
        private fun take(c: Char): Unit { if (!consume(c)) throw Invalid() }
        private fun consume(c: Char): Boolean = if (index < text.length && text[index] == c) { index++; true } else false
        private fun word(word: String): Boolean = if (text.startsWith(word, index)) { index += word.length; true } else false
    }
}

/** Validation uses URI syntax only and never DNS, discovery, or an alternate trusted origin. */
internal object RemoteMediaUrls {
    fun validate(origin: String, url: String, variant: UUID): Unit {
        val base = checked(origin)
        if (base.rawPath.isNotEmpty() || base.toASCIIString() != origin) throw Invalid()
        val target = checked(url)
        if (base.rawAuthority != target.rawAuthority) throw Invalid()
        val path = "/api/v1/stream/audio-variants/$variant"
        if (target.rawPath != path && target.rawPath != "$path/") throw Invalid()
    }
    private fun checked(value: String): URI {
        if (value.toByteArray(StandardCharsets.UTF_8).size !in 1..4096 ||
            value.any { it.code !in 33..126 || it == '\\' || it == '%' }) throw Invalid()
        val uri = try { URI(value) } catch (_: Exception) { throw Invalid() }
        if (uri.scheme != "https" || uri.isOpaque || uri.rawUserInfo != null || uri.rawQuery != null || uri.rawFragment != null ||
            uri.host == null || uri.port !in -1..65535 || uri.port == 0 || uri.port == 443) throw Invalid()
        val host = uri.host
        if (host.startsWith('[')) {
            if (host != "[${ipv6(host.removePrefix("[").removeSuffix("]"))}]") throw Invalid()
        } else if (!host.matches(Regex("[a-z0-9]+(?:[.-][a-z0-9]+)*")) || host.length > 253 ||
            host.split('.').any { it.length > 63 || it.startsWith('-') || it.endsWith('-') }) throw Invalid()
        if (host.all { it in '0'..'9' || it == '.' }) {
            val parts = host.split('.')
            if (parts.size != 4 || parts.any { part ->
                    val octet = part.toIntOrNull()
                    octet == null || octet !in 0..255 || (part.length > 1 && part.startsWith('0'))
                }) throw Invalid()
        }
        if (uri.rawAuthority != host + (if (uri.port == -1) "" else ":${uri.port}")) throw Invalid()
        return uri
    }
    private fun ipv6(text: String): String {
        if (!text.matches(Regex("[0-9a-f:]+")) || text.count { it == ':' } < 2 || text.split("::").size > 2) throw Invalid()
        val halves = text.split("::")
        fun groups(part: String): List<Int> = if (part.isEmpty()) emptyList() else part.split(':').map {
            if (it.length !in 1..4) throw Invalid(); it.toIntOrNull(16) ?: throw Invalid()
        }
        val left = groups(halves[0]); val right = if (halves.size == 2) groups(halves[1]) else emptyList()
        val missing = 8 - left.size - right.size
        if ((halves.size == 1 && missing != 0) || (halves.size == 2 && missing < 1)) throw Invalid()
        val groups = left + List(missing) { 0 } + right
        var best = -1; var length = 0; var index = 0
        while (index < 8) {
            if (groups[index] != 0) { index++; continue }
            val start = index; while (index < 8 && groups[index] == 0) index++
            if (index - start > length && index - start >= 2) { best = start; length = index - start }
        }
        if (best < 0) return groups.joinToString(":") { it.toString(16) }
        return groups.take(best).joinToString(":") { it.toString(16) } + "::" +
            groups.drop(best + length).joinToString(":") { it.toString(16) }
    }
}
