"""Jev story classification: questions, requests, validation, and the policy truth table.

The recorded fixtures follow the TypeSafe response shape. No test calls the live API and none
asserts a probability the live model would have to reproduce.
"""

import itertools
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx2
import pytest
from pydantic import SecretStr, ValidationError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Noul,
    RetryPolicy,
    Score,
    TypeSafeAPIConnectionError,
    TypeSafeAPIError,
)

from ai_trend_pipeline.config import DEFAULT_TYPESAFE_MODEL, Settings, load_settings
from ai_trend_pipeline.sources import SourceEvent, SourceName
from ai_trend_pipeline.sources.models import MAX_EVENTS
from ai_trend_pipeline.story_classification import (
    ESTIMATED_CHARS_PER_TOKEN,
    INVALID_CLASSIFICATION_ERROR_TYPE,
    MAX_ESTIMATED_REQUEST_TOKENS,
    MAX_EVENTS_PER_REQUEST,
    POLICIES,
    ClassifyStoryEventsInput,
    ClassifyStoryEventsOutput,
    Decision,
    DecisionReason,
    EventClassification,
    Judgment,
    JudgmentLevels,
    NewsPolicyMode,
    StoryClassificationActivities,
    StoryJudgments,
    Verdict,
    _estimated_tokens,  # pyright: ignore[reportPrivateUsage]  # chunk-size guard under test
    _state_for,  # pyright: ignore[reportPrivateUsage]  # the exact state sent to Jev
    chunk_events,
    decide,
    level_for,
    make_typesafe_client,
    question_for,
    verdict_for,
)

FIXTURES = Path(__file__).parent / "fixtures"
OBSERVED = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
AUTHOR_TITLE = "370 Books in 20 months: South African author defends her use of AI"
CLICKHOUSE_TITLE = "ClickHouse acquires a data platform company to expand its cloud offering"

Handler = Callable[[httpx2.Request], httpx2.Response]


def fixture(name: str) -> dict[str, object]:
    path = FIXTURES / f"typesafe_story_classification_{name}.json"
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def event(
    external_id: str,
    title: str = "A headline",
    metrics: dict[str, int] | None = None,
) -> SourceEvent:
    return SourceEvent(
        source=SourceName.HN,
        external_id=external_id,
        title=title,
        url=f"https://example.com/{external_id}",  # pyright: ignore[reportArgumentType]  # str coerced to HttpUrl
        published_at=OBSERVED,
        observed_at=OBSERVED,
        metrics=metrics or {},
    )


class Recorder:
    """A MockTransport handler that replays recorded bodies and remembers each request."""

    def __init__(self, *responses: dict[str, object] | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        assert request.method == "POST" and request.url.path == "/v1/systemone"
        self.requests.append(cast(dict[str, object], json.loads(request.content)))
        index = len(self.requests) - 1
        item = self._responses[min(index, len(self._responses) - 1)]
        if isinstance(item, Exception):
            raise item
        if "status" in item:
            return httpx2.Response(cast(int, item["status"]), json=item["body"])
        return httpx2.Response(200, json=item)


def make_client(handler: Handler) -> AsyncTypeSafeClient:
    return AsyncTypeSafeClient(
        api_key="test-key",
        model="jev-1.14.0",
        retry=RetryPolicy(max_retries=0),
        transport=httpx2.MockTransport(handler),
        compression=False,
    )


async def classify(
    recorder: Recorder,
    events: list[SourceEvent],
    mode: NewsPolicyMode = NewsPolicyMode.STRICT,
    *,
    max_events_per_request: int = MAX_EVENTS_PER_REQUEST,
) -> ClassifyStoryEventsOutput:
    activities = StoryClassificationActivities(
        make_client(recorder), "jev-1.14.0", max_events_per_request=max_events_per_request
    )
    return await ActivityEnvironment().run(
        activities.classify_story_events, ClassifyStoryEventsInput(events=events, mode=mode)
    )


def request_titles(request: dict[str, object]) -> list[str]:
    state = cast(dict[str, dict[str, dict[str, str]]], request["state"])
    return [item["title"] for item in state["events"].values()]


def fixture_answers(name: str) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], fixture(name)["answers"])


