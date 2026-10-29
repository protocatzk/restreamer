from __future__ import annotations

import pytest
from yt_dlp.extractor.tiktok import TikTokLiveIE
from yt_dlp.utils import UserNotLive

import app.ytdlp_wrapper as wrapper


def test_patch_swallows_deprecated_live_detail(monkeypatch) -> None:
    def raise_not_live(self, url, param, room_id, uploader, key=None):
        raise UserNotLive(video_id=uploader)

    monkeypatch.setattr(wrapper, "_original_call_api", raise_not_live)

    result = wrapper._patched_call_api(
        None,
        "https://www.tiktok.com/api/live/detail/",
        "roomID",
        "123",
        "alice",
    )
    assert result == {}


def test_patch_keeps_other_not_live_errors(monkeypatch) -> None:
    def raise_not_live(self, url, param, room_id, uploader, key=None):
        raise UserNotLive(video_id=uploader)

    monkeypatch.setattr(wrapper, "_original_call_api", raise_not_live)

    with pytest.raises(UserNotLive):
        wrapper._patched_call_api(
            None,
            "https://webcast.tiktok.com/webcast/room/info",
            "room_id",
            "123",
            "alice",
        )


def test_install_patch(monkeypatch) -> None:
    original = TikTokLiveIE._call_api
    monkeypatch.setattr(TikTokLiveIE, "_call_api", original)

    wrapper.install_patch()

    assert TikTokLiveIE._call_api is wrapper._patched_call_api
