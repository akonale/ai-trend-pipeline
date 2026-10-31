"""Render activities: call the renderer, validate output, store it, return a bounded artifact."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.media.models import (
    MAX_ANIMATED_BYTES,
    MAX_MEDIA_DIMENSION_PX,
    MAX_STATIC_BYTES,
    MIN_MEDIA_DIMENSION_PX,
    MediaArtifact,
    MediaBrief,
    MediaKind,
    MediaType,
    RenderMediaInput,
    artifact_id_for,
    has_media_signature,
)
from ai_trend_pipeline.media.providers import MediaRenderer, MediaRenderError, RenderedMedia
from ai_trend_pipeline.media.storage import write_artifact

RENDER_FAILED_ERROR_TYPE = "MediaRenderFailed"
RENDER_INVALID_ERROR_TYPE = "MediaRenderInvalid"
ARTIFACT_TOO_LARGE_ERROR_TYPE = "MediaArtifactTooLarge"

_ALLOWED_TYPES = {
    MediaKind.STATIC: {MediaType.PNG, MediaType.WEBP},
    MediaKind.ANIMATED: {MediaType.GIF},
}
_MAX_BYTES = {MediaKind.STATIC: MAX_STATIC_BYTES, MediaKind.ANIMATED: MAX_ANIMATED_BYTES}


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=RENDER_INVALID_ERROR_TYPE, non_retryable=True)


class StoryMediaActivities:
    def __init__(self, media_root: Path, renderer: MediaRenderer) -> None:
        self._media_root = media_root
        self._renderer = renderer

    @activity.defn(name="render_static_media")
    async def render_static_media(self, data: RenderMediaInput) -> MediaArtifact:
        return await self._render(data, MediaKind.STATIC, self._renderer.render_static)

    @activity.defn(name="render_animated_media")
    async def render_animated_media(self, data: RenderMediaInput) -> MediaArtifact:
        return await self._render(data, MediaKind.ANIMATED, self._renderer.render_animated)

    async def _render(
        self,
        data: RenderMediaInput,
        kind: MediaKind,
        render: Callable[[MediaBrief], RenderedMedia],
    ) -> MediaArtifact:
        if data.kind is not kind:
            raise _invalid(f"expected a {kind.value} render request")
        try:
            rendered = await asyncio.to_thread(render, data.brief)
        except MediaRenderError as exc:
            raise ApplicationError(
                f"{kind.value} render failed", type=RENDER_FAILED_ERROR_TYPE
            ) from exc
        if rendered.media_type not in _ALLOWED_TYPES[kind]:
            raise _invalid("renderer returned a media type that is not allowed for this kind")
        if not has_media_signature(rendered.data, rendered.media_type):
            raise _invalid("renderer output does not match its media type")
        if len(rendered.data) > _MAX_BYTES[kind]:
            raise ApplicationError(
                "rendered media is too large",
                type=ARTIFACT_TOO_LARGE_ERROR_TYPE,
                non_retryable=True,
            )
        for side in (rendered.width, rendered.height):
            if not MIN_MEDIA_DIMENSION_PX <= side <= MAX_MEDIA_DIMENSION_PX:
                raise _invalid("renderer returned out-of-range dimensions")
        stored = await asyncio.to_thread(
            write_artifact, self._media_root, rendered.data, rendered.media_type
        )
        try:
            return MediaArtifact(
                artifact_id=artifact_id_for(
                    data.brief, kind, self._renderer.name, self._renderer.version
                ),
                kind=kind,
                media_type=rendered.media_type,
                width=rendered.width,
                height=rendered.height,
                duration_ms=rendered.duration_ms,
                byte_size=stored.byte_size,
                alt_text=data.brief.alt_text,
                content_hash=stored.content_hash,
                relative_path=stored.relative_path,
            )
        except ValueError as exc:
            raise _invalid("renderer output failed artifact validation") from exc
