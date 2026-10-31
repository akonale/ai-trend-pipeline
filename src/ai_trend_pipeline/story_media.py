"""StoryMediaWorkflow: render a static card and an optional GIF for one story package.

Deterministic: rendering happens in activities, and no media bytes ever enter workflow history.
Render failures never fail the workflow; they only lower the outcome.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from ai_trend_pipeline.media.briefs import (
        StoryMediaInput,
        StoryMediaResult,
        media_brief_for,
        story_content_hash,
    )
    from ai_trend_pipeline.media.models import (
        MediaArtifact,
        MediaKind,
        MediaManifest,
        MediaOutcome,
        RenderMediaInput,
        media_brief_hash,
    )

STORY_MEDIA_TASK_QUEUE = "story-media-task-queue"
RENDER_STATIC_ACTIVITY = "render_static_media"
RENDER_ANIMATED_ACTIVITY = "render_animated_media"
STATIC_RENDER_TIMEOUT = timedelta(seconds=60)
ANIMATED_RENDER_TIMEOUT = timedelta(seconds=45)
RENDER_MAX_ATTEMPTS = 3
RENDER_RETRY_POLICY = RetryPolicy(
    maximum_attempts=RENDER_MAX_ATTEMPTS,
    non_retryable_error_types=["MediaRenderInvalid", "MediaArtifactTooLarge"],
)

# A deterministic render rarely succeeds on a third try, so animated renders get two attempts.
ANIMATED_RENDER_RETRY_POLICY = RetryPolicy(
    maximum_attempts=2,
    non_retryable_error_types=["MediaRenderInvalid", "MediaArtifactTooLarge"],
)


@workflow.defn
class StoryMediaWorkflow:
    @workflow.run
    async def run(self, data: StoryMediaInput) -> StoryMediaResult:
        brief = media_brief_for(data.package, data.facts)

        def manifest(
            outcome: MediaOutcome,
            static: MediaArtifact | None = None,
            animated: MediaArtifact | None = None,
        ) -> StoryMediaResult:
            return StoryMediaResult(
                manifest=MediaManifest(
                    story_id=data.story_id,
                    story_content_hash=story_content_hash(data.package),
                    brief_hash=media_brief_hash(brief),
                    outcome=outcome,
                    static_artifact=static,
                    animated_artifact=animated,
                )
            )

        try:
            static = await workflow.execute_activity(
                RENDER_STATIC_ACTIVITY,
                RenderMediaInput(brief=brief, kind=MediaKind.STATIC),
                result_type=MediaArtifact,
                start_to_close_timeout=STATIC_RENDER_TIMEOUT,
                retry_policy=RENDER_RETRY_POLICY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            return manifest(MediaOutcome.TEXT_ONLY)
        if not data.include_animation:
            return manifest(MediaOutcome.STATIC, static)
        try:
            animated = await workflow.execute_activity(
                RENDER_ANIMATED_ACTIVITY,
                RenderMediaInput(brief=brief, kind=MediaKind.ANIMATED),
                result_type=MediaArtifact,
                start_to_close_timeout=ANIMATED_RENDER_TIMEOUT,
                retry_policy=ANIMATED_RENDER_RETRY_POLICY,
            )
        except ActivityError as exc:
            if is_cancelled_exception(exc):
                raise
            return manifest(MediaOutcome.STATIC_FALLBACK, static)
        return manifest(MediaOutcome.GIF, static, animated)
