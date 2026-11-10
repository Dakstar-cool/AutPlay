package app.autplay.application.desktop

/** A replaceable local text projection, never artist/album or authenticated identity. */
data class DesktopTextSearchMatches(
    /** Exact supplied query, allowing the caller to reject a mismatched adapter response. */
    val query: String,
    /** False only for blank input: the ordinary unsearched library remains available. */
    val searched: Boolean,
    /** Complete matching IDs, ranked within each kind; never a silently truncated first page. */
    val trackIds: List<String> = emptyList(),
    val playlistIds: List<String> = emptyList(),
    /** Stable safe code only; a failed search has no partial IDs and is independent of browsing. */
    val errorCode: String? = null,
)

/** One retained process owner; callers fence results by their exact snapshot/query revision. */
interface DesktopTextSearchPort {
    /** Reads this display snapshot only, never media/source capabilities. Preserve cancellation. */
    suspend fun search(snapshot: DesktopLibrarySnapshot, query: String): DesktopTextSearchMatches
    /** Thread-safe, idempotent admission barrier; no wait for an in-flight native step. */
    fun retire(): Unit
    /** Retires and joins admitted work before releasing derived resources on IO, even if cancelled. */
    suspend fun close(): Unit
}

/** Same token/prefix policy as Android SafeFtsQueryBuilder, with no user SQL/operator syntax. */
fun desktopTextSearchExpression(rawInput: String): String? {
    val tokens = ArrayList<String>(8)
    val current = StringBuilder()
    fun flush(): Unit {
        if (current.isNotEmpty() && tokens.size < 8) {
            tokens.add(current.toString())
            current.clear()
        }
    }
    for (character in rawInput.take(256)) {
        if (character.isLetterOrDigit()) {
            if (current.length < 48) current.append(character.lowercaseChar())
        } else flush()
        if (tokens.size == 8) break
    }
    flush()
    if (tokens.isEmpty()) return null
    return tokens.joinToString(" AND ") { token ->
        val literal = "\"$token\""
        if (token.length >= 2) "$literal*" else literal
    }
}
