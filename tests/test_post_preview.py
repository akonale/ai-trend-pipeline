"""Pure post preview: the exact text the formatter would send."""

import ast
from pathlib import Path

from ai_trend_pipeline.media.models import MediaOutcome
from ai_trend_pipeline.publishing import preview as preview_module
from ai_trend_pipeline.publishing.formatter import format_publication
from ai_trend_pipeline.publishing.models import PublicationErrorCode
from ai_trend_pipeline.publishing.preview import preview_post
from ai_trend_pipeline.publishing.registry import BLUESKY_CAPABILITIES
from tests.publishing_fixtures import make_approved_package


def test_preview_text_matches_format_publication_text_and_facets() -> None:
    package, digest = make_approved_package(MediaOutcome.STATIC)
    preview = preview_post(package, digest)
    out = format_publication(package, digest, BLUESKY_CAPABILITIES)
    assert preview.text == out.text
    assert preview.link_text == "news.example"
    assert preview.body_text + preview.link_text == preview.text
    assert preview.body_text.endswith("Source: ")
    assert preview.char_count == len(out.text)
    assert preview.max_chars == 300
    assert preview.problem is None


def test_preview_over_limit_returns_text_and_post_text_too_long() -> None:
    package, digest = make_approved_package(None, post_text="x" * 290)
    preview = preview_post(package, digest)
    assert preview.problem is PublicationErrorCode.POST_TEXT_TOO_LONG
    assert preview.text.startswith("x" * 290)
    assert preview.char_count > preview.max_chars


def test_preview_text_only_static_and_gif_packages() -> None:
    for outcome in (None, MediaOutcome.TEXT_ONLY, MediaOutcome.STATIC, MediaOutcome.GIF):
        package, digest = make_approved_package(outcome)
        preview = preview_post(package, digest)
        assert preview.problem is None
        assert preview.text.endswith("Source: news.example")


def test_preview_module_imports_no_httpx_clock_or_services() -> None:
    tree = ast.parse(Path(preview_module.__file__ or "").read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    banned = ("httpx", "datetime", "time", "random", "ai_trend_pipeline.services")
    assert not [n for n in names if n.split(".")[0] in banned or n.startswith(banned[-1])]
    assert "ai_trend_pipeline.web" not in names
