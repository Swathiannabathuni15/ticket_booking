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

**Real posters (optional).** Posters are drawn from the movie title by default. To get the real ones with IMDb data, get a free key at omdbapi.com/apikey.aspx (click the activation link in the email, the key doesn't work before that), then run:
```
OMDB_API_KEY=your-key python scripts/fetch_posters.py all --source omdb --language Telugu
```
`--language Telugu` keeps it to the real Telugu releases. The made-up demo movies (Midnight Express and so on) share names with real films, so leave them alone.
It also fills in the IMDb rating, runtime and genre. It only uses a poster when the match is certain, and lists the ones it was unsure about, so you can set those by hand in the Admin page. Small regional films are often missing from OMDb. The posters are linked from IMDb's image server, which is fine for a personal demo project but is not licensed for a commercial product. (TMDB works too: `--source tmdb`, but some networks block it.)

**6. Start the website** (another new terminal)
```
cd frontend
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
- **Turn on the AI assistant.** Put one line in a file called `.env` in the project folder, then restart the backend:
  ```
  GEMINI_API_KEY=your-key-here
  ```
  (Free key from Google AI Studio. `ANTHROPIC_API_KEY=...` works too. If both are set, Gemini is used.) Without a key, the chat says the assistant is not set up. `.env` is never committed to git.

## Use it on your phone

The website is built for phones too (two-column movie grid, swipe-sideways seat map, pop-ups that slide up, big tap buttons).

To open it from a phone on the **same Wi-Fi** as your laptop:

1. Find the laptop's address: `hostname -I` (use the first one, like `192.168.1.20`).
2. Start the backend so it can be reached from other devices (instead of the normal command):
   ```
   uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0
   ```
3. Start the website for the network:
   ```
   cd frontend
   npm run dev:phone
   ```
4. On the phone open `http://192.168.1.20:5173` (your address, port 5173).

If it doesn't open: some office or guest Wi-Fi blocks devices from talking to each other, and a firewall on the laptop can block ports 5173 and 8000. A phone hotspot shared with the laptop works around the first problem. Only use this on a network you trust.

Notifications are the one thing that does **not** work this way: browsers only allow them on `https://` addresses, so a phone over plain `http://192.168...` can't get them (see "Things to know").

## Send real sign-in codes (optional)

Out of the box the code is shown on screen (demo mode). To really send it, put settings in your `.env` file and restart the backend. Set `OTP_DEV_MODE=false` once real sending works, so the code is no longer shown on screen.

**By email** (free, easiest). A Gmail account works if you turn on 2-step verification and create an "app password" (Google Account > Security > App passwords):
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-16-letter-app-password
SMTP_FROM=you@gmail.com
```
On the sign-in window choose "Continue with Email" then "Email me a code instead".

**By SMS** needs a paid account with an SMS company:
```
OTP_SMS_PROVIDER=twilio
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_FROM=+1xxxxxxxxxx
```
or, for India:
```
OTP_SMS_PROVIDER=msg91
MSG91_AUTH_KEY=...
MSG91_TEMPLATE_ID=...
```
Be honest with yourself about these: a Twilio trial account only texts numbers you have verified, and in India MSG91 (and other companies) require an approved message template (called DLT). The SMS code is written from their public guides but has **not** been run against a real account here. The email code was tested against a real mail-server connection (a local one).

Codes work once, last 5 minutes, allow 5 wrong tries, and you can ask for at most 3 per minute.

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
- **Sign-in codes are shown on screen** until you set up an email or SMS provider (see above).
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
