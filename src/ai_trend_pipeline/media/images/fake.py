"""Deterministic image backend for tests and local runs. Makes no network calls."""

import io

from PIL import Image

from ai_trend_pipeline.media.images.backends import (
    ImageBackendName,
    ImageBackendRefused,
    ImageBackendTransient,
)
from ai_trend_pipeline.media.images.models import GeneratedImage, ImageBatch, ImageBrief
from ai_trend_pipeline.media.models import DRAWN_ROLE_ORDER, DrawnRole

FAKE_WIDTH = 1200
FAKE_HEIGHT = 900


def _png(role: DrawnRole) -> bytes:
    shade = 60 + 40 * DRAWN_ROLE_ORDER.index(role)
    buffer = io.BytesIO()
    Image.new("RGB", (FAKE_WIDTH, FAKE_HEIGHT), (shade, 120, 200 - shade // 2)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


class FakeImageBackend:
    name = ImageBackendName.FAKE

    def __init__(self, *, empty: bool = False, transient: bool = False, refused: bool = False):
        self._empty = empty
        self._transient = transient
        self._refused = refused
        self.briefs: list[ImageBrief] = []

    async def generate(self, brief: ImageBrief) -> ImageBatch:
        self.briefs.append(brief)
        if self._transient:
            raise ImageBackendTransient("drawing request failed, will retry")
        if self._refused:
            raise ImageBackendRefused("drawing request was refused")
        if self._empty:
            return ImageBatch()
        return ImageBatch(
            images=tuple(
                GeneratedImage(role=role, png=_png(role), width=FAKE_WIDTH, height=FAKE_HEIGHT)
                for role in brief.roles
            )
        )
