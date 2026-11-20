import { useState } from "react";
import { Link } from "react-router-dom";
import { api, money, when } from "../api";
import { DirectionsButton } from "../components/Directions";
import { ErrorBanner, Loading, StatusBadge } from "../components/ui";
import { errText, useLoad } from "../hooks";

export default function MyBookingsPage() {
  const { data, error, loading, reload } = useLoad(api.myBookings);
  const [actionError, setActionError] = useState<string | null>(null);

  async function cancel(id: number) {
    if (!confirm("Cancel this booking?")) return;
    try { await api.cancelBooking(id); reload(); } catch (e) { setActionError(errText(e)); }
  }

  return (
    <>
      <h1>My bookings</h1>
      <ErrorBanner message={error ?? actionError} />
      {loading && !data && <Loading />}
      {data?.length === 0 && <p className="muted">No bookings yet. <Link to="/">Browse movies</Link></p>}
      <div className="list">
        {data?.map((b) => (
          <div key={b.id} className="card row">
            <div>
              <strong>{b.movie_title}</strong> <StatusBadge status={b.status} />
              <div className="muted">{b.theatre_name}, {b.city} - {when(b.starts_at)}</div>
              <div>Seats {b.seat_labels.join(", ")} - {money(b.total_cents)}</div>
            </div>
            <div className="actions">
              {b.status === "pending_payment" && <Link className="btn primary" to={`/checkout/${b.id}`}>Pay now</Link>}
              {b.status === "confirmed" && <Link className="btn primary" to={`/bookings/${b.id}/ticket`}>🎟️ E-ticket</Link>}
              {b.status === "confirmed" && <DirectionsButton theatre={b.theatre_name} city={b.city} />}
              {b.status === "confirmed" && <Link className="btn" to={`/bookings/${b.id}/split`}>Split bill</Link>}
              {b.status !== "cancelled" && <button onClick={() => cancel(b.id)}>Cancel</button>}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
