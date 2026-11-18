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


# THE PER-SHOWTIME CAP IS CHECKED IN THE SAME ATOMIC STEP. Checking "how many do you already hold?"
# in Python and then acquiring is a race: two requests sent at the same instant both see 4 held, both
# add 4, and the person ends up with 8 + 8. So each (user, showtime) has an index - a sorted set of the
# seat ids they hold, scored by expiry time - and the script counts and writes in one go.
#
# KEYS = the requested seats' hold keys (n of them), then one index key per showtime in the request.
# ARGV = user_id, token, ttl, cap, n, then per seat: seat_id, showtime group number (1-based).
# Returns {0, 0} = held; {1, i} = seat i is someone else's; {2, g} = showtime g would go over the cap.
# Index entries that expired, or whose seat is no longer this user's (released, booked), are pruned
# first. (That check reads `seat_hold:<id>` keys by name - fine on one Redis server, which is what this
# app uses; a Redis Cluster would need hash tags.)
_ACQUIRE_ALL_OR_NOTHING = """
local uid, token, ttl, cap, n = ARGV[1], ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])
local function owner(v)
    if not v then return nil end
    local sep = string.find(v, ":")
    return string.sub(v, 1, sep - 1)
end
for i = 1, n do
    local cur = redis.call("GET", KEYS[i])
    if cur and owner(cur) ~= uid then
        return {1, i}
    end
end
local t = redis.call("TIME")
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
for g = 1, #KEYS - n do
    local idx = KEYS[n + g]
    redis.call("ZREMRANGEBYSCORE", idx, "-inf", now_ms)
    local held, count = {}, 0
    for _, sid in ipairs(redis.call("ZRANGE", idx, 0, -1)) do
        if owner(redis.call("GET", "seat_hold:" .. sid)) == uid then
            held[sid] = true
            count = count + 1
        else
            redis.call("ZREM", idx, sid)
        end
    end
    for i = 1, n do
        local sid = ARGV[4 + 2 * i]
        if tonumber(ARGV[5 + 2 * i]) == g and not held[sid] then
            held[sid] = true
            count = count + 1
        end
    end
    if count > cap then
        return {2, g}
    end
end
for i = 1, n do
    redis.call("SET", KEYS[i], uid .. ":" .. token, "EX", ttl)
    local idx = KEYS[n + tonumber(ARGV[5 + 2 * i])]
    redis.call("ZADD", idx, now_ms + ttl * 1000, ARGV[4 + 2 * i])
    redis.call("EXPIRE", idx, ttl)
end
return {0, 0}
"""


class HoldCapExceeded(Exception):
    """Holding these would put the user over the per-showtime limit. args[0] = the showtime id."""


def _index_key(user_id: int, showtime_id: int) -> str:
    return f"user_holds:{user_id}:{showtime_id}"


def acquire_holds(
    r: redis.Redis, seat_ids: list[int], user_id: int, showtime_of: dict[int, int], cap: int | None = None
) -> dict:
    """Hold one or more seats for `user_id`, all-or-nothing (see module docstring), and never more than
    `cap` seats per showtime in total (default: MAX_HELD_SEATS_PER_SHOWTIME). `showtime_of` maps each seat
    id to its showtime. Re-holding your own seat(s) refreshes their TTL and does not count twice."""
    if not seat_ids:
        raise ValueError("seat_ids must not be empty")
    cap = settings.max_held_seats_per_showtime if cap is None else cap
    showtimes = sorted({showtime_of[sid] for sid in seat_ids})
    group = {st: i + 1 for i, st in enumerate(showtimes)}
    keys = [_key(sid) for sid in seat_ids] + [_index_key(user_id, st) for st in showtimes]
    args: list = [str(user_id), uuid.uuid4().hex, HOLD_TTL_SECONDS, cap, len(seat_ids)]
    for sid in seat_ids:
        args += [sid, group[showtime_of[sid]]]
    try:
        code, at = r.eval(_ACQUIRE_ALL_OR_NOTHING, len(keys), *keys, *args)
    except redis.exceptions.RedisError as exc:
        raise HoldUnavailable() from exc
    if code == 1:
        raise SeatHeldByOther(seat_ids[at - 1])
    if code == 2:
        raise HoldCapExceeded(showtimes[at - 1])
    return {"seat_ids": seat_ids, "ttl_seconds": HOLD_TTL_SECONDS}


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
