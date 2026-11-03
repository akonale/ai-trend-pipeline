"""StoryMediaWorkflow with the fake renderer in the time-skipping environment."""

import json
import uuid
from pathlib import Path

import pytest
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.media.activities import StoryMediaActivities
from ai_trend_pipeline.media.briefs import (
    StoryMediaInput,
    StoryMediaResult,
    media_workflow_id_for,
)
from ai_trend_pipeline.media.card import StoryCardRenderer
from ai_trend_pipeline.media.fake import FakeIllustrationProvider, FakeMediaRenderer
from ai_trend_pipeline.media.illustration import IllustrationProvider
from ai_trend_pipeline.media.illustration_activities import IllustrationActivities
from ai_trend_pipeline.media.models import (
    DrawnArtifact,
    DrawnImagesResult,
    DrawnRole,
    DrawnStatus,
    IllustrationStatus,
    MediaBrief,
    MediaKind,
    MediaManifest,
    MediaOutcome,
)
from ai_trend_pipeline.media.providers import MediaRenderer, MediaRenderError, RenderedMedia
from ai_trend_pipeline.story_generation import MEDIA_CHILD_TIMEOUT
from ai_trend_pipeline.story_media import (
    ANIMATED_RENDER_RETRY_POLICY,
    ANIMATED_RENDER_TIMEOUT,
    DRAW_SCHEDULE_TO_CLOSE,
    RENDER_MAX_ATTEMPTS,
    RENDER_RETRY_POLICY,
    STATIC_RENDER_TIMEOUT,
    STORY_MEDIA_TASK_QUEUE,
    StoryMediaWorkflow,
)
from tests.media_fixtures import make_artifact
from tests.story_generation_fixtures import FakeDrawActivities, make_facts, make_package

STORY = "story-1"


class CountingRenderer(FakeMediaRenderer):
    def __init__(self, **kwargs: int | bool) -> None:
        super().__init__(
            fail_static_times=int(kwargs.get("fail_static_times", 0)),
            fail_animated_times=int(kwargs.get("fail_animated_times", 0)),
            oversize=bool(kwargs.get("oversize", False)),
            wrong_signature=bool(kwargs.get("wrong_signature", False)),
        )

    @property
    def static_calls(self) -> int:
        return self._static_calls

    @property
    def animated_calls(self) -> int:
        return self._animated_calls


async def _run(
    tmp_path: Path,
    renderer: MediaRenderer,
    *,
    include_animation: bool = True,
    illustration: IllustrationProvider | None = None,
    draw: FakeDrawActivities | None = None,
    serve_draw: bool = True,
    **package_overrides: object,
) -> tuple[StoryMediaResult, WorkflowHandle[StoryMediaWorkflow, StoryMediaResult]]:
    package = make_package(STORY, **package_overrides)
    data = StoryMediaInput(
        story_id=STORY,
        package=package,
        facts=make_facts(STORY),
        include_animation=include_animation,
    )
    acts = StoryMediaActivities(tmp_path, renderer)
    illustrate = IllustrationActivities(tmp_path, illustration)
    drawing = draw or FakeDrawActivities()
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    ) as env:
        async with Worker(
            env.client,
            task_queue=STORY_MEDIA_TASK_QUEUE,
            workflows=[StoryMediaWorkflow],
            activities=[
                acts.render_static_media,
                acts.render_animated_media,
                illustrate.illustrate_story,
                *([drawing.draw_story_images] if serve_draw else []),
            ],
        ):
            handle = await env.client.start_workflow(
                StoryMediaWorkflow.run,
                data,
                id=f"{media_workflow_id_for(package)}-{uuid.uuid4()}",
                task_queue=STORY_MEDIA_TASK_QUEUE,
            )
            result = await handle.result()
            history = await handle.fetch_history()
            _HISTORY.append(history.to_json())
    return result, handle


_HISTORY: list[str] = []


