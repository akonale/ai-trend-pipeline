"""General-audience classification of source events with TypeSafe Jev.

Jev supplies typed judgments (Noul probabilities and Score positions); the pure functions in this
module own every accept/reject decision. All question wording, criteria, thresholds and policy
tables live here so they can be audited and edited in one place. Jev never writes prose.
"""

import json
import math
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from temporalio import activity
from temporalio.exceptions import ApplicationError
from typesafe_sdk import (
    AsyncTypeSafeClient,
    ContentValue,
    Noul,
    NoulAnswer,
    NoulCriteria,
    RetryPolicy,
    Score,
    ScoreAnswer,
    SystemOneResponse,
    TypeSafeAPIResponseValidationError,
)

from ai_trend_pipeline.config import Settings
from ai_trend_pipeline.sources.models import MAX_EVENTS, SourceEvent

MAX_CLASSIFIED_EVENTS = MAX_EVENTS
MAX_MODEL_LENGTH = 100
INVALID_CLASSIFICATION_ERROR_TYPE = "StoryClassificationInvalid"

# Jev 1.14 documents 64k tokens per request and 32k per question. Stay well inside both without
# the long-context header: at most this many events and this estimated size per request.
MAX_EVENTS_PER_REQUEST = 10
MAX_ESTIMATED_REQUEST_TOKENS = 24_000
ESTIMATED_CHARS_PER_TOKEN = 3  # deliberately pessimistic for URLs and non-English titles
SDK_MAX_RETRIES = 1  # Temporal owns the outer retries
SDK_TIMEOUT_S = 30.0

NOTE_FOR_MODEL = (
    "Each event is a news headline with its link and publication date. The article itself was "
    "not read, so judge only what the headline and link say."
)

# Noul bands: at or above YES_AT reads as yes, at or below NO_AT as no, in between is unsure.
YES_AT = 0.7
NO_AT = 0.3
TOP_LEVEL = 3  # Score questions below have levels 0..3
LEVEL_PROBABILITY_TOLERANCE = 0.05


class NewsPolicyMode(StrEnum):
    """Named general-audience policies; `strict` is the default."""

    STRICT = "strict"
    BALANCED = "balanced"
    RELAXED = "relaxed"


DEFAULT_NEWS_POLICY_MODE = NewsPolicyMode.STRICT


class Judgment(StrEnum):
    """One atomic question asked about every event."""

    AI_CENTRAL = "ai_central"
    HUMAN_INTEREST = "human_interest"
    CLARITY = "clarity"
    ROUTINE_INDUSTRY = "routine_industry"
    EVERYDAY_IMPACT = "everyday_impact"


def _title_path(key: str) -> str:
    return f"`events.{key}.title`"


def question_for(judgment: Judgment, key: str) -> Noul | Score:
    """The Jev question for one judgment about the event stored under `events.<key>`."""
    title = _title_path(key)
    if judgment is Judgment.AI_CENTRAL:
        return Noul(
            instructions=(
                "Is artificial intelligence, or what it does to people, the main subject of "
                f"{title}?"
            ),
            criteria=NoulCriteria(
                true=(
                    "The headline is mainly about an AI system, what an AI tool produced or did, "
                    "or how AI is changing people's work, art, relationships, or society"
                ),
                false=(
                    "AI is mentioned only in passing or as one feature of something else, "
                    "or the headline is about a different subject"
                ),
            ),
        )
    if judgment is Judgment.ROUTINE_INDUSTRY:
        return Noul(
            instructions=f"Is {title} routine enterprise, technical, or industry news?",
            criteria=NoulCriteria(
                true=(
                    "Business or developer news written for industry insiders, such as company "
                    "acquisitions and funding rounds, data or cloud infrastructure, databases, "
                    "developer tools, benchmarks, or product and version releases"
                ),
                false=(
                    "The headline is about people, creativity, culture, safety, or an event that "
                    "ordinary readers would talk about, even if a company is involved"
                ),
            ),
        )
    if judgment is Judgment.HUMAN_INTEREST:
        return Score(
            instructions=f"How much human interest does {title} have for an ordinary reader?",
            criteria=[
                (
                    "No human angle: a product, system, or company action with no person or "
                    "community affected"
                ),
                (
                    "A faint human angle: people are mentioned but the story is mainly about "
                    "technology or business"
                ),
                (
                    "A clear human angle: a person's experience, a creative or ethical dispute, "
                    "or an effect on a community"
                ),
                (
                    "A strong human story that most people would want to read or discuss, with "
                    "personal stakes, creativity, fairness, or surprise"
                ),
            ],
        )
    if judgment is Judgment.CLARITY:
        return Score(
            instructions=(
                "How easily can a reader with no technical background understand what "
                f"{title} is about?"
            ),
            criteria=[
                "Full of jargon, product names, or abbreviations that a layperson cannot decode",
                "Mostly technical wording; a layperson would get only a vague idea",
                "A layperson understands the gist, though a term or two is unfamiliar",
                "Plain language; anyone can tell what happened",
            ],
        )
    return Score(
        instructions=(
            f"How explicitly does {title} state an effect on ordinary people's daily life, "
            "jobs, money, health, safety, or learning?"
        ),
        criteria=[
            "No effect on ordinary people is stated",
            "An effect on ordinary people is only implied or merely possible",
            "A concrete effect on some ordinary people is stated",
            "A concrete effect on many ordinary people's daily lives is stated",
        ],
    )


