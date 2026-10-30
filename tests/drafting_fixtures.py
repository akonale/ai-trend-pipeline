"""Deterministic builders for story drafting tests.

No files, network, clock or randomness: every value is fixed, so the same input yields the same
models and prompts on every run.
"""

from datetime import date, datetime

from ai_trend_pipeline.analysis.models import FactNoteCode, FactSet, FactSetStatus
from ai_trend_pipeline.research.models import (
    EvidenceKind,
    EvidenceSource,
    ResearchFailure,
    ResearchFailureCode,
    ResearchOutput,
    ResearchStatus,
    StoryContext,
    StoryInput,
)
from ai_trend_pipeline.story_drafting.models import (
    STORY_PACKAGE_SCHEMA_VERSION,
    CritiqueCategory,
    CritiqueFinding,
    CritiqueInput,
    CritiqueLocation,
    CritiqueOutput,
    CritiqueSeverity,
    DraftContext,
    DraftEvidence,
    DraftInput,
    RevisionRequest,
    StoryKeyPoint,
    StoryPackage,
    StorySourceLink,
)
from tests.fact_fixtures import (
    ARTICLE_EVIDENCE_ID,
    CANDIDATE_ID,
    CONTEXT_ID,
    DATASET_EVIDENCE_ID,
    DEFAULT_FACT_ID,
    NOW,
    make_fact_set,
    make_story,
)

STORY_URL = "https://news.example/story"
ARTICLE_TITLE = "Life expectancy keeps rising"
OBSERVATION_DATE = date(2026, 10, 30)
CONTEXT_TEXT = (
    "World life expectancy reached 72.5 years in 2020, according to the latest dataset. The "
    "article reports a single headline figure and does not describe a longer trend. Analysts "
    "say the number should be read with care because only one observation is available for the "
    "period described here, and the source does not explain the methodology behind the figure."
)
REVISION_GUIDANCE = "Soften the headline so it does not imply a trend from one observation."


def make_context(
    *,
    context_id: str = CONTEXT_ID,
    text: str = CONTEXT_TEXT,
    title: str | None = ARTICLE_TITLE,
    publisher: str = "news.example",
    url: str = STORY_URL,
    retrieved_at: datetime = NOW,
) -> DraftContext:
    return DraftContext.model_validate(
        {
            "context_id": context_id,
            "title": title,
            "publisher": publisher,
            "url": url,
            "text": text,
            "retrieved_at": retrieved_at,
        }
    )


def make_article_draft_evidence(
    *,
    evidence_id: str = ARTICLE_EVIDENCE_ID,
    url: str = STORY_URL,
    excerpt: str = "A short excerpt about the latest figure.",
) -> DraftEvidence:
    return DraftEvidence.model_validate(
        {
            "evidence_id": evidence_id,
            "kind": EvidenceKind.ARTICLE,
            "title": ARTICLE_TITLE,
            "url": url,
            "publisher": "news.example",
            "observation_date": OBSERVATION_DATE,
            "excerpt": excerpt,
            "dataset_slug": None,
            "dataset_title": None,
            "dataset_since_year": None,
            "dataset_row_count": None,
        }
    )


def make_dataset_draft_evidence(
    *,
    evidence_id: str = DATASET_EVIDENCE_ID,
    row_count: int = 1,
) -> DraftEvidence:
    return DraftEvidence.model_validate(
        {
            "evidence_id": evidence_id,
            "kind": EvidenceKind.DATASET,
            "title": "Life expectancy",
            "url": None,
            "publisher": None,
            "observation_date": None,
            "excerpt": None,
            "dataset_slug": "life-expectancy",
            "dataset_title": "Life expectancy",
            "dataset_since_year": 2000,
            "dataset_row_count": row_count,
        }
    )


def make_empty_fact_set() -> FactSet:
    return make_fact_set(
        status=FactSetStatus.EMPTY,
        facts=[],
        evidence_ids=[],
        dataset_slug=None,
        notes=[],
    )


def make_unsupported_fact_set() -> FactSet:
    return make_fact_set(
        status=FactSetStatus.UNSUPPORTED,
        research_status=ResearchStatus.UNSUPPORTED,
        facts=[],
        evidence_ids=[],
        dataset_slug=None,
        notes=[FactNoteCode.RESEARCH_UNSUPPORTED],
    )


def make_draft_input(
    *,
    story: StoryInput | None = None,
    facts: FactSet | None = None,
    contexts: list[DraftContext] | None = None,
    evidence: list[DraftEvidence] | None = None,
    context_truncated: bool = False,
    revision: RevisionRequest | None = None,
) -> DraftInput:
    return DraftInput(
        story=make_story() if story is None else story,
        facts=make_fact_set() if facts is None else facts,
        contexts=[make_context()] if contexts is None else contexts,
        evidence=(
            [make_article_draft_evidence(), make_dataset_draft_evidence()]
            if evidence is None
            else evidence
        ),
        context_truncated=context_truncated,
        revision=revision,
    )


