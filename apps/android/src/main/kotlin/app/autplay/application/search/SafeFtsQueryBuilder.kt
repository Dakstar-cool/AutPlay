package app.autplay.application.search

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
        val tokens = tokenize(rawInput.take(maxInputCharacters))
        if (tokens.isEmpty()) {
            return null
        }
        return tokens.joinToString(separator = " AND ") { token ->
            val literal = "\"$token\""
            if (token.length >= MIN_PREFIX_TOKEN_LENGTH) "$literal*" else literal
        }
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
