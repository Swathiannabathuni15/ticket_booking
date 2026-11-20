"""Seed demo data through the real HTTP API (so it exercises the same paths
the UI does). Usage:  python scripts/seed_demo.py [http://localhost:8000]

Creates an admin (admin@demo-cinema.com / demo-password-1), 6 movies and, in 5 cities,
a theatre with a 272-seat screen (3 price tiers, aisles) and 3 showtimes a day for 3 days.
Safe to re-run: it adds another set each time.
"""

import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

sys.path.insert(0, ".")

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@demo-cinema.com")
PASSWORD = os.environ.get("ADMIN_PASSWORD") or "demo-password-1"  # set ADMIN_PASSWORD for any public server


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
    c = patient(httpx.Client(base_url=BASE, timeout=30))
    try:
        c.get("/health")
    except httpx.ConnectError:
        sys.exit(
            f"Can't reach the backend at {BASE}.\n"
            "Start it first, in another terminal:\n"
            "    source .venv/bin/activate\n"
            "    uvicorn app.main:app --reload --reload-dir app\n"
            "then run this script again."
        )
    c.post("/auth/signup", json={"email": EMAIL, "password": PASSWORD})
    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as db:
        u = db.query(User).filter(User.email == EMAIL).one()
        u.is_admin = True
        db.commit()
    token = c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    existing = {m["title"] for m in c.get("/movies?limit=100").json()}
    movies = [
        ("Monsoon Melody", 142, "Romance/Musical", "Telugu", 8.7),
        ("Paper Lanterns", 98, "Animation/Family", "English", 8.9),
        ("Iron Harbour", 126, "Action/Crime", "Tamil", 8.2),
        ("Neon Nights", 117, "Thriller/Mystery", "Hindi", 9.1),
        ("The Salt Road", 134, "Adventure/Drama", "Hindi", 8.4),
        ("Cosmic Kitchen", 101, "Comedy/Sci-Fi", "English", 7.9),
    ]
    movie_ids = []
    for title, mins, genre, lang, rating in movies:
        if title in existing:
            print("skip (exists):", title)
            continue
        m = c.post(
            "/movies",
            json={"title": title, "duration_minutes": mins, "genre": genre, "language": lang, "rating": rating},
            headers=h,
        ).json()
        movie_ids.append(m["id"])

    # A real hall: 17 rows, 3 price tiers, two aisles per row (3 | 10 | 3 seats).
    def row(letter, kind):
        return {"row": letter, "seats": [kind] * 3 + [None] + [kind] * 10 + [None] + [kind] * 3}

    rows = (
        [row(letter, "platinum") for letter in "ABC"]
        + [row(letter, "gold") for letter in "DEFGHIJKL"]
        + [row(letter, "silver") for letter in "MNOPQ"]
    )
    cities = {
        "Hyderabad": "AMB Cinemas",
        "Mumbai": "Regal Grand",
        "Bengaluru": "Phoenix Multiplex",
        "Chennai": "Marina Screens",
        "Delhi-NCR": "Capital Cinemas",
    }
    day0 = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for city, name in cities.items() if movie_ids else []:
        theatre = c.post("/theatres", json={"name": name, "city": city}, headers=h).json()
        screen = c.post(
            "/screens", json={"theatre_id": theatre["id"], "name": "Screen 1 (Laser)", "rows": rows}, headers=h
        ).json()
        n = 0
        for day in range(3):
            for k, mid in enumerate(movie_ids):
                for hour in (11 + k % 3, 15 + k % 2, 20):
                    st = c.post(
                        "/showtimes",
                        json={
                            "movie_id": mid,
                            "screen_id": screen["id"],
                            "starts_at": (day0 + timedelta(days=day + 1, hours=hour - day0.hour)).isoformat(),
                            "prices": {"platinum": 350, "gold": 295, "silver": 190},
                        },
                        headers=h,
                    )
                    n += st.status_code == 201
        print(f"{city}: {name}, {n} showtimes")
    print(f"admin login: {EMAIL} / {PASSWORD}")


main()
