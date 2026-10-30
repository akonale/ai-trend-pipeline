"""Unit tests for deterministic draft validation; no LLM or network is involved."""

from datetime import UTC, datetime

from ai_trend_pipeline.analysis.models import (
    FactKind,
    FactSet,
    FactSetStatus,
    fact_id_for,
    fact_set_id_for,
)
from ai_trend_pipeline.story_drafting import (
    MAX_CRITIQUE_FINDINGS,
    CritiqueCategory,
    CritiqueSeverity,
    StoryKeyPoint,
    StorySourceLink,
)
from ai_trend_pipeline.story_drafting.validation import (
    canonical_number,
    merge_findings,
    numeric_tokens,
    revision_guidance_for,
    validate_draft,
    validate_numeric_fidelity,
)
from tests.drafting_fixtures import (
    ARTICLE_EVIDENCE_ID,
    CANDIDATE_ID,
    DATASET_EVIDENCE_ID,
    DEFAULT_FACT_ID,
    STORY_URL,
    make_blocking_finding,
    make_clean_package,
    make_draft_input,
    make_empty_fact_set,
    make_fact_set,
)
from tests.fact_fixtures import (
    make_display,
    make_fact,
    make_story,
)

SECOND_FACT_ID = fact_id_for(
    FactKind.LATEST, ["World"], "Life expectancy", 10.0, 2020, 2020, [DATASET_EVIDENCE_ID]
)


def _link(
    evidence_id: str, label: str = "Original report", url: str = STORY_URL
) -> StorySourceLink:
    return StorySourceLink.model_validate({"evidence_id": evidence_id, "label": label, "url": url})


def _two_fact_set() -> FactSet:
    first = make_fact()
    second = make_fact(
        fact_id=SECOND_FACT_ID,
        value=10.0,
        display=make_display(value_text="10"),
    )
    return make_fact_set(
        fact_set_id=fact_set_id_for(CANDIDATE_ID, FactSetStatus.AVAILABLE, [first, second]),
        facts=[first, second],
    )


def test_clean_package_has_no_findings() -> None:
    data = make_draft_input()
    package = make_clean_package()
    assert validate_draft(package, data) == []
    assert validate_numeric_fidelity(package, data) == []


def test_invented_and_unknown_fact_ids_are_blocking() -> None:
    data = make_draft_input()
    invented = "fact-v1-0123456789abcdef"
    package = make_clean_package(key_points=[{"text": "A cited claim.", "fact_ids": [invented]}])
    findings = validate_draft(package, data)
    citations = [
        finding for finding in findings if finding.category is CritiqueCategory.MISSING_CITATION
    ]
    assert citations
    assert all(finding.severity is CritiqueSeverity.BLOCKING for finding in citations)
    assert citations[0].fact_ids == [invented]

    empty_data = make_draft_input(facts=make_empty_fact_set())
    empty_package = make_clean_package(
        key_points=[{"text": "A cited claim.", "fact_ids": [DEFAULT_FACT_ID]}]
    )
    empty_findings = validate_draft(empty_package, empty_data)
    assert any(finding.category is CritiqueCategory.MISSING_CITATION for finding in empty_findings)


def test_unsupported_number_is_blocking() -> None:
    data = make_draft_input()

    body_package = make_clean_package(body="The latest figure was 80.0 years.")
    body_findings = validate_numeric_fidelity(body_package, data)
    assert any(
        finding.category is CritiqueCategory.NUMERICAL_FIDELITY
        and finding.location is not None
        and finding.location.value == "body"
        for finding in body_findings
    )

    headline_package = make_clean_package(headline="Life expectancy reaches 99")
    assert any(
        finding.category is CritiqueCategory.NUMERICAL_FIDELITY
        for finding in validate_numeric_fidelity(headline_package, data)
    )

    key_point_package = make_clean_package(
        key_points=[
            {"text": "World life expectancy was 99.0 years in 2020.", "fact_ids": [DEFAULT_FACT_ID]}
        ]
    )
    assert any(
        finding.category is CritiqueCategory.NUMERICAL_FIDELITY
        for finding in validate_numeric_fidelity(key_point_package, data)
    )

    assert validate_numeric_fidelity(make_clean_package(), data) == []


def test_key_point_numbers_must_come_from_its_own_cited_facts() -> None:
    data = make_draft_input(facts=_two_fact_set())
    package = make_clean_package(
        key_points=[
            {"text": "The other country was 10.0 years in 2020.", "fact_ids": [DEFAULT_FACT_ID]}
        ]
    )
    findings = validate_numeric_fidelity(package, data)
    assert any(finding.category is CritiqueCategory.NUMERICAL_FIDELITY for finding in findings)

    dated_story = make_story(
        published_at=datetime(2018, 5, 1, tzinfo=UTC),
        observed_at=datetime(2019, 5, 1, tzinfo=UTC),
    )
    dated_data = make_draft_input(story=dated_story)
    dated_package = make_clean_package(
        summary="The report was published in 2018 and retrieved in 2026.",
        body="The figure was reported in 2018 and the article was retrieved in 2026.",
    )
    assert validate_numeric_fidelity(dated_package, dated_data) == []


