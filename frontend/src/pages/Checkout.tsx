import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, money, uuid, when, type Booking, type PayMethod } from "../api";
import { useAuth } from "../auth";
import { payWithRazorpay } from "../razorpay";
import { TheatreMap } from "../components/Directions";
import ReminderOptIn from "../components/ReminderOptIn";
import { ErrorBanner, Loading, Notice, StatusBadge } from "../components/ui";
import { errText, mmss, useCountdown, useLoad } from "../hooks";

// One UUID per payment ATTEMPT, saved in the browser. If the connection drops,
// the phone sleeps, or the page reloads, the next click re-sends the SAME key,
// so the server and the bank recognise it as the same attempt and never charge twice.
const keyName = (bookingId: number) => `pay-key:${bookingId}`;
function attemptKey(bookingId: number): string {
  try {
    const saved = localStorage.getItem(keyName(bookingId));
    if (saved) return saved;
    const fresh = uuid();
    localStorage.setItem(keyName(bookingId), fresh);
    return fresh;
  } catch { return uuid(); } // storage blocked: still works, just not across reloads
}
function forgetKey(bookingId: number) {
  try { localStorage.removeItem(keyName(bookingId)); } catch { /* ignore */ }
}

export default function CheckoutPage() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const { data: bookings, error: loadError, loading, reload, setData } = useLoad(api.myBookings, [id]);
  const booking = bookings?.find((b) => b.id === id) ?? null;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [method, setMethod] = useState<PayMethod>("tok_ok");
  const { user } = useAuth();
  const [testCards, setTestCards] = useState(false);   // the demo card menu only exists when the server allows it
  const [razorpay, setRazorpay] = useState(false);     // real (test-mode) payments through Razorpay's window
  const [confirming, setConfirming] = useState(false); // paid in Razorpay's window; waiting for our server to confirm
  useEffect(() => {
    api.config().then((c) => { setTestCards(c.test_cards); setRazorpay(c.payment_provider === "razorpay"); })
      .catch(() => setTestCards(false));
  }, []);
  const due = booking?.payment_due_at ? new Date(booking.payment_due_at).getTime() : null;
  const processing = booking?.status === "pending_payment" && (booking.payment_status === "processing" || confirming);
  const left = useCountdown(booking?.status === "pending_payment" && !processing ? due : null);

  useEffect(() => { if (left === 0) reload(); }, [left, reload]);

  // The bank's confirmation arrives by webhook, not in our reply: keep checking.
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(() => { reload(); }, 2000);
    return () => clearInterval(t);
  }, [processing, reload]);
  useEffect(() => { if (booking?.payment_status === "paid") forgetKey(id); }, [booking?.payment_status, id]);

  function update(b: Booking) {
    setData((bookings ?? []).map((x) => (x.id === b.id ? b : x)));
  }

  /** Razorpay: our server makes ONE order (saved in the database), their window takes the payment, then our
   *  server checks the signature. If that last call is lost, the webhook confirms it and this page just waits. */
  async function payRazorpay() {
    const order = await api.razorpayOrder(id, attemptKey(id));      // the same order every time for this booking
    const r = await payWithRazorpay(order, user?.email);
    if (r.kind === "closed") {
      if (r.failure) setError(`${r.failure} Nothing was charged. You can try again.`);
      return;                                                       // closed without paying: key kept, same order next time
    }
    try {
      update(await api.razorpayVerify({ razorpay_order_id: r.orderId, razorpay_payment_id: r.paymentId, razorpay_signature: r.signature }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) { setError(errText(e)); return; }   // bad signature: not paid
      setConfirming(true);                                          // can't tell: the webhook will confirm; keep checking
    }
  }

  useEffect(() => { if (booking?.status !== "pending_payment") setConfirming(false); }, [booking?.status]);

  async function pay() {
    setBusy(true); setError(null);
    try {
      if (razorpay) { await payRazorpay(); reload(); return; }
      update(await api.pay(id, attemptKey(id), testCards ? method : "tok_ok"));   // 201 paid, 200 replay, 202 processing
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) {
        forgetKey(id);                                     // a clean decline: the next try is a NEW attempt
        setError(`${e.message} Nothing was charged. You can try again.`);
      } else if (e instanceof ApiError && e.status === 0) {
        // No answer at all: we can't know if it went through. KEEP the key so
        // pressing Pay again (or coming back later) cannot charge a second time.
        setError("We lost the connection, so we don't know if the payment went through. Press Pay again - you won't be charged twice.");
      } else { setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Unexpected error"); }
      reload();
    } finally { setBusy(false); }
  }

  async function cancel() {
    setBusy(true); setError(null);
    try { update(await api.cancelBooking(id)); forgetKey(id); }
    catch (e) { setError(errText(e)); }
    finally { setBusy(false); }
  }

  if (loading && !bookings) return <Loading />;
  if (loadError) return <ErrorBanner message={loadError} />;
  if (!booking) return <p>Booking not found. <Link to="/bookings">My bookings</Link></p>;

  return (
    <div className="card narrow">
      <h1>{booking.status === "confirmed" ? "You're all set!" : "Checkout"}</h1>
      <ErrorBanner message={error} onClose={() => setError(null)} />
      <h3>{booking.movie_title}</h3>
      <p className="muted">{when(booking.starts_at)}</p>
      <p>Seats: <strong>{booking.seat_labels.join(", ")}</strong></p>
      <p className="total">{money(booking.total_cents)} <StatusBadge status={booking.status} /></p>

      {processing && (
        <Notice>
          <span>
            <strong>Confirming your payment{razorpay ? "" : " with the bank"}...</strong><br />
            Please don't pay again. This page updates by itself, and it's safe to close it:
            your seats are kept and the booking is confirmed as soon as the bank replies.
          </span>
        </Notice>
      )}

      {booking.status === "pending_payment" && !processing && (
        <>
          <div className={`countdown ${left !== null && left < 60 ? "urgent" : ""}`}>
            Pay within {left !== null ? mmss(left) : "..."} or the seats are released
          </div>
          {testCards && <label>Demo card (no real money)
            <select value={method} onChange={(e) => setMethod(e.target.value as PayMethod)}>
              <option value="tok_ok">Card works - payment succeeds</option>
              <option value="tok_decline">Card declined</option>
              <option value="tok_timeout">Bank charges, but the reply is lost (webhook confirms)</option>
            </select>
          </label>}
          <div className="actions">
            <button className="primary big" disabled={busy} onClick={pay}>{busy ? "Processing..." : `Pay ${money(booking.total_cents)}`}</button>
            <button disabled={busy} onClick={cancel}>Cancel booking</button>
          </div>
        </>
      )}
      {booking.status === "pending_payment" && processing && (
        <div className="actions"><button onClick={() => reload()}>Check now</button></div>
      )}
      {booking.status === "confirmed" && (
        <>
          <p className="success">Payment received. Your tickets are confirmed.</p>
          <p className="muted">Your e-ticket with its QR code is ready. If your account has an email address, we have emailed it to you too.</p>
          <p><strong>{booking.theatre_name}</strong>, {booking.city}</p>
          <TheatreMap theatre={booking.theatre_name} city={booking.city} />
          <ReminderOptIn />
          <div className="actions">
            <Link className="btn primary" to={`/bookings/${booking.id}/ticket`}>🎟️ View e-ticket</Link>
            <button onClick={() => nav(`/bookings/${booking.id}/split`)}>Split the bill with friends</button>
            <Link className="btn" to="/bookings">My bookings</Link>
          </div>
        </>
      )}
      {booking.status === "cancelled" && (
        <>
          <p className="error-text">This booking was cancelled or expired, so the seats were released. If any money was taken, it is refunded automatically.</p>
          <Link className="btn" to="/">Pick new seats</Link>
        </>
      )}
    </div>
  );
}
