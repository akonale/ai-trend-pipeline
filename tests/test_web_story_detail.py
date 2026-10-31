"""Story detail routes, fragments and view models with a fake detail service; no Temporal."""

import html
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.services.stories import (
    DecideStoryRequest,
    StoryDecisionResult,
    StoryDetail,
    StoryDetailExpired,
    StoryDetailKind,
    StoryDetailMalformed,
    StoryDetailNotFound,
    StoryDetailUnavailable,
    StoryNotReviewable,
    StoryReviewStale,
)
from ai_trend_pipeline.story_drafting.models import StoryKeyPoint, StoryPackage, StorySourceLink
from ai_trend_pipeline.story_status import PrimaryStoryStatus
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import get_chart_service, get_story_detail_service
from ai_trend_pipeline.web.view_models import (
    APPROVED_BODY,
    APPROVED_HEADING,
    FINAL_DECISION_NOTE,
    FINAL_REVIEW_HEADING,
    INVALID_DECISION_ERROR,
    STORY_DETAIL_BACK_LABEL,
    story_decision_notice,
)
from tests.media_fixtures import make_manifest

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
CREATED = NOW - timedelta(hours=2)
CANDIDATE_ID = "candidate-v1-0123456789abcdef"
STORY_ID = "story-v1-fedcba9876543210"
FACT_A = "fact-v1-0000000000000001"
FACT_B = "fact-v1-0000000000000002"
FACT_C = "fact-v1-0000000000000003"
HASH = "ab12cd34" + "0" * 56


def make_package(**overrides: object) -> StoryPackage:
    data: dict[str, object] = {
        "story_id": STORY_ID,
        "headline": "Rates hold steady again",
        "summary": "The central bank paused its rate rises for a third time.",
        "body": "First line.\nStill the first paragraph.\n\nSecond paragraph.",
        "key_points": [
            {"text": "Rates did not change.", "fact_ids": [FACT_A, FACT_B]},
            {"text": "This is the third pause.", "fact_ids": [FACT_B, FACT_C]},
        ],
        "source_links": [
            {
                "evidence_id": "ev-1",
                "label": "Original report",
                "url": "https://example.com/report?x=1&y=2",
            }
        ],
        "caveats": ["The figure is provisional."],
        "post_text": "Rates held steady again.",
    }
    data.update(overrides)
    return StoryPackage.model_validate(data)


def make_detail(
    *,
    detail_id: str = STORY_ID,
    workflow_id: str = STORY_ID,
    candidate_id: str = CANDIDATE_ID,
    kind: StoryDetailKind = StoryDetailKind.GENERATION,
    title: str = "Rates hold steady",
    source: str = "hn",
    status: PrimaryStoryStatus = PrimaryStoryStatus.REVIEW,
    internal_status: str = "review",
    status_detail: str | None = None,
    package: StoryPackage | None = None,
    decided_by: str | None = None,
    decided_at: datetime | None = None,
    note: str = "",
    content_hash: str | None = None,
) -> StoryDetail:
    return StoryDetail(
        detail_id=detail_id,
        workflow_id=workflow_id,
        candidate_id=candidate_id,
        kind=kind,
        title=title,
        source=source,
        created_at=CREATED,
        status=status,
        internal_status=internal_status,
        status_detail=status_detail,
        package=package,
        content_hash=content_hash,
        decided_by=decided_by,
        decided_at=decided_at,
        note=note,
    )


class FakeDetailService:
    def __init__(
        self,
        detail: StoryDetail | None = None,
        *,
        show_error: Exception | None = None,
        decide_result: StoryDecisionResult | None = None,
        decide_error: Exception | None = None,
    ) -> None:
        self.detail = detail
        self.show_error = show_error
        self.decide_result = decide_result
        self.decide_error = decide_error
        self.show_calls: list[str] = []
        self.decide_calls: list[DecideStoryRequest] = []

    async def show(self, detail_id: str, *, now: datetime) -> StoryDetail:
        _ = now
        self.show_calls.append(detail_id)
        if self.show_error is not None:
            raise self.show_error
        assert self.detail is not None
        return self.detail

    async def decide(self, request: DecideStoryRequest, *, now: datetime) -> StoryDecisionResult:
        _ = now
        self.decide_calls.append(request)
        if self.decide_error is not None:
            raise self.decide_error
        assert self.decide_result is not None
        return self.decide_result