class Verdict(StrEnum):
    """A Noul probability read through the fixed bands."""

    YES = "yes"
    UNSURE = "unsure"
    NO = "no"


def verdict_for(probability: float) -> Verdict:
    if probability >= YES_AT:
        return Verdict.YES
    if probability <= NO_AT:
        return Verdict.NO
    return Verdict.UNSURE


def level_for(score: float) -> int:
    """The nearest Score level, 0..TOP_LEVEL."""
    return min(TOP_LEVEL, max(0, math.floor(score + 0.5)))


Level = Annotated[int, Field(ge=0, le=TOP_LEVEL)]


class JudgmentLevels(BaseModel):
    """Every judgment reduced to its discrete value; the decision function's whole input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ai_central: Verdict
    routine_industry: Verdict
    human_interest: Level
    clarity: Level
    everyday_impact: Level


class StoryJudgments(BaseModel):
    """The validated Jev answers for one event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ai_central: float = Field(ge=0, le=1)
    routine_industry: float = Field(ge=0, le=1)
    human_interest: float = Field(ge=0, le=TOP_LEVEL)
    clarity: float = Field(ge=0, le=TOP_LEVEL)
    everyday_impact: float = Field(ge=0, le=TOP_LEVEL)

    def levels(self) -> JudgmentLevels:
        return JudgmentLevels(
            ai_central=verdict_for(self.ai_central),
            routine_industry=verdict_for(self.routine_industry),
            human_interest=level_for(self.human_interest),
            clarity=level_for(self.clarity),
            everyday_impact=level_for(self.everyday_impact),
        )


class NewsPolicy(BaseModel):
    """Thresholds for one mode. AI centrality is a hard gate and has no knob."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_human_interest: Level
    min_clarity: Level
    # Routine news is excluded when judged yes, and also when unsure if this is set.
    routine_blocks_unsure: bool
    # The everyday-impact level that lets a routine story through; it never beats the other gates.
    routine_override_impact: Level


POLICIES: Mapping[NewsPolicyMode, NewsPolicy] = {
    NewsPolicyMode.STRICT: NewsPolicy(
        min_human_interest=3,
        min_clarity=2,
        routine_blocks_unsure=True,
        routine_override_impact=3,
    ),
    NewsPolicyMode.BALANCED: NewsPolicy(
        min_human_interest=2,
        min_clarity=2,
        routine_blocks_unsure=False,
        routine_override_impact=3,
    ),
    NewsPolicyMode.RELAXED: NewsPolicy(
        min_human_interest=1,
        min_clarity=1,
        routine_blocks_unsure=False,
        routine_override_impact=2,
    ),
}


class DecisionReason(StrEnum):
    """Why an event is or is not eligible; the first failing gate wins."""

    ELIGIBLE = "eligible"
    ELIGIBLE_IMPACT_OVERRIDE = "eligible_impact_override"
    AI_NOT_CENTRAL = "ai_not_central"
    LOW_HUMAN_INTEREST = "low_human_interest"
    HARD_TO_UNDERSTAND = "hard_to_understand"
    ROUTINE_INDUSTRY = "routine_industry"


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    reason: DecisionReason


def decide(levels: JudgmentLevels, mode: NewsPolicyMode) -> Decision:
    """Total, pure decision over discrete judgments. Confidence and engagement never enter."""
    policy = POLICIES[mode]
    if levels.ai_central is not Verdict.YES:
        return Decision(eligible=False, reason=DecisionReason.AI_NOT_CENTRAL)
    if levels.human_interest < policy.min_human_interest:
        return Decision(eligible=False, reason=DecisionReason.LOW_HUMAN_INTEREST)
    if levels.clarity < policy.min_clarity:
        return Decision(eligible=False, reason=DecisionReason.HARD_TO_UNDERSTAND)
    routine = levels.routine_industry is Verdict.YES or (
        levels.routine_industry is Verdict.UNSURE and policy.routine_blocks_unsure
    )
    if routine:
        if levels.everyday_impact >= policy.routine_override_impact:
            return Decision(eligible=True, reason=DecisionReason.ELIGIBLE_IMPACT_OVERRIDE)
        return Decision(eligible=False, reason=DecisionReason.ROUTINE_INDUSTRY)
    return Decision(eligible=True, reason=DecisionReason.ELIGIBLE)


class EventClassification(BaseModel):
    """The bounded per-event result: judgments plus the code-owned decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    judgments: StoryJudgments
    eligible: bool
    reason: DecisionReason


