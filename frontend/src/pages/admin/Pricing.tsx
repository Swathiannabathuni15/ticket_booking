import { useState, type FormEvent } from "react";
import { api, CUR, type PricePreview, type PricingRule, type PricingRuleIn } from "../../api";
import { ErrorBanner, Notice } from "../../components/ui";
import { errText, useLoad } from "../../hooks";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const PRESETS: Record<string, number[]> = { "Every day": [], Weekdays: [0, 1, 2, 3, 4], Weekend: [5, 6] };

/** "18:30" -> 1110 minutes after midnight; "" -> null. */
export function toMinutes(hhmm: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

const EMPTY = { name: "", days: [] as number[], from: "", to: "", seatType: "", percent: "10" };

function RuleForm({ seatTypes, onSaved }: { seatTypes: string[]; onSaved: () => void }) {
  const [f, setF] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toggleDay = (d: number) => setF({ ...f, days: f.days.includes(d) ? f.days.filter((x) => x !== d) : [...f.days, d].sort() });

  async function submit(e: FormEvent) {
    e.preventDefault();
    const body: PricingRuleIn = {
      name: f.name.trim(), days: f.days, start_minute: toMinutes(f.from), end_minute: toMinutes(f.to),
      seat_type: f.seatType || null, percent: Number(f.percent), active: true,
    };
    if ((body.start_minute === null) !== (body.end_minute === null)) { setError("Give both a start and an end time, or neither."); return; }
    setBusy(true); setError(null);
    try { await api.createPricingRule(body); setF(EMPTY); onSaved(); } catch (err) { setError(errText(err)); } finally { setBusy(false); }
  }

  const pct = Number(f.percent);
  return (
    <form className="card form" onSubmit={submit}>
      <h3>New rule</h3>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <label>Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Weekend" required maxLength={80} /></label>
      <div>
        <div className="label">Days</div>
        <div className="day-picks">
          {Object.entries(PRESETS).map(([name, days]) => (
            <button type="button" key={name} className={`chip ${JSON.stringify(f.days) === JSON.stringify(days) ? "on" : ""}`} onClick={() => setF({ ...f, days })}>{name}</button>
          ))}
          <span className="sep" />
          {DAYS.map((d, i) => (
            <button type="button" key={d} className={`chip ${f.days.includes(i) ? "on" : ""}`} aria-pressed={f.days.includes(i)} onClick={() => toggleDay(i)}>{d}</button>
          ))}
        </div>
      </div>
      <div className="row">
        <label>Shows starting from<input type="time" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} /></label>
        <label>until<input type="time" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></label>
      </div>
      <p className="muted small">Leave the times empty for any time. A window can cross midnight (22:00 until 02:00).</p>
      <div className="row">
        <label>Seats<select value={f.seatType} onChange={(e) => setF({ ...f, seatType: e.target.value })}>
          <option value="">All seat types</option>
          {seatTypes.map((t) => <option key={t} value={t}>{t}</option>)}
        </select></label>
        <label>Change (%)<input type="number" min={-90} max={200} step={1} value={f.percent} onChange={(e) => setF({ ...f, percent: e.target.value })} required /></label>
      </div>
      <p className="muted small">
        {pct > 0 ? `${pct}% dearer: ${CUR}200 becomes ${CUR}${Math.round(200 * (100 + pct) / 100)}.` :
          pct < 0 ? `${-pct}% cheaper: ${CUR}200 becomes ${CUR}${Math.round(200 * (100 + pct) / 100)}.` : "Enter a change other than 0."}
      </p>
      <button className="primary" disabled={busy}>{busy ? "Saving..." : "Add rule"}</button>
    </form>
  );
}

function Preview({ screens }: { screens: { id: number; name: string }[] }) {
  const [screenId, setScreenId] = useState("");
  const [at, setAt] = useState("");
  const [p, setP] = useState<PricePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function go(e: FormEvent) {
    e.preventDefault(); setError(null);
    try { setP(await api.pricePreview(Number(screenId), at)); } catch (err) { setError(errText(err)); }
  }
  return (
    <form className="card form" onSubmit={go}>
      <h3>Try it</h3>
      <p className="muted small">What would a new show cost with the rules that are on?</p>
      <div className="row">
        <label>Screen<select value={screenId} onChange={(e) => setScreenId(e.target.value)} required>
          <option value="">Choose...</option>
          {screens.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select></label>
        <label>Starts at<input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} required /></label>
      </div>
      <button>Show prices</button>
      <ErrorBanner message={error} />
      {p && (
        <table className="small">
          <thead><tr><th>Seat type</th><th className="num">Base</th><th className="num">Price</th></tr></thead>
          <tbody>{Object.keys(p.base).map((t) => (
            <tr key={t}><td>{t}</td><td className="num">{CUR}{p.base[t]}</td><td className="num"><strong>{CUR}{p.final[t]}</strong></td></tr>
          ))}</tbody>
          <caption className="muted small">{p.note ?? "No rule applies: base prices."}</caption>
        </table>
      )}
    </form>
  );
}

export default function Pricing() {
  const rules = useLoad(api.pricingRules);
  const screens = useLoad(api.screens);
  const [error, setError] = useState<string | null>(null);
  const seatTypes = [...new Set((screens.data ?? []).flatMap((s) => (s.layout as { seats: (string | null)[] }[]).flatMap((r) => r.seats.filter((x): x is string => !!x))))].sort();
  const screenList = (screens.data ?? []).map((s) => ({ id: s.id, name: `${s.name} (#${s.id})` }));

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); rules.reload(); } catch (e) { setError(errText(e)); }
  }
  const toggle = (r: PricingRule) => act(() => api.updatePricingRule(r.id, {
    name: r.name, days: r.days, start_minute: r.start_minute, end_minute: r.end_minute, seat_type: r.seat_type, percent: r.percent, active: !r.active,
  }));

  return (
    <>
      <Notice>
        Rules set the price of <strong>new</strong> showtimes (the automatic schedule and the Create showtime form).
        Shows already on sale keep their prices, so nobody is charged more than they were shown.
        Matching rules add up: Weekend +20% and Evening +10% make a Saturday night show 30% dearer.
      </Notice>
      <ErrorBanner message={error ?? rules.error} onClose={() => setError(null)} />
      <div className="card">
        <h3>Rules</h3>
        {rules.data?.length === 0 && <p className="muted">No rules yet: every show uses its base prices.</p>}
        <ul className="rule-list">
          {rules.data?.map((r) => (
            <li key={r.id} className={r.active ? "" : "off"}>
              <div>
                <strong>{r.name}</strong> <span className={`pct ${r.percent > 0 ? "up" : "down"}`}>{r.percent > 0 ? `+${r.percent}` : r.percent}%</span>
                <div className="muted small">{r.summary}</div>
              </div>
              <div className="actions">
                <button onClick={() => toggle(r)}>{r.active ? "Turn off" : "Turn on"}</button>
                <button onClick={() => { if (confirm(`Delete "${r.name}"?`)) act(() => api.deletePricingRule(r.id)); }}>Delete</button>
              </div>
            </li>
          ))}
        </ul>
      </div>
      <div className="pricing-grid">
        <RuleForm seatTypes={seatTypes} onSaved={rules.reload} />
        <Preview screens={screenList} />
      </div>
    </>
  );
}
