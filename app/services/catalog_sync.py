"""Adding and updating movies from a catalogue file (see scripts/sync_catalog.py).

A movie in the file is matched to one in the database by title and language
(ignoring case and punctuation). A match is UPDATED (never overwriting something
already known with something blank); no match means a new movie is created.
"""

import re
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Movie

PLACEHOLDER_MINUTES = 150  # what the Telugu seed used when the real runtime was unknown


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def upsert_movies(db: Session, entries: list[dict], *, posters_only: bool = False) -> dict:
    """entries: [{title, language, release_date?, genre?, poster_url?, duration_minutes?, rating?}]
    posters_only: do not CREATE a movie that has no poster (existing movies are still updated), so the
    listing never gains a plain generated-poster entry."""
    existing = {(_norm(m.title), (m.language or "").lower()): m for m in db.scalars(select(Movie)).all()}
    created = updated = 0
    for e in entries:
        key = (_norm(e["title"]), e.get("language", "English").lower())
        release = date.fromisoformat(e["release_date"]) if e.get("release_date") else None
        m = existing.get(key)
        if m is None:
            if posters_only and not e.get("poster_url"):
                continue
            m = Movie(
                title=e["title"],
                language=e.get("language", "English"),
                genre=e.get("genre", ""),
                duration_minutes=e.get("duration_minutes") or PLACEHOLDER_MINUTES,
                poster_url=e.get("poster_url"),
                rating=e.get("rating"),
                release_date=release,
            )
            db.add(m)
            existing[key] = m
            created += 1
            continue
        changed = False
        if release and m.release_date != release:
            m.release_date, changed = release, True
        if e.get("poster_url") and not m.poster_url:
            m.poster_url, changed = e["poster_url"], True
        if e.get("genre") and not m.genre:
            m.genre, changed = e["genre"], True
        if e.get("rating") is not None and m.rating is None:
            m.rating, changed = e["rating"], True
        if e.get("duration_minutes") and m.duration_minutes == PLACEHOLDER_MINUTES:
            m.duration_minutes, changed = e["duration_minutes"], True  # only replaces the placeholder
        updated += changed
    db.commit()
    return {"created": created, "updated": updated}
