"""Render activities: validation, storage and error mapping."""

from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.media.activities import StoryMediaActivities
from ai_trend_pipeline.media.fake import FakeMediaRenderer
from ai_trend_pipeline.media.models import MediaKind, MediaType, RenderMediaInput, artifact_id_for
from tests.media_fixtures import make_brief


def _acts(tmp_path: Path, renderer: FakeMediaRenderer | None = None) -> StoryMediaActivities:
    return StoryMediaActivities(tmp_path, renderer or FakeMediaRenderer())


@pytest.mark.asyncio
async def test_render_static_stores_file_and_returns_bounded_artifact(tmp_path: Path) -> None:
    brief = make_brief()
    acts = _acts(tmp_path)
    artifact = await ActivityEnvironment().run(
        acts.render_static_media, RenderMediaInput(brief=brief, kind=MediaKind.STATIC)
    )
    assert artifact.media_type is MediaType.PNG
    assert artifact.artifact_id == artifact_id_for(brief, MediaKind.STATIC, "fake", "1")
    assert artifact.alt_text == brief.alt_text
    assert (tmp_path / artifact.relative_path).stat().st_size == artifact.byte_size
    assert len(artifact.model_dump_json()) < 1_000
    animated = await ActivityEnvironment().run(
        acts.render_animated_media, RenderMediaInput(brief=brief, kind=MediaKind.ANIMATED)
    )
    assert animated.media_type is MediaType.GIF and animated.duration_ms == 4000


@pytest.mark.asyncio
async def test_rerun_with_same_input_is_idempotent(tmp_path: Path) -> None:
    acts = _acts(tmp_path)
    data = RenderMediaInput(brief=make_brief(), kind=MediaKind.STATIC)
    first = await ActivityEnvironment().run(acts.render_static_media, data)
    second = await ActivityEnvironment().run(acts.render_static_media, data)
    assert first == second
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_transient_render_error_is_retryable_application_error(tmp_path: Path) -> None:
    acts = _acts(tmp_path, FakeMediaRenderer(fail_static_times=1))
    data = RenderMediaInput(brief=make_brief(), kind=MediaKind.STATIC)
    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(acts.render_static_media, data)
    assert info.value.type == "MediaRenderFailed"
    assert not info.value.non_retryable
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_wrong_signature_and_oversize_are_non_retryable(tmp_path: Path) -> None:
    data = RenderMediaInput(brief=make_brief(), kind=MediaKind.STATIC)
    with pytest.raises(ApplicationError) as bad:
        await ActivityEnvironment().run(
            _acts(tmp_path, FakeMediaRenderer(wrong_signature=True)).render_static_media, data
        )
    assert bad.value.type == "MediaRenderInvalid" and bad.value.non_retryable
    with pytest.raises(ApplicationError) as big:
        await ActivityEnvironment().run(
            _acts(tmp_path, FakeMediaRenderer(oversize=True)).render_static_media, data
        )
    assert big.value.type == "MediaArtifactTooLarge" and big.value.non_retryable
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_kind_mismatch_is_non_retryable(tmp_path: Path) -> None:
    acts = _acts(tmp_path)
    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(
            acts.render_static_media, RenderMediaInput(brief=make_brief(), kind=MediaKind.ANIMATED)
        )
    assert info.value.type == "MediaRenderInvalid" and info.value.non_retryable
