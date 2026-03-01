"""
Pydantic request models for the API.
"""
from pydantic import BaseModel
from typing import Optional


class TopUpRequest(BaseModel):
    """Request to add a ledger entry."""
    provider: str
    amount: float
    note: str = ""
    project: Optional[str] = None


class TopUpDeleteRequest(BaseModel):
    """Request to remove a ledger entry."""
    provider: str
    index: int
    project: Optional[str] = None
