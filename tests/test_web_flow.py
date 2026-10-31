"""Tests for the guided flow: step 1, step 2, the stepper and the run page stepper."""

import re
from pathlib import Path
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.daily_chart import ReviewState
from ai_trend_pipeline.services.charts import ChartService
from ai_trend_pipeline.services.styles import StyleService, StyleStoreUnavailable
from ai_trend_pipeline.style import ChartStyle
from ai_trend_pipeline.web.app import create_app
from ai_trend_pipeline.web.dependencies import (
    DEV_USER_EMAIL,
    get_chart_service,
    get_style_service,
)
from ai_trend_pipeline.web.view_models import (
    OptionsFormValues,
    build_shell,
    build_stepper,
    flow_query,
)
from tests.style_fakes import InMemoryStyleStore
from tests.test_web import REVIEW, RecGateway, Status, lazy_store_client
from tests.test_web_settings import BrokenStore

FIELDS = ("chart_type", "time_preset", "from_year", "to_year", "countries", "tone", "instructions")
CSS = (Path(__file__).parent.parent / "src/ai_trend_pipeline/web/static/app.css").read_text()


def flow_client(
    store: object | None = None,
    gateway: RecGateway | None = None,
    mode: Literal["local", "cloudflare"] = "local",
) -> TestClient:
    settings = Settings(auth_mode=mode)
    app = create_app(settings)
    chart_service = ChartService(gateway or RecGateway(), settings)
    style_service = StyleService(store if store is not None else InMemoryStyleStore())  # pyright: ignore[reportArgumentType]  # structural fake

    async def fake_chart() -> ChartService:
        return chart_service

    async def fake_style() -> StyleService:
        return style_service

    app.dependency_overrides[get_chart_service] = fake_chart
    app.dependency_overrides[get_style_service] = fake_style
    return TestClient(app)


def stepper_items(text: str) -> list[str]:
    match = re.search(r'<ol id="flow-stepper".*?</ol>', text, re.S)
    assert match, "no stepper"
    return re.findall(r"<li .*?</li>", match.group(0), re.S)


def current_steps(text: str) -> list[str]:
    return [i for i in stepper_items(text) if 'aria-current="step"' in i]


# ---- Step 1 ----


def test_step_one_page() -> None:
    text = flow_client().get("/new").text
    assert '<form class="topic-form" method="get" action="/new/style">' in text
    assert 'name="topic"' in text
    assert "Next: choose a style" in text
    assert 'aria-label="Example topics"' in text
    assert 'href="/new?topic=Life%20expectancy"' in text
    assert "Tap an example to put it in the box." in text
    assert 'name="chart_type"' not in text
    assert "<details" not in text
    current = current_steps(text)
    assert len(current) == 1 and "step--current" in current[0] and ">Topic<" in current[0]


def test_step_one_carries_options() -> None:
    text = flow_client().get("/new?topic=a&countries=France").text
    assert re.search(r'<input type="hidden" name="countries" value="France">', text)
    assert 'type="hidden"' not in flow_client().get("/new?topic=a").text


# ---- Step 2 routes ----


