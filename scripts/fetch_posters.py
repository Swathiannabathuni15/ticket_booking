"""Real posters, runtimes, genres and ratings, from OMDb (IMDb data) or TMDB.

OMDb  (works on most networks; the IMDb rating):
     OMDB_API_KEY=... python scripts/fetch_posters.py all --source omdb
TMDB  (some networks block it - see the three steps below):
     TMDB_API_KEY=... python scripts/fetch_posters.py all --source tmdb

TMDB in three steps.

Why three steps: only the TMDB lookup needs to reach TMDB. Some networks (many
Indian internet providers, some company networks) block it, so the lookup can run
somewhere else (a personal computer, or Google Colab in a browser) while the other
two steps run next to your app.

  1. Where the app runs:   python scripts/fetch_posters.py export            -> movies.json
  2. Anywhere TMDB opens:  TMDB_API_KEY=... python scripts/tmdb_lookup.py movies.json posters.json
  3. Where the app runs:   python scripts/fetch_posters.py apply posters.json

Everything in one go (if TMDB opens on this machine):
     TMDB_API_KEY=... python scripts/fetch_posters.py all

Options: --base http://localhost:8000   --all-movies (include ones that already have a poster)
Posters are linked from TMDB's image server, not copied.
"""
import argparse
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
EMAIL, PASSWORD = "admin@demo-cinema.com", "demo-password-1"
ALLOWED_FIELDS = ("poster_url", "duration_minutes", "genre", "rating")
# A poster link in a result file may only point at these image servers.
ALLOWED_POSTER_HOSTS = ("https://image.tmdb.org/", "https://m.media-amazon.com/")


def list_movies(api) -> list[dict]:
    movies, offset = [], 0
    while True:
        page = api.get(f"/movies?limit=100&offset={offset}").json()
        movies += page
        if len(page) < 100:
            return movies
        offset += 100


def apply_posters(api, headers: dict, entries: list[dict]) -> int:
    """PATCH each movie with the looked-up fields. Only known fields are sent, and a
    poster must come from a known image server (the file can't point anywhere else)."""
    done = 0
    for e in entries:
        patch = {k: e[k] for k in ALLOWED_FIELDS if k in e}
        if not str(patch.get("poster_url", "")).startswith(ALLOWED_POSTER_HOSTS):
            print(f"  skipped {e.get('title')}: poster is not from a known image server")
            continue
        for _ in range(20):                      # the API limits each user per second: wait and retry
            r = api.patch(f"/movies/{e['id']}", json=patch, headers=headers)
            if r.status_code != 429:
                break
            time.sleep(float(r.headers.get("Retry-After", "1")))
        if r.status_code == 200:
            done += 1
            print(f"  ok  {e.get('title')}")
        else:
            print(f"  FAILED {e.get('title')}: HTTP {r.status_code}")
    return done


def connect(base: str):
    api = httpx.Client(base_url=base, timeout=30)
    try:
        api.get("/health")
    except httpx.ConnectError:
        sys.exit(f"Can't reach the backend at {base}. Start it first.")
    token = api.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json().get("access_token")
    if not token:
        sys.exit("Admin login failed - run scripts/seed_demo.py first.")
    return api, {"Authorization": f"Bearer {token}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["export", "apply", "all"])
    ap.add_argument("file", nargs="?", default=None)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--all-movies", action="store_true")
    ap.add_argument("--language", help="only handle movies in this language, e.g. Telugu")
    ap.add_argument("--source", choices=["omdb", "tmdb"], default="omdb", help="where `all` looks movies up")
    a = ap.parse_args()

    api, headers = connect(a.base)
    if a.command == "export":
        movies = [m for m in list_movies(api) if (a.all_movies or not m["poster_url"]) and (not a.language or m["language"] == a.language)]
        out = a.file or "movies.json"
        with open(out, "w") as f:
            json.dump([{k: m[k] for k in ("id", "title", "language", "genre")} for m in movies], f, indent=2)
        print(f"Wrote {len(movies)} movies to {out}.\nNext: run scripts/tmdb_lookup.py on a device where TMDB opens.")
    elif a.command == "apply":
        if not a.file:
            sys.exit("Usage: fetch_posters.py apply posters.json")
        data = json.load(open(a.file))
        n = apply_posters(api, headers, data["posters"])
        print(f"\nUpdated {n} movies.")
        if data.get("unmatched"):
            print(f"{len(data['unmatched'])} had no confident match (left as they were; set them in the Admin page):")
            for t in data["unmatched"]:
                print("   -", t)
    else:
        lookup = __import__("omdb_lookup" if a.source == "omdb" else "tmdb_lookup")
        key = os.environ.get("OMDB_API_KEY" if a.source == "omdb" else "TMDB_API_KEY", "").strip()
        if not key:
            sys.exit(f"Set {'OMDB' if a.source == 'omdb' else 'TMDB'}_API_KEY first.")
        movies = [m for m in list_movies(api) if (a.all_movies or not m["poster_url"]) and (not a.language or m["language"] == a.language)]
        found, unmatched = lookup.lookup_movies(movies, key)
        print(f"\nUpdated {apply_posters(api, headers, found)} movies; {len(unmatched)} not matched (left as they were):")
        for t in unmatched:
            print("   -", t)


if __name__ == "__main__":
    main()