class ExplodingChartService:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"chart service must not be used ({name})")


def text_of(response: object) -> str:
    text = getattr(response, "text", "")
    assert isinstance(text, str)
    return html.unescape(text)


def client_for(service: FakeDetailService) -> TestClient:
    app = create_app(Settings())

    async def fake_service() -> FakeDetailService:
        return service

    app.dependency_overrides[get_story_detail_service] = fake_service
    return TestClient(app)


def test_pending_and_writing_pages_show_one_badge_and_one_explanation() -> None:
    pending = make_detail(
        detail_id=CANDIDATE_ID,
        workflow_id=CANDIDATE_ID,
        kind=StoryDetailKind.CANDIDATE,
        status=PrimaryStoryStatus.PENDING,
        internal_status="awaiting_review",
    )
    text = client_for(FakeDetailService(pending)).get(f"/stories/{CANDIDATE_ID}").text

    assert text.count('class="badge badge--muted">Pending</span>') == 1
    assert text.count('class="story-detail__progress"') == 1
    assert "Waiting for the candidate review." in text
    assert "hx-get=" in text
    assert "Updating while the story is in progress." in text
    assert "Key points" not in text
    assert "awaiting_review" not in text

    writing = make_detail(
        status=PrimaryStoryStatus.WRITING,
        internal_status="researching",
        status_detail="Reading the original story.",
    )
    text = client_for(FakeDetailService(writing)).get(f"/stories/{STORY_ID}").text

    assert text.count('class="badge badge--progress">Writing</span>') == 1
    assert text.count('class="story-detail__progress"') == 1
    assert "Reading the original story." in text
    assert "researching" not in text
    assert 'hx-trigger="every 10s"' in text

    generic = make_detail(status=PrimaryStoryStatus.WRITING, internal_status="writing")
    text = client_for(FakeDetailService(generic)).get(f"/stories/{STORY_ID}").text
    assert "Writing and checking the story." in text


def test_review_renders_the_full_package() -> None:
    detail = make_detail(package=make_package())
    text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text

    assert '<h1 class="story-detail__title">Rates hold steady again</h1>' in text
    assert "The central bank paused its rate rises for a third time." in text
    assert "<p>First line. Still the first paragraph.</p>" in text
    assert "<p>Second paragraph.</p>" in text
    assert text.index("Rates did not change.") < text.index("This is the third pause.")
    assert text.count('class="story-citation"') == 4
    assert 'href="#story-fact-1"' in text
    assert 'href="#story-fact-3"' in text
    assert 'id="story-fact-1"' in text
    assert f"[1] {FACT_A}" in text
    assert 'href="https://example.com/report?x=1&amp;y=2"' in text
    assert 'target="_blank" rel="noopener noreferrer"' in text
    assert "(opens in a new tab)" in text
    assert "The figure is provisional." in text
    assert "Rates held steady again." in text
    assert "<h2>Key points</h2>" in text
    assert "<h2>Evidence</h2>" in text
    assert "<h2>Fact references</h2>" in text
    assert "<h2>Caveats</h2>" in text
    assert "<h2>Post text</h2>" in text
    assert text.count('<form class="decision"') == 2
    assert 'name="decision" value="approve"' in text
    assert 'name="decision" value="reject"' in text
    assert "Approve final story" in text
    assert "Reject final story" in text
    assert FINAL_REVIEW_HEADING in text
    assert FINAL_DECISION_NOTE in text
    assert f'action="/stories/{STORY_ID}/decide"' in text
    assert "hx-get" not in text