def test_classification_input_and_result_are_bounded_and_validated() -> None:
    assert ClassifyStoryEventsInput(events=[]).mode is NewsPolicyMode.STRICT
    too_many = [event(str(i)) for i in range(MAX_EVENTS + 1)]
    with pytest.raises(ValidationError):
        ClassifyStoryEventsInput(events=too_many)
    with pytest.raises(ValidationError, match="duplicate external_id"):
        ClassifyStoryEventsInput(events=[event("1"), event("1")])
    with pytest.raises(ValidationError):
        ClassifyStoryEventsInput.model_validate({"events": [], "mode": "loose"})
    with pytest.raises(ValidationError):
        ClassifyStoryEventsInput.model_validate({"events": [], "extra": 1})

    good = StoryJudgments(
        ai_central=0.9, routine_industry=0.1, human_interest=2.5, clarity=3, everyday_impact=0
    )
    for bad in (
        {"ai_central": 1.01},
        {"routine_industry": -0.1},
        {"human_interest": 3.01},
        {"clarity": -1},
        {"everyday_impact": 4},
    ):
        with pytest.raises(ValidationError):
            StoryJudgments.model_validate({**good.model_dump(), **bad})
    result = EventClassification(
        external_id="1", judgments=good, eligible=True, reason=DecisionReason.ELIGIBLE
    )
    with pytest.raises(ValidationError):
        ClassifyStoryEventsOutput(
            model="jev-1.14.0",
            mode=NewsPolicyMode.STRICT,
            results=[result] * (MAX_EVENTS + 1),
        )


@pytest.mark.asyncio
async def test_questions_are_atomic_typed_and_exclude_engagement_metrics() -> None:
    recorder = Recorder(fixture("positive"))
    await classify(recorder, [event("1", AUTHOR_TITLE, {"score": 987654, "comments": 43210})])

    (request,) = recorder.requests
    assert request["model"] == "jev-1.14.0"
    state = cast(dict[str, object], request["state"])
    assert set(state) == {"note", "events"}
    assert state["events"] == {
        "e0": {
            "title": AUTHOR_TITLE,
            "url": "https://example.com/1",
            "published_at": "2026-11-01",
        }
    }
    sent = json.dumps(state)
    for leaked in ("987654", "43210", '"score"', '"comments"', "metrics"):
        assert leaked not in sent

    questions = cast(dict[str, dict[str, object]], request["questions"])
    assert set(questions) == {f"e0__{judgment.value}" for judgment in Judgment}
    for judgment in (Judgment.AI_CENTRAL, Judgment.ROUTINE_INDUSTRY):
        assert questions[f"e0__{judgment.value}"]["type"] == "noul"
    for judgment in (Judgment.HUMAN_INTEREST, Judgment.CLARITY, Judgment.EVERYDAY_IMPACT):
        question = questions[f"e0__{judgment.value}"]
        assert question["type"] == "score"
        criteria = cast(list[str], question["criteria"])
        assert len(criteria) == 4
        assert len(set(criteria)) == 4
    for question in questions.values():
        # Each question names the one event it is about and asks one thing.
        assert "`events.e0.title`" in cast(str, question["instructions"])
        assert " and " not in cast(str, question["instructions"])


def test_question_criteria_cover_author_positive_and_clickhouse_negative() -> None:
    def text(judgment: Judgment) -> str:
        return question_for(judgment, "e0").model_dump_json().lower()

    # The criteria describe kinds of stories, never the calibration headlines themselves.
    everything = " ".join(text(judgment) for judgment in Judgment)
    for forbidden in ("clickhouse", "south african", "370 books", "author defends"):
        assert forbidden not in everything
    assert isinstance(question_for(Judgment.AI_CENTRAL, "e0"), Noul)
    assert isinstance(question_for(Judgment.HUMAN_INTEREST, "e0"), Score)

    # A creator's personal use of AI is the positive human-interest and AI-central shape...
    assert "person's experience" in text(Judgment.HUMAN_INTEREST)
    assert "creative" in text(Judgment.HUMAN_INTEREST)
    assert "what an ai tool produced or did" in text(Judgment.AI_CENTRAL)
    # ...and a data-platform acquisition is the routine enterprise-infrastructure exclusion.
    routine = text(Judgment.ROUTINE_INDUSTRY)
    for phrase in ("acquisitions", "data or cloud infrastructure", "industry insiders"):
        assert phrase in routine


