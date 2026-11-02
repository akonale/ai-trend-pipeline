"""Every vendored SVG has exactly one complete, truthful catalogue record."""

import hashlib
import json
from collections import Counter
from datetime import date

import pytest
from pydantic import ValidationError

from ai_trend_pipeline.media.illustrations.catalogue import (
    ASSET_DIR,
    CATALOGUE_PATH,
    MAX_ASSETS,
    MAX_ASSETS_PER_TOPIC,
    IllustrationAsset,
    load_catalogue,
    read_asset_svg,
)
from ai_trend_pipeline.media.illustrations.matching import TOPIC_TAGS
from ai_trend_pipeline.media.illustrations.rasterise import rasterise_svg
from ai_trend_pipeline.media.illustrations.scene import svg_aspect

LICENSES = CATALOGUE_PATH.parent / "LICENSES.md"


def raw_entries() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    return entries


def valid_entry(**overrides: object) -> dict[str, object]:
    entry = dict(raw_entries()[0])
    entry.update(overrides)
    return entry


def test_each_file_has_exactly_one_entry_and_each_entry_has_a_file() -> None:
    assets = load_catalogue()
    files = sorted(path.name for path in ASSET_DIR.glob("*.svg"))
    assert sorted(asset.file for asset in assets) == files
    assert len({asset.id for asset in assets}) == len(assets)
    assert all(asset.file == f"{asset.id}.svg" for asset in assets)


def test_every_record_is_either_a_full_doodle_or_a_project_original() -> None:
    kinds: set[str] = set()
    for asset in load_catalogue():
        kinds.add(asset.source.value)
        assert asset.licence_url == "https://creativecommons.org/publicdomain/zero/1.0/"
        assert 3 <= len(asset.description) <= 80
        assert 3 <= len(asset.tags) <= 30
        assert asset.attribution_required is False and asset.attribution_text is None
        assert set(asset.recolour.values()) <= {"ink", "paper", "accent", "support"}
        if asset.source.value == "open-doodles":
            assert asset.licence.value == "CC0-1.0"
            assert asset.author == "Pablo Stanley"
            assert asset.source_url is not None
            host = asset.source_url.removeprefix("https://").split("/")[0]
            assert host == "opendoodles.com" or host.endswith(".opendoodles.com")
            assert asset.licence_verified_on is not None and asset.retrieved_on is not None
            assert asset.licence_verified_on <= date.today()
            assert asset.retrieved_on <= date.today()
            assert asset.modified and asset.modification_note
            assert "viewBox cropped" in asset.modification_note
        else:
            assert asset.licence.value == "original-project"
            assert asset.author == "this project"
            assert asset.source_url is None
            assert asset.licence_verified_on is None and asset.retrieved_on is None
            assert not asset.modified and asset.modification_note is None
            assert asset.id.startswith("glyph-")
    assert kinds == {"open-doodles", "project"}


def test_the_three_verified_doodles_are_kept() -> None:
    doodles = {a.id for a in load_catalogue() if a.source.value == "open-doodles"}
    assert doodles == {"person-reading", "reading-with-books", "person-meditating"}


def test_sha256_matches_the_committed_file() -> None:
    for asset in load_catalogue():
        data = (ASSET_DIR / asset.file).read_bytes()
        assert hashlib.sha256(data).hexdigest() == asset.sha256
        assert read_asset_svg(asset) == data.decode("utf-8")


def test_read_asset_svg_rejects_a_file_that_changed() -> None:
    asset = load_catalogue()[0]
    wrong = asset.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ValueError, match="sha256"):
        read_asset_svg(wrong)


def test_licences_md_lists_every_asset_and_the_required_statements() -> None:
    text = LICENSES.read_text(encoding="utf-8")
    for asset in load_catalogue():
        assert asset.id in text
        assert asset.sha256 in text
        if asset.attribution_required:
            assert asset.attribution_text and asset.attribution_text in text
    assert "https://creativecommons.org/publicdomain/zero/1.0/" in text
    assert (
        "These glyphs are original work created for this project. The project owner dedicates "
        "them to the public domain under CC0 1.0 "
        "(https://creativecommons.org/publicdomain/zero/1.0/). No attribution is required."
    ) in text
    assert "confirmed" not in text.lower()
    assert "Free for Commercial and Personal Use. No need to credit" in text
    assert "any purpose without restriction under copyright or database law" in text
    assert "Openclipart" in text and "deferred" in text
    assert "2026-11-02" in text


