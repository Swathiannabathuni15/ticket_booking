import { useEffect, useRef, useState } from "react";
import { api, ticketCodeFrom, when, type CheckIn } from "../../api";
import { ErrorBanner } from "../../components/ui";
import { errText } from "../../hooks";
import { CheckInBanner } from "../Ticket";

// The browser's built-in QR reader (Chrome / Edge / Android). Where it is missing, staff type the code instead.
type Detector = { detect: (v: HTMLVideoElement) => Promise<{ rawValue: string }[]> };
const BarcodeDetectorCtor = (globalThis as unknown as { BarcodeDetector?: new (o: { formats: string[] }) => Detector }).BarcodeDetector;

/** At the door: scan or type a ticket code, see a big ADMIT / DO NOT ADMIT. */
export default function CheckInDesk() {
  const [code, setCode] = useState("");
  const [result, setResult] = useState<CheckIn | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const lastScan = useRef("");

  async function check(text: string) {
    const c = ticketCodeFrom(text);
    if (!c) { setError("That is not a ticket code. It looks like ABCD-EFGH-JKLM."); setResult(null); return; }
    setBusy(true); setError(null);
    try { setResult(await api.checkIn(c)); setCode(""); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  useEffect(() => {
    if (!scanning || !BarcodeDetectorCtor) return;
    let stream: MediaStream | null = null, stop = false;
    const detector = new BarcodeDetectorCtor({ formats: ["qr_code"] });
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        if (stop || !video.current) return;
        video.current.srcObject = stream;
        await video.current.play();
        while (!stop) {
          const found = await detector.detect(video.current).catch(() => []);
          const text = found[0]?.rawValue;
          if (text && text !== lastScan.current) { lastScan.current = text; await check(text); }
          await new Promise((r) => setTimeout(r, 300));
        }
      } catch {
        setError("Could not open the camera. Allow camera access, or type the code."); setScanning(false);
      }
    })();
    return () => { stop = true; stream?.getTracks().forEach((t) => t.stop()); };
  }, [scanning]);

  return (
    <div className="card checkin-desk">
      <h2>Check-in</h2>
      <p className="muted">Scan the QR code on the customer's ticket, or type the code printed under it.</p>
      <form className="row" onSubmit={(e) => { e.preventDefault(); check(code); }}>
        <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="ABCD-EFGH-JKLM" aria-label="Ticket code"
          autoCapitalize="characters" autoComplete="off" spellCheck={false} />
        <button className="primary" disabled={busy || !code.trim()}>{busy ? "Checking..." : "Check"}</button>
        {BarcodeDetectorCtor && (
          <button type="button" onClick={() => { lastScan.current = ""; setScanning((s) => !s); }}>{scanning ? "Stop camera" : "📷 Scan"}</button>
        )}
      </form>
      {scanning && <video ref={video} className="checkin-video" muted playsInline />}
      <ErrorBanner message={error} onClose={() => setError(null)} />
      {result && (
        <>
          <CheckInBanner result={result} />
          {result.ticket && (
            <p><strong>{result.ticket.movie_title}</strong> · {when(result.ticket.starts_at)} · {result.ticket.screen_name} ·
              Seats <strong>{result.ticket.seat_labels.join(", ")}</strong></p>
          )}
        </>
      )}
    </div>
  );
}
