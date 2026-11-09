import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Movie, type Showtime } from "../api";
import { useCity } from "../city";
import { DirectionsButton } from "../components/Directions";
import Poster from "../components/Poster";
import { ErrorBanner, Loading } from "../components/ui";
import { useLoad } from "../hooks";

export const matches = (m: Movie, q: string) =>
  `${m.title} ${m.genre} ${m.language}`.toLowerCase().includes(q.trim().toLowerCase());

function Hero({ movies }: { movies: Movie[] }) {
  const [i, setI] = useState(0);
  const feat = movies.slice(0, 4);
  useEffect(() => {
    if (feat.length < 2) return;
    const t = setInterval(() => setI((n) => (n + 1) % feat.length), 5000);
    return () => clearInterval(t);
  }, [feat.length]);
  if (feat.length === 0) return null;
  const m = feat[i % feat.length];
  return (
    <section className="hero" aria-label="Featured">
      <div className="hero-art"><Poster movie={m} /></div>
      <div className="hero-text">
        <span className="hero-tag">NOW SHOWING</span>
        <h1>{m.title}</h1>
        <p>{[m.genre, m.language, `${m.duration_minutes} min`].filter(Boolean).join(" • ")}</p>
        <Link className="btn primary big" to={`/movies/${m.id}`}>Book now</Link>
      </div>
      <div className="hero-dots">
        {feat.map((_, k) => <button key={k} className={k === i ? "on" : ""} onClick={() => setI(k)} aria-label={`Show featured movie ${k + 1}`} />)}
      </div>
    </section>
  );
}

export function MovieCard({ m, index = 0 }: { m: Movie; index?: number }) {
  return (
    <Link to={`/movies/${m.id}`} className="mcard" style={{ animationDelay: `${Math.min(index, 12) * 40}ms` }}>
      <div className="mcard-img">
        <Poster movie={m} />
        {m.rating !== null && <div className="mcard-rate">★ {m.rating.toFixed(1)}/10</div>}
      </div>
      <h3>{m.title}</h3>
      <p className="muted">{[m.genre, m.language].filter(Boolean).join(" • ")}</p>
    </Link>
  );
}

export function MoviesPage({ search }: { search: string }) {
  const { city } = useCity();
  const { data, error, loading } = useLoad(() => api.movies(city), [city]);
  const shown = useMemo(() => (data ?? []).filter((m) => matches(m, search)), [data, search]);
  const languages = useMemo(() => [...new Set((data ?? []).map((m) => m.language))].sort(), [data]);
  const [lang, setLang] = useState<string | null>(null);
  const list = lang ? shown.filter((m) => m.language === lang) : shown;
  // One section per language, Telugu first (then A-Z); a search shows a single list.
  const groups = useMemo(() => {
    if (search) return list.length ? ([["Results", list]] as [string, Movie[]][]) : [];
    const by = new Map<string, Movie[]>();
    for (const m of list) by.set(m.language, [...(by.get(m.language) ?? []), m]);
    return [...by.entries()].sort(([a], [b]) => (a === "Telugu" ? -1 : b === "Telugu" ? 1 : a.localeCompare(b)));
  }, [list, search]);

  return (
    <>
      {!search && <Hero movies={[...(data ?? [])].sort((a, b) => Number(b.language === "Telugu") - Number(a.language === "Telugu") || (b.rating ?? 0) - (a.rating ?? 0))} />}
      {languages.length > 1 && (
        <div className="filters">
          <button className={`chip ${lang === null ? "on" : ""}`} onClick={() => setLang(null)}>All</button>
          {languages.map((l) => <button key={l} className={`chip ${lang === l ? "on" : ""}`} onClick={() => setLang(lang === l ? null : l)}>{l}</button>)}
        </div>
      )}
      <ErrorBanner message={error} />
      {loading && !data && <div className="mgrid">{Array.from({ length: 8 }, (_, i) => <div key={i} className="mcard skeleton" />)}</div>}
      {!loading && list.length === 0 && (
        <p className="muted">{search ? "No movies match your search." : `No movies are showing in ${city ?? "this city"} yet. Try another city.`}</p>
      )}
      {groups.map(([language, movies]) => (
        <section key={language} className="rise">
          <h2 className="section">
            {search ? `Results for "${search}"` : `${language} movies`}
            <span className="count">{movies.length}</span>
            {city && <span className="muted"> in {city}</span>}
          </h2>
          <div className="mgrid">{movies.map((m, i) => <MovieCard key={m.id} m={m} index={i} />)}</div>
        </section>
      ))}
    </>
  );
}

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;

