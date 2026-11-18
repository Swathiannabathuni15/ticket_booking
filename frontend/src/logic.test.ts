import { describe, expect, it } from "vitest";
import { buildRows } from "./components/SeatMap";
import { equalShares } from "./pages/SplitPage";
import { friendlyMessage } from "./api";
import type { Seat } from "./api";

const seat = (label: string): Seat => ({ id: 1, label, seat_type: "standard", price: 10, status: "available", available: true });

describe("equalShares", () => {
  it("adds up exactly and gives leftover cents to the first people", () => {
    expect(equalShares(1000, 3)).toEqual([334, 333, 333]);
    for (const [t, n] of [[1, 3], [999, 7], [2500, 4], [1, 1]]) expect(equalShares(t, n).reduce((a, b) => a + b, 0)).toBe(t);
  });
});

describe("buildRows", () => {
  it("turns missing seat numbers into aisle gaps", () => {
    const rows = buildRows([seat("A1"), seat("A2"), seat("A4"), seat("B1"), seat("B2"), seat("B3"), seat("B4")]);
    expect(rows.map((r) => r.row)).toEqual(["A", "B"]);
    expect(rows[0].cells.map((c) => ("gap" in c ? "gap" : "seat"))).toEqual(["seat", "seat", "gap", "seat"]);
  });
});

describe("friendlyMessage", () => {
  it("covers the statuses the UI must handle", () => {
    expect(friendlyMessage(429, "", 7)).toContain("7s");
    expect(friendlyMessage(503, "")).toContain("unavailable");
    expect(friendlyMessage(409, "Seat taken")).toBe("Seat taken");
    expect(friendlyMessage(401, "")).toContain("log in");
  });
});

import { adjacentRun } from "./components/SeatMap";
describe("adjacentRun", () => {
  const mk = (label: string, status: Seat["status"] = "available", id = Number(label.slice(1))): Seat => ({ ...seat(label), id, status });
  const row = [mk("A1"), mk("A2"), mk("A3", "booked"), mk("A5"), mk("A6"), mk("A7")];
  it("selects qty adjacent seats starting at the click", () => {
    expect(adjacentRun(row, row[3], 3).map((s) => s.label)).toEqual(["A5", "A6", "A7"]);
  });
  it("shifts left when the right side is too short", () => {
    expect(adjacentRun(row, row[5], 2).map((s) => s.label)).toEqual(["A6", "A7"]);
  });
  it("never spans a booked seat or an aisle gap", () => {
    expect(adjacentRun(row, row[1], 3)).toEqual([]);
    expect(adjacentRun(row, row[0], 2).map((s) => s.label)).toEqual(["A1", "A2"]);
  });
});

import { seatGeometry } from "./components/SeatView";
describe("seatGeometry (view from seat)", () => {
  it("front rows see a bigger screen than back rows", () => {
    const back = seatGeometry(0, 17, 8, 18), front = seatGeometry(16, 17, 8, 18);
    expect(front.screenAngle).toBeGreaterThan(back.screenAngle);
    expect(back.eye).toBeGreaterThan(front.eye); // stadium rise
  });
  it("centre seats are dead centre, side seats are off-axis", () => {
    expect(seatGeometry(8, 17, 8.5, 18).offAxis).toBeLessThan(1);
    expect(seatGeometry(8, 17, 0, 18).offAxis).toBeGreaterThan(10);
  });
  it("rates a middle seat as great and the very front as poor", () => {
    expect(seatGeometry(6, 17, 8.5, 18).tone).toBe("great");
    expect(seatGeometry(16, 17, 8.5, 18).tone).toBe("poor");
  });
});

import { wrapTitle } from "./components/Poster";
import { matches } from "./pages/Movies";
describe("poster + search helpers", () => {
  it("wraps long titles onto several short lines", () => {
    expect(wrapTitle("The Salt Road")).toEqual(["The Salt", "Road"]);
    expect(wrapTitle("Cosmic Kitchen").every((l) => l.length <= 14)).toBe(true);
  });
  it("search matches title, genre and language case-insensitively", () => {
    const m = { id: 1, title: "Iron Harbour", duration_minutes: 100, poster_url: null, genre: "Action/Crime", language: "Tamil", rating: 8, release_date: null, status: "now_showing" as const };
    expect(matches(m, "tamil")).toBe(true);
    expect(matches(m, "  crime ")).toBe(true);
    expect(matches(m, "romance")).toBe(false);
  });
});

