"""Standalone, idempotent research activities.

They are read-only: no workflow start, no Temporal client and no registration (053 owns wiring).
Expected URL, content and dataset outcomes are returned as values; transient HTTP failures are
raised so Temporal retries them.
"""

from datetime import UTC, datetime

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.owid import FetchOwidInput, OwidActivities, match_dataset
from ai_trend_pipeline.research.fetch import HostResolver, SafeFetcher
from ai_trend_pipeline.research.models import (
    MAX_CONTEXTS,
    MAX_DATASET_COUNTRIES,
    MAX_DATASET_NOTE_CHARS,
    MAX_DATASET_NOTES,
    MAX_DATASET_ROWS,
    MAX_EVIDENCE,
    MAX_EVIDENCE_EXCERPT_CHARS,
    MAX_FAILURES,
    MAX_NOTES,
    MAX_TITLE_CHARS,
    MAX_TOTAL_CONTEXT_CHARS,
    MIN_CONTEXT_TEXT_CHARS,
    DatasetEvidence,
    DatasetRow,
    EvidenceKind,
    EvidenceSource,
    FetchContextInput,
    FetchContextOutput,
    MatchEvidenceInput,
    MatchEvidenceOutput,
    ResearchFailure,
    ResearchFailureCode,
    ResearchInput,
    ResearchNoteCode,
    ResearchOutput,
    ResearchStatus,
    StoryContext,
    evidence_id_for,
)

_ACCEPT_ENCODING = "identity"
_ACTIVITY_FAILURE_MESSAGES = {
    ResearchFailureCode.NO_SOURCE_URL: "The story has no source URL.",
    ResearchFailureCode.INSUFFICIENT_EVIDENCE: "The article text is too short.",
}


def make_research_client(settings: Settings) -> httpx.AsyncClient:
    """Build the article client with redirects and environment proxies disabled."""
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
        headers={
            "accept-encoding": _ACCEPT_ENCODING,
            "user-agent": settings.research_user_agent,
        },
        timeout=settings.research_fetch_timeout_s,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _host(url: str) -> str:
    return (httpx.URL(url).host or "").lower().rstrip(".")


def _failure(code: ResearchFailureCode, host: str | None = None) -> ResearchFailure:
    return ResearchFailure(code=code, message=_ACTIVITY_FAILURE_MESSAGES[code], host=host)


def _bounded_notes(notes: list[ResearchNoteCode]) -> list[ResearchNoteCode]:
    unique = list(dict.fromkeys(notes))
    return unique[:MAX_NOTES]


def _bounded_failures(failures: list[ResearchFailure]) -> list[ResearchFailure]:
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[ResearchFailure] = []
    for failure in failures:
        key = (failure.code.value, failure.message, failure.host)
        if key in seen:
            continue
        seen.add(key)
        unique.append(failure)
    return unique[:MAX_FAILURES]


