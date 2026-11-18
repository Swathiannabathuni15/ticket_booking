"""Give every city several theatres, then fill the next few days with showtimes.

  python scripts/seed_cities.py

Adds the 10 big cities (the ones people expect) with three theatres each and a mix of halls
(a 272-seat hall, a 144-seat hall, and a 48-seat recliner lounge in some). Theatres that already
exist are left alone, so it is safe to run again. Also adds two clearly made-up movies with a
future release date so the "Coming soon" row has something in it.

Talks to the database directly (same DATABASE_URL as the app). Theatre names are made up.
"""

import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Movie, Screen, Theatre  # noqa: E402
from app.services import halls  # noqa: E402
from app.services.schedule_service import ensure_schedule  # noqa: E402

CITIES = [
    "Mumbai",
    "Delhi-NCR",
    "Bengaluru",
    "Hyderabad",
    "Chandigarh",
    "Ahmedabad",
    "Pune",
    "Chennai",
    "Kolkata",
    "Kochi",
]
THEATRES = [  # (name, [(screen name, layout builder)])
    ("Orion Cinemas", [("Screen 1 (Large)", halls.large_hall), ("Screen 2", halls.medium_hall)]),
    ("Starlight Multiplex", [("Screen 1", halls.medium_hall), ("Recliner Lounge", halls.lounge_hall)]),
    ("Majestic Screens", [("Screen 1", halls.large_hall)]),
]
COMING_SOON = [
    ("Moonlit Harbour", 128, "Drama/Mystery", "Hindi", 9),
    ("The Clockmaker's Daughter", 112, "Fantasy/Family", "English", 16),
]

with SessionLocal() as db:
    added = 0
    for city in CITIES:
        for name, screens in THEATRES:
            theatre = db.scalar(select(Theatre).where(Theatre.city == city, Theatre.name == name))
            if theatre is None:
                theatre = Theatre(name=name, city=city)
                db.add(theatre)
                db.flush()
                added += 1
            have = {s.name for s in db.scalars(select(Screen).where(Screen.theatre_id == theatre.id))}
            for screen_name, build in screens:
                if screen_name not in have:
                    db.add(Screen(theatre_id=theatre.id, name=screen_name, layout=build()))
    for title, minutes, genre, language, days in COMING_SOON:
        if db.scalar(select(Movie.id).where(Movie.title == title)) is None:
            db.add(
                Movie(
                    title=title,
                    duration_minutes=minutes,
                    genre=genre,
                    language=language,
                    release_date=date.today() + timedelta(days=days),
                )
            )
    db.commit()
    print(f"{added} new theatres. Creating showtimes (this can take a minute)...")
    print(f"Created {ensure_schedule(db, 4)} showtimes.")
