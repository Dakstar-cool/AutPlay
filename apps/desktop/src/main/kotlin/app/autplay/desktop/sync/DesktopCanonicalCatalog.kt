package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import java.time.LocalDate
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.json.*
import kotlin.coroutines.coroutineContext

internal enum class CanonicalQueryKind(val wire: String, val resource: String, val suffix: String) {
    RecordingDetail("recording_detail", "recordings", "detail"),
    RecordingReleases("recording_releases", "recordings", "releases"),
    ReleaseDetail("release_detail", "releases", "detail"),
    ReleaseTracks("release_tracks", "releases", "tracks");
    val detail: Boolean get() = suffix == "detail"
}

public data class DesktopCanonicalCredit(public val id: AccountUuid, public val revision: Long,
    public val displayName: String, public val members: List<AccountCatalogCreditMember>)
public sealed interface DesktopCanonicalDetail {
    public val id: AccountUuid
    public val revision: Long
    public val title: String
    public val credit: DesktopCanonicalCredit
    public data class Recording(override val id: AccountUuid, override val revision: Long,
        override val title: String, override val credit: DesktopCanonicalCredit,
        public val durationMs: Long?, public val versionText: String?, public val disambiguation: String?,
        public val rawKind: String, public val rawIdentityStatus: String) : DesktopCanonicalDetail
    public data class Release(override val id: AccountUuid, override val revision: Long,
        override val title: String, override val credit: DesktopCanonicalCredit,
        public val date: String?, public val precision: String?, public val country: String?,
        public val rawStatus: String) : DesktopCanonicalDetail
}
public data class DesktopCanonicalPosition(public val id: AccountUuid, public val recording: AccountUuid,
    public val release: AccountUuid, public val medium: AccountUuid, public val mediumPosition: Int,
    public val sequence: Int, public val number: String?, public val title: String,
    public val durationMs: Long?, public val revision: Long, public val releaseTitle: String,
    public val releaseDate: String?, public val datePrecision: String?, public val credit: DesktopCanonicalCredit)

internal data class CanonicalCursor(val release: AccountUuid?, val medium: Int, val sequence: Int,
    val track: AccountUuid) : Comparable<CanonicalCursor> {
    override fun compareTo(other: CanonicalCursor): Int = compareValuesBy(this, other,
        { it.release?.value ?: "" }, { it.medium }, { it.sequence }, { it.track.value })
}

/** Only this process owner issues continuations; UI sees an opaque host target. */
public class DesktopCanonicalContinuation internal constructor(internal val lease: AccountLease,
    internal val generation: Long, internal val kind: CanonicalQueryKind, internal val subject: AccountUuid,
    internal val limit: Int, internal val revision: String, internal val cursor: CanonicalCursor) {
    override fun toString(): String = "DesktopCanonicalContinuation(<redacted>)"
}
public data class DesktopCanonicalPage(public val items: List<DesktopCanonicalPosition>,
    public val next: DesktopCanonicalContinuation?, public val exhausted: Boolean,
    /** Describes only this owner's visible subset, including on its last page. */
    public val ownerVisibleSubset: Boolean = true)

internal class CanonicalQuery(val kind: CanonicalQueryKind, val subject: AccountUuid,
    val limit: Int = 100, val after: DesktopCanonicalContinuation? = null) {
    init {
        require(limit in 1..100 && (!kind.detail || (limit == 100 && after == null)))
        if (after != null) require(after.kind == kind && after.subject == subject && after.limit == limit)
    }
    val path: String get() = "/catalog-view/${kind.resource}/${subject.value}/${kind.suffix}"
    fun parameters(): Map<String, String> = if (kind.detail) emptyMap() else buildMap {
        put("limit", limit.toString())
        after?.let {
            put("view_revision", it.revision)
            put("after_medium_position", it.cursor.medium.toString())
            put("after_sequence_no", it.cursor.sequence.toString())
            put("after_release_track_id", it.cursor.track.value)
            it.cursor.release?.let { release -> put("after_release_id", release.value) }
        }
    }
}

