"""Turning TMDB results into movies, and adding / updating them without losing what we know."""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from app.models import Movie
from app.services.catalog_sync import PLACEHOLDER_MINUTES, upsert_movies

spec = importlib.util.spec_from_file_location("sync_catalog", Path(__file__).resolve().parents[1] / "scripts" / "sync_catalog.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

GENRES = {28: "Action", 35: "Comedy", 18: "Drama", 53: "Thriller"}
ITEM = {"id": 1, "title": "Cult", "original_language": "te", "release_date": "2026-10-30", "poster_path": "/p.jpg",
        "genre_ids": [28, 18, 53, 35], "vote_average": 7.44, "vote_count": 120}


def test_a_tmdb_result_becomes_a_catalogue_entry():
    e = sync.entry_from_tmdb(ITEM, GENRES, runtime=142)
    assert e == {"title": "Cult", "language": "Telugu", "release_date": "2026-10-30", "genre": "Action/Drama/Thriller",
                 "poster_url": "https://image.tmdb.org/t/p/w500/p.jpg", "duration_minutes": 142, "rating": 7.4}


def test_missing_or_unhelpful_values_are_left_out_not_invented():
    e = sync.entry_from_tmdb({**ITEM, "poster_path": None, "vote_count": 3, "genre_ids": []}, GENRES, runtime=None)
    assert e == {"title": "Cult", "language": "Telugu", "release_date": "2026-10-30"}
    assert "duration_minutes" not in sync.entry_from_tmdb(ITEM, GENRES, runtime=0)


def test_other_languages_and_undated_movies_are_skipped():
    assert sync.entry_from_tmdb({**ITEM, "original_language": "ja"}, GENRES) is None
    assert sync.entry_from_tmdb({**ITEM, "release_date": ""}, GENRES) is None
    assert sync.entry_from_tmdb({**ITEM, "title": ""}, GENRES) is None


def test_new_movies_are_created_and_a_future_date_means_coming_soon(db):
    future = (date.today() + timedelta(days=12)).isoformat()
    r = upsert_movies(db, [{"title": "Brand New", "language": "Hindi", "release_date": future, "genre": "Drama"},
                           {"title": "No Runtime", "language": "Tamil"}])
    assert r == {"created": 2, "updated": 0}
    brand = db.scalar(select(Movie).where(Movie.title == "Brand New"))
    assert brand.status == "coming_soon" and brand.duration_minutes == PLACEHOLDER_MINUTES


def test_existing_movies_are_updated_without_overwriting_what_is_known(db):
    db.add(Movie(title="Cult", language="Telugu", genre="Horror", duration_minutes=PLACEHOLDER_MINUTES, rating=None))
    db.add(Movie(title="Known", language="Telugu", genre="Comedy", duration_minutes=101, rating=8.0,
                 poster_url="https://example.com/mine.jpg"))
    db.commit()
    r = upsert_movies(db, [
        {"title": "CULT!", "language": "telugu", "release_date": "2026-10-30", "genre": "Action", "poster_url": "https://image.tmdb.org/x.jpg",
         "duration_minutes": 142, "rating": 7.4},                                         # matches "Cult" despite case/punctuation
        {"title": "Known", "language": "Telugu", "genre": "Drama", "poster_url": "https://image.tmdb.org/y.jpg",
         "duration_minutes": 150, "rating": 5.0},
    ])
    assert r == {"created": 0, "updated": 1}
    cult = db.scalar(select(Movie).where(Movie.title == "Cult"))
    assert (cult.genre, cult.duration_minutes, float(cult.rating), cult.release_date) == ("Horror", 142, 7.4, date(2026, 10, 30))
    assert cult.poster_url == "https://image.tmdb.org/x.jpg"                             # blank poster filled in; genre kept
    known = db.scalar(select(Movie).where(Movie.title == "Known"))
    assert (known.genre, known.duration_minutes, float(known.rating), known.poster_url) == ("Comedy", 101, 8.0, "https://example.com/mine.jpg")


def test_the_same_title_in_another_language_is_a_different_movie(db):
    db.add(Movie(title="Magic", language="Telugu", duration_minutes=100)); db.commit()
    assert upsert_movies(db, [{"title": "Magic", "language": "Marathi"}]) == {"created": 1, "updated": 0}


def test_posters_only_skips_new_movies_without_a_poster_but_still_updates_existing_ones(db):
    db.add(Movie(title="Existing", language="Hindi", duration_minutes=PLACEHOLDER_MINUTES)); db.commit()
    r = upsert_movies(db, [
        {"title": "Has Art", "language": "Hindi", "poster_url": "https://image.tmdb.org/a.jpg"},
        {"title": "No Art", "language": "Hindi"},
        {"title": "Existing", "language": "Hindi", "duration_minutes": 130},          # no poster, but it is already ours
    ], posters_only=True)
    assert r == {"created": 1, "updated": 1}
    assert {m.title for m in db.scalars(select(Movie))} == {"Existing", "Has Art"}
    assert db.scalar(select(Movie.duration_minutes).where(Movie.title == "Existing")) == 130
