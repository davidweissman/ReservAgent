"""
Resy venue search — HTTP I/O kept separate from the agent so it can be unit-tested
independently and reused outside of an agent context.

Deliberately has no imports from resy_client.py to avoid a circular dependency:
  resy_client  →  tools  (OK)
  tools        ↛  resy_client
"""

from __future__ import annotations

import logging

import requests
from pydantic import BaseModel

log = logging.getLogger(__name__)

_VENUE_SEARCH_URL = 'https://api.resy.com/3/venuesearch/search'


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------

class VenueSearchResult(BaseModel):
    venue_id: int
    name: str
    locality: str = ""
    url_slug: str


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class VenueNotFoundError(Exception):
    """Raised by call_venue_search when the API returns zero hits."""


# ---------------------------------------------------------------------------
# HTTP function
# ---------------------------------------------------------------------------

def call_venue_search(
    session: requests.Session,
    query: str,
    lat: float,
    lon: float,
    per_page: int = 5,
) -> VenueSearchResult:
    """
    POST /3/venuesearch/search and return the top hit.

    Args:
        session:   An authenticated requests.Session (built by ResyClient).
        query:     Restaurant name or free-text search string.
        lat/lon:   Geographic centre for the search (default: Manhattan).
        per_page:  Maximum results to request from Resy.

    Returns:
        VenueSearchResult for the first hit.

    Raises:
        VenueNotFoundError:       API returned zero hits.
        requests.HTTPError:       Non-2xx response from Resy (caller converts to ResyError).
    """
    payload = {
        'geo': {'latitude': lat, 'longitude': lon},
        'per_page': per_page,
        'query': query,
        'types': ['venue'],
    }
    log.debug(
        '[call_venue_search] POST /3/venuesearch/search query=%r lat=%.4f lon=%.4f',
        query, lat, lon,
    )
    resp = session.post(_VENUE_SEARCH_URL, json=payload)
    log.debug('[call_venue_search] status=%s body=%s', resp.status_code, resp.text[:500])

    # Let the caller (ResyClient.search_venues) translate this into ResyError.
    resp.raise_for_status()

    hits = resp.json().get('search', {}).get('hits', [])
    if not hits:
        log.debug('[call_venue_search] no hits for query=%r', query)
        raise VenueNotFoundError(f"No restaurant found for '{query}'.")

    top = hits[0]
    venue_id = top.get('id', {}).get('resy')
    name = top.get('name', '')
    locality = top.get('locality', '')
    url_slug = top.get('url_slug', '')

    if venue_id is None:
        raise ValueError(f'[call_venue_search] missing id.resy in hit: {top}')

    log.debug('[call_venue_search] top hit venue_id=%d name=%r', venue_id, name)
    return VenueSearchResult(venue_id=venue_id, name=name, locality=locality, url_slug=url_slug)
