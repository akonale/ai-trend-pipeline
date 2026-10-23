"""Start, show, approve or reject a daily chart run: `python -m ai_trend_pipeline.cli`."""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ai_trend_pipeline.daily_chart import (
    DAILY_CHART_TASK_QUEUE,
    ApprovalDecision,
    DailyChartInput,
    DailyChartResult,
    DailyChartWorkflow,
    ReviewState,
)
from ai_trend_pipeline.temporal_client import connect

Handle = WorkflowHandle[DailyChartWorkflow, DailyChartResult]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_trend_pipeline.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start a daily chart run")
    start.add_argument("--id", dest="wf_id", default=None)
    sub.add_parser("show", help="show the pending review").add_argument("wf_id", metavar="ID")
    for name in ("approve", "reject"):
        cmd = sub.add_parser(name, help=f"{name} the pending chart")
        cmd.add_argument("wf_id", metavar="ID")
        cmd.add_argument("--note", default="")
    return parser


def _print_state(state: ReviewState) -> None:
    print(f"status: {state.status}")
    review = state.review
    if review is None:
        return
    print(f"topic: {review.topic}")
    print(f"topic reason: {review.topic_reason}")
    print(f"caption: {review.caption}")
    print(f"png: {review.png_path}")
    print(f"attempts: {review.attempts}")
    print(f"critique passed: {review.critique.passed}")
    if not review.critique.passed:
        for reason in review.critique.reasons:
            print(f"critique reason: {reason}")


async def _start(client: Client, wf_id: str | None) -> int:
    wf_id = wf_id or f"daily-chart-{datetime.now(UTC).date().isoformat()}"
    data = DailyChartInput()
    output_dir = os.environ.get("CHART_OUTPUT_DIR")
    if output_dir:
        data = DailyChartInput(output_dir=output_dir)
    try:
        await client.start_workflow(
            DailyChartWorkflow.run,
            data,
            id=wf_id,
            task_queue=DAILY_CHART_TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
        )
    except WorkflowAlreadyStartedError:
        print(f"A run with id {wf_id} already exists.")
        return 1
    print(wf_id)
    return 0


async def _decide(handle: Handle, approved: bool, note: str) -> int:
    state = await handle.query(DailyChartWorkflow.review_state)
    if state.status != "awaiting_decision":
        print(f"Cannot decide: status is {state.status}.")
        return 1
    await handle.signal(DailyChartWorkflow.decide, ApprovalDecision(approved=approved, note=note))
    result = await handle.result()
    print(f"status: {result.status}")
    if result.post is not None:
        label = "already posted" if result.post.already_posted else "posted"
        print(f"{label}: {result.post.uri}")
    return 0


async def run_cli(argv: list[str], client: Client) -> int:
    args = _parser().parse_args(argv)
    if args.command == "start":
        return await _start(client, args.wf_id)
    handle: Handle = client.get_workflow_handle_for(DailyChartWorkflow.run, args.wf_id)
    try:
        if args.command == "show":
            _print_state(await handle.query(DailyChartWorkflow.review_state))
            return 0
        return await _decide(handle, args.command == "approve", args.note)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            print(f"Error: workflow {args.wf_id} not found.")
            return 1
        raise


async def _main() -> int:
    return await run_cli(sys.argv[1:], await connect())


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
