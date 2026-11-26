package app.autplay.application.search

import app.autplay.application.artist.compactSpelling
import app.autplay.application.artist.musicSearchKey
import app.autplay.application.artist.stripSourceSite

/** Builds a bounded FTS5 MATCH expression that is always passed as a bound DAO parameter. */
class SafeFtsQueryBuilder(
    private val maxInputCharacters: Int = 256,
    private val maxTokens: Int = 8,
    private val maxTokenCharacters: Int = 48,
) {
    init {
        require(maxInputCharacters > 0)
        require(maxTokens > 0)
        require(maxTokenCharacters > 0)
    }

    /** Returns null for an empty/non-searchable query so callers never trigger an unbounded scan. */
    fun build(rawInput: String): String? {
        val tokens = tokenize(app.autplay.application.artist.artistSearchKey(rawInput.take(maxInputCharacters)))
        if (tokens.isEmpty()) {
            return null
        }
        return tokens.joinToString(separator = " AND ") { token ->
            val literal = "\"$token\""
            if (token.length >= MIN_PREFIX_TOKEN_LENGTH) "$literal*" else literal
        }
    }

    /** Fixed columns restrict every token; callers still bind the complete MATCH expression. */
    fun build(rawInput: String, kind: LibrarySearchKind): String? {
        val bounded = rawInput.take(maxInputCharacters)
        val query = if (kind == LibrarySearchKind.Album) bounded else stripSourceSite(bounded).ifEmpty { bounded }
        val expression = build(query) ?: return null
        val original = "${kind.ftsColumns}:($expression)"
        val alternatives = mutableListOf(original)
        if (kind == LibrarySearchKind.Track || kind == LibrarySearchKind.All) {
            val joined = compactSpelling(musicSearchKey(query))
            if (joined != null && joined.length <= maxTokenCharacters) {
                alternatives.add("title:(\"$joined\"*)")
            }
        }
        if (kind == LibrarySearchKind.Artist || kind == LibrarySearchKind.All) {
            val stage = app.autplay.application.artist.StageCredits.single(query.trim())?.stage
            val alternative = stage?.let { build(it) }
            if (alternative != null && alternative != expression) alternatives.add("artist:($alternative)")
        }
        return if (alternatives.size == 1) original else alternatives.joinToString(" OR ", "(", ")")
    }

    private fun tokenize(input: String): List<String> {
        val tokens = mutableListOf<String>()
        val current = StringBuilder()

        fun flush() {
            if (current.isNotEmpty() && tokens.size < maxTokens) {
                tokens += current.toString()
                current.clear()
            }
        }

        for (character in input) {
            if (character.isLetterOrDigit()) {
                if (current.length < maxTokenCharacters) {
                    current.append(character.lowercaseChar())
                }
            } else {
                flush()
            }
            if (tokens.size == maxTokens) {
                break
            }
        }
        flush()
        return tokens
    }

    private companion object {
        const val MIN_PREFIX_TOKEN_LENGTH = 2
    }
}
