package app.autplay.application.syncbinding

import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.Platform
import app.autplay.application.devicecompatibility.DeviceContractV2Negotiation.TrustedM5Identity
import app.autplay.application.devicecompatibility.OriginAuthorityV2
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.NoSuchAlgorithmException
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECFieldFp
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.time.Duration
import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.erdtman.jcs.JsonCanonicalizer

/**
 * Pure CLIENT B1 prerequisite under ADR-059. SOURCE ONLY / UNVERIFIED.
 * No HTTP, signing, enrollment, bearer custody, persistence, cache or mutation authority.
 * Independently trusted M5 identity, current authenticated actor and trusted time are inputs.
 * Public discovery cannot establish trust. These types cannot prove a current bearer, server
 * authority or K/J provenance. A future approved custodian must supply actual protected guard
 * and durable Journal readbacks, compare-and-set them across lifecycle changes, and reconcile
 * the exact bind response durably before publishing BOUND or admitting protocol-1 sync.
 * K extensions remain unapproved; constructing a value here does not implement those readbacks.
 * M5ServerInstanceRow revision is a third, separate view, never this capability high-water.
 */
object SyncBindingContractV2 {
    const val MAX_BODY_BYTES: Int = 16_384
    const val DISCOVERY_MAX_BYTES: Int = 16_384
    const val CAPABILITIES_MAX_BYTES: Int = 65_536
    const val MAX_INTEGER: Long = 9_007_199_254_740_991L
    private const val DISCOVERY_DOMAIN = "autplay:sync-binding:negotiation:discovery:v2\n"
    private const val CAPABILITIES_DOMAIN = "autplay:sync-binding:negotiation:capabilities:v2\n"

    /** Future transport: GET has no query/body; POST has JSON UTF-8, no query/encoding.
     * Exactly one current nonempty Bearer for protected operations, no cookies/proof substitute.
     * Use exact trusted API origin, release M5 PKI/confirmed SPKI (existing opt-in debug rule),
     * no redirects, aliases, platform substitution or v1 fallback. Require no-store/no-cache.
     */
    enum class Operation(val method: String, val path: String, val bearerRequired: Boolean) {
        DISCOVERY("GET", "/api/v1/sync-binding/v2/discovery", false),
        CAPABILITIES("GET", "/api/v1/sync-binding/v2/capabilities", true),
        BIND("POST", "/api/v1/sync-binding/v2/bind", true),
    }

    enum class Failure {
        INVALID_JSON, INVALID_DOCUMENT, TOO_LARGE, UNSUPPORTED, UNAVAILABLE,
        IDENTITY_MISMATCH, SCOPE_MISMATCH, HASH_MISMATCH, INVALID_SIGNATURE,
        EXPIRED, CLOCK_SKEW, REVISION_ROLLBACK, REVISION_CONFLICT,
        GUARD_MISMATCH, STALE_CONTEXT, LINEAGE_REQUIRED, BINDING_UNAVAILABLE,
        ESTABLISHED_TUPLE_REQUIRED, UNEXPECTED_STATUS,
    }
    sealed interface Result<out T> {
        class Verified<T> internal constructor(val value: T) : Result<T> {
            override fun toString(): String = "SyncBindingV2.Verified"
        }
        class Rejected(val failure: Failure) : Result<Nothing> {
            override fun toString(): String = "SyncBindingV2.Rejected($failure)"
        }
    }

    /** Captured from current local authority, never from client wire claims. No default platform. */
    class OwnerCapture(
        val identity: TrustedM5Identity, val userId: String, val deviceId: String,
        val platform: Platform, val lifecycleGeneration: Long,
    ) {
        override fun toString(): String = "SyncBindingV2.OwnerCapture(<redacted>)"
    }

    /** Separate caller-supplied durable J readback; missing/restored J must not fabricate this. */
    class DurableLineage(
        val owner: OwnerCapture, val serverProfileId: String, val journalEpoch: String,
    ) {
        override fun toString(): String = "SyncBindingV2.DurableLineage(<redacted>)"
    }

    class Discovery internal constructor(
        val identity: TrustedM5Identity, val bindingAvailable: Boolean,
        val issuedAt: Instant, val expiresAt: Instant, canonicalEnvelope: ByteArray,
    ) {
        private val bytes: ByteArray = canonicalEnvelope.copyOf()
        val canonicalEnvelope: ByteArray get() = bytes.copyOf()
        // Exact wire order was checked; never expose mutable backing collections.
        val platforms: List<Platform> get() = if (bindingAvailable) listOf(Platform.ANDROID, Platform.MACOS) else emptyList()
        override fun toString(): String = "SyncBindingV2.Discovery(<redacted>)"
    }

