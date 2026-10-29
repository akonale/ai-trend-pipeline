"""Run page as flow steps 3 and 4: headings, stage messages, end states, Bluesky link."""

from typing import Literal

import pytest

from ai_trend_pipeline.daily_chart import APPROVAL_TIMEOUT, ReviewRequest, ReviewState
from ai_trend_pipeline.post_options import PostOptions
from ai_trend_pipeline.web.view_models import (
    RunView,
    bluesky_post_url,
    build_run_view,
    critique_summary,
)
from tests.test_web import REVIEW, RecGateway, client_for

Status = Literal["drafting", "awaiting_decision", "posting", "approved", "rejected", "expired"]

DID = "did:plc:" + "abcdefghijklmnopqrstuvwx"[:24]
RKEY = "3kabcdefg2345"
VALID_URI = f"at://{DID}/app.bsky.feed.post/{RKEY}"
VALID_URL = f"https://bsky.app/profile/{DID}/post/{RKEY}"
FAKE_POST_URI = "at://did:plc:fake/app.bsky.feed.post/fakerkey"
POLL = 'hx-trigger="every 2s"'

ALL_STATUSES: list[Status] = [
    "drafting",
    "awaiting_decision",
    "posting",
    "approved",
    "rejected",
    "expired",
]


def test_valid_uri_maps_to_url() -> None:
    assert len(DID) == len("did:plc:") + 24
    assert bluesky_post_url(VALID_URI) == VALID_URL


@pytest.mark.parametrize(
    "uri",
    [
        None,
        "",
        FAKE_POST_URI,
        f"at://did:web:example.com/app.bsky.feed.post/{RKEY}",
        f"at://{DID}/app.bsky.feed.like/{RKEY}",
        f"{VALID_URI}/extra",
        f"{VALID_URI}?x=1",
        f"{VALID_URI}\n",
        f" {VALID_URI}",
        f"at://{DID.upper()}/app.bsky.feed.post/{RKEY}",
        f"https://bsky.app/profile/{DID}/post/{RKEY}",
        "javascript:alert(1)",
        '"><script>alert(1)</script>',
        f"at://did:plc:{'а' + 'b' * 23}/app.bsky.feed.post/{RKEY}",
    ],
)
def test_bad_uri_gives_no_url(uri: str | None) -> None:
    assert bluesky_post_url(uri) is None


def run_view(
    status: Status, sent: bool = False, post_uri: str | None = None, review: bool = True
) -> RunView:
    state = ReviewState(status=status, review=REVIEW if review else None, post_uri=post_uri)
    return build_run_view("wf-1", state, "http://t", sent=sent)


EXPIRED_TEXT = (
    f"Nobody approved or rejected this chart within "
    f"{int(APPROVAL_TIMEOUT.total_seconds() // 3600)} hours, so nothing was posted."
)


@pytest.mark.parametrize(
    ("status", "sent", "heading", "progress", "is_end", "tone", "end"),
    [
        (
            "drafting",
            False,
            "Drafting your chart",
            "Drafting your chart. This usually takes a minute or two.",
            False,
            "",
            "",
        ),
        ("awaiting_decision", False, "Review your chart", "", False, "", ""),
        (
            "awaiting_decision",
            True,
            "Sending your decision",
            "Decision sent, waiting for the workflow.",
            False,
            "",
            "",
        ),
        ("posting", False, "Posting to Bluesky", "Posting your chart to Bluesky.", False, "", ""),
        (
            "approved",
            False,
            "Posted to Bluesky",
            "",
            True,
            "success",
            "Your chart is live on Bluesky.",
        ),
        ("rejected", False, "Rejected", "", True, "danger", "Nothing was posted."),
        ("expired", False, "Expired: nobody decided in time", "", True, "muted", EXPIRED_TEXT),
    ],
)
def test_run_view_copy_per_state(
    status: Status,
    sent: bool,
    heading: str,
    progress: str,
    is_end: bool,
    tone: str,
    end: str,
) -> None:
    v = run_view(status, sent)
    assert v.heading == heading
    assert v.progress_message == progress
    assert v.is_end is is_end
    assert v.outcome_tone == tone
    assert v.end_message == end
    assert v.another_href == "/"


def test_expired_text_says_20_hours() -> None:
    assert "within 20 hours" in EXPIRED_TEXT


def test_approved_with_valid_uri_has_link() -> None:
    v = run_view("approved", post_uri=VALID_URI)
    assert v.post_url == VALID_URL
    assert v.post_link_note == ""


