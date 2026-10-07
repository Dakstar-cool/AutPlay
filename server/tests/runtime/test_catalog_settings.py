"""Catalog containment is explicit and never falls back to uncontained Linux HTTP."""

from pathlib import Path

import pytest
from autplay.adapters.process_tree import ProcessTree
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.entrypoints import catalog_composition
from autplay.runtime.settings import SettingsLoadError, load_api_settings

from .test_settings import AUTH_SECRET, DATABASE_URL, PUBLIC_SOURCE_SECRET


def test_catalog_root_environment_and_absolute_validation(tmp_path: Path) -> None:
    overrides = {
        "database_url": DATABASE_URL,
        "auth_signing_secret": AUTH_SECRET,
        "public_access_source_hmac_secret": PUBLIC_SOURCE_SECRET,
    }
    settings = load_api_settings(
        overrides=overrides, environ={"AUTPLAY_CATALOG_CGROUP_ROOT": str(tmp_path)}
    )
    assert settings.catalog_cgroup_root == tmp_path
    for value in ("relative", str(tmp_path / ".." / "escape")):
        with pytest.raises(SettingsLoadError):
            load_api_settings(overrides=overrides, environ={"AUTPLAY_CATALOG_CGROUP_ROOT": value})


def test_missing_linux_containment_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_api_settings(
        overrides={
            "database_url": DATABASE_URL,
            "auth_signing_secret": AUTH_SECRET,
            "public_access_source_hmac_secret": PUBLIC_SOURCE_SECRET,
        },
        environ={},
    )
    # Patch the selector only after constructing native Path values.
    monkeypatch.setattr("autplay.entrypoints.catalog_composition.os.name", "posix")
    with pytest.raises(ResourceAdmissionError, match="containment_unavailable"):
        catalog_composition.catalog_process_tree(settings)


def test_bad_configured_containment_is_not_reported_ready() -> None:
    settings = load_api_settings(
        overrides={
            "database_url": DATABASE_URL,
            "auth_signing_secret": AUTH_SECRET,
            "public_access_source_hmac_secret": PUBLIC_SOURCE_SECRET,
        },
        environ={},
    )

    def denied() -> ProcessTree:
        raise PermissionError("synthetic nondelegated cgroup")

    with pytest.raises(ResourceAdmissionError, match="containment_unavailable"):
        catalog_composition.CatalogRuntime(settings, tree_factory=denied)
