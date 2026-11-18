#!/usr/bin/env python3
"""STEP 2 of 3 - look movies up on TMDB. Runs on ANY device that can reach TMDB.

Standalone on purpose: Python 3.8+, standard library only, no other project files.
(Google Colab works: upload this file and movies.json, then run it.)

    TMDB_API_KEY=your-key python tmdb_lookup.py movies.json posters.json

  movies.json  - made by `python scripts/fetch_posters.py export` on the machine with the app
  posters.json - the result; bring it back and run `python scripts/fetch_posters.py apply posters.json`

Only confident matches are used (same title, right language, recent release);
the rest are listed as "unmatched" and left alone. TMDB asks apps to show:
"This product uses the TMDB API but is not endorsed or certified by TMDB."
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

API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
LANG_CODES = {"telugu": "te", "hindi": "hi", "tamil": "ta", "english": "en", "kannada": "kn", "malayalam": "ml"}



_conn = None   # the one kept-open connection (see _fetch_json)


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



def _fetch_json(request, tries: int = 15):
    """GET a URL and parse the JSON, over ONE connection that is kept open and reused. On a flaky network
    each new connection can be reset before it starts, so opening hundreds of them fails; reusing one
    needs far fewer. A dropped connection (or a 429/5xx reply) is retried with a short pause."""
    import http.client
    global _conn
    parts = urllib.parse.urlsplit(request.full_url)
    target = parts.path + ("?" + parts.query if parts.query else "")
    last = None
    for attempt in range(tries):
        try:
            if _conn is None:
                _conn = http.client.HTTPSConnection(parts.netloc, timeout=20)
            _conn.request("GET", target, headers=dict(request.header_items()))
            resp = _conn.getresponse()
            body = resp.read()
            if resp.status in (429, 500, 502, 503, 504):
                last = f"HTTP {resp.status}"
                _conn.close(); _conn = None
            elif resp.status >= 400:
                raise urllib.error.HTTPError(request.full_url, resp.status, resp.reason, resp.headers, None)
            else:
                return json.loads(body)
        except urllib.error.HTTPError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            if _conn is not None:
                _conn.close()
            _conn = None
        time.sleep(min(0.5 * (attempt + 1), 4))
    raise SystemExit(f"Could not reach the service after {tries} tries ({last}). Check your network and try again.")


def _prefer_ipv4() -> None:
    """Try IPv4 addresses first. On some networks IPv6 is advertised but does not work, which makes
    every request wait for a timeout before falling back."""
    import socket
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: sorted(real(*a, **k), key=lambda r: r[0] != socket.AF_INET)


def _norm(title):
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def pick_match(results, title, language, today=None, max_age_days=900):
    """Best TMDB result for `title`, or None. A wrong poster is worse than a
    generated one, so it must have a poster, the SAME title, the right original
    language, and a recent release date (an old film with the same name can't win)."""
    today = today or date.today()
    want_lang = LANG_CODES.get((language or "").lower())
    best, best_score = None, 0.0
    for r in results:
        if not r.get("poster_path"):
            continue
        if _norm(title) not in {_norm(r.get("title") or ""), _norm(r.get("original_title") or "")}:
            continue
        if want_lang and r.get("original_language") and r["original_language"] != want_lang:
            continue
        try:
            released = date.fromisoformat(r.get("release_date") or "")
        except ValueError:
            continue
        if (today - released).days > max_age_days:
            continue
        score = float(r.get("popularity") or 0) + float(r.get("vote_count") or 0) / 100
        if best is None or score > best_score:
            best, best_score = r, score
    return best


def _get(path, key, params=None):
    params = dict(params or {})
    headers = {"Accept": "application/json"}
    if len(key) > 40:                       # the long "API Read Access Token"
        headers["Authorization"] = "Bearer " + key
    else:
        params["api_key"] = key
    url = API + path + "?" + urllib.parse.urlencode(params)
    try:
        return _fetch_json(urllib.request.Request(url, headers=headers))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            sys.exit("TMDB rejected the key (401). Check TMDB_API_KEY.")
        raise


def lookup_movies(movies, key, log=print):
    """movies: [{id, title, language, genre?}] -> (found, unmatched_titles)"""
    found, unmatched = [], []
    for m in movies:
        res = _get("/search/movie", key, {"query": m["title"], "include_adult": "false"})
        match = pick_match(res.get("results", []), m["title"], m.get("language", ""))
        if match is None:
            unmatched.append(m["title"])
            continue
        detail = _get(f"/movie/{match['id']}", key)
        entry = {"id": m["id"], "title": m["title"], "poster_url": IMAGE_BASE + match["poster_path"]}
        if detail.get("runtime"):
            entry["duration_minutes"] = int(detail["runtime"])
        if not m.get("genre") and detail.get("genres"):
            entry["genre"] = "/".join(g["name"] for g in detail["genres"][:3])
        if (match.get("vote_count") or 0) >= 20 and match.get("vote_average"):
            entry["rating"] = round(float(match["vote_average"]), 1)
        found.append(entry)
        log("  ok  " + m["title"])
        time.sleep(0.1)
    return found, unmatched


def main():
    _prefer_ipv4()
    key = _env("TMDB_API_KEY")
    if not key:
        sys.exit("Set TMDB_API_KEY first (free key: https://www.themoviedb.org/settings/api).")
    if len(sys.argv) < 3:
        sys.exit("Usage: TMDB_API_KEY=... python tmdb_lookup.py movies.json posters.json")
    with open(sys.argv[1]) as f:
        movies = json.load(f)
    found, unmatched = lookup_movies(movies, key)
    with open(sys.argv[2], "w") as f:
        json.dump({"posters": found, "unmatched": unmatched}, f, indent=2)
    print(f"\nWrote {len(found)} matches to {sys.argv[2]}; {len(unmatched)} not matched (left as they are).")


if __name__ == "__main__":
    main()