def clean_content() -> dict[str, object]:
    return {
        "headline": "Life expectancy reaches 72.5 years",
        "summary": (
            "World life expectancy reached 72.5 years in 2020, the latest figure in the dataset."
        ),
        "body": (
            "The latest available figure for the world was 72.5 years in 2020.\n\n"
            "The single observation is a headline number rather than a long-run trend."
        ),
        "key_points": [
            {
                "text": "World life expectancy was 72.5 years in 2020.",
                "fact_ids": [DEFAULT_FACT_ID],
            }
        ],
        "source_links": [
            {
                "evidence_id": ARTICLE_EVIDENCE_ID,
                "label": "Original report",
                "url": STORY_URL,
            }
        ],
        "caveats": ["The figure is based on a single available observation."],
        "post_text": "Life expectancy reached 72.5 years in 2020.",
    }


def make_clean_package(**overrides: object) -> StoryPackage:
    data: dict[str, object] = clean_content()
    data.update({"story_id": CANDIDATE_ID, "schema_version": STORY_PACKAGE_SCHEMA_VERSION})
    data.update(overrides)
    return StoryPackage.model_validate(data)


def make_key_point(
    text: str = "World life expectancy was 72.5 years in 2020.",
    fact_ids: list[str] | None = None,
) -> StoryKeyPoint:
    return StoryKeyPoint(text=text, fact_ids=[DEFAULT_FACT_ID] if fact_ids is None else fact_ids)


def make_source_link(
    evidence_id: str = ARTICLE_EVIDENCE_ID,
    label: str = "Original report",
    url: str = STORY_URL,
) -> StorySourceLink:
    return StorySourceLink.model_validate({"evidence_id": evidence_id, "label": label, "url": url})


def make_blocking_finding(
    *,
    category: CritiqueCategory = CritiqueCategory.HEADLINE_OVERSTATEMENT,
    message: str = "The headline overstates what the fact supports.",
    location: CritiqueLocation | None = CritiqueLocation.HEADLINE,
    fact_ids: list[str] | None = None,
) -> CritiqueFinding:
    return CritiqueFinding(
        category=category,
        severity=CritiqueSeverity.BLOCKING,
        message=message,
        location=location,
        fact_ids=[] if fact_ids is None else fact_ids,
    )


def make_failed_critique(
    *,
    story_id: str = CANDIDATE_ID,
    findings: list[CritiqueFinding] | None = None,
    revision_guidance: str = REVISION_GUIDANCE,
) -> CritiqueOutput:
    return CritiqueOutput(
        story_id=story_id,
        findings=[make_blocking_finding()] if findings is None else findings,
        passed=False,
        revision_guidance=revision_guidance,
    )


def make_passing_critique(story_id: str = CANDIDATE_ID) -> CritiqueOutput:
    return CritiqueOutput(story_id=story_id, passed=True, findings=[], revision_guidance=None)


def make_revision(
    *,
    prior_draft: StoryPackage | None = None,
    critique: CritiqueOutput | None = None,
) -> RevisionRequest:
    return RevisionRequest(
        prior_draft=make_clean_package() if prior_draft is None else prior_draft,
        critique=make_failed_critique() if critique is None else critique,
        round=1,
    )


def make_critique_input(
    *,
    draft_input: DraftInput | None = None,
    draft: StoryPackage | None = None,
    round: int = 0,
) -> CritiqueInput:
    data = make_draft_input() if draft_input is None else draft_input
    return CritiqueInput(
        draft_input=data,
        draft=make_clean_package() if draft is None else draft,
        round=round,
    )


def make_research_output(
    *,
    contexts: list[StoryContext],
    evidence: list[EvidenceSource],
    status: ResearchStatus = ResearchStatus.SUPPORTED,
    story_id: str = CANDIDATE_ID,
) -> ResearchOutput:
    return ResearchOutput(
        story_id=story_id,
        status=status,
        contexts=contexts,
        evidence=evidence,
        failures=(
            []
            if status is ResearchStatus.SUPPORTED
            else [
                ResearchFailure(
                    code=ResearchFailureCode.INSUFFICIENT_EVIDENCE,
                    message="The article text is too short.",
                )
            ]
        ),
        notes=[],
        researched_at=NOW,
    )


def make_research_context(
    *,
    context_id: str = CONTEXT_ID,
    text: str = CONTEXT_TEXT,
    url: str = STORY_URL,
) -> StoryContext:
    return StoryContext.model_validate(
        {
            "context_id": context_id,
            "requested_url": url,
            "url": url,
            "title": ARTICLE_TITLE,
            "publisher": "news.example",
            "content_type": "text/html",
            "text": text,
            "truncated": False,
            "retrieved_at": NOW,
        }
    )


def make_research_article_evidence(
    *,
    evidence_id: str = ARTICLE_EVIDENCE_ID,
    context_id: str = CONTEXT_ID,
) -> EvidenceSource:
    return EvidenceSource.model_validate(
        {
            "evidence_id": evidence_id,
            "kind": EvidenceKind.ARTICLE,
            "title": ARTICLE_TITLE,
            "url": STORY_URL,
            "publisher": "news.example",
            "retrieved_at": NOW,
            "observation_date": OBSERVATION_DATE,
            "context_id": context_id,
            "excerpt": "A short excerpt about the latest figure.",
        }
    )
