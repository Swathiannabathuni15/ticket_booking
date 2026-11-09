import type { Booking } from "./api";

export const REMINDER_MINUTES = 15;
const KEY = "reminder-dismissed";

/** Paid bookings whose show starts within the next `REMINDER_MINUTES` minutes
 *  (and hasn't started yet), minus the ones the person already dismissed. */
export function dueReminders(bookings: Booking[], now: number, dismissed: Set<number>): Booking[] {
  return bookings.filter((b) => {
    if (b.status !== "confirmed" || dismissed.has(b.id)) return false;
    const minutes = (new Date(b.starts_at).getTime() - now) / 60000;
    return minutes > 0 && minutes <= REMINDER_MINUTES;
  });
}

export function minutesLeft(b: Booking, now: number): number {
  return Math.max(1, Math.ceil((new Date(b.starts_at).getTime() - now) / 60000));
}

export function loadDismissed(): Set<number> {
  try { return new Set(JSON.parse(localStorage.getItem(KEY) ?? "[]") as number[]); } catch { return new Set(); }
}
export function saveDismissed(ids: Set<number>) {
  try { localStorage.setItem(KEY, JSON.stringify([...ids].slice(-200))); } catch { /* private mode */ }
}
