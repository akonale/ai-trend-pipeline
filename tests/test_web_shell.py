"""Tests for the app shell: header, nav, user chip, footer and the optional user."""

from pathlib import Path

from fastapi.testclient import TestClient

from ai_trend_pipeline.daily_chart import ReviewState
from ai_trend_pipeline.web.dependencies import CF_EMAIL_HEADER
from ai_trend_pipeline.web.view_models import SHELL_TAGLINE, ShellView, build_shell
from tests.style_fakes import InMemoryStyleStore
from tests.test_web import REVIEW, RecGateway, auth_client, client_for
from tests.test_web_candidates import (
    FakeGateway as InboxGateway,
)
from tests.test_web_candidates import (
    client_for as inbox_client,
)
from tests.test_web_candidates import (
    page as inbox_page,
)
from tests.test_web_candidates import (
    summary as inbox_summary,
)
from tests.test_web_settings import FORM, make_client

CSS = (
    Path(__file__).parent.parent / "src" / "ai_trend_pipeline" / "web" / "static" / "app.css"
).read_text()

CF = "cloudflare"
BLANK_HEADERS = [{}, {CF_EMAIL_HEADER: "   "}]


def current_labels(shell: ShellView) -> list[str]:
    return [item.label for item in shell.nav if item.current]


def test_build_shell_current_flags() -> None:
    for path in ("/new", "/runs", "/runs/wf-1"):
        assert current_labels(build_shell(path, None, "local")) == ["New chart"], path
    assert current_labels(build_shell("/", None, "local")) == ["Stories"]
    assert current_labels(build_shell("/settings", None, "local")) == ["Your style"]
    assert current_labels(build_shell("/health", None, "local")) == []
    assert current_labels(build_shell("/stories", None, "local")) == ["Stories"]
    assert current_labels(build_shell("/stories/story-v1-abc", None, "local")) == ["Stories"]
    assert current_labels(build_shell("/candidates", None, "local")) == ["Candidates"]
    assert current_labels(build_shell("/candidates/candidate-v1-abc", None, "local")) == [
        "Candidates"
    ]


def test_build_shell_fields() -> None:
    local = build_shell("/", "a@b.co", "local")
    assert local.env_tag == "dev"
    assert local.user_email == "a@b.co"
    assert build_shell("/", None, CF).env_tag == ""
    assert build_shell("/", None, CF).user_email is None
    assert [item.href for item in local.nav] == [
        "/stories",
        "/candidates",
        "/new",
        "/settings",
    ]
    assert local.brand_href == "/stories"
    assert [item.label for item in local.nav] == [
        "Stories",
        "Candidates",
        "New chart",
        "Your style",
    ]
    assert all(item.badge is None for item in local.nav)
    assert local.tagline == SHELL_TAGLINE


def test_optional_user_normalises_header_for_chip() -> None:
    client = auth_client(RecGateway(), CF)
    text = client.get("/new", headers={CF_EMAIL_HEADER: " Friend@Example.com "}).text
    assert "friend@example.com</span>" in text
    assert "Friend@Example.com" not in text


def test_cloudflare_without_header_get_pages_have_no_chip() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    client = auth_client(gateway, CF)
    for headers in BLANK_HEADERS:
        for path in ("/new", "/runs/wf-1", "/runs/wf-1/status", "/health"):
            res = client.get(path, headers=headers)
            assert res.status_code == 200, path
            assert "user-chip" not in res.text, path
            assert "dev-tag" not in res.text, path


def test_cloudflare_without_header_posts_and_settings_are_401() -> None:
    client = auth_client(RecGateway(), CF)
    assert client.post("/runs", data={"topic": "abc"}).status_code == 401
    assert client.post("/runs/wf-1/decision", data={"approved": "true"}).status_code == 401
    settings_client = make_client(InMemoryStyleStore(), CF)
    assert settings_client.post("/settings", data=FORM).status_code == 401
    # The documented exception: GET /settings needs an email to load the saved style.
    assert settings_client.get("/settings").status_code == 401


def test_local_mode_shows_dev_user_and_tag() -> None:
    client = auth_client(RecGateway())
    for path in ("/new", "/health"):
        text = client.get(path, headers={CF_EMAIL_HEADER: "other@example.com"}).text
        assert "dev@localhost" in text, path
        assert "other@example.com" not in text, path
        assert 'class="user-chip"' in text, path
        assert 'class="dev-tag"' in text, path


def test_cloudflare_mode_never_shows_dev_tag() -> None:
    text = auth_client(RecGateway(), CF).get("/new", headers={CF_EMAIL_HEADER: "a@b.co"}).text
    assert "dev-tag" not in text
    assert "a@b.co" in text


def assert_shell(text: str) -> None:
    assert 'class="skip-link" href="#main"' in text
    assert text.index('class="skip-link"') < text.index('<header class="site-header">')
    assert 'aria-label="Main"' in text
    assert 'id="main"' in text
    assert 'class="site-footer"' in text
    assert SHELL_TAGLINE in text
    assert "An editorial desk" not in text


def shell_pages() -> dict[str, str]:
    awaiting = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW))
    approved = RecGateway(ReviewState(status="approved", review=REVIEW))
    pages = {
        "home": client_for(RecGateway()).get("/new").text,
        "run": client_for(awaiting).get("/runs/wf-1").text,
        "run 404": client_for(RecGateway()).get("/runs/x").text,
        "settings": make_client(InMemoryStyleStore()).get("/settings").text,
        "health": client_for(RecGateway()).get("/health").text,
        "runs 400": client_for(RecGateway()).post("/runs", data={"topic": " "}).text,
        "decision 409": client_for(approved)
        .post("/runs/wf-1/decision", data={"approved": "true"})
        .text,
        "settings 400": make_client(InMemoryStyleStore())
        .post("/settings", data={**FORM, "palette": "neon"})
        .text,
    }
    return pages


def test_every_page_has_the_shell() -> None:
    for name, text in shell_pages().items():
        try:
            assert_shell(text)
        except AssertionError as exc:
            raise AssertionError(f"{name}: {exc}") from exc


def test_error_rerender_status_codes() -> None:
    client = client_for(RecGateway())
    assert client.get("/runs/x").status_code == 404
    assert client.post("/runs", data={"topic": " "}).status_code == 400


def aria_current_links(client: TestClient, path: str) -> list[str]:
    text = client.get(path).text
    return [
        part.split(">")[1].split("<")[0]
        for part in text.split("<a ")
        if 'aria-current="page"' in part.split(">")[0]
    ]


def test_aria_current_on_the_right_link() -> None:
    client = client_for(RecGateway())
    assert client.get("/new").text.count('aria-current="page"') == 1
    assert aria_current_links(client, "/new") == ["New chart"]
    settings = make_client(InMemoryStyleStore())
    assert settings.get("/settings").text.count('aria-current="page"') == 1
    assert aria_current_links(settings, "/settings") == ["Your style"]
    assert client.get("/health").text.count('aria-current="page"') == 0
    inbox = inbox_client(InboxGateway(inbox_page(inbox_summary())))
    assert inbox.get("/candidates").text.count('aria-current="page"') == 1
    assert aria_current_links(inbox, "/candidates") == ["Candidates"]


def test_css_has_skip_link_and_no_tagline() -> None:
    assert ".skip-link" in CSS
    assert ".skip-link:focus" in CSS
    assert ".tagline" not in CSS