def test_ready_renders_the_package_read_only() -> None:
    detail = make_detail(
        status=PrimaryStoryStatus.READY,
        internal_status="ready",
        package=make_package(),
        decided_by="editor@example.com",
        decided_at=datetime.now(UTC) - timedelta(minutes=3),
    )
    text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text

    assert '<h1 class="story-detail__title">Rates hold steady again</h1>' in text
    assert "Rates did not change." in text
    assert APPROVED_HEADING in text
    assert APPROVED_BODY in text
    assert "Approved by editor@example.com" in text
    assert "Approved 3 minutes ago" in text
    assert "<form" not in text
    assert "<button" not in text
    assert "Approve final story" not in text
    assert "hx-get" not in text


@pytest.mark.parametrize(
    ("kind", "internal", "status_detail", "heading", "body"),
    [
        (
            StoryDetailKind.CANDIDATE,
            "rejected",
            None,
            "This story was rejected",
            "A person rejected the candidate. Nothing was published.",
        ),
        (
            StoryDetailKind.GENERATION,
            "rejected",
            None,
            "This story was rejected at final review",
            "A person rejected the story package. Nothing was published.",
        ),
        (
            StoryDetailKind.GENERATION,
            "unsupported",
            "There was not enough readable evidence to support this story.",
            "This story could not be supported",
            "There was not enough readable evidence to support this story.",
        ),
        (
            StoryDetailKind.GENERATION,
            "unsupported",
            None,
            "This story could not be supported",
            "There was not enough evidence to support this story.",
        ),
        (
            StoryDetailKind.GENERATION,
            "failed",
            "The draft did not pass factual validation.",
            "Story generation failed",
            "The draft did not pass factual validation.",
        ),
        (
            StoryDetailKind.GENERATION,
            "failed",
            None,
            "Story generation failed",
            "Story generation failed.",
        ),
        (
            StoryDetailKind.GENERATION,
            "expired",
            None,
            "This story expired",
            "The review window closed before anyone decided. Nothing was published.",
        ),
        (
            StoryDetailKind.CANDIDATE,
            "expired",
            None,
            "This story expired",
            "The review window closed before anyone decided. Nothing was published.",
        ),
        (
            StoryDetailKind.GENERATION,
            "something-new",
            None,
            "This story is closed",
            "It is no longer available for review. Nothing was published.",
        ),
    ],
)
def test_closed_renders_one_badge_and_each_reason(
    kind: StoryDetailKind,
    internal: str,
    status_detail: str | None,
    heading: str,
    body: str,
) -> None:
    detail = make_detail(
        kind=kind,
        status=PrimaryStoryStatus.CLOSED,
        internal_status=internal,
        status_detail=status_detail,
        package=None,
    )
    text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text

    assert text.count('class="badge badge--danger">Closed</span>') == 1
    assert heading in text
    assert body in text
    assert "Key points" not in text
    assert "<form" not in text
    assert "Approve final story" not in text
    assert "hx-get" not in text


def test_closed_renders_a_quiet_note() -> None:
    detail = make_detail(
        kind=StoryDetailKind.GENERATION,
        status=PrimaryStoryStatus.CLOSED,
        internal_status="rejected",
        note="The numbers did not add up.",
    )
    text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text

    assert "Note: The numbers did not add up." in text


