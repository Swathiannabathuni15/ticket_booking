# Ticket Booking API

A backend where **a seat can never be sold twice**, even when hundreds of people
click "book" at the same moment.

Built with Python, FastAPI, PostgreSQL and SQLAlchemy. Free to run locally.

## Why this project exists

The hard part of a booking system is not the CRUD. It is the **race condition**:

1. User A checks seat A1 -> free
2. User B checks seat A1 -> free (A has not saved yet)
3. Both save -> seat sold twice

This project stops it with two layers:

| Layer | What | Where |
|---|---|---|
| 1. Row lock | `SELECT ... FOR UPDATE` makes the second request wait, then see the seat is taken | `app/services/booking_service.py` |
| 2. DB constraint | Partial unique index: one active (`pending_payment` or `confirmed`) booking per seat. Postgres refuses a second one | `app/models.py` |

**Proof:** `tests/test_concurrency.py` sends 200 parallel booking requests for one
seat and asserts exactly **1 wins and 199 lose**. In an experiment with both layers
removed, 7 of 200 requests "won" the same seat.

## Project structure

```
app/
  main.py                  App start, registers routers, creates tables
  config.py                Settings from environment variables
  db.py                    Database engine and per-request session
  redis_client.py          Redis connection pool for request-time use (holds, rate limiting)
  models.py                Tables: users, events, seats, bookings (+ the unique index)
  schemas.py               Request/response shapes and validation
  security.py              Password hashing (bcrypt) and JWT tokens
  deps.py                  "Who is the logged-in user?" / "are they an admin?" dependencies
  rate_limit_deps.py       FastAPI dependencies: per-IP login limit, per-user hold/booking limits
  routers/                 HTTP layer: thin, only translates errors to status codes
    auth.py  events.py  bookings.py  holds.py  payments.py
  services/
    booking_service.py     Business logic: the locking lives here
    payment_service.py     Pay a booking, or expire one that never got paid
    hold_service.py        Redis seat holds: SET NX EX to acquire, a Lua script to release
    rate_limit.py           Fixed-window counter: check() increments and raises past the limit
  jobs/                    Background worker (RQ): expires unpaid bookings
    queue.py  tasks.py  worker.py  scheduler_loop.py
tests/
  test_api.py              End-to-end API behaviour
  test_concurrency.py      The race-condition proof (booking)
  test_holds.py            Seat-hold behaviour, including a 100-way race and Redis-down
  test_rate_limit.py       429 + Retry-After, per-user scoping, fail-open on Redis-down
  test_admin.py            Only admins can create events; 401 vs. 403 distinction
  test_payments.py         Double payment, payment retry, expiry, the real RQ worker
scripts/
  make_admin.py            Promote an existing user to admin (see "Admin role" below)
migrations/                Alembic: schema history (see "Migrations" below)
alembic.ini                Alembic config - no DB URL here; it reads app.config.settings
```

Request flow: `router` (HTTP) -> `service` (rules + transaction) -> `models` (database).

## Run it (PyCharm or terminal)

1. Start the database (needs Docker Desktop, free):
   ```
   docker compose up -d
   ```
2. Create a virtual environment and install packages:
   ```
   python -m venv .venv
   .venv\Scripts\activate        # Windows   (Mac/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   ```
3. Apply migrations (creates the tables - see "Migrations" below):
   ```
   alembic upgrade head
   ```
4. Start the API:
   ```
   uvicorn app.main:app --reload
   ```
5. Open http://localhost:8000/docs and try the endpoints in the browser.
6. (Optional, only needed for unpaid bookings to actually expire) start the
   background worker and its scheduler, each in its own terminal:
   ```
   python -m app.jobs.worker
   python -m app.jobs.scheduler_loop
   ```
   See "Payments and the expiry worker" below for what these do.

## Migrations

The app no longer creates tables on startup (`Base.metadata.create_all` is gone
from `app/main.py`). Schema changes go through Alembic, in `migrations/`.

**Apply the latest schema** (do this after cloning, and after every `git pull`
that touches `app/models.py`):
```
alembic upgrade head
```

**After changing a model**, generate a migration from the diff and read it
before applying - autogenerate is a draft, not a guarantee:
```
alembic revision --autogenerate -m "describe the change"
# open migrations/versions/<new file>.py and check it actually says what you
# meant (new columns get the right nullability/default, renames vs
# drop+add look the way you intended, any raw SQL you need is still manual)
alembic upgrade head
```

**Undo the last migration** (useful while iterating locally):
```
alembic downgrade -1
```

**Where the DB URL comes from:** `migrations/env.py` imports `app.config.settings`
and uses `settings.database_url` - the same `DATABASE_URL` / `.env` the app itself
reads. There is no second URL to keep in sync in `alembic.ini`.