    class Capabilities internal constructor(
        val owner: OwnerCapture, val revision: Long, val bindingAvailable: Boolean,
        val issuedAt: Instant, val expiresAt: Instant, canonicalEnvelope: ByteArray,
    ) {
        private val bytes: ByteArray = canonicalEnvelope.copyOf()
        val canonicalEnvelope: ByteArray get() = bytes.copyOf()
        override fun toString(): String = "SyncBindingV2.Capabilities(<redacted>)"
    }

    /** B1 content only, excluding issue/expiry/revision. Never compare with a D1 wire digest. */
    class CapabilityObservation internal constructor(
        val owner: OwnerCapture, val revision: Long, effectiveDigest: ByteArray,
    ) {
        private val digest: ByteArray = effectiveDigest.copyOf()
        val effectivePayloadSha256: ByteArray get() = digest.copyOf()
        internal fun sameContent(other: CapabilityObservation): Boolean = MessageDigest.isEqual(digest, other.digest)
        override fun toString(): String = "SyncBindingV2.CapabilityObservation(<redacted>)"
    }

    /**
     * Comparison snapshot only. globalHighWater is the SAME scoped D1+B1 capability revision.
     * Zero means explicitly authorized initial observation, not recovery from lost custody.
     * Keep a separate D1 content guard and this B1 guard in future approved protected storage.
     * A newer D1 observation may raise globalHighWater without changing the retained B1 digest.
     * No ordinary file/DB cache, lifecycle reset or volatile flag may stand in for that store.
     */
    class RevisionGuard internal constructor(
        val owner: OwnerCapture, val globalHighWater: Long, val previousB1: CapabilityObservation?,
    ) {
        override fun toString(): String = "SyncBindingV2.RevisionGuard(<redacted>)"
    }

    class CapabilityTransition internal constructor(
        val snapshot: Capabilities, val expectedPrevious: RevisionGuard, val next: RevisionGuard,
    ) {
        override fun toString(): String = "SyncBindingV2.CapabilityTransition(<redacted>)"
    }

    /** Pure matching of externally obtained protected readback, not a protected write or receipt. */
    class AcceptedCapabilities internal constructor(val snapshot: Capabilities, internal val guard: RevisionGuard) {
        override fun toString(): String = "SyncBindingV2.AcceptedCapabilities(<redacted>)"
    }

    enum class BindMode { FIRST_BIND, ESTABLISHED_CONFIRMATION }

    /** Only a separately captured durable K/J established tuple may supply this input.
     * A decoded response alone must never be promoted into this type or durable BOUND.
     */
    class EstablishedTuple(val lineage: DurableLineage) {
        override fun toString(): String = "SyncBindingV2.EstablishedTuple(<redacted>)"
    }

    class BindContext internal constructor(
        internal val accepted: AcceptedCapabilities, val lineage: DurableLineage, val mode: BindMode,
    ) {
        override fun toString(): String = "SyncBindingV2.BindContext(<redacted>)"
    }
    class BindRequest internal constructor(internal val context: BindContext, canonicalBody: ByteArray) {
        private val bytes: ByteArray = canonicalBody.copyOf()
        val canonicalBody: ByteArray get() = bytes.copyOf()
        val operation: Operation get() = Operation.BIND
        val mode: BindMode get() = context.mode
        override fun toString(): String = "SyncBindingV2.BindRequest(<redacted>)"
    }
    /** Echo match at remote linearization only, NOT a connection state or continuing authority.
     * Future application must recheck current protected guards, time, owner and K/J readbacks
     * before applying; ESTABLISHED_CONFIRMATION cannot admit a new first bind or sync pass.
     */
    class MatchedBindingResult internal constructor(
        val lineage: DurableLineage, val mode: BindMode, canonicalBody: ByteArray,
    ) {
        private val bytes: ByteArray = canonicalBody.copyOf()
        val canonicalBody: ByteArray get() = bytes.copyOf()
        override fun toString(): String = "SyncBindingV2.MatchedBindingResult(<redacted>)"
    }

    fun discovery(bytes: ByteArray, trusted: TrustedM5Identity, now: Instant): Result<Discovery> = guarded {
        val frame = envelope(bytes, DISCOVERY_MAX_BYTES, discoveryMembers, trusted, DISCOVERY_DOMAIN)
        val available = availability(frame.payload)
        val (issued, expires) = freshness(frame.payload, now, 600)
        Discovery(trusted, available, issued, expires, frame.canonical)
    }