def test_missing_is_404_and_past_cutoff_is_410() -> None:
    missing = client_for(FakeDetailService(show_error=StoryDetailNotFound(STORY_ID))).get(
        f"/stories/{STORY_ID}"
    )
    text = missing.text
    assert missing.status_code == 404
    assert "This story is not available" in text
    assert "It may have been removed, or the link may be wrong." in text
    assert "The stories page is the best place to start." in text
    assert f'<a href="/stories">{STORY_DETAIL_BACK_LABEL}</a>' in text
    assert "Try again" not in text
    assert 'class="skip-link"' in text
    assert "<header" in text
    assert "<footer" in text

    expired = client_for(FakeDetailService(show_error=StoryDetailExpired(STORY_ID))).get(
        f"/stories/{STORY_ID}"
    )
    text = expired.text
    assert expired.status_code == 410
    assert "This story is no longer available" in text
    assert (
        "Stories leave this page 48 hours after they were first detected. Nothing was published."
        in text
    )
    assert "Try again" not in text
    assert f'href="/stories">{STORY_DETAIL_BACK_LABEL}' in text


def test_malformed_and_unavailable_are_502_and_503() -> None:
    malformed = client_for(FakeDetailService(show_error=StoryDetailMalformed(STORY_ID))).get(
        f"/stories/{STORY_ID}"
    )
    assert malformed.status_code == 502
    assert "This story can't be shown right now" in text_of(malformed)
    assert "Something in the story record could not be read. Nothing was published." in (
        text_of(malformed)
    )
    assert f'href="/stories/{STORY_ID}">Try again' in text_of(malformed)
    assert "Traceback" not in text_of(malformed)

    unavailable = client_for(FakeDetailService(show_error=StoryDetailUnavailable())).get(
        f"/stories/{STORY_ID}"
    )
    assert unavailable.status_code == 503
    assert "This story is unavailable" in text_of(unavailable)
    assert "We couldn't reach the story service. Nothing is lost." in text_of(unavailable)
    assert f'href="/stories/{STORY_ID}">Try again' in text_of(unavailable)
    assert "Traceback" not in text_of(unavailable)


@pytest.mark.parametrize(
    ("decision", "result_status", "token"),
    [
        ("approve", "ready", "approved"),
        ("reject", "rejected", "rejected"),
        ("approve", "unsupported", "closed"),
        ("reject", "expired", "closed"),
        ("reject", "ready", "closed"),
    ],
)
def test_decision_redirects_with_notice_and_focus(
    decision: str, result_status: str, token: str
) -> None:
    service = FakeDetailService(
        make_detail(package=make_package()),
        decide_result=StoryDecisionResult(
            workflow_id=STORY_ID,
            status=result_status,  # pyright: ignore[reportArgumentType]
            decided_by="dev@localhost",
            decided_at=NOW,
        ),
    )
    client = client_for(service)

    res = client.post(
        f"/stories/{STORY_ID}/decide", data={"decision": decision}, follow_redirects=False
    )

    assert res.status_code == 303
    assert res.headers["location"] == f"/stories/{STORY_ID}?decided={token}#story-notice"
    assert len(service.decide_calls) == 1
    assert service.decide_calls[0].decided_by == "dev@localhost"

    followed = client.get(f"/stories/{STORY_ID}?decided={token}")
    text = followed.text
    assert 'id="story-notice"' in text
    assert 'tabindex="-1"' in text
    assert 'role="status"' in text
    assert story_decision_notice(token) in text


def test_repeated_or_raced_decision_redirects_with_closed_notice() -> None:
    service = FakeDetailService(
        make_detail(package=make_package()), decide_error=StoryNotReviewable(STORY_ID)
    )
    res = client_for(service).post(
        f"/stories/{STORY_ID}/decide", data={"decision": "approve"}, follow_redirects=False
    )

    assert res.status_code == 303
    assert res.headers["location"] == f"/stories/{STORY_ID}?decided=closed#story-notice"
    assert len(service.decide_calls) == 1


def test_invalid_decision_is_400_without_a_signal() -> None:
    service = FakeDetailService(make_detail(package=make_package()))
    res = client_for(service).post(
        f"/stories/{STORY_ID}/decide", data={"decision": "maybe"}, follow_redirects=False
    )
    text = res.text

    assert res.status_code == 400
    assert INVALID_DECISION_ERROR == "Choose Approve or Reject."
    assert INVALID_DECISION_ERROR in text
    assert 'class="error candidate-flash"' in text
    assert "Key points" in text
    assert 'name="decision" value="approve"' in text
    assert service.decide_calls == []


