package app.autplay.application.artist

import java.text.Normalizer
import java.util.Locale

private val spaces = Regex("[\\s\\p{Z}]+")
private val bandAlias = Regex("""25\s*(?:[/‐‑–—-]|\s)\s*17""")
private val bandInCredit = Regex("""(?<![\p{L}\p{N}_])25\s*(?:[/‐‑–—-]|\s)\s*17(?![\p{L}\p{N}_])""")
private val separators = Regex("""\s*[,;]\s*|\s+(?:feat\.?|ft\.?|featuring)\s+""", RegexOption.IGNORE_CASE)
private val adjacentPartner = Regex("""^(25/17)\s*/\s*(.+)$|^(.+?)\s*/\s*(25/17)$""")

internal const val ARTIST_25_17_MBID = "3e7108b0-91a7-4599-8313-38594939c8b5"

internal fun musicDisplayTitle(raw: String): String =
    Normalizer.normalize(raw, Normalizer.Form.NFC).replace(spaces, " ").trim()

internal fun musicSearchKey(raw: String): String =
    musicDisplayTitle(Normalizer.normalize(raw, Normalizer.Form.NFKC)).lowercase(Locale.ROOT)
        .replace('’', '\'').replace('‘', '\'').replace('‐', '-').replace('‑', '-')
        .replace('–', '-').replace('—', '-')

internal fun artistDisplayName(raw: String): String {
    val name = musicDisplayTitle(raw)
    return if (bandAlias.matches(name)) "25/17" else name
}

internal fun musicDisplayCredit(raw: String): String = musicDisplayTitle(raw).replace(bandInCredit, "25/17")

internal fun artistSearchKey(raw: String): String = musicSearchKey(raw).replace(bandInCredit, "25/17")

// Only parse unstructured credits. A slash can belong to an artist's name (e.g. AC/DC).
internal fun displayArtists(credit: String): List<String> =
    musicDisplayTitle(Normalizer.normalize(credit, Normalizer.Form.NFKC)).split(separators).flatMap { part ->
        val name = part.trim().replace(bandInCredit, "25/17")
        val pair = adjacentPartner.matchEntire(name)
        if (pair == null) listOf(name) else pair.groupValues.drop(1).filter { it.isNotEmpty() }
    }.map(String::trim).filter(String::isNotEmpty).distinctBy(::musicSearchKey)