    /** d1 contributes scoped numeric high-water ONLY. Its content digest is deliberately unused. */
    fun revisionGuard(
        owner: OwnerCapture, retainedGlobalHighWater: Long, previousB1: CapabilityObservation?,
        d1: DeviceContractV2Negotiation.CapabilityObservation? = null,
    ): Result<RevisionGuard> = guarded {
        validateOwner(owner)
        ensure(retainedGlobalHighWater in 0..MAX_INTEGER, Failure.INVALID_DOCUMENT)
        if (previousB1 != null) {
            ensure(sameOwner(previousB1.owner, owner), Failure.GUARD_MISMATCH)
            ensure(previousB1.revision <= retainedGlobalHighWater, Failure.GUARD_MISMATCH)
        }
        var highWater = retainedGlobalHighWater
        if (d1 != null) {
            ensure(sameIdentity(d1.identity, owner.identity) && d1.scope.userId == owner.userId &&
                d1.scope.deviceId == owner.deviceId && d1.scope.platform == owner.platform, Failure.GUARD_MISMATCH)
            highWater = maxOf(highWater, d1.revision)
        }
        RevisionGuard(owner, highWater, previousB1)
    }

    fun capabilities(
        bytes: ByteArray, currentOwner: OwnerCapture, guard: RevisionGuard, now: Instant,
    ): Result<CapabilityTransition> = guarded {
        validateOwner(currentOwner)
        ensure(sameOwner(guard.owner, currentOwner), Failure.STALE_CONTEXT)
        val frame = envelope(bytes, CAPABILITIES_MAX_BYTES, capabilityMembers, currentOwner.identity, CAPABILITIES_DOMAIN)
        val payload = frame.payload
        ensure(uuid(payload.text("user_id")) == currentOwner.userId &&
            uuid(payload.text("device_id")) == currentOwner.deviceId &&
            platform(payload.text("platform")) == currentOwner.platform, Failure.SCOPE_MISMATCH)
        val revision = payload.positive("capability_revision")
        val available = availability(payload)
        val (issued, expires) = freshness(payload, now, 3600)
        val digest = sha(canonical(JsonObject(payload.filterKeys {
            it !in setOf("issued_at", "expires_at", "capability_revision")
        })))
        val observation = CapabilityObservation(currentOwner, revision, digest)
        ensure(revision >= guard.globalHighWater, Failure.REVISION_ROLLBACK)
        val previous = guard.previousB1
        ensure(previous == null || previous.revision != revision || previous.sameContent(observation), Failure.REVISION_CONFLICT)
        val snapshot = Capabilities(currentOwner, revision, available, issued, expires, frame.canonical)
        CapabilityTransition(snapshot, guard, RevisionGuard(currentOwner, revision, observation))
    }

    /** Call only with actual protected readback after future approved compare-and-set of next.
     * The caller must atomically fence expectedPrevious, D1's separate guard and generation.
     */
    fun acceptCapabilities(
        transition: CapabilityTransition, protectedReadback: RevisionGuard, currentOwner: OwnerCapture,
    ): Result<AcceptedCapabilities> = guarded {
        validateOwner(currentOwner)
        ensure(sameOwner(transition.snapshot.owner, currentOwner), Failure.STALE_CONTEXT)
        ensure(sameGuard(transition.next, protectedReadback), Failure.GUARD_MISMATCH)
        AcceptedCapabilities(transition.snapshot, protectedReadback)
    }

    fun bindContext(
        accepted: AcceptedCapabilities, currentOwner: OwnerCapture, durableLineage: DurableLineage?,
        currentGuard: RevisionGuard, mode: BindMode, established: EstablishedTuple?, now: Instant,
    ): Result<BindContext> = guarded {
        validateOwner(currentOwner)
        val lineage = durableLineage ?: reject(Failure.LINEAGE_REQUIRED)
        validateLineage(lineage)
        ensure(sameOwner(lineage.owner, currentOwner) && sameOwner(accepted.snapshot.owner, currentOwner) &&
            sameOwner(currentGuard.owner, currentOwner), Failure.STALE_CONTEXT)
        ensure(currentGuard.globalHighWater >= accepted.snapshot.revision, Failure.REVISION_ROLLBACK)
        if (mode == BindMode.FIRST_BIND) {
            ensure(established == null, Failure.ESTABLISHED_TUPLE_REQUIRED)
            ensure(sameGuard(accepted.guard, currentGuard), Failure.GUARD_MISMATCH)
            ensure(accepted.snapshot.expiresAt > now, Failure.EXPIRED)
            ensure(accepted.snapshot.bindingAvailable, Failure.BINDING_UNAVAILABLE)
        } else {
            val tuple = established ?: reject(Failure.ESTABLISHED_TUPLE_REQUIRED)
            validateLineage(tuple.lineage)
            ensure(sameLineage(tuple.lineage, lineage), Failure.STALE_CONTEXT)
            // Exact established readback may use an older revision or disabled/expired view.
            // It still needs current live bearer/trust/history authority at the server, and
            // cannot admit a first bind, bootstrap, sync pass or any new mutation locally.
        }
        BindContext(accepted, lineage, mode)
    }

