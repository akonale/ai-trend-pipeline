"""Fixed inputs for the illustrated-card goldens and the contact sheet. No live calls."""

import hashlib
from dataclasses import dataclass

from ai_trend_pipeline.media.models import IllustrateInput


def content_hash(name: str) -> str:
    return hashlib.sha256(f"illustrated-case|{name}".encode()).hexdigest()


@dataclass(frozen=True)
class Case:
    name: str
    headline: str
    summary: str
    topic_label: str  # for the contact sheet: the topic the headline is about
    seed: str | None = None  # defaults to the case name

    def data(self) -> IllustrateInput:
        return IllustrateInput(
            story_id=f"story-{self.name}",
            story_content_hash=content_hash(self.seed or self.name),
            headline=self.headline,
            summary=self.summary,
        )


LONG_HEADLINE = (
    "Researchers studying schools, students and teachers in forty countries say learning "
    "outcomes improved far more slowly than every national plan had promised"
)

TOPIC_CASES: tuple[Case, ...] = (
    Case("ai", "OpenAI ships a new model for every developer", "", "ai"),
    Case("security", "Researchers disclose a database breach", "", "security"),
    Case("chips", "Nvidia unveils a faster GPU", "", "chips"),
    Case("code", "Linux kernel adds Rust drivers", "", "code"),
    Case("internet", "Broadband reaches rural areas", "", "internet"),
    Case("energy", "Solar overtakes coal in Europe", "", "energy"),
    Case("electric-cars", "Tesla expands charging network", "", "electric-cars"),
    Case("climate", "CO2 emissions fall for the first time", "", "climate"),
    Case("economy", "GDP growth slows as inflation bites", "", "economy"),
    Case("space", "NASA picks a Mars landing site", "", "space"),
    Case("science", "Physics lab finds a new state of matter", "", "science"),
    Case("food", "Farming yields hit a record harvest", "", "food"),
    Case("education-a", "Schools bring back reading time for students", "", "education", "edu-a"),
    Case("education-b", "Students and teachers return to class", "", "education", "edu-b"),
    Case("health", "Life expectancy rises across the world", "", "health"),
)

CASES: tuple[Case, ...] = (
    *TOPIC_CASES,
    Case("generic-no-tags", "A quiet Tuesday for everyone", "Nothing much happened.", "none"),
    Case(
        "generic-summary-only", "A quiet Tuesday for everyone", "Solar is mentioned once.", "energy"
    ),
    Case("long-ellipsised", LONG_HEADLINE, "", "education"),
    Case("unicode", "Éclair café: naïve São Paulo ŝtudents — “quoted” …", "", "none"),
    Case("same-topic-seed-1", "Schools adopt a new reading plan", "", "education", "seed-1"),
    Case("same-topic-seed-2", "Schools adopt a new reading plan", "", "education", "seed-2"),
    Case("same-topic-seed-3", "Schools adopt a new reading plan", "", "education", "seed-3"),
)
CASE_BY_NAME = {case.name: case for case in CASES}
GOLDEN_NAMES = tuple(case.name for case in CASES)