def unsafe_package() -> StoryPackage:
    """A package built with model_construct so unsafe URLs and raw markup survive validation."""
    return StoryPackage.model_construct(
        story_id=STORY_ID,
        headline="Unsafe links",
        summary="A summary.",
        body="A body.",
        key_points=[StoryKeyPoint.model_construct(text="A point.", fact_ids=[FACT_A])],
        source_links=[
            StorySourceLink.model_construct(
                evidence_id=f"ev-{index}", label=f"Link {index}", url=url
            )
            for index, url in enumerate(
                [
                    "javascript:alert(1)",
                    "data:text/html,<script>alert(1)</script>",
                    "ftp://example.com/file",
                    "//example.com/protocol-relative",
                    "https://example.com/ok?x=1&y=2",
                ]
            )
        ],
        caveats=["A caveat."],
        post_text=None,
    )


def test_source_links_are_limited_to_http_and_https() -> None:
    detail = make_detail(package=unsafe_package())
    text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text

    assert text.count("Link unavailable.") == 4
    assert 'href="javascript:' not in text
    assert 'href="data:' not in text
    assert 'href="ftp:' not in text
    assert 'href="//example.com' not in text
    assert 'href="https://example.com/ok?x=1&amp;y=2"' in text
    assert 'target="_blank" rel="noopener noreferrer"' in text


def test_untrusted_package_text_is_escaped() -> None:
    package = StoryPackage.model_construct(
        story_id=STORY_ID,
        headline="<script>alert(1)</script>",
        summary="<img src=x onerror=alert(2)>",
        body="<script>body</script>",
        key_points=[StoryKeyPoint.model_construct(text="<b>point</b>", fact_ids=[FACT_A])],
        source_links=[
            StorySourceLink.model_construct(
                evidence_id="ev-1", label="<em>label</em>", url="https://example.com/x"
            )
        ],
        caveats=["<u>caveat</u>"],
        post_text="<b>post</b>",
    )
    text = (
        client_for(FakeDetailService(make_detail(package=package))).get(f"/stories/{STORY_ID}").text
    )

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(2)&gt;" in text
    assert "&lt;script&gt;body&lt;/script&gt;" in text
    assert "&lt;b&gt;point&lt;/b&gt;" in text
    assert "&lt;em&gt;label&lt;/em&gt;" in text
    assert "&lt;u&gt;caveat&lt;/u&gt;" in text
    assert "&lt;b&gt;post&lt;/b&gt;" in text
    assert "<script>" not in text
    assert "<img src=x" not in text


def long_package() -> StoryPackage:
    fact_ids = [f"fact-v1-{index:016x}" for index in range(1, 21)]
    key_points = [
        {
            "text": f"Point {point} with enough words to read.",
            "fact_ids": fact_ids[point * 4 : point * 4 + 4],
        }
        for point in range(5)
    ]
    return make_package(
        body="word " * 1200,
        key_points=key_points,
        source_links=[
            {
                "evidence_id": f"ev-{index}",
                "label": f"Source {index}",
                "url": f"https://example.com/source/{index}",
            }
            for index in range(4)
        ],
        caveats=[f"Caveat number {index}." for index in range(5)],
    )


def test_long_body_citations_and_missing_optional_sections() -> None:
    text = (
        client_for(FakeDetailService(make_detail(package=long_package())))
        .get(f"/stories/{STORY_ID}")
        .text
    )

    assert text.count('class="story-citation"') == 20
    assert 'id="story-fact-20"' in text
    assert "[20]" in text
    assert text.count('class="story-evidence"') == 1
    assert text.count('class="story-caveats"') == 1
    assert text.count("<li>Caveat number") == 5
    assert "https://example.com/source/3" in text

    empty = make_package(key_points=[], source_links=[], caveats=[], post_text=None)
    empty_text = (
        client_for(FakeDetailService(make_detail(package=empty))).get(f"/stories/{STORY_ID}").text
    )
    for heading in ("Key points", "Evidence", "Fact references", "Caveats", "Post text"):
        assert f"<h2>{heading}</h2>" not in empty_text


