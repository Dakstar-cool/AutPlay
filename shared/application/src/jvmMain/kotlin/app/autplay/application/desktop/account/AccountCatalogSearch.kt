package app.autplay.application.desktop.account

/** Process-only J display projection. Neither text nor an edition key grants catalog access. */
enum class AccountCatalogSearchKind { ARTIST, ALBUM }

data class AccountCatalogSearchDocument(
    val key: String,
    val artist: AccountCatalogArtist? = null,
    val album: AccountMetadataAlbum? = null,
    val albumAnchor: AccountUuid? = null,
)

/** Exact lease plus connection-local mutation revision, captured in one J read transaction. */
data class AccountCatalogSearchSnapshot(
    val lease: AccountLease,
    val revision: Long,
    val kind: AccountCatalogSearchKind,
    val documents: List<AccountCatalogSearchDocument>,
)

data class AccountCatalogSearchPage(
    val revision: Long,
    val items: List<AccountCatalogSearchDocument>,
    val nextKey: String?,
)

/** Validates the entire query before building quoted MATCH; no dropped tokens or operators. */
fun accountCatalogSearchExpression(input: String): String? {
    if (input.length > 256) throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
    val tokens = ArrayList<String>()
    val word = StringBuilder()
    fun flush() {
        if (word.isNotEmpty()) { tokens.add(word.toString()); word.clear() }
    }
    for (character in input) {
        if (character.isLetterOrDigit()) word.append(character.lowercaseChar()) else flush()
        if (word.length > 48 || tokens.size > 8)
            throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
    }
    flush()
    if (tokens.size > 8 || (input.isNotBlank() && tokens.isEmpty()))
        throw AccountStoreException(AccountFailure.ACCOUNT_INPUT_INVALID)
    return tokens.takeIf { it.isNotEmpty() }?.joinToString(" AND ") {
        if (it.length >= 2) "\"$it\"*" else "\"$it\""
    }
}
