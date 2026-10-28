"""Pydantic models shaped for templates."""

from pydantic import BaseModel


class HealthView(BaseModel):
    status: str
    temporal_address: str


class HomeView(BaseModel):
    error: str = ""
    topic: str = ""


class RunSummaryView(BaseModel):
    wf_id: str
    status: str
    topic: str | None = None