@pytest.mark.asyncio
async def test_recorded_positive_and_negative_fixtures_decide_as_calibrated() -> None:
    positive = await classify(Recorder(fixture("positive")), [event("1", AUTHOR_TITLE)])
    negative = await classify(Recorder(fixture("negative")), [event("2", CLICKHOUSE_TITLE)])
    for mode in NewsPolicyMode:
        positive_mode = await classify(
            Recorder(fixture("positive")), [event("1", AUTHOR_TITLE)], mode
        )
        negative_mode = await classify(
            Recorder(fixture("negative")), [event("2", CLICKHOUSE_TITLE)], mode
        )
        assert positive_mode.results[0].eligible, mode
        assert not negative_mode.results[0].eligible, mode
    assert positive.model == "jev-1.14.0"
    assert negative.results[0].judgments.routine_industry >= 0.7
    # The routine exclusion alone also blocks it once AI centrality is granted.
    levels = (
        negative.results[0]
        .judgments.levels()
        .model_copy(update={"ai_central": Verdict.YES, "human_interest": 3, "clarity": 3})
    )
    assert decide(levels, NewsPolicyMode.RELAXED).reason is DecisionReason.ROUTINE_INDUSTRY


def test_large_batches_chunk_deterministically_and_preserve_event_order() -> None:
    events = [event(str(i), "A fairly long headline " * 8) for i in range(MAX_EVENTS)]
    chunks = chunk_events(events)
    assert chunks == chunk_events(events)
    assert [e.external_id for chunk in chunks for e in chunk] == [e.external_id for e in events]
    assert all(1 <= len(chunk) <= MAX_EVENTS_PER_REQUEST for chunk in chunks)
    assert len(chunks) == MAX_EVENTS // MAX_EVENTS_PER_REQUEST
    for chunk in chunks:
        assert _estimated_tokens(_state_for(chunk), len(chunk)) <= MAX_ESTIMATED_REQUEST_TOKENS

    # The token estimate, not only the event count, splits oversized requests.
    one = _estimated_tokens(_state_for(events[:1]), 1)
    tight = chunk_events(events[:6], max_tokens=one * 2 + 1)
    assert [len(chunk) for chunk in tight] == [2, 2, 2]
    assert chunk_events([]) == []
    with pytest.raises(ValueError):
        chunk_events(events, max_events=0)
    assert ESTIMATED_CHARS_PER_TOKEN >= 1


@pytest.mark.asyncio
async def test_chunked_requests_keep_order_and_return_one_result_per_event() -> None:
    recorder = Recorder(*cast(list[dict[str, object]], fixture("chunked")["responses"]))
    events = [event("a", "First"), event("b", "Second"), event("c", "Third")]
    output = await classify(recorder, events, max_events_per_request=2)

    assert len(recorder.requests) == 2
    titles = [request_titles(r) for r in recorder.requests]
    assert titles == [["First", "Second"], ["Third"]]
    assert [r.external_id for r in output.results] == ["a", "b", "c"]
    assert [r.eligible for r in output.results] == [True, False, True]
    assert output.eligible_ids() == {"a", "c"}


@pytest.mark.asyncio
async def test_empty_batch_makes_no_request() -> None:
    recorder = Recorder(fixture("positive"))
    output = await classify(recorder, [])
    assert output.results == []
    assert recorder.requests == []


def _without(name: str, key: str) -> dict[str, object]:
    body = fixture(name)
    answers = dict(fixture_answers(name))
    del answers[key]
    return {**body, "answers": answers}


def _with(name: str, key: str, answer: dict[str, object]) -> dict[str, object]:
    return {**fixture(name), "answers": {**fixture_answers(name), key: answer}}


def _score_probabilities(probabilities: dict[str, float]) -> dict[str, object]:
    return {**fixture_answers("positive")["e0__clarity"], "probabilities": probabilities}


