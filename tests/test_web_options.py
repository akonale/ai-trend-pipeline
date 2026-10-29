"""Web tests for the per-post options form and the run page options list."""

import re

import pytest

from ai_trend_pipeline.daily_chart import ReviewState
from ai_trend_pipeline.post_options import PostOptions
from ai_trend_pipeline.web.view_models import build_run_view, option_lines
from tests.test_web import REVIEW, RecGateway, client_for

FIELDS = ("chart_type", "time_preset", "from_year", "to_year", "countries", "tone", "instructions")


def test_home_has_closed_details_with_labelled_fields() -> None:
    text = client_for(RecGateway()).get("/").text
    assert '<details class="more-options">' in text
    assert "<summary>More options</summary>" in text
    for name in FIELDS:
        assert f'name="{name}"' in text
        assert f'<label for="{name}"' in text
    assert "aria-describedby" in text
    assert "Up to 8, separated by commas, e.g. France, Japan" in text
    assert 'maxlength="300"' in text
    assert 'href="/?topic=Life%20expectancy"' in text


def test_valid_post_passes_parsed_options() -> None:
    gateway = RecGateway()
    res = client_for(gateway).post(
        "/runs",
        data={
            "topic": "Life expectancy",
            "chart_type": "bar",
            "from_year": "1990",
            "countries": "France, Japan",
            "tone": "punchy",
            "instructions": "Be brief",
        },
        follow_redirects=False,
    )
    assert res.status_code == 303
    options = gateway.started[0][1].options
    assert options == PostOptions(
        chart_type="bar",
        from_year=1990,
        countries=["France", "Japan"],
        tone="punchy",
        instructions="Be brief",
    )


def test_blank_options_post_passes_none() -> None:
    gateway = RecGateway()
    client_for(gateway).post("/runs", data={"topic": "abc", "chart_type": ""})
    assert gateway.started[0][1].options is None


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"chart_type": "pie"}, "Chart type:"),
        ({"time_preset": "last10", "from_year": "2000"}, "Time range:"),
        ({"from_year": "abc"}, "Time range:"),
        ({"countries": ",".join(f"c{i}" for i in range(9))}, "Countries:"),
        ({"instructions": "x" * 301}, "Instructions:"),
    ],
)
def test_invalid_options_are_400_and_keep_values(data: dict[str, str], message: str) -> None:
    gateway = RecGateway()
    res = client_for(gateway).post("/runs", data={"topic": "My topic", **data})
    assert res.status_code == 400
    assert re.search(r'role="alert">[^<]*' + re.escape(message), res.text)
    assert '<details class="more-options" open>' in res.text
    assert 'value="My topic"' in res.text
    # Select values outside the list cannot be re-selected, so only typed fields are checked.
    for key in ("from_year", "countries", "instructions"):
        if key in data:
            assert data[key] in res.text
    assert gateway.started == []


def test_other_errors_keep_option_values() -> None:
    res = client_for(RecGateway(exists=True)).post(
        "/runs", data={"topic": "abc", "countries": "France"}
    )
    assert res.status_code == 409
    assert 'value="France"' in res.text
    assert "open>" in res.text


def test_run_page_lists_options_and_notes_escaped() -> None:
    state = ReviewState(
        status="awaiting_decision",
        review=REVIEW,
        options=PostOptions(
            chart_type="area", time_preset="last20", countries=["France"], instructions="<b>hi</b>"
        ),
        option_notes=["No data for 'Narnia' in Life expectancy."],
    )
    text = client_for(RecGateway(state)).get("/runs/wf-1").text
    assert '<dl class="options-list">' in text
    assert "Area" in text
    assert "Last 20 years" in text
    assert "&lt;b&gt;hi&lt;/b&gt;" in text
    assert "<b>hi</b>" not in text
    assert "No data for &#39;Narnia&#39; in Life expectancy." in text


def test_run_page_without_options_has_no_list() -> None:
    state = ReviewState(status="awaiting_decision", review=REVIEW)
    assert "options-list" not in client_for(RecGateway(state)).get("/runs/wf-1").text


def test_time_range_text() -> None:
    def value(options: PostOptions) -> str:
        return option_lines(options)[0].value

    assert value(PostOptions(from_year=1990, to_year=2010)) == "1990 to 2010"
    assert value(PostOptions(from_year=1990)) == "From 1990"
    assert value(PostOptions(to_year=2010)) == "Up to 2010"
    assert option_lines(None) == []
    view = build_run_view("w", ReviewState(status="drafting"), "http://t")
    assert view.options == [] and view.option_notes == []
