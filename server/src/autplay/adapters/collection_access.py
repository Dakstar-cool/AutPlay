"""Short-lived Spotify PKCE grants for a single owner-confirmed collection transfer."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import requests
from cryptography.fernet import Fernet, InvalidToken

from autplay.domain.external_collections import CollectionError


def _cipher() -> Fernet:
    key_file = os.environ.get("AUTPLAY_IMPORT_KEY_FILE")
    if not key_file:
        raise CollectionError("spotify_not_configured", 503)
    try:
        with Path(key_file).open("rb") as stream:
            return Fernet(stream.read(100).strip())
    except (OSError, ValueError) as error:
        raise CollectionError("spotify_not_configured", 503) from error


def _settings() -> tuple[str, str]:
    client = os.environ.get("AUTPLAY_IMPORT_SPOTIFY_CLIENT_ID", "")
    redirect = os.environ.get("AUTPLAY_IMPORT_SPOTIFY_REDIRECT_URI", "")
    if not client or urlsplit(redirect).scheme != "https":
        raise CollectionError("spotify_not_configured", 503)
    return client, redirect


def spotify_authorization(owner: UUID, operation: UUID, *, likes: bool) -> tuple[str, str]:
    client, redirect = _settings()
    verifier = secrets.token_urlsafe(48)
    state = _cipher().encrypt(json.dumps([str(owner), str(operation), verifier]).encode()).decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    url = "https://accounts.spotify.com/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": client,
            "redirect_uri": redirect,
            "scope": "user-library-read"
            if likes
            else "playlist-read-private playlist-read-collaborative",
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
    )
    return url, hashlib.sha256(state.encode()).hexdigest()


def spotify_state(token: str) -> tuple[UUID, UUID, str]:
    try:
        owner, operation, verifier = json.loads(_cipher().decrypt(token.encode(), ttl=600))
        return UUID(owner), UUID(operation), verifier
    except (InvalidToken, ValueError) as error:
        raise CollectionError("authorization_expired", 403) from error


def spotify_exchange(code: str, verifier: str) -> str:
    client, redirect = _settings()
    try:
        with requests.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "authorization_code",
                "redirect_uri": redirect,
                "code": code,
                "client_id": client,
                "code_verifier": verifier,
            },
            timeout=(4, 12),
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise CollectionError("authorization_failed", 403)
            token = response.json()["access_token"]
            if not isinstance(token, str) or not 1 <= len(token) <= 8192:
                raise CollectionError("authorization_failed", 403)
            return _cipher().encrypt(token.encode()).decode()
    except (requests.RequestException, KeyError) as error:
        raise CollectionError("authorization_failed", 403) from error


def access_token(provider: str, snapshot: dict[str, Any], owner: UUID | None = None) -> str | None:
    if provider == "yandex" and owner is not None:
        root = os.environ.get("AUTPLAY_IMPORT_YANDEX_TOKEN_DIR")
        if root:
            try:
                with (Path(root) / str(owner)).open(encoding="ascii") as stream:
                    value = stream.read(8193).strip()
                    if 0 < len(value) <= 8192 and not any(char.isspace() for char in value):
                        return value
            except FileNotFoundError:
                return None
            except (OSError, UnicodeError) as error:
                raise CollectionError("source_access_required", 403) from error

    if provider == "spotify" and snapshot.get("grant"):
        try:
            return _cipher().decrypt(snapshot["grant"].encode(), ttl=3500).decode()
        except InvalidToken:
            return None
    if provider == "soundcloud":
        token_file = os.environ.get("AUTPLAY_IMPORT_SOUNDCLOUD_APP_TOKEN_FILE")
        if token_file:
            try:
                with Path(token_file).open(encoding="ascii") as stream:
                    value = stream.read(8193).strip()
                    if 0 < len(value) <= 8192:
                        return value
            except OSError, UnicodeError:
                pass
        raise CollectionError("soundcloud_not_configured", 503)
    return None