@pytest.mark.asyncio
async def test_gif_outcome_has_static_and_animated_artifacts(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, CountingRenderer())
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.static_artifact is not None and manifest.animated_artifact is not None
    assert manifest.story_id == STORY
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.asyncio
async def test_static_outcome_when_animation_disabled(tmp_path: Path) -> None:
    renderer = CountingRenderer()
    result, _ = await _run(tmp_path, renderer, include_animation=False)
    assert result.manifest.outcome is MediaOutcome.STATIC
    assert result.manifest.animated_artifact is None
    assert renderer.animated_calls == 0


@pytest.mark.asyncio
async def test_static_fallback_when_animation_fails_after_retries(tmp_path: Path) -> None:
    renderer = CountingRenderer(fail_animated_times=10)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.STATIC_FALLBACK
    assert result.manifest.static_artifact is not None
    assert result.manifest.animated_artifact is None
    assert renderer.animated_calls == 2


@pytest.mark.asyncio
async def test_text_only_when_static_fails_and_animation_not_attempted(tmp_path: Path) -> None:
    renderer = CountingRenderer(fail_static_times=10)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert result.manifest.static_artifact is None
    assert renderer.static_calls == 3
    assert renderer.animated_calls == 0


@pytest.mark.asyncio
async def test_transient_failures_recover_within_the_retry_budget(tmp_path: Path) -> None:
    renderer = CountingRenderer(fail_static_times=2, fail_animated_times=1)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.GIF
    assert renderer.static_calls == 3 and renderer.animated_calls == 2


@pytest.mark.asyncio
async def test_non_retryable_render_errors_are_attempted_once(tmp_path: Path) -> None:
    renderer = CountingRenderer(oversize=True)
    result, _ = await _run(tmp_path, renderer)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert renderer.static_calls == 1
    wrong = CountingRenderer(wrong_signature=True)
    result, _ = await _run(tmp_path, wrong)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert wrong.static_calls == 1


@pytest.mark.asyncio
async def test_history_contains_no_media_bytes(tmp_path: Path) -> None:
    _HISTORY.clear()
    await _run(tmp_path, CountingRenderer())
    text = _HISTORY[-1]
    assert "iVBOR" not in text  # base64 of the PNG signature
    assert "R0lGODlh" not in text  # base64 of GIF89a
    assert "GIF89a" not in text
    assert "\\u0089PNG" not in text and "\x89PNG" not in text


@pytest.mark.asyncio
async def test_same_package_produces_same_manifest(tmp_path: Path) -> None:
    first, _ = await _run(tmp_path / "a", CountingRenderer())
    second, _ = await _run(tmp_path / "b", CountingRenderer())
    assert first.manifest == second.manifest


@pytest.mark.asyncio
async def test_real_renderer_gif_outcome_has_poster_and_gif(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, StoryCardRenderer())
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.static_artifact is not None and manifest.animated_artifact is not None
    assert manifest.static_artifact.kind is MediaKind.STATIC
    assert manifest.animated_artifact.duration_ms == 4500
    assert (tmp_path / manifest.animated_artifact.relative_path).read_bytes().startswith(b"GIF89a")


class BrokenEncoderRenderer(StoryCardRenderer):
    animated_calls = 0

    def render_animated(self, brief: MediaBrief) -> RenderedMedia:
        BrokenEncoderRenderer.animated_calls += 1
        raise MediaRenderError("gif encoding failed")


@pytest.mark.asyncio
async def test_encoder_failure_falls_back_to_static_after_two_attempts(tmp_path: Path) -> None:
    BrokenEncoderRenderer.animated_calls = 0
    result, _ = await _run(tmp_path, BrokenEncoderRenderer())
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.STATIC_FALLBACK
    assert manifest.static_artifact is not None and manifest.animated_artifact is None
    assert BrokenEncoderRenderer.animated_calls == 2


