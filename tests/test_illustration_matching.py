"""Topic matching: pure, table-driven, runs against a small in-test catalogue."""

from dataclasses import dataclass

import pytest

from ai_trend_pipeline.media.illustrations.catalogue import load_catalogue
from ai_trend_pipeline.media.illustrations.matching import (
    MATCH_THRESHOLD,
    TOPIC_ORDER,
    TOPIC_TAGS,
    match_story,
)
from ai_trend_pipeline.media.models import IllustrationStatus


@dataclass(frozen=True)
class FakeAsset:
    id: str
    topic: str


ASSETS = [FakeAsset(f"{topic}-1", topic) for topic in TOPIC_ORDER]
TWO_CODE = [*ASSETS, FakeAsset("code-2", "code")]


def topic_of(headline: str, summary: str = "", assets: list[FakeAsset] = ASSETS) -> str | None:
    result = match_story(headline, summary, assets, pick=0)
    return result.topic if result.status is IllustrationStatus.MATCHED else None


@pytest.mark.parametrize(
    ("headline", "topic"),
    [
        ("OpenAI ships a new model", "ai"),
        ("Researchers disclose a database breach", "security"),
        ("Nvidia unveils a faster GPU", "chips"),
        ("Linux kernel adds Rust drivers", "code"),
        ("Broadband reaches rural areas", "internet"),
        ("Solar overtakes coal in Europe", "energy"),
        ("Tesla expands charging network", "electric-cars"),
        ("CO2 emissions fall for the first time", "climate"),
        ("GDP growth slows as inflation bites", "economy"),
        ("Life expectancy rises again", "health"),
        ("NASA picks a Mars landing site", "space"),
        ("Physics lab finds a new state of matter", "science"),
        ("Schools adopt a new reading plan", "education"),
        ("Farming yields hit a record harvest", "food"),
    ],
)
def test_one_headline_per_topic_picks_that_topic(headline: str, topic: str) -> None:
    assert topic_of(headline) == topic


def test_rust_in_a_headline_picks_the_code_topic() -> None:
    assert topic_of("Why we rewrote it in Rust") == "code"


def test_topic_without_an_asset_is_generic_never_another_topics_drawing() -> None:
    covered = [a for a in ASSETS if a.topic != "security"]
    result = match_story("Ransomware and malware spread", "", covered, pick=0)
    assert result.status is IllustrationStatus.GENERIC
    assert result.topic is None and result.asset is None


def test_multi_word_tags_match_only_consecutive_tokens() -> None:
    assert topic_of("An electric car launch") == "electric-cars"
    assert topic_of("Electric guitar car show") is None
    assert topic_of("Artificial intelligence arrives") == "ai"
    assert topic_of("An intelligence artificial twist") is None
    assert topic_of("Life expectancy gap widens") == "health"
    assert topic_of("Life and expectancy") is None


def test_single_word_tags_match_whole_tokens_only() -> None:
    assert topic_of("Said aid arrives") is None  # "ai" is not inside "said"
    assert topic_of("Waiting for the app store") == "code"
    assert topic_of("Happy applications") is None


def test_summary_only_single_hit_is_generic() -> None:
    result = match_story("A quiet Tuesday", "The company said solar will grow.", ASSETS, pick=0)
    assert result.status is IllustrationStatus.GENERIC and result.score == 1


def test_three_summary_hits_make_matched() -> None:
    summary = "Solar, wind and battery storage are growing."
    result = match_story("A quiet Tuesday", summary, ASSETS, pick=0)
    assert result.status is IllustrationStatus.MATCHED
    assert (result.topic, result.score) == ("energy", MATCH_THRESHOLD)


def test_summary_is_cut_at_600_characters() -> None:
    summary = "x " * 300 + "solar wind battery"
    assert match_story("A quiet Tuesday", summary, ASSETS, pick=0).status is (
        IllustrationStatus.GENERIC
    )


def test_a_tag_counts_once_per_field() -> None:
    result = match_story("Solar solar solar", "", ASSETS, pick=0)
    assert result.score == 3


def test_ties_use_headline_hits_then_topic_order() -> None:
    # One headline hit each: topic order decides ("ai" is before "chips").
    assert topic_of("AI chip race") == "ai"
    # security: 3 summary hits (score 3, 0 headline hits) vs code: 1 headline hit (score 3).
    summary = "A breach, a password leak and malware."
    result = match_story("Open source news", summary, ASSETS, pick=0)
    assert result.topic == "code"
    # same score, a headline hit beats summary hits even when the topic comes later
    assert match_story("Open source news", summary, ASSETS[::-1], pick=0).topic == "code"


def test_result_does_not_depend_on_case_or_unicode_form() -> None:
    assert topic_of("OPENAI SHIPS A MODEL") == topic_of("openai ships a model") == "ai"
    assert topic_of("ＯｐｅｎＡＩ ships a model") == "ai"  # full-width letters, NFKC
    assert topic_of("Café and café news about GPU") == "chips"


def test_headline_without_tags_is_generic() -> None:
    result = match_story("A very ordinary day", "Nothing to see.", ASSETS, pick=0)
    assert result.status is IllustrationStatus.GENERIC
    assert result.asset is None and result.score == 0


def test_two_assets_in_a_topic_are_picked_by_the_seed_byte() -> None:
    picks = {match_story("New Python release", "", TWO_CODE, pick=n).asset for n in range(4)}
    assert picks == {FakeAsset("code-1", "code"), FakeAsset("code-2", "code")}
    again = match_story("New Python release", "", TWO_CODE, pick=3)
    assert again.asset == match_story("New Python release", "", TWO_CODE, pick=3).asset


def test_tag_table_is_lowercase_and_unique_per_topic() -> None:
    assert len(TOPIC_ORDER) == 14
    for topic, tags in TOPIC_TAGS.items():
        assert tags == tuple(sorted(set(tags), key=tags.index))
        assert all(tag == tag.lower() for tag in tags), topic


# Against the real catalogue: one fixed headline per covered topic matches that topic.
REAL_HEADLINES = {
    "education": "Schools and students return to class",
    "health": "Life expectancy rises across the world",
}


def test_real_catalogue_matches_each_covered_topic() -> None:
    assets = load_catalogue()
    covered = {a.topic for a in assets}
    assert covered == set(REAL_HEADLINES)
    for topic, headline in REAL_HEADLINES.items():
        result = match_story(headline, "", assets, pick=0)
        assert result.status is IllustrationStatus.MATCHED
        assert result.topic == topic
        assert result.asset is not None and result.asset.topic == topic


def test_real_catalogue_uncovered_topics_are_generic() -> None:
    for headline in ("OpenAI ships a new model", "CO2 emissions fall", "Solar overtakes coal"):
        result = match_story(headline, "", load_catalogue(), pick=0)
        assert result.status is IllustrationStatus.GENERIC and result.asset is None
