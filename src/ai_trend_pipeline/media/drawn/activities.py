"""Activity that asks Claude for drawn SVGs, rasterises the valid ones and returns metadata only.

Never logs or raises anything the model or the story wrote. A story is drawn at most once: any
existing pointer short-circuits before the API is called. Not registered in a worker yet.
"""

import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from anthropic.types import Message, ToolUseBlock
from pydantic import ValidationError
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.media.drawn.pipeline import (
    DrawingRejected,
    RenderedDrawing,
    render_drawn_svg,
)
from ai_trend_pipeline.media.drawn.prompt import (
    HEADLINE_LIMIT,
    SUMMARY_LIMIT,
    SYSTEM_PROMPT,
    TOOL_NAME_DRAW,
    DrawnImagesReply,
    build_user_prompt,
    clean_story_text,
    draw_tool,
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
from ai_trend_pipeline.media.models import (
    DRAWN_ROLE_ORDER,
    DrawImagesInput,
    DrawnArtifact,
    DrawnImagesResult,
    DrawnRole,
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
DRAW_REQUEST_TIMEOUT_S = 100.0
MAX_DRAW_TOKENS_PER_IMAGE = 1400
DRAW_TOKENS_OVERHEAD = 400
TRANSIENT_STATUSES = frozenset({408, 409, 429})

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
    ) -> None:
        self._client = client
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
        outcome = await self._call_api(headline, summary, roles)
        if isinstance(outcome, ApplicationError):
            raise outcome  # raised outside any except block so no SDK error is chained
        response = outcome

        rejects: Counter[str] = Counter()
        reply = self._parse_reply(response.stop_reason, response.content)
        images: list[DrawnArtifact] = []
        if reply is not None:
            images = await self._keep_images(reply, headline, story_hash, rejects)
        status = DrawnStatus.DRAWN if images else DrawnStatus.FAILED
        images.sort(key=lambda image: DRAWN_ROLE_ORDER.index(image.role))
        pointer_status = "drawn" if images else "failed"
        await asyncio.to_thread(
            write_pointer,
            root,
            DrawnPointer(
                story_content_hash=story_hash,
                model=self._model,
                prompt_hash=drawn_prompt_hash(),
                status=pointer_status,
                images=tuple(images),
            ),
        )
        _log.info(
            "draw_story_images status=%s kept=%d rejects=%s input_tokens=%d output_tokens=%d",
            status.value,
            len(images),
            dict(sorted(rejects.items())),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        return DrawnImagesResult(status=status, images=tuple(images))

    async def _call_api(
        self, headline: str, summary: str, roles: tuple[DrawnRole, ...]
    ) -> Message | ApplicationError:
        try:
            return await self._client.with_options(
                max_retries=0, timeout=DRAW_REQUEST_TIMEOUT_S
            ).messages.create(
                model=self._model,
                max_tokens=MAX_DRAW_TOKENS_PER_IMAGE * self._count + DRAW_TOKENS_OVERHEAD,
                system=SYSTEM_PROMPT,
                tools=[draw_tool()],
                tool_choice={"type": "tool", "name": TOOL_NAME_DRAW},
                messages=[{"role": "user", "content": build_user_prompt(headline, summary, roles)}],
            )
        except anthropic.APIConnectionError:
            return _transient()
        except anthropic.APIStatusError as exc:
            if exc.status_code in TRANSIENT_STATUSES or exc.status_code >= 500:
                return _transient()
            return _refused()
        except anthropic.APIError:
            return _refused()

    @staticmethod
    def _parse_reply(stop_reason: str | None, content: Sequence[object]) -> DrawnImagesReply | None:
        if stop_reason == "max_tokens":
            return None
        for block in content:
            if isinstance(block, ToolUseBlock) and block.name == TOOL_NAME_DRAW:
                try:
                    return DrawnImagesReply.model_validate(block.input)
                except ValidationError:
                    return None
        return None

    async def _keep_images(
        self,
        reply: DrawnImagesReply,
        headline: str,
        story_hash: str,
        rejects: Counter[str],
    ) -> list[DrawnArtifact]:
        wanted = set(requested_roles(self._count))
        seen: set[str] = set()
        kept: list[DrawnArtifact] = []
        for spec in reply.images:
            if len(kept) >= self._count:
                break
            if spec.role not in wanted or spec.role.value in seen:
                continue
            seen.add(spec.role.value)
            try:
                drawing: RenderedDrawing = await asyncio.to_thread(render_drawn_svg, spec.svg)
            except DrawingRejected as exc:
                rejects[exc.reason.value] += 1
                continue
            stored = await asyncio.to_thread(
                write_artifact, self._media_root, drawing.png, MediaType.PNG
            )
            artifact = MediaArtifact(
                artifact_id=drawn_artifact_id_for(
                    story_hash,
                    spec.role,
                    stored.content_hash,
                    drawn_prompt_hash(),
                    self._model,
                ),
                kind=MediaKind.STATIC,
                media_type=MediaType.PNG,
                width=drawing.width,
                height=drawing.height,
                byte_size=stored.byte_size,
                alt_text=drawn_alt_text(headline, spec.role),
                content_hash=stored.content_hash,
                relative_path=stored.relative_path,
            )
            kept.append(DrawnArtifact(role=spec.role, artifact=artifact))
        return kept
