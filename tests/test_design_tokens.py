"""Design-system checks: colour tokens are hex and every text/background pair meets WCAG AA."""

import re
from pathlib import Path

import pytest

from ai_trend_pipeline.daily_chart import ReviewState
from tests.test_web import REVIEW_FAILED, RecGateway, client_for

CSS_PATH = Path(__file__).parent.parent / "src/ai_trend_pipeline/web/static/app.css"
CSS = CSS_PATH.read_text()

# (foreground, background, minimum ratio)
PAIRS: list[tuple[str, str, float]] = [
    ("text", "bg", 4.5),
    ("text", "grid", 4.5),
    ("text", "surface", 4.5),
    ("text", "field-bg", 4.5),
    ("muted", "bg", 4.5),
    ("muted", "grid", 4.5),
    ("muted", "surface", 4.5),
    ("muted", "field-bg", 4.5),
    ("accent", "bg", 4.5),
    ("accent", "grid", 4.5),
    ("accent", "surface", 4.5),
    ("accent-contrast", "accent", 4.5),
    ("on-fill", "highlight", 4.5),
    ("on-fill", "warning", 4.5),
    ("on-fill", "info", 4.5),
    ("on-fill", "success", 4.5),
    ("on-fill", "danger", 4.5),
    ("on-fill", "neutral", 4.5),
    ("on-fill-muted", "neutral", 4.5),
    ("on-fill", "chart-bg", 4.5),
    ("border", "bg", 3),
    ("border", "grid", 3),
    ("border", "surface", 3),
    ("field-border", "field-bg", 3),
    ("field-border", "surface", 3),
    ("focus", "bg", 3),
    ("focus", "grid", 3),
    ("focus", "surface", 3),
]


def luminance(hex_color: str) -> float:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def color_tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--color-([a-z-]+):\s*([^;]+);", block))


def blocks() -> tuple[str, str]:
    dark_start = CSS.index("@media (prefers-color-scheme: dark)")
    match = re.search(r":root\s*{(.*?)}", CSS[dark_start:], re.S)
    assert match
    return CSS[: CSS.index("@media")], match.group(1)


def theme(name: str) -> dict[str, str]:
    light_block, dark_block = blocks()
    tokens = color_tokens(light_block)
    if name == "dark":
        tokens.update(color_tokens(dark_block))
    return tokens


def test_contrast_math() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_color_tokens_are_hex() -> None:
    light_block, dark_block = blocks()
    for block in (light_block, dark_block):
        tokens = color_tokens(block)
        assert tokens
        for name, value in tokens.items():
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()), f"--color-{name}: {value}"
    assert len(color_tokens(light_block)) >= 20


@pytest.mark.parametrize("name", ["light", "dark"])
def test_pairs_meet_wcag(name: str) -> None:
    tokens = theme(name)
    for fg, bg, minimum in PAIRS:
        ratio = contrast(tokens[fg], tokens[bg])
        assert ratio >= minimum, f"{name}: {fg} on {bg} is {ratio:.2f}, needs {minimum}"


def test_component_classes_present() -> None:
    needles = (
        ".stepper", ".step ", ".step__num", ".step--done", ".step--current", ".step--upcoming",
        ".btn--secondary", ".user-chip", ".site-footer", ".dev-tag", ".check", ".card--chart",
        ".visually-hidden", ".btn:active", ":focus-visible", "prefers-reduced-motion",
        "word-break: keep-all",
    )  # fmt: skip
    for needle in needles:
        assert needle in CSS, needle


def test_chart_section_uses_card_chart() -> None:
    gateway = RecGateway(ReviewState(status="awaiting_decision", review=REVIEW_FAILED))
    text = client_for(gateway).get("/runs/wf-1").text
    assert 'class="card card--chart"' in text
