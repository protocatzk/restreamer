from __future__ import annotations

from app.redaction import redact, redact_value


def test_redact_removes_known_secrets() -> None:
    assert redact("key=secret-token", ["secret-token"]) == "key=***"
    assert redact("abc", ["ab"]) == "abc"


def test_redact_always_masks_api_key_parameters() -> None:
    assert (
        redact("https://example/x?api_key=abc123&period=today")
        == "https://example/x?api_key=***&period=today"
    )


def test_redact_value_masks_short_single_secret() -> None:
    assert redact_value("token=ab", "ab") == "token=***"
    assert redact_value("nothing", "") == "nothing"
