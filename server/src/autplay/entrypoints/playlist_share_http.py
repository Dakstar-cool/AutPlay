"""Permanent read-only links to explicitly shared playlist versions."""

from collections.abc import Callable
from html import escape
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from autplay.application.playlist_shares import PlaylistShares, SnapshotBody, SnapshotTooLarge


def create_playlist_share_router(
    service: PlaylistShares, authenticated: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(prefix="/playlist-shares")
    headers = {
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
        ),
    }

    @router.post("", dependencies=[Depends(authenticated)])
    def publish(body: SnapshotBody, request: Request) -> dict[str, UUID]:
        try:
            return {"token": service.publish(request.state.principal.user_id, body)}
        except SnapshotTooLarge:
            raise HTTPException(413, "Playlist document is too large") from None

    def snapshot(token: UUID) -> dict[str, Any]:
        document = service.snapshot(token)
        if document is None:
            raise HTTPException(404, "Playlist link not found")
        return document

    @router.get("/{token}/file")
    def download(token: UUID) -> JSONResponse:
        return JSONResponse(
            {**snapshot(token), "token": str(token)},
            media_type="application/vnd.autplay.playlist+json",
            headers={
                **headers,
                "Content-Disposition": 'attachment; filename="playlist.autplaylist"',
            },
        )

    @router.get("/{token}", response_class=HTMLResponse)
    def preview(token: UUID) -> HTMLResponse:
        doc = snapshot(token)
        title = escape(doc["name"])
        rows = []
        for index in doc["entries"]:
            track = doc["tracks"][index]
            artist = escape(track["artist"] or "")
            rows.append(
                "<li>"
                + escape(track["title"] or "Без названия")
                + (" — " + artist if artist else "")
                + "</li>"
            )
        return HTMLResponse(
            '<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{title} · AutPlay</title>"
            "<style>body{font:1rem/1.5 system-ui;margin:2rem auto;"
            "max-width:46rem;padding:0 1rem;overflow-wrap:anywhere}li{padding:.3rem 0}</style>"
            f"<main><h1>{title}</h1><p>{escape(doc['description'] or '')}</p>"
            f'<a href="{token}/file" download>Скачать плейлист для AutPlay</a>'
            "<p>Откройте AutPlay, выберите «Моя музыка → Плейлисты → Открыть файл плейлиста» "
            "и сохраните копию. "
            "Доступность аудио зависит от вашей библиотеки.</p>"
            f"<ol>{''.join(rows)}</ol></main></html>",
            headers=headers,
        )

    return router
