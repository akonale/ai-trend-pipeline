"""Topic matching: pure keyword rules over the headline and the start of the summary.

The whole topic table lives here, but only topics that have an asset in the catalogue take part.
A story about a topic with no drawing therefore scores 0 and gets the generic scene.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ai_trend_pipeline.media.models import MAX_ILLUSTRATE_SUMMARY_CHARS, IllustrationStatus

MATCH_THRESHOLD = 3
HEADLINE_WEIGHT = 3
SUMMARY_WEIGHT = 1

# Order is priority: it breaks ties after the number of headline hits.
TOPIC_TAGS: dict[str, tuple[str, ...]] = {
    "ai": (
        "ai",
        "artificial intelligence",
        "llm",
        "llms",
        "chatbot",
        "robot",
        "robots",
        "machine learning",
        "neural",
        "gpt",
        "openai",
        "anthropic",
        "claude",
        "gemini",
        "agent",
        "agents",
    ),
    "security": (
        "security",
        "privacy",
        "hack",
        "hacked",
        "hackers",
        "breach",
        "vulnerability",
        "exploit",
        "malware",
        "ransomware",
        "encryption",
        "password",
        "passwords",
    ),
    "chips": (
        "chip",
        "chips",
        "semiconductor",
        "gpu",
        "gpus",
        "cpu",
        "processor",
        "silicon",
        "nvidia",
        "hardware",
    ),
    "code": (
        "software",
        "code",
        "coding",
        "programming",
        "developer",
        "developers",
        "open source",
        "compiler",
        "linux",
        "python",
        "rust",
        "javascript",
        "database",
        "app",
    ),
    "internet": (
        "internet",
        "online",
        "broadband",
        "web",
        "network",
        "browser",
        "connectivity",
        "wifi",
    ),
    "energy": (
        "solar",
        "wind",
        "renewable",
        "renewables",
        "energy",
        "electricity",
        "power grid",
        "battery",
        "batteries",
    ),
    "electric-cars": (
        "electric car",
        "electric cars",
        "electric vehicle",
        "ev",
        "evs",
        "tesla",
        "charging",
    ),
    "climate": (
        "climate",
        "co2",
        "carbon",
        "emissions",
        "environment",
        "nature",
        "forest",
        "warming",
        "pollution",
    ),
    "economy": (
        "gdp",
        "economy",
        "economic",
        "income",
        "money",
        "prices",
        "inflation",
        "market",
        "markets",
        "funding",
        "startup",
        "revenue",
        "tax",
    ),
    "health": (
        "health",
        "life expectancy",
        "longevity",
        "medicine",
        "medical",
        "disease",
        "hospital",
        "vaccine",
        "fitness",
    ),
    "space": (
        "space",
        "rocket",
        "nasa",
        "satellite",
        "satellites",
        "orbit",
        "launch",
        "mars",
        "moon",
    ),
    "science": (
        "science",
        "research",
        "researchers",
        "scientists",
        "study",
        "lab",
        "physics",
        "biology",
        "chemistry",
    ),
    "education": (
        "education",
        "school",
        "schools",
        "students",
        "learning",
        "book",
        "books",
        "teachers",
    ),
    "food": ("food", "farming", "agriculture", "crops", "harvest", "diet"),
}
TOPIC_ORDER: tuple[str, ...] = tuple(TOPIC_TAGS)

_TOKEN = re.compile(r"[a-z0-9]+")


class TopicAsset(Protocol):
    @property
    def topic(self) -> str: ...


@dataclass(frozen=True)
class MatchResult[A: TopicAsset]:
    status: IllustrationStatus
    topic: str | None
    score: int
    asset: A | None


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(unicodedata.normalize("NFKC", text).casefold())


def _distinct_hits(tokens: list[str], tags: Sequence[str]) -> int:
    hits = 0
    for tag in tags:
        words = tokenize(tag)
        size = len(words)
        if any(tokens[i : i + size] == words for i in range(len(tokens) - size + 1)):
            hits += 1
    return hits


def match_story[A: TopicAsset](
    headline: str, summary: str, assets: Sequence[A], pick: int
) -> MatchResult[A]:
    """Pick the best covered topic. `pick` (a seed byte) chooses among a topic's assets."""
    covered = {asset.topic for asset in assets}
    head_tokens = tokenize(headline)
    summary_tokens = tokenize(summary[:MAX_ILLUSTRATE_SUMMARY_CHARS])
    best: tuple[int, int, int, str] | None = None  # (score, headline hits, -order, topic)
    for order, topic in enumerate(TOPIC_ORDER):
        if topic not in covered:
            continue
        head_hits = _distinct_hits(head_tokens, TOPIC_TAGS[topic])
        summary_hits = _distinct_hits(summary_tokens, TOPIC_TAGS[topic])
        score = HEADLINE_WEIGHT * head_hits + SUMMARY_WEIGHT * summary_hits
        candidate = (score, head_hits, -order, topic)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    if best is None or best[0] < MATCH_THRESHOLD:
        return MatchResult(IllustrationStatus.GENERIC, None, best[0] if best else 0, None)
    topic = best[3]
    options = [asset for asset in assets if asset.topic == topic]
    return MatchResult(IllustrationStatus.MATCHED, topic, best[0], options[pick % len(options)])
