import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";

const KEY = "tb_city";
const POPULAR: { name: string; icon: string }[] = [
  { name: "Mumbai", icon: "🌉" }, { name: "Delhi-NCR", icon: "🏛️" }, { name: "Bengaluru", icon: "🌳" },
  { name: "Hyderabad", icon: "🕌" }, { name: "Chennai", icon: "🛕" }, { name: "Kolkata", icon: "🌁" },
  { name: "Pune", icon: "🏰" }, { name: "Ahmedabad", icon: "🕍" }, { name: "Kochi", icon: "🌴" },
  { name: "Chandigarh", icon: "🌿" },
];

type CityCtx = { city: string | null; setCity: (c: string) => void; openPicker: () => void };
const Ctx = createContext<CityCtx>(null!);
export const useCity = () => useContext(Ctx);

function readCity(): string | null {
  try { return localStorage.getItem(KEY); } catch { return null; }
}

export function CityProvider({ children }: { children: ReactNode }) {
  const [city, setCityState] = useState<string | null>(readCity);
  const [open, setOpen] = useState(readCity() === null); // first visit: ask for a city
  const setCity = (c: string) => {
    setCityState(c);
    try { localStorage.setItem(KEY, c); } catch { /* private mode: just not remembered */ }
    setOpen(false);
  };
  return (
    <Ctx.Provider value={{ city, setCity, openPicker: () => setOpen(true) }}>
      {children}
      {open && <CityModal current={city} onPick={setCity} onClose={city ? () => setOpen(false) : undefined} />}
    </Ctx.Provider>
  );
}

function CityModal({ current, onPick, onClose }: { current: string | null; onPick: (c: string) => void; onClose?: () => void }) {
  const [q, setQ] = useState("");
  const [live, setLive] = useState<string[]>([]);
  useEffect(() => { api.cities().then(setLive).catch(() => {}); }, []);

  const all = useMemo(() => {
    const names = new Set(POPULAR.map((p) => p.name));
    return [...POPULAR, ...live.filter((c) => !names.has(c)).map((name) => ({ name, icon: "📍" }))];
  }, [live]);
  const shown = all.filter((c) => c.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide-modal" role="dialog" aria-label="Select your city" onClick={(e) => e.stopPropagation()}>
        {onClose && <button className="x" onClick={onClose} aria-label="Close">×</button>}
        <input autoFocus className="city-search" placeholder="Search for your city" value={q} onChange={(e) => setQ(e.target.value)} />
        <h3 className="center">Popular cities</h3>
        <div className="city-grid">
          {shown.map((c) => (
            <button key={c.name} className={`city ${c.name === current ? "on" : ""}`} onClick={() => onPick(c.name)}>
              <span className="city-icon">{c.icon}</span>
              <span>{c.name}</span>
              {!live.includes(c.name) && <small className="muted">no shows yet</small>}
            </button>
          ))}
          {shown.length === 0 && <p className="muted">No city matches "{q}".</p>}
        </div>
      </div>
    </div>
  );
}
