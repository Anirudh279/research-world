"""Shared native-discovery contract for HTTP and MCP callers."""
from pydantic import BaseModel, ConfigDict, Field


class DiscoveredSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=4000)
    query: str = Field(default='', max_length=600)
    rationale: str = Field(default='', max_length=2000)
