"""Candidate detail, decision and not-available tests, using fakes and no Temporal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.candidates import (
    CandidateHandle,
    CandidateNotFound,
    CandidatePage,
    CandidateService,
    ListCandidatesRequest,
)
from ai_trend_pipeline.sources import SourceName
from ai_trend_pipeline.story_candidate import (
    CandidateDecision,
    CandidateReviewState,
    CandidateStatus,
    StoryCandidate,
    StoryCandidateResult,
    candidate_id_for,
)
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_candidate_service, get_chart_service
from ai_trend_pipeline.web.view_models import (
    DECISION_NOTE,
    INVALID_DECISION_ERROR,
    build_candidate_detail_view,
    build_candidate_not_available_view,
    decision_notice,
    metric_chips,
    safe_source_url,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
OBSERVED = NOW - timedelta(hours=2)
RECENT = datetime.now(UTC) - timedelta(hours=2)


def make_candidate(**overrides: object) -> StoryCandidate:
    """A compact candidate; model_construct keeps malicious URLs and blank text intact."""
    # Any: model_construct deliberately accepts unvalidated fixture values.
    data: dict[str, Any] = {
        "source": SourceName.HN,
        "external_id": "story-1",
        "title": "Interest rates hold steady",
        "url": "https://news.ycombinator.com/item?id=1",
        "published_at": None,
        "observed_at": RECENT,
        "summary": "The central bank paused its rate rises for the third time this year.",
        "proposed_angle": "What the pause means for first-time buyers.",
        "selection_reason": "It affects a large audience and the angle is clear.",
        "metrics": {"score": 412, "comments": 87},
    }
    data.update(overrides)
    return StoryCandidate.model_construct(**data)


CANDIDATE = make_candidate()
CANDIDATE_ID = candidate_id_for(CANDIDATE.source, CANDIDATE.external_id)


class FakeHandle:
    def __init__(
        self,
        candidate_id: str,
        candidate: StoryCandidate,
        status: CandidateStatus = CandidateStatus.AWAITING_REVIEW,
        *,
        missing: bool = False,
    ) -> None:
        self._id = candidate_id
        self.candidate = candidate
        self.status = status
        self.missing = missing
        self.received: list[tuple[str, CandidateDecision]] = []

    @property
    def id(self) -> str:
        return self._id

    async def review_state(self) -> CandidateReviewState:
        if self.missing:
            raise CandidateNotFound(self._id)
        return CandidateReviewState(status=self.status, candidate=self.candidate)

    async def approve(self, decision: CandidateDecision) -> None:
        if self.status is not CandidateStatus.AWAITING_REVIEW:
            raise CandidateNotFound(self._id)
        self.received.append(("approve", decision))
        self.status = CandidateStatus.APPROVED

    async def reject(self, decision: CandidateDecision) -> None:
        if self.status is not CandidateStatus.AWAITING_REVIEW:
            raise CandidateNotFound(self._id)
        self.received.append(("reject", decision))
        self.status = CandidateStatus.REJECTED

    async def result(self) -> StoryCandidateResult:
        if self.status is CandidateStatus.APPROVED:
            return StoryCandidateResult(status="approved", candidate=self.candidate)
        if self.status is CandidateStatus.REJECTED:
            return StoryCandidateResult(status="rejected", candidate=self.candidate)
        return StoryCandidateResult(status="expired", candidate=self.candidate)


class FakeGateway:
    def __init__(self, handles: list[FakeHandle], page: CandidatePage | None = None) -> None:
        self.handles = {handle.id: handle for handle in handles}
        self.page = page if page is not None else CandidatePage()

    async def list_pending(
        self, request: ListCandidatesRequest, *, cutoff: datetime
    ) -> CandidatePage:
        _ = (request, cutoff)
        return self.page

    async def start(self, candidate: StoryCandidate, review_timeout_seconds: int) -> None:
        raise NotImplementedError

    def handle(self, candidate_id: str) -> CandidateHandle:
        return self.handles.get(
            candidate_id, FakeHandle(candidate_id, make_candidate(), missing=True)
        )


class ExplodingChartService:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"chart service must not be used ({name})")


def client_for(*handles: FakeHandle, page: CandidatePage | None = None) -> TestClient:
    app = create_app(Settings())
    service = CandidateService(FakeGateway(list(handles), page), Settings())

    async def fake_service() -> CandidateService:
        return service

    app.dependency_overrides[get_candidate_service] = fake_service
    return TestClient(app)


def awaiting_handle(**overrides: object) -> FakeHandle:
    return FakeHandle(CANDIDATE_ID, make_candidate(**overrides))


def test_detail_page_shows_story_and_source() -> None:
    text = client_for(awaiting_handle()).get(f"/candidates/{CANDIDATE_ID}").text

    assert "<h1" in text
    assert text.index(CANDIDATE.title) < text.index("Hacker News")
    assert 'class="badge source-badge"' in text
    assert text.count('class="candidate-back"') == 1
    assert text.index('class="candidate-back"') < text.index("<h1")
    assert 'class="candidate-back"><a href="/candidates">Back to candidates</a>' in text
    assert "Back to candidates" not in text.split("<aside")[1]
    assert '<span class="visually-hidden">Source: </span>' in text
    assert "Detected 2 hours ago" in text
    assert "<time datetime=" in text
    assert 'href="https://news.ycombinator.com/item?id=1"' in text
    assert 'target="_blank" rel="noopener noreferrer"' in text
    assert "Read the original story" in text
    assert "opens in a new tab" in text
    assert "Summary" in text
    assert "Proposed angle" in text
    assert "Why this story" in text
    assert "Source metrics" in text
    assert "Points" in text
    assert "Comments" in text
    assert "412" in text
    assert "87" in text
    assert f'action="/candidates/{CANDIDATE_ID}/decide"' in text
    assert 'name="decision" value="approve"' in text
    assert 'name="decision" value="reject"' in text
    assert "Approve story" in text
    assert "Reject story" in text
    assert DECISION_NOTE in text
    assert "Back to candidates" in text
    assert 'class="skip-link"' in text
    assert "<header" in text
    assert "<footer" in text


def test_detail_long_title_and_missing_optional_fields() -> None:
    long_title = "T" * 200
    candidate = make_candidate(
        title=long_title,
        url=None,
        summary="",
        proposed_angle="",
        selection_reason="",
        metrics={},
    )
    text = client_for(FakeHandle(CANDIDATE_ID, candidate)).get(f"/candidates/{CANDIDATE_ID}").text

    assert long_title in text
    assert "No source link for this story." in text
    assert "No summary available." in text
    assert "No angle proposed." in text
    assert "No reason recorded." in text
    assert "No source metrics for this story." in text
    assert 'target="_blank"' not in text


def test_detail_escapes_untrusted_html() -> None:
    candidate = make_candidate(
        title="<script>alert(1)</script>",
        summary="<img src=x onerror=alert(2)>",
        proposed_angle="<b>angle</b>",
        selection_reason="<em>reason</em>",
    )
    text = client_for(FakeHandle(CANDIDATE_ID, candidate)).get(f"/candidates/{CANDIDATE_ID}").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(2)&gt;" in text
    assert "&lt;b&gt;angle&lt;/b&gt;" in text
    assert "&lt;em&gt;reason&lt;/em&gt;" in text
    assert "<script>alert" not in text
    assert "<img src=x" not in text


def test_source_link_is_limited_to_http_and_https() -> None:
    good = make_candidate(url="https://example.com/story?x=1&y=2")
    text = client_for(FakeHandle(CANDIDATE_ID, good)).get(f"/candidates/{CANDIDATE_ID}").text
    assert 'href="https://example.com/story?x=1&amp;y=2"' in text
    assert 'target="_blank" rel="noopener noreferrer"' in text

    for bad in (
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/file",
        "//example.com/story",
    ):
        candidate = make_candidate(url=bad)
        page = client_for(FakeHandle(CANDIDATE_ID, candidate)).get(f"/candidates/{CANDIDATE_ID}")
        assert page.status_code == 200
        assert "No source link for this story." in page.text
        assert 'target="_blank"' not in page.text
        assert f'href="{bad}"' not in page.text


def test_safe_source_url_rejects_non_http_and_hostless_urls() -> None:
    assert safe_source_url(None) is None
    assert safe_source_url("https://example.com/a") == "https://example.com/a"
    assert safe_source_url("http://example.com") == "http://example.com"
    assert safe_source_url("javascript:alert(1)") is None
    assert safe_source_url("data:text/html,x") is None
    assert safe_source_url("ftp://example.com/file") is None
    assert safe_source_url("//example.com/story") is None
    assert safe_source_url("https://") is None


def test_view_metric_chips() -> None:
    chips = metric_chips({"score": 412, "comments": 1200, "view_count": 5})
    assert [(c.label, c.value) for c in chips] == [
        ("Points", "412"),
        ("Comments", "1,200"),
        ("View count", "5"),
    ]
    assert metric_chips({}) == []

    candidate = make_candidate(metrics={})
    view = build_candidate_detail_view(
        CandidateReviewState(status=CandidateStatus.AWAITING_REVIEW, candidate=candidate),
        now=NOW,
    )
    assert view.metrics == []
    assert view.metrics_note == "No source metrics for this story."


def test_approve_redirects_to_inbox_notice() -> None:
    handle = awaiting_handle()
    res = client_for(handle).post(
        f"/candidates/{CANDIDATE_ID}/decide", data={"decision": "approve"}, follow_redirects=False
    )

    assert res.status_code == 303
    assert res.headers["location"] == "/candidates?notice=approved#candidate-notice"
    assert len(handle.received) == 1
    action, decision = handle.received[0]
    assert action == "approve"
    assert decision.decided_by == "dev@localhost"


def test_reject_redirects_to_inbox_notice() -> None:
    handle = awaiting_handle()
    res = client_for(handle).post(
        f"/candidates/{CANDIDATE_ID}/decide", data={"decision": "reject"}, follow_redirects=False
    )

    assert res.status_code == 303
    assert res.headers["location"] == "/candidates?notice=rejected#candidate-notice"
    assert [action for action, _ in handle.received] == ["reject"]
    assert handle.received[0][1].decided_by == "dev@localhost"


def test_success_notice_and_focus_target() -> None:
    res = client_for(page=CandidatePage()).get("/candidates?notice=approved")
    text = res.text

    assert res.status_code == 200
    assert "Approved: the story is recorded. Nothing was published." in text
    assert 'id="candidate-notice"' in text
    assert 'tabindex="-1"' in text
    assert 'role="status"' in text
    assert "hx-" not in text


def test_unknown_notice_token_is_ignored() -> None:
    text = client_for(page=CandidatePage()).get("/candidates?notice=hello").text

    assert 'class="notice"' not in text
    assert "Hello" not in text
    assert decision_notice("hello") == ""
    assert decision_notice("<script>") == ""


def test_missing_candidate_is_404() -> None:
    res = client_for().get("/candidates/does-not-exist")
    text = res.text

    assert res.status_code == 404
    assert "This story is not available" in text
    assert "It may have been removed, or the link may be wrong." in text
    assert "Back to candidates" in text
    assert 'href="/candidates"' in text
    assert 'class="skip-link"' in text
    assert "<header" in text
    assert "<footer" in text


@pytest.mark.parametrize(
    ("status", "heading", "body"),
    [
        (
            CandidateStatus.APPROVED,
            "This story was already approved",
            "It is no longer waiting for review. Approving only records a story; "
            "nothing was published.",
        ),
        (
            CandidateStatus.REJECTED,
            "This story was already rejected",
            "It is no longer waiting for review. Rejecting only removes a story; "
            "nothing was published.",
        ),
        (
            CandidateStatus.EXPIRED,
            "This story expired without a decision",
            "The review window closed before anyone decided. Nothing was published.",
        ),
    ],
)
def test_already_decided_candidates_are_410(
    status: CandidateStatus, heading: str, body: str
) -> None:
    handle = FakeHandle(CANDIDATE_ID, make_candidate(), status)
    res = client_for(handle).get(f"/candidates/{CANDIDATE_ID}")
    text = res.text

    assert res.status_code == 410
    assert heading in text
    assert body in text
    assert "Back to candidates" in text
    assert 'class="candidate-actions"' not in text
    assert "Approve story" not in text


def test_repeated_decision_redirects_with_closed_notice() -> None:
    handle = awaiting_handle()
    client = client_for(handle)

    first = client.post(
        f"/candidates/{CANDIDATE_ID}/decide", data={"decision": "approve"}, follow_redirects=False
    )
    second = client.post(
        f"/candidates/{CANDIDATE_ID}/decide", data={"decision": "reject"}, follow_redirects=False
    )

    assert first.status_code == 303
    assert second.status_code == 303
    assert second.headers["location"] == "/candidates?notice=closed#candidate-notice"
    assert [action for action, _ in handle.received] == ["approve"]


def test_invalid_decision_renders_error_without_signal() -> None:
    handle = awaiting_handle()
    res = client_for(handle).post(
        f"/candidates/{CANDIDATE_ID}/decide", data={"decision": "maybe"}, follow_redirects=False
    )
    text = res.text

    assert res.status_code == 400
    assert INVALID_DECISION_ERROR == "Choose Approve or Reject."
    assert INVALID_DECISION_ERROR in text
    assert 'class="error candidate-flash"' in text
    assert "Summary" in text
    assert 'name="decision" value="approve"' in text
    assert handle.received == []


def test_decision_never_calls_the_chart_service() -> None:
    handle = awaiting_handle()
    app = create_app(Settings())
    service = CandidateService(FakeGateway([handle]), Settings())

    async def fake_service() -> CandidateService:
        return service

    app.dependency_overrides[get_candidate_service] = fake_service
    app.dependency_overrides[get_chart_service] = ExplodingChartService
    client = TestClient(app)

    res = client.post(
        f"/candidates/{CANDIDATE_ID}/decide", data={"decision": "approve"}, follow_redirects=False
    )

    assert res.status_code == 303
    assert [action for action, _ in handle.received] == ["approve"]


def test_detail_and_not_available_need_no_javascript() -> None:
    detail = client_for(awaiting_handle()).get(f"/candidates/{CANDIDATE_ID}").text
    missing = client_for().get("/candidates/nope").text

    assert "hx-" not in detail
    assert "hx-" not in missing
    assert 'method="post"' in detail
    assert "decide" in detail


def test_not_available_view_copy() -> None:
    expected = [
        (
            None,
            "This story is not available",
            "It may have been removed, or the link may be wrong. "
            "The candidate inbox is the best place to start.",
        ),
        (
            "approved",
            "This story was already approved",
            "It is no longer waiting for review. Approving only records a story; "
            "nothing was published.",
        ),
        (
            "rejected",
            "This story was already rejected",
            "It is no longer waiting for review. Rejecting only removes a story; "
            "nothing was published.",
        ),
        (
            "expired",
            "This story expired without a decision",
            "The review window closed before anyone decided. Nothing was published.",
        ),
        (
            "something-else",
            "This story is no longer available",
            "It is no longer waiting for review.",
        ),
    ]
    for status, heading, body in expected:
        view = build_candidate_not_available_view(status)
        assert view.heading == heading
        assert view.body == body
        assert view.page_title == heading
        assert view.back_href == "/candidates"
        assert view.back_label == "Back to candidates"