    /** Recheck captured lifecycle/lineage and current guard immediately before future transport. */
    fun encodeBind(
        context: BindContext, currentOwner: OwnerCapture, durableLineage: DurableLineage?,
        currentGuard: RevisionGuard, now: Instant,
    ): Result<BindRequest> = guarded {
        current(context, currentOwner, durableLineage)
        ensure(sameOwner(currentGuard.owner, currentOwner), Failure.STALE_CONTEXT)
        ensure(currentGuard.globalHighWater >= context.accepted.snapshot.revision, Failure.REVISION_ROLLBACK)
        if (context.mode == BindMode.FIRST_BIND) {
            ensure(sameGuard(context.accepted.guard, currentGuard), Failure.GUARD_MISMATCH)
            ensure(context.accepted.snapshot.expiresAt > now, Failure.EXPIRED)
            ensure(context.accepted.snapshot.bindingAvailable, Failure.BINDING_UNAVAILABLE)
        }
        val fields = lineageFields(context.lineage) + identityFields(currentOwner.identity, true) +
            ("expected_capability_revision" to JsonPrimitive(context.accepted.snapshot.revision))
        val body = canonical(JsonObject(fields))
        ensure(body.size <= MAX_BODY_BYTES, Failure.TOO_LARGE)
        BindRequest(context, body)
    }

    fun decodeBindResult(
        httpStatus: Int, bytes: ByteArray, request: BindRequest,
        currentOwner: OwnerCapture, durableLineage: DurableLineage?,
    ): Result<MatchedBindingResult> = guarded {
        current(request.context, currentOwner, durableLineage)
        ensure(httpStatus == 200, Failure.UNEXPECTED_STATUS)
        val (result, body) = document(bytes, MAX_BODY_BYTES)
        ensure(result.keys == resultMembers, Failure.INVALID_DOCUMENT)
        versions(result)
        eventVersions(result)
        ensure(result.text("state") == "BOUND", Failure.UNSUPPORTED)
        // Validate lexical syntax before comparing the exact context-derived object.
        listOf("user_id", "device_id", "server_profile_id", "journal_epoch", "server_instance_id")
            .forEach { uuid(result.text(it)) }
        result.positive("identity_epoch")
        hash(result.text("identity_thumbprint_sha256"))
        origin(result.text("api_origin")); origin(result.text("stream_origin"))
        platform(result.text("platform"))
        val expected = JsonObject(lineageFields(request.context.lineage) + identityFields(currentOwner.identity, false) +
            mapOf("state" to JsonPrimitive("BOUND"), "platform" to JsonPrimitive(currentOwner.platform.name)))
        ensure(result == expected, Failure.SCOPE_MISMATCH)
        MatchedBindingResult(request.context.lineage, request.mode, body)
    }

