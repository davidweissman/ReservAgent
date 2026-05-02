import json
import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError

from .models import BookingLog
from .resy_client import ResyClient, ResyAuthError, ResyError, pick_closest_slot
from .schemas import BookingRequest

log = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class BookReservationView(View):
    """
    POST /api/book/

    Body (JSON):
        venue_id       int       required
        date           YYYY-MM-DD  required
        party_size     int       required
        time           HH:MM     required
        time_delta     int       optional — minutes window around requested time
    """

    def post(self, request):
        # --- Parse & validate input ---
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON body.'}, status=400)

        try:
            req = BookingRequest.model_validate(body)
        except ValidationError as exc:
            return JsonResponse({'error': exc.errors()}, status=400)

        log_entry = BookingLog(
            venue_id=req.venue_id,
            date=req.date,
            party_size=req.party_size,
            requested_time=req.time,
            time_delta_minutes=req.time_delta,
            status=BookingLog.Status.FAILURE,
        )

        try:
            client = ResyClient()

            # Step 1 — find available slots
            slots = client.find_slots(req.venue_id, req.party_size, req.date)

            # Pick the best slot
            chosen = pick_closest_slot(slots, req.time, req.time_delta)

            # Step 2 — get a book token for the chosen slot
            book_token = client.get_book_token(chosen.config_id, req.party_size, req.date)

            # Step 3 — complete the booking
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

            return JsonResponse(
                {
                    'reservation_id': reservation_id,
                    'resy_token': resy_token,
                    'confirmation_code': confirmation_code,
                    'venue_id': req.venue_id,
                    'date': req.date.isoformat(),
                    'party_size': req.party_size,
                    'booked_time': chosen.start_time.strftime('%H:%M'),
                    'slot_type': chosen.type,
                },
                status=201,
            )

        except ResyAuthError as exc:
            log.error('Resy auth error: %s', exc)
            log_entry.error_message = str(exc)
            log_entry.save()
            return JsonResponse({'error': str(exc)}, status=401)

        except ResyError as exc:
            log.error('Resy booking error: %s', exc)
            log_entry.error_message = str(exc)
            log_entry.save()
            return JsonResponse({'error': str(exc)}, status=422)

        except Exception as exc:
            log.exception('Unexpected error during booking')
            log_entry.error_message = str(exc)
            log_entry.save()
            return JsonResponse({'error': 'An unexpected error occurred.'}, status=500)