@pytest.mark.parametrize("uri", [None, FAKE_POST_URI])
def test_approved_without_usable_uri_has_note(uri: str | None) -> None:
    v = run_view("approved", post_uri=uri)
    assert v.post_url is None
    assert v.post_link_note == "The link to the post is not available for this run."


@pytest.mark.parametrize(
    "status", ["drafting", "awaiting_decision", "posting", "rejected", "expired"]
)
def test_non_approved_never_has_post_url(status: Status) -> None:
    v = run_view(status, post_uri=VALID_URI)
    assert v.post_url is None
    assert v.post_link_note == ""


def test_critique_summary_singular_and_plural() -> None:
    assert critique_summary(True, 1) == "Passed after 1 draft."
    assert critique_summary(False, 2) == "Failed after 2 drafts."
    assert run_view("awaiting_decision").critique_summary == "Passed after 1 draft."
    assert run_view("drafting", review=False).critique_summary == ""


def page(
    status: Status,
    post_uri: str | None = None,
    sent: bool = False,
    review: ReviewRequest | None = REVIEW,
    path: str = "/runs/wf-1",
) -> str:
    state = ReviewState(status=status, review=review, post_uri=post_uri)
    return client_for(RecGateway(state)).get(path + ("?sent=1" if sent else "")).text


def test_route_approved_with_link() -> None:
    text = page("approved", VALID_URI)
    assert "Posted to Bluesky" in text
    assert f'href="{VALID_URL}"' in text
    assert 'target="_blank" rel="noopener noreferrer"' in text
    assert "View the post" in text
    assert "(opens in a new tab)" in text
    assert "Make another chart" in text
    assert "<button" not in text
    assert POLL not in text


def test_route_approved_without_link() -> None:
    text = page("approved", None)
    assert "Posted to Bluesky" in text
    assert "The link to the post is not available for this run." in text
    assert "bsky.app" not in text
    assert "View the post" not in text
    assert "Make another chart" in text


@pytest.mark.parametrize(
    ("status", "heading", "message"),
    [
        ("rejected", "Rejected", "Nothing was posted."),
        ("expired", "Expired: nobody decided in time", EXPIRED_TEXT),
    ],
)
def test_route_rejected_and_expired(status: Status, heading: str, message: str) -> None:
    text = page(status)
    assert heading in text
    assert message in text
    assert '<a class="btn btn--primary" href="/">Make another chart</a>' in text
    assert "<button" not in text
    assert "hx-trigger" not in text


@pytest.mark.parametrize(
    ("status", "sent", "message"),
    [
        ("drafting", False, "Drafting your chart. This usually takes a minute or two."),
        ("posting", False, "Posting your chart to Bluesky."),
        ("awaiting_decision", True, "Decision sent, waiting for the workflow."),
    ],
)
def test_route_polling_states(status: Status, sent: bool, message: str) -> None:
    review = None if status == "drafting" else REVIEW
    for path in ("/runs/wf-1", "/runs/wf-1/status"):
        text = page(status, sent=sent, review=review, path=path)
        assert POLL in text
        start = text.index('class="run-progress" role="status"')
        assert message in text[start : text.index("</div>", text.index("</div>", start) + 1)]
        assert "<button" not in text
        assert "Make another chart" not in text


def test_route_awaiting_does_not_poll_and_shows_decision() -> None:
    text = page("awaiting_decision")
    assert "Review your chart" in text
    assert "Approve and post" in text
    assert ">Reject<" in text
    assert "Approving posts this chart and caption to Bluesky." in text
    assert "<h3>Critique</h3>" in text
    assert "Passed after 1 draft." in text
    assert "Options used" not in text
    assert "hx-trigger" not in text
    assert "run-progress" not in text


def test_route_awaiting_shows_options_used_when_present() -> None:
    state = ReviewState(
        status="awaiting_decision",
        review=REVIEW,
        options=PostOptions(chart_type="bar"),
        option_notes=["No data for Atlantis."],
    )
    text = client_for(RecGateway(state)).get("/runs/wf-1").text
    assert "<h3>Options used</h3>" in text
    assert "No data for Atlantis." in text


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_route_one_h1_per_page(status: Status) -> None:
    review = None if status == "drafting" else REVIEW
    assert page(status, review=review).count("<h1") == 1


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_route_fragment_keeps_contract(status: Status) -> None:
    review = None if status == "drafting" else REVIEW
    text = page(status, review=review, path="/runs/wf-1/status")
    assert 'id="run-status"' in text
    assert text.count('id="flow-stepper"') == 1
    assert 'hx-swap-oob="true"' in text
    assert "<html" not in text
    assert "<h1" in text


def test_full_page_has_exactly_one_stepper() -> None:
    assert page("approved", VALID_URI).count('id="flow-stepper"') == 1
