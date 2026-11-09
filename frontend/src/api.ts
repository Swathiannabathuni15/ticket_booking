// API client: token handling + one place that turns HTTP failures into messages.
const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "tb_token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter?: number) {
    super(message);
  }
}

/** Fired on any 401 while logged in, so the app can drop the session and go to /login. */
export const SESSION_EXPIRED = "tb:session-expired";

export function friendlyMessage(status: number, detail: string, retryAfter?: number): string {
  switch (status) {
    case 401:
      return detail || "Please log in again.";
    case 409:
      return detail || "That conflicts with the current state - refresh and try again.";
    case 429:
      return `Too many requests - try again${retryAfter ? ` in ${retryAfter}s` : " shortly"}.`;
    case 503:
      // The server's own reason (e.g. "The assistant is not configured") beats a generic line.
      return detail || "The service is temporarily unavailable. Please try again in a moment.";
    default:
      return detail || `Something went wrong (${status}).`;
  }
}

function detailOf(body: unknown): string {
  const d = (body as { detail?: unknown } | null)?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e) => (e as { msg?: string }).msg ?? "invalid input").join("; ");
  return "";
}

export async function request<T>(
  method: string,
  path: string,
  opts: { body?: unknown; headers?: Record<string, string>; auth?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = { ...opts.headers };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const token = tokenStore.get();
  if (token && opts.auth !== false) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const retry = Number(res.headers.get("Retry-After")) || undefined;
    if (res.status === 401 && token && opts.auth !== false) {
      tokenStore.clear();
      window.dispatchEvent(new Event(SESSION_EXPIRED));
    }
    throw new ApiError(res.status, friendlyMessage(res.status, detailOf(body), retry), retry);
  }
  return body as T;
}

export const uuid = () => crypto.randomUUID();
/** Demo cards: the bank approves, declines, or charges but the reply is lost. */
export type PayMethod = "tok_ok" | "tok_decline" | "tok_timeout";

// ---- types ----
export type User = { id: number; email: string | null; phone: string | null; is_admin: boolean };
export type Movie = {
  id: number; title: string; duration_minutes: number;
  poster_url: string | null; genre: string; language: string; rating: number | null;
};
export type Theatre = { id: number; name: string; city: string };
export type Screen = { id: number; theatre_id: number; name: string; layout: unknown[] };
export type Showtime = {
  id: number; movie_id: number; screen_id: number; starts_at: string;
  movie_title: string; screen_name: string; theatre_name: string; theatre_id: number; city: string;
};
export type SeatStatus = "available" | "held" | "held_by_me" | "booked";
export type Seat = {
  id: number; label: string; seat_type: string; price: number; status: SeatStatus; available: boolean;
};
export type Booking = {
  id: number; showtime_id: number; seat_ids: number[]; seat_labels: string[];
  movie_title: string; theatre_name: string; city: string; starts_at: string; total_cents: number;
  status: "pending_payment" | "confirmed" | "cancelled";
  /** paid | processing (waiting for the bank / webhook) | failed | unpaid */
  payment_status: "paid" | "processing" | "failed" | "unpaid";
  created_at: string; payment_due_at: string | null; paid_at: string | null; user_id?: number;
};
export type Share = {
  id: number; name: string; amount_cents: number; is_owner: boolean;
  status: "unpaid" | "paid" | "cancelled"; paid_at: string | null; link: string | null;
};
export type Split = {
  id: number; booking_id: number; total_cents: number; status: string; paid_cents: number; shares: Share[];
};
export type ShareView = {
  name: string; amount_cents: number; status: string; movie_title: string; starts_at: string; seats: string[];
};

