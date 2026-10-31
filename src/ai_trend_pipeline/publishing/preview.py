"""Pure preview of the exact post a Ready story would publish. No clock, no I/O."""

from pydantic import BaseModel, ConfigDict

from ai_trend_pipeline.publishing.formatter import (
    PublicationRejected,
    format_publication,
    formatted_post,
)
from ai_trend_pipeline.publishing.models import DestinationName, PublicationErrorCode
from ai_trend_pipeline.publishing.registry import capabilities_for
from ai_trend_pipeline.story_drafting.models import StoryPackage


class PostPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    body_text: str
    link_text: str | None = None
    char_count: int
    max_chars: int
    destination: DestinationName
    problem: PublicationErrorCode | None = None


def preview_post(
    package: StoryPackage,
    content_hash: str,
    destination: DestinationName = DestinationName.BLUESKY,
) -> PostPreview:
    """The formatted text with its count. A local rejection sets `problem` and keeps the text."""
    capabilities = capabilities_for(destination)
    text, _, anchor = formatted_post(package, capabilities)
    problem: PublicationErrorCode | None = None
    try:
        format_publication(package, content_hash, capabilities)
    except PublicationRejected as exc:
        problem = exc.code
    body_text = text[: len(text) - len(anchor)] if anchor else text
    return PostPreview(
        text=text,
        body_text=body_text,
        link_text=anchor,
        char_count=len(text),
        max_chars=capabilities.max_text_chars,
        destination=destination,
        problem=problem,
    )
