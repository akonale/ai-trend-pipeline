"""Cross-page candidate navigation, badge, title and CSS consolidation tests."""

import re
from datetime import datetime
from pathlib import Path

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
from ai_trend_pipeline.web.view_models import build_shell, pending_nav_badge
from tests.style_fakes import InMemoryStyleStore
from tests.test_web import RecGateway
from tests.test_web import client_for as chart_client
from tests.test_web_candidate_detail import (
    CANDIDATE_ID,
    FakeHandle,
    make_candidate,
)
from tests.test_web_candidate_detail import (
    client_for as detail_client,
)
from tests.test_web_settings import make_client

CSS = (
    Path(__file__).parent.parent / "src" / "ai_trend_pipeline" / "web" / "static" / "app.css"
).read_text()
BASE_MARKER = "/* Base (zero-specificity element rules via :where) */"


def summary(
    candidate_id: str = "candidate-v1-abc",
    title: str = "A headline",
) -> CandidateSummary:
    return CandidateSummary(
        candidate_id=candidate_id,
        source=SourceName.HN,
        title=title,
        proposed_angle="Why it matters",
        observed_at=None,
        status=CandidateStatus.AWAITING_REVIEW,
    )


def page(*candidates: CandidateSummary, next_token: str | None = None) -> CandidatePage:
    return CandidatePage(candidates=list(candidates), next_token=next_token)


class FakeGateway:
    def __init__(self, result: CandidatePage | None = None, error: Exception | None = None) -> None:
        self.result = result if result is not None else CandidatePage()
        self.error = error
        self.requests: list[ListCandidatesRequest] = []

    async def list_pending(
        self, request: ListCandidatesRequest, *, cutoff: datetime
    ) -> CandidatePage:
        self.requests.append(request)
        _ = cutoff
        if self.error is not None:
            raise self.error
        return self.result

    async def start(self, candidate: StoryCandidate, review_timeout_seconds: int) -> None:
        raise NotImplementedError

    def handle(self, candidate_id: str) -> CandidateHandle:
        raise NotImplementedError


def inbox_client(gateway: FakeGateway) -> TestClient:
    app = create_app(Settings())
    service = CandidateService(gateway, Settings())

    async def fake_service() -> CandidateService:
        return service

    app.dependency_overrides[get_candidate_service] = fake_service
    return TestClient(app)


def current_labels(path: str) -> list[str]:
    return [item.label for item in build_shell(path, None, "local").nav if item.current]


def test_build_shell_nav_order_and_current_flags() -> None:
    shell = build_shell("/", None, "local")
    assert [item.label for item in shell.nav] == [
        "Stories",
        "Candidates",
        "New chart",
        "Your style",
    ]
    assert [item.href for item in shell.nav] == [
        "/stories",
        "/candidates",
        "/new",
        "/settings",
    ]

    assert current_labels("/") == ["Stories"]
    assert current_labels("/new") == ["New chart"]
    assert current_labels("/new/style") == ["New chart"]
    assert current_labels("/runs/x") == ["New chart"]
    assert current_labels("/settings") == ["Your style"]
    assert current_labels("/health") == []
    assert current_labels("/stories") == ["Stories"]
    assert current_labels("/stories/story-v1-abc") == ["Stories"]
    assert current_labels("/candidates") == ["Candidates"]
    assert current_labels("/candidates/candidate-v1-abc") == ["Candidates"]


def test_pending_nav_badge() -> None:
    assert pending_nav_badge(CandidatePage(), first_page=True) is None
    assert pending_nav_badge(page(summary()), first_page=False) is None

    one = pending_nav_badge(page(summary()), first_page=True)
    assert one is not None
    assert one.text == "1"
    assert one.label == "1 story waiting for review"

    many = pending_nav_badge(
        page(*[summary(f"candidate-v1-{n}") for n in range(3)]), first_page=True
    )
    assert many is not None
    assert many.text == "3"
    assert many.label == "3 stories waiting for review"

    more = pending_nav_badge(
        page(*[summary(f"candidate-v1-{n}") for n in range(3)], next_token="next"),
        first_page=True,
    )
    assert more is not None
    assert more.text == "3+"
    assert more.label == "3 or more stories waiting for review"

    assert pending_nav_badge(page(summary(), next_token="next"), first_page=False) is None


