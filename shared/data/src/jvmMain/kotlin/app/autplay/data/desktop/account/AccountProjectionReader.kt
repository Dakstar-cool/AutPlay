package app.autplay.data.desktop.account

import androidx.sqlite.SQLiteStatement
import app.autplay.application.desktop.account.*

/** Bounded typed reads on the same serial connection; no catalog/media/portable availability claim. */
internal class AccountProjectionReader(private val sql: AccountPageSql, private val scope: AccountUuid) {
    fun read(kind: AccountProjectionKind, after: AccountUuid?, limit: Int): List<AccountDomainProjection> {
        val type = when (kind) {
            AccountProjectionKind.TRACK -> "USER_TRACK_REF"
            AccountProjectionKind.LIBRARY -> "LIBRARY_ENTRY"
            AccountProjectionKind.PREFERENCE -> "USER_TRACK_PREFERENCE"
            AccountProjectionKind.PLAYLIST -> "PLAYLIST"
            AccountProjectionKind.PLAYLIST_ENTRY -> "PLAYLIST_ENTRY"
            AccountProjectionKind.LISTENING -> "LISTENING_EVENT"
        }
        val headers = sql.query("""SELECT local_id,server_id,observed_version,state,effective_deleted,pending,snapshot_confirmed
            FROM j_account_projection_visibility WHERE scope_id=? AND aggregate_type=? AND (? IS NULL OR local_id>?) ORDER BY local_id LIMIT ?""",
            scope, type, after, after, limit) {
            val server = AccountUuid(it.getText(1))
            val canonical = resolve(type, server)
            val canonicalLocal = sql.text("SELECT local_id FROM j_projection_state WHERE scope_id=? AND aggregate_type=? AND server_id=?",
                scope, type, canonical)?.let(::AccountUuid)
            AccountProjectionHeader(AccountUuid(it.getText(0)), server, canonicalLocal, canonical,
                long(it, 2), it.getText(3), it.getLong(4) == 1L, it.getLong(5) == 1L, if (it.isNull(6)) null else it.getLong(6) == 1L)
        }
        return headers.map { h -> when (kind) {
            AccountProjectionKind.TRACK -> sql.query("""SELECT t.title,t.artist,d.title_is_null,d.artist_is_null,
                d.album,d.duration_ms,d.recording_id,d.resolution_status FROM j_track_ref t LEFT JOIN j_track_detail d
                ON d.scope_id=t.scope_id AND d.track_ref_id=t.track_ref_id WHERE t.scope_id=? AND t.track_ref_id=?""", scope, h.localId) {
                AccountRemoteTrack(h, if (!it.isNull(2) && it.getLong(2) == 1L) null else it.getText(0),
                    if (!it.isNull(3) && it.getLong(3) == 1L) null else it.getText(1), sql.nullable(it, 4), long(it, 5),
                    sql.nullable(it, 6)?.let(::AccountUuid), sql.nullable(it, 7))
            }.single()
            AccountProjectionKind.LIBRARY -> sql.query("""SELECT l.track_ref_id,d.source,d.availability_status
                FROM (SELECT * FROM j_library_entry UNION ALL SELECT * FROM j_library_relation) l
                LEFT JOIN j_library_detail d ON d.scope_id=l.scope_id AND d.entry_id=l.entry_id
                WHERE l.scope_id=? AND l.entry_id=?""", scope, h.localId) {
                AccountRemoteLibraryEntry(h, AccountUuid(it.getText(0)), sql.nullable(it, 1), sql.nullable(it, 2))
            }.single()
            AccountProjectionKind.PREFERENCE -> sql.query("SELECT track_ref_id,preference,excluded_from_taste FROM j_preference WHERE scope_id=? AND preference_id=?", scope, h.localId) {
                AccountRemotePreference(h, AccountUuid(it.getText(0)), it.getText(1), it.getLong(2) == 1L)
            }.single()
            AccountProjectionKind.PLAYLIST -> sql.query("SELECT name,description,visibility,playlist_type FROM j_playlist WHERE scope_id=? AND playlist_id=?", scope, h.localId) {
                AccountRemotePlaylist(h, sql.nullable(it, 0), sql.nullable(it, 1), sql.nullable(it, 2), sql.nullable(it, 3))
            }.single()
            AccountProjectionKind.PLAYLIST_ENTRY -> sql.query("SELECT playlist_id,track_ref_id,position_key FROM j_playlist_entry WHERE scope_id=? AND entry_id=?", scope, h.localId) {
                AccountRemotePlaylistEntry(h, AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), it.getText(2))
            }.single()
            AccountProjectionKind.LISTENING -> sql.query("""SELECT track_ref_id,source_local_track_id,recording_id,played_ms,duration_ms,
                completion_ratio,event_origin,context,explicit_feedback,excluded_from_taste,source_created_at
                FROM j_listening WHERE scope_id=? AND listening_id=?""", scope, h.localId) {
                AccountRemoteListening(h, AccountUuid(it.getText(0)), AccountUuid(it.getText(1)), sql.nullable(it, 2)?.let(::AccountUuid),
                    it.getLong(3), long(it, 4), if (it.isNull(5)) null else it.getDouble(5), it.getText(6), it.getText(7),
                    it.getText(8), it.getLong(9) == 1L, it.getText(10).takeIf(String::isNotEmpty))
            }.single()
        } }
    }
    private fun long(statement: SQLiteStatement, column: Int): Long? = if (statement.isNull(column)) null else statement.getLong(column)
    private fun resolve(type: String, start: AccountUuid): AccountUuid {
        var cursor = start
        val visited = HashSet<AccountUuid>()
        repeat(100) {
            if (!visited.add(cursor)) fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
            val next = sql.text("SELECT canonical_server_id FROM j_redirect WHERE scope_id=? AND aggregate_type=? AND alias_server_id=?", scope, type, cursor)
                ?: return cursor
            cursor = AccountUuid(next)
        }
        fail(AccountFailure.ACCOUNT_RECONCILIATION_REQUIRED)
    }
}
