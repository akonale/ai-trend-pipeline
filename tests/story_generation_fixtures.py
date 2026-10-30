"""Deterministic builders for story generation tests.

No files, network, clock or randomness: every value is fixed, so the same fixture yields the
same models on every run.
"""

from datetime import UTC, datetime

from ai_trend_pipeline.analysis.models import FactSet
from ai_trend_pipeline.research.models import (
    ResearchFailure,
    ResearchFailureCode,
    ResearchOutput,
    ResearchStatus,
    StoryInput,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import StoryCandidate, story_input_for
from ai_trend_pipeline.story_drafting import (
    CritiqueOutput,
    DraftOutput,
    StoryPackage,
)
from tests.drafting_fixtures import (
    clean_content,
    make_passing_critique,
    make_research_article_evidence,
    make_research_context,
)
from tests.fact_fixtures import (
    DATASET_EVIDENCE_ID,
    NOW,
    make_dataset_evidence,
    make_fact_set,
    make_rows,
)

OBSERVED = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def make_candidate(**overrides: object) -> StoryCandidate:
    payload: dict[str, object] = {
        "source": SourceName.HN,
        "external_id": "123",
        "title": "Life expectancy keeps rising",
        "url": "https://news.example/story",
        "published_at": datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
        "observed_at": OBSERVED,
        "summary": "Global life expectancy climbed again.",
        "proposed_angle": "Show the long-run trend.",
        "selection_reason": "Relevant and timely.",
        "metrics": {"score": 10},
    }
    payload.update(overrides)
    return StoryCandidate.model_validate(payload)


def make_story_input(**overrides: object) -> StoryInput:
    return story_input_for(make_candidate(**overrides))


def make_supported_research(story: StoryInput) -> ResearchOutput:
    context = make_research_context()
    article = make_research_article_evidence(context_id=context.context_id)
    dataset = make_dataset_evidence(make_rows({"World": {2019: 72.0, 2020: 72.5}}))
    return ResearchOutput(
        story_id=story.candidate_id,
        status=ResearchStatus.SUPPORTED,
        contexts=[context],
        evidence=[article, dataset],
        failures=[],
        notes=[],
        researched_at=NOW,
    )


def make_unsupported_research(
    story: StoryInput,
    code: ResearchFailureCode = ResearchFailureCode.NO_SOURCE_URL,
) -> ResearchOutput:
    return ResearchOutput(
        story_id=story.candidate_id,
        status=ResearchStatus.UNSUPPORTED,
        contexts=[],
        evidence=[],
        failures=[ResearchFailure(code=code, message="The story could not be researched.")],
        notes=[],
        researched_at=NOW,
    )


def make_facts(story_id: str) -> FactSet:
    return make_fact_set(story_id=story_id)


def make_package(story_id: str, **overrides: object) -> StoryPackage:
    payload: dict[str, object] = clean_content()
    payload.update({"story_id": story_id, "schema_version": 1})
    payload.update(overrides)
    return StoryPackage.model_validate(payload)


def make_draft_output(story_id: str, **overrides: object) -> DraftOutput:
    return DraftOutput(
        story_id=story_id,
        draft=make_package(story_id, **overrides),
        revision_applied=False,
        notes=[],
    )


def make_passing(story_id: str) -> CritiqueOutput:
    return make_passing_critique(story_id)


def dataset_evidence_id() -> str:
    return DATASET_EVIDENCE_ID
