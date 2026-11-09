// k6 version of the booking race. Run:  k6 run -e BASE=http://localhost:8000 -e TOKENS_FILE=tokens.json loadtest/k6.js
// tokens.json: a JSON array of pre-minted JWTs (one per virtual user) and SHOWTIME_ID must exist -
// scripts/loadtest.py shows how to create users, tokens and the showtime.
import http from "k6/http";
import { check } from "k6";
import { SharedArray } from "k6/data";

const BASE = __ENV.BASE || "http://localhost:8000";
const SHOWTIME = __ENV.SHOWTIME_ID;
const tokens = new SharedArray("tokens", () => JSON.parse(open(__ENV.TOKENS_FILE || "tokens.json")));

export const options = {
  scenarios: { race: { executor: "per-vu-iterations", vus: 1000, iterations: 1, maxDuration: "2m" } },
  thresholds: { http_req_duration: ["p(95)<1500"], "http_req_failed{expected:true}": ["rate<0.01"] },
};

export default function () {
  const h = { Authorization: `Bearer ${tokens[(__VU - 1) % tokens.length]}`, "Content-Type": "application/json" };
  for (let attempt = 0; attempt < 4; attempt++) {
    const seats = http.get(`${BASE}/showtimes/${SHOWTIME}/seats`, { headers: h }).json();
    const free = seats.filter((s) => s.status === "available");
    if (!free.length) return;
    const seat = free[Math.floor(Math.random() * free.length)].id;
    const hold = http.post(`${BASE}/holds`, JSON.stringify({ seat_ids: [seat] }), { headers: h, responseCallback: http.expectedStatuses(201, 409) });
    if (hold.status !== 201) continue;
    const book = http.post(`${BASE}/bookings`, JSON.stringify({ seat_ids: [seat] }), { headers: { ...h, "Idempotency-Key": `${__VU}-${attempt}` } });
    if (!check(book, { booked: (r) => r.status === 201 })) return;
    http.post(`${BASE}/payments`, JSON.stringify({ booking_id: book.json("id") }), { headers: { ...h, "Idempotency-Key": `pay-${__VU}` } });
    return;
  }
}
