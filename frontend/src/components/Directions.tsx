import { useState } from "react";
import { directionsUrl, mapEmbedUrl } from "../maps";

export function DirectionsButton({ theatre, city, primary = false }: { theatre: string; city: string; primary?: boolean }) {
  return (
    <a className={`btn ${primary ? "primary" : ""}`} href={directionsUrl(theatre, city)} target="_blank" rel="noopener noreferrer">
      📍 Get directions
    </a>
  );
}

/** A small embedded Google map of the theatre, shown on request. */
export function TheatreMap({ theatre, city }: { theatre: string; city: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="theatremap">
      <div className="actions">
        <DirectionsButton theatre={theatre} city={city} primary />
        <button onClick={() => setOpen(!open)}>{open ? "Hide map" : "Show map"}</button>
      </div>
      {open && (
        <iframe title={`Map of ${theatre}`} src={mapEmbedUrl(theatre, city)} loading="lazy" referrerPolicy="no-referrer-when-downgrade" />
      )}
    </div>
  );
}
