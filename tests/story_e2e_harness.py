"""Shared end-to-end harness: real Temporal workers, services and routes, no live calls."""

import asyncio
import json
import re
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import anthropic
import httpx
import httpx2
from fastapi import FastAPI
from temporalio.api.enums.v1 import EventType
from temporalio.client import Client, WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ai_trend_pipeline.analysis.activities import FactActivities
from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.hn import HN_BASE_URL
from ai_trend_pipeline.media.providers import MediaRenderer
from ai_trend_pipeline.owid import OWID_BASE_URL
from ai_trend_pipeline.research.activities import StoryResearchActivities
from ai_trend_pipeline.services.candidates import (
    CandidateClosed,
    CandidateNotFound,
    CandidateService,
    ListCandidatesRequest,
)
from ai_trend_pipeline.services.ingestion import source_ingestion_input
from ai_trend_pipeline.services.publishing import StoryPublishingService
from ai_trend_pipeline.services.stories import (
    ListStoriesRequest,
    StoryDetailNotFound,
    StoryDetailService,
    StoryStatusService,
)
from ai_trend_pipeline.services.temporal_gateway import (
    TemporalCandidateGateway,
    TemporalStoryDetailGateway,
    TemporalStoryPublishingGateway,
    TemporalStoryStatusGateway,
)
from ai_trend_pipeline.source_ingestion import (
    SOURCE_INGESTION_TASK_QUEUE,
    SourceIngestionResult,
    SourceIngestionWorkflow,
    source_ingestion_workflow_id,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.sources.hn import HNSourceActivities
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_SOURCE_ATTRIBUTE,
    CANDIDATE_STATUS_ATTRIBUTE,
    CANDIDATE_TASK_QUEUE,
    StoryCandidateWorkflow,
)
from ai_trend_pipeline.story_drafting.critique import StoryCritiqueActivities
from ai_trend_pipeline.story_drafting.writing import StoryDraftActivities
from ai_trend_pipeline.story_generation import (
    STORY_TASK_QUEUE,
    StoryGenerationResult,
    StoryGenerationWorkflow,
)
from ai_trend_pipeline.story_publishing import STORY_PUBLISHING_TASK_QUEUE, StoryPublishingWorkflow
from ai_trend_pipeline.story_selection import StorySelectionActivities
from ai_trend_pipeline.story_status import PrimaryStoryStatus
from ai_trend_pipeline.story_visibility import (
    STORY_CANDIDATE_ID_ATTRIBUTE,
    STORY_CREATED_AT_ATTRIBUTE,
    STORY_SOURCE_ATTRIBUTE,
    STORY_STATUS_ATTRIBUTE,
    STORY_STATUS_DETAIL_ATTRIBUTE,
    STORY_TITLE_ATTRIBUTE,
    STORY_UPDATED_AT_ATTRIBUTE,
)
from ai_trend_pipeline.temporal_client import ensure_story_search_attributes
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import (
    get_candidate_service,
    get_story_detail_service,
    get_story_publishing_service,
    get_story_status_service,
)
from ai_trend_pipeline.web.view_models import (
    STORY_STATUS_LABELS,
)
from tests.publishing_fixtures import FakePds, make_activities
from tests.story_generation_fixtures import make_media_worker

FIXTURES = Path(__file__).parent / "fixtures"
ARTICLE_HTML = (FIXTURES / "research" / "article_supported.html").read_text(encoding="utf-8")
OWID_CSV = (FIXTURES / "owid_story_e2e.csv").read_text(encoding="utf-8")
PUBLIC_IP = "93.184.216.34"

HN_FIXTURE = "hn_source_poll_story_e2e.json"
HN_ERRORS_FIXTURE = "hn_source_poll_story_e2e_errors.json"
SELECTION_FIXTURE = "llm_select_story_candidates_story_e2e.json"
SELECTION_ERRORS_FIXTURE = "llm_select_story_candidates_story_e2e_errors.json"
DRAFT_CLEAN_FIXTURE = "llm_draft_story_clean.json"
DRAFT_REVISION_FIXTURE = "llm_draft_story_revision.json"
CRITIQUE_PASS_FIXTURE = "llm_critique_story_pass.json"
CRITIQUE_MISSING_CAVEAT_FIXTURE = "llm_critique_story_missing_caveat.json"

