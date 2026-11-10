package app.autplay.desktop.sync

import app.autplay.application.desktop.account.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.*

public data class DesktopStatisticsGenre(public val name: String, public val listenedMs: Long)
public data class DesktopStatisticsTrack(public val identity: String, public val title: String?, public val artist: String?,
    public val sessions: Long, public val listenedMs: Long)
public data class DesktopStatisticsArtist(public val name: String?, public val sessions: Long, public val listenedMs: Long)
public data class DesktopServerStatistics(public val throughMs: Long, public val listenedMs: Long,
    public val genres: List<DesktopStatisticsGenre>, public val tracks: List<DesktopStatisticsTrack>,
    public val artists: List<DesktopStatisticsArtist>)
public enum class DesktopStatisticsSource { UNAVAILABLE, PORTABLE_ACCOUNT, CURRENT_SERVER }
/** Portable deduplicated totals and a current-server sample are independent, never added together. */
public data class DesktopAccountStatisticsState(public val source: DesktopStatisticsSource = DesktopStatisticsSource.UNAVAILABLE,
    public val portable: AccountPortableStatistics? = null, public val currentServer: DesktopServerStatistics? = null,
    public val failure: DesktopSyncFailure? = null) {
    public val available: Boolean get() = source != DesktopStatisticsSource.UNAVAILABLE
    public val listenedMs: Long? get() = when (source) {
        DesktopStatisticsSource.PORTABLE_ACCOUNT -> portable?.takeIf { it.available && it.complete }?.playedMs
        DesktopStatisticsSource.CURRENT_SERVER -> currentServer?.listenedMs
        DesktopStatisticsSource.UNAVAILABLE -> null
    }
    public val listens: Long? get() = if (source == DesktopStatisticsSource.PORTABLE_ACCOUNT) portable?.listens else null
}

public class DesktopAccountStatisticsRuntime(private val store: DesktopAccountStore, private val sync: DesktopSyncTransport) {
    private val mutable = MutableStateFlow(DesktopAccountStatisticsState())
    public val state: StateFlow<DesktopAccountStatisticsState> = mutable.asStateFlow()
    public suspend fun refresh(lease: AccountLease): DesktopAccountStatisticsState {
        val local = try { store.portableData?.statistics(lease) } catch (failure: AccountStoreException) {
            if (failure.failure in setOf(AccountFailure.ACCOUNT_IDENTITY_CONFLICT, AccountFailure.ACCOUNT_UNSUPPORTED_OPERATION)) null
            else return DesktopAccountStatisticsState(failure = DesktopSyncFailure.UNAVAILABLE).also { mutable.value = it }
        } catch (_: Exception) {
            return DesktopAccountStatisticsState(failure = DesktopSyncFailure.UNAVAILABLE).also { mutable.value = it }
        }
        var remote: DesktopServerStatistics? = null
        var error: DesktopSyncFailure? = null
        try {
            val capture = sync.session.capture(lease)
            sync.session.request(lease, DomainRoute.Statistics()).use { response ->
                sync.requireSuccess(response)
                val root = DomainJson.document(response.bytes, 65_536)
                require(root.keys == setOf("schema_version", "account_id", "through_ms", "listened_ms", "top_genres", "top_tracks", "top_artists") &&
                    DomainJson.number(root, "schema_version") == 1L && DomainJson.text(root, "account_id", 36) == capture.owner.account)
                fun rows(name: String): List<JsonObject> = (root[name] as? JsonArray
                    ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE)).also { require(it.size <= 5) }
                    .map { it as? JsonObject ?: throw DesktopSyncException(DesktopSyncFailure.INVALID_RESPONSE) }
                fun nullable(row: JsonObject, name: String): String? = if (row[name] == JsonNull) null else DomainJson.text(row, name)
                remote = DesktopServerStatistics(DomainJson.number(root, "through_ms"), DomainJson.number(root, "listened_ms"),
                    rows("top_genres").map { row ->
                        require(row.keys == setOf("genre", "listened_ms"))
                        DesktopStatisticsGenre(DomainJson.text(row, "genre", 100), DomainJson.number(row, "listened_ms"))
                    }, rows("top_tracks").map { row ->
                        require(row.keys == setOf("identity_key", "title", "artist_name", "play_session_count", "listened_ms"))
                        DesktopStatisticsTrack(DomainJson.text(row, "identity_key", 80), nullable(row, "title"), nullable(row, "artist_name"),
                            DomainJson.number(row, "play_session_count"), DomainJson.number(row, "listened_ms"))
                    }, rows("top_artists").map { row ->
                        require(row.keys == setOf("artist_name", "play_session_count", "listened_ms"))
                        DesktopStatisticsArtist(nullable(row, "artist_name"), DomainJson.number(row, "play_session_count"), DomainJson.number(row, "listened_ms"))
                    })
                sync.session.revalidate(capture)
            }
        } catch (failure: DesktopSyncException) { remote = null; error = failure.failure }
        catch (_: Exception) { remote = null; error = DesktopSyncFailure.UNAVAILABLE }
        val source = when {
            local?.available == true && local.complete && local.playedMs != null && local.listens != null -> DesktopStatisticsSource.PORTABLE_ACCOUNT
            remote != null -> DesktopStatisticsSource.CURRENT_SERVER
            else -> DesktopStatisticsSource.UNAVAILABLE
        }
        return DesktopAccountStatisticsState(source, local, remote, error).also { mutable.value = it }
    }
    public fun invalidate() { mutable.value = DesktopAccountStatisticsState() }
}
