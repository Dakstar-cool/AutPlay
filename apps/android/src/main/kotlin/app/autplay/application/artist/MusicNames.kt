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

internal fun musicDisplayCredit(raw: String): String = StageCredits.display(musicDisplayTitle(raw).replace(bandInCredit, "25/17"))

internal fun artistSearchKey(raw: String): String = musicSearchKey(raw).replace(bandInCredit, "25/17")

// Only parse unstructured credits. A slash can belong to an artist's name (e.g. AC/DC).
internal fun displayArtists(credit: String): List<String> =
    musicDisplayCredit(Normalizer.normalize(credit, Normalizer.Form.NFKC)).split(separators).flatMap { part ->
        val name = part.trim().replace(bandInCredit, "25/17")
        val pair = adjacentPartner.matchEntire(name)
        if (pair == null) listOf(name) else pair.groupValues.drop(1).filter { it.isNotEmpty() }
    }.map(String::trim).filter(String::isNotEmpty).distinctBy(::musicSearchKey)

internal data class TrackNames(val title: String?, val artist: String?)

private val audioExtension = Regex("""\.(?:mp3|flac|m4a|aac|ogg|oga|opus|wav|aif|aiff|alac|wma|aifc)$""", RegexOption.IGNORE_CASE)
private val possibleDecoration = Regex("""[_@|–—\[\]()"”»-]""")
private val encodedSeparator = Regex("_[-–—]_")
private val channelVideoCredit = Regex(
    """\s+[-–—|]\s*HC WORLDWIDE\s*\(OFFICIAL (?:FULL HD|HD|4K) VERSION HCWW\)\s*$""", RegexOption.IGNORE_CASE)
private val explicitVideoCredit = Regex(
    """([\[(])(uncensored|censored|explicit|clean)\s+official\s+(?:music\s+)?video([\])])""", RegexOption.IGNORE_CASE)
// A bare @handle can be part of a title. Only a known venue or explicit live label identifies a show.
private val venueCredit = Regex("""\s+(?:live\s+@\s*([^@|()\[\]]{1,80})|@\s*(16\s+тонн))$""", RegexOption.IGNORE_CASE)
private val sessionCredit = Regex("""\s+[|–—-]\s*([\p{L}\p{N}][\p{L}\p{N} '&’.-]{0,60}\s+sessions)$""", RegexOption.IGNORE_CASE)
private val titleSeparator = Regex("""\s+[-–—]\s+|_[-–—]_""")
private val snakeWord = Regex("""[\p{L}\p{N}]+(?:[-’'][\p{L}\p{N}]+)*""")
private const val officialNotice = "(?:official (?:music video|audio|video|lyrics? video|visualizer)|mood video|официальный клип|официальное видео)"
private const val noticeDetail = """(?:19|20)\d{2}|remaster(?:ed)?(?: \d{4})?|hd|4k|with lyrics|feat\.? [^\[\]()]+"""
private val groupNotice = Regex(
    """(?:\s+[|–—-]\s*)?([\[(])\s*""" + officialNotice +
        """(?:\s*[-–—/|:,]\s*([^\[\]()]+?)|\s+(""" + noticeDetail + """))?\s*!?\s*([\])])""", RegexOption.IGNORE_CASE)
private val groupNoticeAfterDetail = Regex("""([\[(])\s*([^\[\]()]+?)\s*(?:[/|,]|\s[-–—]\s)\s*""" +
    officialNotice + """\s*([\])])""", RegexOption.IGNORE_CASE)
private val separateNotice = Regex("""(?:\s+[-–—]\s*|\s*\|\s*|(?<=[)\]"”»])\s+)""" + officialNotice +
    """(?:\s+((?:19|20)\d{2}))?(?:\s+(?:HD|4K))?(?:\s*\|(?=\s*$))?(?=\s*(?:$|[\[(|]|\s[-–—]\s))""", RegexOption.IGNORE_CASE)

private fun retainedNoticeDetail(match: MatchResult): String {
    val groups = match.groupValues
    val open = groups[1]
    val close = groups.last()
    if ((open == "(") != (close == ")")) return match.value
    val detail = groups.subList(2, groups.lastIndex).firstOrNull { it.isNotBlank() }?.trim()
    return if (detail == null || detail.lowercase(Locale.ROOT) in setOf("hd", "4k", "with lyrics")) " "
        else " $open$detail$close "
}

