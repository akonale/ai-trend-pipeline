"""Candidate inbox route and view-model tests, using fakes and no Temporal."""

import html
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.candidates import (
    CandidateHandle,
    CandidatePage,
    CandidateService,
    CandidateSummary,
    CandidateUnavailable,
    InvalidPageToken,
    ListCandidatesRequest,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import CandidateStatus, StoryCandidate
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_candidate_service
from ai_trend_pipeline.web.view_models import (
    build_candidate_inbox_view,
    build_candidate_unavailable_view,
    relative_time,
    source_label,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def summary(
    candidate_id: str = "candidate-v1-abc",
    *,
    source: SourceName = SourceName.HN,
    title: str = "A headline",
    angle: str = "Why it matters",
    observed_at: datetime | None = OBSERVED,
) -> CandidateSummary:
    return CandidateSummary(
        candidate_id=candidate_id,
        source=source,
        title=title,
        proposed_angle=angle,
        observed_at=observed_at,
        status=CandidateStatus.AWAITING_REVIEW,
    )


class FakeGateway:
    def __init__(self, page: CandidatePage | None = None, error: Exception | None = None) -> None:
        self.page = page if page is not None else CandidatePage()
        self.error = error
        self.requests: list[ListCandidatesRequest] = []

    async def list_pending(self, request: ListCandidatesRequest) -> CandidatePage:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.page

    async def start(self, candidate: StoryCandidate, review_timeout_seconds: int) -> None:
        raise NotImplementedError

    def handle(self, candidate_id: str) -> CandidateHandle:
        raise NotImplementedError


def client_for(gateway: FakeGateway) -> TestClient:
    app = create_app(Settings())
    service = CandidateService(gateway, Settings())

    async def fake_service() -> CandidateService:
        return service

    app.dependency_overrides[get_candidate_service] = fake_service
    return TestClient(app)


def page(*candidates: CandidateSummary, next_token: str | None = None) -> CandidatePage:
    return CandidatePage(candidates=list(candidates), next_token=next_token)


def test_view_relative_time() -> None:
    assert relative_time(None, NOW) == ""
    assert relative_time(NOW, NOW) == "just now"
    assert relative_time(NOW + timedelta(minutes=5), NOW) == "just now"
    assert relative_time(NOW - timedelta(seconds=59), NOW) == "just now"
    assert relative_time(NOW - timedelta(seconds=60), NOW) == "1 minute ago"
    assert relative_time(NOW - timedelta(minutes=2), NOW) == "2 minutes ago"
    assert relative_time(NOW - timedelta(minutes=59), NOW) == "59 minutes ago"
    assert relative_time(NOW - timedelta(minutes=60), NOW) == "1 hour ago"
    assert relative_time(NOW - timedelta(hours=23), NOW) == "23 hours ago"
    assert relative_time(NOW - timedelta(hours=24), NOW) == "1 day ago"
    assert relative_time(NOW - timedelta(days=6, hours=23), NOW) == "6 days ago"
    assert relative_time(NOW - timedelta(days=7), NOW) == "on 05 Oct 2026"
    naive = datetime(2026, 10, 12, 11, 0)
    assert relative_time(naive, NOW) == "1 hour ago"


def test_view_source_labels_and_count() -> None:
    assert source_label(SourceName.HN) == "Hacker News"
    assert source_label(SourceName.FAKE) == "Test source"
    assert source_label("news") == "news"

    one = build_candidate_inbox_view(page(summary(source=SourceName.FAKE)), now=NOW)
    assert one.count_text == "1 story waiting for review."
    assert one.rows[0].source_label == "Test source"

    two = build_candidate_inbox_view(page(summary(), summary(source=SourceName.FAKE)), now=NOW)
    assert two.count_text == "2 stories waiting for review."
    assert [row.source_label for row in two.rows] == ["Hacker News", "Test source"]


def test_inbox_rows() -> None:
    recent = datetime.now(UTC) - timedelta(hours=2)
    client = client_for(
        FakeGateway(
            page(
                summary(
                    candidate_id="cand/1 &x",
                    title="Interest rates hold steady",
                    angle="What the pause means for savers",
                    observed_at=recent,
                ),
                summary(
                    candidate_id="candidate-v1-two",
                    source=SourceName.FAKE,
                    title="Second story",
                    angle="Second angle",
                    observed_at=None,
                ),
            )
        )
    )
    text = client.get("/candidates").text

    assert "Story candidates" in text
    assert "Interest rates hold steady" in text
    assert "What the pause means for savers" in text
    assert 'href="/candidates/cand%2F1%20%26x"' in text
    assert 'href="/candidates/candidate-v1-two"' in text
    assert 'class="badge badge--muted"' in text
    assert "Hacker News" in text
    assert "Test source" in text
    assert '<span class="visually-hidden">Source: </span>' in text
    assert '<time datetime="' in text
    assert "2 hours ago" in text


def test_untrusted_text_is_escaped() -> None:
    client = client_for(
        FakeGateway(
            page(
                summary(
                    title="<script>alert(1)</script>",
                    angle="<img src=x onerror=alert(1)>",
                )
            )
        )
    )
    text = client.get("/candidates").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text
    assert "<script>alert" not in text
    assert "<img src=x" not in text


def test_missing_fields_and_long_text() -> None:
    long_title = "T" * 200
    long_angle = "a" * 500
    client = client_for(
        FakeGateway(
            page(
                summary(title="", angle="", observed_at=None),
                summary(
                    candidate_id="candidate-v1-long",
                    title=long_title,
                    angle=long_angle,
                    observed_at=None,
                ),
            )
        )
    )
    text = client.get("/candidates").text

    assert "Untitled story" in text
    assert "No angle proposed." in text
    assert "<time" not in text
    assert long_title in text
    assert long_angle in text


def test_pagination_links() -> None:
    first = client_for(FakeGateway(page(summary())))
    text = first.get("/candidates").text
    assert "Older stories" not in text
    assert "Back to newest" not in text

    with_next = client_for(FakeGateway(page(summary(), next_token="tok+/=")))
    text = with_next.get("/candidates").text
    assert "Older stories" in text
    assert 'href="/candidates?page=tok%2B%2F%3D"' in text
    assert "Back to newest" not in text

    cursor_gateway = FakeGateway(page(summary()))
    cursor = client_for(cursor_gateway)
    text = cursor.get("/candidates?page=abc").text
    assert "Back to newest" in text
    assert 'href="/candidates"' in text
    assert "Older stories" not in text
    assert cursor_gateway.requests[0].page_token == "abc"

    both = client_for(FakeGateway(page(summary(), next_token="next")))
    text = both.get("/candidates?page=abc").text
    assert "Older stories" in text
    assert "Back to newest" in text


def test_invalid_page_token() -> None:
    client = client_for(FakeGateway(error=InvalidPageToken("not-a-token")))
    res = client.get("/candidates?page=not-a-token")
    text = html.unescape(res.text)

    assert res.status_code == 400
    assert "That page link is out of date. Start from the newest stories." in text
    assert "Back to newest" in text
    assert 'href="/candidates"' in text
    assert 'class="skip-link"' in text
    assert "<header" in text


def test_empty_state() -> None:
    res = client_for(FakeGateway(page())).get("/candidates")
    text = res.text

    assert res.status_code == 200
    assert "Nothing waiting for review" in text
    assert "New candidates show up here when a source finds a story." in text
    assert "Check again" in text
    assert 'class="candidate-inbox"' not in text
    assert 'class="candidate-row"' not in text


def test_unavailable_state() -> None:
    res = client_for(FakeGateway(error=CandidateUnavailable())).get("/candidates")
    text = html.unescape(res.text)

    assert res.status_code == 503
    assert "The inbox is unavailable" in text
    assert "We couldn't reach the candidate service." in text
    assert "Try again" in text
    assert 'class="skip-link"' in text
    assert "<header" in text
    assert "<footer" in text


def test_page_needs_no_javascript() -> None:
    text = (
        client_for(FakeGateway(page(summary(), summary(candidate_id="candidate-v1-two"))))
        .get("/candidates")
        .text
    )

    assert "hx-" not in text
    assert "<button" not in text
    assert 'class="candidate-row" href="/candidates/candidate-v1-abc"' in text
    assert 'class="candidate-row" href="/candidates/candidate-v1-two"' in text


def test_unavailable_view_has_no_list() -> None:
    view = build_candidate_unavailable_view(NOW)
    assert view.unavailable is True
    assert view.rows == []
    assert view.retry_href == "/candidates"
