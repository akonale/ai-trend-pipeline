"""Tests for the approval CLI, run against the time-skipping environment client."""

import uuid

import pytest
from temporalio.client import WorkflowExecutionStatus

from ai_trend_pipeline.cli import run_cli
from ai_trend_pipeline.daily_chart import DailyChartResult, DailyChartWorkflow
from tests.test_daily_chart import (
    FAIL,
    PNG_PATH,
    POST_URI,
    FakeDailyActivities,
    make_worker,
    start_env,
    start_workflow,
    wait_for_state,
)


@pytest.mark.asyncio
async def test_cli_approve_done_when(capsys: pytest.CaptureFixture[str]) -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        assert (await handle.describe()).status == WorkflowExecutionStatus.RUNNING
        assert await run_cli(["approve", handle.id], env.client) == 0
        result = await handle.result()
    assert result.status == "approved"
    assert "approved" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_reject() -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        assert await run_cli(["reject", handle.id, "--note", "bad"], env.client) == 0
        result: DailyChartResult = await handle.result()
    assert result.status == "rejected"
    assert result.note == "bad"


@pytest.mark.asyncio
async def test_cli_start_uses_id_and_output_dir(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHART_OUTPUT_DIR", "custom-out")
    wf_id = f"cli-{uuid.uuid4()}"
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        assert await run_cli(["start", "--id", wf_id], env.client) == 0
        assert wf_id in capsys.readouterr().out
        handle = env.client.get_workflow_handle_for(DailyChartWorkflow.run, wf_id)
        await wait_for_state(handle, "awaiting_decision")
    assert fakes.render_inputs[0].output_dir == "custom-out"


@pytest.mark.asyncio
async def test_cli_start_duplicate_id_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    wf_id = f"cli-{uuid.uuid4()}"
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        assert await run_cli(["start", "--id", wf_id], env.client) == 0
        capsys.readouterr()
        assert await run_cli(["start", "--id", wf_id], env.client) == 1
        assert "already" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_cli_show_prints_review(capsys: pytest.CaptureFixture[str]) -> None:
    fakes = FakeDailyActivities(critiques=[FAIL, FAIL])
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        assert await run_cli(["show", handle.id], env.client) == 0
    out = capsys.readouterr().out
    assert "awaiting_decision" in out
    assert "caption 2" in out
    assert PNG_PATH in out
    for reason in FAIL.reasons:
        assert reason in out


@pytest.mark.asyncio
async def test_cli_approve_when_not_awaiting_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        assert await run_cli(["approve", handle.id, "--note", "first"], env.client) == 0
        capsys.readouterr()
        assert await run_cli(["approve", handle.id, "--note", "second"], env.client) == 1
        assert "approved" in capsys.readouterr().out
        result = await handle.result()
    assert result.note == "first"


@pytest.mark.asyncio
async def test_cli_unknown_id_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    async with await start_env() as env:
        assert await run_cli(["show", "missing"], env.client) == 1
        assert await run_cli(["approve", "missing"], env.client) == 1
    assert capsys.readouterr().out.lower().count("not found") >= 2


@pytest.mark.asyncio
async def test_cli_approve_prints_post_uri(capsys: pytest.CaptureFixture[str]) -> None:
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        handle = await start_workflow(env)
        await wait_for_state(handle, "awaiting_decision")
        assert await run_cli(["approve", handle.id], env.client) == 0
    assert f"posted: {POST_URI}" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_start_with_topic() -> None:
    wf_id = f"cli-{uuid.uuid4()}"
    fakes = FakeDailyActivities()
    async with await start_env() as env, make_worker(env, fakes):
        assert (
            await run_cli(["start", "--id", wf_id, "--topic", "Life expectancy"], env.client) == 0
        )
        handle = env.client.get_workflow_handle_for(DailyChartWorkflow.run, wf_id)
        state = await wait_for_state(handle, "awaiting_decision")
    assert state.review is not None
    assert state.review.topic == "Life expectancy"
    assert "pick_topic" not in fakes.calls