@pytest.mark.asyncio
async def test_real_renderer_same_package_produces_same_manifest(tmp_path: Path) -> None:
    first, _ = await _run(tmp_path / "a", StoryCardRenderer())
    second, _ = await _run(tmp_path / "b", StoryCardRenderer())
    assert first.manifest == second.manifest
    assert MediaManifest.model_validate(first.manifest.model_dump()) == second.manifest
    leftovers = [p for root in ("a", "b") for p in (tmp_path / root).glob(".media-*.tmp")]
    assert leftovers == []


@pytest.mark.asyncio
async def test_animated_activity_uses_dedicated_timeout_and_retry_policy(tmp_path: Path) -> None:
    assert ANIMATED_RENDER_TIMEOUT.total_seconds() == 45
    assert ANIMATED_RENDER_RETRY_POLICY.maximum_attempts == 2
    assert RENDER_RETRY_POLICY.maximum_attempts == 3
    _HISTORY.clear()
    await _run(tmp_path, CountingRenderer())
    events = json.loads(_HISTORY[-1])["events"]
    scheduled = {
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]: e[
            "activityTaskScheduledEventAttributes"
        ]
        for e in events
        if "activityTaskScheduledEventAttributes" in e
    }
    animated = scheduled["render_animated_media"]
    static = scheduled["render_static_media"]
    assert animated["startToCloseTimeout"] == "45s"
    assert animated["retryPolicy"]["maximumAttempts"] == 2
    assert static["retryPolicy"]["maximumAttempts"] == 3


# --- 065a: the illustrated card --------------------------------------------------------------


@pytest.mark.asyncio
async def test_matched_illustration_is_added_beside_the_gif(tmp_path: Path) -> None:
    provider = FakeIllustrationProvider()
    result, _ = await _run(tmp_path, CountingRenderer(), illustration=provider)
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.illustration_status is IllustrationStatus.MATCHED
    assert manifest.illustration_artifact is not None
    assert manifest.selected is None
    assert len(list(tmp_path.iterdir())) == 3
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_generic_illustration_is_recorded(tmp_path: Path) -> None:
    result, _ = await _run(
        tmp_path, CountingRenderer(), illustration=FakeIllustrationProvider(generic=True)
    )
    assert result.manifest.illustration_status is IllustrationStatus.GENERIC
    assert result.manifest.illustration_artifact is not None


@pytest.mark.asyncio
async def test_static_outcome_without_animation_still_gets_an_illustration(
    tmp_path: Path,
) -> None:
    result, _ = await _run(
        tmp_path,
        CountingRenderer(),
        include_animation=False,
        illustration=FakeIllustrationProvider(),
    )
    assert result.manifest.outcome is MediaOutcome.STATIC
    assert result.manifest.illustration_artifact is not None


@pytest.mark.asyncio
async def test_disabled_provider_records_disabled_and_stores_nothing(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, CountingRenderer())
    assert result.manifest.illustration_status is IllustrationStatus.DISABLED
    assert result.manifest.illustration_artifact is None
    assert len(list(tmp_path.iterdir())) == 2


@pytest.mark.asyncio
async def test_failed_illustration_after_two_attempts_leaves_the_card_outcome(
    tmp_path: Path,
) -> None:
    provider = FakeIllustrationProvider(fail_times=10)
    result, _ = await _run(tmp_path, CountingRenderer(), illustration=provider)
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.animated_artifact is not None
    assert manifest.illustration_status is IllustrationStatus.FAILED
    assert manifest.illustration_artifact is None
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_unsupported_illustration_is_not_retried(tmp_path: Path) -> None:
    provider = FakeIllustrationProvider(unsupported=True)
    result, _ = await _run(tmp_path, CountingRenderer(), illustration=provider)
    assert result.manifest.illustration_status is IllustrationStatus.FAILED
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_card_outcome_is_unchanged_by_an_illustration_failure(tmp_path: Path) -> None:
    plain, _ = await _run(tmp_path / "a", CountingRenderer(fail_animated_times=10))
    failed, _ = await _run(
        tmp_path / "b",
        CountingRenderer(fail_animated_times=10),
        illustration=FakeIllustrationProvider(fail_times=10),
    )
    assert plain.manifest.outcome is failed.manifest.outcome is MediaOutcome.STATIC_FALLBACK
    assert plain.manifest.static_artifact == failed.manifest.static_artifact