def _empty_results() -> list[EventClassification]:
    return []


class ClassifyStoryEventsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[SourceEvent] = Field(max_length=MAX_CLASSIFIED_EVENTS)
    mode: NewsPolicyMode = DEFAULT_NEWS_POLICY_MODE

    @model_validator(mode="after")
    def _unique_ids(self) -> "ClassifyStoryEventsInput":
        seen: set[str] = set()
        for event in self.events:
            if event.external_id in seen:
                raise ValueError(f"duplicate external_id {event.external_id!r}")
            seen.add(event.external_id)
        return self


class ClassifyStoryEventsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MODEL_LENGTH)]
    mode: NewsPolicyMode
    results: list[EventClassification] = Field(
        default_factory=_empty_results, max_length=MAX_CLASSIFIED_EVENTS
    )

    def eligible_ids(self) -> frozenset[str]:
        return frozenset(result.external_id for result in self.results if result.eligible)


def _invalid(message: str) -> ApplicationError:
    return ApplicationError(
        f"Invalid story classification: {message}",
        type=INVALID_CLASSIFICATION_ERROR_TYPE,
        non_retryable=True,
    )


def _key(index: int) -> str:
    return f"e{index}"


def _question_id(key: str, judgment: Judgment) -> str:
    return f"{key}__{judgment.value}"


def _state_for(events: Sequence[SourceEvent]) -> dict[str, ContentValue | None]:
    """Only the headline, link and date. Scores, comments and other metrics are never sent."""
    items: dict[str, ContentValue | None] = {}
    for index, event in enumerate(events):
        items[_key(index)] = {
            "title": event.title,
            "url": str(event.url) if event.url is not None else None,
            "published_at": (
                event.published_at.date().isoformat() if event.published_at is not None else None
            ),
        }
    return {"note": NOTE_FOR_MODEL, "events": items}


def _questions_for(count: int) -> dict[str, Noul | Score]:
    return {
        _question_id(_key(index), judgment): question_for(judgment, _key(index))
        for index in range(count)
        for judgment in Judgment
    }


def _estimated_tokens(state: Mapping[str, ContentValue | None], count: int) -> int:
    state_chars = len(json.dumps(state, ensure_ascii=False, default=str))
    question_chars = sum(len(q.model_dump_json()) for q in _questions_for(count).values())
    return math.ceil((state_chars + question_chars) / ESTIMATED_CHARS_PER_TOKEN)