def test_missing_citation_when_available_facts_are_unused() -> None:
    no_numbers: dict[str, object] = {
        "headline": "Life expectancy keeps rising",
        "summary": "World life expectancy is higher than in earlier decades.",
        "body": "The latest dataset shows a higher world life expectancy.",
        "caveats": ["Only one observation is available."],
        "post_text": None,
        "key_points": [{"text": "World life expectancy is higher than before.", "fact_ids": []}],
    }
    data = make_draft_input()
    findings = validate_draft(make_clean_package(**no_numbers), data)
    assert any(
        finding.category is CritiqueCategory.MISSING_CITATION
        and finding.severity is CritiqueSeverity.BLOCKING
        for finding in findings
    )

    empty_data = make_draft_input(facts=make_empty_fact_set())
    assert validate_draft(make_clean_package(**no_numbers), empty_data) == []


def test_source_links_must_match_article_evidence() -> None:
    data = make_draft_input()
    base = make_clean_package()

    unknown = base.model_copy(update={"source_links": [_link("ev-v1-aaaaaaaaaaaaaaaa", "Unknown")]})
    assert any(
        finding.category is CritiqueCategory.SOURCE_FIDELITY
        for finding in validate_draft(unknown, data)
    )

    dataset = base.model_copy(update={"source_links": [_link(DATASET_EVIDENCE_ID, "Dataset")]})
    assert any(
        finding.category is CritiqueCategory.SOURCE_FIDELITY
        for finding in validate_draft(dataset, data)
    )

    mismatched = base.model_copy(
        update={
            "source_links": [_link(ARTICLE_EVIDENCE_ID, "Mismatch", "https://other.example/story")]
        }
    )
    assert any(
        finding.category is CritiqueCategory.SOURCE_FIDELITY
        for finding in validate_draft(mismatched, data)
    )

    link = _link(ARTICLE_EVIDENCE_ID, "Duplicate")
    duplicate = base.model_copy(update={"source_links": [link, link]})
    duplicate_findings = validate_draft(duplicate, data)
    assert any(
        finding.category is CritiqueCategory.SOURCE_FIDELITY and "duplicate" in finding.message
        for finding in duplicate_findings
    )

    assert validate_draft(base, data) == []


def test_urls_in_prose_and_messages_are_rejected() -> None:
    data = make_draft_input()
    package = make_clean_package().model_copy(
        update={"body": "Read the secret at https://example.com/secret for details."}
    )
    findings = validate_draft(package, data)
    assert any(
        finding.category is CritiqueCategory.SOURCE_FIDELITY
        and finding.severity is CritiqueSeverity.BLOCKING
        for finding in findings
    )
    for finding in findings:
        assert "https://" not in finding.message
        assert "secret" not in finding.message

    www_package = make_clean_package().model_copy(
        update={"caveats": ["See www.example.com for methodology."]}
    )
    assert any(
        finding.category is CritiqueCategory.SOURCE_FIDELITY
        for finding in validate_draft(www_package, data)
    )


def test_findings_are_ordered_bounded_and_deduplicated() -> None:
    data = make_draft_input()
    invented = "fact-v1-0123456789abcdef"
    package = make_clean_package().model_copy(
        update={
            "key_points": [StoryKeyPoint(text="A cited claim.", fact_ids=[invented])],
            "source_links": [_link("ev-v1-aaaaaaaaaaaaaaaa", "Unknown")],
            "body": "Read https://example.com/secret and see 80.0 years.",
            "headline": "Life expectancy rises",
            "summary": "World life expectancy changed.",
            "caveats": ["Single observation."],
            "post_text": None,
        }
    )
    findings = validate_draft(package, data)
    assert len(findings) <= MAX_CRITIQUE_FINDINGS
    categories = [finding.category for finding in findings]
    assert categories == [
        CritiqueCategory.MISSING_CITATION,
        CritiqueCategory.SOURCE_FIDELITY,
        CritiqueCategory.SOURCE_FIDELITY,
        CritiqueCategory.NUMERICAL_FIDELITY,
    ]

    same = make_blocking_finding(message="The same problem.")
    merged = merge_findings([same], [same, same])
    assert merged == [same]

    guidance = revision_guidance_for(findings)
    assert guidance.startswith("Blocking issues: ")
    codes = [
        part.strip() for part in guidance.removeprefix("Blocking issues: ").rstrip(".").split(",")
    ]
    assert set(codes) <= {category.value for category in CritiqueCategory}


def test_numeric_canonicalisation() -> None:
    assert canonical_number("+1,234.500%") == "1234.5"
    assert canonical_number("-7") == "7"
    assert canonical_number("72.50") == "72.5"
    assert canonical_number("007") == "7"
    assert numeric_tokens("72.5% and 1,000 and -3") == ["72.5%", "1,000", "-3"]
