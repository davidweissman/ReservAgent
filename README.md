# ReservAgent

A Django-based proof-of-concept that programmatically books a Resy reservation given a venue, date, party size, and preferred time.

## Setup

**1. Create and activate a virtual environment**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

**2. Install dependencies**

```bash
pip install django requests pydantic python-dotenv
```

**3. Configure credentials**

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `RESY_API_KEY` | Static Resy API key (starts with `VbWk…`) |
| `RESY_AUTH_TOKEN` | Your `x-resy-auth-token` from a logged-in Resy session |
| `USER_AGENT` | Browser user-agent string (optional — a default is provided) |

To find your auth token: open Resy in a browser, open DevTools → Network, make any request, and copy the `x-resy-auth-token` request header.

**4. Apply migrations**

```bash
python manage.py migrate
```

**5. Start the server**

```bash
python manage.py runserver
```

The API is available at `http://localhost:8000`.

## Usage

### `POST /api/book/`

Books the available slot closest to the requested time.

**Request body (JSON)**

| Field | Type | Required | Description |
|---|---|---|---|
| `venue_id` | int | yes | Resy venue ID |
| `date` | string | yes | `YYYY-MM-DD` |
| `party_size` | int | yes | 1–20 |
| `time` | string | yes | `HH:MM` (24-hour) |
| `time_delta` | int | no | Acceptable window in minutes around `time`. Omit to pick the globally closest slot. |

**Example**

```bash
curl -s -X POST http://localhost:8000/api/book/ \
  -H 'Content-Type: application/json' \
  -d '{
    "venue_id": 1234,
    "date": "2026-06-15",
    "party_size": 2,
    "time": "19:30",
    "time_delta": 30
  }'
```

**Success — `201 Created`**

```json
{
  "reservation_id": "12345678",
  "resy_token": "...",
  "confirmation_code": "RES-ABC123",
  "venue_id": 1234,
  "date": "2026-06-15",
  "party_size": 2,
  "booked_time": "19:15",
  "slot_type": "Dining Room"
}
```

**Errors**

| Status | Meaning |
|---|---|
| `400` | Missing or invalid request fields |
| `401` | Resy credentials invalid or expired |
| `422` | No matching slot found (e.g. outside `time_delta`) |
| `500` | Unexpected server error |

## Debugging

All Resy API calls are logged at `DEBUG` level. Logs go to:

- **stdout** of the `runserver` process
- **`debug.log`** in the project root (appended on every request)

```bash
tail -f debug.log
```

## Booking flow

```
POST /api/book/
  │
  ├── GET  api.resy.com/4/find        → available slots for venue/date/party
  ├── pick slot closest to requested time (within time_delta if set)
  ├── POST api.resy.com/3/details     → exchange config_id for book_token (JSON)
  └── POST api.resy.com/3/book        → finalise reservation (form-encoded)
```

Every attempt (success or failure) is recorded in the `BookingLog` table and visible in the Django admin at `/admin/`.