@pytest.mark.parametrize("query", ["", "?topic=", "?topic=%20%20"])
def test_step_two_redirects_without_topic(query: str) -> None:
    res = flow_client().get(f"/new/style{query}", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/new"


def test_step_two_too_long_renders_step_one() -> None:
    res = flow_client().get("/new/style", params={"topic": "x" * 201, "countries": "France"})
    assert res.status_code == 400
    assert 'action="/new/style"' in res.text
    assert re.search(
        r'role="alert">Your topic is too long\. Use 200 characters or fewer\.<', res.text
    )
    assert f'value="{"x" * 200}"' in res.text
    assert "x" * 201 not in res.text
    assert 'name="countries" value="France"' in res.text


def test_step_two_page() -> None:
    text = flow_client().get("/new/style?topic=Life%20expectancy").text
    assert "Set up this chart - Topic Studio" in text
    assert 'class="topic-recap__text">Life expectancy<' in text
    assert '<input type="hidden" name="topic" value="Life expectancy">' in text
    for name in FIELDS:
        assert f'name="{name}"' in text
        assert f'<label for="{name}"' in text
    assert 'action="/runs"' in text
    assert 'method="post"' in text
    assert "Generate chart" in text
    assert 'formaction="/new"' in text and 'formmethod="get"' in text and "formnovalidate" in text
    assert text.index("Generate chart") < text.index('formaction="/new"')
    assert 'href="/new?topic=Life%20expectancy"' in text
    assert "<details" not in text


def test_step_two_prefills_from_query() -> None:
    text = flow_client().get("/new/style?topic=a&countries=France&tone=punchy&from_year=1990").text
    assert 'value="France"' in text
    assert 'value="1990"' in text
    assert '<option value="punchy" selected>' in text
    assert 'href="/new?topic=a&amp;from_year=1990&amp;countries=France&amp;tone=punchy"' in text


def test_step_two_stepper() -> None:
    items = stepper_items(flow_client().get("/new/style?topic=a").text)
    assert "step--done" in items[0] and '<a href="/new?topic=a">' in items[0]
    assert ", done" in items[0]
    assert 'aria-current="step"' in items[1] and "step--current" in items[1]
    assert "step--upcoming" in items[2] and "step--upcoming" in items[3]
    assert "<a " not in items[1] + items[2] + items[3]


def test_step_two_style_summary() -> None:
    store = InMemoryStyleStore(
        {
            DEV_USER_EMAIL: ChartStyle(
                palette="ocean",
                font="serif",
                tone="punchy",
                hashtags=["ai", "data"],
                signature="By Ash",
            )
        }
    )
    text = flow_client(store).get("/new/style?topic=a").text
    for expected in ("Your saved style", "Ocean", "Serif", "Punchy", "#ai #data", "By Ash"):
        assert expected in text
    assert 'class="swatch-colors"' in text
    assert 'href="/settings"' in text
    assert "Changes there apply to every chart." in text
    assert store.get_calls == [DEV_USER_EMAIL]


def test_step_two_style_summary_defaults_show_none() -> None:
    text = flow_client().get("/new/style?topic=a").text
    assert "Classic" in text and "None" in text


def test_step_two_store_unavailable_is_200_with_notice() -> None:
    res = flow_client(BrokenStore(StyleStoreUnavailable())).get("/new/style?topic=a")
    assert res.status_code == 200
    assert "could not be loaded right now. Generate still works." in res.text
    assert "Generate chart" in res.text


def test_step_two_cloudflare_without_header_never_reads_store() -> None:
    client = flow_client(BrokenStore(), mode="cloudflare")
    res = client.get("/new/style?topic=a")
    assert res.status_code == 200
    assert "Sign in to see your saved style." in res.text


# ---- POST /runs re-renders ----


def assert_step_two(text: str, topic: str) -> None:
    assert len(current_steps(text)) == 1 and ">Style<" in current_steps(text)[0]
    assert f'<input type="hidden" name="topic" value="{topic}">' in text
    assert "Set up this chart" in text
    assert "Your saved style is applied when you generate." in text


def test_post_bad_options_rerenders_step_two() -> None:
    res = flow_client().post(
        "/runs", data={"topic": "My topic", "chart_type": "pie", "countries": "France"}
    )
    assert res.status_code == 400
    assert_step_two(res.text, "My topic")
    assert "Chart type: pick line, bar or area." in res.text
    assert 'value="France"' in res.text


def test_post_daily_cap_rerenders_step_two() -> None:
    gateway = RecGateway()
    gateway.count = 10
    res = flow_client(gateway=gateway).post("/runs", data={"topic": "abc", "tone": "punchy"})
    assert res.status_code == 429
    assert_step_two(res.text, "abc")
    assert "00:00 UTC" in res.text
    assert '<option value="punchy" selected>' in res.text


def test_post_run_exists_rerenders_step_two() -> None:
    res = flow_client(gateway=RecGateway(exists=True)).post(
        "/runs", data={"topic": "abc", "instructions": "Be brief"}
    )
    assert res.status_code == 409
    assert_step_two(res.text, "abc")
    assert "already exists" in res.text and "Be brief" in res.text


def test_post_style_unavailable_rerenders_step_two(monkeypatch: pytest.MonkeyPatch) -> None:
    client = lazy_store_client(monkeypatch, RecGateway(), BrokenStore(StyleStoreUnavailable()))
    res = client.post("/runs", data={"topic": "abc", "from_year": "1990"})
    assert res.status_code == 503
    assert_step_two(res.text, "abc")
    assert "load your style settings right now. Please try again in a moment." in res.text
    assert 'value="1990"' in res.text


def test_post_blank_topic_rerenders_step_one_with_carry() -> None:
    res = flow_client().post("/runs", data={"topic": "  ", "countries": "France"})
    assert res.status_code == 400
    assert "Error: enter a topic of 1 to 200 characters." in res.text
    assert 'action="/new/style"' in res.text
    assert 'name="countries" value="France"' in res.text
    assert len(current_steps(res.text)) == 1 and ">Topic<" in current_steps(res.text)[0]


def test_post_success_still_redirects_with_options() -> None:
    gateway = RecGateway()
    res = flow_client(gateway=gateway).post(
        "/runs", data={"topic": "abc", "chart_type": "bar"}, follow_redirects=False
    )
    assert res.status_code == 303
    assert res.headers["location"] == f"/runs/{gateway.started[0][0]}"
    options = gateway.started[0][1].options
    assert options is not None and options.chart_type == "bar"


# ---- Run page stepper ----


@pytest.mark.parametrize(
    ("status", "number"),
    [
        ("drafting", 3),
        ("awaiting_decision", 3),
        ("posting", 3),
        ("approved", 4),
        ("rejected", 4),
        ("expired", 4),
    ],
)
def test_run_page_stepper(status: Status, number: int) -> None:
    review = None if status == "drafting" else REVIEW
    gateway = RecGateway(ReviewState(status=status, review=review))
    text = flow_client(gateway=gateway).get("/runs/wf-1").text
    items = stepper_items(text)
    assert len(current_steps(text)) == 1 and "step--current" in items[number - 1]
    assert all("step--done" in i for i in items[: number - 1])
    assert all("step--upcoming" in i for i in items[number:])
    assert "<a " not in "".join(items)
    assert "hx-swap-oob" not in text.split('id="run-status"')[0]
    assert text.count('id="flow-stepper"') == 1
    assert 'id="flow-stepper" class="stepper" aria-label="Progress">' in text


def test_status_fragment_carries_oob_stepper() -> None:
    gateway = RecGateway(ReviewState(status="approved", review=REVIEW))
    text = flow_client(gateway=gateway).get("/runs/wf-1/status").text
    assert 'id="flow-stepper"' in text and 'hx-swap-oob="true"' in text
    assert "<html" not in text


def test_stepper_absent_elsewhere() -> None:
    client = flow_client()
    for path in ("/settings", "/health", "/runs/nope"):
        assert 'class="stepper"' not in client.get(path).text, path


# ---- Unit tests ----


def test_build_stepper_states() -> None:
    for current in (1, 2, 3, 4):
        stepper = build_stepper(current)
        assert [s.label for s in stepper.steps] == ["Topic", "Style", "Review", "Done"]
        assert [s.state for s in stepper.steps] == [
            "done" if n < current else "current" if n == current else "upcoming"
            for n in (1, 2, 3, 4)
        ]
        assert [s.number for s in stepper.steps if s.aria_current] == [current]
        assert all(s.href is None for s in stepper.steps)
        assert stepper.oob is False


def test_build_stepper_href_only_on_done_step_one() -> None:
    assert [s.href for s in build_stepper(2, "/new?topic=a").steps] == [
        "/new?topic=a",
        None,
        None,
        None,
    ]
    assert all(s.href is None for s in build_stepper(1, "/new?topic=a").steps)
    assert all(s.href is None for s in build_stepper(3).steps)


def test_flow_query() -> None:
    assert flow_query("Life expectancy", OptionsFormValues()) == "topic=Life%20expectancy"
    values = OptionsFormValues(tone="punchy", chart_type="bar", countries="France, Japan")
    assert (
        flow_query("a", values) == "topic=a&chart_type=bar&countries=France%2C%20Japan&tone=punchy"
    )


def test_build_shell_marks_new_chart_on_new_paths() -> None:
    for path in ("/new", "/new/style"):
        shell = build_shell(path, None, "local")
        assert [n.label for n in shell.nav if n.current] == ["New chart"], path


def test_root_redirects_to_stories() -> None:
    res = flow_client().get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/stories"


def test_css_flow_rules() -> None:
    assert ".step-actions" in CSS
    assert ".topic-recap__text" in CSS
    assert ".chart-options label" in CSS
    assert ".more-options" not in CSS
