import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { useCity } from "../city";
import ReminderOptIn from "./ReminderOptIn";

export default function Navbar({ search, onSearch }: { search: string; onSearch: (q: string) => void }) {
  const { user, openSignIn, logout } = useAuth();
  const { city, openPicker } = useCity();
  const nav = useNavigate();
  return (
    <header className="topbar">
      <div className="topbar-in">
        <Link to="/" className="brand">ticket<span>show</span></Link>
        <input
          className="searchbox" placeholder="Search for movies, genres or languages"
          value={search} aria-label="Search movies"
          onChange={(e) => { onSearch(e.target.value); nav("/"); }}
        />
        <button className="citybtn" onClick={openPicker} aria-label="Change city">📍 {city ?? "Select city"} ▾</button>
        {user ? (
          <details className="usermenu">
            <summary>{user.email ?? user.phone}</summary>
            <div className="menu">
              <Link to="/bookings">My bookings</Link>
              <ReminderOptIn compact />
              {user.is_admin && <Link to="/admin">Admin</Link>}
              <button className="link" onClick={logout}>Log out</button>
            </div>
          </details>
        ) : (
          <button className="primary signin" onClick={() => openSignIn()}>Sign in</button>
        )}
      </div>
    </header>
  );
}
