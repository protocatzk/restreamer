"""yt-dlp wrapper with a workaround for TikTok's deprecated live-detail API.

yt-dlp's TikTokLiveIE falls back to ``https://www.tiktok.com/api/live/detail/``
when the primary webcast response contains no MP4/HLS formats (TikTok often
returns FLV-only streams). That endpoint is deprecated and now answers with
HTTP 400, which makes yt-dlp raise ``UserNotLive`` even though the stream is
live and valid FLV formats were already found.

This wrapper swallows that specific error and keeps the already-discovered
formats. It is installed as ``yt-dlp-live`` in the Docker image and configured
via ``YTDLP_BIN``.
"""

from __future__ import annotations

import sys

from yt_dlp import main
from yt_dlp.extractor.tiktok import TikTokLiveIE
from yt_dlp.utils import UserNotLive

_original_call_api = TikTokLiveIE._call_api


def _patched_call_api(self, url, param, room_id, uploader, key=None):
    try:
        return _original_call_api(self, url, param, room_id, uploader, key)
    except UserNotLive:
        if "api/live/detail" in url:
            return {}
        raise


def install_patch() -> None:
    TikTokLiveIE._call_api = _patched_call_api


if __name__ == "__main__":
    install_patch()
    sys.exit(main())