def test_topics_and_tags_equal_the_matching_table() -> None:
    for asset in load_catalogue():
        assert asset.topic in TOPIC_TAGS
        assert asset.tags == list(TOPIC_TAGS[asset.topic])


def test_size_limits() -> None:
    assets = load_catalogue()
    assert MAX_ASSETS == 30
    assert 1 <= len(assets) <= MAX_ASSETS
    per_topic = Counter(asset.topic for asset in assets)
    assert max(per_topic.values()) <= MAX_ASSETS_PER_TOPIC == 2


def glyph_entry(**overrides: object) -> dict[str, object]:
    entry = next(e for e in raw_entries() if e["source"] == "project")
    return {**entry, **overrides}


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_url": "https://www.opendoodles.com/"},
        {"licence_verified_on": "2026-11-02"},
        {"retrieved_on": "2026-11-02"},
        {"licence": "CC0-1.0"},
        {"author": "Pablo Stanley"},
        {"modified": True, "modification_note": "x"},
        {"attribution_required": True, "attribution_text": "x"},
        {"licence": "openclipart-public-domain"},
        {"source": "openclipart"},
    ],
)
def test_project_records_reject_mixed_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IllustrationAsset.model_validate(glyph_entry(**overrides))
    assert IllustrationAsset.model_validate(glyph_entry()).source.value == "project"


@pytest.mark.parametrize(
    "overrides",
    [
        {"licence": "original-project"},
        {"source_url": None},
        {"licence_verified_on": None},
        {"retrieved_on": None},
        {"author": "this project"},
    ],
)
def test_doodle_records_reject_project_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IllustrationAsset.model_validate(valid_entry(**overrides))


def test_tight_viewbox_and_aspect_for_every_asset() -> None:
    for asset in load_catalogue():
        svg = read_asset_svg(asset)
        aspect = svg_aspect(svg)
        assert 0.6 <= aspect <= 1.6, asset.id
        size = 512
        width, height = (
            (size, round(size / aspect)) if aspect >= 1 else (round(size * aspect), size)
        )
        bbox = rasterise_svg(svg, width, height).getchannel("A").getbbox()
        assert bbox is not None
        span = max((bbox[2] - bbox[0]) / width, (bbox[3] - bbox[1]) / height)
        assert span >= 0.95, (asset.id, span)


@pytest.mark.parametrize(
    "overrides",
    [
        {"licence": "openclipart-public-domain"},
        {"licence": "CC-BY-4.0"},
        {"source": "openclipart"},
        {"source": "unsplash"},
        {"source_url": "http://www.opendoodles.com/"},
        {"source_url": "https://example.com/doodle"},
        {"licence_url": "http://creativecommons.org/publicdomain/zero/1.0/"},
        {"id": "Bad Id", "file": "Bad Id.svg"},
        {"file": "other.svg"},
        {"description": "x"},
        {"tags": ["ai"]},
        {"topic": "no-such-topic"},
        {"tags": ["a", "b", "c"]},
        {"modified": True, "modification_note": None},
        {"attribution_required": True, "attribution_text": None},
        {"sha256": "abc"},
        {"recolour": {"000000": "ink"}},
        {"recolour": {"#000000": "paint"}},
        {"extra": 1},
    ],
)
def test_model_rejects_bad_records(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        IllustrationAsset.model_validate(valid_entry(**overrides))


def test_model_is_frozen_and_valid_entry_round_trips() -> None:
    asset = IllustrationAsset.model_validate(valid_entry())
    with pytest.raises(ValidationError):
        asset.title = "x"
    assert IllustrationAsset.model_validate_json(asset.model_dump_json()) == asset
