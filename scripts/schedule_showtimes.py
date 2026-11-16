"""Keep the cinemas' showtimes current: make sure the next few days have shows.

  python scripts/schedule_showtimes.py            # today + the next 4 days
  python scripts/schedule_showtimes.py --days 7

Safe to run as often as you like (it only adds what is missing), so run it once a day,
or set AUTO_SCHEDULE_DAYS=4 and let the server's background loop do it. It talks to the
database directly, so it uses the same DATABASE_URL as the app (your .env).

The showtimes are generated, not real cinema schedules.
"""
import argparse
import sys

sys.path.insert(0, ".")
from app.db import SessionLocal  # noqa: E402
from app.services.schedule_service import ensure_schedule  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=4, help="how many days ahead (default 4)")
a = ap.parse_args()
with SessionLocal() as db:
    n = ensure_schedule(db, a.days)
print(f"Created {n} showtimes ({'nothing was missing' if n == 0 else 'schedule extended'}).")
