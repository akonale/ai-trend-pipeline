"""Activity that asks Claude for drawn SVGs, rasterises the valid ones and returns metadata only.

Never logs or raises anything the model or the story wrote. A story is drawn at most once: any
existing pointer short-circuits before the backend is called.
The Claude call lives behind an `ImageBackend`.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.media.drawn.prompt import (
    HEADLINE_LIMIT,
    SUMMARY_LIMIT,
    clean_story_text,
    drawn_alt_text,
    drawn_prompt_hash,
    requested_roles,
)
from ai_trend_pipeline.media.drawn.store import (
    DrawnPointer,
    PointerCorrupt,
    read_pointer,
    reserve_call,
    story_lock,
    write_pointer,
)
from ai_trend_pipeline.media.images.anthropic_svg import (
    DRAW_TOKENS_OVERHEAD,
    MAX_DRAW_TOKENS_PER_IMAGE,
    AnthropicSvgBackend,
)
from ai_trend_pipeline.media.images.backends import (
    ImageBackend,
    ImageBackendRefused,
    ImageBackendTransient,
)
from ai_trend_pipeline.media.images.models import ImageBatch, ImageBrief
from ai_trend_pipeline.media.models import (
    DRAWN_ROLE_ORDER,
    DrawImagesInput,
    DrawnArtifact,
    DrawnImagesResult,
    DrawnStatus,
    MediaArtifact,
    MediaKind,
    MediaType,
    drawn_artifact_id_for,
)
from ai_trend_pipeline.media.storage import (
    MediaArtifactMissing,
    resolve_artifact_path,
    write_artifact,
)

DRAW_ACTIVITY = "draw_story_images"
DRAWING_TRANSIENT_ERROR_TYPE = "DrawingTransient"
DRAWING_FAILED_ERROR_TYPE = "DrawingFailed"

__all__ = ["DRAW_TOKENS_OVERHEAD", "MAX_DRAW_TOKENS_PER_IMAGE", "DrawnImageActivities"]

_log = activity.logger


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _failed() -> DrawnImagesResult:
    return DrawnImagesResult(status=DrawnStatus.FAILED)


def _result_from_pointer(media_root: Path, pointer: DrawnPointer) -> DrawnImagesResult:
    try:
        for image in pointer.images:
            resolve_artifact_path(media_root, image.artifact)
    except MediaArtifactMissing:
        return _failed()
    return DrawnImagesResult(status=DrawnStatus(pointer.status), images=pointer.images)


def _transient() -> ApplicationError:
    return ApplicationError("drawing request failed, will retry", type=DRAWING_TRANSIENT_ERROR_TYPE)


def _refused() -> ApplicationError:
    return ApplicationError(
        "drawing request was refused", type=DRAWING_FAILED_ERROR_TYPE, non_retryable=True
    )


class DrawnImageActivities:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        media_root: Path,
        *,
        enabled: bool,
        model: str,
        count: int,
        daily_cap: int,
        clock: Callable[[], datetime] = _utc_now,
        backend: ImageBackend | None = None,
    ) -> None:
        self._backend = backend if backend is not None else AnthropicSvgBackend(client, model)
        self._media_root = media_root
        self._enabled = enabled
        self._model = model
        self._count = count
        self._daily_cap = daily_cap
        self._clock = clock

    @activity.defn(name=DRAW_ACTIVITY)
    async def draw_story_images(self, data: DrawImagesInput) -> DrawnImagesResult:
        if not self._enabled:
            return DrawnImagesResult(status=DrawnStatus.DISABLED)
        lock = story_lock(self._media_root, data.story_content_hash)
        await asyncio.to_thread(lock.__enter__)
        try:
            return await self._draw_locked(data)
        finally:
            await asyncio.to_thread(lock.__exit__, None, None, None)

    async def _draw_locked(self, data: DrawImagesInput) -> DrawnImagesResult:
        root = self._media_root
        story_hash = data.story_content_hash
        try:
            pointer = read_pointer(root, story_hash)
        except PointerCorrupt:
            return _failed()
        if pointer is not None:
            return _result_from_pointer(root, pointer)

        headline = clean_story_text(data.headline, HEADLINE_LIMIT)
        summary = clean_story_text(data.summary, SUMMARY_LIMIT)
        if not headline:
            return _failed()

        today = self._clock().astimezone(UTC).date()
        if not await asyncio.to_thread(reserve_call, root, today, self._daily_cap):
            return DrawnImagesResult(status=DrawnStatus.CAPPED)

        roles = requested_roles(self._count)
        outcome = await self._generate(ImageBrief(headline=headline, summary=summary, roles=roles))
        if isinstance(outcome, ApplicationError):
            raise outcome  # raised outside any except block so no SDK error is chained
        batch = outcome

        images: list[DrawnArtifact] = []
        for generated in batch.images:
            stored = await asyncio.to_thread(
                write_artifact, self._media_root, generated.png, MediaType.PNG
            )
            artifact = MediaArtifact(
                artifact_id=drawn_artifact_id_for(
                    story_hash,
                    generated.role,
                    stored.content_hash,
                    drawn_prompt_hash(),
                    self._model,
                ),
                kind=MediaKind.STATIC,
                media_type=MediaType.PNG,
                width=generated.width,
                height=generated.height,
                byte_size=stored.byte_size,
                alt_text=drawn_alt_text(headline, generated.role),
                content_hash=stored.content_hash,
                relative_path=stored.relative_path,
            )
            images.append(DrawnArtifact(role=generated.role, artifact=artifact))
        status = DrawnStatus.DRAWN if images else DrawnStatus.FAILED
        images.sort(key=lambda image: DRAWN_ROLE_ORDER.index(image.role))
        pointer_status = "drawn" if images else "failed"
        await asyncio.to_thread(
            write_pointer,
            root,
            DrawnPointer(
                story_content_hash=story_hash,
                model=self._model,
                provider=self._backend.name.value,
                prompt_hash=drawn_prompt_hash(),
                status=pointer_status,
                images=tuple(images),
            ),
        )
        _log.info(
            "draw_story_images status=%s kept=%d rejects=%s input_tokens=%d output_tokens=%d",
            status.value,
            len(images),
            dict(sorted(batch.rejects.items())),
            batch.input_tokens,
            batch.output_tokens,
        )
        return DrawnImagesResult(status=status, images=tuple(images))

    async def _generate(self, brief: ImageBrief) -> ImageBatch | ApplicationError:
        try:
            return await self._backend.generate(brief)
        except ImageBackendTransient:
            return _transient()
        except ImageBackendRefused:
            return _refused()
