# ruff: noqa: RUF001 -- Russian version labels and Unicode release punctuation.
"""Content rules shared with the independently packaged local acquisition worker."""

import re
import unicodedata

MAX_TRACK_SECONDS = 20 * 60

_LIVE = re.compile(
    r"\b(?:live\s+(?:at|from|in|on|version|session|recording|concert|performance)\b"
    r"|live\s*@|live\s+(?:19|20)\d{2}\b|recorded\s+live\b|in\s+concert\b|mtv\s+unplugged\b"
    r"|concert\s+(?:version|recording|performance|at|in)\b|лайф[\s-]+версия\b"
    r"|концерт(?:ный|ная|ное|ные|ной|ном|е|а)\b|концертник\b|концерт\s+в\b"
    r"|жив(?:ой звук|ое исполнение|ая запись)\b|живь[её]м\b|лайв(?:е|а)?\b"
    r"|en vivo\b|ao vivo\b|en concert\b)"
    r"|(?:^|[\[(|:]|\s[-–—]\s*)\s*(?:live|unplugged|концерт)"
    r"\s*(?:$|[\])|,:/\-–—]|\d{4}\b|acoustic\b)"
)
_NON_TRACK = re.compile(
    r"\b(?:full[\s-]+(?:movie|film|episode|album|lp|concert|session)|official\s+(?:teaser\s+)?trailer"
    r"|полный\s+(?:фильм|альбом|концерт)|фильм\s+целиком|все\s+серии|весь\s+альбом"
    r"|официальный трейлер|podcast|audiobook|подкаст|аудиокнига|интервью)\b"
    r"|[\[(]\s*(?:плейлист|playlist)\s*[\])]"
)


def track_review_reasons(seconds: float, *labels: object) -> tuple[str, ...]:
    reasons = []
    if not 0 < seconds <= MAX_TRACK_SECONDS:
        reasons.append("duration_outside_track_limit")
    texts = [
        unicodedata.normalize("NFKC", s).casefold().strip() for s in labels if isinstance(s, str)
    ]
    if any(_LIVE.search(s) for s in texts):
        reasons.append("live_performance_label")
    if any(_NON_TRACK.search(s) for s in texts):
        reasons.append("non_track_label")
    return tuple(reasons)