def test_polling_only_while_pending_or_writing() -> None:
    pending = make_detail(
        kind=StoryDetailKind.CANDIDATE,
        status=PrimaryStoryStatus.PENDING,
        internal_status="awaiting_review",
    )
    writing = make_detail(status=PrimaryStoryStatus.WRITING, internal_status="researching")
    review = make_detail(package=make_package())
    ready = make_detail(status=PrimaryStoryStatus.READY, internal_status="ready")
    closed = make_detail(status=PrimaryStoryStatus.CLOSED, internal_status="rejected")

    for detail in (pending, writing):
        text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text
        assert f'hx-get="/stories/{STORY_ID}/status"' in text
        assert 'hx-trigger="every 10s"' in text
        assert 'hx-swap="outerHTML"' in text

    for detail in (review, ready, closed):
        text = client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text
        assert "hx-get" not in text


def test_polling_refresh_id_keeps_the_requested_alias() -> None:
    writing = make_detail(
        detail_id=CANDIDATE_ID,
        workflow_id=STORY_ID,
        status=PrimaryStoryStatus.WRITING,
        internal_status="researching",
    )
    text = client_for(FakeDetailService(writing)).get(f"/stories/{CANDIDATE_ID}").text

    assert f'hx-get="/stories/{CANDIDATE_ID}/status"' in text

    review = make_detail(
        detail_id=CANDIDATE_ID,
        workflow_id=STORY_ID,
        status=PrimaryStoryStatus.REVIEW,
        internal_status="review",
        package=make_package(),
    )
    review_text = client_for(FakeDetailService(review)).get(f"/stories/{CANDIDATE_ID}").text
    assert f'action="/stories/{STORY_ID}/decide"' in review_text
    assert "hx-get" not in review_text


def test_status_fragment_returns_only_the_body() -> None:
    service = FakeDetailService(
        make_detail(status=PrimaryStoryStatus.WRITING, internal_status="researching")
    )
    res = client_for(service).get(f"/stories/{STORY_ID}/status")
    text = res.text

    assert res.status_code == 200
    assert 'id="story-detail"' in text
    assert "<html" not in text
    assert "<title" not in text
    assert "<header" not in text
    assert "Back to stories" not in text
    assert 'hx-get="/stories/' in text

    terminal = (
        client_for(FakeDetailService(make_detail(status=PrimaryStoryStatus.READY)))
        .get(f"/stories/{STORY_ID}/status")
        .text
    )
    assert 'id="story-detail"' in terminal
    assert "hx-get" not in terminal


def test_fragment_typed_failures_are_200_and_stop_polling() -> None:
    expired = client_for(FakeDetailService(show_error=StoryDetailExpired(STORY_ID)))
    fragment = expired.get(f"/stories/{STORY_ID}/status")
    assert fragment.status_code == 200
    assert "This story is no longer updating." in fragment.text
    assert "hx-get" not in fragment.text
    assert expired.get(f"/stories/{STORY_ID}").status_code == 410

    unavailable = client_for(FakeDetailService(show_error=StoryDetailUnavailable()))
    fragment = unavailable.get(f"/stories/{STORY_ID}/status")
    assert fragment.status_code == 200
    assert "Status is unavailable. Check again." in fragment.text
    assert "hx-get" not in fragment.text
    assert unavailable.get(f"/stories/{STORY_ID}").status_code == 503

    missing = client_for(FakeDetailService(show_error=StoryDetailNotFound(STORY_ID)))
    fragment = missing.get(f"/stories/{STORY_ID}/status")
    assert fragment.status_code == 200
    assert "This story is no longer updating." in fragment.text
    assert "hx-get" not in fragment.text


