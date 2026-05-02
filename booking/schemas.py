from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class BookingRequest(BaseModel):
    venue_id: int = Field(..., gt=0)
    date: date
    party_size: int = Field(..., ge=1, le=20)
    time: time
    time_delta: Optional[int] = Field(default=None, ge=0)

    @field_validator('time', mode='before')
    @classmethod
    def parse_hhmm(cls, v):
        # Pydantic v2 expects HH:MM:SS; accept bare HH:MM from callers.
        if isinstance(v, str) and len(v) == 5:
            return datetime.strptime(v, '%H:%M').time()
        return v


class SlotInfo(BaseModel):
    config_id: str
    start_time: time
    type: str
