"""Stories route, fragment and view-model tests with a fake service; no Temporal."""

import html
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.stories import (
    InvalidStoryPageToken,
    ListStoriesRequest,
    StoryFilter,
    StoryListPage,
    StoryRow,
    StoryStatusUnavailable,
)
from ai_trend_pipeline.story_status import PrimaryStoryStatus
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_story_status_service
from ai_trend_pipeline.web.view_models import build_shell

NOW = datetime.now(UTC)


def story(
    candidate_id: str = "candidate-v1-a",
    detail_id: str = "story-v1-a",
    *,
    title: str = "A story",
    source: str = "hn",
    status: PrimaryStoryStatus = PrimaryStoryStatus.PENDING,
    detail: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = NOW - timedelta(minutes=2),
) -> StoryRow:
    return StoryRow(
        candidate_id=candidate_id,
        detail_id=detail_id,
        title=title,
        source=source,
        status=status,
        status_detail=detail,
        created_at=created_at if created_at is not None else NOW - timedelta(hours=1),
        updated_at=updated_at,
    )


def page(*rows: StoryRow, next_token: str | None = None) -> StoryListPage:
    return StoryListPage(stories=list(rows), next_token=next_token)


class FakeStoryService:
    def __init__(self, result: StoryListPage | None = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else StoryListPage()
        self.error = error
        self.calls: list[tuple[ListStoriesRequest, datetime]] = []
        self.handle_calls = 0

    async def list_stories(self, request: ListStoriesRequest, *, now: datetime) -> StoryListPage:
        self.calls.append((request, now))
        if self.error is not None:
            raise self.error
        return self.result

    def handle(self, candidate_id: str) -> None:
        self.handle_calls += 1
        raise AssertionError(f"per-row workflow query for {candidate_id}")


def client_for(service: FakeStoryService) -> TestClient:
    app = create_app(Settings())

    async def fake_service() -> FakeStoryService:
        return service

    app.dependency_overrides[get_story_status_service] = fake_service
    return TestClient(app)


def test_stories_page_renders_five_statuses_and_rows() -> None:
    service = FakeStoryService(
        page(
            story(
                "candidate-v1-pending",
                "story-v1-pending",
                title="Pending story",
                status=PrimaryStoryStatus.PENDING,
                detail="Starting story generation.",
                updated_at=datetime.now(UTC) - timedelta(minutes=2),
            ),
            story(
                "candidate-v1-writing",
                "story-v1-writing",
                title="Writing story",
                source="fake",
                status=PrimaryStoryStatus.WRITING,
            ),
            story(
                "candidate-v1-review",
                "story-v1-review",
                title="Review story",
                status=PrimaryStoryStatus.REVIEW,
            ),
            story(
                "candidate-v1-ready",
                "story-v1-ready",
                title="Ready story",
                status=PrimaryStoryStatus.READY,
            ),
            story(
                "candidate-v1-closed",
                "story-v1-closed",
                title="Closed story",
                status=PrimaryStoryStatus.CLOSED,
            ),
        )
    )
    text = client_for(service).get("/stories").text

    assert "<title>Stories - Topic Studio</title>" in text
    assert 'class="badge badge--muted">Pending</span>' in text
    assert 'class="badge badge--progress">Writing</span>' in text
    assert 'class="badge badge--attention">Review</span>' in text
    assert 'class="badge badge--success">Ready</span>' in text
    assert 'class="badge badge--danger">Closed</span>' in text
    assert 'class="badge source-badge"' in text
    assert "Hacker News" in text
    assert "Test source" in text
    assert '<span class="story-row__detail">Starting story generation.</span>' in text
    assert 'href="/stories/story-v1-pending"' in text
    assert 'href="/stories/story-v1-ready"' in text
    assert "<time datetime=" in text
    assert "Updated 2 minutes ago" in text
    assert 'class="skip-link"' in text
    assert "<header" in text
    assert "<footer" in text


def test_filter_links_and_active_state() -> None:
    text = client_for(FakeStoryService(page(story()))).get("/stories?filter=review").text

    assert text.count('<a class="status-filter') == 6
    assert text.count('aria-current="true"') == 1
    assert 'href="/stories?filter=review" aria-current="true">Review</a>' in text
    assert '<a class="status-filter" href="/stories">All</a>' in text
    for key, label in (
        ("pending", "Pending"),
        ("writing", "Writing"),
        ("ready", "Ready"),
        ("closed", "Closed"),
    ):
        assert f'href="/stories?filter={key}"' in text
        assert label in text


def test_pager_preserves_the_filter() -> None:
    service = FakeStoryService(page(story(), next_token="tok+/="))
    text = html.unescape(client_for(service).get("/stories?filter=writing&page=tok").text)

    assert "Older stories" in text
    assert 'href="/stories?filter=writing&page=tok%2B%2F%3D"' in text
    assert "Back to newest" in text
    assert 'href="/stories?filter=writing"' in text
    assert service.calls[0][0].page_token == "tok"
    assert service.calls[0][0].filter is StoryFilter.WRITING

    all_pages = html.unescape(
        client_for(FakeStoryService(page(story(), next_token="tok+/=")))
        .get("/stories?page=tok")
        .text
    )
    assert 'href="/stories?page=tok%2B%2F%3D"' in all_pages
    assert 'href="/stories"' in all_pages


def test_empty_all_and_filtered_states() -> None:
    all_text = client_for(FakeStoryService(page())).get("/stories").text
    assert "No stories yet" in all_text
    assert "Stories appear here when a source finds a candidate." in all_text
    assert "They leave 48 hours after they were first detected." in all_text
    assert "Check again" in all_text
    assert 'class="candidate-inbox"' not in all_text
    assert 'class="candidate-row"' not in all_text

    filtered = client_for(FakeStoryService(page())).get("/stories?filter=ready").text
    assert "No stories in this view" in filtered
    assert "Nothing in the last 48 hours has this status." in filtered
    assert "Stories leave this page 48 hours after they were first detected." in filtered
    assert "Show all stories" in filtered
    assert 'href="/stories">Show all stories' in filtered
    assert 'class="candidate-inbox"' not in filtered


def test_unavailable_page_is_503_with_shell() -> None:
    res = client_for(FakeStoryService(error=StoryStatusUnavailable())).get("/stories")
    text = html.unescape(res.text)

    assert res.status_code == 503
    assert "Stories are unavailable" in text
    assert "We couldn't reach the story service. Nothing is lost." in text
    assert "Try again" in text
    assert 'class="skip-link"' in text
    assert "<header" in text
    assert "<footer" in text
    assert "hx-get" not in text


def test_invalid_page_token_is_a_friendly_400() -> None:
    res = client_for(FakeStoryService(error=InvalidStoryPageToken("bad"))).get("/stories?page=bad")
    text = html.unescape(res.text)

    assert res.status_code == 400
    assert "That page link is out of date. Start from the newest stories." in text
    assert "Back to newest" in text
    assert 'href="/stories">Back to newest' in text
    assert 'class="skip-link"' in text
    assert "<header" in text


def test_untrusted_text_is_escaped() -> None:
    service = FakeStoryService(
        page(
            story(
                title="<script>alert(1)</script>",
                detail="<img src=x onerror=alert(1)>",
                source="<b>bad source</b>",
            )
        )
    )
    text = client_for(service).get("/stories").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    assert "&lt;b&gt;bad source&lt;/b&gt;" in text
    assert "<script>alert" not in text
    assert "<img src=x" not in text
    assert "<b>bad source" not in text


def test_stale_metadata_and_long_text() -> None:
    long_title = "T" * 400
    long_detail = "d" * 600
    service = FakeStoryService(
        page(
            story(
                title="",
                detail=None,
                source="",
                updated_at=None,
            ),
            story(
                "candidate-v1-long",
                "story-v1-long",
                title=long_title,
                detail=long_detail,
                source="news",
            ),
        )
    )
    text = client_for(service).get("/stories").text

    assert "Untitled story" in text
    assert "Unknown source" in text
    assert long_title in text
    assert long_detail[:200] in text
    assert long_detail not in text
    assert '<span class="story-row__detail">' in text
    assert text.count("<time") == 1


def test_polling_only_while_active_rows_are_visible() -> None:
    pending = client_for(FakeStoryService(page(story()))).get("/stories").text
    assert 'hx-get="/stories/rows?filter=all"' in pending
    assert 'hx-trigger="every 10s"' in pending
    assert 'hx-swap="outerHTML"' in pending
    assert "Updating while stories are in progress." in pending

    for status in (PrimaryStoryStatus.WRITING, PrimaryStoryStatus.REVIEW):
        text = client_for(FakeStoryService(page(story(status=status)))).get("/stories").text
        assert "hx-get=" in text, status

    for status in (PrimaryStoryStatus.READY, PrimaryStoryStatus.CLOSED):
        text = client_for(FakeStoryService(page(story(status=status)))).get("/stories").text
        assert "hx-get" not in text, status

    empty = client_for(FakeStoryService(page())).get("/stories").text
    assert "hx-get" not in empty

    ready_filter = (
        client_for(FakeStoryService(page(story(status=PrimaryStoryStatus.READY))))
        .get("/stories?filter=ready")
        .text
    )
    assert "hx-get" not in ready_filter

    writing_filter = (
        client_for(
            FakeStoryService(
                page(story(candidate_id="candidate-v1-w", status=PrimaryStoryStatus.WRITING))
            )
        )
        .get("/stories?filter=writing&page=tok")
        .text
    )
    assert 'hx-get="/stories/rows?filter=writing&amp;page=tok"' in writing_filter


def test_fragment_returns_only_the_results_region() -> None:
    service = FakeStoryService(page(story(status=PrimaryStoryStatus.WRITING)))
    res = client_for(service).get("/stories/rows?filter=writing")
    text = res.text

    assert res.status_code == 200
    assert 'id="stories-results"' in text
    assert "<html" not in text
    assert "<title" not in text
    assert 'class="status-filters"' not in text
    assert 'hx-get="/stories/rows?filter=writing"' in text


def test_fragment_service_failure_is_200_and_stops_polling() -> None:
    service = FakeStoryService(error=StoryStatusUnavailable())
    partial = client_for(service).get("/stories/rows?filter=writing")
    full = client_for(service).get("/stories?filter=writing")

    assert partial.status_code == 200
    assert "Stories are unavailable" in partial.text
    assert "hx-get" not in partial.text
    assert full.status_code == 503


def test_route_makes_one_service_call_and_no_per_row_query() -> None:
    service = FakeStoryService(page(story()))
    client_for(service).get("/stories?filter=pending")

    assert len(service.calls) == 1
    assert service.handle_calls == 0


def test_page_works_without_javascript() -> None:
    text = (
        client_for(FakeStoryService(page(story(status=PrimaryStoryStatus.READY))))
        .get("/stories")
        .text
    )

    assert '<a class="status-filter"' in text
    assert 'class="candidate-row"' in text
    assert "<button" not in text
    assert "hx-" not in text


def test_nav_marks_stories_current_and_candidates_on_the_inbox() -> None:
    shell = build_shell("/stories", None, "local")
    assert [item.label for item in shell.nav] == [
        "Stories",
        "Candidates",
        "New chart",
        "Your style",
    ]
    assert [item.href for item in shell.nav] == [
        "/stories",
        "/candidates",
        "/",
        "/settings",
    ]
    assert [item.label for item in shell.nav if item.current] == ["Stories"]
    assert all(item.badge is None for item in shell.nav)

    assert [
        item.label
        for item in build_shell("/stories/story-v1-abc", None, "local").nav
        if item.current
    ] == ["Stories"]
    assert [
        item.label for item in build_shell("/candidates/x", None, "local").nav if item.current
    ] == ["Candidates"]
    assert [item for item in build_shell("/health", None, "local").nav if item.current] == []

    text = client_for(FakeStoryService(page())).get("/stories").text
    assert 'aria-current="page">Stories' in text
