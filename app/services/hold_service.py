"""Seat holds: a short-lived, Redis-backed reservation.

WHY A HOLD
    Without one, a user fills in payment details for a seat that someone
    else books out from under them mid-checkout. A hold gives one user
    exclusive right to book a specific seat for HOLD_TTL_SECONDS.

WHY REDIS, AND WHY A PLAIN SET NX EX
    `SET key value NX EX ttl` is a single atomic command: "create this key
    only if it doesn't exist, and expire it in `ttl` seconds." That one
    command is both the race-free compare-and-set AND the auto-cleanup -
    an abandoned hold just expires on its own, nothing else has to notice.

WHY A LUA SCRIPT TO RELEASE
    Releasing needs "delete this key, but only if it's still mine." A plain
    GET-then-DEL is two round trips with a gap in between: another process
    could re-hold the seat for someone else between the GET and the DEL, and
    this call would then delete THEIR hold. A Lua script runs both steps as
    one atomic operation on the Redis server, closing that gap.

MULTI-SEAT HOLDS ARE ALL-OR-NOTHING
    Holding several seats for one order (acquire_holds) must not leave a
    partial result: if seat 3 of 4 is already taken, NONE of the four should
    end up held. A Python loop calling SET NX once per seat can't guarantee
    that - the process could crash mid-loop, or seat 4 could lose its race
    after seats 1-3 already succeeded, leaving those three held by us with
    no caller left to release them. A single Lua script takes every seat key
    as `KEYS`, checks all of them first, and only writes if none are blocked
    - the whole check-then-set runs as one atomic step on the Redis server,
    so there is no window where a partial result could exist.

FAIL CLOSED
    If Redis can't be reached, we do not silently let bookings through
    without a hold - that would defeat the point of having one. Every
    function here raises HoldUnavailable, which the router turns into a
    503. The cost is honest: seat holds (and therefore booking) are
    unavailable while Redis is down.
"""

import uuid
from collections.abc import Sequence

import redis

from app.config import settings

HOLD_TTL_SECONDS = settings.hold_ttl_seconds


class HoldUnavailable(Exception):
    """Redis could not be reached."""


class SeatHeldByOther(Exception):
    pass


def _key(seat_id: int) -> str:
    return f"seat_hold:{seat_id}"


def _owner(value: str | None) -> str | None:
    return value.split(":", 1)[0] if value else None


# KEYS[1] = hold key, ARGV[1] = user_id. Deletes the key only if it is still
# owned by that user; a no-op otherwise (already released, expired, or
# re-held by someone else since the caller last checked).
_RELEASE_IF_OWNER = """
local current = redis.call("GET", KEYS[1])
if not current then
    return 0
end
local sep = string.find(current, ":")
if sep and string.sub(current, 1, sep - 1) == ARGV[1] then
    redis.call("DEL", KEYS[1])
    return 1
end
return 0
"""


# KEYS = one hold key per seat in the request, in no particular order (no
# deadlock risk - a Lua script's commands run atomically, so there's no
# window for two scripts to interleave and wait on each other).
# ARGV[1] = user_id, ARGV[2] = a fresh token, ARGV[3] = ttl seconds.
# Checks every key BEFORE writing any of them: if seat i is held by someone
# else, returns i (1-based) and writes nothing. Otherwise sets every key and
# returns 0. Re-holding your own already-held seats just refreshes their TTL.
_ACQUIRE_ALL_OR_NOTHING = """
for i = 1, #KEYS do
    local current = redis.call("GET", KEYS[i])
    if current then
        local sep = string.find(current, ":")
        local owner = string.sub(current, 1, sep - 1)
        if owner ~= ARGV[1] then
            return i
        end
    end
end
for i = 1, #KEYS do
    redis.call("SET", KEYS[i], ARGV[1] .. ":" .. ARGV[2], "EX", ARGV[3])
end
return 0
"""


def acquire_holds(r: redis.Redis, seat_ids: list[int], user_id: int) -> dict:
    """Hold one or more seats for `user_id`, all-or-nothing (see module
    docstring). Re-holding your own seat(s) refreshes their TTL."""
    if not seat_ids:
        raise ValueError("seat_ids must not be empty")
    keys = [_key(sid) for sid in seat_ids]
    token = uuid.uuid4().hex
    try:
        blocked_at = r.eval(_ACQUIRE_ALL_OR_NOTHING, len(keys), *keys, str(user_id), token, HOLD_TTL_SECONDS)
        if blocked_at:
            raise SeatHeldByOther(seat_ids[blocked_at - 1])
        return {"seat_ids": seat_ids, "ttl_seconds": HOLD_TTL_SECONDS}
    except redis.exceptions.RedisError as exc:
        raise HoldUnavailable() from exc


def release_holds(r: redis.Redis, seat_ids: list[int], user_id: int) -> int:
    """Release several holds. Unlike acquiring, releasing needs no
    all-or-nothing guarantee - each seat's release is already an independent
    atomic operation (release_hold), so there's nothing a partial result
    could leave inconsistent. Returns how many were actually released."""
    return sum(release_hold(r, sid, user_id) for sid in seat_ids)


def has_valid_holds(r: redis.Redis, seat_ids: list[int], user_id: int) -> bool:
    return all(has_valid_hold(r, sid, user_id) for sid in seat_ids)


def seats_held_by_user(r: redis.Redis, seat_ids: Sequence[int], user_id: int) -> set[int]:
    """Which of `seat_ids` does `user_id` currently hold? Used to enforce a
    cap on cumulative holds per showtime (see routers/holds.py) - the
    per-request limit alone doesn't stop someone from calling /holds several
    times to pile up far more than that in total."""
    if not seat_ids:
        return set()
    try:
        values = r.mget(_key(sid) for sid in seat_ids)
    except redis.exceptions.RedisError as exc:
        raise HoldUnavailable() from exc
    return {sid for sid, value in zip(seat_ids, values, strict=False) if _owner(value) == str(user_id)}


def release_hold(r: redis.Redis, seat_id: int, user_id: int) -> bool:
    """Release a hold, atomically, but only the one owned by `user_id`.

    Returns True if a hold was actually released, False if there was nothing
    to release or it belonged to someone else (a "late" release).
    """
    try:
        return bool(r.eval(_RELEASE_IF_OWNER, 1, _key(seat_id), str(user_id)))
    except redis.exceptions.RedisError as exc:
        raise HoldUnavailable() from exc


def has_valid_hold(r: redis.Redis, seat_id: int, user_id: int) -> bool:
    try:
        return _owner(r.get(_key(seat_id))) == str(user_id)
    except redis.exceptions.RedisError as exc:
        raise HoldUnavailable() from exc
