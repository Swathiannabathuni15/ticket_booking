"""Restock the cinemas' showtimes with the current movie mix.

  python scripts/refresh_schedule.py            # DRY RUN: only says what it would remove
  python scripts/refresh_schedule.py --yes      # do it: remove unbooked future showtimes, then recreate them

It removes showtimes that have not started yet and have NO bookings at all (a showtime with any booking,
even a cancelled one, is kept), then creates fresh ones (scripts/schedule_showtimes.py logic), which prefer
movies that have posters. Use it after importing new movies, so they actually get shows.
These showtimes are generated demo data. Talks to the database directly (same DATABASE_URL as the app).
"""

import argparse
import sys

sys.path.insert(0, ".")
from app.db import SessionLocal  # noqa: E402
from app.services.schedule_service import clear_unbooked_future_showtimes, ensure_schedule  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--yes", action="store_true", help="actually do it (without this it is a dry run)")
ap.add_argument("--days", type=int, default=4)
a = ap.parse_args()
with SessionLocal() as db:
    n = clear_unbooked_future_showtimes(db, dry_run=not a.yes)
    if not a.yes:
        print(f"DRY RUN: {n} unbooked future showtimes would be removed and recreated. Run again with --yes to do it.")
    else:
        print(f"Removed {n} unbooked future showtimes. Creating fresh ones (this can take a minute)...")
        print(f"Created {ensure_schedule(db, a.days)} showtimes.")
