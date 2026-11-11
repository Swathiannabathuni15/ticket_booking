"""OMDb matching: prefer 'no poster' over a WRONG poster."""
import importlib.util
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location("omdb_lookup", Path(__file__).resolve().parents[1] / "scripts" / "omdb_lookup.py")
omdb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(omdb)

TODAY = date(2026, 11, 10)


def _r(**kw):
    base = {"Response": "True", "Type": "movie", "Title": "Cult", "Year": "2026", "Language": "Telugu, Hindi",
            "Poster": "https://m.media-amazon.com/images/M/abc.jpg", "Runtime": "142 min", "Genre": "Action, Drama, Thriller, Crime",
            "imdbRating": "7.4", "imdbVotes": "1,234"}
    return {**base, **kw}


MOVIE = {"id": 7, "title": "Cult", "language": "Telugu", "genre": ""}


def test_good_match_parses_poster_runtime_genre_rating():
    m = omdb.pick_match(_r(), "Cult", "Telugu", TODAY)
    assert m
    e = omdb.entry_from(m, MOVIE)
    assert e == {"id": 7, "title": "Cult", "poster_url": "https://m.media-amazon.com/images/M/abc.jpg",
                 "duration_minutes": 142, "genre": "Action/Drama/Thriller", "rating": 7.4}


def test_wrong_title_old_film_other_language_or_not_found_are_rejected():
    assert omdb.pick_match(_r(Title="Cults"), "Cult", "Telugu", TODAY) is None
    assert omdb.pick_match(_r(Year="1999"), "Cult", "Telugu", TODAY) is None
    assert omdb.pick_match(_r(Language="English"), "Cult", "Telugu", TODAY) is None
    assert omdb.pick_match({"Response": "False", "Error": "Movie not found!"}, "Cult", "Telugu", TODAY) is None
    assert omdb.pick_match(_r(Type="series"), "Cult", "Telugu", TODAY) is None


def test_unknown_language_is_accepted_but_the_title_must_still_match():
    assert omdb.pick_match(_r(Language="N/A"), "Cult", "Telugu", TODAY)
    assert omdb.pick_match(_r(Title="Cult Two", Language="N/A"), "Cult", "Telugu", TODAY) is None


def test_missing_values_are_left_out_not_invented():
    e = omdb.entry_from(_r(Poster="N/A", Runtime="N/A", Genre="N/A", imdbRating="N/A", imdbVotes="N/A"), MOVIE)
    assert e == {"id": 7, "title": "Cult"}                           # nothing made up
    assert "rating" not in omdb.entry_from(_r(imdbVotes="3"), MOVIE)  # 3 votes is not a rating
    assert "genre" not in omdb.entry_from(_r(), {**MOVIE, "genre": "Comedy"})   # never overwrite an existing genre
    assert "poster_url" not in omdb.entry_from(_r(Poster="http://insecure.example.com/p.jpg"), MOVIE)


def test_common_title_is_found_by_asking_for_the_recent_year(monkeypatch):
    """'t=Cult' alone returns an old film; asking for 2026 returns the new one."""
    calls = []

    def fake_get(key, params):
        calls.append(params)
        if params.get("y") == "2026":
            return _r(Title="Cult", Year="2026")
        return _r(Title="Cult", Year="1999")                       # the old film with the same name

    monkeypatch.setattr(omdb, "_get", fake_get)
    monkeypatch.setattr(omdb.time, "sleep", lambda s: None)
    m = omdb.find(MOVIE, "key", TODAY)
    assert m and m["Year"] == "2026"
    assert [c.get("y") for c in calls] == [None, "2026"]            # stopped as soon as it found it
    monkeypatch.setattr(omdb, "_get", lambda key, params: {"Response": "False", "Error": "Movie not found!"})
    assert omdb.find(MOVIE, "key", TODAY) is None                   # never found => no guess


def test_short_films_are_rejected():
    assert omdb.pick_match(_r(Genre="Short, Drama"), "Cult", "Telugu", TODAY) is None