REVISION_HEADLINE = "World life expectancy was 72.5 years in 2020"
EXTERNAL_IDS = ("3001", "3002", "3003", "4001", "4002", "4003", "5001")
FIVE_LABELS = frozenset(label for label in STORY_STATUS_LABELS.values())
STATUS_BADGE = re.compile(r'class="badge badge--[a-z]+">([^<]+)</span>')


def fixture_json(name: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


async def public_resolver(_host: str) -> Sequence[str]:
    return [PUBLIC_IP]


def external_id_in(prompt: str) -> str:
    for external_id in EXTERNAL_IDS:
        if f'"external_id":"{external_id}"' in prompt:
            return external_id
    raise AssertionError("prompt carries no known external id")


@dataclass
class Calls:
    article_paths: list[str] = field(default_factory=lambda: [])
    owid_requests: int = 0
    drafts: list[tuple[str, int]] = field(default_factory=lambda: [])
    critiques: list[tuple[str, int]] = field(default_factory=lambda: [])


@dataclass
class Harness:
    env: WorkflowEnvironment
    ingestion: WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult]
    candidate_service: CandidateService
    story_service: StoryStatusService
    detail_service: StoryDetailService
    app: FastAPI
    calls: Calls
    draft_gate: asyncio.Event
    media_dir: Path
    publishing_service: StoryPublishingService


def hn_client(fixture_name: str) -> httpx.AsyncClient:
    data = fixture_json(fixture_name)
    topstories = cast(list[int], data["topstories"])
    items = cast(dict[str, object], data["items"])

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v0/topstories.json":
            return httpx.Response(200, json=topstories)
        prefix, suffix = "/v0/item/", ".json"
        if path.startswith(prefix) and path.endswith(suffix):
            key = path[len(prefix) : -len(suffix)]
            if key in items:
                return httpx.Response(200, content=json.dumps(items[key]))
        raise AssertionError(f"unexpected HN URL: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=HN_BASE_URL)


def article_client(calls: Calls) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.article_paths.append(f"{request.url.host}{request.url.path}")
        headers = {"content-type": "text/html; charset=utf-8"}
        if request.url.path == "/story":
            return httpx.Response(200, text=ARTICLE_HTML, headers=headers)
        if request.url.path == "/missing":
            return httpx.Response(404, headers=headers)
        if request.url.path == "/retry":
            return httpx.Response(500, headers=headers)
        raise AssertionError(f"unexpected article URL: {request.url}")

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
    )


def owid_client(calls: Calls) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.owid_requests += 1
        if request.url.path != "/grapher/life-expectancy.csv":
            raise AssertionError(f"unexpected OWID URL: {request.url}")
        return httpx.Response(200, text=OWID_CSV)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=OWID_BASE_URL)


def anthropic_client(
    calls: Calls,
    draft_gate: asyncio.Event,
    gated_external_ids: frozenset[str],
) -> anthropic.AsyncAnthropic:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        payload = cast(dict[str, object], json.loads(request.content))
        tools = cast(list[dict[str, object]], payload["tools"])
        tool_name = cast(str, tools[0]["name"])
        messages = cast(list[dict[str, object]], payload["messages"])
        prompt = cast(str, messages[0]["content"])

        if tool_name == "select_story_candidates":
            name = (
                SELECTION_ERRORS_FIXTURE if '"external_id":"4001"' in prompt else SELECTION_FIXTURE
            )
            return httpx2.Response(200, json=fixture_json(name))

        external_id = external_id_in(prompt)
        if tool_name == "draft_story":
            revision = "Previous draft (revise once):" in prompt
            round_number = 1 if revision else 0
            calls.drafts.append((external_id, round_number))
            if external_id in gated_external_ids and not revision:
                await draft_gate.wait()
            body_name = (
                DRAFT_REVISION_FIXTURE
                if external_id == "4002" and revision
                else DRAFT_CLEAN_FIXTURE
            )
            return httpx2.Response(200, json=fixture_json(body_name))

        if tool_name == "critique_story":
            round_number = 1 if "Round: 1" in prompt else 0
            calls.critiques.append((external_id, round_number))
            body_name = (
                CRITIQUE_MISSING_CAVEAT_FIXTURE
                if external_id == "4002" and round_number == 0
                else CRITIQUE_PASS_FIXTURE
            )
            return httpx2.Response(200, json=fixture_json(body_name))

        raise AssertionError(f"unexpected tool: {tool_name}")

    return anthropic.AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


