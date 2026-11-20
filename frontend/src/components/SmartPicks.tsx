import { useEffect, useState } from "react";
import { api, CUR, type Seat, type SeatOption, type SeatOptions } from "../api";
import { errText } from "../hooks";
import MiniHall from "./MiniHall";
import { ErrorBanner } from "./ui";
import { labelTone, viewWord } from "./viewQuality";

type Props = {
  showtimeId: number; count: number; seats: Seat[];
  onSelect: (o: SeatOption) => void; onPreview3D: (seatId: number) => void; onClose: () => void;
};

/** "Best available", done properly: a few different good choices for N seats together, each with how
 *  well you would see, what it costs, where it is, and the reasons. Nothing is reserved until you pick. */
export default function SmartPicks({ showtimeId, count, seats, onSelect, onPreview3D, onClose }: Props) {
  const [budget, setBudget] = useState("");
  const [aisle, setAisle] = useState(false);
  const [data, setData] = useState<SeatOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  useEffect(() => {
    let stale = false;
    setBusy(true);
    const t = setTimeout(async () => {            // wait while the person is still typing a budget
      try {
        const total = Number(budget);
        const r = await api.seatOptions(showtimeId, { count, max_total: total > 0 ? total : undefined, prefer_aisle: aisle });
        if (!stale) { setData(r); setError(null); }
      } catch (e) { if (!stale) setError(errText(e)); }
      finally { if (!stale) setBusy(false); }
    }, 300);
    return () => { stale = true; clearTimeout(t); };
  }, [showtimeId, count, budget, aisle]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal picks" role="dialog" aria-label="Best available seats" onClick={(e) => e.stopPropagation()}>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
        <h2>Best available for {count} {count === 1 ? "seat" : "seats"}</h2>
        <p className="muted small center">We compare how well you'd see the screen, what it costs, and whether it leaves a lonely empty seat.</p>

        <div className="picks-controls">
          <label>Budget for all {count} (optional)
            <input type="number" min="1" inputMode="numeric" placeholder={`e.g. ${CUR}${count * 250}`} value={budget} onChange={(e) => setBudget(e.target.value)} />
          </label>
          <label className="check"><input type="checkbox" checked={aisle} onChange={(e) => setAisle(e.target.checked)} /> I'd like an aisle seat</label>
        </div>

        <ErrorBanner message={error} />
        {data?.message && <div className="banner info">{data.message}</div>}
        {busy && !data && <div className="pick-card skeleton-card" />}

        <div className={`picks-list ${busy ? "dim" : ""}`}>
          {data?.options.map((o) => {
            const v = viewWord(o.visibility);
            return (
              <article className="pick-card" key={o.seat_ids.join("-")}>
                <MiniHall seats={seats} highlight={new Set(o.seat_ids)} />
                <div className="pick-body">
                  <div className="pick-labels">{o.labels.map((l) => <span key={l} className={`pick-label ${labelTone(l)}`}>{l}</span>)}</div>
                  <div className="pick-seats">{o.seat_labels.join(" · ")}</div>
                  <div className="muted small">{o.seat_type.charAt(0).toUpperCase() + o.seat_type.slice(1)} · {CUR}{o.price_each} each</div>
                  <div className="meter" title={`View score ${Math.round(o.visibility)} out of 100`}>
                    <div className={`meter-fill ${v.tone}`} style={{ width: `${Math.max(4, o.visibility)}%` }} />
                  </div>
                  <div className={`meter-word ${v.tone}`}>{v.word} <span className="muted small">({Math.round(o.visibility)}/100)</span></div>
                  <ul className="reasons">{o.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
                  {o.warnings.length > 0 && <ul className="warns">{o.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
                </div>
                <div className="pick-side">
                  <div className="pick-total">{CUR}{o.total_price}</div>
                  <button className="primary" onClick={() => onSelect(o)}>Select these seats</button>
                  <button onClick={() => onPreview3D(o.seat_ids[0])}>See the view in 3D</button>
                </div>
              </article>
            );
          })}
        </div>
        <p className="muted small center">Seats stay free for everyone until you continue to payment. Views are estimated from the hall's layout.</p>
      </div>
    </div>
  );
}
