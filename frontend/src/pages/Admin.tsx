import { useState, type FormEvent } from "react";
import { api, money, when } from "../api";
import { ErrorBanner, Notice, StatusBadge } from "../components/ui";
import { errText, useLoad } from "../hooks";

function seatTypes(layout: unknown[]): string[] {
  const types = new Set<string>();
  for (const row of layout as { seats: (string | null)[] }[])
    for (const t of row.seats) if (t) types.add(t);
  return [...types];
}

export default function AdminPage() {
  const [tab, setTab] = useState<"showtime" | "bookings">("showtime");
  return (
    <>
      <h1>Admin</h1>
      <div className="seg">
        <button className={tab === "showtime" ? "primary" : ""} onClick={() => setTab("showtime")}>Create showtime</button>
        <button className={tab === "bookings" ? "primary" : ""} onClick={() => setTab("bookings")}>Bookings</button>
      </div>
      {tab === "showtime" ? <ShowtimeForm /> : <Bookings />}
    </>
  );
}

function ShowtimeForm() {
  const movies = useLoad(api.movies);
  const screens = useLoad(api.screens);
  const [movieId, setMovieId] = useState("");
  const [screenId, setScreenId] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [prices, setPrices] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [newMins, setNewMins] = useState("120");
  const [newGenre, setNewGenre] = useState("");
  const [newLang, setNewLang] = useState("English");
  const [newRating, setNewRating] = useState("");
  const [newPoster, setNewPoster] = useState("");

  const screen = screens.data?.find((s) => String(s.id) === screenId);
  const types = screen ? seatTypes(screen.layout) : [];

  async function submit(e: FormEvent) {
    e.preventDefault(); setError(null); setOk(null);
    try {
      const st = await api.createShowtime({
        movie_id: Number(movieId), screen_id: Number(screenId),
        starts_at: new Date(startsAt).toISOString(),
        prices: Object.fromEntries(types.map((t) => [t, Number(prices[t])])),
      });
      setOk(`Showtime #${st.id} created for ${st.movie_title}.`);
    } catch (err) { setError(errText(err)); }
  }

  async function addMovie(e: FormEvent) {
    e.preventDefault(); setError(null);
    try {
      await api.createMovie({
        title: newTitle, duration_minutes: Number(newMins), genre: newGenre, language: newLang,
        rating: newRating ? Number(newRating) : undefined, poster_url: newPoster || undefined,
      });
      setNewTitle(""); setNewPoster(""); setNewGenre(""); setNewRating(""); movies.reload();
    }
    catch (err) { setError(errText(err)); }
  }

  return (
    <>
      <ErrorBanner message={error ?? movies.error ?? screens.error} />
      {ok && <Notice>{ok}</Notice>}
      <form className="card narrow wide" onSubmit={submit}>
        <h3>New showtime</h3>
        <label>Movie
          <select required value={movieId} onChange={(e) => setMovieId(e.target.value)}>
            <option value="">Select...</option>
            {movies.data?.map((m) => <option key={m.id} value={m.id}>{m.title}</option>)}
          </select>
        </label>
        <label>Screen
          <select required value={screenId} onChange={(e) => setScreenId(e.target.value)}>
            <option value="">Select...</option>
            {screens.data?.map((s) => <option key={s.id} value={s.id}>#{s.id} {s.name}</option>)}
          </select>
        </label>
        <label>Starts at
          <input type="datetime-local" required value={startsAt} onChange={(e) => setStartsAt(e.target.value)} />
        </label>
        {types.map((t) => (
          <label key={t}>Price for "{t}" seats (₹)
            <input type="number" min="0.01" step="0.01" required value={prices[t] ?? ""}
              onChange={(e) => setPrices({ ...prices, [t]: e.target.value })} />
          </label>
        ))}
        <button className="primary">Create showtime</button>
      </form>

      <form className="card narrow wide" onSubmit={addMovie}>
        <h3>New movie</h3>
        <label>Title<input required value={newTitle} onChange={(e) => setNewTitle(e.target.value)} /></label>
        <label>Duration (minutes)<input type="number" min="1" max="600" required value={newMins} onChange={(e) => setNewMins(e.target.value)} /></label>
        <label>Genre (e.g. Action/Comedy)<input value={newGenre} onChange={(e) => setNewGenre(e.target.value)} /></label>
        <label>Language<input value={newLang} onChange={(e) => setNewLang(e.target.value)} /></label>
        <label>Rating (0-10)<input type="number" min="0" max="10" step="0.1" value={newRating} onChange={(e) => setNewRating(e.target.value)} /></label>
        <label>Poster image URL (optional - a poster is generated if empty)<input type="url" placeholder="https://..." value={newPoster} onChange={(e) => setNewPoster(e.target.value)} /></label>
        <button>Add movie</button>
      </form>
    </>
  );
}

function Bookings() {
  const [status, setStatus] = useState("");
  const { data, error, loading, reload } = useLoad(() => api.adminBookings(status || undefined), [status]);
  return (
    <>
      <div className="actions">
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          <option value="pending_payment">Pending payment</option>
          <option value="confirmed">Confirmed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <button onClick={reload} disabled={loading}>Refresh</button>
      </div>
      <ErrorBanner message={error} />
      <div className="table-wrap">
        <table>
          <thead><tr><th>#</th><th>User</th><th>Movie</th><th>Showing</th><th>Seats</th><th>Total</th><th>Status</th></tr></thead>
          <tbody>
            {data?.map((b) => (
              <tr key={b.id}>
                <td>{b.id}</td><td>{b.user_id}</td><td>{b.movie_title}</td><td>{when(b.starts_at)}</td>
                <td>{b.seat_labels.join(", ")}</td><td className="num">{money(b.total_cents)}</td>
                <td><StatusBadge status={b.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data?.length === 0 && <p className="muted">No bookings.</p>}
    </>
  );
}
