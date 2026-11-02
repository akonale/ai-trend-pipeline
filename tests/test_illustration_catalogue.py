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


def test_every_record_is_complete_and_truthful() -> None:
    for asset in load_catalogue():
        assert asset.source.value == "open-doodles"
        assert asset.licence.value == "CC0-1.0"
        assert asset.author == "Pablo Stanley"
        host = asset.source_url.removeprefix("https://").split("/")[0]
        assert host == "opendoodles.com" or host.endswith(".opendoodles.com")
        assert asset.licence_url == "https://creativecommons.org/publicdomain/zero/1.0/"
        assert asset.licence_verified_on <= date.today()
        assert asset.retrieved_on <= date.today()
        assert asset.modified and asset.modification_note
        assert 3 <= len(asset.description) <= 80
        assert 3 <= len(asset.tags) <= 30
        assert asset.attribution_required is False and asset.attribution_text is None
        assert set(asset.recolour.values()) <= {"ink", "paper", "accent", "support"}


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
    assert 1 <= len(assets) <= MAX_ASSETS == 20
    per_topic = Counter(asset.topic for asset in assets)
    assert max(per_topic.values()) <= MAX_ASSETS_PER_TOPIC == 2


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
