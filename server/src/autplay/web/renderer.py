"""Strict auto-escaped Jinja rendering and bounded EN/RU formatting for M6."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from typing import Final

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

SUPPORTED_LOCALES: Final = ("en", "ru")
STATIC_ASSET_DIGESTS: Final = {
    "admin-v1.css": "10e85268761bb7635618153b49f823c2b99349dca5d407cb920ffce79b3a2d39"
}
_MONTHS: Final = {
    "en": (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ),
    "ru": (
        "янв.",
        "февр.",
        "мар.",
        "апр.",
        "мая",
        "июня",
        "июля",
        "авг.",
        "сент.",
        "окт.",
        "нояб.",
        "дек.",
    ),
}


class AdminTemplateRenderer:
    """Render only bundled templates with strict variables and HTML autoescaping."""

    def __init__(self) -> None:
        self._environment = Environment(
            loader=PackageLoader("autplay.web", "templates"),
            autoescape=select_autoescape(("html", "xml"), default=True),
            undefined=StrictUndefined,
            enable_async=False,
            auto_reload=False,
        )
        self._catalogs = {locale: _load_catalog(locale) for locale in SUPPORTED_LOCALES}

    def render(
        self,
        template: str,
        *,
        locale: str,
        context: Mapping[str, object] | None = None,
    ) -> str:
        selected = locale if locale in SUPPORTED_LOCALES else "en"
        catalog = self._catalogs[selected]

        def translate(key: str) -> str:
            try:
                return catalog[key]
            except KeyError as error:
                raise ValueError("unknown admin translation key") from error

        values: dict[str, object] = {
            "locale": selected,
            "other_locale": "ru" if selected == "en" else "en",
            "t": translate,
            "format_datetime": lambda value: format_datetime(value, selected),
            "format_count": lambda value: format_count(value, selected),
            "format_bytes": lambda value: format_bytes(value, selected),
        }
        if context is not None:
            values.update(context)
        return self._environment.get_template(template).render(values)


def resolve_locale(explicit: str | None, accept_language: str | None) -> str:
    """Select only the two supported locales without persisting request headers."""

    if explicit in SUPPORTED_LOCALES:
        return explicit
    if accept_language:
        for item in accept_language.split(",")[:8]:
            language = item.split(";", 1)[0].strip().lower().split("-", 1)[0]
            if language in SUPPORTED_LOCALES:
                return language
    return "en"


def format_datetime(value: datetime | None, locale: str) -> str:
    if value is None:
        return "—"
    normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    normalized = normalized.astimezone(UTC)
    month = _MONTHS[locale][normalized.month - 1]
    if locale == "ru":
        return f"{normalized.day} {month} {normalized.year}, {normalized:%H:%M} UTC"
    return f"{month} {normalized.day}, {normalized.year}, {normalized:%H:%M} UTC"


def format_count(value: int, locale: str) -> str:
    separator = "\N{NO-BREAK SPACE}" if locale == "ru" else ","
    return f"{value:,}".replace(",", separator)


def format_bytes(value: int | None, locale: str) -> str:
    if value is None:
        return "—"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(max(0, value))
    unit = units[0]
    for candidate in units:
        unit = candidate
        if amount < 1024 or candidate == units[-1]:
            break
        amount /= 1024
    rendered = f"{amount:.1f}" if unit != "B" else str(int(amount))
    if locale == "ru":
        rendered = rendered.replace(".", ",")
    return f"{rendered} {unit}"


def read_static_asset(name: str) -> tuple[bytes, str]:
    """Read one allowlisted bundled asset and verify its committed digest."""

    try:
        expected = STATIC_ASSET_DIGESTS[name]
    except KeyError as error:
        raise ValueError("unknown admin static asset") from error
    payload = files("autplay.web").joinpath("static", name).read_bytes()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise RuntimeError("admin static asset integrity check failed")
    return payload, expected


def _load_catalog(locale: str) -> dict[str, str]:
    resource = files("autplay.web").joinpath("i18n", f"{locale}.json")
    document = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in document.items()
    ):
        raise RuntimeError("invalid admin translation catalog")
    return dict(document)


__all__ = (
    "STATIC_ASSET_DIGESTS",
    "SUPPORTED_LOCALES",
    "AdminTemplateRenderer",
    "format_bytes",
    "format_count",
    "format_datetime",
    "read_static_asset",
    "resolve_locale",
)
