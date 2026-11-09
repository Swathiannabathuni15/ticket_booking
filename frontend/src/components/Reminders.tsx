import { useEffect, useRef, useState } from "react";
import { api, ApiError, when, type Booking } from "../api";
import { useAuth } from "../auth";
import { pushState } from "../push";
import { dueReminders, loadDismissed, minutesLeft, saveDismissed } from "../reminders";
import { DirectionsButton } from "./Directions";

/** "Your movie starts in N minutes - have you reached the theatre?"
 *  Checks the person's paid bookings every minute while the site is open, shows a
 *  popup once per booking, and (if they allow it) also a browser notification when
 *  the tab is in the background. Limit: it only works while the website is open in
 *  a tab; real "push" messages to a closed browser would need a push server. */
export default function Reminders() {
  const { user } = useAuth();
  const [due, setDue] = useState<Booking[]>([]);
  const [now, setNow] = useState(Date.now());
  const dismissed = useRef<Set<number>>(loadDismissed());
  const notified = useRef<Set<number>>(new Set());
  const [perm, setPerm] = useState<NotificationPermission | "unsupported">(
    typeof Notification === "undefined" ? "unsupported" : Notification.permission,
  );

  useEffect(() => {
    if (!user) { setDue([]); return; }
    let stop = false;
    async function check() {
      try {
        const all = await api.myBookings();
        if (stop) return;
        const t = Date.now();
        setNow(t);
        const pushOn = (await pushState()) === "on";     // the system notification covers it
        const list = pushOn ? [] : dueReminders(all, t, dismissed.current);
        setDue(list);
        for (const b of list) {
          if (!notified.current.has(b.id) && document.hidden && typeof Notification !== "undefined" && Notification.permission === "granted") {
            notified.current.add(b.id);
            new Notification(`${b.movie_title} starts in ${minutesLeft(b, t)} min`, { body: `Have you reached ${b.theatre_name}?` });
          }
        }
      } catch (e) { if (!(e instanceof ApiError)) throw e; /* offline or signed out: try again next minute */ }
    }
    check();
    const id = setInterval(check, 60_000);
    const onVis = () => { if (!document.hidden) check(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { stop = true; clearInterval(id); document.removeEventListener("visibilitychange", onVis); };
  }, [user]);

  function dismiss(id: number) {
    dismissed.current.add(id);
    saveDismissed(dismissed.current);
    setDue((d) => d.filter((b) => b.id !== id));
  }

  const b = due[0];
  if (!user || !b) return null;
  return (
    <div className="modal-backdrop" role="alertdialog" aria-label="Your movie is about to start">
      <div className="modal reminder">
        <div className="reminder-icon">🎬</div>
        <h2>Your movie starts in {minutesLeft(b, now)} min</h2>
        <p><strong>{b.movie_title}</strong><br />{b.theatre_name}, {b.city} - {when(b.starts_at)}<br />Seats {b.seat_labels.join(", ")}</p>
        <p className="reminder-q">Have you reached the theatre?</p>
        <div className="modal-actions">
          <button className="primary big" onClick={() => dismiss(b.id)}>Yes, I'm here</button>
          <DirectionsButton theatre={b.theatre_name} city={b.city} />
          <button className="big" onClick={() => dismiss(b.id)}>Dismiss</button>
        </div>
        {perm === "default" && (
          <p className="small"><button className="link" onClick={async () => setPerm(await Notification.requestPermission())}>
            Also notify me when this tab is in the background</button></p>
        )}
        {due.length > 1 && <p className="muted small">+{due.length - 1} more show{due.length > 2 ? "s" : ""} starting soon</p>}
      </div>
    </div>
  );
}
