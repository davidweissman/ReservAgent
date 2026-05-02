"""
Thin wrapper around the api.resy.com endpoints used for booking.

Three-step flow:
  1. GET  /4/find     — list available slots
  2. POST /3/details  — exchange config_id for a book_token  (JSON body)
  3. POST /3/book     — finalise the reservation              (form-encoded body)
"""

from __future__ import annotations

import logging
from datetime import date, time, datetime
from typing import Optional

import requests
from django.conf import settings

from .schemas import SlotInfo

log = logging.getLogger(__name__)

RESY_BASE = 'https://api.resy.com'


class ResyError(Exception):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class ResyAuthError(ResyError):
    """Raised when Resy rejects credentials."""


class ResyClient:
    def __init__(self):
        api_key = settings.RESY_API_KEY
        auth_token = settings.RESY_AUTH_TOKEN

        if not api_key or not auth_token:
            raise ResyAuthError(
                'RESY_API_KEY and RESY_AUTH_TOKEN must be set in the environment.'
            )

        self._session = requests.Session()
        self._session.headers.update({
            'Authorization': f'ResyAPI api_key="{api_key}"',
            'X-Resy-Auth-Token': auth_token,
            'User-Agent': settings.RESY_USER_AGENT,
            'Accept': 'application/json, text/plain, */*',
            'Origin': 'https://resy.com',
            'Referer': 'https://resy.com/',
        })

    def find_slots(self, venue_id: int, party_size: int, reservation_date: date) -> list[SlotInfo]:
        params = {
            'lat': 0,
            'long': 0,
            'day': reservation_date.strftime('%Y-%m-%d'),
            'party_size': party_size,
            'venue_id': venue_id,
        }
        log.debug('[find_slots] GET /4/find params=%s', params)
        resp = self._session.get(f'{RESY_BASE}/4/find', params=params)
        log.debug('[find_slots] status=%s body=%s', resp.status_code, resp.text[:500])
        self._raise_for_resy_error(resp)

        venues = resp.json().get('results', {}).get('venues', [])
        if not venues:
            log.debug('[find_slots] no venues in response')
            return []

        raw_slots = venues[0].get('slots', [])
        log.debug('[find_slots] raw slot count=%d', len(raw_slots))

        slots: list[SlotInfo] = []
        for slot in raw_slots:
            config = slot.get('config', {})
            config_id = config.get('token', '')
            date_str = slot.get('date', {}).get('start', '')

            if not date_str or not config_id:
                log.debug('[find_slots] skipping incomplete slot: %s', slot)
                continue

            try:
                start_dt = datetime.fromisoformat(date_str)
                slots.append(SlotInfo(
                    config_id=config_id,
                    start_time=start_dt.time().replace(second=0, microsecond=0),
                    type=config.get('type', ''),
                ))
                log.debug('[find_slots] slot time=%s config_id=%.20s', start_dt.time(), config_id)
            except ValueError:
                log.warning('[find_slots] could not parse slot date: %s', date_str)

        log.debug('[find_slots] returning %d usable slots', len(slots))
        return slots

    def get_book_token(self, config_id: str, party_size: int, reservation_date: date) -> str:
        payload = {
            'commit': 1,
            'config_id': config_id,
            'day': reservation_date.strftime('%Y-%m-%d'),
            'party_size': party_size,
        }
        log.debug('[get_book_token] POST /3/details payload=%s', payload)
        resp = self._session.post(f'{RESY_BASE}/3/details', json=payload)
        log.debug('[get_book_token] status=%s body=%s', resp.status_code, resp.text[:800])
        self._raise_for_resy_error(resp)

        body = resp.json()
        book_token = body.get('book_token', {}).get('value', '')
        log.debug('[get_book_token] token present=%s prefix=%.20s', bool(book_token), book_token or 'N/A')

        if not book_token:
            raise ResyError(f'No book_token in /3/details response. Body: {body}')
        return book_token

    def complete_booking(self, book_token: str, source_id: str = 'resy.com-venue-venue') -> dict:
        payload = {
            'book_token': book_token,
            'source_id': source_id,
            'struct_payment_method': '{"id":23521524}',
            'charge_payment_method': False,
        }
        log.debug('[complete_booking] POST /3/book token_prefix=%.20s', book_token)
        resp = self._session.post(f'{RESY_BASE}/3/book', data=payload)
        log.debug('[complete_booking] status=%s body=%s', resp.status_code, resp.text[:800])
        self._raise_for_resy_error(resp)
        return resp.json()

    @staticmethod
    def _raise_for_resy_error(resp: requests.Response) -> None:
        if resp.status_code == 401:
            raise ResyAuthError('Resy authentication failed — check RESY_AUTH_TOKEN.', status_code=401)
        if resp.status_code == 403:
            raise ResyAuthError('Resy access forbidden — credentials may be expired.', status_code=403)
        if not resp.ok:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise ResyError(f'Resy API error {resp.status_code}: {detail}', status_code=resp.status_code)


def pick_closest_slot(slots: list[SlotInfo], target: time, time_delta_minutes: Optional[int]) -> SlotInfo:
    if not slots:
        raise ResyError('No available slots found for the requested venue/date/party.')

    def mins(t: time) -> int:
        return t.hour * 60 + t.minute

    target_min = mins(target)
    candidates = slots

    if time_delta_minutes is not None:
        candidates = [s for s in slots if abs(mins(s.start_time) - target_min) <= time_delta_minutes]
        log.debug('[pick_closest_slot] %d/%d slots within %d min of %s', len(candidates), len(slots), time_delta_minutes, target)

    if not candidates:
        raise ResyError(f'No slots found within {time_delta_minutes} minutes of {target.strftime("%H:%M")}.')

    chosen = min(candidates, key=lambda s: abs(mins(s.start_time) - target_min))
    log.debug('[pick_closest_slot] chose time=%s config_id=%.20s', chosen.start_time, chosen.config_id)
    return chosen