@pytest.mark.asyncio
async def test_card_failure_makes_no_illustration_call(tmp_path: Path) -> None:
    provider = FakeIllustrationProvider()
    result, _ = await _run(tmp_path, CountingRenderer(fail_static_times=10), illustration=provider)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert result.manifest.illustration_status is None
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_illustration_runs_concurrently_with_the_gif(tmp_path: Path) -> None:
    _HISTORY.clear()
    await _run(tmp_path, CountingRenderer(), illustration=FakeIllustrationProvider())
    events = json.loads(_HISTORY[-1])["events"]
    names: dict[int, str] = {}
    for event in events:
        attrs = event.get("activityTaskScheduledEventAttributes")
        if attrs:
            names[int(event["eventId"])] = attrs["activityType"]["name"]
    by_name = {name: event_id for event_id, name in names.items()}
    assert {"illustrate_story", "render_animated_media"} <= set(by_name)
    pair = {by_name["illustrate_story"], by_name["render_animated_media"]}
    first_done = min(
        int(e["eventId"])
        for e in events
        if int(e.get("activityTaskCompletedEventAttributes", {}).get("scheduledEventId", 0)) in pair
    )
    assert max(pair) < first_done  # both are scheduled before either finishes
    attrs = next(
        e["activityTaskScheduledEventAttributes"]
        for e in events
        if e.get("activityTaskScheduledEventAttributes", {}).get("activityType", {}).get("name")
        == "illustrate_story"
    )
    assert attrs["startToCloseTimeout"] == "30s"
    assert attrs["retryPolicy"]["maximumAttempts"] == 2
    assert attrs["retryPolicy"]["nonRetryableErrorTypes"] == ["IllustrationInvalid"]


@pytest.mark.asyncio
async def test_illustration_history_has_no_image_bytes_and_manifest_stays_small(
    tmp_path: Path,
) -> None:
    _HISTORY.clear()
    result, _ = await _run(tmp_path, CountingRenderer(), illustration=FakeIllustrationProvider())
    assert "iVBOR" not in _HISTORY[-1] and "fake-illustration" not in _HISTORY[-1]
    assert len(result.manifest.model_dump_json()) < 8_192


@pytest.mark.asyncio
async def test_story_media_v2_illustrated_history_replays_and_is_current() -> None:
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    path = Path(__file__).parent / "fixtures" / "histories" / "story_media_v2_illustrated.json"
    history = WorkflowHistory.from_json("story-media-v2-replay", path.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[StoryMediaWorkflow], data_converter=pydantic_data_converter)
    assert (await replayer.replay_workflow(history)).replay_failure is None


# --- 069: drawn images ----------------------------------------------------------------------


def _drawn(*roles: DrawnRole, alt: str = "A drawing") -> DrawnImagesResult:
    images: list[DrawnArtifact] = []
    for n, role in enumerate(roles, start=1):
        digest = f"{n + 8:x}" * 64
        artifact = make_artifact(
            artifact_id="art-v1-" + f"{n + 8:016x}",
            content_hash=digest,
            relative_path=f"{digest}.png",
            alt_text=alt,
        )
        images.append(DrawnArtifact(role=role, artifact=artifact))
    return DrawnImagesResult(status=DrawnStatus.DRAWN, images=tuple(images))


def _scheduled(history_json: str) -> dict[str, dict[str, object]]:
    events = json.loads(history_json)["events"]
    return {
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]: e[
            "activityTaskScheduledEventAttributes"
        ]
        for e in events
        if "activityTaskScheduledEventAttributes" in e
    }


