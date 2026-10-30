"""Contract tests for story drafting models: bounds, invariants, freezing and the pure mapper."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.analysis.models import (
    FactKind,
    FactSetStatus,
    fact_id_for,
    fact_set_id_for,
)
from ai_trend_pipeline.research import EvidenceKind, ResearchStatus
from ai_trend_pipeline.research.models import ResearchOutput
from ai_trend_pipeline.story_drafting import (
    MAX_BODY_CHARS,
    MAX_CAVEAT_CHARS,
    MAX_CAVEATS,
    MAX_DRAFT_CONTEXT_CHARS,
    MAX_DRAFT_INPUT_BYTES_TARGET,
    MAX_DRAFT_PROMPT_CHARS,
    MAX_EVIDENCE_ID_CHARS,
    MAX_HEADLINE_CHARS,
    MAX_KEY_POINT_FACT_IDS,
    MAX_KEY_POINTS,
    MAX_PACKAGE_BYTES_TARGET,
    MAX_POST_TEXT_CHARS,
    MAX_REVISION_ROUNDS,
    MAX_SOURCE_LINKS,
    MAX_STORY_ID_CHARS,
    MAX_SUMMARY_CHARS,
    CritiqueCategory,
    CritiqueFinding,
    CritiqueInput,
    CritiqueLocation,
    CritiqueOutput,
    CritiqueSeverity,
    DraftInput,
    DraftNoteCode,
    DraftOutput,
    RevisionRequest,
    StoryPackage,
    draft_input_for,
)
from tests.drafting_fixtures import (
    ARTICLE_EVIDENCE_ID,
    CANDIDATE_ID,
    CONTEXT_ID,
    CONTEXT_TEXT,
    DATASET_EVIDENCE_ID,
    DEFAULT_FACT_ID,
    NOW,
    clean_content,
    make_article_draft_evidence,
    make_clean_package,
    make_context,
    make_critique_input,
    make_dataset_draft_evidence,
    make_draft_input,
    make_empty_fact_set,
    make_failed_critique,
    make_passing_critique,
    make_research_article_evidence,
    make_research_context,
    make_research_output,
    make_revision,
    make_unsupported_fact_set,
)
from tests.fact_fixtures import (
    make_dataset_evidence,
    make_fact_set,
    make_rows,
    make_story,
)


def _key_points(*entries: tuple[str, list[str]]) -> list[dict[str, object]]:
    return [{"text": text, "fact_ids": fact_ids} for text, fact_ids in entries]


def test_story_package_is_frozen_versioned_and_bounded() -> None:
    package = make_clean_package()
    assert package.story_id == CANDIDATE_ID
    assert package.schema_version == 1
    assert package.post_text == "Life expectancy reached 72.5 years in 2020."
    with pytest.raises(ValidationError):
        package.headline = "Changed"
    with pytest.raises(ValidationError):
        StoryPackage.model_validate({**clean_content(), "extra": "nope"})

    blank_post = make_clean_package(post_text="   ")
    assert blank_post.post_text is None

    cleaned = make_clean_package(headline="  A\x00  news\n headline  ")
    assert cleaned.headline == "A news headline"

    for field, limit in (
        ("headline", MAX_HEADLINE_CHARS),
        ("summary", MAX_SUMMARY_CHARS),
        ("body", MAX_BODY_CHARS),
        ("post_text", MAX_POST_TEXT_CHARS),
    ):
        with pytest.raises(ValidationError):
            make_clean_package(**{field: "a" * (limit + 1)})
    assert MAX_HEADLINE_CHARS == 160
    assert MAX_SUMMARY_CHARS == 600
    assert MAX_BODY_CHARS == 6_000


def test_story_package_rejects_urls_in_free_text_and_bad_fact_ids() -> None:
    for field in ("headline", "summary", "body", "post_text"):
        for marker in ("https://example.com", "http://example.com", "www.example.com"):
            with pytest.raises(ValidationError):
                make_clean_package(**{field: f"Story at {marker}"})
    with pytest.raises(ValidationError):
        make_clean_package(
            caveats=["See https://example.com for more."],
        )
    with pytest.raises(ValidationError):
        make_clean_package(
            key_points=_key_points(("See www.example.com", [])),
        )

    for bad_fact_id in ("not-a-fact", "fact-v1-ABC", DEFAULT_FACT_ID + "0"):
        with pytest.raises(ValidationError):
            make_clean_package(key_points=[{"text": "A fact", "fact_ids": [bad_fact_id]}])
    with pytest.raises(ValidationError):
        make_clean_package(
            key_points=[{"text": "A fact", "fact_ids": [DEFAULT_FACT_ID, DEFAULT_FACT_ID]}]
        )
    with pytest.raises(ValidationError):
        make_clean_package(
            key_points=[
                {"text": "Too many", "fact_ids": [DEFAULT_FACT_ID] * (MAX_KEY_POINT_FACT_IDS + 1)}
            ]
        )
    with pytest.raises(ValidationError):
        make_clean_package(
            key_points=_key_points(
                ("One", []),
                ("Two", []),
                ("Three", []),
                ("Four", []),
                ("Five", []),
                ("Six", []),
            )
        )
    with pytest.raises(ValidationError):
        make_clean_package(
            caveats=["a"] * (MAX_CAVEATS + 1),
        )
    with pytest.raises(ValidationError):
        make_clean_package(caveats=["a" * (MAX_CAVEAT_CHARS + 1)])
    with pytest.raises(ValidationError):
        make_clean_package(
            source_links=[
                {"evidence_id": ARTICLE_EVIDENCE_ID, "label": "One", "url": "https://a.example"},
                {"evidence_id": ARTICLE_EVIDENCE_ID, "label": "Two", "url": "https://b.example"},
            ]
        )
    assert MAX_KEY_POINTS == 5
    assert MAX_SOURCE_LINKS == 4
    assert MAX_STORY_ID_CHARS == 64


def test_draft_input_bounds_contexts_and_evidence_and_requires_supported_facts() -> None:
    assert len(make_draft_input().contexts) == 1

    contexts = [make_context(context_id=f"ctx-{index}", text="a" * 300) for index in range(4)]
    with pytest.raises(ValidationError):
        make_draft_input(contexts=contexts)
    with pytest.raises(ValidationError):
        make_context(text="a" * 4_001)
    with pytest.raises(ValidationError):
        make_draft_input(
            contexts=[
                make_context(context_id=f"ctx-{index}", text="a" * 3_000) for index in range(3)
            ]
        )
    with pytest.raises(ValidationError):
        make_draft_input(
            contexts=[make_context(context_id=CONTEXT_ID), make_context(context_id=CONTEXT_ID)]
        )
    assert MAX_DRAFT_CONTEXT_CHARS == 8_000

    evidence = [make_article_draft_evidence(evidence_id=f"ev-{index}") for index in range(5)]
    with pytest.raises(ValidationError):
        make_draft_input(evidence=evidence)
    with pytest.raises(ValidationError):
        make_draft_input(
            evidence=[
                make_article_draft_evidence(evidence_id="ev-same"),
                make_article_draft_evidence(evidence_id="ev-same"),
            ]
        )

    mismatched = make_fact_set(story_id="candidate-v1-ffffffffffffffff")
    with pytest.raises(ValidationError):
        make_draft_input(facts=mismatched)
    with pytest.raises(ValidationError):
        make_draft_input(facts=make_unsupported_fact_set())
    with pytest.raises(ValidationError):
        make_draft_input(contexts=[])
    with pytest.raises(ValidationError):
        make_draft_input(evidence=[make_dataset_draft_evidence()])


def test_revision_request_and_critique_rounds_are_capped_at_one() -> None:
    for bad_round in (0, 2):
        with pytest.raises(ValidationError):
            RevisionRequest(
                prior_draft=make_clean_package(),
                critique=make_failed_critique(),
                round=bad_round,
            )
    with pytest.raises(ValidationError):
        RevisionRequest(
            prior_draft=make_clean_package(),
            critique=make_passing_critique(),
            round=1,
        )
    with pytest.raises(ValidationError):
        RevisionRequest(
            prior_draft=make_clean_package(),
            critique=make_failed_critique(story_id="candidate-v1-ffffffffffffffff"),
            round=1,
        )
    assert make_revision().round == 1
    assert MAX_REVISION_ROUNDS == 1

    for bad_round in (2, -1):
        with pytest.raises(ValidationError):
            make_critique_input(round=bad_round)
    assert make_critique_input(round=0).round == 0
    assert make_critique_input(round=1).round == 1


def test_critique_output_invariants() -> None:
    blocking = CritiqueFinding(
        category=CritiqueCategory.UNSUPPORTED_CLAIM,
        severity=CritiqueSeverity.BLOCKING,
        message="A claim is unsupported.",
        location=CritiqueLocation.BODY,
    )
    with pytest.raises(ValidationError):
        CritiqueOutput(story_id=CANDIDATE_ID, passed=True, findings=[blocking])
    with pytest.raises(ValidationError):
        CritiqueOutput(story_id=CANDIDATE_ID, passed=False, findings=[])
    with pytest.raises(ValidationError):
        CritiqueOutput(
            story_id=CANDIDATE_ID,
            passed=False,
            findings=[blocking],
            revision_guidance=None,
        )
    blank = CritiqueOutput(
        story_id=CANDIDATE_ID,
        passed=True,
        findings=[],
        revision_guidance="   ",
    )
    assert blank.revision_guidance is None
    with pytest.raises(ValidationError):
        CritiqueOutput(
            story_id=CANDIDATE_ID,
            passed=False,
            findings=[blocking] * 9,
            revision_guidance="Fix it.",
        )
    with pytest.raises(ValidationError):
        CritiqueFinding(
            category=CritiqueCategory.SOURCE_FIDELITY,
            severity=CritiqueSeverity.ADVISORY,
            message="See https://example.com for details.",
        )
    passed = CritiqueOutput(story_id=CANDIDATE_ID, passed=True)
    assert passed.findings == []


def test_draft_input_for_maps_and_truncates_research_deterministically() -> None:
    contexts = [
        make_research_context(context_id="ctx-1", text="a" * 5_000),
        make_research_context(context_id="ctx-2", text="b" * 4_000),
        make_research_context(context_id="ctx-3", text="c" * 300),
    ]
    research = make_research_output(
        contexts=contexts,
        evidence=[
            make_research_article_evidence(context_id="ctx-1"),
            make_dataset_evidence(make_rows({"World": {2020: 72.5}})),
        ],
    )
    facts = make_fact_set()
    first = draft_input_for(make_story(), research, facts)
    assert [context.context_id for context in first.contexts] == ["ctx-1", "ctx-2"]
    assert len(first.contexts[0].text) == 4_000
    assert len(first.contexts[1].text) == 4_000
    assert first.context_truncated is True
    assert first.revision is None

    article = next(item for item in first.evidence if item.evidence_id == ARTICLE_EVIDENCE_ID)
    assert article.excerpt == "A short excerpt about the latest figure."
    dataset = next(item for item in first.evidence if item.evidence_id == DATASET_EVIDENCE_ID)
    assert dataset.dataset_row_count == 1
    assert not hasattr(dataset, "rows")

    second = draft_input_for(make_story(), research, facts)
    assert first.model_dump_json() == second.model_dump_json()

    untruncated = make_research_output(
        contexts=[make_research_context()],
        evidence=[make_research_article_evidence()],
    )
    assert draft_input_for(make_story(), untruncated, facts).context_truncated is False

    unsupported = make_research_output(contexts=[], evidence=[], status=ResearchStatus.UNSUPPORTED)
    with pytest.raises(ValueError):
        draft_input_for(make_story(), unsupported, facts)
    with pytest.raises(ValueError):
        draft_input_for(make_story(), research, make_unsupported_fact_set())
    with pytest.raises(ValueError):
        draft_input_for(make_story(), research, make_fact_set(story_id="candidate-v1-ffff"))


def test_draft_input_for_dedupes_and_caps_evidence() -> None:
    duplicate = make_research_article_evidence()
    second = make_research_article_evidence(
        evidence_id="ev-v1-aaaaaaaaaaaaaaaa", context_id=CONTEXT_ID
    )
    third = make_research_article_evidence(
        evidence_id="ev-v1-bbbbbbbbbbbbbbbb", context_id=CONTEXT_ID
    )
    fourth = make_research_article_evidence(
        evidence_id="ev-v1-cccccccccccccccc", context_id=CONTEXT_ID
    )
    fifth = make_research_article_evidence(
        evidence_id="ev-v1-dddddddddddddddd", context_id=CONTEXT_ID
    )
    research = ResearchOutput.model_construct(
        story_id=CANDIDATE_ID,
        status=ResearchStatus.SUPPORTED,
        contexts=[make_research_context(text=CONTEXT_TEXT)],
        evidence=[duplicate, duplicate, second, third, fourth, fifth],
        failures=[],
        notes=[],
        researched_at=NOW,
    )
    mapped = draft_input_for(make_story(), research, make_fact_set())
    assert [item.evidence_id for item in mapped.evidence] == [
        ARTICLE_EVIDENCE_ID,
        "ev-v1-aaaaaaaaaaaaaaaa",
        "ev-v1-bbbbbbbbbbbbbbbb",
        "ev-v1-cccccccccccccccc",
    ]


def test_models_round_trip_json_and_respect_byte_targets() -> None:
    package = StoryPackage.model_validate(
        {
            "story_id": CANDIDATE_ID,
            "schema_version": 1,
            "headline": "a" * MAX_HEADLINE_CHARS,
            "summary": "b" * MAX_SUMMARY_CHARS,
            "body": "c" * MAX_BODY_CHARS,
            "key_points": [
                {"text": "d" * 400, "fact_ids": [DEFAULT_FACT_ID]} for _ in range(MAX_KEY_POINTS)
            ],
            "source_links": [
                {
                    "evidence_id": f"ev-v1-{index:016x}",
                    "label": "label" * 24,
                    "url": f"https://example.com/{index}",
                }
                for index in range(MAX_SOURCE_LINKS)
            ],
            "caveats": ["e" * MAX_CAVEAT_CHARS for _ in range(MAX_CAVEATS)],
            "post_text": "f" * MAX_POST_TEXT_CHARS,
        }
    )
    dumped = package.model_dump_json()
    assert StoryPackage.model_validate_json(dumped) == package
    assert len(dumped.encode("utf-8")) <= MAX_PACKAGE_BYTES_TARGET

    contexts = [
        make_context(context_id=f"ctx-{index}", text=str(index) * 4_000) for index in range(2)
    ]
    evidence = [
        make_article_draft_evidence(
            evidence_id=f"ev-v1-{index:016x}",
            excerpt="x" * 500,
            url=f"https://example.com/{'a' * 900}{index}",
        )
        for index in range(3)
    ] + [make_dataset_draft_evidence(row_count=300)]
    data = make_draft_input(contexts=contexts, evidence=evidence)
    encoded = data.model_dump_json()
    assert DraftInput.model_validate_json(encoded) == data
    assert len(encoded.encode("utf-8")) <= MAX_DRAFT_INPUT_BYTES_TARGET
    assert MAX_DRAFT_PROMPT_CHARS == 100_000


def test_draft_output_notes_and_story_ids() -> None:
    package = make_clean_package()
    output = DraftOutput(
        story_id=CANDIDATE_ID,
        draft=package,
        revision_applied=False,
        notes=[DraftNoteCode.CONTEXT_TRUNCATED, DraftNoteCode.ARTICLE_ONLY],
    )
    assert output.notes == [
        DraftNoteCode.CONTEXT_TRUNCATED,
        DraftNoteCode.ARTICLE_ONLY,
    ]
    with pytest.raises(ValidationError):
        DraftOutput(
            story_id=CANDIDATE_ID,
            draft=package,
            revision_applied=False,
            notes=[DraftNoteCode.ARTICLE_ONLY, DraftNoteCode.ARTICLE_ONLY],
        )
    with pytest.raises(ValidationError):
        DraftOutput(
            story_id="candidate-v1-ffffffffffffffff",
            draft=package,
            revision_applied=False,
        )


def test_inputs_reject_extra_fields() -> None:
    payload = make_draft_input().model_dump()
    payload["extra"] = "nope"
    with pytest.raises(ValidationError):
        DraftInput.model_validate(payload)
    critique_payload = make_critique_input().model_dump()
    critique_payload["extra"] = "nope"
    with pytest.raises(ValidationError):
        CritiqueInput.model_validate(critique_payload)
    for model in (DraftInput, CritiqueInput):
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("frozen") is True


def test_fact_set_helpers_used_by_fixtures_are_consistent() -> None:
    facts = make_fact_set()
    assert facts.status is FactSetStatus.AVAILABLE
    assert facts.story_id == CANDIDATE_ID
    assert [fact.fact_id for fact in facts.facts] == [DEFAULT_FACT_ID]
    empty = make_empty_fact_set()
    assert empty.status is FactSetStatus.EMPTY
    second_id = fact_id_for(
        FactKind.LATEST, ["World"], "Life expectancy", 10.0, 2020, 2020, [DATASET_EVIDENCE_ID]
    )
    assert second_id != DEFAULT_FACT_ID
    assert fact_set_id_for(CANDIDATE_ID, FactSetStatus.EMPTY, []).startswith("fset-v1-")
    assert json.loads(facts.model_dump_json())["status"] == "available"
    assert NOW.tzinfo is UTC
    assert isinstance(datetime(2026, 10, 30, 12, tzinfo=UTC), datetime)
    assert EvidenceKind.ARTICLE.value == "article"
    assert MAX_EVIDENCE_ID_CHARS == 80
