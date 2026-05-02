"""
ResyAuthService — fetch and cache a Resy authentication token.

Flow:
  1. Check Django's cache for key ``resy_token_system``.
  2. On miss: POST /3/auth/password with env-configured email + password.
  3. Cache the returned token for 2 hours and return it.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .resy_client import ResyAuthError

log = logging.getLogger(__name__)

_AUTH_URL = 'https://api.resy.com/3/auth/password'
CACHE_KEY = 'resy_token_system'
_CACHE_TTL = 7200  # 2 hours


class ResyAuthService:
    @staticmethod
    def get_auth_token() -> str:
        """
        Return a valid Resy auth token, fetching and caching one if needed.

        Raises:
            ResyAuthError: Credentials missing or rejected by Resy.
        """
        cached = cache.get(CACHE_KEY)
        if cached:
            log.debug('[ResyAuthService] cache hit')
            return cached

        email = settings.RESY_EMAIL
        password = settings.RESY_PASSWORD
        api_key = settings.RESY_API_KEY

        if not email or not password:
            raise ResyAuthError('RESY_EMAIL and RESY_PASSWORD must be set in the environment.')
        if not api_key:
            raise ResyAuthError('RESY_API_KEY must be set in the environment.')

        universal_auth = settings.RESY_UNIVERSAL_AUTH
        if not universal_auth:
            raise ResyAuthError(
                'RESY_UNIVERSAL_AUTH must be set to the static Resy guest token '
                '(capture from X-Resy-Universal-Auth on api.resy.com while logged out).'
            )

        log.debug('[ResyAuthService] cache miss — authenticating with Resy')
        resp = requests.post(
            _AUTH_URL,
            data={'email': email, 'password': password},
            headers={
                'Authorization': f'ResyAPI api_key="{api_key}"',
                'X-Resy-Universal-Auth': universal_auth,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json, text/plain, */*',
                'User-Agent': settings.RESY_USER_AGENT,
                'Origin': 'https://resy.com',
                'Referer': 'https://resy.com/',
            },
            timeout=10,
        )
        log.debug('[ResyAuthService] outbound headers=%s', dict(resp.request.headers))
        log.debug('[ResyAuthService] POST /3/auth/password status=%s body=%s', resp.status_code, resp.text[:300])

        if resp.status_code == 401:
            raise ResyAuthError('Resy authentication failed — check RESY_EMAIL and RESY_PASSWORD.')
        if not resp.ok:
            raise ResyAuthError(f'Resy auth API error {resp.status_code}: {resp.text[:200]}')

        token = resp.json().get('token', '')
        if not token:
            raise ResyAuthError(f'No token in Resy auth response. Body: {resp.text[:200]}')

        cache.set(CACHE_KEY, token, _CACHE_TTL)
        log.debug('[ResyAuthService] token cached for %d seconds', _CACHE_TTL)
        return token