@asynccontextmanager
async def story_e2e(
    hn_fixture: str,
    *,
    gated_external_ids: frozenset[str] = frozenset(),
    pds: FakePds | None = None,
    renderer: MediaRenderer | None = None,
) -> AsyncGenerator[Harness, None]:
    """The full stack. With `pds`, a publishing worker also runs the real Bluesky activity."""
    # The SDK default for search_attributes contains an unresolved generic; typed keys are safe.
    env = await WorkflowEnvironment.start_local(  # pyright: ignore[reportUnknownMemberType]
        data_converter=pydantic_data_converter,
        search_attributes=[
            CANDIDATE_STATUS_ATTRIBUTE,
            CANDIDATE_SOURCE_ATTRIBUTE,
            STORY_STATUS_ATTRIBUTE,
            STORY_TITLE_ATTRIBUTE,
            STORY_SOURCE_ATTRIBUTE,
            STORY_CANDIDATE_ID_ATTRIBUTE,
            STORY_CREATED_AT_ATTRIBUTE,
            STORY_UPDATED_AT_ATTRIBUTE,
            STORY_STATUS_DETAIL_ATTRIBUTE,
        ],
    )
    await ensure_story_search_attributes(env.client)
    settings = Settings(source_poll_interval_s=30)
    calls = Calls()
    draft_gate = asyncio.Event()
    hn = hn_client(hn_fixture)
    article = article_client(calls)
    owid = owid_client(calls)
    llm = anthropic_client(calls, draft_gate, gated_external_ids)
    research = StoryResearchActivities(article, owid, settings, resolver=public_resolver)
    source = HNSourceActivities(hn)
    selection = StorySelectionActivities(llm)
    drafting = StoryDraftActivities(llm)
    critique = StoryCritiqueActivities(llm)
    facts = FactActivities()

    candidate_service = CandidateService(TemporalCandidateGateway(env.client), settings)
    story_service = StoryStatusService(TemporalStoryStatusGateway(env.client))
    publishing_gateway = TemporalStoryPublishingGateway(env.client)
    detail_service = StoryDetailService(
        TemporalStoryDetailGateway(env.client), publications=publishing_gateway
    )
    publishing_service = StoryPublishingService(publishing_gateway)
    app = create_app(settings)

    def candidate_dependency() -> CandidateService:
        return candidate_service

    def story_status_dependency() -> StoryStatusService:
        return story_service

    def story_detail_dependency() -> StoryDetailService:
        return detail_service

    app.dependency_overrides[get_candidate_service] = candidate_dependency
    app.dependency_overrides[get_story_status_service] = story_status_dependency
    app.dependency_overrides[get_story_detail_service] = story_detail_dependency

    def story_publishing_dependency() -> StoryPublishingService:
        return publishing_service

    app.dependency_overrides[get_story_publishing_service] = story_publishing_dependency

    workflow_id = source_ingestion_workflow_id(SourceName.HN)
    async with env, hn, article, owid:
        with (
            ThreadPoolExecutor(max_workers=4) as executor,
            tempfile.TemporaryDirectory() as media_dir,
        ):
            bluesky = make_activities(
                pds or FakePds(), Path(media_dir), clock=lambda: datetime.now(UTC)
            )
            async with (
                make_media_worker(env, Path(media_dir), renderer),
                Worker(
                    env.client,
                    task_queue=STORY_PUBLISHING_TASK_QUEUE,
                    workflows=[StoryPublishingWorkflow],
                    activities=[bluesky.publish_story_to_bluesky] if pds is not None else [],
                ),
                Worker(
                    env.client,
                    task_queue=SOURCE_INGESTION_TASK_QUEUE,
                    workflows=[SourceIngestionWorkflow],
                    activities=[source.poll_hn_source, selection.select_story_candidates],
                ),
                Worker(
                    env.client,
                    task_queue=CANDIDATE_TASK_QUEUE,
                    workflows=[StoryCandidateWorkflow],
                ),
                Worker(
                    env.client,
                    task_queue=STORY_TASK_QUEUE,
                    workflows=[StoryGenerationWorkflow],
                    activities=[
                        research.research_story,
                        facts.calculate_facts,
                        drafting.draft_story,
                        critique.critique_story,
                    ],
                    activity_executor=executor,
                ),
            ):
                client: Client = env.client
                await client.start_workflow(
                    SourceIngestionWorkflow.run,
                    source_ingestion_input(settings, SourceName.HN),
                    id=workflow_id,
                    task_queue=SOURCE_INGESTION_TASK_QUEUE,
                )
                ingestion: WorkflowHandle[SourceIngestionWorkflow, SourceIngestionResult] = (
                    client.get_workflow_handle(workflow_id, result_type=SourceIngestionResult)
                )
                yield Harness(
                    env=env,
                    ingestion=ingestion,
                    candidate_service=candidate_service,
                    story_service=story_service,
                    detail_service=detail_service,
                    app=app,
                    calls=calls,
                    draft_gate=draft_gate,
                    media_dir=Path(media_dir),
                    publishing_service=publishing_service,
                )