import { dueReminders } from "./reminders";
import { directionsUrl } from "./maps";
describe("reminders + directions", () => {
  const NOW = new Date("2026-11-08T12:00:00Z").getTime();
  const b = (id: number, minutesFromNow: number, status = "confirmed") => ({
    id, status, starts_at: new Date(NOW + minutesFromNow * 60000).toISOString(),
  }) as unknown as import("./api").Booking;
  it("reminds only for paid shows starting within 15 minutes", () => {
    const list = [b(1, 10), b(2, 16), b(3, -5), b(4, 14.9), b(5, 10, "pending_payment"), b(6, 10, "cancelled")];
    expect(dueReminders(list, NOW, new Set()).map((x) => x.id)).toEqual([1, 4]);
  });
  it("does not remind again after the person dismissed it", () => {
    expect(dueReminders([b(1, 10)], NOW, new Set([1]))).toEqual([]);
  });
  it("builds a Google Maps directions link from the theatre name and city", () => {
    expect(directionsUrl("Prasads Multiplex", "Hyderabad")).toBe(
      "https://www.google.com/maps/dir/?api=1&destination=Prasads%20Multiplex%2C%20Hyderabad");
  });
});

import { apiBase } from "./api";
describe("apiBase (so a phone finds the backend)", () => {
  const pc = { protocol: "http:", hostname: "localhost" };
  const phone = { protocol: "http:", hostname: "192.168.1.20" };
  it("uses the page's own address when nothing is configured", () => {
    expect(apiBase(undefined, pc)).toBe("http://localhost:8000");
    expect(apiBase(undefined, phone)).toBe("http://192.168.1.20:8000");
  });
  it("replaces a localhost setting when the page was opened from another address", () => {
    expect(apiBase("http://localhost:8000", pc)).toBe("http://localhost:8000");
    expect(apiBase("http://localhost:8000", phone)).toBe("http://192.168.1.20:8000");
  });
  it("keeps a real configured address", () => {
    expect(apiBase("https://api.example.com/", phone)).toBe("https://api.example.com");
  });
});

import { uuid } from "./api";
describe("uuid works without crypto.randomUUID (a phone on plain http)", () => {
  const RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  it("builds a valid v4 UUID from getRandomValues when randomUUID is missing", () => {
    const real = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", { value: { getRandomValues: real.getRandomValues.bind(real) }, configurable: true });
    try {
      const a = uuid(), b = uuid();
      expect(a).toMatch(RE);
      expect(a).not.toBe(b);
    } finally { Object.defineProperty(globalThis, "crypto", { value: real, configurable: true }); }
  });
  it("still works with no crypto at all (last resort)", () => {
    const real = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
    try { expect(uuid()).toMatch(RE); } finally { Object.defineProperty(globalThis, "crypto", { value: real, configurable: true }); }
  });
  it("uses the native one when available", () => { expect(uuid()).toMatch(RE); });
});

import { labelTone, viewWord } from "./components/viewQuality";
describe("smart picks wording", () => {
  it("turns the 0-100 view score into plain words", () => {
    expect(viewWord(100).word).toBe("Excellent view");
    expect(viewWord(80).tone).toBe("good");
    expect(viewWord(60).word).toBe("Fair view");
    expect(viewWord(30)).toEqual({ word: "Poor view", tone: "poor" });
  });
  it("colours each kind of option label", () => {
    expect(labelTone("Best view")).toBe("view");
    expect(labelTone("Best value")).toBe("value");
    expect(labelTone("Cheapest")).toBe("cheap");
    expect(labelTone("Closest to your budget")).toBe("warn");
    expect(labelTone("Also good")).toBe("plain");
  });
});

import { ticketCodeFrom } from "./api";

describe("ticketCodeFrom (what a scanned QR code or typed text means)", () => {
  it("reads the code out of the ticket link", () => {
    expect(ticketCodeFrom("https://example.com/ticket/ABCD-EFGH-JK23")).toBe("ABCD-EFGH-JK23");
  });
  it("accepts typed codes in any case, with spaces or no dashes", () => {
    expect(ticketCodeFrom("abcd efgh jk23")).toBe("ABCD-EFGH-JK23");
    expect(ticketCodeFrom("ABCDEFGHJK23")).toBe("ABCD-EFGH-JK23");
  });
  it("refuses anything that cannot be a ticket", () => {
    expect(ticketCodeFrom("hello")).toBeNull();
    expect(ticketCodeFrom("OOOO-OOOO-OOOO")).toBeNull();   // O, 0, I, 1 are never used
    expect(ticketCodeFrom("https://evil.example/login")).toBeNull();
  });
});
