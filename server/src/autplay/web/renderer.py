"""Strict auto-escaped Jinja rendering and bounded EN/RU formatting for M6."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib.resources import files
from typing import Final, Self

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

SUPPORTED_LOCALES: Final = ("en", "ru")
STATIC_ASSET_DIGESTS: Final = {
    "admin-local-time-v1.js": "a9b74968e5ee27714bba7a80ae945f6c828bafd8aca29cf3585874e04cf6faac",
    "vault-disk-v1.css": "077932a634b0f4a4faf2c6ef521ce7d64164035fea0791af4535083c23fd701e",
    "vault-v1.js": "ffd036c58086c095206173342269a616a2911702ef4146ebbe294752c067b7f3",
    "vault-v1.css": "4845927765429656a9d9af24a5085b471cd93035ddbd04c2ca09cfd7ff72266b",
    "dashboard-health-v1.js": "0301121f840c88f3d689377b7bb7df8e2f6ad79261668fd7a5a74bf53b95fc9e",
    "pending-devices-v1.js": "90452832f3611ee128c4c1d2cb27ba78813cf9cd0e318d3a308e5aa576eab405",
    "server-connection-v1.js": "49c106551e5f5cbafbbf070e21a28953d60606203b0bf0240e273b852cffd7f7",
    "qrcodegen-v1.js": "2511bc17f40a3c41d4a0578995db956b38997334d3d20113a5d4dc5c49c69480",
    "admin-forms-v1.js": "bc7f3c7cb164dd0b401f757842eac271c445c8a0a60cf3de58da8c16f782f048",
    "admin-v1.css": "b3c13018b1db8ec4c083b6f708ce473b1bf18aec620f73df81f7cbfc27d6b9ec",
    "admin-v2.css": "f5e8e720d9d1313a10bc857e8a943f9ddc029703bda9c6c066bcd7047e8c68b9",
    "passkeys-v1.js": "0e680bd5de574d07a0f728104ec11a962fdef5c0fc81e0849cf1ba5ed4acc2e5",
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


class LocalTimeText(str):
    """UTC fallback text carrying its instant for browser-local presentation."""

    instant: datetime

    def __new__(cls, value: str, instant: datetime) -> Self:
        result = super().__new__(cls, value)
        result.instant = instant
        return result


def _finalize(value: object) -> object:
    if isinstance(value, LocalTimeText):
        return Markup('<time datetime="{}" data-local-time>{}</time>').format(
            value.instant.isoformat(), str(value)
        )
    return value


class AdminTemplateRenderer:
    """Render only bundled templates with strict variables and HTML autoescaping."""

    def __init__(self) -> None:
        self._environment = Environment(
            loader=PackageLoader("autplay.web", "templates"),
            autoescape=select_autoescape(("html", "xml"), default=True),
            undefined=StrictUndefined,
            enable_async=False,
            auto_reload=False,
            finalize=_finalize,
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
            "local_time": lambda value: local_time(value, selected),
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
        text = f"{normalized.day} {month} {normalized.year}, {normalized:%H:%M} UTC"
    else:
        text = f"{month} {normalized.day}, {normalized.year}, {normalized:%H:%M} UTC"
    return LocalTimeText(text, normalized)


def local_time(value: datetime | str | None, locale: str) -> str:
    if isinstance(value, datetime) or value is None:
        return format_datetime(value, locale)
    if len(value) <= 64:
        try:
            return format_datetime(datetime.fromisoformat(value), locale)
        except ValueError:
            pass
    return "—"


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