/** Process-only C1 reader over the actual K-backed session and retained data HTTP owner. */
public class DesktopCanonicalCatalog internal constructor(private val sync: DesktopSyncTransport) {
    private val closed = AtomicBoolean(false)
    private val generation = AtomicLong(1)
    public fun invalidate(): Unit { generation.incrementAndGet() }
    public fun retire(): Unit { closed.set(true); invalidate() }
    private fun admit(expected: Long) {
        if (closed.get() || generation.get() != expected) throw DesktopSyncException(DesktopSyncFailure.CONTEXT_CHANGED)
    }

    private suspend fun <T> read(lease: AccountLease, query: CanonicalQuery,
        parse: (JsonObject, Long) -> T): T {
        val owner = generation.get(); admit(owner); coroutineContext.ensureActive()
        query.after?.let { require(it.lease == lease && it.generation == owner) }
        val started = System.nanoTime()
        fun deadline(): Long {
            val left = 10_000 - (System.nanoTime() - started) / 1_000_000
            if (left < 100) throw DesktopSyncException(DesktopSyncFailure.RETRY_LATER)
            return left
        }
        val captured = sync.session.capture(lease)
        deadline(); admit(owner)
        sync.session.request(lease, DomainRoute.CatalogView(query), deadlineMs = deadline()).use { response ->
            sync.requireSuccess(response)
            val root = DomainJson.document(response.bytes, 262_144)
            val common = setOf("schema", "kind", "subject_id", "owner_id", "view_revision")
            require(root.keys == common + if (query.kind.detail) setOf("detail") else
                setOf("items", "scanned_through", "exhausted", "owner_visible_subset"))
            require(DomainJson.number(root, "schema") == 1L && DomainJson.text(root, "kind", 32) == query.kind.wire)
            require(id(root, "subject_id") == query.subject && id(root, "owner_id").value == captured.owner.account)
            val witness = DomainJson.text(root, "view_revision", 64)
            require(witness.length == 64 && witness.all { it in '0'..'9' || it in 'a'..'f' })
            query.after?.let { require(witness == it.revision) }
            val result = parse(root, owner)
            deadline(); sync.session.revalidate(captured); deadline()
            admit(owner); coroutineContext.ensureActive()
            return result
        }
    }

    public suspend fun detail(lease: AccountLease, subject: AccountUuid, recording: Boolean): DesktopCanonicalDetail {
        val kind = if (recording) CanonicalQueryKind.RecordingDetail else CanonicalQueryKind.ReleaseDetail
        return read(lease, CanonicalQuery(kind, subject)) { root, _ ->
            val value = objectValue(root, "detail")
            val shared = setOf("row_version", "title", "credit")
            require(value.keys == shared + if (recording) setOf("recording_id", "duration_ms", "version_text",
                "disambiguation", "recording_kind", "identity_status") else
                setOf("release_id", "release_date", "date_precision", "country_code", "status"))
            val revision = version(value); val title = DomainJson.text(value, "title", 2000)
            val credit = credit(objectValue(value, "credit"))
            if (recording) {
                require(id(value, "recording_id") == subject)
                DesktopCanonicalDetail.Recording(subject, revision, title, credit, nullableNumber(value, "duration_ms"),
                    nullableText(value, "version_text", 262_144), nullableText(value, "disambiguation", 262_144),
                    DomainJson.text(value, "recording_kind", 100), DomainJson.text(value, "identity_status", 100))
            } else {
                require(id(value, "release_id") == subject)
                DesktopCanonicalDetail.Release(subject, revision, title, credit, date(value, "release_date"),
                    nullableText(value, "date_precision", 100), nullableText(value, "country_code", 2), DomainJson.text(value, "status", 100))
            }
        }
    }

