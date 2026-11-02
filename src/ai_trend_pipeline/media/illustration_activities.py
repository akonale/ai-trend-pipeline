"""`illustrate_story`: run the provider, validate its output, store it, return an artifact."""

import asyncio
from pathlib import Path

from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.media.illustration import (
    IllustrationOutput,
    IllustrationProvider,
    illustration_alt_text,
)
from ai_trend_pipeline.media.models import (
    MAX_MEDIA_DIMENSION_PX,
    MAX_STATIC_BYTES,
    MIN_MEDIA_DIMENSION_PX,
    IllustrateInput,
    IllustrationResult,
    IllustrationStatus,
    MediaArtifact,
    MediaKind,
    MediaType,
    has_media_signature,
    illustration_artifact_id_for,
)
from ai_trend_pipeline.media.providers import MediaContentUnsupported
from ai_trend_pipeline.media.storage import write_artifact

ILLUSTRATION_INVALID_ERROR_TYPE = "IllustrationInvalid"
ILLUSTRATION_FAILED_ERROR_TYPE = "IllustrationFailed"
ILLUSTRATE_ACTIVITY = "illustrate_story"

_STATIC_TYPES = {MediaType.PNG, MediaType.WEBP}


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(message, type=ILLUSTRATION_INVALID_ERROR_TYPE, non_retryable=True)


def _validate(output: IllustrationOutput) -> None:
    rendered = output.rendered
    if rendered.media_type not in _STATIC_TYPES:
        raise _invalid("illustration must be a png or webp still")
    if not has_media_signature(rendered.data, rendered.media_type):
        raise _invalid("illustration output does not match its media type")
    if len(rendered.data) > MAX_STATIC_BYTES:
        raise _invalid("illustration is too large")
    for side in (rendered.width, rendered.height):
        if not MIN_MEDIA_DIMENSION_PX <= side <= MAX_MEDIA_DIMENSION_PX:
            raise _invalid("illustration has out-of-range dimensions")
    if output.status not in {IllustrationStatus.MATCHED, IllustrationStatus.GENERIC}:
        raise _invalid("a provider may only return matched or generic")


class IllustrationActivities:
    """`provider=None` is the kill switch: every call returns `disabled`."""

    def __init__(self, media_root: Path, provider: IllustrationProvider | None) -> None:
        self._media_root = media_root
        self._provider = provider

    @activity.defn(name=ILLUSTRATE_ACTIVITY)
    async def illustrate_story(self, data: IllustrateInput) -> IllustrationResult:
        provider = self._provider
        if provider is None:
            return IllustrationResult(status=IllustrationStatus.DISABLED)
        try:
            output = await asyncio.to_thread(provider.illustrate, data)
        except MediaContentUnsupported as exc:
            raise _invalid("this headline cannot be drawn by the illustration provider") from exc
        except Exception as exc:
            raise ApplicationError(
                "illustration failed", type=ILLUSTRATION_FAILED_ERROR_TYPE
            ) from exc
        _validate(output)
        stored = await asyncio.to_thread(
            write_artifact, self._media_root, output.rendered.data, output.rendered.media_type
        )
        try:
            artifact = MediaArtifact(
                artifact_id=illustration_artifact_id_for(data, provider.name, provider.version),
                kind=MediaKind.STATIC,
                media_type=output.rendered.media_type,
                width=output.rendered.width,
                height=output.rendered.height,
                duration_ms=None,
                byte_size=stored.byte_size,
                alt_text=illustration_alt_text(data.headline, output.description),
                content_hash=stored.content_hash,
                relative_path=stored.relative_path,
            )
        except ValueError as exc:
            raise _invalid("illustration failed artifact validation") from exc
        return IllustrationResult(status=output.status, artifact=artifact)
