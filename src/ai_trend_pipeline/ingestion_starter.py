"""Operator entry point: `python -m ai_trend_pipeline.ingestion_starter` (`just ingest`).

Loads settings, connects to Temporal, and asks the service to ensure the configured source
ingestion workflows exist. It never starts ingestion from worker startup.
"""

import asyncio

from temporalio.client import Client

from ai_trend_pipeline.config import Settings, load_settings
from ai_trend_pipeline.services.ingestion import SourceIngestionService
from ai_trend_pipeline.services.temporal_gateway import TemporalSourceIngestionGateway
from ai_trend_pipeline.temporal_client import connect


async def main() -> None:
    settings: Settings = load_settings()
    client: Client = await connect(settings)
    service = SourceIngestionService(TemporalSourceIngestionGateway(client), settings)
    result = await service.ensure()
    for workflow_id in result.workflow_ids:
        print(workflow_id)


if __name__ == "__main__":
    asyncio.run(main())
