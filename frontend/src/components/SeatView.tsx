import type { Seat } from "../api";
import { buildRows } from "./SeatMap";

// ---- geometry (metres). Row A is the BACK of the hall, the screen is below the last row. ----
const ROW_DEPTH = 1.1;      // distance between rows
const SEAT_PITCH = 0.55;    // distance between seats
const SCREEN_GAP = 3;       // last row -> screen
const SCREEN_W = 14, SCREEN_BOTTOM = 1.4, SCREEN_H = 6;
const RISE_PER_ROW = 0.22;  // stadium seating: each row further back sits higher
const EYE_BASE = 1.1;

export type ViewStats = {
  rowsFromScreen: number; distance: number; xOffset: number; eye: number;
  screenAngle: number; offAxis: number; verdict: string; tone: "great" | "ok" | "poor";
};

export function seatGeometry(rowIdx: number, nRows: number, colIdx: number, nCols: number): ViewStats {
  const rowsFromScreen = nRows - 1 - rowIdx;
  const distance = SCREEN_GAP + rowsFromScreen * ROW_DEPTH;
  const xOffset = (colIdx - (nCols - 1) / 2) * SEAT_PITCH;
  const eye = EYE_BASE + rowsFromScreen * RISE_PER_ROW;
  const screenAngle = (2 * Math.atan(SCREEN_W / 2 / distance) * 180) / Math.PI;
  const offAxis = (Math.atan(Math.abs(xOffset) / distance) * 180) / Math.PI;
  let verdict = "Great view - nicely centred";
  let tone: ViewStats["tone"] = "great";
  if (screenAngle > 75) { verdict = "Very close - you'll be looking up and across"; tone = "poor"; }
  else if (screenAngle < 32) { verdict = "Far back - small screen, but you see it all"; tone = "ok"; }
  if (offAxis > 22) { verdict = "Steep side angle - the picture will look stretched"; tone = "poor"; }
  else if (offAxis > 12 && tone === "great") { verdict = "Good view from the side"; tone = "ok"; }
  return { rowsFromScreen, distance, xOffset, eye, screenAngle, offAxis, verdict, tone };
}

/** Where is this seat in the hall? */
export function locateSeat(seats: Seat[], seat: Seat) {
  const rows = buildRows(seats);
  const rowIdx = rows.findIndex((r) => r.cells.some((c) => "seat" in c && c.seat.id === seat.id));
  const colIdx = rows[rowIdx].cells.findIndex((c) => "seat" in c && c.seat.id === seat.id);
  return { rowIdx, nRows: rows.length, colIdx, nCols: rows[rowIdx].cells.length };
}

const VW = 800, VH = 420, F = VW / 2 / Math.tan((62 * Math.PI) / 360); // ~62 degree horizontal field of view
const HORIZON = VH * 0.5;

function rnd(seed: number) { // tiny deterministic PRNG so the "audience" doesn't flicker between renders
  const x = Math.sin(seed * 9301 + 49297) * 233280;
  return x - Math.floor(x);
}

type Props = { seats: Seat[]; seat: Seat; title: string; onClose?: () => void; onOpen3D?: () => void };

