import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, CUR, when, type BestShow } from "../api";
import { useCity } from "../city";
import { errText } from "../hooks";
import { ErrorBanner } from "./ui";

const TIMES = [["", "Any time"], ["morning", "Morning"], ["afternoon", "Afternoon"], ["evening", "Evening"], ["night", "Night"]] as const;

/** "Find the best seats": for every upcoming show of this movie, the best free seats together
 *  (centred, a good distance back), with filters. Pick one and the seat page opens with those
 *  seats already chosen. */
export default function SeatFinder({ movieId }: { movieId: number }) {
  const { city } = useCity();
  const [count, setCount] = useState(2);
  const [centre, setCentre] = useState(true);
  const [notClose, setNotClose] = useState(true);
  const [maxPrice, setMaxPrice] = useState("");
  const [tod, setTod] = useState("");
  const [results, setResults] = useState<BestShow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let stale = false;
    const t = setTimeout(async () => {          // wait for the person to stop typing a price
      setBusy(true); setError(null);
      try {
        const price = Number(maxPrice);
        const r = await api.bestShows(movieId, {
          count, city, centre_only: centre, not_too_close: notClose,
          max_price: price > 0 ? price : undefined, time_of_day: tod || undefined,
        });
        if (!stale) setResults(r);
      } catch (e) { if (!stale) setError(errText(e)); }
      finally { if (!stale) setBusy(false); }
    }, 250);
    return () => { stale = true; clearTimeout(t); };
  }, [movieId, count, city, centre, notClose, maxPrice, tod]);

  return (
    <section className="card finder">
      <h3>Find the best seats</h3>
      <p className="muted small">The best free seats together in every upcoming show{city ? ` in ${city}` : ""}.</p>
      <div className="finder-controls">
        <label>Seats
          <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
            {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label>Max price per seat ({CUR})
          <input type="number" min="1" inputMode="numeric" placeholder="No limit" value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} />
        </label>
        <label className="check"><input type="checkbox" checked={centre} onChange={(e) => setCentre(e.target.checked)} /> Centre seats only</label>
        <label className="check"><input type="checkbox" checked={notClose} onChange={(e) => setNotClose(e.target.checked)} /> Not too close to the screen</label>
      </div>
      <div className="filters">
        {TIMES.map(([v, label]) => <button key={v} className={`chip ${tod === v ? "on" : ""}`} onClick={() => setTod(v)}>{label}</button>)}
      </div>
      <ErrorBanner message={error} />
      {busy && !results && <p className="muted">Looking...</p>}
      {results && results.length === 0 && !busy && <p className="muted">No shows have {count} seats together that fit. Try fewer seats or loosen a filter.</p>}
      <div className="finder-results">
        {results?.map((r) => (
          <div className="finder-row" key={r.showtime_id}>
            <div>
              <strong>{when(r.starts_at)}</strong>
              <div className="muted small">{r.theatre_name}{r.city ? `, ${r.city}` : ""}</div>
              <div className="small">Seats <strong>{r.seat_labels.join(", ")}</strong> - {CUR}{r.total_price.toFixed(0)}
                <span className="muted"> - {Math.abs(r.off_centre) < 1 ? "centred" : `${Math.abs(r.off_centre)} seats off-centre`}, {r.rows_from_screen + 1} rows from the screen</span>
              </div>
            </div>
            <Link className="btn primary" to={`/showtimes/${r.showtime_id}?seats=${r.seat_ids.join(",")}`}>Pick these</Link>
          </div>
        ))}
      </div>
    </section>
  );
}
