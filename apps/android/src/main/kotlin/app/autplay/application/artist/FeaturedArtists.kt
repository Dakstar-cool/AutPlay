package app.autplay.application.artist

private const val featureMarker = """(?:feat(?:uring)?|ft)\.?\s+(?!of\b)"""
private const val guestName = """[^()\[\]|]+?"""
private const val featureSeparator = """(?:\s*\|\s*|\s+\+\s*|\s+[-–—]\s+)"""
private const val featureDetail = """(?:(?:19|20)\d\d|prod\.\s+[^()\[\]|]+?|(?:[^()\[\]|]+?\s+)?(?:cover|remix))"""
private val detailLabel = Regex(featureDetail, RegexOption.IGNORE_CASE)
private val mixedFeature = Regex("""\(($guestName)[,;]?\s+$featureMarker($guestName)\)""", RegexOption.IGNORE_CASE)
private val wrappedFeature = Regex("""\[\s*($guestName)\s+$featureMarker($guestName)\s*\]""", RegexOption.IGNORE_CASE)
private val feature = Regex(
    """(?:$featureSeparator?\s*([(\[])\s*$featureMarker($guestName)\s*([)\]]))|(?:(?:$featureSeparator|\s+)$featureMarker($guestName)(?=\s*(?:$|[(\[|]|\s#|\s[-–—]\s)))""",
    RegexOption.IGNORE_CASE)
private val feet = Regex("""^\s+ft\s+(?:under|tall|high|wide|long|deep|apart)\b""", RegexOption.IGNORE_CASE)
private val guestJoin = Regex("""\s*(?:[,;]|\s(?:feat(?:uring)?\.?|ft\.?|&|×|x)\s)\s*""", RegexOption.IGNORE_CASE)
private val guestOpening = Regex("""\s*[(\[](?=feat|ft)""", RegexOption.IGNORE_CASE)
private val existingFeature = Regex("""\b(?:feat(?:uring)?|ft)\.?\s""", RegexOption.IGNORE_CASE)
private val encodedSpaces = Regex("""(?:(?:&#x0*20;|&#0*32;|&nbsp;)\s*)+$""", RegexOption.IGNORE_CASE)

internal fun TrackNames.withFeaturedArtists(allowArtistChange: Boolean = true): TrackNames {
    val original = title ?: return this
    if (!original.contains('f', ignoreCase = true)) return this
    val credit = artist?.takeUnless { it.isBlank() || musicSearchKey(it) in setOf("<unknown>", "unknown artist", "неизвестный исполнитель") } ?: return this
    val known = guestJoin.split(credit.replace(guestOpening, " ")).map { musicSearchKey(it.trim(' ', '(', ')', '[', ']')) }.toMutableSet()
    val wrappedTitle = wrappedFeature.matchEntire(original)?.groupValues?.get(1)?.trim()
    val name = if (wrappedTitle != null && musicSearchKey(wrappedTitle) !in known && !detailLabel.matches(wrappedTitle)) {
        original.substring(1, original.lastIndex).trim()
    } else mixedFeature.replace(original) { match ->
        val (detail, guest) = match.destructured
        val primary = existingFeature.find(credit)?.let { credit.take(it.range.first).trimEnd(' ', '(', '[') } ?: credit
        when {
            detail.equals(primary, ignoreCase = true) -> "(feat. $guest)"
            detailLabel.matches(detail) -> "($detail) (feat. $guest)"
            else -> match.value
        }
    }
    val additions = mutableListOf<String>()
    val result = StringBuilder()
    var end = 0
    for (match in feature.findAll(name)) {
        val before = name.take(match.range.first).trimEnd()
        if (before.isEmpty() || before.count { it == '(' } != before.count { it == ')' } ||
            before.count { it == '[' } != before.count { it == ']' }) continue
        val (opening, bracketed, close, bare) = match.destructured
        if (opening.isNotEmpty() && (opening == "(") != (close == ")")) continue
        if (before.last().isDigit() && feet.containsMatchIn(match.value)) continue
        val guest = (bracketed.ifEmpty { bare }).trim().replace(encodedSpaces, "").trim()
        if (guest.none(Char::isLetterOrDigit)) continue
        val names = guestJoin.split(guest).filter(String::isNotEmpty)
        val fresh = names.filter { known.add(musicSearchKey(it)) }
        if (fresh.isNotEmpty()) additions += if (fresh == names) guest else fresh.joinToString(", ")
        result.append(name, end, match.range.first).append(' ')
        end = match.range.last + 1
    }
    if (end == 0 || (additions.isNotEmpty() && !allowArtistChange)) return this
    val cleaned = musicDisplayTitle(result.append(name, end, name.length).toString())
    val join = if (existingFeature.containsMatchIn(credit)) ", " else " feat. "
    val artists = if (additions.isEmpty()) credit else credit + join + additions.joinToString(", ")
    return if (cleaned.isEmpty() || artists.length > 500) this else TrackNames(cleaned, artists)
}
