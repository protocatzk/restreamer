"""Shared request helpers for the JSON API."""

from __future__ import annotations

from fastapi import HTTPException, Request


async def json_object(request: Request) -> dict:
    """Parse a JSON object, rejecting malformed or non-object payloads."""
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Ungültiges JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON-Objekt erwartet")
    return payload


async def watch_flag(request: Request) -> bool:
    """Read the optional ``watch`` flag; an empty/invalid body means true."""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - empty body is fine
        return True
    if isinstance(payload, dict):
        return bool(payload.get("watch", True))
    return True
