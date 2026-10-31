"""Deterministic fake renderer. Bytes carry a valid signature but are not decodable images."""

import hashlib

from ai_trend_pipeline.media.models import (
    MAX_ANIMATED_BYTES,
    MAX_STATIC_BYTES,
    MEDIA_SIGNATURES,
    MediaBrief,
    MediaType,
    canonical_json,
)
from ai_trend_pipeline.media.providers import MediaRenderError, RenderedMedia

FAKE_WIDTH = 1200
FAKE_HEIGHT = 675
FAKE_DURATION_MS = 4_000


class FakeMediaRenderer:
    name = "fake"
    version = "1"

    def __init__(
        self,
        fail_static_times: int = 0,
        fail_animated_times: int = 0,
        oversize: bool = False,
        wrong_signature: bool = False,
    ) -> None:
        self._fail_static_times = fail_static_times
        self._fail_animated_times = fail_animated_times
        self._oversize = oversize
        self._wrong_signature = wrong_signature
        self._static_calls = 0
        self._animated_calls = 0

    def _digest(self, brief: MediaBrief, label: str) -> bytes:
        payload = canonical_json(brief.model_dump(mode="json")) + label.encode("utf-8")
        return hashlib.sha256(payload).digest()

    def render_static(self, brief: MediaBrief) -> RenderedMedia:
        self._static_calls += 1
        if self._static_calls <= self._fail_static_times:
            raise MediaRenderError("fake static render failure")
        data = MEDIA_SIGNATURES[MediaType.PNG] + self._digest(brief, "static")
        if self._wrong_signature:
            data = MEDIA_SIGNATURES[MediaType.GIF] + self._digest(brief, "static")
        if self._oversize:
            data += b"\0" * MAX_STATIC_BYTES
        return RenderedMedia(data, MediaType.PNG, FAKE_WIDTH, FAKE_HEIGHT, None)

    def render_animated(self, brief: MediaBrief) -> RenderedMedia:
        self._animated_calls += 1
        if self._animated_calls <= self._fail_animated_times:
            raise MediaRenderError("fake animated render failure")
        data = MEDIA_SIGNATURES[MediaType.GIF] + self._digest(brief, "animated")
        if self._wrong_signature:
            data = MEDIA_SIGNATURES[MediaType.PNG] + self._digest(brief, "animated")
        if self._oversize:
            data += b"\0" * MAX_ANIMATED_BYTES
        return RenderedMedia(data, MediaType.GIF, FAKE_WIDTH, FAKE_HEIGHT, FAKE_DURATION_MS)
