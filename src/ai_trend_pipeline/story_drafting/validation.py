"""Pure, LLM-free validation for story drafts and critiques.

Every check returns a bounded list of `CritiqueFinding`s with fixed English messages that contain
only category codes, fact IDs and evidence IDs. No I/O, no clock and no model: the activities run
these checks before and after the writer, and the checker can short-circuit on them.
"""

import re
from collections.abc import Iterable, Sequence

from ai_trend_pipeline.analysis.models import Fact, FactSetStatus
from ai_trend_pipeline.research.models import EvidenceKind
from ai_trend_pipeline.story_drafting.models import (
    MAX_CRITIQUE_FINDINGS,
    CritiqueCategory,
    CritiqueFinding,
    CritiqueLocation,
    CritiqueSeverity,
    DraftInput,
    StoryPackage,
)

_NUMBER = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?%?")
_URL_MARKER = re.compile(r"(?i)(?:https?://|www\.)")


def _blocking(
    category: CritiqueCategory,
    message: str,
    *,
    location: CritiqueLocation | None = None,
    fact_ids: Sequence[str] | None = None,
) -> CritiqueFinding:
    return CritiqueFinding(
        category=category,
        severity=CritiqueSeverity.BLOCKING,
        message=message,
        location=location,
        fact_ids=[] if fact_ids is None else list(fact_ids),
    )


def _bind(findings: Iterable[CritiqueFinding]) -> list[CritiqueFinding]:
    """Dedupe by (category, severity, message) keeping order, then cap at the contract limit."""
    seen: set[tuple[CritiqueCategory, CritiqueSeverity, str]] = set()
    kept: list[CritiqueFinding] = []
    for finding in findings:
        key = (finding.category, finding.severity, finding.message.casefold())
        if key in seen:
            continue
        seen.add(key)
        kept.append(finding)
        if len(kept) == MAX_CRITIQUE_FINDINGS:
            break
    return kept


def canonical_number(token: str) -> str:
    """Canonicalise a digit token: no commas, sign, percent or redundant trailing zeros."""
    value = token.replace(",", "").strip()
    if value[:1] in {"+", "-"}:
        value = value[1:]
    if value.endswith("%"):
        value = value[:-1]
    elif value[-1:].lower() == "x":
        value = value[:-1]
    if "." in value:
        whole, fraction = value.split(".", 1)
        fraction = fraction.rstrip("0")
        whole = whole.lstrip("0") or "0"
        return whole if not fraction else f"{whole}.{fraction}"
    return value.lstrip("0") or "0"


def numeric_tokens(text: str) -> list[str]:
    return [match.group(0) for match in _NUMBER.finditer(text)]


def numeric_vocabulary(texts: Iterable[str]) -> set[str]:
    return {canonical_number(token) for text in texts for token in numeric_tokens(text)}


def _fact_vocabulary(fact: Fact) -> set[str]:
    texts: list[str] = [
        fact.display.value_text,
        fact.display.label,
        fact.display.period_text,
        str(fact.period_start_year),
        str(fact.period_end_year),
    ]
    texts.extend(str(observation.year) for observation in fact.observation_dates)
    return numeric_vocabulary(texts)


def _approved_context_vocabulary(data: DraftInput) -> set[str]:
    story = data.story
    texts: list[str] = [story.title, story.summary, story.proposed_angle]
    years: list[str] = [str(story.observed_at.year)]
    if story.published_at is not None:
        years.append(str(story.published_at.year))
    for item in data.evidence:
        if item.observation_date is not None:
            years.append(str(item.observation_date.year))
    return numeric_vocabulary(texts) | set(years)


def _has_url(text: str) -> bool:
    return _URL_MARKER.search(text) is not None


def _fact_id_findings(draft: StoryPackage, facts: dict[str, Fact]) -> list[CritiqueFinding]:
    findings: list[CritiqueFinding] = []
    for key_point in draft.key_points:
        for fact_id in key_point.fact_ids:
            if fact_id not in facts:
                findings.append(
                    _blocking(
                        CritiqueCategory.MISSING_CITATION,
                        f"missing_citation: unknown fact ID {fact_id}",
                        location=CritiqueLocation.KEY_POINT,
                        fact_ids=[fact_id],
                    )
                )
    return findings


def _source_link_findings(draft: StoryPackage, data: DraftInput) -> list[CritiqueFinding]:
    findings: list[CritiqueFinding] = []
    evidence = {item.evidence_id: item for item in data.evidence}
    seen: set[str] = set()
    for link in draft.source_links:
        item = evidence.get(link.evidence_id)
        if item is None:
            findings.append(
                _blocking(
                    CritiqueCategory.SOURCE_FIDELITY,
                    f"source_fidelity: unknown evidence ID {link.evidence_id}",
                    location=CritiqueLocation.SOURCE_LINKS,
                )
            )
            continue
        if item.kind is not EvidenceKind.ARTICLE:
            findings.append(
                _blocking(
                    CritiqueCategory.SOURCE_FIDELITY,
                    f"source_fidelity: non-article evidence ID {link.evidence_id}",
                    location=CritiqueLocation.SOURCE_LINKS,
                )
            )
            continue
        if item.url is None or str(link.url) != str(item.url):
            findings.append(
                _blocking(
                    CritiqueCategory.SOURCE_FIDELITY,
                    f"source_fidelity: URL mismatch for evidence ID {link.evidence_id}",
                    location=CritiqueLocation.SOURCE_LINKS,
                )
            )
        if link.evidence_id in seen:
            findings.append(
                _blocking(
                    CritiqueCategory.SOURCE_FIDELITY,
                    f"source_fidelity: duplicate evidence ID {link.evidence_id}",
                    location=CritiqueLocation.SOURCE_LINKS,
                )
            )
        seen.add(link.evidence_id)
    return findings


