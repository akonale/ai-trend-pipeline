"""Contract, ID and prompt-bound tests for story generation."""

import hashlib
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from temporalio.contrib.pydantic import pydantic_data_converter

from ai_trend_pipeline.analysis.models import (
    CalculationLineage,
    Fact,
    FactCaveatCode,
    FactDisplay,
    FactKind,
    FactMethod,
    FactSet,
    FactSetStatus,
    SourceReference,
    fact_set_id_for,
)
from ai_trend_pipeline.research.models import (
    DatasetEvidence,
    EvidenceKind,
    EvidenceSource,
    ResearchNoteCode,
    ResearchStatus,
    StoryContext,
    evidence_id_for,
)
from ai_trend_pipeline.story_candidate import candidate_id_for
from ai_trend_pipeline.story_drafting import (
    MAX_BODY_CHARS,
    MAX_CAVEAT_CHARS,
    MAX_CAVEATS,
    MAX_CONTEXTS,
    MAX_DRAFT_CONTEXT_CHARS,
    MAX_DRAFT_EVIDENCE,
    MAX_DRAFT_PROMPT_CHARS,
    MAX_HEADLINE_CHARS,
    MAX_KEY_POINT_CHARS,
    MAX_KEY_POINT_FACT_IDS,
    MAX_KEY_POINTS,
    MAX_SOURCE_LABEL_CHARS,
    MAX_SOURCE_LINKS,
    MAX_SUMMARY_CHARS,
    CritiqueInput,
    DraftInput,
    RevisionRequest,
    StoryPackage,
    critique,
    draft_input_for,
    writing,
)
from ai_trend_pipeline.story_generation import (
    STORY_DRAFT_MAX_FACTS,
    FinalStoryDecision,
    StoryGenerationInput,
    StoryGenerationResult,
    StoryGenerationState,
    StoryGenerationStatus,
    story_id_for,
)
from ai_trend_pipeline.story_status import DEFAULT_STORY_REVIEW_TIMEOUT_S
from ai_trend_pipeline.story_visibility import (
    MAX_STATUS_DETAIL_CHARS,
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
    bounded_detail,
    story_start_attributes,
    story_status_updates,
)
from tests.drafting_fixtures import (
    ARTICLE_EVIDENCE_ID,
    STORY_URL,
    make_failed_critique,
)
from tests.fact_fixtures import DATASET_EVIDENCE_ID, make_rows
from tests.story_generation_fixtures import (
    OBSERVED,
    make_candidate,
    make_story_input,
    make_supported_research,
)


def test_story_id_is_deterministic_versioned_and_candidate_specific() -> None:
    candidate_id = "candidate-v1-0123456789abcdef"
    expected = hashlib.sha256(candidate_id.encode("utf-8")).hexdigest()[:16]
    story_id = story_id_for(candidate_id)
    assert story_id == f"story-v1-{expected}"
    assert story_id_for(candidate_id) == story_id
    assert story_id_for("candidate-v1-fedcba9876543210") != story_id


def test_story_input_matches_candidate_and_detection_time() -> None:
    candidate = make_candidate()
    story = make_story_input()
    candidate_id = candidate_id_for(candidate.source, candidate.external_id)
    assert story.candidate_id == candidate_id
    assert story.source is candidate.source
    assert story.external_id == candidate.external_id
    assert story.title == candidate.title
    assert str(story.url) == str(candidate.url)
    assert story.published_at == candidate.published_at
    assert story.observed_at == candidate.observed_at
    assert story.summary == candidate.summary
    assert story.proposed_angle == candidate.proposed_angle
    assert story.selection_reason == candidate.selection_reason
    assert story.metrics == candidate.metrics

    data = StoryGenerationInput(
        candidate_id=story.candidate_id,
        story=story,
        story_created_at=story.observed_at,
    )
    assert data.story_created_at == story.observed_at
    assert data.review_timeout_seconds == DEFAULT_STORY_REVIEW_TIMEOUT_S

    with pytest.raises(ValidationError):
        StoryGenerationInput(
            candidate_id="candidate-v1-ffffffffffffffff",
            story=story,
            story_created_at=story.observed_at,
        )
    with pytest.raises(ValidationError):
        StoryGenerationInput(
            candidate_id=story.candidate_id,
            story=story,
            story_created_at=story.observed_at.replace(tzinfo=None),
        )
    with pytest.raises(ValidationError):
        StoryGenerationInput(
            candidate_id=story.candidate_id,
            story=story,
            story_created_at=story.observed_at + timedelta(seconds=1),
        )


