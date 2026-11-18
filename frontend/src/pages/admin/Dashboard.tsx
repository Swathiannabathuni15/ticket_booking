import { useMemo, useState } from "react";
import { api, money, when, type DayStat } from "../../api";
import { ErrorBanner, Loading } from "../../components/ui";
import { useLoad } from "../../hooks";

const RANGES = [7, 14, 30];
const short = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const rupees = (cents: number) => `₹${Math.round(cents / 100).toLocaleString("en-IN")}`;

/** "Nice" axis maximum and 3 ticks: 0, half, max. */
export function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  return ([1, 2, 2.5, 5, 10].find((m) => m * p >= v) ?? 10) * p;
}

/** A bar with 4px rounded top corners, anchored flat on the baseline. */
function barPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/** One series per chart (never two y-scales on one chart): revenue and bookings get their own. */
function DayBars({ title, data, value, format }: {
  title: string; data: DayStat[]; value: (d: DayStat) => number; format: (v: number) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 180, L = 52, B = 22, T = 10;
  const max = niceMax(Math.max(0, ...data.map(value)));
  const step = (W - L) / data.length;
  const bw = Math.max(2, step - 2);                 // 2px gap between bars
  const y = (v: number) => T + (H - T - B) * (1 - v / max);
  const every = Math.ceil(data.length / 8);         // at most ~8 date labels
  const h = hover !== null ? data[hover] : null;

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <div className="chart-box">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${title}, one bar per day`} onMouseLeave={() => setHover(null)}>
          {[0, max / 2, max].map((t) => (
            <g key={t}>
              <line x1={L} x2={W} y1={y(t)} y2={y(t)} className="grid" />
              <text x={L - 6} y={y(t)} className="tick" textAnchor="end" dominantBaseline="middle">{format(t)}</text>
            </g>
          ))}
          {data.map((d, i) => {
            const v = value(d), x = L + i * step + 1;
            return (
              <g key={d.date}>
                <path d={barPath(x, y(v), bw, y(0) - y(v))} className={`bar ${hover === i ? "on" : ""}`} />
                {/* the hit target is the whole column, bigger than the bar */}
                <rect x={L + i * step} y={T} width={step} height={H - T - B} fill="transparent"
                  onMouseEnter={() => setHover(i)} onClick={() => setHover(i)} />
                {i % every === 0 && <text x={x + bw / 2} y={H - 6} className="tick" textAnchor="middle">{short(d.date)}</text>}
              </g>
            );
          })}
        </svg>
        {h && hover !== null && (
          <div className="chart-tip" style={{ left: `${((L + hover * step + step / 2) / W) * 100}%` }}>
            <strong>{format(value(h))}</strong>
            <span>{short(h.date)}</span>
          </div>
        )}
      </div>
    </figure>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

function Meter({ percent }: { percent: number }) {
  return (
    <div className="meter" role="meter" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label="Seats sold">
      <span style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

export default function Dashboard() {
  const [days, setDays] = useState(14);
  const [sort, setSort] = useState<"fullest" | "soonest">("fullest");
  const [asTable, setAsTable] = useState(false);
  const { data, error, loading, reload } = useLoad(() => api.dashboard(days), [days]);

  const shows = useMemo(() => {
    const list = [...(data?.shows ?? [])];
    if (sort === "soonest") list.sort((a, b) => a.starts_at.localeCompare(b.starts_at));
    return list;
  }, [data, sort]);

  if (!data) return loading ? <Loading /> : <ErrorBanner message={error} />;
  const t = data.totals;
  const paidRate = t.started ? Math.round((100 * (t.started - t.abandoned - t.pending)) / t.started) : 0;

  return (
    <div className="dashboard">
      <div className="actions dash-filters">
        <div className="seg" role="group" aria-label="Time range">
          {RANGES.map((n) => <button key={n} className={n === days ? "primary" : ""} onClick={() => setDays(n)}>Last {n} days</button>)}
        </div>
        <button onClick={reload} disabled={loading}>{loading ? "Loading..." : "Refresh"}</button>
      </div>
      <ErrorBanner message={error} />
      <p className="muted small">{short(data.from_date)} – {short(data.to_date)} · days follow the cinema's clock ({data.timezone}) · only paid bookings count</p>

      <div className="stat-row">
        <Tile label="Revenue" value={rupees(t.revenue_cents)} sub={`${t.bookings} paid bookings`} />
        <Tile label="Tickets sold" value={t.tickets.toLocaleString("en-IN")} sub={`avg ${money(t.avg_ticket_cents)} each`} />
        <Tile label="Occupancy" value={`${t.occupancy_percent}%`} sub={`${t.shows} shows, incl. next 7 days`} />
        <Tile label="Paid" value={`${paidRate}%`} sub={`${t.abandoned} cancelled or expired, ${t.pending} waiting`} />
      </div>

      <div className="card">
        <div className="chart-head">
          <h3>Per day</h3>
          <button className="link" onClick={() => setAsTable((v) => !v)}>{asTable ? "Show charts" : "Show as table"}</button>
        </div>
        {asTable ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Day</th><th className="num">Bookings</th><th className="num">Tickets</th><th className="num">Revenue</th></tr></thead>
              <tbody>
                {data.per_day.map((d) => (
                  <tr key={d.date}><td>{short(d.date)}</td><td className="num">{d.bookings}</td><td className="num">{d.tickets}</td><td className="num">{money(d.revenue_cents)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="chart-pair">
            <DayBars title="Revenue" data={data.per_day} value={(d) => d.revenue_cents} format={rupees} />
            <DayBars title="Bookings" data={data.per_day} value={(d) => d.bookings} format={(v) => String(Math.round(v))} />
          </div>
        )}
      </div>

      <div className="card">
        <div className="chart-head">
          <h3>Occupancy per show</h3>
          <select value={sort} onChange={(e) => setSort(e.target.value as "fullest" | "soonest")} aria-label="Sort shows">
            <option value="fullest">Fullest first</option>
            <option value="soonest">By time</option>
          </select>
        </div>
        {shows.length === 0 ? <p className="muted">No shows in this period.</p> : (
          <div className="table-wrap">
            <table className="occupancy">
              <thead><tr><th>Show</th><th>Where</th><th>Sold</th><th className="num">Revenue</th></tr></thead>
              <tbody>
                {shows.map((s) => (
                  <tr key={s.showtime_id}>
                    <td><strong>{s.movie_title}</strong><div className="muted small">{when(s.starts_at)}</div></td>
                    <td>{s.theatre_name}<div className="muted small">{s.city} · {s.screen_name}</div></td>
                    <td className="occ-cell">
                      <Meter percent={s.occupancy_percent} />
                      <span className="small">{s.seats_sold}/{s.seats_total} · {s.occupancy_percent}%</span>
                    </td>
                    <td className="num">{money(s.revenue_cents)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data.shows.length === 100 && <p className="muted small">Showing the 100 fullest shows.</p>}
      </div>

      <div className="card">
        <h3>Top movies</h3>
        {data.top_movies.length === 0 ? <p className="muted">No paid bookings yet in this period.</p> : (
          <ul className="hbars">
            {data.top_movies.map((m) => (
              <li key={m.title} title={`${m.title}: ${money(m.revenue_cents)}, ${m.tickets} tickets`}>
                <span className="hbar-name">{m.title}</span>
                <span className="hbar-track"><span style={{ width: `${(100 * m.revenue_cents) / data.top_movies[0].revenue_cents}%` }} /></span>
                <span className="hbar-value">{rupees(m.revenue_cents)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
