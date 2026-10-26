"""Start, show, approve or reject a daily chart run: `python -m ai_trend_pipeline.cli`."""

import argparse
import asyncio
import sys

from temporalio.client import Client

from ai_trend_pipeline.config import Settings, load_settings
from ai_trend_pipeline.daily_chart import DailyChartResult, ReviewState
from ai_trend_pipeline.services.charts import (
    ChartRunRef,
    ChartService,
    DecideChartRequest,
    NotAwaitingDecision,
    RunAlreadyExists,
    RunNotFound,
    StartChartRequest,
)
from ai_trend_pipeline.services.temporal_gateway import TemporalChartRunGateway
from ai_trend_pipeline.temporal_client import connect


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai_trend_pipeline.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start a daily chart run")
    start.add_argument("--id", dest="wf_id", default=None)
    start.add_argument("--topic", default=None)
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


def _print_result(result: DailyChartResult) -> None:
    print(f"status: {result.status}")
    if result.post is not None:
        label = "already posted" if result.post.already_posted else "posted"
        print(f"{label}: {result.post.uri}")


async def _dispatch(args: argparse.Namespace, service: ChartService) -> None:
    if args.command == "start":
        result = await service.start(StartChartRequest(wf_id=args.wf_id, topic=args.topic))
        print(result.wf_id)
    elif args.command == "show":
        _print_state(await service.show(ChartRunRef(wf_id=args.wf_id)))
    else:
        decision = DecideChartRequest(
            wf_id=args.wf_id, approved=args.command == "approve", note=args.note
        )
        _print_result(await service.decide(decision))


async def run_cli(argv: list[str], client: Client, settings: Settings | None = None) -> int:
    args = _parser().parse_args(argv)
    service = ChartService(TemporalChartRunGateway(client), settings or load_settings())
    try:
        await _dispatch(args, service)
    except RunAlreadyExists as exc:
        print(f"A run with id {exc.wf_id} already exists.")
        return 1
    except NotAwaitingDecision as exc:
        print(f"Cannot decide: status is {exc.status}.")
        return 1
    except RunNotFound as exc:
        print(f"Error: workflow {exc.wf_id} not found.")
        return 1
    return 0


async def _main() -> int:
    settings = load_settings()
    return await run_cli(sys.argv[1:], await connect(settings), settings)


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