internal fun isAudioFilename(name: String): Boolean = audioExtension.containsMatchIn(name.trim())

private fun unpackWords(label: String): String {
    if ('_' !in label) return label
    val words = label.split('_')
    if (words.size < 2 || words.any { !snakeWord.matches(it) && it.lowercase(Locale.ROOT) !in setOf("feat.", "ft.") }) return label
    if (words.size == 2 && words.any { word ->
        word.length < 2 || !word.all(Char::isLetter) || word.all(Char::isUpperCase)
    }) return label
    return if (words.count { it.any(Char::isLetter) } >= 2) words.joinToString(" ") else label
}

private fun sourceNames(raw: String?, artist: String?, inferArtist: Boolean): TrackNames {
    val name = raw?.let(::musicDisplayTitle)
    var credit = artist?.let(::musicDisplayCredit)
    if (name.isNullOrEmpty()) return TrackNames(name, credit)
    val file = isAudioFilename(name)
    var stage = StageCredits.single(credit) ?: StageCredits.declared(artist)
    if (stage == null && !file && !possibleDecoration.containsMatchIn(name)) return TrackNames(name, credit)
    var stem = if (file) musicDisplayTitle(name.replace(audioExtension, "").replace('_', ' ')).ifEmpty { name } else name
    if (!file && encodedSeparator.containsMatchIn(stem)) {
        stem = stem.split(encodedSeparator).joinToString(" - ", transform = ::unpackWords)
    }
    stem = stem.replace(channelVideoCredit, "").ifEmpty { stem }
    stem = explicitVideoCredit.replace(stem) { it.groupValues[1] + it.groupValues[2] + it.groupValues[3] }
    val groups = groupNoticeAfterDetail.replace(groupNotice.replace(stem, ::retainedNoticeDetail), ::retainedNoticeDetail)
    val withoutNotice = separateNotice.replace(groups) { match ->
        match.groups[1]?.value?.let { " ($it) " } ?: " "
    }
    stem = musicDisplayTitle(withoutNotice).ifEmpty { stem }
    var known = credit?.takeUnless { it.isBlank() || it == "Unknown artist" || it == "<unknown>" }?.let(::artistSearchKey)
    while (true) {
        val separator = titleSeparator.find(stem) ?: break
        val prefix = stem.substring(0, separator.range.first)
        val rest = stem.substring(separator.range.last + 1)
        val matches = known != null && (stage?.matches(prefix) == true || artistSearchKey(StageCredits.display(prefix)) == known || artistSearchKey(StageCredits.display(unpackWords(prefix))) == known)
        val infer = file && known == null && inferArtist && !titleSeparator.containsMatchIn(rest)
        if (prefix.isEmpty() || rest.isEmpty() || !(matches || infer)) break
        if (infer) {
            credit = musicDisplayCredit(prefix)
            stage = StageCredits.single(credit)
            known = artistSearchKey(credit)
        }
        stem = rest
    }
    if (!file && artistSearchKey(stem) != known) stem = unpackWords(stem)
    val cleanTitle = stage?.trimTitle(stem)
        ?: credit?.takeIf { known != null }?.let { trimRepeatedCredit(stem, it) } ?: stem
    val performance = venueCredit.replace(cleanTitle) { " (Live at " + (it.groups[1] ?: it.groups[2])!!.value + ")" }
    return TrackNames(sessionCredit.replace(performance) { " (" + it.groupValues[1] + ")" }, credit)
}

internal fun trackNames(
    rawTitle: String?, artist: String?, title: String? = rawTitle,
    hasTitle: Boolean = false, hasArtist: Boolean = false, cleanSourceTitle: Boolean = false, cleanSourceArtist: Boolean = false,
): TrackNames {
    val fallback = sourceNames(rawTitle, artist, !hasArtist || artist.isNullOrBlank())
    val effectiveTitle = when {
        !hasTitle -> fallback.title
        !cleanSourceTitle || title == null -> title
        title == rawTitle -> fallback.title
        else -> sourceNames(title, artist, false).title
    }
    return TrackNames(effectiveTitle, if (hasArtist && !cleanSourceArtist) artist else fallback.artist)
}
