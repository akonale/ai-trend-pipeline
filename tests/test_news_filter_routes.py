"""Route tests for /news-filter with an in-memory store."""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.news_filter import (
    NewsFilterSettings,
    NewsFilterStoreError,
    NewsFilterUnavailable,
)
from ai_trend_pipeline.services.news_filter import NewsFilterService
from ai_trend_pipeline.story_classification import NewsPolicyMode
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import (
    CF_EMAIL_HEADER,
    DEV_USER_EMAIL,
    get_news_filter_service,
)
from tests.test_news_filter_service import InMemoryNewsFilterStore

CSS = (
    Path(__file__).parent.parent / "src" / "ai_trend_pipeline" / "web" / "static" / "app.css"
).read_text()
WHEN = datetime(2026, 11, 11, 9, 30, tzinfo=UTC)


def make_client(
    store: InMemoryNewsFilterStore, mode: Literal["local", "cloudflare"] = "local"
) -> TestClient:
    app = create_app(Settings(auth_mode=mode))
    service = NewsFilterService(store, clock=lambda: WHEN)

    async def fake_service() -> NewsFilterService:
        return service

    app.dependency_overrides[get_news_filter_service] = fake_service
    return TestClient(app, follow_redirects=False)


def test_get_news_filter_shows_current_mode_and_future_poll_copy() -> None:
    res = make_client(InMemoryNewsFilterStore()).get("/news-filter")
    assert res.status_code == 200
    text = res.text
    assert "<fieldset" in text and "<legend>" in text
    for mode in NewsPolicyMode:
        assert f'name="mode" value="{mode.value}"' in text
    assert 'value="strict" checked' in text
    assert 'value="balanced" checked' not in text
    assert "exceptional general-audience stories" in text
    assert "more clearly relevant stories" in text
    assert "borderline or industry-adjacent" in text
    assert "still requiring central AI" in text or "AI stays central" in text
    assert "future polls" in text
    assert "already in the inbox" in text
    assert 'role="status"' not in text
    assert 'role="alert"' not in text
    for forbidden in ("threshold", 'type="number"', 'type="text"', "<textarea", "<script"):
        assert forbidden not in text.replace('<script src="/static/htmx.min.js">', "")


def test_get_news_filter_shows_saved_mode_and_audit() -> None:
    store = InMemoryNewsFilterStore(
        NewsFilterSettings(
            mode=NewsPolicyMode.BALANCED, updated_at=WHEN, updated_by="ash@example.com"
        )
    )
    text = make_client(store).get("/news-filter?saved=1").text
    assert 'value="balanced" checked' in text
    assert 'value="strict" checked' not in text
    assert 'role="status"' in text
    assert "ash@example.com" in text
    assert "2026-11-11" in text


def test_post_news_filter_validates_saves_and_redirects() -> None:
    store = InMemoryNewsFilterStore()
    client = make_client(store)
    res = client.post("/news-filter", data={"mode": "relaxed"})
    assert res.status_code == 303
    assert res.headers["location"] == "/news-filter?saved=1"
    assert store.stored == NewsFilterSettings(
        mode=NewsPolicyMode.RELAXED, updated_at=WHEN, updated_by=DEV_USER_EMAIL
    )

    for bad in ({"mode": "loose"}, {"mode": ""}, {}, {"mode": "0.2"}):
        before = store.stored
        res = client.post("/news-filter", data=bad)
        assert res.status_code == 400, bad
        assert 'role="alert"' in res.text
        assert 'value="relaxed" checked' in res.text  # still shows what is actually saved
        assert store.stored == before


def test_news_filter_requires_user_and_handles_store_unavailable() -> None:
    store = InMemoryNewsFilterStore()
    client = make_client(store, "cloudflare")
    assert client.get("/news-filter").status_code == 401
    assert client.post("/news-filter", data={"mode": "relaxed"}).status_code == 401
    assert store.stored is None
    headers = {CF_EMAIL_HEADER: "Friend@Example.com"}
    assert (
        client.post("/news-filter", data={"mode": "balanced"}, headers=headers).status_code == 303
    )
    assert store.stored is not None
    assert store.stored.updated_by == "friend@example.com"

    store.error = NewsFilterUnavailable("postgresql://user:secret@db")
    for res in (
        client.get("/news-filter", headers=headers),
        client.post("/news-filter", data={"mode": "strict"}, headers=headers),
    ):
        assert res.status_code == 503
        assert 'role="alert"' in res.text
        assert "secret" not in res.text
        assert "postgresql" not in res.text

    store.error = NewsFilterStoreError("bad row")
    res = client.get("/news-filter", headers=headers)
    assert res.status_code == 503
    assert "bad row" not in res.text


def test_news_filter_navigation_accessibility_and_mobile_layout() -> None:
    text = make_client(InMemoryNewsFilterStore()).get("/news-filter").text
    nav = re.search(r'<nav class="site-nav".*?</nav>', text, re.S)
    assert nav is not None
    assert '<a href="/news-filter" aria-current="page">News filter</a>' in nav.group(0)
    assert nav.group(0).count("aria-current") == 1
    assert "<h1>" in text and text.count("<h1>") == 1
    # Every radio sits inside its label, so the whole option is the click and screen reader target.
    assert len(re.findall(r'<label class="filter-option">', text)) == 3
    assert "Save News filter" in text or "Save filter" in text
    other = make_client(InMemoryNewsFilterStore()).get("/settings").text
    assert 'href="/news-filter" aria-current' not in other
    assert 'href="/news-filter">News filter' in other

    assert ".filter-option:has(input:focus-visible)" in CSS
    assert ".filter-option:has(input:checked)" in CSS
    assert "prefers-reduced-motion" in CSS
    block = re.search(r"\.filter-options\s*\{([^}]*)\}", CSS)
    assert block is not None
    assert "minmax(" not in block.group(1)  # one column at 360px, no fixed wide tracks
    assert "overflow-x" not in block.group(1)
