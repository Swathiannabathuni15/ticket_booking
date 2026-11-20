"""Load test: N users race for a small number of seats, full flow each
(browse -> hold -> book -> pay). Then it audits the database.

  DATABASE_URL=... python scripts/loadtest.py --base http://localhost:8100 --users 1000 --seats 100

Setup writes users straight into the DB and mints JWTs (signup/login are
bcrypt-bound and have their own rate limits; they aren't what this measures).
The audit proves what matters: no seat has two active bookings, and every
confirmed booking has exactly one payment.
"""

import argparse
import asyncio
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict

import httpx

sys.path.insert(0, ".")
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402

lat: dict[str, list[float]] = defaultdict(list)
codes: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))


async def call(c: httpx.AsyncClient, name: str, method: str, url: str, **kw):
    t0 = time.perf_counter()
    try:
        r = await c.request(method, url, **kw)
        code = r.status_code
    except httpx.HTTPError:
        r, code = None, 0
    lat[name].append((time.perf_counter() - t0) * 1000)
    codes[name][code] += 1
    return r


async def user_flow(c: httpx.AsyncClient, token: str, showtime_id: int, out: dict, ramp: float = 0):
    if ramp:
        await asyncio.sleep(random.uniform(0, ramp))  # users arrive spread over `ramp` seconds
    h = {"Authorization": f"Bearer {token}"}
    for _ in range(4):  # a real user retries with another seat when theirs is taken
        r = await call(c, "GET seats", "GET", f"/showtimes/{showtime_id}/seats", headers=h)
        if r is None or r.status_code != 200:
            return
        free = [s["id"] for s in r.json() if s["status"] == "available"]
        if not free:
            out["sold_out"] += 1
            return
        seat = random.choice(free)
        r = await call(c, "POST holds", "POST", "/holds", json={"seat_ids": [seat]}, headers=h)
        if r is None or r.status_code != 201:
            continue
        r = await call(
            c,
            "POST bookings",
            "POST",
            "/bookings",
            json={"seat_ids": [seat]},
            headers={**h, "Idempotency-Key": uuid.uuid4().hex},
        )
        if r is None or r.status_code != 201:
            return
        bid = r.json()["id"]
        r = await call(
            c,
            "POST payments",
            "POST",
            "/payments",
            json={"booking_id": bid},
            headers={**h, "Idempotency-Key": uuid.uuid4().hex},
        )
        if r is not None and r.status_code == 201:
            out["paid"] += 1
        return


