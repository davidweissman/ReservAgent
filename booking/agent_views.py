"""
Agent-backed API views.

POST /api/natural-booking/  — parse a natural-language reservation request.
POST /api/test/search/      — resolve a venue name to Resy IDs without booking.
"""

from __future__ import annotations

import logging
from datetime import date as Date

from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .agent import ReservationAgentService
from .models import BookingLog
from .resy_client import ResyAuthError, ResyClient, ResyError, pick_closest_slot
from .services import ResyAuthService
from .tools import VenueNotFoundError

log = logging.getLogger(__name__)

_DEFAULT_LAT = 40.7428  # Manhattan
_DEFAULT_LON = -73.9712


# ---------------------------------------------------------------------------
# Serializers (used for schema generation and request validation)
# ---------------------------------------------------------------------------

class NaturalBookingRequestSerializer(serializers.Serializer):
    prompt = serializers.CharField(help_text="Natural-language reservation request")
    current_date = serializers.DateField(required=False, help_text="Anchor date for relative expressions (YYYY-MM-DD); defaults to today")
    lat = serializers.FloatField(required=False, default=_DEFAULT_LAT, help_text="Venue search latitude")
    lon = serializers.FloatField(required=False, default=_DEFAULT_LON, help_text="Venue search longitude")


class NaturalBookingResponseSerializer(serializers.Serializer):
    restaurant_name = serializers.CharField()
    venue_id = serializers.IntegerField()
    date = serializers.DateField()
    time = serializers.TimeField(allow_null=True)
    party_size = serializers.IntegerField()


class VenueSearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(help_text="Restaurant name or free-text search string")
    lat = serializers.FloatField(required=False, default=_DEFAULT_LAT, help_text="Search latitude")
    lon = serializers.FloatField(required=False, default=_DEFAULT_LON, help_text="Search longitude")


class VenueSearchResponseSerializer(serializers.Serializer):
    venue_id = serializers.IntegerField()
    name = serializers.CharField()
    locality = serializers.CharField()
    url_slug = serializers.CharField()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@extend_schema(
    request=NaturalBookingRequestSerializer,
    responses={200: NaturalBookingResponseSerializer},
    examples=[
        OpenApiExample(
            "4 Charles tomorrow at 7pm",
            value={"prompt": "Table for 2 at 4 Charles tomorrow at 7pm"},
            request_only=True,
        ),
    ],
    summary="Parse a natural-language reservation request",
    description=(
        "Accepts a free-text reservation request. The agent searches Resy for the "
        "named venue, then extracts date, time, and party size from the message. "
        "Returns a structured BookingIntent ready to pass to POST /api/book/."
    ),
)
class ParseIntentView(APIView):
    def post(self, request):
        serializer = NaturalBookingRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        message = data['prompt']
        current_date: Date | None = data.get('current_date')
        lat: float = data.get('lat', _DEFAULT_LAT)
        lon: float = data.get('lon', _DEFAULT_LON)

        try:
            intent = ReservationAgentService.parse_intent(message, current_date, lat, lon)
        except Exception as exc:
            log.exception('Agent failed to parse intent for message=%r', message)
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(intent.model_dump(mode='json'))


