"""Pure publication contracts."""

import ast
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.publishing.models import (
    RETRYABLE_CODES,
    DestinationName,
    LinkFacet,
    PublicationErrorCode,
    PublicationInput,
    PublicationPackage,
    publication_key,
)
from tests.publishing_fixtures import make_approved_package

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
SRC = Path(__file__).parent.parent / "src" / "ai_trend_pipeline" / "publishing"


def test_publication_key_is_deterministic_and_varies_by_story_destination_hash() -> None:
    key = publication_key("story-1", DestinationName.BLUESKY, HASH_A)
    assert key == f"publish-v1-story-1-bluesky-{HASH_A}"
    assert key == publication_key("story-1", DestinationName.BLUESKY, HASH_A)
    assert key != publication_key("story-2", DestinationName.BLUESKY, HASH_A)
    assert key != publication_key("story-1", DestinationName.BLUESKY, HASH_B)


def facet(start: int, end: int) -> LinkFacet:
    return LinkFacet(byte_start=start, byte_end=end, uri="https://example.com/a")  # pyright: ignore[reportArgumentType]


def test_package_rejects_out_of_range_or_overlapping_facets() -> None:
    def build(*facets: LinkFacet) -> PublicationPackage:
        return PublicationPackage(
            destination=DestinationName.BLUESKY,
            story_id="s",
            content_hash=HASH_A,
            text="0123456789",
            facets=list(facets),
        )

    assert build(facet(0, 4), facet(4, 10)).facets
    with pytest.raises(ValidationError):
        build(facet(5, 11))
    with pytest.raises(ValidationError):
        build(facet(0, 5), facet(4, 8))
    with pytest.raises(ValidationError):
        facet(5, 5)
    with pytest.raises(ValidationError):
        facet(-1, 3)


def test_input_rejects_story_id_mismatch_bad_hash_and_naive_datetimes() -> None:
    package, _ = make_approved_package(None)
    good: dict[str, object] = {
        "destination": DestinationName.BLUESKY,
        "story_id": package.story_id,
        "candidate_id": "cand-1",
        "story_created_at": NOW,
        "requested_at": NOW,
        "package": package,
        "content_hash": HASH_A,
    }
    parsed = PublicationInput.model_validate(good)
    offset = timezone(timedelta(hours=2))
    shifted = PublicationInput.model_validate({**good, "requested_at": NOW.astimezone(offset)})
    assert shifted.requested_at.utcoffset() == timedelta(0)
    for bad in (
        {"story_id": "other"},
        {"content_hash": "xyz"},
        {"requested_at": datetime(2026, 5, 1, 12, 0)},
        {"story_created_at": datetime(2026, 5, 1, 12, 0)},
    ):
        with pytest.raises(ValidationError):
            PublicationInput.model_validate({**good, **bad})
    assert parsed.package.story_id == package.story_id


def test_retryable_codes_are_the_transient_ones() -> None:
    assert {c.value for c in RETRYABLE_CODES} == {
        "DestinationRateLimited",
        "DestinationUnavailable",
        "UnexpectedDestinationResponse",
    }
    assert len(PublicationErrorCode) == 13


def test_pure_modules_import_no_http_llm_or_renderer() -> None:
    banned = (
        "httpx",
        "anthropic",
        "ai_trend_pipeline.media.activities",
        "ai_trend_pipeline.media.card",
    )
    banned += ("ai_trend_pipeline.media.animation", "ai_trend_pipeline.media.illustration")
    banned += ("ai_trend_pipeline.media.providers", "ai_trend_pipeline.bluesky")
    banned += ("ai_trend_pipeline.publishing.bluesky_story", "ai_trend_pipeline.media.storage")
    for name in ("models.py", "registry.py", "formatter.py"):
        tree = ast.parse((SRC / name).read_text())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert not any(module == b or module.startswith(b + ".") for b in banned), (
                    name,
                    module,
                )