def test_decision_never_calls_chart_or_publishing() -> None:
    app = create_app(Settings())
    service = FakeDetailService(
        make_detail(package=make_package()),
        decide_result=StoryDecisionResult(workflow_id=STORY_ID, status="ready"),
    )

    async def fake_service() -> FakeDetailService:
        return service

    app.dependency_overrides[get_story_detail_service] = fake_service
    app.dependency_overrides[get_chart_service] = ExplodingChartService
    client = TestClient(app)

    res = client.post(
        f"/stories/{STORY_ID}/decide", data={"decision": "approve"}, follow_redirects=False
    )

    assert res.status_code == 303
    assert len(service.decide_calls) == 1


def test_pages_work_without_javascript() -> None:
    text = (
        client_for(FakeDetailService(make_detail(package=make_package())))
        .get(f"/stories/{STORY_ID}")
        .text
    )

    assert 'href="#story-fact-1"' in text
    assert 'href="https://example.com/report?x=1&amp;y=2"' in text
    assert '<form class="decision" method="post"' in text
    assert "hx-" not in text


def test_keyboard_order_and_notice_focus_target() -> None:
    text = (
        client_for(FakeDetailService(make_detail(package=make_package())))
        .get(f"/stories/{STORY_ID}?decided=approved")
        .text
    )

    assert text.index('class="candidate-back"') < text.index('class="story-detail__title"')
    assert text.index('href="#story-fact-1"') < text.index('class="story-evidence"')
    assert text.index('class="story-evidence"') < text.index('action="/stories/')
    assert text.index('action="/stories/') < text.index("Approve final story")
    assert text.index("Approve final story") < text.index("Reject final story")
    assert text.index('id="story-notice"') < text.index('id="story-detail"')
    assert 'id="story-notice" tabindex="-1" role="status"' in text
    assert story_decision_notice("hello") == ""
    assert (
        'id="story-notice"'
        not in client_for(FakeDetailService(make_detail(package=make_package())))
        .get(f"/stories/{STORY_ID}?decided=hello")
        .text
    )


def media_detail(
    outcome: MediaOutcome | None,
    *,
    status: PrimaryStoryStatus = PrimaryStoryStatus.REVIEW,
    **overrides: object,
) -> StoryDetail:
    manifest = None if outcome is None else make_manifest(outcome)
    return make_detail(
        status=status,
        internal_status=status.value,
        package=make_package(media=manifest),
        content_hash=HASH,
        decided_by="dev@localhost" if status is PrimaryStoryStatus.READY else None,
        decided_at=NOW if status is PrimaryStoryStatus.READY else None,
        **overrides,  # pyright: ignore[reportArgumentType]
    )


def page(detail: StoryDetail) -> str:
    return client_for(FakeDetailService(detail)).get(f"/stories/{STORY_ID}").text


def test_review_renders_picture_with_poster_alt_and_gif_source() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    assert manifest.static_artifact is not None and manifest.animated_artifact is not None
    text = page(media_detail(MediaOutcome.GIF))
    base = f"/stories/{STORY_ID}/media/"
    assert "<picture>" in text
    assert (
        f'<source media="(prefers-reduced-motion: no-preference)" '
        f'srcset="{base}{manifest.animated_artifact.artifact_id}" type="image/gif">'
    ) in text
    assert f'src="{base}{manifest.static_artifact.artifact_id}"' in text
    assert 'width="1200" height="675"' in text
    assert f'alt="{manifest.static_artifact.alt_text}"' in text
    assert text.index("<figure") < text.index('class="story-summary"')
    assert "Animated card. Plays once, 4 seconds." in text
    assert "Reduced motion is on, so the still card is shown." in text
    assert f"Alt text: {manifest.static_artifact.alt_text}" in text
    assert "You approve the text and the story card together." in text