// ---- endpoints ----
export const api = {
  signup: (email: string, password: string) =>
    request<User>("POST", "/auth/signup", { body: { email, password }, auth: false }),
  login: (email: string, password: string) =>
    request<{ access_token: string }>("POST", "/auth/login", { body: { email, password }, auth: false }),
  me: () => request<User>("GET", "/me"),
  refresh: () => request<{ access_token: string }>("POST", "/auth/refresh"),
  otpRequest: (phone: string) =>
    request<{ expires_in_seconds: number; dev_otp: string | null }>("POST", "/auth/otp/request", { body: { phone }, auth: false }),
  otpVerify: (phone: string, code: string) =>
    request<{ access_token: string }>("POST", "/auth/otp/verify", { body: { phone, code }, auth: false }),
  cities: () => request<string[]>("GET", "/cities", { auth: false }),
  movies: (city?: string | null) =>
    request<Movie[]>("GET", `/movies?limit=100${city ? `&city=${encodeURIComponent(city)}` : ""}`, { auth: false }),
  showtimes: (opts: { movieId?: number; city?: string | null } = {}) =>
    request<Showtime[]>(
      "GET",
      `/showtimes?limit=100${opts.movieId ? `&movie_id=${opts.movieId}` : ""}${opts.city ? `&city=${encodeURIComponent(opts.city)}` : ""}`,
      { auth: false },
    ),
  showtime: (id: number) => request<Showtime>("GET", `/showtimes/${id}`, { auth: false }),
  bestSeats: (showtimeId: number, count: number, seatType?: string) =>
    request<Seat[]>("GET", `/showtimes/${showtimeId}/best-seats?count=${count}${seatType ? `&seat_type=${seatType}` : ""}`),
  seats: (showtimeId: number) => request<Seat[]>("GET", `/showtimes/${showtimeId}/seats`),
  hold: (seatIds: number[]) =>
    request<{ seat_ids: number[]; ttl_seconds: number }>("POST", "/holds", { body: { seat_ids: seatIds } }),
  release: (seatId: number) => request<void>("DELETE", `/holds/${seatId}`),
  book: (seatIds: number[], key: string) =>
    request<Booking>("POST", "/bookings", { body: { seat_ids: seatIds }, headers: { "Idempotency-Key": key } }),
  myBookings: () => request<Booking[]>("GET", "/bookings/me"),
  cancelBooking: (id: number) => request<Booking>("DELETE", `/bookings/${id}`),
  pay: (bookingId: number, key: string, method: PayMethod = "tok_ok") =>
    request<Booking>("POST", "/payments", {
      body: { booking_id: bookingId, payment_method: method },
      headers: { "Idempotency-Key": key },
    }),
  createSplit: (
    id: number,
    body: { friends: string[]; include_owner: boolean; mode: "equal" | "custom"; custom_cents?: number[] },
  ) => request<Split>("POST", `/bookings/${id}/split`, { body }),
  getSplit: (id: number) => request<Split>("GET", `/bookings/${id}/split`),
  cancelSplit: (id: number) => request<Split>("DELETE", `/bookings/${id}/split`),
  viewShare: (token: string) => request<ShareView>("GET", `/pay/share/${token}`, { auth: false }),
  payShare: (token: string) => request<Share>("POST", `/pay/share/${token}`, { auth: false }),
  chat: (message: string) => request<{ reply: string }>("POST", "/agent/chat", { body: { message } }),
  pushPublicKey: () => request<{ public_key: string }>("GET", "/push/public-key", { auth: false }),
  pushSubscribe: (sub: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<{ subscribed: boolean }>("POST", "/push/subscribe", { body: sub }),
  pushUnsubscribe: (endpoint: string) => request<{ subscribed: boolean }>("DELETE", "/push/subscribe", { body: { endpoint } }),
  pushTest: () => request<{ sent: number }>("POST", "/push/test"),
  // admin
  adminBookings: (status?: string) =>
    request<Booking[]>("GET", `/admin/bookings?limit=200${status ? `&status=${status}` : ""}`),
  screens: () => request<Screen[]>("GET", "/screens", { auth: false }),
  createMovie: (body: { title: string; duration_minutes: number; genre?: string; language?: string; rating?: number; poster_url?: string }) =>
    request<Movie>("POST", "/movies", { body }),
  createShowtime: (body: { movie_id: number; screen_id: number; starts_at: string; prices: Record<string, number> }) =>
    request<Showtime>("POST", "/showtimes", { body }),
};

export const CUR = "₹";
export const money = (cents: number) => `${CUR}${(cents / 100).toFixed(2)}`;
export const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
