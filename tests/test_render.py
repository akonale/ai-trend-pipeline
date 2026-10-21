"""Tests for the Vega-Lite PNG render activity."""

import json
import struct
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ai_trend_pipeline.chart_spec import ChartSpecActivities, WriteChartSpecInput
from ai_trend_pipeline.render import (
    INVALID_SPEC_ERROR_TYPE,
    RenderChartInput,
    RenderChartOutput,
    render_chart_png,
    spec_sha256,
)
from tests.test_chart_spec import make_client, make_table

FIXTURES = Path(__file__).parent / "fixtures"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _spec() -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue], json.loads((FIXTURES / "vega_lite_line_chart.json").read_text())
    )


async def _render(spec: dict[str, JsonValue], out: Path) -> RenderChartOutput:
    return await ActivityEnvironment().run(
        render_chart_png, RenderChartInput(vega_lite_spec=spec, output_dir=str(out))
    )


@pytest.mark.asyncio
async def test_render_writes_png(tmp_path: Path) -> None:
    result = await _render(_spec(), tmp_path / "out")
    data = Path(result.path).read_bytes()
    assert data.startswith(PNG_MAGIC)
    assert len(data) > 1000
    assert result.size_bytes == len(data)
    width, height = struct.unpack(">II", data[16:24])
    assert width > 0 and height > 0


@pytest.mark.asyncio
async def test_render_is_idempotent(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = await _render(_spec(), out)
    second = await _render(_spec(), out)
    assert first.path == second.path
    assert first.spec_sha256 == second.spec_sha256
    assert len(list(out.iterdir())) == 1


@pytest.mark.asyncio
async def test_path_derives_from_spec_hash(tmp_path: Path) -> None:
    spec = _spec()
    result = await _render(spec, tmp_path)
    digest = spec_sha256(spec)
    assert result.spec_sha256 == digest
    assert Path(result.path).name == f"chart-{digest[:16]}.png"
    changed = {**spec, "title": "Other title"}
    other = await _render(changed, tmp_path)
    assert other.path != result.path


@pytest.mark.asyncio
async def test_invalid_spec_is_non_retryable(tmp_path: Path) -> None:
    with pytest.raises(ApplicationError) as exc:
        await _render({"data": {"values": []}}, tmp_path)
    assert exc.value.type == INVALID_SPEC_ERROR_TYPE
    assert exc.value.non_retryable is True


@pytest.mark.asyncio
async def test_fixture_to_png_end_to_end(tmp_path: Path) -> None:
    activities = ChartSpecActivities(make_client())
    chart = await ActivityEnvironment().run(
        activities.write_chart_spec,
        WriteChartSpecInput(topic="Life expectancy", table=make_table()),
    )
    result = await _render(chart.vega_lite_spec, tmp_path)
    data = Path(result.path).read_bytes()
    assert data.startswith(PNG_MAGIC)
    assert len(data) > 1000