    public suspend fun positions(lease: AccountLease, subject: AccountUuid, recording: Boolean,
        after: DesktopCanonicalContinuation? = null, limit: Int = 100): DesktopCanonicalPage {
        val kind = if (recording) CanonicalQueryKind.RecordingReleases else CanonicalQueryKind.ReleaseTracks
        return read(lease, CanonicalQuery(kind, subject, limit, after)) { root, owner ->
            require(boolean(root, "owner_visible_subset"))
            val exhausted = boolean(root, "exhausted")
            val array = root["items"] as? JsonArray ?: invalid()
            require(array.size <= limit)
            val items = array.map { element ->
                val value = element as? JsonObject ?: invalid()
                require(value.keys == setOf("release_track_id", "recording_id", "sequence_no", "number_text", "title",
                    "duration_ms", "row_version", "medium_id", "medium_position", "release_id", "release_title",
                    "release_date", "date_precision", "credit"))
                DesktopCanonicalPosition(id(value, "release_track_id"), id(value, "recording_id"), id(value, "release_id"),
                    id(value, "medium_id"), positiveInt(value, "medium_position"), positiveInt(value, "sequence_no"),
                    nullableText(value, "number_text", 262_144), DomainJson.text(value, "title", 2000), nullableNumber(value, "duration_ms"),
                    version(value), DomainJson.text(value, "release_title", 2000), date(value, "release_date"),
                    nullableText(value, "date_precision", 100), credit(objectValue(value, "credit")))
            }
            fun key(item: DesktopCanonicalPosition): CanonicalCursor = CanonicalCursor(item.release.takeIf { recording },
                item.mediumPosition, item.sequence, item.id)
            require(items.all { if (recording) it.recording == subject else it.release == subject })
            require(items.zipWithNext().all { (a, b) -> key(a) < key(b) })
            val scanned = when (val value = root["scanned_through"]) {
                JsonNull -> null
                is JsonObject -> {
                    require(value.keys == setOf("medium_position", "sequence_no", "release_track_id") +
                        if (recording) setOf("release_id") else emptySet())
                    CanonicalCursor(if (recording) id(value, "release_id") else null,
                        positiveInt(value, "medium_position"), positiveInt(value, "sequence_no"), id(value, "release_track_id"))
                }
                else -> invalid()
            }
            require((exhausted || scanned != null) && (items.isEmpty() || scanned != null))
            if (scanned != null) {
                after?.let { require(scanned > it.cursor && items.all { item -> key(item) > it.cursor }) }
                require(items.all { key(it) <= scanned })
            }
            DesktopCanonicalPage(items, if (exhausted) null else DesktopCanonicalContinuation(lease, owner, kind,
                subject, limit, DomainJson.text(root, "view_revision", 64), requireNotNull(scanned)), exhausted)
        }
    }

    private companion object {
        fun invalid(): Nothing = throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)
        fun objectValue(root: JsonObject, key: String): JsonObject = root[key] as? JsonObject ?: invalid()
        fun id(root: JsonObject, key: String): AccountUuid = AccountUuid(DomainJson.text(root, key, 36))
        fun version(root: JsonObject): Long = DomainJson.number(root, "row_version").also { require(it >= 1) }
        fun positiveInt(root: JsonObject, key: String): Int = DomainJson.number(root, key).also {
            require(it in 1..Int.MAX_VALUE.toLong()) }.toInt()
        fun nullableNumber(root: JsonObject, key: String): Long? = if (root[key] == JsonNull) null else
            DomainJson.number(root, key).also { require(it >= 1) }
        fun nullableText(root: JsonObject, key: String, maximum: Int): String? = if (root[key] == JsonNull) null else DomainJson.text(root, key, maximum)
        fun date(root: JsonObject, key: String): String? = nullableText(root, key, 10)?.also { require(LocalDate.parse(it).toString() == it) }
        fun boolean(root: JsonObject, key: String): Boolean = (root[key] as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull ?: invalid()
        fun credit(value: JsonObject): DesktopCanonicalCredit {
            require(value.keys == setOf("credit_id", "row_version", "display_name", "members"))
            val names = value["members"] as? JsonArray ?: invalid()
            require(names.size <= 1000)
            val members = names.map { element ->
                val row = element as? JsonObject ?: invalid()
                require(row.keys == setOf("position", "artist_id", "credited_name", "join_phrase", "role"))
                val position = DomainJson.number(row, "position").also { require(it <= Int.MAX_VALUE) }.toInt()
                val join = (row["join_phrase"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: invalid()
                require(join.length <= 1000 && join.none { it.code < 32 || it.code == 127 })
                AccountCatalogCreditMember(id(row, "artist_id"), position, DomainJson.text(row, "credited_name", 1000),
                    join, DomainJson.text(row, "role", 100))
            }
            require(members.zipWithNext().all { (a, b) -> a.position < b.position })
            return DesktopCanonicalCredit(id(value, "credit_id"), version(value), DomainJson.text(value, "display_name", 2000), members)
        }
    }
}