    private fun current(context: BindContext, owner: OwnerCapture, lineage: DurableLineage?) {
        validateOwner(owner)
        val actual = lineage ?: reject(Failure.LINEAGE_REQUIRED)
        validateLineage(actual)
        ensure(sameOwner(context.lineage.owner, owner) && sameLineage(context.lineage, actual), Failure.STALE_CONTEXT)
    }
    private fun validateOwner(owner: OwnerCapture) {
        validateIdentity(owner.identity)
        uuid(owner.userId); uuid(owner.deviceId)
        ensure(owner.lifecycleGeneration in 1..MAX_INTEGER, Failure.INVALID_DOCUMENT)
    }
    private fun validateLineage(lineage: DurableLineage) {
        validateOwner(lineage.owner)
        uuid(lineage.serverProfileId); uuid(lineage.journalEpoch)
    }
    private fun sameIdentity(a: TrustedM5Identity, b: TrustedM5Identity): Boolean =
        a.serverInstanceId == b.serverInstanceId && a.identityEpoch == b.identityEpoch &&
            a.thumbprintSha256 == b.thumbprintSha256 && a.apiOrigin == b.apiOrigin &&
            a.streamOrigin == b.streamOrigin && a.publicKeySpki.contentEquals(b.publicKeySpki)
    private fun sameOwner(a: OwnerCapture, b: OwnerCapture): Boolean =
        sameIdentity(a.identity, b.identity) && a.userId == b.userId && a.deviceId == b.deviceId &&
            a.platform == b.platform && a.lifecycleGeneration == b.lifecycleGeneration
    private fun sameLineage(a: DurableLineage, b: DurableLineage): Boolean = sameOwner(a.owner, b.owner) &&
        a.serverProfileId == b.serverProfileId && a.journalEpoch == b.journalEpoch
    private fun sameGuard(a: RevisionGuard, b: RevisionGuard): Boolean =
        sameOwner(a.owner, b.owner) && a.globalHighWater == b.globalHighWater && when {
            a.previousB1 == null -> b.previousB1 == null
            b.previousB1 == null -> false
            else -> a.previousB1.revision == b.previousB1.revision && a.previousB1.sameContent(b.previousB1)
        }
    private fun versionFields(): Map<String, JsonElement> = mapOf(
        "contract_version" to JsonPrimitive("v2"), "schema_version" to JsonPrimitive(2),
        "binding_contract_version" to JsonPrimitive(2), "protocol_version" to JsonPrimitive(1))
    private fun lineageFields(lineage: DurableLineage): Map<String, JsonElement> = versionFields() + mapOf(
        "user_id" to JsonPrimitive(lineage.owner.userId), "device_id" to JsonPrimitive(lineage.owner.deviceId),
        "server_profile_id" to JsonPrimitive(lineage.serverProfileId), "journal_epoch" to JsonPrimitive(lineage.journalEpoch),
        "supported_event_schema_versions" to JsonArray(listOf(JsonPrimitive(1))))
    private fun identityFields(identity: TrustedM5Identity, expected: Boolean): Map<String, JsonElement> {
        val prefix = if (expected) "expected_" else ""
        return mapOf(
            "${prefix}server_instance_id" to JsonPrimitive(identity.serverInstanceId),
            "${prefix}identity_epoch" to JsonPrimitive(identity.identityEpoch),
            "${prefix}identity_thumbprint_sha256" to JsonPrimitive(identity.thumbprintSha256),
            "${prefix}api_origin" to JsonPrimitive(identity.apiOrigin),
            "${prefix}stream_origin" to JsonPrimitive(identity.streamOrigin))
    }

