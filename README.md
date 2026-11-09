# Ticket Booking

A movie ticket booking website. You pick a city, pick a movie, pick your seats, and pay.

**The one big promise: the same seat can never be sold to two people**, even when hundreds of people press "book" at the same second.

Built with Python (FastAPI), React, PostgreSQL and Redis.

## The extra things we added

These are the parts that go beyond a basic booking site.

1. **Split the bill with friends.** After you pay, send each friend a link. They pay their share without making an account. Shares always add up to the exact total, and if you cancel, friends who already paid get their money back.
2. **A payment that is safe even if the network breaks.** Every payment attempt gets its own unique ID. If your phone switches off right after you press Pay, you are never charged twice, and the bank tells our server the result by itself, so your booking still gets confirmed.
3. **See the view from your seat.** Hover over a seat (or long-press on a phone) to see a picture of what the screen looks like from there.
4. **Smart seat picking.** Choose how many seats you need, click once, and a group of seats next to each other is selected. Or press "Best available" and we pick good ones for you.
5. **An AI assistant that can't spend your money.** Ask it to find a movie or hold seats. It has no way to pay, so even a trick can't make it charge you.
6. **Fair rate limits.** Limits are counted per person (email, phone number or account), not per internet address. Too many tries gets a "slow down" reply.
7. **Proven under load.** We tested 1,000 users fighting for 260 seats. Zero seats were double booked.
8. **Real notifications before your movie.** Turn on reminders once and you get a system notification (like any app) 15 minutes before a show you paid for: "Have you reached the theatre?", with Directions and "I'm here" buttons. It works even when the website is closed.
9. **Directions to the theatre.** One tap opens Google Maps with the route from where you are, and a small map shows on your confirmed booking. No API key needed.
10. **Stay signed in.** Sign in once and you stay signed in for 30 days. Every time you open the site the 30 days start again.

Also included: city picker, movie posters, Telugu movies, email or phone-code sign in, a small admin area, and a white page with a soft moving background.

## How to run it

You need Python 3.12, Node 22, PostgreSQL and Redis (Docker is the easy way to get the last two).

Open a terminal in the project folder and do these steps in order.

**1. Install Python packages**
```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**2. Start PostgreSQL and Redis**
```
docker compose up -d
```
No Docker? Start them yourself and create two databases: `ticketdb` and `ticketdb_test`.

**3. Create the tables**
```
alembic upgrade head
```

**4. Start the backend** (leave this terminal open)
```
uvicorn app.main:app --reload --reload-dir app
```
It is working if http://localhost:8000/docs opens.

**5. Add demo movies** (new terminal, backend must be running)
```
source .venv/bin/activate
python scripts/seed_demo.py
python scripts/seed_telugu.py
```

**Real posters (optional).** Posters are drawn from the movie title by default. To get the real ones, make a free account at themoviedb.org, create an API key, then run:
```
TMDB_API_KEY=your-key python scripts/fetch_posters.py
```
It also fills in real runtimes, genres and ratings. It only uses a poster when the match is certain, and lists the ones it was unsure about, so you can set those by hand in the Admin page. (We don't copy posters from BookMyShow or Google. They are copyrighted, so we use TMDB, which allows this.)

**6. Start the website** (another new terminal)
```
cd frontend
echo "VITE_API_URL=http://localhost:8000" > .env
npm install
npm run dev
```

**7. Open http://localhost:5173**

## Try it

1. Pick a city, then a movie and a showtime.
2. Choose how many seats, then click a seat.
3. Press **Pay**, sign in (with a phone number, the code is shown on screen), and pay.
4. On the payment page, try the **Demo card** menu. "Bank charges, but the reply is lost" shows the safe-payment feature: the page says "Confirming..." and a few seconds later the booking confirms by itself.
5. Press **Split the bill**, add friends, and open a link in a private window to pay as a friend.
6. Try the notification: click your email at the top right, press **Turn on ticket reminders**, allow it, then press **Send a test**. For the real thing, run `python scripts/demo_reminder.py` (makes a show starting in 12 minutes), book and pay for it, and make sure the two programs above are running. A notification appears when it is 15 minutes away.
7. Admin login: `admin@demo-cinema.com` / `demo-password-1`.

## Optional

- **Cancel unpaid bookings and send the "movie starts soon" notifications.** Both need these two programs running, each in its own terminal:
  ```
  python -m app.jobs.scheduler_loop
  python -m app.jobs.worker
  ```
- **Turn on the AI assistant.** Put `ANTHROPIC_API_KEY=your-key` in a file called `.env` in the project folder, then restart the backend. Without it, the chat says the assistant is not set up.

## Tests

```
python -m pytest -q        # backend
cd frontend && npm test    # frontend
```

## Load test result

1,000 users, 260 seats, on one laptop that also ran the test tools:

| Test | Slowest 5% of requests | Errors | Double bookings |
|---|---|---|---|
| Users arrive over 30 seconds | 67 ms | 0 | **0** |
| All 1,000 users at the same instant | 12.5 s | some "busy" replies | **0** |

The second test mostly shows one laptop running out of power. To run it yourself, see `scripts/loadtest.py`.

## Things to know (being honest)

- **Payments are pretend.** The bank is a simulation. The safety logic is real, but it has only been tested against the simulation, not a real bank.
- **Google and Apple sign in** buttons are shown but turned off. They need keys we don't have.
- **Phone sign in is a demo.** No SMS is sent. The code appears on screen.
- **Telugu movie list** comes from public release calendars, not from BookMyShow (it blocks automatic reading). Dates in those calendars disagree. Genres and ratings are blank where we didn't know them, and runtimes are placeholders.
- **Posters are generated** from the movie title until you run the TMDB poster script (needs your own free key) or an admin pastes an image link.
- **Notifications on a phone need https.** Browsers only allow them on `https://` addresses (and `localhost`, which is only your own computer). So they work on this laptop now, but a phone can only get them after the site is put online with https. On an iPhone you must also "Add to Home Screen" first. If notifications are not turned on, a small popup appears instead while the site is open.
- **This is not SMS.** Texting a phone number needs a paid SMS service.
- **Directions** find the theatre by its name and city using Google Maps. A very new or oddly named theatre may need its name fixed.
- **The "view from seat" picture** is an illustration, not a real photo of the cinema.
- **Visitors who aren't signed in** are not rate limited on public pages, because there is no account to count.
- **The Docker files** for running everything together were written but never tried.
- The website has been tested by code, but not carefully clicked through by a person.

## Problems?

| Problem | Try this |
|---|---|
| No movies show up | Did you run the two seed commands? Is the backend running? Did you pick a city that has shows? |
| Website won't load | Stop it (Ctrl+C) and run `npm run dev` again. |
| Seat holds fail | Redis is not running. |
| Can't connect to database | PostgreSQL is not running. |
| TMDB site or poster script "can't be reached" | Some internet providers block TMDB. Change your DNS to 1.1.1.1 (Settings > Network > your Wi-Fi > IPv4 > DNS) or turn on Secure DNS in Chrome, or use a phone hotspot. |
| Chat says "not configured" | Add `ANTHROPIC_API_KEY` as described above. |
