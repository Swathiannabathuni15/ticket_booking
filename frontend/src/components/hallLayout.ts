import type { Seat } from "../api";
import { buildRows } from "./SeatMap";

// All sizes in metres. The screen is at z = 0 on the wall; the audience sits at positive z
// (row A is the BACK of the hall, the last row is nearest the screen); y is up.
export const HALL = {
  rowDepth: 1.1,        // distance between rows
  seatPitch: 0.55,      // distance between seats in a row
  screenGap: 3,         // front row to screen
  rise: 0.22,           // each row further back sits this much higher (stadium seating)
  seatHeight: 0.45,     // seat cushion above its own row's floor
  eyeAboveFloor: 1.1,   // eyes above the floor of the row
  screenBottom: 1.4,    // bottom edge of the screen above the front floor
};

export type Placed = {
  seat: Seat; x: number; y: number; z: number;   // y = the floor of this seat's row
  rowIdx: number; colIdx: number; rowsFromScreen: number;
};
export type HallLayout = {
  seats: Placed[]; nRows: number; nCols: number;
  width: number; depth: number; backFloorY: number;
  screen: { w: number; h: number; bottom: number; cy: number };
};

export function layoutHall(seats: Seat[]): HallLayout {
  const rows = buildRows(seats);
  const nRows = rows.length;
  const nCols = rows[0]?.cells.length ?? 0;
  const placed: Placed[] = [];
  rows.forEach((row, rowIdx) => {
    const rowsFromScreen = nRows - 1 - rowIdx;
    row.cells.forEach((cell, colIdx) => {
      if (!("seat" in cell)) return;
      placed.push({
        seat: cell.seat, rowIdx, colIdx, rowsFromScreen,
        x: (colIdx - (nCols - 1) / 2) * HALL.seatPitch,
        y: rowsFromScreen * HALL.rise,
        z: HALL.screenGap + rowsFromScreen * HALL.rowDepth,
      });
    });
  });
  const width = nCols * HALL.seatPitch;
  const w = Math.max(9, width * 1.35);
  const h = w * 0.44;
  return {
    seats: placed, nRows, nCols, width,
    depth: HALL.screenGap + Math.max(0, nRows - 1) * HALL.rowDepth,
    backFloorY: Math.max(0, nRows - 1) * HALL.rise,
    screen: { w, h, bottom: HALL.screenBottom, cy: HALL.screenBottom + h / 2 },
  };
}

/** Where the camera sits and what it looks at, for a person in this seat. */
export function viewFrom(layout: HallLayout, p: Placed) {
  return {
    position: [p.x, p.y + HALL.eyeAboveFloor, p.z] as [number, number, number],
    target: [0, layout.screen.cy, 0] as [number, number, number],
  };
}