def test_badge_only_on_first_inbox_page() -> None:
    three = [summary(f"candidate-v1-{n}") for n in range(3)]

    first = inbox_client(FakeGateway(page(*three))).get("/candidates").text
    assert (
        'aria-current="page">Candidates<span class="nav-badge" aria-hidden="true">3</span>' in first
    )
    assert '<span class="visually-hidden">, 3 stories waiting for review</span>' in first

    next_page = inbox_client(FakeGateway(page(*three, next_token="next"))).get("/candidates").text
    assert 'class="nav-badge" aria-hidden="true">3+</span>' in next_page
    assert "3 or more stories waiting for review" in next_page

    older = inbox_client(FakeGateway(page(*three))).get("/candidates?page=abc").text
    empty = inbox_client(FakeGateway(page())).get("/candidates").text
    invalid = (
        inbox_client(FakeGateway(error=InvalidPageToken("bad"))).get("/candidates?page=bad").text
    )
    unavailable = inbox_client(FakeGateway(error=CandidateUnavailable())).get("/candidates").text
    home = chart_client(RecGateway()).get("/new").text
    settings = make_client(InMemoryStyleStore()).get("/settings").text
    health = chart_client(RecGateway()).get("/health").text

    for text in (older, empty, invalid, unavailable, home, settings, health):
        assert 'class="nav-badge"' not in text


def test_candidate_pages_mark_candidates_current() -> None:
    inbox = inbox_client(FakeGateway(page(summary()))).get("/candidates").text
    detail = (
        detail_client(FakeHandle(CANDIDATE_ID, make_candidate()))
        .get(f"/candidates/{CANDIDATE_ID}")
        .text
    )
    missing = detail_client().get("/candidates/does-not-exist").text
    closed = (
        detail_client(FakeHandle(CANDIDATE_ID, make_candidate(), CandidateStatus.APPROVED))
        .get(f"/candidates/{CANDIDATE_ID}")
        .text
    )

    for text in (inbox, detail, missing, closed):
        assert text.count('aria-current="page"') == 1
        assert 'aria-current="page">Candidates' in text

    assert "<title>Story candidates - Topic Studio</title>" in inbox


def test_candidate_page_titles_and_back_link() -> None:
    detail = (
        detail_client(FakeHandle(CANDIDATE_ID, make_candidate()))
        .get(f"/candidates/{CANDIDATE_ID}")
        .text
    )
    assert "<title>Interest rates hold steady - Topic Studio</title>" in detail

    missing = detail_client().get("/candidates/does-not-exist").text
    assert "<title>This story is not available - Topic Studio</title>" in missing

    closed = (
        detail_client(FakeHandle(CANDIDATE_ID, make_candidate(), CandidateStatus.EXPIRED))
        .get(f"/candidates/{CANDIDATE_ID}")
        .text
    )
    assert "<title>This story expired without a decision - Topic Studio</title>" in closed

    back = '<p class="candidate-back"><a href="/candidates">Back to candidates</a></p>'
    for text in (detail, missing, closed):
        assert text.count('class="candidate-back"') == 1
        assert back in text
        assert text.index('class="candidate-back"') < text.index("<h1")


def test_candidate_css_is_consolidated_and_token_only() -> None:
    for needle in (
        ".nav-badge",
        ".source-badge",
        ".candidate-state",
        ".candidate-state--empty",
        ".candidate-state--unavailable",
        ".candidate-flash",
        ".candidate-back",
        ".candidate-decisions",
        ".candidate-actions .candidate-decisions",
        ".candidate-actions .candidate-decisions .btn",
    ):
        assert needle in CSS, needle

    assert ".candidate-inbox .candidate-row" not in CSS
    assert ".candidate-actions .decisions" not in CSS
    assert ".candidate-actions .decision .btn" not in CSS
    assert ".candidate-actions .back" not in CSS
    assert ".notice[tabindex" not in CSS
    assert ".decisions {" in CSS
    motion = re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{\s*"
        r"\.candidate-row \{ transition: none; \}\s*\}",
        CSS,
    )
    assert motion

    below_tokens = CSS[CSS.index(BASE_MARKER) :]
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", below_tokens) is None