def chunk_events(
    events: Sequence[SourceEvent],
    *,
    max_events: int = MAX_EVENTS_PER_REQUEST,
    max_tokens: int = MAX_ESTIMATED_REQUEST_TOKENS,
) -> list[list[SourceEvent]]:
    """Greedy, order-preserving chunks that fit the documented per-request context."""
    if max_events < 1:
        raise ValueError("max_events must be positive")
    chunks: list[list[SourceEvent]] = []
    current: list[SourceEvent] = []
    for event in events:
        candidate = [*current, event]
        too_big = len(candidate) > max_events or (
            current and _estimated_tokens(_state_for(candidate), len(candidate)) > max_tokens
        )
        if too_big:
            chunks.append(current)
            current = [event]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _noul(response: SystemOneResponse, question_id: str) -> float:
    answer = response.answers.get(question_id)
    if not isinstance(answer, NoulAnswer):
        raise _invalid("a yes/no answer is missing or has the wrong type")
    return _bounded(answer.noul, 0.0, 1.0)


def _score(response: SystemOneResponse, question_id: str) -> float:
    answer = response.answers.get(question_id)
    if not isinstance(answer, ScoreAnswer):
        raise _invalid("a score answer is missing or has the wrong type")
    if set(answer.probabilities) != set(range(TOP_LEVEL + 1)):
        raise _invalid("a score answer has unexpected levels")
    for probability in answer.probabilities.values():
        _bounded(probability, 0.0, 1.0)
    if abs(sum(answer.probabilities.values()) - 1.0) > LEVEL_PROBABILITY_TOLERANCE:
        raise _invalid("score level probabilities do not sum to one")
    return _bounded(answer.score, 0.0, float(TOP_LEVEL))


def _bounded(value: float, low: float, high: float) -> float:
    if not math.isfinite(value) or value < low or value > high:
        raise _invalid("an answer is outside its allowed range")
    return value


def _judgments_from(response: SystemOneResponse, key: str) -> StoryJudgments:
    return StoryJudgments(
        ai_central=_noul(response, _question_id(key, Judgment.AI_CENTRAL)),
        routine_industry=_noul(response, _question_id(key, Judgment.ROUTINE_INDUSTRY)),
        human_interest=_score(response, _question_id(key, Judgment.HUMAN_INTEREST)),
        clarity=_score(response, _question_id(key, Judgment.CLARITY)),
        everyday_impact=_score(response, _question_id(key, Judgment.EVERYDAY_IMPACT)),
    )


def judgments_from_response(
    response: SystemOneResponse, events: Sequence[SourceEvent]
) -> list[StoryJudgments]:
    """Validate completeness and return one judgment set per event, in event order."""
    expected = set(_questions_for(len(events)))
    if set(response.answers) != expected:
        raise _invalid("response question ids do not match the request")
    return [_judgments_from(response, _key(index)) for index in range(len(events))]


def make_typesafe_client(settings: Settings) -> AsyncTypeSafeClient:
    if settings.typesafe_api_key is None:
        raise RuntimeError("TYPESAFE_API_KEY environment variable is not set")
    return AsyncTypeSafeClient(
        api_key=settings.typesafe_api_key.get_secret_value(),
        model=settings.typesafe_model,
        retry=RetryPolicy(max_retries=SDK_MAX_RETRIES),
        timeout=SDK_TIMEOUT_S,
    )


class StoryClassificationActivities:
    def __init__(
        self,
        client: AsyncTypeSafeClient,
        model: str,
        *,
        max_events_per_request: int = MAX_EVENTS_PER_REQUEST,
    ) -> None:
        self._client = client
        self._model = model
        self._max_events = max_events_per_request

    async def _ask(self, chunk: Sequence[SourceEvent]) -> SystemOneResponse:
        try:
            return await self._client.system_one(
                state=_state_for(chunk),
                questions=_questions_for(len(chunk)),
                model=self._model,
            )
        except TypeSafeAPIResponseValidationError as exc:
            raise _invalid("the API response could not be parsed") from exc

    @activity.defn
    async def classify_story_events(
        self, data: ClassifyStoryEventsInput
    ) -> ClassifyStoryEventsOutput:
        results: list[EventClassification] = []
        model: str | None = None
        for chunk in chunk_events(data.events, max_events=self._max_events):
            response = await self._ask(chunk)
            judgments = judgments_from_response(response, chunk)
            model = model or response.model
            for event, judged in zip(chunk, judgments, strict=True):
                decision = decide(judged.levels(), data.mode)
                results.append(
                    EventClassification(
                        external_id=event.external_id,
                        judgments=judged,
                        eligible=decision.eligible,
                        reason=decision.reason,
                    )
                )
        return ClassifyStoryEventsOutput(
            model=model or self._model, mode=data.mode, results=results
        )
