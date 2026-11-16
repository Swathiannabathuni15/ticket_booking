#!/usr/bin/env python3
"""Look movies up on OMDb (IMDb data): poster, IMDb rating, runtime, genre.

Standalone: Python 3.8+, standard library only. Two ways to use it:

  A) Everything in one go, next to your running app (easiest):
       OMDB_API_KEY=your-key python scripts/fetch_posters.py all --source omdb

  B) Just the lookup (movies.json -> posters.json):
       OMDB_API_KEY=your-key python scripts/omdb_lookup.py movies.json posters.json
     then:  python scripts/fetch_posters.py apply posters.json

Get a free key at https://www.omdbapi.com/apikey.aspx (about 1,000 requests a day).
The key only works AFTER you click the activation link OMDb emails you.

Only confident matches are used: the title must be the same, the language must
not contradict ours, and the film must be from THIS YEAR (our listings are new
releases, so last year's film with the same name can't win). Everything else is listed as
"unmatched" and left alone. Small regional films are often missing from OMDb.
Poster links point at IMDb's image server (we link them, we do not copy them);
that artwork belongs to its owners, which is fine for a personal demo project.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

API = "https://www.omdbapi.com/"



def _env(name: str) -> str:
    """The setting from the environment, else from a .env file (current folder, this script's folder, or its parent)."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (os.getcwd(), here, os.path.dirname(here)):
        try:
            for line in open(os.path.join(folder, ".env")):
                k, _, v = line.strip().partition("=")
                if k == name and v.strip():
                    return v.strip().strip("'\"")
        except OSError:
            pass
    return ""


def _prefer_ipv4() -> None:
    """Try IPv4 addresses first. On some networks IPv6 is advertised but does not work, which makes
    every request wait for a timeout before falling back."""
    import socket
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: sorted(real(*a, **k), key=lambda r: r[0] != socket.AF_INET)


def _norm(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _num(text):
    digits = re.sub(r"[^0-9.]", "", text or "")
    return float(digits) if digits not in ("", ".") else None


def pick_match(resp, title, language, today=None, max_age_years=0):
    """resp: one OMDb reply for ?t=title. Returns resp if it is a confident match, else None."""
    today = today or date.today()
    if not resp or resp.get("Response") != "True" or resp.get("Type") not in (None, "movie"):
        return None
    if "short" in (resp.get("Genre") or "").lower():
        return None                                   # a short film is not the feature release we list
    if _norm(resp.get("Title")) != _norm(title):
        return None
    langs = (resp.get("Language") or "")
    if langs not in ("", "N/A") and language and language.lower() not in langs.lower():
        return None                                   # OMDb says it is in a different language
    m = re.match(r"\d{4}", resp.get("Year") or "")
    if not m or today.year - int(m.group()) > max_age_years:
        return None
    return resp


def entry_from(resp, movie):
    """The fields we update, parsed from an OMDb reply (anything missing is simply left out)."""
    e = {"id": movie["id"], "title": movie["title"]}
    poster = resp.get("Poster")
    if poster and poster != "N/A" and poster.startswith("https://"):
        e["poster_url"] = poster
    minutes = _num(resp.get("Runtime"))
    if minutes and 0 < minutes <= 600:
        e["duration_minutes"] = int(minutes)
    if not movie.get("genre") and resp.get("Genre") not in (None, "", "N/A"):
        e["genre"] = "/".join(g.strip() for g in resp["Genre"].split(",")[:3])
    rating, votes = _num(resp.get("imdbRating")), _num(resp.get("imdbVotes"))
    if rating and (votes or 0) >= 20:                 # a rating from a handful of votes means nothing
        e["rating"] = round(rating, 1)
    return e


def _get(key, params):
    url = API + "?" + urllib.parse.urlencode({"apikey": key, "type": "movie", **params})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            sys.exit("OMDb rejected the key (401). Check OMDB_API_KEY, and make sure you clicked the activation link in the email.")
        raise


def find(movie, key, today=None, max_age_years=0):
    """Ask OMDb for the title as-is, then for each recent release year. Common
    names ("Cult", "Magic") otherwise come back as some older film with the same
    name; naming the year gets the new one. Returns a confident match or None."""
    today = today or date.today()
    tries = [{}] + [{"y": str(today.year - back)} for back in range(max_age_years + 1)]
    for extra in tries:
        resp = _get(key, {"t": movie["title"], **extra})
        if resp.get("Error") == "Request limit reached!":
            sys.exit("OMDb daily limit reached (free keys allow about 1,000 a day). Run again tomorrow; finished ones are kept.")
        match = pick_match(resp, movie["title"], movie.get("language", ""), today, max_age_years)
        if match:
            return match
        time.sleep(0.05)
    return None


def lookup_movies(movies, key, log=print):
    """movies: [{id, title, language, genre?}] -> (found, unmatched_titles)"""
    found, unmatched = [], []
    for m in movies:
        match = find(m, key)
        e = entry_from(match, m) if match else None
        if not e or "poster_url" not in e:
            unmatched.append(m["title"])
            continue
        found.append(e)
        log("  ok  " + m["title"])
        time.sleep(0.1)
    return found, unmatched


def main():
    _prefer_ipv4()
    key = _env("OMDB_API_KEY")
    if not key:
        sys.exit("Set OMDB_API_KEY first (free key: https://www.omdbapi.com/apikey.aspx).")
    if len(sys.argv) < 3:
        sys.exit("Usage: OMDB_API_KEY=... python omdb_lookup.py movies.json posters.json")
    movies = json.load(open(sys.argv[1]))
    found, unmatched = lookup_movies(movies, key)
    json.dump({"posters": found, "unmatched": unmatched}, open(sys.argv[2], "w"), indent=2)
    print(f"\nWrote {len(found)} matches to {sys.argv[2]}; {len(unmatched)} not matched (left as they are).")


if __name__ == "__main__":
    main()
