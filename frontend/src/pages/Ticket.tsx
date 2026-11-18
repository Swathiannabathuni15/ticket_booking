import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, money, type CheckIn, type TicketPublic, type TicketState } from "../api";
import { useAuth } from "../auth";
import Poster from "../components/Poster";
import { ErrorBanner, Loading } from "../components/ui";
import { errText, useLoad } from "../hooks";

const STATE_TEXT: Record<TicketState, string> = { valid: "Valid", used: "Used", cancelled: "Cancelled" };

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const time = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** The top half of the ticket: poster, movie, when and where, seats. Shared by both pages. */
function TicketHead({ t }: { t: TicketPublic }) {
  return (
    <div className="ticket-head">
      <Poster movie={{ title: t.movie_title, genre: "", poster_url: t.poster_url }} className="ticket-poster" />
      <div className="ticket-info">
        <div className={`ticket-state ${t.state}`}>{STATE_TEXT[t.state]}</div>
        <h2>{t.movie_title}</h2>
        <div className="ticket-when"><strong>{day(t.starts_at)}</strong> · {time(t.starts_at)}</div>
        <div className="muted">{t.theatre_name}, {t.city}</div>
        <div className="ticket-seats">
          <span className="muted">{t.screen_name}</span>
          <strong>{t.seat_labels.join(", ")}</strong>
          <span className="muted">{t.seat_labels.length} {t.seat_labels.length === 1 ? "ticket" : "tickets"}</span>
        </div>
      </div>
    </div>
  );
}

/** /bookings/:id/ticket - the owner's e-ticket with the QR code to show at the door. */
export function TicketPage() {
  const bookingId = Number(useParams().id);
  const { data: t, error, loading } = useLoad(() => api.ticket(bookingId), [bookingId]);
  const [msg, setMsg] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true); setMsg(null); setActionError(null);
    try { const m = await fn(); if (m) setMsg(m); } catch (e) { setActionError(errText(e)); } finally { setBusy(false); }
  }

  if (loading && !t) return <Loading />;
  if (!t) return <><ErrorBanner message={error} /><Link to="/bookings">My bookings</Link></>;

  return (
    <div className="ticket-page">
      <div className="no-print"><Link to="/bookings">← My bookings</Link></div>
      <ErrorBanner message={actionError} onClose={() => setActionError(null)} />
      {msg && <p className="success no-print">{msg}</p>}

      <article className={`ticket ${t.state}`} aria-label="E-ticket">
        <TicketHead t={t} />
        <div className="ticket-tear" aria-hidden="true" />
        <div className="ticket-qr">
          {t.state === "cancelled"
            ? <p className="error-text">This booking was cancelled. The ticket is no longer valid.</p>
            : <img src={t.qr_image} alt={`QR code for ticket ${t.ticket_code}`} width={220} height={220} />}
          <div className="ticket-code">{t.ticket_code}</div>
          {t.state === "valid" && <p className="muted">Show this QR code at the entrance. Brightness up helps the scanner.</p>}
          {t.state === "used" && t.checked_in_at && <p className="muted">Checked in at {time(t.checked_in_at)}. Enjoy the movie!</p>}
          <div className="ticket-total"><span className="muted">Amount paid</span><strong>{money(t.total_cents)}</strong></div>
        </div>
      </article>

      {t.state !== "cancelled" && (
        <div className="actions ticket-actions no-print">
          <button disabled={busy} onClick={() => run(async () => `Sent to ${(await api.emailTicket(t.booking_id)).sent_to}.`)}>✉️ Email me</button>
          <button disabled={busy} onClick={() => run(() => api.ticketCalendar(t.booking_id))}>📅 Add to calendar</button>
          <button onClick={() => window.print()}>🖨️ Print</button>
          <a className="btn" href={t.directions_url} target="_blank" rel="noreferrer">🧭 Directions</a>
        </div>
      )}
    </div>
  );
}

const RESULT_CLASS: Record<CheckIn["result"], string> = {
  admitted: "ok", already_used: "bad", cancelled: "bad", invalid: "bad", too_early: "warn", expired: "warn",
};

/** Big coloured answer for staff at the door. */
export function CheckInBanner({ result }: { result: CheckIn }) {
  return (
    <div className={`checkin-banner ${RESULT_CLASS[result.result]}`} role="status">
      <strong>{result.result === "admitted" ? "✔ ADMIT" : result.result === "too_early" || result.result === "expired" ? "⚠ CHECK" : "✖ DO NOT ADMIT"}</strong>
      <span>{result.message}</span>
    </div>
  );
}

/** /ticket/:code - what a phone camera opens when it scans the QR code. Anyone sees whether the ticket is valid
 *  (no personal details); staff also get an Admit button. */
export function PublicTicketPage() {
  const code = useParams().code ?? "";
  const { user } = useAuth();
  const { data: t, error, loading, reload } = useLoad(() => api.publicTicket(code), [code]);
  const [result, setResult] = useState<CheckIn | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function admit() {
    setBusy(true); setActionError(null);
    try { setResult(await api.checkIn(code)); reload(); } catch (e) { setActionError(errText(e)); } finally { setBusy(false); }
  }

  if (loading && !t) return <Loading />;
  if (!t) return <div className="ticket-page"><ErrorBanner message={error} /><p className="muted">Check the code and try again.</p></div>;

  return (
    <div className="ticket-page">
      {result && <CheckInBanner result={result} />}
      <ErrorBanner message={actionError} onClose={() => setActionError(null)} />
      <article className={`ticket ${t.state}`} aria-label="Ticket">
        <TicketHead t={t} />
        <div className="ticket-tear" aria-hidden="true" />
        <div className="ticket-qr">
          <div className="ticket-code">{t.ticket_code}</div>
          {t.state === "used" && t.checked_in_at && <p className="muted">Checked in {day(t.checked_in_at)}, {time(t.checked_in_at)}.</p>}
          {t.state === "cancelled" && <p className="error-text">This booking was cancelled.</p>}
        </div>
      </article>
      {user?.is_admin && t.state === "valid" && (
        <div className="actions ticket-actions">
          <button className="primary big" disabled={busy} onClick={admit}>{busy ? "Checking..." : "Admit"}</button>
        </div>
      )}
    </div>
  );
}