@pytest.mark.asyncio
async def test_drawn_images_are_folded_into_the_manifest(tmp_path: Path) -> None:
    fake = FakeDrawActivities(_drawn(DrawnRole.HERO, DrawnRole.CONCEPT))
    result, _ = await _run(tmp_path, CountingRenderer(), draw=fake)
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.drawn_status is DrawnStatus.DRAWN
    assert [i.role for i in manifest.review_gallery()] == [DrawnRole.HERO, DrawnRole.CONCEPT]
    assert fake.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [DrawnStatus.FAILED, DrawnStatus.CAPPED])
async def test_failed_and_capped_drawings_set_the_status_and_no_images(
    tmp_path: Path, status: DrawnStatus
) -> None:
    fake = FakeDrawActivities(DrawnImagesResult(status=status))
    result, _ = await _run(tmp_path, CountingRenderer(), draw=fake)
    assert result.manifest.drawn_status is status
    assert result.manifest.drawn_artifacts == ()
    assert result.manifest.outcome is MediaOutcome.GIF


@pytest.mark.asyncio
async def test_disabled_drawing_leaves_no_drawn_keys(tmp_path: Path) -> None:
    result, _ = await _run(tmp_path, CountingRenderer())
    dumped = result.manifest.model_dump(mode="json")
    assert "drawn_status" not in dumped and "drawn_artifacts" not in dumped
    assert result.manifest.drawn_status is None


@pytest.mark.asyncio
async def test_transient_drawing_error_is_tried_twice_then_failed(tmp_path: Path) -> None:
    fake = FakeDrawActivities(raise_type="DrawingTransient")
    result, _ = await _run(tmp_path, CountingRenderer(), draw=fake)
    assert fake.calls == 2
    assert result.manifest.drawn_status is DrawnStatus.FAILED
    assert result.manifest.outcome is MediaOutcome.GIF


@pytest.mark.asyncio
async def test_drawing_failed_error_is_not_retried(tmp_path: Path) -> None:
    fake = FakeDrawActivities(raise_type="DrawingFailed", non_retryable=True)
    result, _ = await _run(tmp_path, CountingRenderer(), draw=fake)
    assert fake.calls == 1
    assert result.manifest.drawn_status is DrawnStatus.FAILED


@pytest.mark.asyncio
async def test_unserved_drawing_gives_failed_and_leaves_the_card(tmp_path: Path) -> None:
    # A drawing that never completes ends in `failed` the same way. A truly hung activity cannot
    # be time-skipped (a running activity blocks the skip), so the 150 s schedule_to_close bound
    # is pinned by the history assertions below, and here the activity is not served at all.
    result, _ = await _run(tmp_path, CountingRenderer(), serve_draw=False)
    assert result.manifest.drawn_status is DrawnStatus.FAILED
    assert result.manifest.outcome is MediaOutcome.GIF


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fake",
    [
        FakeDrawActivities(_drawn(DrawnRole.HERO)),
        FakeDrawActivities(raise_type="DrawingTransient"),
        FakeDrawActivities(DrawnImagesResult(status=DrawnStatus.CAPPED)),
    ],
)
async def test_drawing_never_changes_the_card_gif_or_illustration(
    tmp_path: Path, fake: FakeDrawActivities
) -> None:
    kwargs = {"fail_animated_times": 10}
    plain, _ = await _run(
        tmp_path / "a", CountingRenderer(**kwargs), illustration=FakeIllustrationProvider()
    )
    drawn, _ = await _run(
        tmp_path / "b",
        CountingRenderer(**kwargs),
        illustration=FakeIllustrationProvider(),
        draw=fake,
    )
    left, right = plain.manifest, drawn.manifest
    assert left.outcome is right.outcome is MediaOutcome.STATIC_FALLBACK
    assert left.static_artifact == right.static_artifact
    assert left.animated_artifact == right.animated_artifact
    assert left.illustration_artifact == right.illustration_artifact
    assert left.illustration_status is right.illustration_status
    assert left.selected is right.selected


