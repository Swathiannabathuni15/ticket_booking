import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, CUR, uuid, when, type Seat, type Showtime } from "../api";
import { useAuth } from "../auth";
import SeatMap, { adjacentRun } from "../components/SeatMap";
import SeatView from "../components/SeatView";

// The 3D hall is a big library: it is only downloaded when someone opens it.
const Hall3D = lazy(() => import("../components/Hall3D"));
import { ErrorBanner, Loading, Notice } from "../components/ui";
import { errText, mmss, useCountdown } from "../hooks";

const MAX_SEATS = 8;
const POLL_MS = 4000;

const deadlineKey = (id: number) => `hold-deadline:${id}`;
function loadDeadline(id: number): number | null {
  try {
    const v = Number(sessionStorage.getItem(deadlineKey(id)));
    return v > Date.now() ? v : null;
  } catch { return null; }
}
function saveDeadline(id: number, ms: number | null) {
  try {
    if (ms === null) sessionStorage.removeItem(deadlineKey(id));
    else sessionStorage.setItem(deadlineKey(id), String(ms));
  } catch { /* storage unavailable: the countdown just won't survive a reload */ }
}

function availability(avail: number, total: number): { text: string; cls: string } {
  if (avail === 0) return { text: "SOLD OUT", cls: "soldout" };
  if (avail / total < 0.15) return { text: "ALMOST FULL", cls: "almost" };
  return { text: "AVAILABLE", cls: "ok" };
}