def test_story_generation_input_bounds_and_serializes_under_16kb() -> None:
    story = make_story_input(
        external_id="e" * 128,
        title="t" * 200,
        url="https://example.com/" + "a" * 900,
        summary="s" * 500,
        proposed_angle="p" * 500,
        selection_reason="r" * 500,
        metrics={"one": 1, "two": 2, "three": 3, "four": 4},
    )
    for timeout in (1, 604800):
        data = StoryGenerationInput(
            candidate_id=story.candidate_id,
            story=story,
            story_created_at=story.observed_at,
            review_timeout_seconds=timeout,
        )
        assert data.review_timeout_seconds == timeout
    for timeout in (0, 604801):
        with pytest.raises(ValidationError):
            StoryGenerationInput(
                candidate_id=story.candidate_id,
                story=story,
                story_created_at=story.observed_at,
                review_timeout_seconds=timeout,
            )

    data = StoryGenerationInput(
        candidate_id=story.candidate_id,
        story=story,
        story_created_at=story.observed_at,
        review_timeout_seconds=604800,
    )
    converter = pydantic_data_converter.payload_converter
    payload = converter.to_payload(data)
    assert len(payload.data) < 16 * 1024
    assert converter.from_payload(payload, StoryGenerationInput) == data


def _result_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "ready",
        "story_id": "story-v1-0123456789abcdef",
        "candidate_id": "candidate-v1-0123456789abcdef",
        "story_created_at": OBSERVED,
        "updated_at": OBSERVED,
        "status_detail": None,
        "package": None,
        "critique": None,
        "decided_by": None,
        "note": "",
        "decided_at": None,
    }
    payload.update(overrides)
    return payload


def test_state_and_result_models_are_frozen_bounded_and_forbid_extra_fields() -> None:
    state = StoryGenerationState()
    assert state.status is StoryGenerationStatus.RESEARCHING
    assert state.story_id == ""
    assert state.package is None and state.critique is None
    assert state.status_detail is None

    for status in ("ready", "rejected", "unsupported", "failed", "expired"):
        result = StoryGenerationResult.model_validate(_result_payload(status=status))
        assert result.status == status
    for status in ("researching", "review", "nope"):
        with pytest.raises(ValidationError):
            StoryGenerationResult.model_validate(_result_payload(status=status))

    assert StoryGenerationResult.model_validate(_result_payload()).package is None
    with pytest.raises(ValidationError):
        StoryGenerationState(status_detail="x" * 201)
    with pytest.raises(ValidationError):
        StoryGenerationState.model_validate({"extra": "x"})
    with pytest.raises(ValidationError):
        StoryGenerationResult.model_validate({**_result_payload(), "extra": "x"})
    with pytest.raises(ValidationError):
        state.status = StoryGenerationStatus.FAILED
    result = StoryGenerationResult.model_validate(_result_payload())
    with pytest.raises(ValidationError):
        result.note = "changed"
    with pytest.raises(ValidationError):
        StoryGenerationInput.model_validate(
            {
                "candidate_id": "candidate-v1-0123456789abcdef",
                "story": make_story_input().model_dump(),
                "story_created_at": OBSERVED,
                "extra": "x",
            }
        )


def test_final_story_decision_strips_and_bounds() -> None:
    decision = FinalStoryDecision(note="  please ship  ", decided_by="  alice  ")
    assert decision.note == "please ship"
    assert decision.decided_by == "alice"
    assert FinalStoryDecision(decided_by="   ").decided_by is None
    assert FinalStoryDecision().note == ""
    assert FinalStoryDecision(note="n" * 500).note == "n" * 500
    with pytest.raises(ValidationError):
        FinalStoryDecision(note="n" * 501)
    with pytest.raises(ValidationError):
        FinalStoryDecision(decided_by="d" * 321)
    with pytest.raises(ValidationError):
        FinalStoryDecision.model_validate({"extra": "x"})
    with pytest.raises(ValidationError):
        decision.note = "changed"