**Tests build their schema through these same migrations** (see `tests/
conftest.py`'s `_schema` fixture: `alembic upgrade head` / `downgrade base`,
not `create_all`/`drop_all`). That's deliberate: it's what actually proves the
migration produces the same schema the models describe, rather than just
trusting that it does.

## Run the tests

```
pytest -v
```
Tests use a separate `ticketdb_test` database (created by docker compose), so your
real data is never touched.

## API

| Method | Path | What |
|---|---|---|
| POST | `/auth/signup` | Create account (rate-limited per IP) |
| POST | `/auth/login` | Get a JWT token (rate-limited per IP) |
| GET | `/me` | Current user |
| POST | `/events` | Create event with a seat grid (**admin only**) |
| GET | `/events` | List events (paginated) |
| GET | `/events/{id}/seats` | Seats with `available` true/false |
| POST | `/holds` | Hold a seat for 5 minutes. Body: `{"seat_id": n}` |
| DELETE | `/holds/{seat_id}` | Release your own hold early |
| POST | `/bookings` | Book a held seat -> `pending_payment`. Optional `Idempotency-Key` header |
| GET | `/bookings/me` | My bookings |
| DELETE | `/bookings/{id}` | Cancel (owner only) |
| POST | `/payments` | Confirm payment for a booking -> `confirmed`. Requires `Idempotency-Key` |

Status codes to know: `201` booked/held/paid, `200` retry returned the original
booking/payment, `409` seat already taken or already held by someone else, or a
booking that's already paid / cancelled / expired, `401` not logged in,
`403` not your booking, or not an admin where one is required, `429` rate limited
(see `Retry-After` header), `503` Redis is down and holds can't be trusted right now.

## Admin role

`/events` creation requires `users.is_admin`. There's no HTTP endpoint that grants
it - the only way to become an admin is `scripts/make_admin.py`, run with shell
access to the server/DB:
```
python scripts/make_admin.py someone@example.com
```
That person must already have an account (signed up with their own password via
`POST /auth/signup`); the script only flips the flag. This is deliberate: no admin
credential, default password, or bootstrap secret lives anywhere in the codebase
or an env var. Once there's one admin, they can promote others the same way (a
future stage could add an admin-only HTTP endpoint for that, instead of needing
shell access every time).

## Idempotency (safe retries)

Send `Idempotency-Key: some-unique-string` with a booking or a payment. If the
network drops and the client retries, they get the **same booking or payment
back** instead of an error or a duplicate. Booking and payment each have their
own idempotency key (a booking's key doesn't cover its later payment) - see
"Payments and the expiry worker" below.

## Payments and the expiry worker

Booking is two steps, same as a real checkout:

1. `POST /bookings` creates the booking as `pending_payment`. The seat is held
   against everyone else from this point (the partial unique index covers
   `pending_payment` too, not just `confirmed`) - but nobody has paid yet.
2. `POST /payments {"booking_id": n}` with an `Idempotency-Key` flips it to
   `confirmed`.

**If nobody pays:** a booking left in `pending_payment` past its
`payment_due_at` (`created_at` + `payment_due_minutes`, default 10) is cancelled
by a background job, freeing the seat for someone else. This needs two
processes, both using **RQ** (Redis Queue) - Redis is already a dependency, so
no new infrastructure:
```
python -m app.jobs.scheduler_loop   # every 30s, enqueues the cleanup job
python -m app.jobs.worker           # pulls jobs off the queue and runs them
```
Splitting these matters: the scheduler is a trigger (just "do this periodically"),
the worker is where the work happens and is the part you'd scale to several
processes under load. A single script doing both would conflate "when" with
"how many are doing it."

**Two different double-charge problems, two different fixes:**
- *Retry* (same request sent twice - double click, dropped response): the
  `Idempotency-Key` makes `POST /payments` return the *same* payment both
  times. No new row, no error.
