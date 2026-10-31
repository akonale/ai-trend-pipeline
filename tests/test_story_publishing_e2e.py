"""End to end: candidate -> approval -> story and media review -> Ready -> Publish -> Published.

Real Temporal workflows (including the publishing workflow and the real Bluesky activity), the
real services and gateways and the real web routes. Bluesky is a `FakePds` behind
`httpx.MockTransport`; HN, articles, OWID and Anthropic use recorded fixtures. No live call.
"""

import asyncio
import html
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ai_trend_pipeline.media.fake import FakeMediaRenderer
from ai_trend_pipeline.media.models import MEDIA_SIGNATURES, MediaOutcome, MediaType
from ai_trend_pipeline.services.stories import (
    PUBLISHING_WORKFLOW_TYPE,
    ListStoriesRequest,
    StoryDetailExpired,
    StoryFilter,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import candidate_id_for
from ai_trend_pipeline.story_generation import story_id_for
from ai_trend_pipeline.story_status import STORY_UI_RETENTION, PrimaryStoryStatus
from ai_trend_pipeline.web.view_models import STORY_STATUS_LABELS
from tests.publishing_fixtures import DID, FakePds, respond
from tests.story_e2e_harness import (
    HN_FIXTURE,
    Harness,
    approve_form,
    stop_ingestion,
    story_e2e,
    wait_for_pending,
    wait_for_story_status,
    wait_until,
)

CANDIDATE_ID = candidate_id_for(SourceName.HN, "3001")
STORY_ID = story_id_for(CANDIDATE_ID)
LABELS = frozenset(STORY_STATUS_LABELS.values())
BADGE = re.compile(r'class="badge badge--(?!source)[a-z]+">([^<]+)</span>')
POST_LINK = re.compile(r'href="(https://bsky\.app/profile/[^"]+/post/[^"]+)"')


def web_client(harness: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app),
        base_url="http://test",
        follow_redirects=True,
    )


class Pages:
    """Every HTML page the test fetched, so one assertion covers the whole journey."""

    def __init__(self, web: httpx.AsyncClient) -> None:
        self.web = web
        self.html: list[str] = []

    async def get(self, path: str) -> str:
        text = (await self.web.get(path)).text
        self.html.append(text)
        return text

    async def post(self, path: str, data: dict[str, str]) -> str:
        text = (await self.web.post(path, data=data)).text
        self.html.append(text)
        return text


async def drive_to_ready(harness: Harness, pages: Pages) -> None:
    await wait_for_pending(harness, 3)
    await pages.get("/stories")
    await pages.post(f"/candidates/{CANDIDATE_ID}/decide", {"decision": "approve"})
    await wait_for_story_status(harness, CANDIDATE_ID, PrimaryStoryStatus.REVIEW)
    review = await pages.get(f"/stories/{STORY_ID}")
    assert "<h2>Bluesky post</h2>" in review
    await pages.post(f"/stories/{STORY_ID}/decide", await approve_form(harness, STORY_ID))
    await wait_for_story_status(harness, CANDIDATE_ID, PrimaryStoryStatus.READY)


async def content_hash(harness: Harness) -> str:
    detail = await harness.detail_service.show(STORY_ID, now=datetime.now(UTC))
    assert detail.content_hash is not None
    return detail.content_hash


async def publish(pages: Pages, harness: Harness) -> str:
    return await pages.post(
        f"/stories/{STORY_ID}/publish", {"content_hash": await content_hash(harness)}
    )


async def wait_for_published(harness: Harness) -> None:
    await wait_for_story_status(harness, CANDIDATE_ID, PrimaryStoryStatus.PUBLISHED)


async def publication_runs(harness: Harness) -> list[str]:
    query = f"WorkflowType = '{PUBLISHING_WORKFLOW_TYPE}'"
    return [e.id async for e in harness.env.client.list_workflows(query)]