INVALID_RESPONSES: dict[str, dict[str, object]] = {
    "missing_answer": fixture("malformed"),
    "missing_event": _without("mixed", "e1__clarity"),
    "unknown_id": _with("positive", "e7__ai_central", {"type": "noul", "noul": 0.5}),
    "wrong_type": _with("positive", "e0__clarity", {"type": "noul", "noul": 0.5}),
    "noul_above_one": _with("positive", "e0__ai_central", {"type": "noul", "noul": 1.2}),
    "noul_negative": _with("positive", "e0__routine_industry", {"type": "noul", "noul": -0.1}),
    "score_out_of_range": _with(
        "positive", "e0__clarity", {**fixture_answers("positive")["e0__clarity"], "score": 3.4}
    ),
    "score_missing_level": _with(
        "positive", "e0__clarity", _score_probabilities({"0": 0.5, "1": 0.5, "2": 0.0})
    ),
    "score_probabilities_do_not_sum": _with(
        "positive",
        "e0__clarity",
        _score_probabilities({"0": 0.4, "1": 0.4, "2": 0.4, "3": 0.4}),
    ),
    "score_probability_out_of_range": _with(
        "positive",
        "e0__clarity",
        _score_probabilities({"0": -0.5, "1": 0.5, "2": 0.5, "3": 0.5}),
    ),
    "no_answers": {"model": "jev-1.14.0", "answers": {}, "usage": {}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(INVALID_RESPONSES))
async def test_missing_duplicate_or_unknown_answers_fail_closed(case: str) -> None:
    events = [event("1"), event("2")] if case == "missing_event" else [event("1")]
    with pytest.raises(ApplicationError) as caught:
        await classify(Recorder(INVALID_RESPONSES[case]), events)
    assert caught.value.non_retryable is True
    assert caught.value.type == INVALID_CLASSIFICATION_ERROR_TYPE


@pytest.mark.asyncio
async def test_unparseable_api_body_is_a_non_retryable_invalid_result() -> None:
    recorder = Recorder({"model": "jev-1.14.0", "answers": {"e0__ai_central": {"noul": 0.5}}})
    with pytest.raises(ApplicationError) as caught:
        await classify(recorder, [event("1")])
    assert caught.value.non_retryable is True


def test_duplicate_event_ids_are_rejected_before_any_request() -> None:
    with pytest.raises(ValidationError):
        ClassifyStoryEventsInput(events=[event("same"), event("same", "Another")])


@pytest.mark.asyncio
async def test_transient_typesafe_errors_retry_and_invalid_results_do_not() -> None:
    # Transient API failures propagate unchanged so Temporal's retry policy handles them.
    with pytest.raises(TypeSafeAPIError) as api_error:
        await classify(Recorder(fixture("transient_error")), [event("1")])
    assert api_error.value.status == 503
    assert not isinstance(api_error.value, ApplicationError)

    with pytest.raises(TypeSafeAPIConnectionError):
        await classify(Recorder(httpx2.ConnectError("connection reset")), [event("1")])

    # A failure after one good chunk fails the whole call; no partial answer is returned.
    recorder = Recorder(
        cast(list[dict[str, object]], fixture("chunked")["responses"])[0],
        fixture("transient_error"),
    )
    with pytest.raises(TypeSafeAPIError):
        await classify(recorder, [event("a"), event("b"), event("c")], max_events_per_request=2)
    assert len(recorder.requests) == 2

    # Invalid content is the opposite: non-retryable, so Temporal does not call Jev again.
    with pytest.raises(ApplicationError) as invalid:
        await classify(Recorder(fixture("malformed")), [event("1")])
    assert invalid.value.non_retryable is True


ALL_LEVELS = [
    JudgmentLevels(
        ai_central=ai,
        routine_industry=routine,
        human_interest=interest,
        clarity=clarity,
        everyday_impact=impact,
    )
    for ai, routine, interest, clarity, impact in itertools.product(
        Verdict, Verdict, range(4), range(4), range(4)
    )
]


def test_verdict_and_level_bands() -> None:
    assert verdict_for(0.7) is Verdict.YES
    assert verdict_for(0.69) is Verdict.UNSURE
    assert verdict_for(0.31) is Verdict.UNSURE
    assert verdict_for(0.3) is Verdict.NO
    assert [level_for(score) for score in (0, 0.49, 0.5, 1.49, 1.5, 2.49, 2.5, 3)] == [
        0,
        0,
        1,
        1,
        2,
        2,
        3,
        3,
    ]


def test_policy_truth_table_covers_every_judgment_combination() -> None:
    assert len(ALL_LEVELS) == 3 * 3 * 4 * 4 * 4
    reasons_seen: set[DecisionReason] = set()
    for mode in NewsPolicyMode:
        for levels in ALL_LEVELS:
            decision = decide(levels, mode)
            assert isinstance(decision, Decision)
            assert decision == decide(levels, mode)
            assert decision.eligible == (
                decision.reason
                in {DecisionReason.ELIGIBLE, DecisionReason.ELIGIBLE_IMPACT_OVERRIDE}
            )
            reasons_seen.add(decision.reason)
    assert reasons_seen == set(DecisionReason)


def test_ai_centrality_remains_required_in_every_mode() -> None:
    for mode in NewsPolicyMode:
        for levels in ALL_LEVELS:
            if levels.ai_central is Verdict.YES:
                continue
            decision = decide(levels, mode)
            assert not decision.eligible
            assert decision.reason is DecisionReason.AI_NOT_CENTRAL
        perfect = JudgmentLevels(
            ai_central=Verdict.UNSURE,
            routine_industry=Verdict.NO,
            human_interest=3,
            clarity=3,
            everyday_impact=3,
        )
        assert not decide(perfect, mode).eligible


def test_strict_balanced_relaxed_have_monotone_eligibility() -> None:
    def eligible(mode: NewsPolicyMode) -> set[int]:
        return {i for i, levels in enumerate(ALL_LEVELS) if decide(levels, mode).eligible}

    strict, balanced, relaxed = (eligible(mode) for mode in NewsPolicyMode)
    assert strict < balanced < relaxed
    assert NewsPolicyMode.STRICT.value == "strict"

    exceptional = JudgmentLevels(
        ai_central=Verdict.YES,
        routine_industry=Verdict.NO,
        human_interest=3,
        clarity=3,
        everyday_impact=0,
    )
    middling = exceptional.model_copy(update={"human_interest": 2})
    faint = exceptional.model_copy(update={"human_interest": 1, "clarity": 1})
    assert [decide(exceptional, mode).eligible for mode in NewsPolicyMode] == [True, True, True]
    assert [decide(middling, mode).eligible for mode in NewsPolicyMode] == [False, True, True]
    assert [decide(faint, mode).eligible for mode in NewsPolicyMode] == [False, False, True]
    unsure_routine = exceptional.model_copy(update={"routine_industry": Verdict.UNSURE})
    assert [decide(unsure_routine, mode).eligible for mode in NewsPolicyMode] == [
        False,
        True,
        True,
    ]
    # Thresholds only ever loosen from strict to relaxed.
    strict_policy, balanced_policy, relaxed_policy = (POLICIES[mode] for mode in NewsPolicyMode)
    assert strict_policy.min_human_interest >= balanced_policy.min_human_interest
    assert balanced_policy.min_human_interest >= relaxed_policy.min_human_interest
    assert strict_policy.min_clarity >= balanced_policy.min_clarity >= relaxed_policy.min_clarity
    assert strict_policy.routine_override_impact >= relaxed_policy.routine_override_impact


def test_everyday_impact_override_is_bounded_by_policy() -> None:
    routine = JudgmentLevels(
        ai_central=Verdict.YES,
        routine_industry=Verdict.YES,
        human_interest=3,
        clarity=3,
        everyday_impact=0,
    )
    for mode in NewsPolicyMode:
        needed = POLICIES[mode].routine_override_impact
        for impact in range(4):
            decision = decide(routine.model_copy(update={"everyday_impact": impact}), mode)
            if impact >= needed:
                assert decision.reason is DecisionReason.ELIGIBLE_IMPACT_OVERRIDE
            else:
                assert decision.reason is DecisionReason.ROUTINE_INDUSTRY
        # Strong impact never rescues a story that fails the other gates.
        strong = routine.model_copy(update={"everyday_impact": 3})
        assert (
            decide(strong.model_copy(update={"ai_central": Verdict.NO}), mode).reason
            is DecisionReason.AI_NOT_CENTRAL
        )
        assert (
            decide(strong.model_copy(update={"human_interest": 0}), mode).reason
            is DecisionReason.LOW_HUMAN_INTEREST
        )
        assert (
            decide(strong.model_copy(update={"clarity": 0}), mode).reason
            is DecisionReason.HARD_TO_UNDERSTAND
        )
    # A non-routine story does not need any impact.
    plain = routine.model_copy(update={"routine_industry": Verdict.NO})
    assert decide(plain, NewsPolicyMode.STRICT).reason is DecisionReason.ELIGIBLE


def _with_confidence(name: str, confidence: float) -> dict[str, object]:
    answers = {
        key: ({**answer, "confidence": confidence} if answer["type"] == "score" else answer)
        for key, answer in fixture_answers(name).items()
    }
    return {**fixture(name), "answers": answers}


@pytest.mark.asyncio
async def test_engagement_and_confidence_do_not_change_eligibility() -> None:
    quiet = await classify(Recorder(fixture("positive")), [event("1", AUTHOR_TITLE, {"score": 1})])
    loud = await classify(
        Recorder(_with_confidence("positive", 0.01)),
        [event("1", AUTHOR_TITLE, {"score": 9_000_000, "comments": 9_000_000})],
    )
    unlikely = await classify(
        Recorder(_with_confidence("negative", 0.99)),
        [event("1", CLICKHOUSE_TITLE, {"score": 9_000_000, "comments": 9_000_000})],
    )
    assert quiet.results[0].eligible and loud.results[0].eligible
    assert quiet.results == loud.results
    assert not unlikely.results[0].eligible
    # The decision has no input that could carry either signal.
    assert set(JudgmentLevels.model_fields) == {j.value for j in Judgment}
    with pytest.raises(ValidationError):
        JudgmentLevels.model_validate({**ALL_LEVELS[0].model_dump(), "confidence": 0.9})
    with pytest.raises(ValidationError):
        JudgmentLevels.model_validate({**ALL_LEVELS[0].model_dump(), "score": 100})


@pytest.mark.asyncio
async def test_uncertain_answers_are_not_eligible_in_strict_mode() -> None:
    output = await classify(Recorder(fixture("uncertain")), [event("1")])
    assert output.results[0].reason is DecisionReason.AI_NOT_CENTRAL
    assert not output.results[0].eligible


@pytest.mark.asyncio
async def test_mixed_batch_returns_one_result_per_event_in_order() -> None:
    output = await classify(Recorder(fixture("mixed")), [event("x"), event("y")])
    assert [r.external_id for r in output.results] == ["x", "y"]
    assert [r.eligible for r in output.results] == [True, False]
    assert output.mode is NewsPolicyMode.STRICT


def test_typesafe_settings_default_to_a_pinned_model_and_hide_the_key() -> None:
    assert DEFAULT_TYPESAFE_MODEL == "jev-1.14.0"
    settings = load_settings({"TYPESAFE_API_KEY": "tskey-secret"})
    assert settings.typesafe_model == DEFAULT_TYPESAFE_MODEL
    assert settings.typesafe_api_key is not None
    assert settings.typesafe_api_key.get_secret_value() == "tskey-secret"
    assert "tskey-secret" not in repr(settings)
    assert load_settings({"TYPESAFE_API_KEY": "", "TYPESAFE_MODEL": " "}).typesafe_api_key is None
    assert load_settings({"TYPESAFE_MODEL": "jev-1.15.2"}).typesafe_model == "jev-1.15.2"
    for moving in ("jev-latest", "jev-preview", "gpt-4"):
        with pytest.raises(ValidationError):
            load_settings({"TYPESAFE_MODEL": moving})


def test_make_typesafe_client_requires_a_key() -> None:
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        make_typesafe_client(Settings())
    client = make_typesafe_client(Settings(typesafe_api_key=SecretStr("tskey-test")))
    assert isinstance(client, AsyncTypeSafeClient)