class StoryResearchActivities:
    """Fetch article context, match supporting data, and produce bounded research output."""

    def __init__(
        self,
        article_client: httpx.AsyncClient,
        owid_client: httpx.AsyncClient,
        settings: Settings,
        *,
        resolver: HostResolver | None = None,
    ) -> None:
        self._settings = settings
        self._owid_client = owid_client
        self._fetcher = SafeFetcher(
            article_client,
            timeout_s=settings.research_fetch_timeout_s,
            max_response_bytes=settings.research_max_response_bytes,
            max_redirects=settings.research_max_redirects,
            user_agent=settings.research_user_agent,
            resolver=resolver,
        )

    @activity.defn
    async def fetch_context(self, data: FetchContextInput) -> FetchContextOutput:
        """Fetch one approved context URL; expected outcomes are values, not exceptions."""
        return await self._fetcher.fetch(str(data.url), approved_hosts=data.approved_hosts)

    @activity.defn
    async def match_owid_evidence(self, data: MatchEvidenceInput) -> MatchEvidenceOutput:
        """Deterministically match one catalog dataset; OWID problems degrade to notes."""
        if not data.include_owid:
            return MatchEvidenceOutput(evidence=None, notes=[ResearchNoteCode.OWID_DISABLED])
        topic = f"{data.story.title} {data.story.summary} {data.story.proposed_angle}"
        dataset = match_dataset(topic)
        if dataset is None:
            return MatchEvidenceOutput(evidence=None, notes=[ResearchNoteCode.NO_CATALOG_MATCH])

        notes: list[ResearchNoteCode] = []
        try:
            table = await OwidActivities(self._owid_client).fetch_owid_dataset(
                FetchOwidInput(topic=dataset.title, since_year=2000)
            )
        except (ApplicationError, httpx.HTTPError):
            return MatchEvidenceOutput(evidence=None, notes=[ResearchNoteCode.DATASET_UNAVAILABLE])
        if not table.rows:
            return MatchEvidenceOutput(evidence=None, notes=[ResearchNoteCode.DATASET_EMPTY])

        truncated = len(table.rows) > MAX_DATASET_ROWS
        if truncated:
            notes.append(ResearchNoteCode.DATASET_TRUNCATED)
        evidence = EvidenceSource(
            evidence_id=evidence_id_for(EvidenceKind.DATASET, table.slug),
            kind=EvidenceKind.DATASET,
            title=table.title[:MAX_TITLE_CHARS],
            url=None,
            publisher=None,
            retrieved_at=_now(),
            observation_date=None,
            context_id=None,
            excerpt=None,
            dataset=DatasetEvidence(
                slug=table.slug,
                title=table.title,
                value_column=table.value_column,
                since_year=2000,
                rows=[
                    DatasetRow(entity=row.entity, code=row.code, year=row.year, value=row.value)
                    for row in table.rows[:MAX_DATASET_ROWS]
                ],
                matched_countries=table.matched_countries[:MAX_DATASET_COUNTRIES],
                notes=[note[:MAX_DATASET_NOTE_CHARS] for note in table.notes[:MAX_DATASET_NOTES]],
                truncated=truncated,
            ),
        )
        return MatchEvidenceOutput(evidence=evidence, notes=notes)

    @activity.defn
    async def research_story(self, data: ResearchInput) -> ResearchOutput:
        """Gather the primary article plus approved extras and optional OWID evidence."""
        story = data.story
        contexts: list[StoryContext] = []
        evidence: list[EvidenceSource] = []
        failures: list[ResearchFailure] = []
        notes: list[ResearchNoteCode] = []
        seen_urls: set[str] = set()

        if story.url is None:
            failures.append(_failure(ResearchFailureCode.NO_SOURCE_URL))
        else:
            primary = await self._fetcher.fetch(str(story.url))
            notes.extend(primary.notes)
            if primary.context is not None:
                contexts.append(primary.context)
                seen_urls.add(str(primary.context.url))
            elif primary.failure is not None:
                failures.append(primary.failure)

        approved = self._settings.research_extra_context_hosts
        for extra_url in data.extra_context_urls:
            host = _host(str(extra_url))
            if host not in approved:
                notes.append(ResearchNoteCode.EXTRA_CONTEXT_NOT_ALLOWED)
                continue
            try:
                extra = await self._fetcher.fetch(str(extra_url), approved_hosts=approved)
            except httpx.HTTPError:
                notes.append(ResearchNoteCode.EXTRA_CONTEXT_FETCH_FAILED)
                continue
            notes.extend(extra.notes)
            if extra.context is None:
                if extra.failure is not None:
                    failures.append(extra.failure)
                notes.append(ResearchNoteCode.EXTRA_CONTEXT_FETCH_FAILED)
                continue
            context = extra.context
            if str(context.url) in seen_urls:
                notes.append(ResearchNoteCode.DUPLICATE_CONTEXT)
                continue
            if (
                len(contexts) >= MAX_CONTEXTS
                or sum(len(item.text) for item in contexts) + len(context.text)
                > MAX_TOTAL_CONTEXT_CHARS
            ):
                notes.append(ResearchNoteCode.TEXT_BUDGET_EXHAUSTED)
                continue
            contexts.append(context)
            seen_urls.add(str(context.url))

        for context in contexts:
            evidence.append(
                EvidenceSource(
                    evidence_id=evidence_id_for(EvidenceKind.ARTICLE, context.context_id),
                    kind=EvidenceKind.ARTICLE,
                    title=(context.title or story.title)[:MAX_TITLE_CHARS],
                    url=context.url,
                    publisher=context.publisher,
                    retrieved_at=context.retrieved_at,
                    observation_date=(
                        story.published_at.date() if story.published_at is not None else None
                    ),
                    context_id=context.context_id,
                    excerpt=context.text[:MAX_EVIDENCE_EXCERPT_CHARS],
                    dataset=None,
                )
            )

        matched = await self.match_owid_evidence(
            MatchEvidenceInput(story=story, include_owid=data.include_owid)
        )
        notes.extend(matched.notes)
        if matched.evidence is not None and len(evidence) < MAX_EVIDENCE:
            evidence.append(matched.evidence)

        supported = any(len(context.text) >= MIN_CONTEXT_TEXT_CHARS for context in contexts)
        if not supported:
            failures.append(_failure(ResearchFailureCode.INSUFFICIENT_EVIDENCE))

        return ResearchOutput(
            story_id=story.candidate_id,
            status=ResearchStatus.SUPPORTED if supported else ResearchStatus.UNSUPPORTED,
            contexts=contexts,
            evidence=evidence,
            failures=_bounded_failures(failures),
            notes=_bounded_notes(notes),
            researched_at=_now(),
        )