def badges(page: str) -> list[str]:
    return [label for label in BADGE.findall(page) if label in LABELS]


@pytest.mark.asyncio
async def test_candidate_to_published_through_ui_creates_one_post() -> None:
    pds = FakePds()
    renderer = FakeMediaRenderer(fail_animated_times=99)
    async with story_e2e(HN_FIXTURE, pds=pds, renderer=renderer) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            ready = await pages.get(f"/stories/{STORY_ID}")
            assert badges(ready) == ["Ready"]
            assert "Publish to Bluesky" in ready
            assert pds.requests == []  # approval never publishes

            after = await publish(pages, harness)
            assert "Publishing started" in after or "Published to Bluesky" in after
            await wait_for_published(harness)

            done = await pages.get(f"/stories/{STORY_ID}")
            assert badges(done) == ["Published"]
            (link,) = POST_LINK.findall(done)
            assert link.startswith(f"https://bsky.app/profile/{DID}/post/")
            assert "View the post on Bluesky" in done
            assert "This is the package that was published." in done
            listing = await pages.get("/stories?filter=published")
            assert 'class="badge badge--published">Published</span>' in listing
            assert f'href="/stories/{STORY_ID}"' in listing
            assert "publish-v1" not in listing

            assert pds.create_ok == 1 and len(pds.records) == 1
            (record,) = pds.records.values()
            posted = record["record"]["text"]
            assert posted.endswith("Source: news.example")
            # The proof on the page is exactly what was posted.
            body, _, host = posted.rpartition("Source: ")
            assert body.strip() in html.unescape(done) and host in done
            assert pds.names().count("uploadBlob") == 1
            await stop_ingestion(harness)


@pytest.mark.asyncio
async def test_gif_story_publishes_still_card_and_text_only_story_publishes_text() -> None:
    gif_pds = FakePds()
    async with story_e2e(HN_FIXTURE, pds=gif_pds) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            detail = await harness.detail_service.show(STORY_ID, now=datetime.now(UTC))
            assert detail.package is not None and detail.package.media is not None
            assert detail.package.media.outcome is MediaOutcome.GIF
            await publish(pages, harness)
            await wait_for_published(harness)
            await stop_ingestion(harness)
    uploads = [r for r in gif_pds.requests if r.url.path.endswith("uploadBlob")]
    assert len(uploads) == 1
    assert uploads[0].content.startswith(MEDIA_SIGNATURES[MediaType.PNG])
    assert not uploads[0].content.startswith(MEDIA_SIGNATURES[MediaType.GIF])

    text_pds = FakePds()
    async with story_e2e(
        HN_FIXTURE, pds=text_pds, renderer=FakeMediaRenderer(fail_static_times=99)
    ) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            ready = await pages.get(f"/stories/{STORY_ID}")
            assert "No story card" in ready
            await publish(pages, harness)
            await wait_for_published(harness)
            await stop_ingestion(harness)
    assert text_pds.create_ok == 1
    assert "uploadBlob" not in text_pds.names()
    (record,) = text_pds.records.values()
    assert "embed" not in record["record"]


@pytest.mark.asyncio
async def test_double_click_publish_creates_one_run_and_one_record() -> None:
    pds = FakePds()
    async with story_e2e(HN_FIXTURE, pds=pds) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            form = {"content_hash": await content_hash(harness)}
            first, second = await asyncio.gather(
                web.post(f"/stories/{STORY_ID}/publish", data=form),
                web.post(f"/stories/{STORY_ID}/publish", data=form),
            )
            assert first.status_code == second.status_code == 200
            await wait_for_published(harness)
            third = await web.post(f"/stories/{STORY_ID}/publish", data=form)
            assert "Published to Bluesky." in third.text
            assert len(await publication_runs(harness)) == 1
            assert pds.create_ok == 1 and len(pds.records) == 1
            await stop_ingestion(harness)