def test_status_detail_cleaner_is_bounded_and_control_free() -> None:
    assert MAX_STATUS_DETAIL_CHARS == 200
    assert bounded_detail("  a\n\n\tb   c  ") == "a b c"
    assert bounded_detail("\x00a\x07b\x1fc") == "abc"
    assert bounded_detail("x" * 250) == "x" * 200
    assert bounded_detail("   \n\t ") is None
    assert bounded_detail(None) is None


def test_story_visibility_helpers_set_all_keys_and_types() -> None:
    updated_at = OBSERVED + timedelta(seconds=5)
    attributes = story_start_attributes(
        "researching",
        "A title",
        "hn",
        "candidate-v1-0123456789abcdef",
        OBSERVED,
        updated_at,
    )
    assert attributes.get(STORY_STATUS_ATTRIBUTE, None) == "researching"
    assert attributes.get(STORY_TITLE_ATTRIBUTE, None) == "A title"
    assert attributes.get(STORY_SOURCE_ATTRIBUTE, None) == "hn"
    assert attributes.get(STORY_CANDIDATE_ID_ATTRIBUTE, None) == "candidate-v1-0123456789abcdef"
    assert attributes.get(STORY_CREATED_AT_ATTRIBUTE, None) == OBSERVED
    assert attributes.get(STORY_UPDATED_AT_ATTRIBUTE, None) == updated_at
    assert attributes.get(STORY_STATUS_DETAIL_ATTRIBUTE, None) is None

    updates = story_status_updates("ready", updated_at)
    assert [update.key.name for update in updates] == ["StoryStatus", "StoryUpdatedAt"]
    assert [update.value for update in updates] == ["ready", updated_at]
    detailed = story_status_updates("expired", updated_at, "  The review window closed. ")
    assert [update.key.name for update in detailed] == [
        "StoryStatus",
        "StoryUpdatedAt",
        "StoryStatusDetail",
    ]
    assert detailed[-1].value == "The review window closed."


def _maximal_fact(index: int, evidence_ids: list[str]) -> Fact:
    fact_id = f"fact-v1-{index:016x}"
    return Fact(
        fact_id=fact_id,
        schema_version=1,
        kind=FactKind.LATEST,
        entities=["E" * 100, "F" * 100],
        metric="m" * 200,
        value=float(index + 1),
        unit="%",
        observation_dates=[
            datetime(2020, 1, 1).date(),
            datetime(2021, 1, 1).date(),
            datetime(2022, 1, 1).date(),
            datetime(2023, 1, 1).date(),
        ],
        period_start_year=2020,
        period_end_year=2023,
        sources=[
            SourceReference.model_validate(
                {
                    "evidence_id": evidence_id,
                    "kind": (
                        EvidenceKind.ARTICLE
                        if evidence_id == ARTICLE_EVIDENCE_ID
                        else EvidenceKind.DATASET
                    ),
                    "title": "s" * 200,
                    "url": STORY_URL,
                    "publisher": "p" * 253,
                }
            )
            for evidence_id in evidence_ids
        ],
        lineage=CalculationLineage(
            calculation=FactKind.LATEST,
            input_evidence_ids=evidence_ids,
            dataset_slug="d" * 100,
            value_column="v" * 100,
            entities=[f"{index}".ljust(100, "e") for index in range(20)],
            entity_count=20,
            start_year=2020,
            end_year=2023,
            baseline_year=2020,
            baseline_value=1.0,
            row_count=300,
            formula="f" * 200,
            method=FactMethod.SERIES_ENDPOINT,
            parameters={f"p{i}": float(i) for i in range(6)},
        ),
        caveats=[FactCaveatCode.SINGLE_OBSERVATION],
        display=FactDisplay(
            label="l" * 160,
            value_text="v" * 120,
            subject="s" * 120,
            period_text="p" * 40,
        ),
    )