    private class Frame(val payload: JsonObject, val canonical: ByteArray)
    private fun envelope(
        bytes: ByteArray, maximum: Int, members: Set<String>, trusted: TrustedM5Identity, domain: String,
    ): Frame {
        validateIdentity(trusted)
        val (root, whole) = document(bytes, maximum)
        ensure(root.keys == envelopeMembers, Failure.INVALID_DOCUMENT)
        version(root)
        ensure(root.text("signature_algorithm") == "ES256-P1363", Failure.UNSUPPORTED)
        val payload = root["payload"] as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
        ensure(payload.keys == members, Failure.INVALID_DOCUMENT)
        versions(payload)
        ensure(payload.positive("api_major") == 1L, Failure.UNSUPPORTED)
        val epoch = payload.positive("identity_epoch")
        if (members == capabilityMembers) payload.positive("capability_revision")
        val digest = sha(canonical(payload))
        ensure(hash(root.text("payload_sha256")) == hex(digest), Failure.HASH_MISMATCH)
        val spki = base64(payload.text("identity_public_key_spki_b64"), false, 91)
        val publicKey = p256(spki)
        val thumb = hash(payload.text("identity_thumbprint_sha256"))
        ensure(thumb == hex(sha(spki)), Failure.IDENTITY_MISMATCH)
        ensure(uuid(payload.text("server_instance_id")) == trusted.serverInstanceId && epoch == trusted.identityEpoch &&
            thumb == trusted.thumbprintSha256 && spki.contentEquals(trusted.publicKeySpki) &&
            origin(payload.text("api_origin")) == trusted.apiOrigin &&
            origin(payload.text("stream_origin")) == trusted.streamOrigin, Failure.IDENTITY_MISMATCH)
        val signature = base64(root.text("signature_b64url"), true, 64)
        val der = p1363ToDer(signature, publicKey.params.order)
        val valid = try {
            Signature.getInstance("SHA256withECDSA").run {
                initVerify(publicKey); update(domain.toByteArray(Charsets.US_ASCII)); update(digest); verify(der)
            }
        } catch (error: NoSuchAlgorithmException) { throw error }
        catch (_: java.security.GeneralSecurityException) { reject(Failure.INVALID_SIGNATURE) }
        ensure(valid, Failure.INVALID_SIGNATURE)
        return Frame(payload, whole)
    }
    private fun validateIdentity(identity: TrustedM5Identity) {
        uuid(identity.serverInstanceId)
        ensure(identity.identityEpoch in 1..MAX_INTEGER, Failure.INVALID_DOCUMENT)
        hash(identity.thumbprintSha256); origin(identity.apiOrigin); origin(identity.streamOrigin)
        val key = identity.publicKeySpki
        p256(key)
        ensure(hex(sha(key)) == identity.thumbprintSha256, Failure.IDENTITY_MISMATCH)
    }
    private fun document(bytes: ByteArray, maximum: Int): Pair<JsonObject, ByteArray> {
        ensure(bytes.size <= maximum, Failure.TOO_LARGE)
        val raw = try {
            Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes.copyOf())).toString()
        } catch (_: Exception) { reject(Failure.INVALID_JSON) }
        val root = StrictJson(raw).parse() as? JsonObject ?: reject(Failure.INVALID_DOCUMENT)
        val body = canonical(root)
        ensure(body.size <= maximum, Failure.TOO_LARGE)
        return root to body
    }
    private fun version(value: JsonObject) {
        ensure(value.text("contract_version") == "v2" && value.positive("schema_version") == 2L, Failure.UNSUPPORTED)
    }
    private fun versions(value: JsonObject) {
        version(value)
        ensure(value.positive("binding_contract_version") == 2L && value.positive("protocol_version") == 1L, Failure.UNSUPPORTED)
    }
    private fun eventVersions(value: JsonObject) {
        val array = value["supported_event_schema_versions"] as? JsonArray ?: reject(Failure.INVALID_DOCUMENT)
        ensure(array.size == 1, Failure.UNSUPPORTED)
        ensure(positive(array[0]) == 1L, Failure.UNSUPPORTED)
    }
    private fun availability(value: JsonObject): Boolean {
        val token = value["binding_available"] as? JsonPrimitive ?: reject(Failure.INVALID_DOCUMENT)
        ensure(!token.isString && token.content in setOf("true", "false"), Failure.INVALID_DOCUMENT)
        val available = token.content == "true"
        val platforms = value["platforms"] as? JsonArray ?: reject(Failure.INVALID_DOCUMENT)
        ensure(platforms.map { string(it) } == if (available) listOf("ANDROID", "MACOS") else emptyList<String>(),
            Failure.INVALID_DOCUMENT)
        return available
    }
    private fun freshness(value: JsonObject, now: Instant, seconds: Long): Pair<Instant, Instant> {
        val issued = time(value.text("issued_at")); val expires = time(value.text("expires_at"))
        val ttl = Duration.between(issued, expires)
        ensure(!ttl.isNegative && !ttl.isZero && ttl <= Duration.ofSeconds(seconds), Failure.INVALID_DOCUMENT)
        ensure(expires > now, Failure.EXPIRED)
        ensure(Duration.between(now, issued) <= Duration.ofSeconds(120), Failure.CLOCK_SKEW)
        return issued to expires
    }
    private fun time(value: String): Instant {
        ensure(Regex("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?Z").matches(value), Failure.INVALID_DOCUMENT)
        return java.time.LocalDateTime.parse(value.dropLast(1)).toInstant(java.time.ZoneOffset.UTC)
    }
    private fun platform(value: String): Platform = when (value) {
        "ANDROID" -> Platform.ANDROID
        "MACOS" -> Platform.MACOS
        else -> reject(Failure.UNSUPPORTED)
    }
    private fun JsonObject.text(name: String): String = string(this[name])
    private fun string(value: JsonElement?): String =
        (value as? JsonPrimitive)?.takeIf { it.isString }?.content ?: reject(Failure.INVALID_DOCUMENT)
    private fun JsonObject.positive(name: String): Long = positive(this[name])
    private fun positive(element: JsonElement?): Long {
        val token = element as? JsonPrimitive ?: reject(Failure.INVALID_DOCUMENT)
        ensure(!token.isString && Regex("[1-9][0-9]{0,15}").matches(token.content), Failure.INVALID_DOCUMENT)
        val number = token.content.toLongOrNull() ?: reject(Failure.INVALID_DOCUMENT)
        ensure(number in 1..MAX_INTEGER, Failure.INVALID_DOCUMENT)
        return number
    }
    private fun uuid(value: String): String {
        ensure(Regex("[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}").matches(value) && UUID.fromString(value).toString() == value,
            Failure.INVALID_DOCUMENT)
        return value
    }
    private fun hash(value: String): String {
        ensure(Regex("[0-9a-f]{64}").matches(value), Failure.INVALID_DOCUMENT)
        return value
    }
    private fun origin(value: String): String {
        ensure(value.length in 1..2048 && value.all { it.code in 33..126 }, Failure.INVALID_DOCUMENT)
        val authority = OriginAuthorityV2.parse(value) ?: reject(Failure.INVALID_DOCUMENT)
        ensure(authority.portText == null || authority.portText.length in 1..5, Failure.INVALID_DOCUMENT)
        ensure(authority.port == null || authority.port in 1..65535, Failure.INVALID_DOCUMENT)
        ensure(authority.isIpv6 || authority.hasDnsName(253), Failure.INVALID_DOCUMENT)
        return value
    }
    private fun base64(value: String, url: Boolean, size: Int): ByteArray {
        ensure(value.length <= 256, Failure.INVALID_DOCUMENT)
        val decoded = try { (if (url) Base64.getUrlDecoder() else Base64.getDecoder()).decode(value) }
        catch (_: IllegalArgumentException) { reject(Failure.INVALID_DOCUMENT) }
        val encoded = (if (url) Base64.getUrlEncoder().withoutPadding() else Base64.getEncoder()).encodeToString(decoded)
        ensure(decoded.size == size && encoded == value, Failure.INVALID_DOCUMENT)
        return decoded
    }
    private fun p256(spki: ByteArray): ECPublicKey {
        val prefix = byteArrayOf(0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86.toByte(),
            0x48, 0xce.toByte(), 0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86.toByte(), 0x48,
            0xce.toByte(), 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00, 0x04)
        ensure(spki.size == 91 && spki.copyOfRange(0, prefix.size).contentEquals(prefix), Failure.INVALID_DOCUMENT)
        val key = KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(spki)) as? ECPublicKey
            ?: reject(Failure.INVALID_DOCUMENT)
        val expected = AlgorithmParameters.getInstance("EC").apply { init(ECGenParameterSpec("secp256r1")) }
            .getParameterSpec(ECParameterSpec::class.java)
        val actual = key.params
        ensure(actual.curve == expected.curve && actual.generator == expected.generator && actual.order == expected.order &&
            actual.cofactor == expected.cofactor && key.encoded.contentEquals(spki), Failure.INVALID_DOCUMENT)
        val p = (expected.curve.field as ECFieldFp).p
        val x = key.w.affineX; val y = key.w.affineY
        ensure(x.signum() >= 0 && x < p && y.signum() >= 0 && y < p &&
            y.multiply(y).mod(p) == x.multiply(x).multiply(x).add(expected.curve.a.multiply(x)).add(expected.curve.b).mod(p),
            Failure.INVALID_DOCUMENT)
        return key
    }
    private fun p1363ToDer(signature: ByteArray, order: BigInteger): ByteArray {
        fun integer(offset: Int): ByteArray {
            val value = BigInteger(1, signature.copyOfRange(offset, offset + 32))
            ensure(value.signum() > 0 && value < order, Failure.INVALID_SIGNATURE)
            return value.toByteArray() // Minimal positive DER INTEGER; high-S remains valid.
        }
        val r = integer(0); val s = integer(32)
        return byteArrayOf(0x30, (4 + r.size + s.size).toByte(), 0x02, r.size.toByte()) + r +
            byteArrayOf(0x02, s.size.toByte()) + s
    }
    private fun canonical(value: JsonObject): ByteArray = JsonCanonicalizer(value.toString()).encodedString.toByteArray(Charsets.UTF_8)
    private fun sha(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)
    private fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it.toInt() and 255) }
    private class Invalid(val failure: Failure) : RuntimeException(failure.name, null, false, false)
    private fun reject(failure: Failure): Nothing = throw Invalid(failure)
    private fun ensure(condition: Boolean, failure: Failure) { if (!condition) reject(failure) }
    private inline fun <T> guarded(block: () -> T): Result<T> = try { Result.Verified(block()) }
    catch (error: Invalid) { Result.Rejected(error.failure) }
    catch (_: NoSuchAlgorithmException) { Result.Rejected(Failure.UNAVAILABLE) }
    catch (_: Exception) { Result.Rejected(Failure.INVALID_DOCUMENT) }

    private val envelopeMembers = setOf("contract_version", "schema_version", "payload", "payload_sha256",
        "signature_algorithm", "signature_b64url")
    private val discoveryMembers = setOf("contract_version", "schema_version", "binding_contract_version", "protocol_version",
        "api_major", "server_instance_id", "identity_epoch", "identity_public_key_spki_b64", "identity_thumbprint_sha256",
        "api_origin", "stream_origin", "binding_available", "platforms", "issued_at", "expires_at")
    private val capabilityMembers = discoveryMembers + setOf("user_id", "device_id", "platform", "capability_revision")
    private val resultMembers = setOf("contract_version", "schema_version", "binding_contract_version", "protocol_version",
        "state", "supported_event_schema_versions", "user_id", "device_id", "server_profile_id", "journal_epoch", "platform",
        "server_instance_id", "identity_epoch", "identity_thumbprint_sha256", "api_origin", "stream_origin")

    /** Lexical parsing precedes JCS: decoded duplicate keys and Unicode are checked at every depth. */
    private class StrictJson(private val raw: String) {
        private var cursor: Int = 0
        fun parse(): JsonElement {
            val result = value(1)
            whitespace()
            ensure(cursor == raw.length, Failure.INVALID_JSON)
            return result
        }
        private fun whitespace() { while (cursor < raw.length && raw[cursor] in " \t\r\n") cursor++ }
        private fun take(character: Char): Boolean {
            whitespace()
            return (cursor < raw.length && raw[cursor] == character).also { if (it) cursor++ }
        }
        private fun value(depth: Int): JsonElement {
            whitespace()
            ensure(cursor < raw.length, Failure.INVALID_JSON)
            // Depth counts containers, root=1. Scalar children add no container depth.
            ensure(raw[cursor] !in "{[" || depth <= 4, Failure.INVALID_JSON)
            return when (raw[cursor]) {
                '{' -> {
                    cursor++
                    val members = linkedMapOf<String, JsonElement>()
                    if (!take('}')) {
                        do {
                            whitespace()
                            val key = quoted()
                            ensure(key !in members && take(':'), Failure.INVALID_JSON)
                            members[key] = value(depth + 1)
                        } while (take(','))
                        ensure(take('}'), Failure.INVALID_JSON)
                    }
                    JsonObject(members)
                }
                '[' -> {
                    cursor++
                    val items = mutableListOf<JsonElement>()
                    if (!take(']')) {
                        do { items.add(value(depth + 1)) } while (take(','))
                        ensure(take(']'), Failure.INVALID_JSON)
                    }
                    JsonArray(items)
                }
                '"' -> JsonPrimitive(quoted())
                else -> {
                    val start = cursor
                    while (cursor < raw.length && raw[cursor] !in " \t\r\n,]}:") cursor++
                    val token = raw.substring(start, cursor)
                    ensure(token in setOf("true", "false", "null") ||
                        Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?").matches(token), Failure.INVALID_JSON)
                    Json.parseToJsonElement(token)
                }
            }
        }
        private fun quoted(): String {
            ensure(cursor < raw.length && raw[cursor] == '"', Failure.INVALID_JSON)
            val start = cursor++
            var closed = false
            while (cursor < raw.length) {
                val character = raw[cursor++]
                if (character == '"') { closed = true; break }
                ensure(character.code >= 32, Failure.INVALID_JSON)
                if (character == '\\') {
                    ensure(cursor < raw.length, Failure.INVALID_JSON)
                    val escape = raw[cursor++]
                    ensure(escape in "\"\\/bfnrtu", Failure.INVALID_JSON)
                    if (escape == 'u') {
                        ensure(cursor + 4 <= raw.length && raw.substring(cursor, cursor + 4)
                            .all { it in "0123456789abcdefABCDEF" }, Failure.INVALID_JSON)
                        cursor += 4
                    }
                }
            }
            ensure(closed, Failure.INVALID_JSON)
            val decoded = (Json.parseToJsonElement(raw.substring(start, cursor)) as JsonPrimitive).content
            var index = 0
            while (index < decoded.length) {
                val character = decoded[index++]
                if (character.isHighSurrogate()) {
                    ensure(index < decoded.length && decoded[index++].isLowSurrogate(), Failure.INVALID_JSON)
                } else ensure(!character.isLowSurrogate(), Failure.INVALID_JSON)
            }
            return decoded
        }
    }
}
