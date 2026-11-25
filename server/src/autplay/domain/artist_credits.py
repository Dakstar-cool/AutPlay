"""Display the performer credited on the release, preserving separate identities and guests."""

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_GUEST_END = re.compile(r"(?:^|\s)(?:feat\.?|ft\.?|featuring|with|и|&)$", re.I)
_JOIN = r"(?:\s*[,;/&+|\u2013—-]\s*|\s+)"
_BRACKETED_GUEST = re.compile(r"(.+?)(\s*\(\s*(?:feat\.?|ft\.?|featuring)\s+)([^()]+)(\s*\))", re.I)
_GUEST_JOIN = re.compile(r"(\s+(?:feat\.?|ft\.?|featuring)\s+)", re.I)


class StageCredit:
    def __init__(self, stage: str, aliases: list[str]) -> None:
        self.stage = stage
        public = re.escape(stage)
        other = "(?:" + "|".join(map(re.escape, aliases)) + ")"
        single = f"(?:{public}|{other})"
        pair = (
            rf"(?:{public}(?:{_JOIN}{other}|\s*\(\s*{other}\s*\))"
            rf"|{other}(?:{_JOIN}{public}|\s*\(\s*{public}\s*\)))"
        )
        self.key = re.compile(rf"(?:{pair}|{single})", re.I)
        self.tail = re.compile(rf"(?:\s+[\u2013—|-]\s*(?:{pair}|{single})|\s+{pair})$", re.I)

    def trim_title(self, title: str) -> str:
        match = self.tail.search(title)
        if not match or match.start() == 0:
            return title
        rest = title[: match.start()].rstrip()
        return title if _GUEST_END.search(rest) else rest


def _read_credits() -> tuple[StageCredit, ...]:
    data = Path(__file__).with_name("labels") / "autplay-artist-credits.tsv"
    credits = []
    for line in data.read_text(encoding="utf-8").splitlines():
        stage, *aliases = line.split("\t")
        if not stage or not aliases or not all(aliases):
            raise ValueError("Incomplete preferred artist credit")
        credits.append(StageCredit(stage, aliases))
    return tuple(credits)


_CREDITS = _read_credits()


def single_stage_credit(artist: str | None) -> StageCredit | None:
    if not artist:
        return None
    return next((credit for credit in _CREDITS if credit.key.fullmatch(artist)), None)


_REAL = re.compile(
    r"(.+?)\s*\(\s*(?:настоящее имя|real name|legal name)\s*[:\u2013—-]?\s+([^()]+)\)", re.I
)
_STAGE = re.compile(
    r"(.+?)\s*\(\s*(?:сценическое имя|псевдоним|stage name)\s*[:\u2013—-]?\s+([^()]+)\)", re.I
)
_COLLABORATION = re.compile(r"[,;]|\s(?:feat\.?|ft\.?|featuring|/|&|\+)\s", re.I)


def declared_credit(label: str) -> StageCredit | None:
    if "(" not in label:
        return None
    for pattern in (_REAL, _STAGE):
        match = pattern.fullmatch(label)
        if match is None:
            continue
        first, second = (value.strip() for value in match.groups())
        stage, other = (first, second) if pattern is _REAL else (second, first)
        if stage and other and not any(_COLLABORATION.search(n) for n in (stage, other)):
            return _verified_rule(stage, (other,))
    return None


def display_credit(artist: str) -> str:
    bracket = _BRACKETED_GUEST.fullmatch(artist)
    if bracket:
        main, marker, guests, close = bracket.groups()
        return display_credit(main) + marker + display_credit(guests) + close
    parts = _GUEST_JOIN.split(artist)
    for index in range(0, len(parts), 2):
        rule = declared_credit(parts[index]) or single_stage_credit(parts[index])
        if rule:
            parts[index] = rule.stage
    return "".join(parts)


@lru_cache(maxsize=512)
def _verified_rule(stage: str, others: tuple[str, ...]) -> StageCredit:
    return StageCredit(stage, list(others))


@dataclass(frozen=True)
class RecordingCredit:
    credited: str
    preferred: str
    single: StageCredit | None

    def matches(self, label: str) -> bool:
        key = " ".join(label.split()).casefold()
        return key in {self.credited.casefold(), self.preferred.casefold()} or bool(
            self.single and self.single.key.fullmatch(label)
        )


def recording_credit(members: object) -> RecordingCredit | None:
    """Only names bound to the same provider/catalog artist ID can describe one performer."""
    if not isinstance(members, (list, tuple)) or not 1 <= len(members) <= 100:
        return None
    names: dict[str, str] = {}
    alternatives: dict[str, set[str]] = {}
    projects: dict[str, str] = {}
    first_labels: dict[str, str] = {}
    joins: dict[str, str] = {}
    credited: list[str] = []
    previous = None
    for member in members:
        if not isinstance(member, dict):
            return None
        identity, name, label, join = (
            member.get(k) for k in ("mbid", "name", "credited_name", "join_phrase")
        )
        if (
            not isinstance(identity, str)
            or not isinstance(name, str)
            or not isinstance(label, str)
            or not all(0 < len(v) <= 1000 for v in (identity, name, label))
        ):
            return None
        if not isinstance(join, str) or len(join) > 1000:
            return None
        if identity in names and names[identity] != name:
            return None
        credited.append(label + join)
        if identity not in names or previous == identity:
            joins[identity] = join
        names[identity] = name
        variants = alternatives.setdefault(identity, set())
        variants.update((label, name))
        first_labels.setdefault(identity, label)
        declared = declared_credit(label)
        rule = single_stage_credit(label)
        if declared:
            project = declared.stage
        elif rule and rule.key.fullmatch(name):
            project = rule.stage
        elif member.get("legal_name") == label:
            project = name
        else:
            project = label
        # A different credited name can be the artist's project on this release.
        # If one ID appears twice under both names, prefer its catalog name.
        if identity not in projects or project == name:
            projects[identity] = project
        previous = identity
    # Two distinct artist IDs may share a spelling or resemble a curated pair.
    # Keep the release attribution wherever a substitution would merge their labels.
    counts = Counter(project.casefold() for project in projects.values())
    for identity, project in projects.items():
        if counts[project.casefold()] > 1:
            projects[identity] = first_labels[identity]
    last = next(reversed(names))
    preferred = "".join(
        project + (joins[key] if key != last else "") for key, project in projects.items()
    )
    stage = None
    if len(names) == 1:
        project = projects[last]
        aliases = tuple(
            sorted(label for label in alternatives[last] if label.casefold() != project.casefold())
        )
        stage = _verified_rule(project, aliases) if aliases else single_stage_credit(project)
    return RecordingCredit(" ".join("".join(credited).split()), " ".join(preferred.split()), stage)


def trim_repeated_credit(title: str, credit: str) -> str:
    """A space or an underscore can be part of the title itself."""
    start = len(title) - len(credit)
    if start <= 1 or title[start:].casefold() != credit.casefold():
        return title
    separator = re.search(r"\s+[-\u2013—|]\s*$", title[:start])
    if separator is None:
        return title
    rest = title[: separator.start()].rstrip()
    return rest if rest and not _GUEST_END.search(rest) else title