def _url_in_prose_findings(draft: StoryPackage) -> list[CritiqueFinding]:
    findings: list[CritiqueFinding] = []
    prose: list[tuple[CritiqueLocation, str]] = [
        (CritiqueLocation.HEADLINE, draft.headline),
        (CritiqueLocation.SUMMARY, draft.summary),
        (CritiqueLocation.BODY, draft.body),
    ]
    prose.extend((CritiqueLocation.KEY_POINT, key_point.text) for key_point in draft.key_points)
    prose.extend((CritiqueLocation.CAVEATS, caveat) for caveat in draft.caveats)
    if draft.post_text is not None:
        prose.append((CritiqueLocation.POST_TEXT, draft.post_text))
    for location, text in prose:
        if _has_url(text):
            findings.append(
                _blocking(
                    CritiqueCategory.SOURCE_FIDELITY,
                    f"source_fidelity: URL found in {location.value}",
                    location=location,
                )
            )
    return findings


def _tokens_not_in(text: str, allowed: set[str]) -> bool:
    return any(canonical_number(token) not in allowed for token in numeric_tokens(text))


def validate_numeric_fidelity(draft: StoryPackage, data: DraftInput) -> list[CritiqueFinding]:
    """Reject numbers that are not backed by cited facts or approved candidate/evidence dates."""
    facts = {fact.fact_id: fact for fact in data.facts.facts}
    findings: list[CritiqueFinding] = []
    cited_vocabulary: set[str] = set()
    for key_point in draft.key_points:
        for fact_id in key_point.fact_ids:
            fact = facts.get(fact_id)
            if fact is not None:
                cited_vocabulary |= _fact_vocabulary(fact)
    allowed_prose = _approved_context_vocabulary(data) | cited_vocabulary

    for key_point in draft.key_points:
        tokens = numeric_tokens(key_point.text)
        if not tokens:
            continue
        if not key_point.fact_ids:
            findings.append(
                _blocking(
                    CritiqueCategory.NUMERICAL_FIDELITY,
                    "numerical_fidelity: key point number lacks a cited fact",
                    location=CritiqueLocation.KEY_POINT,
                )
            )
            findings.append(
                _blocking(
                    CritiqueCategory.MISSING_CITATION,
                    "missing_citation: key point number has no fact IDs",
                    location=CritiqueLocation.KEY_POINT,
                )
            )
            continue
        allowed: set[str] = set()
        for fact_id in key_point.fact_ids:
            fact = facts.get(fact_id)
            if fact is not None:
                allowed |= _fact_vocabulary(fact)
        if _tokens_not_in(key_point.text, allowed):
            findings.append(
                _blocking(
                    CritiqueCategory.NUMERICAL_FIDELITY,
                    "numerical_fidelity: key point number is not in its cited facts",
                    location=CritiqueLocation.KEY_POINT,
                )
            )

    prose: list[tuple[CritiqueLocation, str]] = [
        (CritiqueLocation.HEADLINE, draft.headline),
        (CritiqueLocation.SUMMARY, draft.summary),
        (CritiqueLocation.BODY, draft.body),
    ]
    prose.extend((CritiqueLocation.CAVEATS, caveat) for caveat in draft.caveats)
    if draft.post_text is not None:
        prose.append((CritiqueLocation.POST_TEXT, draft.post_text))
    for location, text in prose:
        if _tokens_not_in(text, allowed_prose):
            findings.append(
                _blocking(
                    CritiqueCategory.NUMERICAL_FIDELITY,
                    f"numerical_fidelity: number in {location.value} is not supported",
                    location=location,
                )
            )
    return _bind(findings)


def validate_draft(draft: StoryPackage, data: DraftInput) -> list[CritiqueFinding]:
    """Deterministic checks in fixed order: fact IDs, links, prose URLs, numbers, citations."""
    facts = {fact.fact_id: fact for fact in data.facts.facts}
    findings: list[CritiqueFinding] = []
    findings.extend(_fact_id_findings(draft, facts))
    findings.extend(_source_link_findings(draft, data))
    findings.extend(_url_in_prose_findings(draft))
    findings.extend(validate_numeric_fidelity(draft, data))
    if data.facts.status is FactSetStatus.AVAILABLE and not any(
        key_point.fact_ids for key_point in draft.key_points
    ):
        findings.append(
            _blocking(
                CritiqueCategory.MISSING_CITATION,
                "missing_citation: no key point cites a fact",
            )
        )
    return _bind(findings)


def merge_findings(
    deterministic: Sequence[CritiqueFinding], model: Sequence[CritiqueFinding]
) -> list[CritiqueFinding]:
    """Deterministic findings first, then model findings, deduped and capped at 8."""
    return _bind([*deterministic, *model])


def revision_guidance_for(findings: Sequence[CritiqueFinding]) -> str:
    categories = sorted(
        {
            finding.category.value
            for finding in findings
            if finding.severity is CritiqueSeverity.BLOCKING
        }
    )
    return f"Blocking issues: {', '.join(categories)}."
