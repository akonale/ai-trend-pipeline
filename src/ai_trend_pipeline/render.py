"""Activity that renders a Vega-Lite spec to a PNG file."""

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path

import vl_convert
from pydantic import BaseModel, Field, JsonValue
from temporalio import activity
from temporalio.exceptions import ApplicationError

PNG_SCALE = 2.0
INVALID_SPEC_ERROR_TYPE = "InvalidVegaLiteSpec"


class RenderChartInput(BaseModel):
    vega_lite_spec: dict[str, JsonValue]
    output_dir: str = Field(min_length=1)


class RenderChartOutput(BaseModel):
    path: str
    spec_sha256: str
    size_bytes: int


def spec_sha256(spec: dict[str, JsonValue]) -> str:
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@activity.defn
async def render_chart_png(data: RenderChartInput) -> RenderChartOutput:
    out_dir = Path(data.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    digest = spec_sha256(data.vega_lite_spec)
    try:
        png = await asyncio.to_thread(
            vl_convert.vegalite_to_png, json.dumps(data.vega_lite_spec), scale=PNG_SCALE
        )
    except ValueError as exc:
        raise ApplicationError(
            f"Vega-Lite spec could not be rendered: {exc}",
            type=INVALID_SPEC_ERROR_TYPE,
            non_retryable=True,
        ) from exc
    target = out_dir / f"chart-{digest[:16]}.png"
    fd, tmp_name = tempfile.mkstemp(dir=out_dir, prefix=".chart-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png)
        os.replace(tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return RenderChartOutput(path=str(target), spec_sha256=digest, size_bytes=len(png))