export default function SeatView({ seats, seat, title, onClose, onOpen3D }: Props) {
  const { rowIdx, nRows, colIdx, nCols } = locateSeat(seats, seat);
  const g = seatGeometry(rowIdx, nRows, colIdx, nCols);
  const px = (x: number, d: number) => VW / 2 + (F * (x - g.xOffset)) / d;
  const py = (y: number, d: number) => HORIZON - (F * (y - g.eye)) / d;

  // screen
  const D = g.distance;
  const sl = px(-SCREEN_W / 2, D), sr = px(SCREEN_W / 2, D);
  const st = py(SCREEN_BOTTOM + SCREEN_H, D), sb = py(SCREEN_BOTTOM, D);

  // the audience in the rows ahead of you (far to near, so near heads overlap far ones)
  const heads: { x: number; y: number; r: number; k: number; key: string }[] = [];
  const ahead = Math.min(6, g.rowsFromScreen);
  for (let k = ahead; k >= 1; k--) {
    const d = D - k * ROW_DEPTH;
    const eyeK = EYE_BASE + (g.rowsFromScreen - k) * RISE_PER_ROW;
    for (let c = -Math.ceil(nCols / 2) - 2; c <= Math.ceil(nCols / 2) + 2; c++) {
      const x = c * SEAT_PITCH;
      if (rnd(rowIdx * 131 + k * 17 + c * 7 + 3) < 0.42) continue; // empty seat
      const hx = px(x, d), hy = py(eyeK + 0.12 + rnd(c + k) * 0.06, d);
      if (hx < -40 || hx > VW + 40) continue;
      heads.push({ x: hx, y: hy, r: (F * 0.105) / d, k, key: `${k}:${c}` });
    }
  }

  return (
    <div className="seatview">
      <div className="seatview-head">
        <strong>View from {seat.label}</strong>
        <span className="muted"> {seat.seat_type}</span>
        {onOpen3D && <button className="link open3d" onClick={onOpen3D}>Open 3D hall</button>}
        {onClose && <button className="link" onClick={onClose} aria-label="Close preview">close</button>}
      </div>
      <svg viewBox={`0 0 ${VW} ${VH}`} role="img" aria-label={`Preview of the view from seat ${seat.label}`}>
        <defs>
          <linearGradient id="room" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#05060b" /><stop offset="1" stopColor="#14162a" />
          </linearGradient>
          <radialGradient id="glow" cx="50%" cy="50%" r="70%">
            <stop offset="0" stopColor="#7aa2ff" stopOpacity=".55" /><stop offset="1" stopColor="#7aa2ff" stopOpacity="0" />
          </radialGradient>
          <linearGradient id="screenFill" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#1b2a55" /><stop offset=".5" stopColor="#3d5fb8" /><stop offset="1" stopColor="#101a3a" />
          </linearGradient>
        </defs>
        <rect width={VW} height={VH} fill="url(#room)" />
        {/* light spilling from the screen onto the room */}
        <ellipse cx={(sl + sr) / 2} cy={(st + sb) / 2} rx={Math.abs(sr - sl) * 0.8} ry={Math.abs(sb - st) * 1.1} fill="url(#glow)" />
        {/* side-wall perspective lines */}
        {[-1, 1].map((side) => (
          <line key={side} x1={VW / 2 + side * (VW / 2 + 60)} y1={0} x2={side < 0 ? sl : sr} y2={st} stroke="#1c2040" strokeWidth="2" />
        ))}
        {/* screen */}
        <rect x={Math.min(sl, sr)} y={st} width={Math.abs(sr - sl)} height={sb - st} fill="url(#screenFill)" stroke="#aab8ff" strokeOpacity=".5" />
        <text x={(sl + sr) / 2} y={(st + sb) / 2} textAnchor="middle" dominantBaseline="middle" fill="#e8ecff"
          fontSize={Math.max(10, Math.min(46, (sb - st) * 0.16))} fontWeight="700" opacity=".9">{title}</text>
        {/* heads of people sitting ahead of you */}
        {heads.map((h) => (
          <g key={h.key} fill={`hsl(235 25% ${5 + (6 - h.k)}%)`}>
            <circle cx={h.x} cy={h.y} r={h.r} />
            <rect x={h.x - h.r * 1.9} y={h.y + h.r * 0.9} width={h.r * 3.8} height={h.r * 6} rx={h.r * 1.4} />
          </g>
        ))}
        {/* the seat-back directly in front of you */}
        <rect x={VW * 0.1} y={VH - 34} width={VW * 0.8} height={60} rx={26} fill="#0a0b14" stroke="#23264a" />
      </svg>
      <div className={`seatview-note ${g.tone}`}>{g.verdict}</div>
      <div className="seatview-stats muted small">
        {g.rowsFromScreen + 1} rows from the screen - screen fills ~{Math.round(g.screenAngle)}° of your view
        {g.offAxis > 1 ? ` - ${Math.round(g.offAxis)}° off-centre` : " - dead centre"}
        <br />Illustrative preview from the hall's layout, not a photo.
      </div>
    </div>
  );
}
