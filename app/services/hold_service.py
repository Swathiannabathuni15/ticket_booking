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

FAIL CLOSED
    If Redis can't be reached, we do not silently let bookings through
    without a hold - that would defeat the point of having one. Every
    function here raises HoldUnavailable, which the router turns into a
    503. The cost is honest: seat holds (and therefore booking) are
    unavailable while Redis is down.
"""
import uuid

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


def acquire_hold(r: redis.Redis, seat_id: int, user_id: int) -> dict:
    """Hold a seat for `user_id`. Re-holding your own seat refreshes the TTL."""
    key = _key(seat_id)
    try:
        current = r.get(key)
        if _owner(current) == str(user_id):
            r.expire(key, HOLD_TTL_SECONDS)
            token = current.split(":", 1)[1]
        else:
            token = uuid.uuid4().hex
            created = r.set(key, f"{user_id}:{token}", nx=True, ex=HOLD_TTL_SECONDS)
            if not created:
                raise SeatHeldByOther(seat_id)
        return {"seat_id": seat_id, "token": token, "ttl_seconds": HOLD_TTL_SECONDS}
    except redis.exceptions.RedisError as exc:
        raise HoldUnavailable() from exc


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
