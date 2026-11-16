import type { Seat } from "../api";
import { buildRows } from "./SeatMap";

/** A tiny map of the hall with the suggested seats lit up, so you can see WHERE they are
 *  (back is at the top, the screen is the bar at the bottom). */
export default function MiniHall({ seats, highlight }: { seats: Seat[]; highlight: Set<number> }) {
  const rows = buildRows(seats);
  return (
    <div className="minihall" aria-hidden="true">
      {rows.map((r) => (
        <div className="minirow" key={r.row}>
          {r.cells.map((c) => "gap" in c
            ? <i key={c.num} className="ms gap" />
            : <i key={c.seat.id} className={`ms ${highlight.has(c.seat.id) ? "pick" : c.seat.status === "available" ? "free" : "taken"}`} />)}
        </div>
      ))}
      <div className="minibar">SCREEN</div>
    </div>
  );
}
