"""Activity that fetches a matching Our World in Data grapher chart as a typed table."""

import csv
import re

import httpx
from pydantic import BaseModel, Field
from temporalio import activity
from temporalio.exceptions import ApplicationError

OWID_BASE_URL = "https://ourworldindata.org/grapher/"
NO_MATCH_ERROR_TYPE = "NoMatchingDataset"


class OwidDataset(BaseModel):
    slug: str
    title: str
    keywords: tuple[str, ...]


class FetchOwidInput(BaseModel):
    topic: str = Field(min_length=1)
    since_year: int = Field(default=2000, ge=1800, le=2100)


class OwidRow(BaseModel):
    entity: str
    code: str | None
    year: int
    value: float


class OwidTable(BaseModel):
    slug: str
    title: str
    value_column: str
    rows: list[OwidRow]


CATALOG: tuple[OwidDataset, ...] = (
    OwidDataset(
        slug="co-emissions-per-capita",
        title="CO2 emissions per capita",
        keywords=("co2", "carbon", "emissions", "climate"),
    ),
    OwidDataset(
        slug="share-of-individuals-using-the-internet",
        title="Share of the population using the Internet",
        keywords=("internet", "online", "broadband"),
    ),
    OwidDataset(
        slug="solar-energy-consumption",
        title="Solar energy consumption",
        keywords=("solar", "renewable", "renewables"),
    ),
    OwidDataset(
        slug="electric-car-sales-share",
        title="Share of new cars sold that are electric",
        keywords=("electric car", "electric vehicle", "ev", "evs", "tesla"),
    ),
    OwidDataset(
        slug="life-expectancy",
        title="Life expectancy",
        keywords=("life expectancy", "health", "longevity"),
    ),
    OwidDataset(
        slug="gdp-per-capita-worldbank",
        title="GDP per capita",
        keywords=("gdp", "economy", "economic", "income"),
    ),
)


def match_dataset(topic: str) -> OwidDataset | None:
    lowered = topic.lower()
    for dataset in CATALOG:
        if dataset.title.lower() == lowered:
            return dataset
    for dataset in CATALOG:
        for keyword in dataset.keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered):
                return dataset
    return None


def make_owid_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=OWID_BASE_URL, timeout=30.0, follow_redirects=True)


class OwidActivities:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @activity.defn
    async def fetch_owid_dataset(self, data: FetchOwidInput) -> OwidTable:
        dataset = match_dataset(data.topic)
        if dataset is None:
            raise ApplicationError(
                f"No OWID dataset matches topic {data.topic!r}",
                type=NO_MATCH_ERROR_TYPE,
                non_retryable=True,
            )
        response = await self._client.get(
            f"{dataset.slug}.csv",
            params={"csvType": "full", "useColumnShortNames": "true"},
        )
        if response.status_code == 404:
            raise ApplicationError(
                f"OWID chart {dataset.slug!r} not found (404)", non_retryable=True
            )
        response.raise_for_status()

        reader = csv.reader(response.text.splitlines())
        empty: list[str] = []
        header: list[str] = next(reader, empty)
        # Real OWID CSVs use lowercase headers, so compare case-insensitively.
        if len(header) < 4 or [h.lower() for h in header[:3]] != ["entity", "code", "year"]:
            raise ApplicationError(
                f"Unexpected OWID CSV header for {dataset.slug!r}: {header!r}",
                non_retryable=True,
            )
        rows: list[OwidRow] = []
        for record in reader:
            if len(record) < 4 or not record[3].strip():
                continue
            year = int(record[2])
            if year < data.since_year:
                continue
            rows.append(
                OwidRow(
                    entity=record[0],
                    code=record[1] or None,
                    year=year,
                    value=float(record[3]),
                )
            )
        return OwidTable(slug=dataset.slug, title=dataset.title, value_column=header[3], rows=rows)