@extend_schema(
    request=VenueSearchRequestSerializer,
    responses={200: VenueSearchResponseSerializer},
    examples=[
        OpenApiExample(
            "Search for Carbone",
            value={"query": "Carbone"},
            request_only=True,
        ),
    ],
    summary="Search for a Resy venue by name",
    description=(
        "Calls the Resy venue-search API directly and returns the top match. "
        "Use this to verify that the agent is resolving the correct restaurant "
        "before triggering a full booking."
    ),
)
class TestSearchView(APIView):
    def post(self, request):
        serializer = VenueSearchRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        query: str = data['query']
        lat: float = data.get('lat', _DEFAULT_LAT)
        lon: float = data.get('lon', _DEFAULT_LON)

        try:
            auth_token = ResyAuthService.get_auth_token()
            client = ResyClient(auth_token=auth_token)
            result = client.search_venues(query, lat, lon)
        except VenueNotFoundError:
            return Response({'error': f'No venue found for query: {query!r}'}, status=status.HTTP_404_NOT_FOUND)
        except ResyAuthError as exc:
            log.error('Resy auth error during venue search: %s', exc)
            return Response({'error': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as exc:
            log.exception('Venue search failed for query=%r', query)
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(VenueSearchResponseSerializer(result).data)


# ---------------------------------------------------------------------------
# Natural-language booking (parse intent → book in one shot)
# ---------------------------------------------------------------------------

class NaturalBookRequestSerializer(serializers.Serializer):
    prompt = serializers.CharField(help_text="Natural-language reservation request")
    current_date = serializers.DateField(required=False, help_text="Anchor date for relative expressions (YYYY-MM-DD); defaults to today")
    lat = serializers.FloatField(required=False, default=_DEFAULT_LAT, help_text="Venue search latitude")
    lon = serializers.FloatField(required=False, default=_DEFAULT_LON, help_text="Venue search longitude")
    time_delta = serializers.IntegerField(required=False, min_value=0, help_text="Acceptable window around requested time in minutes; omit to allow any slot")


class BookingConfirmationSerializer(serializers.Serializer):
    reservation_id = serializers.CharField()
    resy_token = serializers.CharField()
    confirmation_code = serializers.CharField()
    venue_id = serializers.IntegerField()
    restaurant_name = serializers.CharField()
    date = serializers.DateField()
    party_size = serializers.IntegerField()
    booked_time = serializers.TimeField(format='%H:%M')
    slot_type = serializers.CharField()


@extend_schema(
    request=NaturalBookRequestSerializer,
    responses={201: BookingConfirmationSerializer},
    examples=[
        OpenApiExample(
            "Book 4 Charles tomorrow at 7pm",
            value={"prompt": "Table for 2 at 4 Charles tomorrow at 7pm"},
            request_only=True,
        ),
    ],
    summary="Parse a natural-language request and book the reservation",
    description=(
        "End-to-end endpoint: the agent resolves the venue and extracts intent "
        "from the prompt, then immediately executes the three-step Resy booking "
        "flow (find slots → get book token → complete booking). "
        "The prompt must include a time; requests without one are rejected with 400."
    ),
)
class NaturalBookView(APIView):
    def post(self, request):
        serializer = NaturalBookRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        time_delta: int | None = data.get('time_delta')

        # Step 1 — resolve intent from natural language
        try:
            intent = ReservationAgentService.parse_intent(
                data['prompt'],
                data.get('current_date'),
                data.get('lat', _DEFAULT_LAT),
                data.get('lon', _DEFAULT_LON),
            )
        except Exception as exc:
            log.exception('Agent failed to parse intent for prompt=%r', data['prompt'])
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if intent.time is None:
            return Response(
                {'error': 'Could not determine a reservation time from your request. Please specify a time.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log_entry = BookingLog(
            venue_id=intent.venue_id,
            date=intent.date,
            party_size=intent.party_size,
            requested_time=intent.time,
            time_delta_minutes=time_delta,
            status=BookingLog.Status.FAILURE,
        )

        # Step 2 — execute the Resy booking flow
        try:
            auth_token = ResyAuthService.get_auth_token()
            client = ResyClient(auth_token=auth_token)
            slots = client.find_slots(intent.venue_id, intent.party_size, intent.date)
            chosen = pick_closest_slot(slots, intent.time, time_delta)
            book_token = client.get_book_token(chosen.config_id, intent.party_size, intent.date)
            confirmation = client.complete_booking(book_token)

            reservation_id = str(
                confirmation.get('reservation_id')
                or confirmation.get('resy_token')
                or confirmation.get('token')
                or ''
            )
            resy_token = confirmation.get('resy_token', '')
            confirmation_code = (
                confirmation.get('confirmation_code')
                or confirmation.get('code')
                or reservation_id
            )

            log_entry.booked_time = chosen.start_time
            log_entry.reservation_id = reservation_id
            log_entry.resy_token = resy_token
            log_entry.status = BookingLog.Status.SUCCESS
            log_entry.save()

            return Response(
                {
                    'reservation_id': reservation_id,
                    'resy_token': resy_token,
                    'confirmation_code': confirmation_code,
                    'venue_id': intent.venue_id,
                    'restaurant_name': intent.restaurant_name,
                    'date': intent.date.isoformat(),
                    'party_size': intent.party_size,
                    'booked_time': chosen.start_time.strftime('%H:%M'),
                    'slot_type': chosen.type,
                },
                status=status.HTTP_201_CREATED,
            )

        except ResyAuthError as exc:
            log.error('Resy auth error: %s', exc)
            log_entry.error_message = str(exc)
            log_entry.save()
            return Response({'error': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        except ResyError as exc:
            log.error('Resy booking error: %s', exc)
            log_entry.error_message = str(exc)
            log_entry.save()
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        except Exception as exc:
            log.exception('Unexpected error during natural booking')
            log_entry.error_message = str(exc)
            log_entry.save()
            return Response({'error': 'An unexpected error occurred.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------------------------------
# Auth test endpoint
# ---------------------------------------------------------------------------

class AuthTestResponseSerializer(serializers.Serializer):
    token = serializers.CharField(help_text="Masked token confirming successful authentication and caching")


@extend_schema(
    request=None,
    responses={200: AuthTestResponseSerializer},
    summary="Test Resy authentication and token caching",
    description=(
        "Calls ResyAuthService.get_auth_token() using RESY_EMAIL and RESY_PASSWORD "
        "from the environment. On success, returns a masked token string confirming "
        "the token was extracted and cached for 2 hours. The actual token value is "
        "never returned in full."
    ),
)
class AuthTestLoginView(APIView):
    def post(self, request):
        try:
            token = ResyAuthService.get_auth_token()
        except ResyAuthError as exc:
            log.error('[AuthTestLoginView] auth failed: %s', exc)
            return Response({'error': str(exc)}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as exc:
            log.exception('[AuthTestLoginView] unexpected error')
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'token': f'token_extracted_and_cached_{token[:8]}...'})
