import { describe, expect, it } from "vitest";
import type { Seat } from "./api";
import { HALL, layoutHall, viewFrom } from "./components/hallLayout";
import { buildHallScene } from "./components/hallScene";

const seat = (label: string, status: Seat["status"] = "available", id = 0): Seat =>
  ({ id, label, seat_type: "gold", price: 200, status, available: status === "available" });

// 3 rows (A back ... C front), 5 columns with an aisle gap at column 3 (so 4 seats per row)
function hall(statusOf: (label: string) => Seat["status"] = () => "available"): Seat[] {
  let id = 1;
  const out: Seat[] = [];
  for (const row of ["A", "B", "C"]) for (const n of [1, 2, 4, 5]) out.push(seat(`${row}${n}`, statusOf(`${row}${n}`), id++));
  return out;
}

describe("layoutHall", () => {
  const layout = layoutHall(hall());
  const at = (label: string) => layout.seats.find((p) => p.seat.label === label)!;

  it("places every seat and leaves the aisle gap empty", () => {
    expect(layout.seats).toHaveLength(12);
    expect(layout.nRows).toBe(3);
    expect(layout.nCols).toBe(5);
  });
  it("mirrors left and right around the middle", () => {
    expect(at("A1").x).toBeCloseTo(-at("A5").x);
    expect(at("A2").x).toBeCloseTo(-at("A4").x);
  });
  it("puts the front row nearest the screen and the back row highest", () => {
    expect(at("C1").z).toBeCloseTo(HALL.screenGap);
    expect(at("A1").z).toBeCloseTo(HALL.screenGap + 2 * HALL.rowDepth);
    expect(at("A1").y).toBeGreaterThan(at("B1").y);
    expect(at("C1").y).toBe(0);
  });
  it("looks from the seat's real eye height at the middle of the screen", () => {
    const v = viewFrom(layout, at("A2"));
    expect(v.position[1]).toBeCloseTo(at("A2").y + HALL.eyeAboveFloor);
    expect(v.target[0]).toBe(0);
    expect(v.target[2]).toBe(0);
    expect(v.target[1]).toBeGreaterThan(HALL.screenBottom);
  });
  it("makes the screen wider for a wider hall", () => {
    const wide: Seat[] = Array.from({ length: 30 }, (_, i) => seat(`A${i + 1}`, "available", i + 1));
    expect(layoutHall(wide).screen.w).toBeGreaterThan(layout.screen.w);
  });
});

describe("buildHallScene (no WebGL needed to build it)", () => {
  const seats = hall((l) => (l === "B1" ? "booked" : l === "C5" ? "held" : "available"));
  const layout = layoutHall(seats);

  it("makes one instance per seat and a person in each taken seat", () => {
    const h = buildHallScene(layout, { title: "Cult" });
    expect(h.seatBases.count).toBe(12);
    expect(h.seatIds).toHaveLength(12);
    const people = h.scene.children.filter((c) => (c as { isInstancedMesh?: boolean }).isInstancedMesh && (c as { count: number }).count === 2);
    expect(people.length).toBe(2);                     // heads + bodies: 2 taken seats each
    h.dispose();
  });
  it("marks the chosen seat and clears the marker again", () => {
    const h = buildHallScene(layout, { title: "Cult", chosenId: null });
    const marker = h.scene.children.find((c) => (c as { geometry?: { type: string } }).geometry?.type === "CylinderGeometry")!;
    expect(marker.visible).toBe(false);
    h.setChosen(seats[0].id);
    expect(marker.visible).toBe(true);
    h.setChosen(null);
    expect(marker.visible).toBe(false);
    h.dispose();
  });
  it("colours seats by status", () => {
    const h = buildHallScene(layout, { title: "Cult", selectedIds: new Set([seats[0].id]) });
    const read = (i: number) => { const col = { r: 0, g: 0, b: 0 }; const a = h.seatBases.instanceColor!.array; col.r = a[i * 3]; col.g = a[i * 3 + 1]; col.b = a[i * 3 + 2]; return col; };
    const free = read(seats.findIndex((s) => s.label === "A2"));
    const sold = read(seats.findIndex((s) => s.label === "B1"));
    const mine = read(0);                              // selected: bright green
    expect(sold).not.toEqual(free);
    expect(mine).not.toEqual(free);
    h.dispose();
  });
});
