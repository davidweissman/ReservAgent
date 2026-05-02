# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands assume the virtualenv is active (`source .venv/bin/activate`) or prefixed with `.venv/bin/`.

```bash
# Run the dev server
python manage.py runserver

# Apply migrations after model changes
python manage.py makemigrations && python manage.py migrate

# Django system check (catches config errors without starting the server)
python manage.py check

# Run tests
python manage.py test booking
```

Credentials must be present in `.env` (copied from `.env.example`) before the server will start. The `ResyClient.__init__` raises `ResyAuthError` immediately if `RESY_API_KEY` or `RESY_AUTH_TOKEN` are empty.

## Architecture

The project is a single Django app (`booking`) with no frontend. The only user-facing surface is `POST /api/book/`.

### Request lifecycle

```
views.BookReservationView.post()
  → schemas.BookingRequest  (Pydantic validation)
  → resy_client.ResyClient.find_slots()     GET  /4/find
  → resy_client.pick_closest_slot()         pure function, no I/O
  → resy_client.ResyClient.get_book_token() POST /3/details  (JSON body)
  → resy_client.ResyClient.complete_booking() POST /3/book   (form-encoded body)
  → BookingLog.save()                       audit record written on success OR failure
```

### Key design notes

- **`/3/details` uses `json=`, `/3/book` uses `data=`** — this asymmetry is intentional and confirmed working. Do not change either to match the other.
- `BookingLog` is append-only; every attempt (success and failure) is persisted before the response is returned.
- CSRF middleware is intentionally removed from `MIDDLEWARE` — the endpoint accepts JSON from API clients.
- `pick_closest_slot` is a pure function: when `time_delta` is `None` it considers all slots; when set, it hard-filters before picking the closest.

### Credentials flow

`.env` → `load_dotenv()` in `settings.py` → `settings.RESY_API_KEY / RESY_AUTH_TOKEN / RESY_USER_AGENT` → read once in `ResyClient.__init__`.

### Logging

The `booking` logger writes `DEBUG` and above to both stdout and `debug.log` in the project root. All three Resy API calls log their full request payload and the first 500–800 chars of the response body. `debug.log` is gitignored via the `*.log` rule.
