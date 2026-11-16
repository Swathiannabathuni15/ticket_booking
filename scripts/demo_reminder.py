"""Create a showtime that starts in a few minutes, so you can try the
"your movie starts soon - have you reached the theatre?" popup.

  python scripts/demo_reminder.py            # starts in 12 minutes
  python scripts/demo_reminder.py 20         # starts in 20 minutes

Then: open the printed link, book seats, pay. Within a minute of the show being
15 minutes away, the popup appears (keep the website open in a tab).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import httpx

BASE = "http://localhost:8000"
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@demo-cinema.com")
PASSWORD = os.environ.get("ADMIN_PASSWORD") or "demo-password-1"   # set ADMIN_PASSWORD for any public server
MINUTES = int(sys.argv[1]) if len(sys.argv) > 1 else 12


from _safety import require_real_admin_password  # noqa: E402


def main():
    require_real_admin_password(BASE, PASSWORD)
    c = httpx.Client(base_url=BASE, timeout=30)
    try:
        c.get("/health")
    except httpx.ConnectError:
        sys.exit("Start the backend first.")
    token = c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json().get("access_token")
    if not token:
        sys.exit("Admin login failed - run scripts/seed_demo.py first.")
    h = {"Authorization": f"Bearer {token}"}
    movie = next((m for m in c.get("/movies?limit=100&city=Hyderabad").json() if m["language"] == "Telugu"), None)
    show = c.get("/showtimes?limit=1&city=Hyderabad").json()
    if not movie or not show:
        sys.exit("No Hyderabad movies yet - run scripts/seed_demo.py and scripts/seed_telugu.py first.")
    starts = datetime.now(timezone.utc) + timedelta(minutes=MINUTES)
    r = c.post("/showtimes", json={"movie_id": movie["id"], "screen_id": show[0]["screen_id"],
                                   "starts_at": starts.isoformat(),
                                   "prices": {"premium": 250, "gold": 200, "standard": 150,
                                              "platinum": 350, "silver": 190}}, headers=h)
    if r.status_code != 201:
        sys.exit(f"Could not create the showtime: {r.status_code} {r.text}")
    st = r.json()
    print(f"Created: {st['movie_title']} at {st['theatre_name']}, starting in {MINUTES} minutes.")
    print(f"Open: http://localhost:5173/showtimes/{st['id']}   (or the 5174 port, whichever your site uses)")


main()
