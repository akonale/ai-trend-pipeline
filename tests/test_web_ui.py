"""UI refresh tests: view models, components and static assets. No Temporal."""

import re
from urllib.parse import quote

import pytest

from ai_trend_pipeline.daily_chart import ReviewState
from ai_trend_pipeline.web.view_models import HomeView, build_run_view
from tests.test_web import REVIEW_FAILED, RecGateway, Status, client_for

BADGES = [
    ("drafting", False, "Drafting", "badge--progress"),
    ("awaiting_decision", False, "Needs review", "badge--attention"),
    ("awaiting_decision", True, "Decision sent", "badge--progress"),
    ("posting", False, "Posting", "badge--progress"),
    ("approved", False, "Posted", "badge--success"),
    ("rejected", False, "Rejected", "badge--danger"),
    ("expired", False, "Expired", "badge--muted"),
]


@pytest.mark.parametrize(("status", "sent", "label", "css"), BADGES)
def test_badge_mapping(status: Status, sent: bool, label: str, css: str) -> None:
    view = build_run_view("w", ReviewState(status=status), "http://t", sent=sent)
    assert view.badge_label == label
    assert view.badge_class == css


def page(status: Status, sent: bool = False) -> str:
    review = None if status == "drafting" else REVIEW_FAILED
    client = client_for(RecGateway(ReviewState(status=status, review=review)))
    return client.get("/runs/wf-1", params={"sent": "1"} if sent else {}).text


def test_awaiting_page_has_badge() -> None:
    text = page("awaiting_decision")
    assert 'class="badge badge--attention"' in text
    assert "Needs review" in text


@pytest.mark.parametrize(
    ("status", "sent", "expected"),
    [
        ("drafting", False, True),
        ("posting", False, True),
        ("awaiting_decision", True, True),
        ("awaiting_decision", False, False),
        ("approved", False, False),
        ("rejected", False, False),
        ("expired", False, False),
    ],
)
def test_progress_only_when_polling(status: Status, sent: bool, expected: bool) -> None:
    text = page(status, sent)
    assert ('class="run-progress"' in text) is expected
    if expected:
        assert re.search(r'class="run-progress"[^>]*role="status"', text)
        assert "Working on it, checking every 2 seconds" in text


def test_awaiting_page_components() -> None:
    text = page("awaiting_decision")
    assert '<figure class="chart-frame"' in text
    assert "<figcaption>" in text and "Emissions rose" in text
    assert "btn--primary" in text
    assert "btn--danger-outline" in text


def test_home_components() -> None:
    text = client_for(RecGateway()).get("/new").text
    for part in ('class="site-header"', '<label for="topic"', 'id="topic"', "btn--primary"):
        assert part in text
    hrefs = re.findall(r'<a class="chip" href="(/new\?topic=[^"]+)"', text)
    assert len(hrefs) >= 3


def test_home_view_examples_encoded() -> None:
    view = HomeView()
    assert view.examples
    for chip in view.examples:
        assert chip.href == f"/new?topic={quote(chip.label)}"


def test_topic_prefill() -> None:
    client = client_for(RecGateway())
    assert 'value="Life expectancy"' in client.get("/new?topic=%20Life%20expectancy%20").text
    assert f'value="{"x" * 200}"' in client.get(f"/new?topic={'x' * 300}").text
    assert f'value="{"x" * 201}"' not in client.get(f"/new?topic={'x' * 300}").text
    assert 'value=""' in client.get("/new").text


def test_blank_topic_error_keeps_chips() -> None:
    res = client_for(RecGateway()).post("/runs", data={"topic": " "})
    assert res.status_code == 400
    assert 'role="alert"' in res.text
    assert 'class="chip"' in res.text


def test_every_page_has_meta_and_static_assets() -> None:
    client = client_for(RecGateway())
    texts = [
        client.get("/new").text,
        page("awaiting_decision"),
        client.get("/health").text,
    ]
    texts.append(client_for(RecGateway(None)).get("/runs/nope").text)
    assert len(texts) == 4
    for text in texts:
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in text
        assert '<meta name="color-scheme" content="light dark">' in text
        urls = re.findall(r'(?:<link[^>]* href|<script[^>]* src)="([^"]+)"', text)
        assert urls
        assert all(url.startswith("/static/") for url in urls)


TOKENS = (
    "--color-bg --color-surface --color-text --color-muted --color-border --color-accent "
    "--color-accent-contrast --color-success --color-danger --color-warning --color-info "
    "--color-focus --font-display --font-body --font-mono --text-sm --text-base --text-lg "
    "--text-xl --text-2xl --space-1 --space-2 --space-3 --space-4 --space-5 --space-6 "
    "--radius-sm --radius-md --radius-pill --shadow-sm --shadow-md"
).split()


def test_css_contents() -> None:
    css = client_for(RecGateway()).get("/static/app.css").text
    for token in TOKENS:
        assert f"{token}:" in css
    for needle in ("prefers-color-scheme: dark", "prefers-reduced-motion", ":focus-visible"):
        assert needle in css
    assert "@import" not in css
    assert "http" not in css