@pytest.mark.asyncio
async def test_static_outcome_without_animation_still_gets_drawings(tmp_path: Path) -> None:
    fake = FakeDrawActivities(_drawn(DrawnRole.HERO))
    result, _ = await _run(tmp_path, CountingRenderer(), include_animation=False, draw=fake)
    assert result.manifest.outcome is MediaOutcome.STATIC
    assert result.manifest.drawn_status is DrawnStatus.DRAWN


@pytest.mark.asyncio
async def test_text_only_story_never_schedules_a_drawing(tmp_path: Path) -> None:
    fake = FakeDrawActivities(_drawn(DrawnRole.HERO))
    result, _ = await _run(tmp_path, CountingRenderer(fail_static_times=10), draw=fake)
    assert result.manifest.outcome is MediaOutcome.TEXT_ONLY
    assert result.manifest.drawn_status is None
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_oversize_drawn_manifest_falls_back_to_failed_with_the_card_intact(
    tmp_path: Path,
) -> None:
    big = "\U0001f600" * 160
    fake = FakeDrawActivities(
        _drawn(*DRAWN_ROLES, alt="\U0001f600" * 240),
    )
    result, _ = await _run(
        tmp_path,
        CountingRenderer(),
        illustration=FakeIllustrationProvider(),
        draw=fake,
        headline=big,
    )
    manifest = result.manifest
    assert manifest.outcome is MediaOutcome.GIF
    assert manifest.static_artifact is not None and manifest.animated_artifact is not None
    assert manifest.drawn_status is DrawnStatus.FAILED
    assert manifest.drawn_artifacts == ()


DRAWN_ROLES = (DrawnRole.HERO, DrawnRole.CONCEPT, DrawnRole.DETAIL, DrawnRole.SETTING)


@pytest.mark.asyncio
async def test_drawing_is_scheduled_concurrently_with_the_attributes_from_the_spec(
    tmp_path: Path,
) -> None:
    _HISTORY.clear()
    fake = FakeDrawActivities(_drawn(DrawnRole.HERO))
    await _run(tmp_path, CountingRenderer(), illustration=FakeIllustrationProvider(), draw=fake)
    events = json.loads(_HISTORY[-1])["events"]
    ids = {
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]: int(e["eventId"])
        for e in events
        if "activityTaskScheduledEventAttributes" in e
    }
    pair = {ids["draw_story_images"], ids["render_animated_media"], ids["illustrate_story"]}
    first_done = min(
        int(e["eventId"])
        for e in events
        if int(e.get("activityTaskCompletedEventAttributes", {}).get("scheduledEventId", 0)) in pair
    )
    assert max(pair) < first_done
    attrs = _scheduled(_HISTORY[-1])["draw_story_images"]
    assert attrs["startToCloseTimeout"] == "110s"
    assert attrs["scheduleToCloseTimeout"] == "150s"
    policy = attrs["retryPolicy"]
    assert isinstance(policy, dict)
    assert policy["maximumAttempts"] == 2
    assert policy["nonRetryableErrorTypes"] == ["DrawingFailed"]
    assert "iVBOR" not in _HISTORY[-1] and "<svg" not in _HISTORY[-1]


def test_drawing_budget_fits_under_the_media_child_timeout() -> None:
    worst = DRAW_SCHEDULE_TO_CLOSE + RENDER_MAX_ATTEMPTS * STATIC_RENDER_TIMEOUT
    assert worst.total_seconds() == 330
    assert worst < MEDIA_CHILD_TIMEOUT


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["story_media_v1_gif.json", "story_media_v3_drawn.json"])
async def test_older_and_drawn_media_histories_replay(name: str) -> None:
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    path = Path(__file__).parent / "fixtures" / "histories" / name
    history = WorkflowHistory.from_json("story-media-replay", path.read_text(encoding="utf-8"))
    replayer = Replayer(workflows=[StoryMediaWorkflow], data_converter=pydantic_data_converter)
    assert (await replayer.replay_workflow(history)).replay_failure is None
