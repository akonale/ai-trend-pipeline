"""Route tests for /settings with an in-memory style store."""

from typing import Literal

from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.services.styles import StyleService, StyleStoreUnavailable
from ai_trend_pipeline.style import ChartStyle
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import DEV_USER_EMAIL, get_style_service
from tests.style_fakes import InMemoryStyleStore

CF_HEADER = "Cf-Access-Authenticated-User-Email"
FORM = {
    "palette": "ocean",
    "font": "serif",
    "tone": "punchy",
    "hashtags": "#ai, data",
    "signature": "By Ash",
}


def make_client(
    store: InMemoryStyleStore, mode: Literal["local", "cloudflare"] = "local"
) -> TestClient:
    app = create_app(Settings(auth_mode=mode))
    service = StyleService(store)

    async def fake_service() -> StyleService:
        return service

    app.dependency_overrides[get_style_service] = fake_service
    return TestClient(app)


def test_get_shows_email_and_defaults() -> None:
    res = make_client(InMemoryStyleStore()).get("/settings")
    assert res.status_code == 200
    assert DEV_USER_EMAIL in res.text
    assert 'value="classic" checked' in res.text
    assert '<option value="sans" selected>' in res.text
    assert '<option value="neutral" selected>' in res.text
    assert 'maxlength="40"' in res.text
    assert "separated by spaces or commas" in res.text
    assert 'role="status"' not in res.text


def test_get_shows_saved_values() -> None:
    store = InMemoryStyleStore(
        {
            DEV_USER_EMAIL: ChartStyle(
                palette="ocean", font="mono", tone="academic", hashtags=["ai"], signature="Me"
            )
        }
    )
    text = make_client(store).get("/settings").text
    assert 'value="ocean" checked' in text
    assert '<option value="mono" selected>' in text
    assert '<option value="academic" selected>' in text
    assert 'value="#ai"' in text
    assert 'value="Me"' in text


def test_home_has_settings_link() -> None:
    res = make_client(InMemoryStyleStore()).get("/")
    assert 'href="/settings"' in res.text
    assert "Your style" in res.text


def test_valid_post_saves_and_redirects() -> None:
    store = InMemoryStyleStore()
    client = make_client(store)
    res = client.post("/settings", data=FORM, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/settings?saved=1"
    assert store.styles[DEV_USER_EMAIL] == ChartStyle(
        palette="ocean", font="serif", tone="punchy", hashtags=["ai", "data"], signature="By Ash"
    )
    text = client.get("/settings?saved=1").text
    assert 'role="status"' in text
    assert "Saved. Your next chart will use this style." in text


def test_invalid_posts_are_400_and_keep_values() -> None:
    cases = {
        "palette": {**FORM, "palette": "neon"},
        "hashtags": {**FORM, "hashtags": "a b c d"},
        "signature": {**FORM, "signature": "x" * 41},
    }
    for field, form in cases.items():
        store = InMemoryStyleStore()
        res = make_client(store).post("/settings", data=form)
        assert res.status_code == 400, field
        assert 'role="alert"' in res.text
        assert field.capitalize() in res.text
        assert store.styles == {}
    res = make_client(InMemoryStyleStore()).post("/settings", data=cases["hashtags"])
    assert 'value="a b c d"' in res.text
    assert "at most 3" in res.text
    assert '<option value="serif" selected>' in res.text


def test_cloudflare_requires_header() -> None:
    client = make_client(InMemoryStyleStore(), "cloudflare")
    assert client.get("/settings").status_code == 401
    assert client.post("/settings", data=FORM).status_code == 401


def test_cloudflare_users_see_their_own_style() -> None:
    store = InMemoryStyleStore()
    client = make_client(store, "cloudflare")
    a, b = {CF_HEADER: "a@x.com"}, {CF_HEADER: "b@x.com"}
    client.post("/settings", data=FORM, headers=a)
    client.post("/settings", data={**FORM, "palette": "sunset", "signature": "Bee"}, headers=b)
    ta, tb = client.get("/settings", headers=a).text, client.get("/settings", headers=b).text
    assert "a@x.com" in ta and 'value="ocean" checked' in ta and 'value="By Ash"' in ta
    assert "b@x.com" in tb and 'value="sunset" checked' in tb and 'value="Bee"' in tb


class BrokenStore:
    """Raises on ANY call, to prove a route never touches the store."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc or AssertionError("store must not be used")

    async def get(self, email: str) -> ChartStyle | None:
        raise self.exc

    async def save(self, email: str, style: ChartStyle) -> None:
        raise self.exc


def test_unavailable_store_gives_friendly_503_on_settings() -> None:
    store = BrokenStore(StyleStoreUnavailable())
    client = make_client(store)  # pyright: ignore[reportArgumentType]  # structural fake
    res = client.get("/settings")
    assert res.status_code == 503
    assert 'role="alert"' in res.text
    assert "load your style settings right now. Please try again in a moment." in res.text
    res = client.post("/settings", data=FORM)
    assert res.status_code == 503
    assert "load your style settings right now. Please try again in a moment." in res.text
    assert 'value="By Ash"' in res.text


def test_signature_with_control_character_is_400() -> None:
    store = InMemoryStyleStore()
    res = make_client(store).post("/settings", data={**FORM, "signature": "a\x07b"})
    assert res.status_code == 400
    assert "Signature" in res.text
    assert store.styles == {}