def _maximal_draft_input() -> DraftInput:
    story = make_story_input()
    evidence_ids = [ARTICLE_EVIDENCE_ID, DATASET_EVIDENCE_ID]
    contexts = [
        StoryContext.model_validate(
            {
                "context_id": f"ctx-v1-{index:016x}",
                "requested_url": f"https://news.example/story-{index}",
                "url": f"https://news.example/story-{index}",
                "title": "t" * 300,
                "publisher": "news.example",
                "content_type": "text/html",
                "text": "x" * (MAX_DRAFT_CONTEXT_CHARS // MAX_CONTEXTS),
                "truncated": False,
                "retrieved_at": OBSERVED,
            }
        )
        for index in range(MAX_CONTEXTS)
    ]
    article_evidence = [
        EvidenceSource.model_validate(
            {
                "evidence_id": evidence_id_for(EvidenceKind.ARTICLE, context.context_id),
                "kind": EvidenceKind.ARTICLE,
                "title": "t" * 300,
                "url": str(context.url),
                "publisher": "news.example",
                "retrieved_at": OBSERVED,
                "observation_date": datetime(2020, 1, 1).date(),
                "context_id": context.context_id,
                "excerpt": "x" * 500,
            }
        )
        for context in contexts
    ]
    dataset = EvidenceSource(
        evidence_id=DATASET_EVIDENCE_ID,
        kind=EvidenceKind.DATASET,
        title="Life expectancy",
        url=None,
        publisher=None,
        retrieved_at=OBSERVED,
        observation_date=None,
        context_id=None,
        excerpt=None,
        dataset=DatasetEvidence(
            slug="life-expectancy",
            title="Life expectancy",
            value_column="life_expectancy_0",
            since_year=2000,
            rows=make_rows({"World": {2019: 72.0, 2020: 72.5}}),
            matched_countries=[],
            notes=[],
            truncated=False,
        ),
    )
    evidence = [*article_evidence, dataset][:MAX_DRAFT_EVIDENCE]
    evidence_ids = [item.evidence_id for item in evidence]
    facts = [_maximal_fact(index, evidence_ids) for index in range(STORY_DRAFT_MAX_FACTS)]
    fact_set = FactSet(
        fact_set_id=fact_set_id_for(story.candidate_id, FactSetStatus.AVAILABLE, facts),
        story_id=story.candidate_id,
        status=FactSetStatus.AVAILABLE,
        research_status=ResearchStatus.SUPPORTED,
        facts=facts,
        evidence_ids=evidence_ids,
        notes=[],
        dataset_slug="life-expectancy",
    )
    research = make_supported_research(story).model_copy(
        update={
            "contexts": contexts,
            "evidence": evidence,
            "notes": [ResearchNoteCode.NO_CATALOG_MATCH],
        }
    )
    draft_input = draft_input_for(story, research, fact_set)
    package = StoryPackage.model_validate(
        {
            "story_id": story.candidate_id,
            "headline": "h" * MAX_HEADLINE_CHARS,
            "summary": "s" * MAX_SUMMARY_CHARS,
            "body": "b" * MAX_BODY_CHARS,
            "key_points": [
                {
                    "text": "k" * MAX_KEY_POINT_CHARS,
                    "fact_ids": [fact.fact_id for fact in facts[:MAX_KEY_POINT_FACT_IDS]],
                }
                for _ in range(MAX_KEY_POINTS)
            ],
            "source_links": [
                {
                    "evidence_id": evidence_ids[index % len(evidence_ids)],
                    "label": "l" * MAX_SOURCE_LABEL_CHARS,
                    "url": f"https://news.example/link-{index}",
                }
                for index in range(MAX_SOURCE_LINKS)
            ],
            "caveats": ["c" * MAX_CAVEAT_CHARS for _ in range(MAX_CAVEATS)],
            "post_text": "p" * 300,
        }
    )
    revision = RevisionRequest(
        prior_draft=package,
        critique=make_failed_critique(story_id=story.candidate_id),
        round=1,
    )
    return draft_input.model_copy(update={"revision": revision})


def test_maximal_draft_prompt_fits_the_reconciled_ceiling() -> None:
    assert STORY_DRAFT_MAX_FACTS == 16
    assert MAX_DRAFT_PROMPT_CHARS == 100_000
    data = _maximal_draft_input()
    assert len(data.facts.facts) == STORY_DRAFT_MAX_FACTS
    assert len(data.contexts) == MAX_CONTEXTS
    revision = data.revision
    assert revision is not None
    writer_prompt = writing.build_user_prompt(data)
    checker_prompt = critique.build_user_prompt(
        CritiqueInput(draft_input=data, draft=revision.prior_draft, round=1)
    )
    assert len(writer_prompt) <= MAX_DRAFT_PROMPT_CHARS
    assert len(checker_prompt) <= MAX_DRAFT_PROMPT_CHARS
