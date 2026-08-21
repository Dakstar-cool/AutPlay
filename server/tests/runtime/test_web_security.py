"""M6 browser request-boundary primitives."""

from __future__ import annotations

from http.cookies import Morsel, SimpleCookie

import pytest
from autplay.runtime.web_security import (
    ADMIN_SECURITY_HEADERS,
    WebCookieProfile,
    WebRequestRejected,
    apply_admin_security_headers,
    canonical_form_request_hash,
    decode_request_integrity_token,
    encode_request_integrity_token,
    parse_urlencoded_form,
    require_exact_origin,
    source_rate_key,
)
from starlette.responses import Response


def test_exact_origin_rejects_missing_duplicate_and_case_change() -> None:
    expected = "https://admin.example.test"
    require_exact_origin(_scope([(b"origin", expected.encode())]), expected)
    for headers in (
        [],
        [(b"origin", b"https://ADMIN.example.test")],
        [(b"origin", expected.encode()), (b"origin", expected.encode())],
    ):
        with pytest.raises(WebRequestRejected, match="origin_invalid"):
            require_exact_origin(_scope(headers), expected)


def test_form_parser_is_bounded_exact_and_duplicate_safe() -> None:
    allowed = frozenset({"csrf_token", "operation_id"})
    assert parse_urlencoded_form(
        "application/x-www-form-urlencoded",
        b"csrf_token=one&operation_id=two",
        allowed_fields=allowed,
    ) == {"csrf_token": "one", "operation_id": "two"}
    for body in (
        b"csrf_token=one",
        b"csrf_token=one&operation_id=two&extra=three",
        b"csrf_token=one&csrf_token=two&operation_id=three",
    ):
        with pytest.raises(WebRequestRejected, match="request_validation_failed"):
            parse_urlencoded_form("application/x-www-form-urlencoded", body, allowed_fields=allowed)


def test_form_request_hash_is_order_independent_but_request_bound() -> None:
    first = canonical_form_request_hash("POST", "/admin/logout", {"b": "2", "a": "1"})
    reordered = canonical_form_request_hash("POST", "/admin/logout", {"a": "1", "b": "2"})
    changed = canonical_form_request_hash("POST", "/admin/logout-all", {"a": "1", "b": "2"})
    assert first == reordered
    assert changed != first


def test_request_integrity_token_has_one_canonical_base64url_form() -> None:
    value = bytes(range(32))
    encoded = encode_request_integrity_token(value)
    assert len(encoded) == 43 and "=" not in encoded
    assert decode_request_integrity_token(encoded) == value
    for malformed in ("", encoded + "=", encoded[:-1] + "+", "A" * 42):
        with pytest.raises(WebRequestRejected, match="csrf_invalid"):
            decode_request_integrity_token(malformed)


def test_cookie_profiles_are_host_only_http_only_and_strict() -> None:
    release = WebCookieProfile.for_origin("https://admin.example.test")
    response = Response()
    release.set_session(response, "opaque", max_age=3600)
    release.set_preauth(response, "challenge")
    cookies = _cookies(response)
    assert cookies["__Host-autplay_admin"]["secure"] is True
    assert cookies["__Host-autplay_admin"]["httponly"] is True
    assert cookies["__Host-autplay_admin"]["samesite"].lower() == "strict"
    assert cookies["__Host-autplay_admin"]["path"] == "/"
    assert not cookies["__Host-autplay_admin"]["domain"]
    assert cookies["__Host-autplay_login"]["max-age"] == "300"

    development = WebCookieProfile.for_origin("http://127.0.0.1:8787")
    dev_response = Response()
    development.set_session(dev_response, "opaque", max_age=60)
    dev_cookie = _cookies(dev_response)["autplay_admin_dev"]
    assert not dev_cookie["secure"]
    assert dev_cookie["path"] == "/admin"


def test_headers_and_source_rate_keys_are_safe_and_deterministic() -> None:
    response = apply_admin_security_headers(Response())
    for name, value in ADMIN_SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert "unsafe-inline" not in response.headers["Content-Security-Policy"]
    first = source_rate_key(b"s" * 32, "192.0.2.8")
    assert first == source_rate_key(b"s" * 32, "192.0.2.8")
    assert first != source_rate_key(b"s" * 32, "192.0.2.9")
    assert b"192.0.2.8" not in first


def _scope(headers: list[tuple[bytes, bytes]]) -> dict[str, object]:
    return {"type": "http", "headers": headers}


def _cookies(response: Response) -> dict[str, Morsel[str]]:
    result: dict[str, Morsel[str]] = {}
    for value in response.headers.getlist("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(value)
        for name in parsed:
            result[name] = parsed[name]
    return result
