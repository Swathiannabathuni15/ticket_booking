import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError, money, when, type ShareView } from "../api";
import { ErrorBanner, Loading, StatusBadge } from "../components/ui";
import { errText } from "../hooks";

/** Public page: a friend opens their signed link. No login. */
export default function SharePayPage() {
  const token = useParams().token!;
  const [view, setView] = useState<ShareView | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.viewShare(token).then(setView).catch((e) => {
      setFatal(e instanceof ApiError && e.status === 410 ? "This payment link has expired. Ask your friend for a new one."
        : e instanceof ApiError && e.status === 404 ? "This payment link isn't valid. Check you copied the whole link."
        : errText(e));
    });
  }, [token]);

  async function pay() {
    setBusy(true); setError(null);
    try { const s = await api.payShare(token); setView((v) => v && { ...v, status: s.status }); }
    catch (e) { setError(errText(e)); }
    finally { setBusy(false); }
  }

  if (fatal) return <div className="card narrow"><h1>Link problem</h1><p className="error-text">{fatal}</p></div>;
  if (!view) return <Loading />;
  return (
    <div className="card narrow">
      <h1>Pay your share</h1>
      <h3>{view.movie_title}</h3>
      <p className="muted">{when(view.starts_at)} - seats {view.seats.join(", ")}</p>
      <p>Hi {view.name}, your share is</p>
      <p className="total">{money(view.amount_cents)} <StatusBadge status={view.status} /></p>
      <ErrorBanner message={error} />
      {view.status === "unpaid" && <button className="primary" disabled={busy} onClick={pay}>{busy ? "Processing..." : `Pay ${money(view.amount_cents)}`}</button>}
      {view.status === "paid" && <p className="success">Paid - thank you!</p>}
      {view.status === "cancelled" && <p className="error-text">This split was cancelled. Nothing to pay.</p>}
      <p className="muted small">Demo payment: no real card is charged.</p>
    </div>
  );
}
