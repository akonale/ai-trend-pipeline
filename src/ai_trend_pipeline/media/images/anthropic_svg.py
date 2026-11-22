"""Claude draws SVGs through a tool call; valid ones are rasterised to PNG."""

import asyncio
from collections import Counter
from collections.abc import Sequence

import anthropic
from anthropic.types import Message, ToolUseBlock
from pydantic import ValidationError

from ai_trend_pipeline.media.drawn.pipeline import (
    DrawingRejected,
    RenderedDrawing,
    render_drawn_svg,
)
from ai_trend_pipeline.media.drawn.prompt import (
    SYSTEM_PROMPT,
    TOOL_NAME_DRAW,
    DrawnImagesReply,
    build_user_prompt,
    draw_tool,
)
from ai_trend_pipeline.media.images.backends import (
    ImageBackendName,
    ImageBackendRefused,
    ImageBackendTransient,
)
from ai_trend_pipeline.media.images.models import GeneratedImage, ImageBatch, ImageBrief

DRAW_REQUEST_TIMEOUT_S = 100.0
MAX_DRAW_TOKENS_PER_IMAGE = 1400
DRAW_TOKENS_OVERHEAD = 400
TRANSIENT_STATUSES = frozenset({408, 409, 429})

_TRANSIENT_MESSAGE = "drawing request failed, will retry"
_REFUSED_MESSAGE = "drawing request was refused"


class AnthropicSvgBackend:
    name = ImageBackendName.ANTHROPIC_SVG

    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self._client = client
        self._model = model

    async def generate(self, brief: ImageBrief) -> ImageBatch:
        outcome = await self._call_api(brief)
        if isinstance(outcome, Exception):
            raise outcome  # raised outside any except block so no SDK error is chained
        reply = self._parse_reply(outcome.stop_reason, outcome.content)
        rejects: Counter[str] = Counter()
        images: tuple[GeneratedImage, ...] = ()
        if reply is not None:
            images = await self._keep_images(reply, brief, rejects)
        return ImageBatch(
            images=images,
            rejects=dict(rejects),
            input_tokens=outcome.usage.input_tokens,
            output_tokens=outcome.usage.output_tokens,
        )

    async def _call_api(self, brief: ImageBrief) -> Message | Exception:
        try:
            return await self._client.with_options(
                max_retries=0, timeout=DRAW_REQUEST_TIMEOUT_S
            ).messages.create(
                model=self._model,
                max_tokens=MAX_DRAW_TOKENS_PER_IMAGE * len(brief.roles) + DRAW_TOKENS_OVERHEAD,
                system=SYSTEM_PROMPT,
                tools=[draw_tool()],
                # claude-sonnet-5-5 rejects a forced tool; a reply with no tool call becomes failed.
                tool_choice={"type": "auto"},
                messages=[
                    {
                        "role": "user",
                        "content": build_user_prompt(brief.headline, brief.summary, brief.roles),
                    }
                ],
            )
        except anthropic.APIConnectionError:
            return ImageBackendTransient(_TRANSIENT_MESSAGE)
        except anthropic.APIStatusError as exc:
            if exc.status_code in TRANSIENT_STATUSES or exc.status_code >= 500:
                return ImageBackendTransient(_TRANSIENT_MESSAGE)
            return ImageBackendRefused(_REFUSED_MESSAGE)
        except anthropic.APIError:
            return ImageBackendRefused(_REFUSED_MESSAGE)

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

    @staticmethod
    async def _keep_images(
        reply: DrawnImagesReply, brief: ImageBrief, rejects: Counter[str]
    ) -> tuple[GeneratedImage, ...]:
        wanted = set(brief.roles)
        seen: set[str] = set()
        kept: list[GeneratedImage] = []
        for spec in reply.images:
            if len(kept) >= len(brief.roles):
                break
            if spec.role not in wanted or spec.role.value in seen:
                continue
            seen.add(spec.role.value)
            try:
                drawing: RenderedDrawing = await asyncio.to_thread(render_drawn_svg, spec.svg)
            except DrawingRejected as exc:
                rejects[exc.reason.value] += 1
                continue
            kept.append(
                GeneratedImage(
                    role=spec.role, png=drawing.png, width=drawing.width, height=drawing.height
                )
            )
        return tuple(kept)