def _shard(args):
    """One OS process: runs its slice of users on its own event loop and
    returns raw latencies/status codes (a single Python event loop cannot
    generate enough load for 1000 users - it becomes the bottleneck)."""
    base, conns, showtime_id, tokens, ramp = args

    async def run():
        out = {"paid": 0, "sold_out": 0}
        limits = httpx.Limits(max_connections=conns, max_keepalive_connections=conns)
        async with httpx.AsyncClient(base_url=base, timeout=60, limits=limits) as c:
            await asyncio.gather(*(user_flow(c, t, showtime_id, out, ramp) for t in tokens))
        return out

    out = asyncio.run(run())
    return out, dict(lat), {k: dict(v) for k, v in codes.items()}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--users", type=int, default=1000)
    ap.add_argument("--seats", type=int, default=100)
    ap.add_argument("--conns", type=int, default=200, help="connections per load-generator process")
    ap.add_argument("--ramp", type=float, default=0, help="spread user arrivals over N seconds (0 = all at once)")
    ap.add_argument("--procs", type=int, default=8, help="load-generator processes")
    a = ap.parse_args()

    run = uuid.uuid4().hex[:8]
    pw = hash_password("load-test-password")  # hash once, reuse for every user
    with SessionLocal() as db:
        users = [User(email=f"load-{run}-{i}@example.com", password_hash=pw, is_admin=(i == 0)) for i in range(a.users)]
        db.add_all(users)
        db.commit()
        ids = [u.id for u in users]
    tokens = [create_access_token(i) for i in ids]
    adm = {"Authorization": f"Bearer {tokens[0]}"}

    limits = httpx.Limits(max_connections=a.conns, max_keepalive_connections=a.conns)
    async with httpx.AsyncClient(base_url=a.base, timeout=60, limits=limits) as c:
        movie = (await c.post("/movies", json={"title": f"Load {run}", "duration_minutes": 100}, headers=adm)).json()
        theatre = (await c.post("/theatres", json={"name": "Load", "city": "X"}, headers=adm)).json()
        cols = 10
        rows = [{"row": chr(65 + i), "seats": ["standard"] * cols} for i in range((a.seats + cols - 1) // cols)]
        screen = (
            await c.post("/screens", json={"theatre_id": theatre["id"], "name": "L", "rows": rows}, headers=adm)
        ).json()
        st = (
            await c.post(
                "/showtimes",
                json={
                    "movie_id": movie["id"],
                    "screen_id": screen["id"],
                    "starts_at": "2031-01-01T18:00:00",
                    "prices": {"standard": 10},
                },
                headers=adm,
            )
        ).json()
        sid = st["id"]

    import concurrent.futures as cf
    import multiprocessing as mp

    shards = [(a.base, a.conns // a.procs + 1, sid, tokens[i :: a.procs], a.ramp) for i in range(a.procs)]
    out = {"paid": 0, "sold_out": 0}
    t0 = time.perf_counter()
    with cf.ProcessPoolExecutor(a.procs, mp_context=mp.get_context("fork")) as ex:
        for o, lats, cd in ex.map(_shard, shards):
            for k in out:
                out[k] += o[k]
            for name, xs in lats.items():
                lat[name].extend(xs)
            for name, d in cd.items():
                for code, n in d.items():
                    codes[name][code] += n
    wall = time.perf_counter() - t0

    total_reqs = sum(len(v) for v in lat.values())
    print(
        f"\n{a.users} users, {a.seats} seats, {wall:.1f}s wall, {total_reqs} requests ({total_reqs / wall:.0f} req/s)\n"
    )
    print(f"{'endpoint':<14}{'count':>7}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}  status codes")
    for name, xs in lat.items():
        print(
            f"{name:<14}{len(xs):>7}{statistics.median(xs):>9.0f}{pct(xs, .95):>9.0f}{pct(xs, .99):>9.0f}  {dict(codes[name])}"
        )
    allx = [x for xs in lat.values() for x in xs]
    print(f"{'ALL':<14}{len(allx):>7}{statistics.median(allx):>9.0f}{pct(allx, .95):>9.0f}{pct(allx, .99):>9.0f}")

    with SessionLocal() as db:
        dup = db.execute(
            text(
                "SELECT count(*) FROM (SELECT showtime_seat_id FROM booking_seats bs JOIN showtime_seats s "
                "ON s.id = bs.showtime_seat_id WHERE s.showtime_id = :sid AND bs.status IN ('pending_payment','confirmed') "
                "GROUP BY showtime_seat_id HAVING count(*) > 1) q"
            ),
            {"sid": sid},
        ).scalar()
        confirmed = db.execute(
            text("SELECT count(*) FROM bookings WHERE showtime_id = :sid AND status = 'confirmed'"), {"sid": sid}
        ).scalar()
        payments = db.execute(
            text("SELECT count(*) FROM payments p JOIN bookings b ON b.id = p.booking_id WHERE b.showtime_id = :sid"),
            {"sid": sid},
        ).scalar()
        seats_taken = db.execute(
            text(
                "SELECT count(DISTINCT showtime_seat_id) FROM booking_seats bs JOIN showtime_seats s ON s.id = bs.showtime_seat_id "
                "WHERE s.showtime_id = :sid AND bs.status IN ('pending_payment','confirmed')"
            ),
            {"sid": sid},
        ).scalar()
    print(
        f"\nAUDIT  seats with >1 active booking: {dup}   confirmed bookings: {confirmed}   payments: {payments}   "
        f"distinct seats taken: {seats_taken}/{a.seats}   users who paid: {out['paid']}"
    )
    ok = dup == 0 and confirmed == payments == out["paid"] and seats_taken <= a.seats
    print("RESULT:", "PASS - zero double bookings" if ok else "FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