@pytest.mark.asyncio
async def test_failed_publication_shows_not_published_then_try_again_succeeds() -> None:
    pds = FakePds()
    pds.on("createSession", respond(401, "auth_failed.json"))
    async with story_e2e(HN_FIXTURE, pds=pds) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            await publish(pages, harness)

            async def failed() -> bool:
                page = await pages.get(f"/stories/{STORY_ID}")
                return "Not published" in page

            await wait_until(failed, what="the failed publication")
            page = pages.html[-1]
            assert badges(page) == ["Ready"]
            assert "Bluesky rejected the account credentials." in page
            assert "Check the Bluesky account settings on the worker, then try again." in page
            assert "Try again</button>" in page
            assert "401" not in page and "fake.access.jwt" not in page
            assert pds.create_ok == 0

            await publish(pages, harness)
            await wait_for_published(harness)
            done = await pages.get(f"/stories/{STORY_ID}")
            assert badges(done) == ["Published"]
            assert pds.create_ok == 1
            await stop_ingestion(harness)


@pytest.mark.asyncio
async def test_ui_shows_only_six_primary_badges_throughout_and_no_internal_publish_badge() -> None:
    pds = FakePds()
    async with story_e2e(HN_FIXTURE, pds=pds) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            await publish(pages, harness)
            await pages.get(f"/stories/{STORY_ID}")
            await pages.get("/stories")
            await pages.get("/stories?filter=ready")
            await wait_for_published(harness)
            await pages.get(f"/stories/{STORY_ID}")
            await pages.get("/stories")
            await pages.get("/stories?filter=published")
            await stop_ingestion(harness)
    assert len(LABELS) == 6
    seen: set[str] = set()
    for page in pages.html:
        raw = BADGE.findall(page)
        seen.update(raw)
        assert set(raw) <= LABELS | {"hn"}, raw
        for internal in (">publishing<", ">publish_failed<", ">Publishing<", ">published<"):
            assert internal not in page
    assert {"Pending", "Review", "Ready", "Published"} <= seen


@pytest.mark.asyncio
async def test_published_story_leaves_list_and_detail_at_48_hours() -> None:
    pds = FakePds()
    async with story_e2e(HN_FIXTURE, pds=pds) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            await publish(pages, harness)
            await wait_for_published(harness)
            await stop_ingestion(harness)
        detail = await harness.detail_service.show(STORY_ID, now=datetime.now(UTC))
        created = detail.created_at
        request = ListStoriesRequest(filter=StoryFilter.PUBLISHED)
        inside = created + STORY_UI_RETENTION - timedelta(seconds=1)
        page = await harness.story_service.list_stories(request, now=inside)
        assert [row.detail_id for row in page.stories] == [STORY_ID]
        shown = await harness.detail_service.show(STORY_ID, now=inside)
        assert shown.status is PrimaryStoryStatus.PUBLISHED
        outside = created + STORY_UI_RETENTION
        assert (await harness.story_service.list_stories(request, now=outside)).stories == []
        with pytest.raises(StoryDetailExpired):
            await harness.detail_service.show(STORY_ID, now=outside)
        # No history is deleted: the generation and publication executions are still there.
        (run_id,) = await publication_runs(harness)
        assert await harness.env.client.get_workflow_handle(run_id).describe()
        assert await harness.env.client.get_workflow_handle(STORY_ID).describe()


@pytest.mark.asyncio
async def test_no_live_network_all_bluesky_requests_hit_fake_pds() -> None:
    pds = FakePds()
    async with story_e2e(HN_FIXTURE, pds=pds) as harness:
        async with web_client(harness) as web:
            pages = Pages(web)
            await drive_to_ready(harness, pages)
            await publish(pages, harness)
            await wait_for_published(harness)
            await stop_ingestion(harness)
    assert pds.requests
    assert {request.url.host for request in pds.requests} == {"pds.invalid"}
    assert set(pds.names()) <= {"createSession", "getRecord", "uploadBlob", "createRecord"}
