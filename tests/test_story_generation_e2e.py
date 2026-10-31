"""Deterministic end-to-end proof of candidate approval -> story -> final review.

No live HTTP or LLM call: HN, articles, OWID and Anthropic all use recorded fixtures through
`httpx.MockTransport`. The real Temporal workflows, activities, gateways, services and FastAPI
routes run against a local Temporal server with visibility.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from temporalio.client import WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy

from ai_trend_pipeline.services.candidates import (
    CandidateClosed,
    CandidatePage,
    DecideCandidateRequest,
    ListCandidatesRequest,
)
from ai_trend_pipeline.services.stories import (
    ListStoriesRequest,
    StoryFilter,
    StoryListPage,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CANDIDATE_TASK_QUEUE,
    StoryCandidate,
    StoryCandidateInput,
    StoryCandidateWorkflow,
    candidate_id_for,
)
from ai_trend_pipeline.story_generation import (
    EXPIRED_DETAIL,
    StoryGenerationResult,
    StoryGenerationWorkflow,
    story_id_for,
)
from ai_trend_pipeline.story_status import STORY_UI_RETENTION, PrimaryStoryStatus
from ai_trend_pipeline.web.view_models import (
    build_candidate_inbox_view,
    build_story_list_view,
    pending_nav_badge,
)
from tests.story_e2e_harness import (
    HN_ERRORS_FIXTURE,
    HN_FIXTURE,
    REVISION_HEADLINE,
    approve_form,
    assert_single_five_status_badge,
    scheduled_activity_names,
    stop_ingestion,
    story_e2e,
    wait_for_candidate_ready,
    wait_for_detail_status,
    wait_for_pending,
    wait_for_story_status,
    workflows_of_story_kind,
)


@pytest.mark.asyncio
async def test_success_unsupported_rejection_retention_and_no_duplicates() -> None:
    async with story_e2e(HN_FIXTURE, gated_external_ids=frozenset({"3001"})) as harness:
        await wait_for_pending(harness, 3)
        candidate_ids = {
            external_id: candidate_id_for(SourceName.HN, external_id)
            for external_id in ("3001", "3002", "3003")
        }
        story_ids = {
            external_id: story_id_for(candidate_ids[external_id]) for external_id in candidate_ids
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app),
            base_url="http://test",
            follow_redirects=True,
        ) as web:
            inbox = (await web.get("/candidates")).text
            for external_id in ("3001", "3002", "3003"):
                assert "Life expectancy" in inbox, external_id
            pending = (await web.get("/stories")).text
            assert pending.count('class="badge badge--muted">Pending</span>') == 3

            for external_id, decision in (
                ("3001", "approve"),
                ("3002", "approve"),
                ("3003", "reject"),
            ):
                response = await web.post(
                    f"/candidates/{candidate_ids[external_id]}/decide",
                    data={"decision": decision},
                )
                assert response.status_code == 200, external_id

            await wait_for_story_status(harness, candidate_ids["3001"], PrimaryStoryStatus.WRITING)
            writing = (await web.get("/stories")).text
            assert 'class="badge badge--progress">Writing</span>' in writing

            detail_writing = (await web.get(f"/stories/{story_ids['3001']}")).text
            assert assert_single_five_status_badge(detail_writing) == "Writing"

            harness.draft_gate.set()
            await wait_for_story_status(harness, candidate_ids["3001"], PrimaryStoryStatus.REVIEW)
            detail_review = (await web.get(f"/stories/{story_ids['3001']}")).text
            assert assert_single_five_status_badge(detail_review) == "Review"

            approved = await web.post(
                f"/stories/{story_ids['3001']}/decide",
                data=await approve_form(harness, story_ids["3001"]),
            )
            assert approved.status_code == 200
            await wait_for_story_status(harness, candidate_ids["3001"], PrimaryStoryStatus.READY)
            detail_ready = (await web.get(f"/stories/{story_ids['3001']}")).text
            assert assert_single_five_status_badge(detail_ready) == "Ready"
            assert "It is not published yet." in detail_ready

            await wait_for_story_status(harness, candidate_ids["3002"], PrimaryStoryStatus.CLOSED)
            detail_unsupported = (await web.get(f"/stories/{story_ids['3002']}")).text
            assert assert_single_five_status_badge(detail_unsupported) == "Closed"
            assert "The original article is no longer available." in detail_unsupported

        workflows = await workflows_of_story_kind(harness)
        for external_id in ("3001", "3002"):
            assert workflows.count(candidate_ids[external_id]) == 1
            assert workflows.count(story_ids[external_id]) == 1
        assert workflows.count(candidate_ids["3003"]) == 1
        assert workflows.count(story_ids["3003"]) == 0

        with pytest.raises(CandidateClosed):
            await harness.candidate_service.decide(
                DecideCandidateRequest(candidate_id=candidate_ids["3001"], decision="approve")
            )
        assert (await workflows_of_story_kind(harness)).count(story_ids["3001"]) == 1

        story_handle: WorkflowHandle[StoryGenerationWorkflow, StoryGenerationResult] = (
            harness.env.client.get_workflow_handle_for(
                StoryGenerationWorkflow.run, story_ids["3001"]
            )
        )
        activity_names = await scheduled_activity_names(story_handle)
        assert activity_names
        for forbidden in ("post_chart_to_bluesky", "render_chart_png", "write_chart_spec"):
            assert forbidden not in activity_names

        detail = await harness.detail_service.show(story_ids["3001"], now=datetime.now(UTC))
        future = detail.created_at + STORY_UI_RETENTION + timedelta(seconds=1)
        expired_stories = await harness.story_service.list_stories(ListStoriesRequest(), now=future)
        assert expired_stories.stories == []
        expired_candidates = await harness.candidate_service.list_pending(
            ListCandidatesRequest(), now=future
        )
        assert expired_candidates.candidates == []

        candidate_view = build_candidate_inbox_view(CandidatePage(), now=future)
        story_view = build_story_list_view(
            StoryListPage(), now=future, filter=StoryFilter.ALL, page_token=None
        )
        assert candidate_view.count_text == ""
        assert pending_nav_badge(CandidatePage(), first_page=True) is None
        assert story_view.count_text == ""

        candidate_handle = harness.env.client.get_workflow_handle_for(
            StoryCandidateWorkflow.run, candidate_ids["3001"]
        )
        assert (await candidate_handle.describe()).id == candidate_ids["3001"]
        assert (await story_handle.describe()).id == story_ids["3001"]

        await stop_ingestion(harness)


@pytest.mark.asyncio
async def test_story_rejection_revision_and_failure_after_retry() -> None:
    async with story_e2e(HN_ERRORS_FIXTURE) as harness:
        await wait_for_pending(harness, 3)
        candidate_ids = {
            external_id: candidate_id_for(SourceName.HN, external_id)
            for external_id in ("4001", "4002", "4003")
        }
        story_ids = {
            external_id: story_id_for(candidate_ids[external_id]) for external_id in candidate_ids
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app),
            base_url="http://test",
            follow_redirects=True,
        ) as web:
            for external_id in ("4001", "4002", "4003"):
                response = await web.post(
                    f"/candidates/{candidate_ids[external_id]}/decide",
                    data={"decision": "approve"},
                )
                assert response.status_code == 200, external_id

            await wait_for_story_status(harness, candidate_ids["4001"], PrimaryStoryStatus.REVIEW)
            rejected = await web.post(
                f"/stories/{story_ids['4001']}/decide", data={"decision": "reject"}
            )
            assert rejected.status_code == 200
            await wait_for_story_status(harness, candidate_ids["4001"], PrimaryStoryStatus.CLOSED)
            closed = await harness.detail_service.show(story_ids["4001"], now=datetime.now(UTC))
            assert closed.internal_status == "rejected"
            rejected_page = (await web.get(f"/stories/{story_ids['4001']}")).text
            assert assert_single_five_status_badge(rejected_page) == "Closed"
            assert "rejected at final review" in rejected_page

            await wait_for_story_status(harness, candidate_ids["4002"], PrimaryStoryStatus.REVIEW)
            assert [call for call in harness.calls.drafts if call[0] == "4002"] == [
                ("4002", 0),
                ("4002", 1),
            ]
            assert [call for call in harness.calls.critiques if call[0] == "4002"] == [
                ("4002", 0),
                ("4002", 1),
            ]
            revision_detail = await harness.detail_service.show(
                story_ids["4002"], now=datetime.now(UTC)
            )
            assert revision_detail.status is PrimaryStoryStatus.REVIEW
            final = await web.post(
                f"/stories/{story_ids['4002']}/decide",
                data=await approve_form(harness, story_ids["4002"]),
            )
            assert final.status_code == 200
            await wait_for_story_status(harness, candidate_ids["4002"], PrimaryStoryStatus.READY)
            ready_detail = await harness.detail_service.show(
                story_ids["4002"], now=datetime.now(UTC)
            )
            assert ready_detail.package is not None
            assert ready_detail.package.headline == REVISION_HEADLINE

            await wait_for_story_status(harness, candidate_ids["4003"], PrimaryStoryStatus.CLOSED)
            failed = await harness.detail_service.show(story_ids["4003"], now=datetime.now(UTC))
            assert failed.internal_status == "failed"
            assert failed.status_detail == "The research step failed after retries."
            assert harness.calls.article_paths.count("news.example/retry") == 3
            failed_page = (await web.get(f"/stories/{story_ids['4003']}")).text
            assert assert_single_five_status_badge(failed_page) == "Closed"

        for story_filter in StoryFilter:
            page = await harness.story_service.list_stories(
                ListStoriesRequest(filter=story_filter), now=datetime.now(UTC)
            )
            ids = [row.candidate_id for row in page.stories]
            assert len(ids) == len(set(ids)), story_filter
            assert set(ids) <= set(candidate_ids.values()), story_filter
        all_rows = await harness.story_service.list_stories(
            ListStoriesRequest(filter=StoryFilter.ALL), now=datetime.now(UTC)
        )
        assert set(row.candidate_id for row in all_rows.stories) == set(candidate_ids.values())

        await stop_ingestion(harness)


@pytest.mark.asyncio
async def test_review_expiry_through_services_and_ui() -> None:
    async with story_e2e(HN_FIXTURE, gated_external_ids=frozenset({"5001"})) as harness:
        observed_at = datetime.now(UTC)
        candidate = StoryCandidate.model_validate(
            {
                "source": SourceName.HN,
                "external_id": "5001",
                "title": "Life expectancy keeps rising",
                "url": "https://news.example/story",
                "published_at": observed_at,
                "observed_at": observed_at,
                "summary": "Global life expectancy climbed again.",
                "proposed_angle": "Show the latest world life-expectancy figure.",
                "selection_reason": "A concrete health story with a supporting dataset.",
                "metrics": {"score": 5},
            }
        )
        candidate_id = candidate_id_for(SourceName.HN, "5001")
        story_id = story_id_for(candidate_id)
        await harness.env.client.start_workflow(
            StoryCandidateWorkflow.run,
            StoryCandidateInput(
                candidate=candidate,
                review_timeout_seconds=3600,
                story_review_timeout_seconds=1,
            ),
            id=candidate_id,
            task_queue=CANDIDATE_TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        await wait_for_candidate_ready(harness, candidate_id)
        decision = await harness.candidate_service.decide(
            DecideCandidateRequest(candidate_id=candidate_id, decision="approve")
        )
        assert decision.status == "approved"

        await wait_for_story_status(harness, candidate_id, PrimaryStoryStatus.WRITING)
        harness.draft_gate.set()
        await wait_for_detail_status(harness, story_id, PrimaryStoryStatus.REVIEW)
        await wait_for_detail_status(harness, story_id, PrimaryStoryStatus.CLOSED)

        expired = await harness.detail_service.show(story_id, now=datetime.now(UTC))
        assert expired.status is PrimaryStoryStatus.CLOSED
        assert expired.internal_status == "expired"
        assert expired.status_detail == EXPIRED_DETAIL

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app),
            base_url="http://test",
            follow_redirects=True,
        ) as web:
            detail_page = (await web.get(f"/stories/{story_id}")).text
            assert assert_single_five_status_badge(detail_page) == "Closed"
            assert "This story expired" in detail_page
            list_page = (await web.get("/stories")).text
            assert EXPIRED_DETAIL in list_page

        await stop_ingestion(harness)
