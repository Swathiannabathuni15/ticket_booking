import { useEffect, useState, type ReactNode } from "react";
import { BrowserRouter, Link, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { CityProvider } from "./city";
import Background from "./components/Background";
import ErrorBoundary from "./components/ErrorBoundary";
import ChatWidget from "./components/ChatWidget";
import Reminders from "./components/Reminders";
import Navbar from "./components/Navbar";
import { Loading } from "./components/ui";
import Admin from "./pages/Admin";
import Checkout from "./pages/Checkout";
import { MovieShowtimesPage, MoviesPage } from "./pages/Movies";
import MyBookings from "./pages/MyBookings";
import SeatPage from "./pages/SeatPage";
import SharePay from "./pages/SharePay";
import SplitPage from "./pages/SplitPage";

/** Not signed in? Open the sign-in modal over the page and, once signed in, come back here. */
function RequireAuth({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, loading, openSignIn } = useAuth();
  const loc = useLocation();
  useEffect(() => { if (!loading && !user) openSignIn(loc.pathname); }, [loading, user, openSignIn, loc.pathname]);
  if (loading) return <Loading />;
  if (!user) return <p className="muted">Please sign in to continue.</p>;
  if (admin && !user.is_admin) return <p className="error-text">Admins only.</p>;
  return <>{children}</>;
}

function Shell() {
  const [search, setSearch] = useState("");
  return (
    <>
      <Background />
      <Navbar search={search} onSearch={setSearch} />
      <main>
        <ErrorBoundary>
        <Routes>
          <Route path="/" element={<MoviesPage search={search} />} />
          <Route path="/movies/:id" element={<MovieShowtimesPage />} />
          <Route path="/showtimes/:id" element={<SeatPage />} />
          <Route path="/checkout/:id" element={<RequireAuth><Checkout /></RequireAuth>} />
          <Route path="/bookings" element={<RequireAuth><MyBookings /></RequireAuth>} />
          <Route path="/bookings/:id/split" element={<RequireAuth><SplitPage /></RequireAuth>} />
          <Route path="/admin" element={<RequireAuth admin><Admin /></RequireAuth>} />
          <Route path="/pay/share/:token" element={<SharePay />} />
          <Route path="*" element={<p>Page not found. <Link to="/">Home</Link></p>} />
        </Routes>
        </ErrorBoundary>
      </main>
      <footer className="footer">
        This product uses the TMDB API but is not endorsed or certified by TMDB. Demo project: no real payments.
      </footer>
      <Reminders />
      <ChatWidget />
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <CityProvider>
          <Shell />
        </CityProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
