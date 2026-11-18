"""Real posters, runtimes, genres and ratings from TMDB (themoviedb.org).

Get a free key at https://www.themoviedb.org/settings/api and put it in your .env file as
TMDB_API_KEY=... (or put it in front of the command). Then, everything in one go (the backend
must be running, and TMDB must open from this machine):

     python scripts/fetch_posters.py all --language Telugu

Some networks block or reset connections to TMDB. If that is you, do it in three steps, so only
the lookup has to run somewhere TMDB opens (a personal computer, or Google Colab in a browser):

  1. Where the app runs:   python scripts/fetch_posters.py export            -> movies.json
  2. Anywhere TMDB opens:  TMDB_API_KEY=... python scripts/tmdb_lookup.py movies.json posters.json
  3. Where the app runs:   python scripts/fetch_posters.py apply posters.json

Only confident matches are used (same title, right language, a 2026 release); anything unsure is
listed at the end and left alone, so set those by hand in the Admin page.

Options: --base http://localhost:8000   --all-movies (include ones that already have a poster)
         --language Telugu (only that language: the made-up demo movies share names with real films)
Posters are linked from TMDB's image server, not copied. TMDB asks apps to show:
"This product uses the TMDB API but is not endorsed or certified by TMDB." (the website footer does).
"""

import argparse
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@demo-cinema.com")
PASSWORD = os.environ.get("ADMIN_PASSWORD") or "demo-password-1"  # set ADMIN_PASSWORD for any public server
ALLOWED_FIELDS = ("poster_url", "duration_minutes", "genre", "rating")
# A poster link in a result file may only point at TMDB's image server.
ALLOWED_POSTER_HOSTS = ("https://image.tmdb.org/",)


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
    poster must come from TMDB's image server (the file can't point anywhere else)."""
    done = 0
    for e in entries:
        patch = {k: e[k] for k in ALLOWED_FIELDS if k in e}
        if not str(patch.get("poster_url", "")).startswith(ALLOWED_POSTER_HOSTS):
            print(f"  skipped {e.get('title')}: poster is not from TMDB's image server")
            continue
        for _ in range(20):  # the API limits each user per second: wait and retry
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


from _safety import require_real_admin_password  # noqa: E402


def connect(base: str):
    require_real_admin_password(base, PASSWORD)
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
    a = ap.parse_args()

    api, headers = connect(a.base)

    def wanted(m):
        return (a.all_movies or not m["poster_url"]) and (not a.language or m["language"] == a.language)

    if a.command == "export":
        movies = [m for m in list_movies(api) if wanted(m)]
        out = a.file or "movies.json"
        with open(out, "w") as f:
            json.dump([{k: m[k] for k in ("id", "title", "language", "genre")} for m in movies], f, indent=2)
        print(f"Wrote {len(movies)} movies to {out}.\nNext: run scripts/tmdb_lookup.py on a device where TMDB opens.")
    elif a.command == "apply":
        if not a.file:
            sys.exit("Usage: fetch_posters.py apply posters.json")
        with open(a.file) as f:
            data = json.load(f)
        n = apply_posters(api, headers, data["posters"])
        print(f"\nUpdated {n} movies.")
        if data.get("unmatched"):
            print(f"{len(data['unmatched'])} had no confident match (left as they were; set them in the Admin page):")
            for t in data["unmatched"]:
                print("   -", t)
    else:
        import tmdb_lookup as lookup

        lookup._prefer_ipv4()
        key = lookup._env("TMDB_API_KEY")
        if not key:
            sys.exit("Set TMDB_API_KEY first (free key: https://www.themoviedb.org/settings/api).")
        movies = [m for m in list_movies(api) if wanted(m)]
        found, unmatched = lookup.lookup_movies(movies, key)
        print(
            f"\nUpdated {apply_posters(api, headers, found)} movies; {len(unmatched)} not matched (left as they were):"
        )
        for t in unmatched:
            print("   -", t)


if __name__ == "__main__":
    main()
