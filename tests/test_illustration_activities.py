"""`illustrate_story`: disabled, matched and generic storage, idempotence and error mapping."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.media.fake import FakeIllustrationProvider
from ai_trend_pipeline.media.illustration import (
    IllustrationOutput,
    IllustrationProvider,
    illustration_alt_text,
)
from ai_trend_pipeline.media.illustration_activities import IllustrationActivities
from ai_trend_pipeline.media.illustrations.renderer import IllustratedCardProvider
from ai_trend_pipeline.media.models import (
    MAX_STATIC_BYTES,
    IllustrateInput,
    IllustrationResult,
    IllustrationStatus,
    MediaType,
    clean_media_text,
    illustration_artifact_id_for,
)
from ai_trend_pipeline.media.providers import RenderedMedia
from tests.illustrated_cases import CASE_BY_NAME
from tests.media_fixtures import HASH_A

INPUT = IllustrateInput(
    story_id="story-1",
    story_content_hash=HASH_A,
    headline="Life expectancy rises across the world",
    summary="Health improves.",
)


async def run(acts: IllustrationActivities, data: IllustrateInput = INPUT) -> IllustrationResult:
    return await ActivityEnvironment().run(acts.illustrate_story, data)


@pytest.mark.asyncio
async def test_disabled_returns_disabled_and_writes_nothing(tmp_path: Path) -> None:
    result = await run(IllustrationActivities(tmp_path, None))
    assert result == IllustrationResult(status=IllustrationStatus.DISABLED)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_matched_writes_one_artifact_and_rerun_is_identical(tmp_path: Path) -> None:
    provider = IllustratedCardProvider()
    acts = IllustrationActivities(tmp_path, provider)
    first = await run(acts)
    second = await run(acts)
    assert first == second
    assert first.status is IllustrationStatus.MATCHED and first.artifact is not None
    artifact = first.artifact
    assert artifact.media_type is MediaType.PNG and (artifact.width, artifact.height) == (1200, 675)
    assert artifact.artifact_id == illustration_artifact_id_for(
        INPUT, provider.name, provider.version
    )
    assert (tmp_path / artifact.relative_path).stat().st_size == artifact.byte_size
    assert artifact.byte_size <= MAX_STATIC_BYTES
    assert len(list(tmp_path.iterdir())) == 1
    # a fresh activity instance and provider give the same artifact
    again = await run(IllustrationActivities(tmp_path, IllustratedCardProvider()))
    assert again == first and len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_generic_writes_one_artifact(tmp_path: Path) -> None:
    data = CASE_BY_NAME["generic-no-tags"].data()
    result = await run(IllustrationActivities(tmp_path, IllustratedCardProvider()), data)
    assert result.status is IllustrationStatus.GENERIC and result.artifact is not None
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.asyncio
async def test_alt_text_matches_the_templates(tmp_path: Path) -> None:
    acts = IllustrationActivities(tmp_path, IllustratedCardProvider())
    matched = await run(acts)
    assert matched.artifact is not None
    assert matched.artifact.alt_text == (
        "Illustrated card with the headline: Life expectancy rises across the world. "
        "Drawing: a person meditating cross-legged, on a patterned background."
    )
    generic = await run(acts, CASE_BY_NAME["generic-no-tags"].data())
    assert generic.artifact is not None
    assert generic.artifact.alt_text == (
        "Illustrated card with the headline: A quiet Tuesday for everyone. "
        "Abstract shapes on a patterned background."
    )
    for text in (matched.artifact.alt_text, generic.artifact.alt_text):
        assert clean_media_text(text) == text


def test_alt_text_is_cut_to_the_limit() -> None:
    assert len(illustration_alt_text("H" * 160, "d" * 80)) <= 1_000
    assert illustration_alt_text("A headline", None).endswith("patterned background.")


@pytest.mark.asyncio
async def test_unsupported_content_is_non_retryable_illustration_invalid(tmp_path: Path) -> None:
    acts = IllustrationActivities(tmp_path, FakeIllustrationProvider(unsupported=True))
    with pytest.raises(ApplicationError) as info:
        await run(acts)
    assert info.value.type == "IllustrationInvalid" and info.value.non_retryable
    real = IllustrationActivities(tmp_path, IllustratedCardProvider())
    greek = INPUT.model_copy(update={"headline": "Ελληνικά"})
    with pytest.raises(ApplicationError) as greek_info:
        await run(real, greek)
    assert greek_info.value.type == "IllustrationInvalid"


@pytest.mark.asyncio
async def test_other_errors_are_retryable_illustration_failed(tmp_path: Path) -> None:
    acts = IllustrationActivities(tmp_path, FakeIllustrationProvider(fail_times=1))
    with pytest.raises(ApplicationError) as info:
        await run(acts)
    assert info.value.type == "IllustrationFailed" and not info.value.non_retryable
    assert (await run(acts)).status is IllustrationStatus.MATCHED


@dataclass
class BadProvider:
    name: str = "bad"
    version: str = "1"
    output: IllustrationOutput | None = None

    def illustrate(self, data: IllustrateInput) -> IllustrationOutput:
        assert self.output is not None
        return self.output


def _bad(data: bytes, media_type: MediaType = MediaType.PNG, side: int = 1200) -> BadProvider:
    rendered = RenderedMedia(data, media_type, side, 675, None)
    return BadProvider(output=IllustrationOutput(IllustrationStatus.GENERIC, rendered))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        _bad(b"\x89PNG\r\n\x1a\n" + b"x" * (MAX_STATIC_BYTES + 1)),
        _bad(b"GIF89a-not-a-png"),
        _bad(b"\x89PNG\r\n\x1a\nx", MediaType.GIF),
        _bad(b"\x89PNG\r\n\x1a\nx", side=10),
    ],
)
async def test_invalid_provider_output_is_rejected_without_storing(
    tmp_path: Path, provider: IllustrationProvider
) -> None:
    with pytest.raises(ApplicationError) as info:
        await run(IllustrationActivities(tmp_path, provider))
    assert info.value.type == "IllustrationInvalid" and info.value.non_retryable
    assert list(tmp_path.iterdir()) == []
