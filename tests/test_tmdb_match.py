"""The poster matcher must prefer 'no poster' over a WRONG poster."""
from datetime import date

import importlib.util
from pathlib import Path

import pytest

from app.services import tmdb as app_tmdb

# the standalone Colab-friendly copy must behave exactly like the app's matcher
_spec = importlib.util.spec_from_file_location("tmdb_lookup", Path(__file__).resolve().parents[1] / "scripts" / "tmdb_lookup.py")
standalone = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standalone)


@pytest.fixture(params=[app_tmdb, standalone], ids=["app", "standalone"], autouse=True)
def _matcher(request, monkeypatch):
    monkeypatch.setitem(globals(), "pick_match", request.param.pick_match)
    monkeypatch.setitem(globals(), "poster_url", getattr(request.param, "poster_url", lambda m: request.param.IMAGE_BASE + m["poster_path"]))


TODAY = date(2026, 11, 8)


def _r(**kw):
    base = {"id": 1, "title": "Cult", "original_title": "Cult", "original_language": "te",
            "release_date": "2026-10-30", "poster_path": "/p.jpg", "popularity": 5, "vote_count": 10}
    return {**base, **kw}


def test_exact_recent_right_language_match_is_accepted():
    m = pick_match([_r()], "Cult", "Telugu", TODAY)
    assert m and poster_url(m) == "https://image.tmdb.org/t/p/w500/p.jpg"


def test_title_matching_ignores_case_and_punctuation():
    assert pick_match([_r(title="Aadarsha Kutumbam House No: 47")], "aadarsha kutumbam house no 47", "Telugu", TODAY)


def test_old_film_with_the_same_name_is_rejected():
    assert pick_match([_r(release_date="1999-01-01")], "Cult", "Telugu", TODAY) is None


def test_wrong_language_is_rejected():
    assert pick_match([_r(original_language="en")], "Cult", "Telugu", TODAY) is None


def test_different_title_or_missing_poster_or_date_is_rejected():
    assert pick_match([_r(title="Cults", original_title="Cults")], "Cult", "Telugu", TODAY) is None
    assert pick_match([_r(poster_path=None)], "Cult", "Telugu", TODAY) is None
    assert pick_match([_r(release_date="")], "Cult", "Telugu", TODAY) is None


def test_most_popular_of_several_matches_wins():
    best = pick_match([_r(id=1, popularity=1), _r(id=2, popularity=50)], "Cult", "Telugu", TODAY)
    assert best["id"] == 2


def test_apply_sends_only_known_fields_and_only_tmdb_images(client):
    """The apply step of scripts/fetch_posters.py, run against the real API."""
    from tests.conftest import admin_headers
    fp_spec = importlib.util.spec_from_file_location("fetch_posters", Path(__file__).resolve().parents[1] / "scripts" / "fetch_posters.py")
    fetch_posters = importlib.util.module_from_spec(fp_spec)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    fp_spec.loader.exec_module(fetch_posters)
    adm = admin_headers(client)
    m = client.post("/movies", json={"title": "Apply Me", "duration_minutes": 150, "language": "Telugu"}, headers=adm).json()
    entries = [
        {"id": m["id"], "title": "Apply Me", "poster_url": "https://image.tmdb.org/t/p/w500/a.jpg",
         "duration_minutes": 132, "rating": 7.4, "title_rename": "ignored", "language": "Hindi"},
        {"id": m["id"], "title": "Evil", "poster_url": "https://evil.example.com/a.jpg"},
    ]
    assert fetch_posters.apply_posters(client, adm, entries) == 1                 # the evil link is skipped
    got = [x for x in client.get("/movies?limit=100").json() if x["id"] == m["id"]][0]
    assert got["poster_url"].startswith("https://image.tmdb.org/") and got["duration_minutes"] == 132
    assert got["language"] == "Telugu" and got["title"] == "Apply Me"             # fields we don't allow are untouched
