import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, money, type Booking, type Split } from "../api";
import { ErrorBanner, Loading, Notice, StatusBadge } from "../components/ui";
import { errText } from "../hooks";

/** Same rule as the server: everyone gets total // n cents, the first
 *  (total % n) people get one extra cent, so shares add up exactly. */
export function equalShares(totalCents: number, n: number): number[] {
  const base = Math.floor(totalCents / n);
  const extra = totalCents % n;
  return Array.from({ length: n }, (_, i) => base + (i < extra ? 1 : 0));
}

export default function SplitPage() {
  const id = Number(useParams().id);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [split, setSplit] = useState<Split | null | undefined>(undefined); // undefined = loading, null = none yet
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const all = await api.myBookings();
      setBooking(all.find((b) => b.id === id) ?? null);
      try { setSplit(await api.getSplit(id)); }
      catch (e) { if (e instanceof ApiError && e.status === 404) setSplit(null); else throw e; }
    } catch (e) { setError(errText(e)); }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  // Friends may be paying right now: refresh the shares every few seconds.
  useEffect(() => {
    if (!split || split.status !== "open") return;
    const t = setInterval(() => api.getSplit(id).then(setSplit).catch(() => {}), 5000);
    return () => clearInterval(t);
  }, [id, split]);

  if (error && !booking) return <ErrorBanner message={error} />;
  if (!booking || split === undefined) return <Loading />;
  if (booking.status !== "confirmed")
    return <Notice>Only paid bookings can be split. <Link to={`/checkout/${id}`}>Go to checkout</Link></Notice>;

  return (
    <div className="card narrow wide">
      <h1>Split the bill</h1>
      <p className="muted">{booking.movie_title} - seats {booking.seat_labels.join(", ")} - total {money(booking.total_cents)}</p>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {split === null
        ? <CreateForm booking={booking} onCreated={setSplit} onError={setError} />
        : <SplitStatus split={split} bookingId={id} onChange={setSplit} onError={setError} />}
    </div>
  );
}

function CreateForm({ booking, onCreated, onError }: { booking: Booking; onCreated: (s: Split) => void; onError: (m: string | null) => void }) {
  const [friends, setFriends] = useState<string[]>([""]);
  const [includeOwner, setIncludeOwner] = useState(true);
  const [mode, setMode] = useState<"equal" | "custom">("equal");
  const [custom, setCustom] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const names = [...(includeOwner ? ["You"] : []), ...friends.map((f) => f.trim() || "Friend")];
  const equal = equalShares(booking.total_cents, names.length);
  const customCents = names.map((_, i) => Math.round(parseFloat(custom[i] || "0") * 100) || 0);
  const customSum = customCents.reduce((a, b) => a + b, 0);
  const shares = mode === "equal" ? equal : customCents;
  const mismatch = mode === "custom" && customSum !== booking.total_cents;

  async function submit() {
    setBusy(true); onError(null);
    try {
      onCreated(await api.createSplit(booking.id, {
        friends: friends.map((f) => f.trim()), include_owner: includeOwner, mode,
        custom_cents: mode === "custom" ? customCents : undefined,
      }));
    } catch (e) { onError(errText(e)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <label className="check"><input type="checkbox" checked={includeOwner} onChange={(e) => setIncludeOwner(e.target.checked)} /> Include my share (I already paid)</label>
      <div className="seg">
        <button className={mode === "equal" ? "primary" : ""} onClick={() => setMode("equal")}>Equal</button>
        <button className={mode === "custom" ? "primary" : ""} onClick={() => setMode("custom")}>Custom amounts</button>
      </div>
      <h3>Friends</h3>
      {friends.map((f, i) => (
        <div className="friend" key={i}>
          <input placeholder={`Friend ${i + 1} name`} value={f} maxLength={100}
            onChange={(e) => setFriends(friends.map((x, j) => (j === i ? e.target.value : x)))} />
          {friends.length > 1 && <button onClick={() => setFriends(friends.filter((_, j) => j !== i))} aria-label="Remove friend">x</button>}
        </div>
      ))}
      <button onClick={() => setFriends([...friends, ""])} disabled={friends.length >= 19}>+ Add friend</button>

      <h3>Shares</h3>
      <table>
        <tbody>
          {names.map((n, i) => (
            <tr key={i}>
              <td>{n}</td>
              <td className="num">
                {mode === "equal" ? money(shares[i]) : (
                  <input className="amount" inputMode="decimal" placeholder="0.00" value={custom[i] ?? ""}
                    onChange={(e) => { const c = [...custom]; c[i] = e.target.value; setCustom(c); }} />
                )}
              </td>
            </tr>
          ))}
          <tr className="sum"><td>Total</td><td className="num">{money(shares.reduce((a, b) => a + b, 0))} / {money(booking.total_cents)}</td></tr>
        </tbody>
      </table>
      {mismatch && <p className="error-text">Shares must add up to {money(booking.total_cents)} ({money(booking.total_cents - customSum)} left).</p>}
      <button className="primary" disabled={busy || mismatch || friends.every((f) => !f.trim())} onClick={submit}>
        Create payment links
      </button>
    </>
  );
}

function SplitStatus({ split, bookingId, onChange, onError }: { split: Split; bookingId: number; onChange: (s: Split) => void; onError: (m: string | null) => void }) {
  const [copied, setCopied] = useState<number | null>(null);

  async function copy(shareId: number, link: string) {
    const url = location.origin + link;
    try { await navigator.clipboard.writeText(url); setCopied(shareId); setTimeout(() => setCopied(null), 1500); }
    catch { prompt("Copy this link:", url); }
  }
  async function cancel() {
    if (!confirm("Cancel the split? Anyone who already paid is refunded.")) return;
    try { onChange(await api.cancelSplit(bookingId)); } catch (e) { onError(errText(e)); }
  }

  return (
    <>
      <p>Status: <StatusBadge status={split.status} /> - collected {money(split.paid_cents)} of {money(split.total_cents)}</p>
      <div className="progress"><div style={{ width: `${(split.paid_cents / split.total_cents) * 100}%` }} /></div>
      <table>
        <tbody>
          {split.shares.map((s) => (
            <tr key={s.id}>
              <td>{s.name}{s.is_owner && " (you)"}</td>
              <td className="num">{money(s.amount_cents)}</td>
              <td><StatusBadge status={s.status} /></td>
              <td>{s.link && <button onClick={() => copy(s.id, s.link!)}>{copied === s.id ? "Copied!" : "Copy link"}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {split.status === "open" && <button onClick={cancel}>Cancel split</button>}
      {split.status === "settled" && <p className="success">Everyone has paid.</p>}
      {split.status === "cancelled" && <p className="muted">This split was cancelled.</p>}
      <p className="muted small">Links expire after 48 hours. Friends do not need an account.</p>
    </>
  );
}