async def stop_ingestion(harness: Harness) -> None:
    await harness.ingestion.signal(SourceIngestionWorkflow.stop)
    result = await harness.ingestion.result()
    assert result.reason == "stopped"


async def wait_until(
    predicate: Callable[[], Awaitable[bool]], *, timeout: float = 20.0, what: str
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


async def wait_for_pending(harness: Harness, count: int) -> None:
    async def ready() -> bool:
        page = await harness.candidate_service.list_pending(
            ListCandidatesRequest(), now=datetime.now(UTC)
        )
        return len(page.candidates) >= count

    await wait_until(ready, what=f"{count} pending candidates")


async def approve_form(harness: Harness, story_id: str) -> dict[str, str]:
    """The approve form fields, including the content hash the review page would post."""
    detail = await harness.detail_service.show(story_id, now=datetime.now(UTC))
    assert detail.content_hash is not None
    return {"decision": "approve", "content_hash": detail.content_hash}


async def wait_for_story_status(
    harness: Harness, candidate_id: str, status: PrimaryStoryStatus
) -> None:
    async def reached() -> bool:
        page = await harness.story_service.list_stories(ListStoriesRequest(), now=datetime.now(UTC))
        return any(
            row.candidate_id == candidate_id and row.status is status for row in page.stories
        )

    await wait_until(reached, what=f"story {candidate_id} to reach {status.value}")


async def wait_for_detail_status(
    harness: Harness, detail_id: str, status: PrimaryStoryStatus
) -> None:
    async def reached() -> bool:
        try:
            detail = await harness.detail_service.show(detail_id, now=datetime.now(UTC))
        except StoryDetailNotFound:
            return False
        return detail.status is status

    await wait_until(reached, what=f"detail {detail_id} to reach {status.value}")


async def wait_for_candidate_ready(harness: Harness, candidate_id: str) -> None:
    async def ready() -> bool:
        try:
            await harness.candidate_service.show(candidate_id)
        except (CandidateClosed, CandidateNotFound):
            return False
        return True

    await wait_until(ready, what=f"candidate {candidate_id} to be reviewable")


async def scheduled_activity_names(
    handle: WorkflowHandle[StoryGenerationWorkflow, StoryGenerationResult],
) -> list[str]:
    history = await handle.fetch_history()
    names: list[str] = []
    for event in history.events:
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            names.append(event.activity_task_scheduled_event_attributes.activity_type.name)
    return names


async def workflows_of_story_kind(harness: Harness) -> list[str]:
    query = "(WorkflowType = 'StoryCandidateWorkflow' OR WorkflowType = 'StoryGenerationWorkflow')"
    return [execution.id async for execution in harness.env.client.list_workflows(query)]


def status_stamps(html: str) -> list[str]:
    return [stamp for stamp in STATUS_BADGE.findall(html) if stamp in FIVE_LABELS]


def assert_single_five_status_badge(html: str) -> str:
    stamps = status_stamps(html)
    assert len(stamps) == 1, stamps
    for internal in ("awaiting_review", "researching", "analyzing", "writing", "checking"):
        assert f">{internal}<" not in html
    return stamps[0]