def test_static_and_static_fallback_have_no_gif_source_and_show_fallback_text() -> None:
    static = page(media_detail(MediaOutcome.STATIC))
    assert "<picture>" in static and "<source" not in static
    assert "Still card." in static
    assert "could not be made" not in static
    fallback = page(media_detail(MediaOutcome.STATIC_FALLBACK))
    assert "<source" not in fallback
    assert "The animation could not be made, so this still card is used." in fallback


def test_text_only_shows_dashed_no_story_card_box() -> None:
    text = page(media_detail(MediaOutcome.TEXT_ONLY))
    assert 'class="story-media story-media--none"' in text
    assert "<h2>No story card</h2>" in text
    assert "No image could be made for this story. Approving it approves the text only." in text
    assert "<picture>" not in text and "<img" not in text
    assert "You approve the text only." in text


def test_old_story_without_media_renders_no_media_section() -> None:
    text = page(media_detail(None))
    assert "story-media" not in text and "<picture>" not in text
    assert "No story card" not in text
    assert "You approve the text only." in text


def test_decision_forms_post_content_hash() -> None:
    text = page(media_detail(MediaOutcome.GIF))
    assert text.count(f'<input type="hidden" name="content_hash" value="{HASH}">') == 2
    assert 'class="story-rail__code">Package ab12cd34<' in text


def test_stale_decision_redirects_with_notice() -> None:
    service = FakeDetailService(
        media_detail(MediaOutcome.GIF), decide_error=StoryReviewStale(STORY_ID)
    )
    client = client_for(service)
    res = client.post(
        f"/stories/{STORY_ID}/decide",
        data={"decision": "approve", "content_hash": "f" * 64},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert res.headers["location"] == f"/stories/{STORY_ID}?decided=stale#story-notice"
    assert service.decide_calls[0].content_hash == "f" * 64
    followed = client.get(f"/stories/{STORY_ID}?decided=stale")
    assert (
        "This story changed after you opened it. Nothing was decided. Review it again."
        in followed.text
    )


def test_media_urls_are_manifest_derived() -> None:
    manifest = make_manifest(MediaOutcome.GIF)
    package = make_package(
        headline="evil ../../etc/passwd.png",
        summary="Path /tmp/x.gif and ../y should never become a media URL.",
        media=manifest,
    )
    text = page(make_detail(package=package, content_hash=HASH))
    urls = [u for u in re.findall(r'(?:src|srcset)="([^"]+)"', text) if "/media/" in u]
    assert urls
    allowed = {
        f"/stories/{STORY_ID}/media/{artifact.artifact_id}"
        for artifact in (manifest.static_artifact, manifest.animated_artifact)
        if artifact is not None
    }
    assert set(urls) <= allowed
    assert "../" not in "".join(urls)


def test_ready_shows_media_read_only_with_package_code() -> None:
    text = page(media_detail(MediaOutcome.GIF, status=PrimaryStoryStatus.READY))
    assert "<picture>" in text
    assert "<form" not in text
    assert 'class="story-rail__code">Package ab12cd34<' in text
    assert 'name="content_hash"' not in text


def test_story_media_css_is_responsive_and_reduced_motion_aware() -> None:
    from pathlib import Path

    css = (Path(__file__).parent.parent / "src/ai_trend_pipeline/web/static/app.css").read_text()
    start = css.index(".story-media {")
    block = css[
        start : css.index("@media (prefers-reduced-motion: reduce) { .story-media__motion-note")
    ]
    assert "max-width: 100%" in block
    assert "height: auto" in block
    assert ".story-media__motion-note { display: none;" in block
    assert (
        "@media (prefers-reduced-motion: reduce) { .story-media__motion-note { display: block; } }"
        in css
    )
    assert "px;" not in re.sub(r"var\(--[a-z-]+\)", "", block).replace("1px", "")
    assert not re.search(r"(?<!max-)width:\s*\d+(px|rem)", block)