export function MovieShowtimesPage() {
  const movieId = Number(useParams().id);
  const { city } = useCity();
  const movie = useLoad(() => api.movies(null).then((all) => all.find((m) => m.id === movieId) ?? null), [movieId]);
  const shows = useLoad(() => api.showtimes({ movieId, city }), [movieId, city]);

  const days = useMemo(() => {
    const keys = new Map<string, Date>();
    for (const s of shows.data ?? []) { const d = new Date(s.starts_at); keys.set(dayKey(d), d); }
    return [...keys.values()].sort((a, b) => +a - +b);
  }, [shows.data]);
  const [day, setDay] = useState<string | null>(null);
  const activeDay = day && days.some((d) => dayKey(d) === day) ? day : days[0] ? dayKey(days[0]) : null;

  const byTheatre = useMemo(() => {
    const m = new Map<string, Showtime[]>();
    for (const s of shows.data ?? []) {
      if (activeDay && dayKey(new Date(s.starts_at)) !== activeDay) continue;
      m.set(s.theatre_name, [...(m.get(s.theatre_name) ?? []), s]);
    }
    for (const list of m.values()) list.sort((a, b) => +new Date(a.starts_at) - +new Date(b.starts_at));
    return [...m.entries()];
  }, [shows.data, activeDay]);

  const mv = movie.data;
  return (
    <>
      {mv && (
        <section className="mhead">
          <div className="mhead-poster"><Poster movie={mv} /></div>
          <div>
            <h1>{mv.title}</h1>
            {mv.rating !== null && <p className="rate">★ {mv.rating.toFixed(1)}/10</p>}
            <p className="muted">{[mv.language, mv.genre, `${Math.floor(mv.duration_minutes / 60)}h ${mv.duration_minutes % 60}m`].filter(Boolean).join(" • ")}</p>
          </div>
        </section>
      )}
      <ErrorBanner message={shows.error ?? movie.error} />
      {shows.loading && !shows.data && <Loading />}
      {!shows.loading && days.length === 0 && <p className="muted">No showtimes{city ? ` in ${city}` : ""} for this movie. Try another city.</p>}
      <div className="daystrip">
        {days.map((d) => (
          <button key={dayKey(d)} className={`day ${activeDay === dayKey(d) ? "on" : ""}`} onClick={() => setDay(dayKey(d))}>
            <small>{d.toLocaleDateString(undefined, { weekday: "short" }).toUpperCase()}</small>
            <strong>{d.getDate()}</strong>
            <small>{d.toLocaleDateString(undefined, { month: "short" }).toUpperCase()}</small>
          </button>
        ))}
      </div>
      {byTheatre.map(([theatre, list]) => (
        <div className="card theatre" key={theatre}>
          <div>
            <strong>{theatre}</strong>
            <div className="muted small">{list[0].city} • {list[0].screen_name}</div>
            <div style={{ marginTop: 6 }}><DirectionsButton theatre={theatre} city={list[0].city} /></div>
          </div>
          <div className="times">
            {list.map((s) => (
              <Link key={s.id} to={`/showtimes/${s.id}`} className="timepill">
                {new Date(s.starts_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
              </Link>
            ))}
          </div>
        </div>
      ))}
    </>
  );
}