- *Genuinely paying twice* (a second payment attempt with a different key,
  because the caller doesn't realize the first one went through): rejected
  with `409`, because `payments.booking_id` is `UNIQUE` - the same "app check
  plus a DB constraint behind it" pattern `booking_service` already uses for
  double-booking, applied to double-paying.

## Try breaking it yourself (best way to learn)

1. In `booking_service.py`, delete `.with_for_update()` and run the concurrency test.
   It still passes, because Layer 2 (the index) catches it.
2. Also remove the `Index(...)` in `models.py` (drop the test database tables first so the
   old index is gone). Now the test will likely fail with several winners. Race conditions
   depend on timing, so run it a few times. Adding a tiny `time.sleep(0.01)` between the
   check and the insert makes the failure appear almost every time.
3. Put the lock and index back. Understand why each layer matters.

## Week 2: seat holds and rate limiting (Redis)

### Why Redis for holds, and what the TTL replaces

Without a hold, a user can fill in payment details for a seat that someone else
books out from under them before they finish. The fix needs two things: a way to
say "this seat is mine for the next few minutes," and a way to make that claim
expire automatically if the user abandons checkout. Postgres can do the first
(a row with an `expires_at` column) but not the second without extra
machinery - something still has to notice the row expired and act on it, which
means either a poller or a cron job.

Redis's `SET key value NX EX 300` does both in one atomic command: `NX` makes the
*acquire* race-safe (only one caller can create the key), and `EX` makes the
*expiry* automatic - the key simply vanishes from Redis after 300 seconds, with
no poller, cron job, or sweep required. The TTL is replacing what would
otherwise be a background job that scans a table for stale rows and deletes
them. For this project's scale, "let Redis forget the key" is strictly simpler
and just as correct.

Releasing a hold (`DELETE /holds/{seat_id}`) needs an extra atomicity guarantee
that `SET NX` doesn't give for free: "delete this key, but only if it's still
mine." A plain `GET` then `DEL` is two round trips with a gap in between, during
which someone else could legitimately acquire the seat - and the original
caller's `DEL` would then destroy *their* hold. `release_hold` in
`app/services/hold_service.py` closes that gap with a small Lua script, which
Redis runs as a single atomic step on the server: check ownership and delete in
one operation, nothing can interleave in the middle.

Postgres is still the real guard against double-booking (`SELECT ... FOR UPDATE`
plus the partial unique index from Week 1). A hold is a courtesy that stops
*most* conflicts before they reach the database at all; it is not itself a
safety mechanism, which is why losing Redis does not risk a double sale - it
just risks a 503 on `/holds`.

### Fail-open vs. fail-closed, and why holds and rate limits disagree

- **Holds fail *closed*.** If Redis is unreachable, `/holds` and `/bookings`
  return `503` rather than silently letting a booking through without a valid
  hold. The cost of failing open here is a double booking (or at least
  bypassing the UX that holds exist to provide); the cost of failing closed is
  temporary unavailability. For a ticketing system, a double sale is worse than
  a short outage, so holds fail closed.
- **Rate limiting fails *open*.** If Redis is unreachable, `app/services/
  rate_limit.py` logs a warning and lets the request through uncounted. The
  rate limiter exists to protect the system from abuse; it must not become a
  second way for the *entire API* to go down whenever Redis has a bad moment.
  The cost of failing open is a little extra, unmetered traffic for as long as
  Redis is down - acceptable, because the alternative (every login and booking
  call also returning 503) is strictly worse.

The same dependency, `get_redis`, is used in both places; the difference is
entirely in how each caller reacts to a `RedisError` - that choice has to be
made per feature, not once for the whole app.

### The fixed-window weakness

The rate limiter counts hits in buckets of wall-clock time: `INCR` a counter
keyed by `action:identity:window_number`, and let Redis expire it when the
window ends. It's cheap (one round trip) but it admits a burst at the window
boundary: a client that sends `limit` requests at `0:59` and another `limit` at
`1:00` gets `2x limit` through in under two seconds, because those two bursts
land in different windows and neither one, on its own, looks like abuse. A
sliding-window log or a token bucket avoids this, at the cost of more Redis
round trips (sliding window) or a Lua script (token bucket) - for login and
booking traffic, an occasional 2x burst at a window edge is an acceptable
trade for the simplicity of a single `INCR`. If this project needed to defend
against a determined attacker rather than normal client retries, a token
bucket would be the next step.

## Known limits (be honest about these in interviews)

- Admin promotion only works via a script with DB access (`scripts/make_admin.py`)
  - there's no HTTP endpoint for an admin to promote someone else yet.
- Fixed-window rate limiting has the boundary-burst weakness described above.
- A hold's existence is never surfaced anywhere durable (Postgres, logs) -
  if Redis loses the key for any reason other than expiry (a flush, an eviction
  under memory pressure), there is no record it ever happened. Fine for a
  convenience layer backed by a real DB constraint; would need an audit trail
  for anything compliance-sensitive.
- Payments are fake: there is no real provider, no card details, no actual
  money. `POST /payments` only flips a status - it's the state machine and
  the double-charge protections, not a payment integration.
- Unpaid-booking expiry only happens if `app.jobs.worker` and
  `app.jobs.scheduler_loop` are actually running. If they're not, a
  `pending_payment` booking just sits there past its due time - the seat
  stays locked until someone cancels it by hand or the job catches up.
- The expiry sweep is a bulk `UPDATE ... WHERE`, not per-row, so it doesn't
  (and doesn't need to) do anything with Redis holds - those are already
  released at booking time, well before a booking could ever expire unpaid.
- Single server and single database. No scaling across machines.

## Roadmap

- Week 3 remaining: k6 load test with numbers, GitHub Actions CI, Docker + deploy
