"""Add recent / current Telugu releases and Hyderabad theatres for them.

  python scripts/seed_telugu.py [http://localhost:8000]      (backend must be running)

WHERE THE TITLES COME FROM
    Public release listings (Filmibeat's October/November 2026 calendars) and
    the Hyderabad page you showed from BookMyShow. BookMyShow itself blocks
    automated access, so this is NOT a copy of its live listing, and release
    dates in those sources conflict - check before relying on any title.
WHAT IS NOT KNOWN
    Genre is filled in only where it was seen; ratings are left empty (not
    invented); every runtime is a PLACEHOLDER (150 min). Posters are generated
    from the title.
Safe to re-run: titles that already exist are skipped.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, ".")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@demo-cinema.com")
PASSWORD = os.environ.get("ADMIN_PASSWORD") or "demo-password-1"   # set ADMIN_PASSWORD for any public server

# (title, genre-if-seen)
MOVIES = [
    ("Aadarsha Kutumbam House No: 47 (AK 47)", "Action/Comedy/Family/Thriller"),
    ("Summer Holidays", "Adventure/Drama/Kids"),
    ("Cult", ""), ("Haiku", ""), ("418", ""), ("Garividi Lakshmi", ""), ("Ranabaali", ""),
    ("Crazy Kalyanam", ""), ("KillR", ""), ("GOAT", ""), ("Idupu Kayitham", ""), ("Itllu Arjuna", ""),
    ("Bhogi", ""), ("Gummadi Narsaiah", ""), ("Narendra", ""), ("Takshakudu", ""), ("Band Baaja", ""),
    ("Magic", ""), ("Vankarodu", ""), ("Mike Testing 143", ""), ("Driver Ramudu", ""), ("Chandrayan", ""),
    ("Donga Naa Koduku", ""), ("Question Mark", ""), ("Seethayanam", ""), ("Police Vari Hecharika", ""),
    ("Vaddura Sodharaa", ""), ("SDT 19", ""), ("Sigma", ""), ("SI Yugandhar", ""), ("Anakapalli", ""),
    ("Comrade Kalyan", ""),
]
THEATRES = ["Prasads Multiplex", "Sudha Cinemas", "Asian Cinemas"]
PLACEHOLDER_MINUTES = 150


def patient(client: httpx.Client) -> httpx.Client:
    """The API limits each signed-in user per second. When a seed script goes
    faster than that it gets 429 + Retry-After: wait that long and try again."""
    import time
    orig = client.request

    def request(method, url, **kw):
        for _ in range(30):
            r = orig(method, url, **kw)
            if r.status_code != 429:
                return r
            time.sleep(float(r.headers.get("Retry-After", "1")))
        return r
    client.request = request  # get/post call request() under the hood
    return client


from _safety import require_real_admin_password  # noqa: E402


def main():
    require_real_admin_password(BASE, PASSWORD)
    c = patient(httpx.Client(base_url=BASE, timeout=60))
    try:
        c.get("/health")
    except httpx.ConnectError:
        sys.exit(f"Can't reach the backend at {BASE}. Start it first (see README, Step 4).")
    token = c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json().get("access_token")
    if not token:
        sys.exit("Admin login failed - run scripts/seed_demo.py first (it creates the admin).")
    h = {"Authorization": f"Bearer {token}"}

    def all_pages(path):
        """Read every row of a list endpoint. Some list endpoints ignore
        ?offset (they return everything every time), so stop on a short page
        or when a page adds nothing new - never loop forever."""
        out, seen, offset = [], set(), 0
        while True:
            page = c.get(f"{path}{'&' if '?' in path else '?'}limit=100&offset={offset}").json()
            fresh = [row for row in page if row["id"] not in seen]
            if not fresh:
                return out
            seen |= {row["id"] for row in fresh}
            out += fresh
            if len(page) < 100:
                return out
            offset += 100

    by_title = {m["title"]: m["id"] for m in all_pages("/movies")}
    created = 0
    for title, genre in MOVIES:
        if title in by_title:
            continue
        r = c.post("/movies", json={"title": title, "duration_minutes": PLACEHOLDER_MINUTES, "genre": genre,
                                    "language": "Telugu"}, headers=h)
        if r.status_code == 201:
            by_title[title] = r.json()["id"]
            created += 1
    # every listed movie that has no showtimes yet (new, or left over from an interrupted run)
    new_ids = [by_title[t] for t, _ in MOVIES if t in by_title and not c.get(f"/showtimes?movie_id={by_title[t]}&limit=1").json()]
    print(f"{created} new Telugu movies; {len(new_ids)} need showtimes")
    if not new_ids:
        return

    # a 144-seat hall: 12 rows, 3 tiers, aisles (2 | 8 | 2)
    def row(letter, kind):
        return {"row": letter, "seats": [kind] * 2 + [None] + [kind] * 8 + [None] + [kind] * 2}
    rows = [row(l, "premium") for l in "ABC"] + [row(l, "gold") for l in "DEFGH"] + [row(l, "standard") for l in "IJKL"]
    screens = []
    known = {(t["name"], t["city"]): t["id"] for t in all_pages("/theatres")}
    screen_by_theatre = {}
    for sc in all_pages("/screens"):
        screen_by_theatre.setdefault(sc["theatre_id"], sc["id"])
    for name in THEATRES:
        tid = known.get((name, "Hyderabad")) or c.post("/theatres", json={"name": name, "city": "Hyderabad"}, headers=h).json()["id"]
        sid = screen_by_theatre.get(tid) or c.post("/screens", json={"theatre_id": tid, "name": "Screen 1", "rows": rows}, headers=h).json()["id"]
        screens.append(sid)

    day0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    made = 0
    for i, mid in enumerate(new_ids):
        for k in range(2):                       # two theatres per movie
            screen = screens[(i + k) % len(screens)]
            for day in (1, 2):                   # tomorrow and the day after
                for hour in (10 + k, 14 + k, 19 + k):
                    start = day0 + timedelta(days=day, hours=hour - day0.hour)
                    r = c.post("/showtimes", json={"movie_id": mid, "screen_id": screen, "starts_at": start.isoformat(),
                                                   "prices": {"premium": 250, "gold": 200, "standard": 150}}, headers=h)
                    made += r.status_code == 201
        print(f"  {i + 1}/{len(new_ids)}", end="\r", flush=True)
    print(f"\ncreated {made} showtimes in {len(THEATRES)} Hyderabad theatres")


main()
