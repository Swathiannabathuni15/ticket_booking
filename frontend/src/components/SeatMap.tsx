import { useRef } from "react";
import type { Seat } from "../api";
import { CUR } from "../api";

// Seat labels look like "A7". The backend leaves aisle gaps as missing seat
// numbers, so a gap is just a number with no seat in that row.
export function parseLabel(label: string): { row: string; num: number } {
  const m = /^([A-Za-z]+)(\d+)$/.exec(label);
  return m ? { row: m[1], num: Number(m[2]) } : { row: label, num: 0 };
}

export type Cell = { seat: Seat; num: number } | { gap: true; num: number };
export type RowData = { row: string; type: string; cells: Cell[] };

export function buildRows(seats: Seat[]): RowData[] {
  const rows = new Map<string, Map<number, Seat>>();
  let maxCol = 0;
  for (const s of seats) {
    const { row, num } = parseLabel(s.label);
    if (!rows.has(row)) rows.set(row, new Map());
    rows.get(row)!.set(num, s);
    maxCol = Math.max(maxCol, num);
  }
  return [...rows.keys()].sort().map((row) => {
    const bySeat = rows.get(row)!;
    const cells: Cell[] = [];
    for (let n = 1; n <= maxCol; n++) {
      const seat = bySeat.get(n);
      cells.push(seat ? { seat, num: n } : { gap: true, num: n });
    }
    return { row, type: [...bySeat.values()][0].seat_type, cells };
  });
}

/** Rows grouped into price tiers (consecutive rows sharing a seat type). */
export function buildTiers(seats: Seat[]) {
  const tiers: { type: string; price: number; rows: RowData[] }[] = [];
  const price = new Map(seats.map((s) => [s.seat_type, s.price]));
  for (const row of buildRows(seats)) {
    const last = tiers[tiers.length - 1];
    if (last && last.type === row.type) last.rows.push(row);
    else tiers.push({ type: row.type, price: price.get(row.type) ?? 0, rows: [row] });
  }
  return tiers;
}

/** The `qty` seats a click should select: a run of adjacent AVAILABLE seats in
 *  the clicked row that includes the clicked seat, preferring to start at it. */
export function adjacentRun(seats: Seat[], clicked: Seat, qty: number): Seat[] {
  const { row } = parseLabel(clicked.label);
  const line = buildRows(seats).find((r) => r.row === row)!.cells;
  const idx = line.findIndex((c) => "seat" in c && c.seat.id === clicked.id);
  const ok = (i: number) => {
    const c = line[i];
    return !!c && "seat" in c && c.seat.status === "available";
  };
  for (let start = idx; start > idx - qty; start--) {
    const end = start + qty - 1;
    if (start < 0 || end >= line.length) continue;
    let all = true;
    for (let i = start; i <= end; i++) if (!ok(i)) { all = false; break; }
    if (all) return line.slice(start, end + 1).map((c) => (c as { seat: Seat }).seat);
  }
  return [];
}

type Props = {
  seats: Seat[];
  selected: Set<number>;
  onSeatClick: (seat: Seat) => void;
  /** Show / hide the "view from this seat" preview. sticky = opened by touch (stays until closed). */
  onPreview?: (seat: Seat | null, sticky: boolean) => void;
};

export default function SeatMap({ seats, selected, onSeatClick, onPreview }: Props) {
  const tiers = buildTiers(seats);
  return (
    <div className="seatmap">
      <div className="seat-scroll">
        <div className="seat-inner">
          {tiers.map((t) => (
            <section key={t.type} className="tier">
              <div className="tier-head">{CUR}{t.price} {t.type.toUpperCase()}</div>
              {t.rows.map(({ row, cells }) => (
                <div className="seat-row" key={row}>
                  <span className="row-label">{row}</span>
                  {cells.map((c) =>
                    "gap" in c ? (
                      <span className="seat gap" key={c.num} />
                    ) : (
                      <SeatButton key={c.seat.id} seat={c.seat} num={c.num} selected={selected.has(c.seat.id)} onClick={onSeatClick} onPreview={onPreview} />
                    ),
                  )}
                </div>
              ))}
            </section>
          ))}
          <div className="screen-bar" aria-hidden>
            <div className="screen-glow" />
            <span>All eyes this way - screen</span>
          </div>
        </div>
      </div>

      <p className="seat-hint muted small">← Swipe sideways to see every seat →</p>
      <div className="legend">
        <Legend cls="available" text="Available" />
        <Legend cls="selected" text="Selected" />
        <Legend cls="mine" text="Held by you" />
        <Legend cls="held" text="Held by someone" />
        <Legend cls="booked" text="Sold" />
      </div>
    </div>
  );
}

function Legend({ cls, text }: { cls: string; text: string }) {
  return (
    <span className="legend-item">
      <i className={`swatch seat ${cls}`} /> {text}
    </span>
  );
}

function SeatButton({ seat, num, selected, onClick, onPreview }: {
  seat: Seat; num: number; selected: boolean; onClick: (s: Seat) => void;
  onPreview?: (seat: Seat | null, sticky: boolean) => void;
}) {
  // Touch: long-press, or swipe up on the seat, opens the view-from-seat preview.
  const touch = useRef<{ y: number; timer: number; fired: boolean }>({ y: 0, timer: 0, fired: false });
  const fire = () => { touch.current.fired = true; onPreview?.(seat, true); };
  const state = selected ? "selected" : seat.status === "held_by_me" ? "mine" : seat.status;
  const clickable = seat.status === "available" || seat.status === "held_by_me";
  return (
    <button
      type="button"
      className={`seat ${state}`}
      disabled={!clickable}
      aria-pressed={selected}
      aria-label={`Seat ${seat.label}, ${seat.seat_type}, ${CUR}${seat.price}, ${state}`}
      title={`${seat.label} - ${seat.seat_type} - ${CUR}${seat.price}`}
      onClick={() => { if (touch.current.fired) { touch.current.fired = false; return; } onClick(seat); }}
      onMouseEnter={() => onPreview?.(seat, false)}
      onMouseLeave={() => onPreview?.(null, false)}
      onFocus={() => onPreview?.(seat, false)}
      onBlur={() => onPreview?.(null, false)}
      onTouchStart={(e) => {
        touch.current = { y: e.touches[0].clientY, fired: false, timer: window.setTimeout(fire, 450) };
      }}
      onTouchMove={(e) => {
        if (!touch.current.fired && touch.current.y - e.touches[0].clientY > 30) { clearTimeout(touch.current.timer); fire(); }
      }}
      onTouchEnd={(e) => { clearTimeout(touch.current.timer); if (touch.current.fired) e.preventDefault(); }}
    >
      {num}
    </button>
  );
}
