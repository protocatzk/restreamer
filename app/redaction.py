"""Shared redaction for logs and API error messages."""

from __future__ import annotations

import re
from collections.abc import Iterable

_API_KEY_PATTERN = re.compile(r"(api_key=)[^&\s]+", re.IGNORECASE)


def redact(
    text: object,
    secrets: Iterable[str] = (),
    *,
    min_secret_length: int = 4,
) -> str:
    """Remove known secrets and ``api_key=`` values from a string."""
    result = str(text)
    for secret in secrets:
        value = str(secret)
        if value and len(value) >= min_secret_length:
            result = result.replace(value, "***")
    return _API_KEY_PATTERN.sub(r"\1***", result)


def redact_value(value: str, secret: str = "") -> str:
    """Redact a single optional secret, matching the monitor helpers."""
    return redact(value, [secret] if secret else [], min_secret_length=1)
