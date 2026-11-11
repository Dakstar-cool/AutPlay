"""UNEXECUTED factory/HTTP/source-budget regressions; synthetic disposable inputs.

All imports, signatures, HTTP and DB assertions below are future test actions.
No executable or cryptographic qualification was performed while authoring.
"""

from __future__ import annotations

from typing import Any

import pytest
from autplay.entrypoints.api import create_app
from autplay.runtime.settings import ApiSettings, RuntimeProfile
from fastapi import Request
from fastapi.testclient import TestClient
from psycopg import Connection
from pydantic import SecretStr

from .deletion_evidence import ledger_settings
from .test_device_admission_s1b import _admission_request
from .test_device_admission_s1b import admission_runtime as admission_runtime
from .test_device_admission_v2 import AdmissionHarness, proof
from .test_device_admission_v2 import admission_v2 as admission_v2

LEGACY = "/api/v1/social/admission-requests"
MODERN = "/api/v1/device-compatibility/v2/admission/requests"
PROXY = "172.30.77.2"


@pytest.mark.parametrize(
    "evidence", ["trusted", "untrusted", "missing", "duplicate", "unconfigured"]
)
def test_factory_http_versions_share_the_established_source_and_ten_request_budget(
    admission_v2: AdmissionHarness,
    database_url: str,
    database_connection: Connection[Any],
    evidence: str,
) -> None:
    h = admission_v2
    app = create_app(
        ApiSettings(
            profile=RuntimeProfile.TEST,
            database_url=SecretStr(database_url),
            auth_signing_secret=SecretStr("d1-source-http-auth-synthetic-at-least-32-bytes"),
            public_access_source_hmac_secret=SecretStr(
                "d1-source-http-reduction-synthetic-32-bytes"
            ),
            public_access_trusted_proxy_ip=None if evidence == "unconfigured" else PROXY,
            device_compatibility_v2_enabled=True,
            **ledger_settings(h.runtime.sessions),
        ),
        profile_pairing_service=h.runtime.service,
    )

    @app.middleware("http")
    async def exact_peer(request: Request, call_next: Any) -> Any:
        # Model only the socket peer; do not pre-process any forwarded header.
        request.scope["client"] = ("172.30.77.3" if evidence == "untrusted" else PROXY, 55001)
        return await call_next(request)

    def headers(marker: int, *, alternate: bool = False) -> list[tuple[str, str]]:
        result = [
            ("Content-Type", "application/json"),
            ("X-Forwarded-For", f"198.51.100.{marker + 1}"),
        ]
        if evidence != "missing":
            # Untrusted/ambiguous evidence changes each attempt, but must still
            # converge on one unknown-source budget in BOTH versions.
            source = "203.0.113.56" if alternate else "203.0.113.55"
            if evidence in {"untrusted", "duplicate", "unconfigured"}:
                source = f"203.0.113.{marker + 1}"
            result.append(("X-AutPlay-Client-IP", source))
        if evidence == "duplicate":
            result.append(("X-AutPlay-Client-IP", "192.0.2.99"))
        return result

    def submit(client: TestClient, modern: bool, marker: int, *, alternate: bool = False) -> Any:
        if modern:
            body, key = h.submit_body()
            return client.post(
                MODERN,
                content=proof(body, key, "admission.submit"),
                headers=headers(marker, alternate=alternate),
            )
        body, _, _ = _admission_request(h.runtime, nonce_marker=marker + 1)
        return client.post(LEGACY, json=body, headers=headers(marker, alternate=alternate))

    with TestClient(app) as client:
        for marker in range(10):
            response = submit(client, marker % 2 == 1, marker)
            assert response.status_code == 202, response.text
            assert response.headers["Cache-Control"] == "no-store"
        before = database_connection.execute(
            "SELECT request_id,contract_version,request_sha256 FROM "
            "account.device_admission ORDER BY request_id"
        ).fetchall()
        windows = database_connection.execute(
            "SELECT attempt_count FROM account.device_admission_rate_window WHERE "
            "scope='SOURCE_15M'"
        ).fetchall()
        database_connection.commit()
        assert len(before) == 10
        assert sorted(row[1] for row in before) == ["v1"] * 5 + ["v2"] * 5
        assert windows == [(10,)]
        for modern in (False, True):
            denied = submit(client, modern, 11 + int(modern))
            assert denied.status_code == 429
            assert denied.json()["error"]["code"] == "admission_rate_limited"
        assert (
            database_connection.execute(
                "SELECT request_id,contract_version,request_sha256 FROM "
                "account.device_admission ORDER BY request_id"
            ).fetchall()
            == before
        )
        assert database_connection.execute(
            "SELECT attempt_count FROM account.device_admission_rate_window WHERE "
            "scope='SOURCE_15M'"
        ).fetchall() == [(10,)]
        database_connection.commit()
        if evidence == "trusted":
            # A second verified real source behind the SAME proxy gets its own
            # bucket. This distinguishes the canonical client from proxy peer.
            assert submit(client, False, 20, alternate=True).status_code == 202
            assert submit(client, True, 21, alternate=True).status_code == 202
            assert database_connection.execute(
                "SELECT attempt_count FROM account.device_admission_rate_window "
                "WHERE scope='SOURCE_15M' ORDER BY attempt_count"
            ).fetchall() == [(2,), (10,)]
            database_connection.commit()
