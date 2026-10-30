"""Pure five-status mapping and 48-hour retention constants."""

from datetime import timedelta

import pytest

from ai_trend_pipeline.story_candidate import CandidateStatus
from ai_trend_pipeline.story_generation import StoryGenerationStatus
from ai_trend_pipeline.story_status import (
    DEFAULT_STORY_REVIEW_TIMEOUT_S,
    STORY_UI_RETENTION,
    PrimaryStoryStatus,
    primary_story_status,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (CandidateStatus.AWAITING_REVIEW.value, PrimaryStoryStatus.PENDING),
        (CandidateStatus.APPROVED.value, PrimaryStoryStatus.WRITING),
        (CandidateStatus.REJECTED.value, PrimaryStoryStatus.CLOSED),
        (CandidateStatus.EXPIRED.value, PrimaryStoryStatus.CLOSED),
        (StoryGenerationStatus.RESEARCHING.value, PrimaryStoryStatus.WRITING),
        (StoryGenerationStatus.ANALYZING.value, PrimaryStoryStatus.WRITING),
        (StoryGenerationStatus.WRITING.value, PrimaryStoryStatus.WRITING),
        (StoryGenerationStatus.CHECKING.value, PrimaryStoryStatus.WRITING),
        (StoryGenerationStatus.REVIEW.value, PrimaryStoryStatus.REVIEW),
        (StoryGenerationStatus.READY.value, PrimaryStoryStatus.READY),
        (StoryGenerationStatus.REJECTED.value, PrimaryStoryStatus.CLOSED),
        (StoryGenerationStatus.UNSUPPORTED.value, PrimaryStoryStatus.CLOSED),
        (StoryGenerationStatus.FAILED.value, PrimaryStoryStatus.CLOSED),
        (StoryGenerationStatus.EXPIRED.value, PrimaryStoryStatus.CLOSED),
    ],
)
def test_every_candidate_and_story_state_maps_to_one_primary_status(
    value: str, expected: PrimaryStoryStatus
) -> None:
    assert primary_story_status(value) is expected


def test_unknown_status_is_closed_and_retention_is_48_hours() -> None:
    assert primary_story_status("something-new") is PrimaryStoryStatus.CLOSED
    assert primary_story_status("") is PrimaryStoryStatus.CLOSED
    assert STORY_UI_RETENTION == timedelta(hours=48)
    assert DEFAULT_STORY_REVIEW_TIMEOUT_S == 172800
