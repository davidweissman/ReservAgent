"""
Pydantic AI agent for extracting reservation intent from natural-language input.

Flow:
  user message
    → ReservationAgentService.parse_intent()
    → _get_agent().run_sync()
        ↳ search_resy_venue tool  →  ResyClient.search_venues()  →  venue_id
    → BookingIntent (restaurant_name, venue_id, date, time, party_size)

The Agent is constructed lazily on first use so that Django management commands
(check, migrate, etc.) work even when ANTHROPIC_API_KEY / Resy credentials are absent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import time as Time
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from .tools import VenueNotFoundError, VenueSearchResult

if TYPE_CHECKING:
    from .resy_client import ResyClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

class BookingIntent(BaseModel):
    restaurant_name: str = Field(..., description="Name of the restaurant as stated by the user")
    venue_id: int = Field(..., description="Resy venue ID resolved via the search_resy_venue tool")
    date: Date = Field(..., description="Reservation date resolved to YYYY-MM-DD")
    time: Optional[Time] = Field(None, description="Desired time in HH:MM; null if unspecified")
    party_size: int = Field(..., ge=1, le=20, description="Number of guests")


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------

@dataclass
class SupportDeps:
    """
    Injected into every agent run.

    current_date  — anchors relative date expressions ("tomorrow", "next Friday").
    resy_client   — authenticated Resy session; used by the search_resy_venue tool.
    lat / lon     — geographic centre for venue search; defaults to Manhattan.
    """
    current_date: Date
    resy_client: ResyClient
    lat: float = field(default=40.7428)
    lon: float = field(default=-73.9712)


# ---------------------------------------------------------------------------
# Lazy agent factory
# ---------------------------------------------------------------------------

_booking_agent: Agent[SupportDeps, BookingIntent] | None = None


def _build_agent() -> Agent[SupportDeps, BookingIntent]:
    from django.conf import settings
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    model = AnthropicModel(
        'claude-sonnet-4-6',
        provider=AnthropicProvider(api_key=settings.ANTHROPIC_API_KEY),
    )

    agent: Agent[SupportDeps, BookingIntent] = Agent(
        model,
        deps_type=SupportDeps,
        output_type=BookingIntent,
        system_prompt=(
            "You are a reservation agent. "
            "When a user provides a restaurant name, you MUST first call the "
            "search_resy_venue tool to find the correct resy_venue_id. "
            "If the tool returns multiple results, use the first one. "
            "Once you have the venue ID, extract the date, time, and party size "
            "from the user's message and return the complete BookingIntent. "
            "If search_resy_venue returns 'No restaurant found', inform the user "
            "and ask them to clarify the name or location."
        ),
    )

    @agent.system_prompt
    def inject_current_date(ctx: RunContext[SupportDeps]) -> str:
        return f"Today's date is {ctx.deps.current_date.isoformat()}."

    @agent.tool
    async def search_resy_venue(
        ctx: RunContext[SupportDeps],
        query: str,
        lat: float = 40.7428,
        lon: float = -73.9712,
    ) -> str:
        """
        Search Resy for a restaurant by name and return its venue_id, name, and url_slug.

        Use the lat/lon from context unless the user specifies a different city.
        Always call this before populating venue_id in the final result.

        Returns a JSON object on success, or "No restaurant found." if there are no hits.
        """
        actual_lat = lat if lat != 40.7428 else ctx.deps.lat
        actual_lon = lon if lon != -73.9712 else ctx.deps.lon

        log.info(
            '[search_resy_venue] query=%r lat=%.4f lon=%.4f',
            query, actual_lat, actual_lon,
        )
        try:
            result: VenueSearchResult = await asyncio.to_thread(
                ctx.deps.resy_client.search_venues, query, actual_lat, actual_lon
            )
        except VenueNotFoundError:
            log.info('[search_resy_venue] no results for query=%r', query)
            return 'No restaurant found.'

        log.info('[search_resy_venue] found venue_id=%d name=%r', result.venue_id, result.name)
        return json.dumps({
            'venue_id': result.venue_id,
            'name': result.name,
            'url_slug': result.url_slug,
        })

    return agent


def _get_agent() -> Agent[SupportDeps, BookingIntent]:
    global _booking_agent
    if _booking_agent is None:
        _booking_agent = _build_agent()
    return _booking_agent


# ---------------------------------------------------------------------------
# Django service
# ---------------------------------------------------------------------------

class ReservationAgentService:
    """
    Thin synchronous wrapper so Django views don't touch pydantic-ai directly.
    """

    @staticmethod
    def parse_intent(
        user_message: str,
        current_date: Date | None = None,
        lat: float = 40.7428,
        lon: float = -73.9712,
    ) -> BookingIntent:
        """
        Run the agent against *user_message* and return a validated BookingIntent.

        Args:
            user_message:  Raw natural-language request from the user.
            current_date:  Anchor for relative dates; defaults to today.
            lat / lon:     User's location for venue search; defaults to Manhattan.

        Raises:
            ResyAuthError:  Resy credentials missing or rejected.
            VenueNotFoundError: propagated if the LLM exhausts retries without a valid venue.
            pydantic_ai.exceptions.UnexpectedModelBehavior: LLM failed to produce a valid result.
        """
        from .resy_client import ResyAuthError, ResyClient
        from .services import ResyAuthService

        try:
            auth_token = ResyAuthService.get_auth_token()
        except ResyAuthError:
            raise

        deps = SupportDeps(
            current_date=current_date or Date.today(),
            resy_client=ResyClient(auth_token=auth_token),
            lat=lat,
            lon=lon,
        )
        log.debug(
            '[ReservationAgentService] run user_message=%r date=%s lat=%.4f lon=%.4f',
            user_message, deps.current_date, deps.lat, deps.lon,
        )
        result = _get_agent().run_sync(user_message, deps=deps)
        log.debug('[ReservationAgentService] intent=%s', result.output)
        return result.output
