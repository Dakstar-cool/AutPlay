"""Portable bounded TXT recovery document and code normalization contract."""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from autplay.domain.account_recovery import (
    AccountRecoveryError,
    RecoveryFile,
    code_verifier,
    new_code,
    normalize_code,
    parse_request,
    verify_device,
)


def document() -> RecoveryFile:
    return RecoveryFile(
        uuid4(),
        1,
        "a" * 64,
        "https://api.invalid",
        "https://stream.invalid",
        uuid4(),
        "Personal account",
        new_code(),
    )


def test_txt_roundtrip_and_code_scope() -> None:
    value = document()
    parsed = RecoveryFile.decode(value.encode())
    assert parsed.code == normalize_code(value.code)
    assert parsed.account_id == value.account_id
    assert value.code not in repr(value) and value.api_origin not in repr(value)
    plain = normalize_code(value.code)
    assert len(plain) == 32
    assert normalize_code(plain.lower()) == normalize_code(" \t" + value.code + "\n")
    expected = code_verifier(value.server_instance_id, value.account_id, value.code)
    assert expected != code_verifier(uuid4(), value.account_id, value.code)
    assert expected != code_verifier(value.server_instance_id, uuid4(), value.code)


@pytest.mark.parametrize(
    "code", ["A" * 31, "A" * 33, "I" * 32, "O" * 32, "\u0410" * 32, "A" * 32 + "\0", " " * 97]
)
def test_manual_code_rejects_ambiguous_or_unbounded_input(code: str) -> None:
    with pytest.raises(AccountRecoveryError, match="recovery_code_invalid"):
        normalize_code(code)


@pytest.mark.parametrize(
    "change",
    [
        {"version": True},
        {"version": 2},
        {"identity_epoch": False},
        {"extra": "ignored"},
        {"api_origin": "https://user:secret@api.invalid"},
        {"api_origin": "file:///tmp/a"},
        {"api_origin": "\u0000https://api.invalid"},
        {"account_label": "hidden\u0000text"},
        {"account_label": "\ud800"},
        {"code": "\uff21" * 32},
    ],
)
def test_txt_rejects_foreign_versions_fields_and_unsafe_locators(change: dict[str, object]) -> None:
    value = json.loads(document().encode())
    value.update(change)
    with pytest.raises(AccountRecoveryError, match="recovery_file_invalid"):
        RecoveryFile.decode(json.dumps(value).encode())


def test_txt_rejects_duplicate_and_oversized_json() -> None:
    raw = document().encode()
    for payload in (b'{"version":1,' + raw[1:], b" " * 4097, b"[]", b"\xff"):
        with pytest.raises(AccountRecoveryError, match="recovery_file_invalid"):
            RecoveryFile.decode(payload)


def test_android_shared_vectors_verify_on_server() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "tests/fixtures/account-recovery/v1/proof-vectors.json"
    )
    vectors = json.loads(path.read_text(encoding="utf-8"))
    document = RecoveryFile.decode(json.dumps(vectors["document"]).encode())
    assert (
        code_verifier(document.server_instance_id, document.account_id, vectors["code"]).hex()
        == vectors["verifier_sha256"]
    )
    for kind, value in vectors["requests"].items():
        parsed = parse_request(kind, value)
        assert UUID(parsed["account_id"]) == document.account_id
        if kind != "configure":
            assert verify_device(kind, parsed)
