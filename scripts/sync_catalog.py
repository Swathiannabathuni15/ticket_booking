#!/usr/bin/env python3
"""Keep the movie list current: what is playing in India now, and what is coming soon.

Source: TMDB (themoviedb.org) - an official, free API for developers (free key at
https://www.themoviedb.org/settings/api). Some networks block TMDB, so the work is split:

  1. On any device where TMDB opens (standard library only, no other files needed):
         TMDB_API_KEY=your-key python sync_catalog.py fetch catalog.json
  2. On the machine with the app (talks to the database directly, like the app does):
         python scripts/sync_catalog.py apply catalog.json
  Or both at once where TMDB opens:   TMDB_API_KEY=... python scripts/sync_catalog.py all

Movies already in the database are updated (release date, poster, genre, rating, real runtime
in place of the 150-minute placeholder); new ones are created. A movie whose release date is in
the future shows under "Coming soon". Only Indian languages and English are kept.

It only supplies the movie LIST. Showtimes are still generated (scripts/schedule_showtimes.py),
because real cinema schedules are not available from TMDB.
TMDB asks apps to show: "This product uses the TMDB API but is not endorsed or certified by TMDB."
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"
LANG_NAMES = {"te": "Telugu", "hi": "Hindi", "ta": "Tamil", "kn": "Kannada", "ml": "Malayalam", "en": "English"}



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



def _fetch_json(request, tries: int = 8):
    """GET a URL and parse the JSON. On a flaky network some connections are reset before they start,
    so a failed connection (or a 429/5xx reply) is retried a few times with a short pause."""
    last = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            last = exc
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last = exc
        time.sleep(min(0.4 * (attempt + 1), 3))
    raise SystemExit(f"Could not reach the service after {tries} tries ({last}). Check your network and try again.")

def _prefer_ipv4() -> None:
    """Try IPv4 addresses first. On some networks IPv6 is advertised but does not work, which makes
    every request wait for a timeout before falling back."""
    import socket
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: sorted(real(*a, **k), key=lambda r: r[0] != socket.AF_INET)


def entry_from_tmdb(item: dict, genre_names: dict, runtime: int | None = None) -> dict | None:
    """One TMDB result -> one catalogue entry, or None if it is not for us (other language, no title/date)."""
    lang = LANG_NAMES.get(item.get("original_language", ""))
    title = (item.get("title") or "").strip()
    if not lang or not title or not item.get("release_date"):
        return None
    e = {"title": title, "language": lang, "release_date": item["release_date"]}
    names = [genre_names[g] for g in item.get("genre_ids", []) if g in genre_names][:3]
    if names:
        e["genre"] = "/".join(names)
    if item.get("poster_path"):
        e["poster_url"] = IMAGE_BASE + item["poster_path"]
    if runtime and 0 < runtime <= 600:
        e["duration_minutes"] = int(runtime)
    if (item.get("vote_count") or 0) >= 20 and item.get("vote_average"):
        e["rating"] = round(float(item["vote_average"]), 1)
    return e


def _get(path: str, key: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    headers = {"Accept": "application/json"}
    if len(key) > 40:
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


def fetch_catalog(key: str, pages: int = 3, log=print) -> list[dict]:
    genres = {g["id"]: g["name"] for g in _get("/genre/movie/list", key).get("genres", [])}
    seen: dict[int, dict] = {}
    for kind in ("now_playing", "upcoming"):
        for page in range(1, pages + 1):
            res = _get(f"/movie/{kind}", key, {"region": "IN", "page": page})
            for item in res.get("results", []):
                seen.setdefault(item["id"], item)
            if page >= res.get("total_pages", 1):
                break
    entries = []
    for tmdb_id, item in seen.items():
        if entry_from_tmdb(item, genres) is None:
            continue
        runtime = _get(f"/movie/{tmdb_id}", key).get("runtime")          # one extra call each: the list has no runtime
        entries.append(entry_from_tmdb(item, genres, runtime))
        time.sleep(0.05)
    log(f"{len(entries)} movies kept out of {len(seen)} listed")
    return entries


def main():
    _prefer_ipv4()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    path = sys.argv[2] if len(sys.argv) > 2 else "catalog.json"
    if cmd not in ("fetch", "apply", "all"):
        sys.exit(__doc__)
    entries = None
    if cmd in ("fetch", "all"):
        key = _env("TMDB_API_KEY")
        if not key:
            sys.exit("Set TMDB_API_KEY first (free key: https://www.themoviedb.org/settings/api).")
        entries = fetch_catalog(key)
        if cmd == "fetch":
            json.dump(entries, open(path, "w"), indent=2)
            print(f"Wrote {path}. Next, on the machine with the app: python scripts/sync_catalog.py apply {path}")
            return
    if cmd == "apply":
        entries = json.load(open(path))
    sys.path.insert(0, ".")
    from app.db import SessionLocal
    from app.services.catalog_sync import upsert_movies
    with SessionLocal() as db:
        print(upsert_movies(db, entries or []))


if __name__ == "__main__":
    main()
