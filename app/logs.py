from __future__ import annotations

from collections import deque
from collections.abc import Iterable


def redact(text: str, secrets: Iterable[str]) -> str:
    """Remove known secret values from a string before it is logged or exposed."""
    result = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, "***")
    return result


class RingLog:
    """Small in-memory log buffer for the dashboard."""

    def __init__(self, maxlen: int = 500) -> None:
        self._lines: deque[str] = deque(maxlen=maxlen)

    def add(self, line: str) -> None:
        self._lines.append(str(line).rstrip())

    def clear(self) -> None:
        self._lines.clear()

    def tail(self, count: int = 200) -> list[str]:
        if count <= 0:
            return []
        return list(self._lines)[-count:]