export default function SeatPage() {
  const showtimeId = Number(useParams().id);
  const { user } = useAuth();
  const nav = useNavigate();
  const [seats, setSeats] = useState<Seat[] | null>(null);
  const [showtime, setShowtime] = useState<Showtime | null>(null);
  const [qty, setQty] = useState<number | null>(null); // null => "How many seats?" modal is open
  const [pendingQty, setPendingQty] = useState(2);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [deadline, setDeadline] = useState<number | null>(() => loadDeadline(showtimeId));
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ seat: Seat; sticky: boolean } | null>(null);
  const [hall3d, setHall3d] = useState<Seat | null>(null);
  const bookKey = useRef(uuid()); // same key on a double click => one booking
  const left = useCountdown(deadline);

  const refresh = useCallback(async () => {
    try {
      const fresh = await api.seats(showtimeId);
      setSeats(fresh);
      setSelected((sel) => {
        const ok = new Set([...sel].filter((id) => fresh.find((s) => s.id === id)?.status === "available"));
        if (ok.size !== sel.size) setNotice("Some seats you picked were just taken - please choose again.");
        return ok.size === sel.size ? sel : ok;
      });
    } catch (e) { setError(errText(e)); }
  }, [showtimeId]);

  // Live updates: poll while the tab is visible.
  useEffect(() => {
    refresh();
    const t = setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
    const onVis = () => { if (!document.hidden) refresh(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", onVis); };
  }, [refresh]);

  useEffect(() => {
    api.showtime(showtimeId).then(setShowtime).catch(() => {});
  }, [showtimeId]);

  // Returning with seats still held (reload): skip the modal.
  const mine = useMemo(() => (seats ?? []).filter((s) => s.status === "held_by_me"), [seats]);
  useEffect(() => { if (qty === null && mine.length > 0) setQty(mine.length); }, [mine.length, qty]);

  useEffect(() => {
    if (left === 0 && deadline !== null) {
      setDeadline(null); saveDeadline(showtimeId, null);
      setNotice("Your hold expired. Pick your seats again.");
      refresh();
    }
  }, [left, deadline, showtimeId, refresh]);

  const tiers = useMemo(() => {
    const m = new Map<string, { price: number; avail: number; total: number }>();
    for (const s of seats ?? []) {
      const t = m.get(s.seat_type) ?? { price: s.price, avail: 0, total: 0 };
      t.total++; if (s.status === "available") t.avail++;
      m.set(s.seat_type, t);
    }
    return [...m.entries()];
  }, [seats]);

  if (!seats) return error ? <ErrorBanner message={error} /> : <Loading />;

  const picked = seats.filter((s) => selected.has(s.id));
  const all = [...mine, ...picked];
  const total = all.reduce((sum, s) => sum + s.price, 0);

  async function onSeatClick(seat: Seat) {
    setError(null); setNotice(null);
    if (seat.status === "held_by_me") {
      try { await api.release(seat.id); } catch (e) { setError(errText(e)); }
      refresh(); return;
    }
    if (selected.has(seat.id)) { setSelected(new Set()); return; }  // tap again to clear
    const n = Math.min(qty ?? 1, MAX_SEATS - mine.length);
    const run = n <= 1 ? [seat] : adjacentRun(seats!, seat, n);
    if (run.length === 0) {
      setNotice(`There aren't ${n} seats together starting here. Try another spot, or change the number of seats.`);
      return;
    }
    setSelected(new Set(run.map((s) => s.id)));
  }

  async function bestAvailable() {
    setBusy(true); setError(null);
    try {
      const best = await api.bestSeats(showtimeId, pendingQty);
      setQty(pendingQty);
      setSelected(new Set(best.map((s) => s.id)));
      setNotice(`Best ${pendingQty} seats together: ${best.map((s) => s.label).join(", ")}`);
    } catch (e) { setError(errText(e)); }
    finally { setBusy(false); }
  }

  async function proceed() {
    if (!user) { nav("/login", { state: { from: `/showtimes/${showtimeId}` } }); return; }
    setBusy(true); setError(null);
    try {
      if (selected.size > 0) {
        const { ttl_seconds } = await api.hold([...selected]);
        const ms = Date.now() + ttl_seconds * 1000;
        setDeadline(ms); saveDeadline(showtimeId, ms);
      }
      const booking = await api.book([...mine, ...picked].map((s) => s.id), bookKey.current);
      saveDeadline(showtimeId, null);
      nav(`/checkout/${booking.id}`);
    } catch (e) {
      setError(errText(e));
      if (e instanceof ApiError && e.status === 409) bookKey.current = uuid();
      setSelected(new Set());
      refresh();
    } finally { setBusy(false); }
  }

  return (
    <>
      <div className="show-head">
        <h1>{showtime?.movie_title ?? "Choose your seats"}</h1>
        {showtime && <p className="muted">{showtime.theatre_name} - {showtime.screen_name} | {when(showtime.starts_at)}</p>}
        {qty !== null && <button className="chip" onClick={() => { setPendingQty(qty); setQty(null); setSelected(new Set()); }}>{qty} {qty === 1 ? "Ticket" : "Tickets"} - change</button>}
      </div>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {notice && <Notice>{notice}</Notice>}
      {!user && <Notice><Link to="/login" state={{ from: `/showtimes/${showtimeId}` }}>Log in</Link> to book seats.</Notice>}

      <SeatMap
        seats={seats} selected={selected} onSeatClick={onSeatClick}
        onPreview={(seat, sticky) => setPreview((p) => (seat ? { seat, sticky } : p?.sticky ? p : null))}
      />
      {preview && (
        <div className="seatview-float">
          <SeatView seats={seats} seat={preview.seat} title={showtime?.movie_title ?? ""}
            onClose={preview.sticky ? () => setPreview(null) : undefined}
            onOpen3D={() => { setHall3d(preview.seat); setPreview(null); }} />
        </div>
      )}

      {all.length > 0 && (
        <div className="paybar">
          <div>
            <strong>{all.length}</strong> seat{all.length > 1 ? "s" : ""}: {all.map((s) => s.label).join(", ")}
            {mine.length > 0 && left !== null && <span className={`countdown inline ${left < 60 ? "urgent" : ""}`}> - held for {mmss(left)}</span>}
          </div>
          <button className="big" onClick={() => setHall3d(all[0])}>View in 3D</button>
          <button className="primary big" disabled={busy} onClick={proceed}>
            {busy ? "Please wait..." : `Pay ${CUR}${total.toFixed(2)}`}
          </button>
        </div>
      )}

      {hall3d && (
        <Suspense fallback={<div className="hall3d"><p className="center" style={{ margin: "auto" }}>Loading the 3D hall...</p></div>}>
          <Hall3D seats={seats} seat={hall3d} title={showtime?.movie_title ?? ""}
            selectedIds={new Set(all.map((x) => x.id))} onClose={() => setHall3d(null)} />
        </Suspense>
      )}

      {qty === null && (
        <div className="modal-backdrop">
          <div className="modal" role="dialog" aria-label="How many seats?">
            <h2>How many seats?</h2>
            <div className="qty">
              {Array.from({ length: MAX_SEATS }, (_, i) => i + 1).map((n) => (
                <button key={n} className={n === pendingQty ? "on" : ""} onClick={() => setPendingQty(n)}>{n}</button>
              ))}
            </div>
            <div className="tier-info">
              {tiers.map(([type, t]) => {
                const a = availability(t.avail, t.total);
                return (
                  <div key={type} className="tier-col">
                    <div className="tier-name">{type.toUpperCase()}</div>
                    <div className="tier-price">{CUR}{t.price}</div>
                    <div className={`tier-avail ${a.cls}`}>{a.text}</div>
                  </div>
                );
              })}
            </div>
            <div className="modal-actions">
              <button className="primary big" onClick={() => { setQty(pendingQty); setSelected(new Set()); }}>Select seats</button>
              <button className="big" disabled={busy} onClick={bestAvailable}>Best available</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
