import { useEffect, useState } from "react";
import { api } from "../api";
import { disablePush, enablePush, pushState, type PushState } from "../push";
import { errText } from "../hooks";

/** Turn phone / desktop reminders ("your movie starts in 15 minutes") on or off. */
export default function ReminderOptIn({ compact = false }: { compact?: boolean }) {
  const [state, setState] = useState<PushState | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { pushState().then(setState); }, []);
  if (!state) return null;

  async function run(fn: () => Promise<void>, ok?: string) {
    setBusy(true); setMsg(null);
    try { await fn(); if (ok) setMsg(ok); } catch (e) { setMsg(e instanceof Error ? e.message : errText(e)); }
    finally { setBusy(false); setState(await pushState()); }
  }

  const note = {
    insecure: "Notifications need a secure (https) address. On this address they only work in a browser on the same computer as the server (localhost). To get them on a phone the site has to be served over https.",
    unsupported: "This browser can't show push notifications. On an iPhone, add the site to your Home Screen first (iOS 16.4 or newer).",
    denied: "Notifications are blocked for this site. Allow them in your browser's site settings, then try again.",
  } as const;

  return (
    <div className={compact ? "optin compact" : "optin card"}>
      {!compact && <strong>🔔 Get a notification before your movie</strong>}
      {state === "off" && (
        <button className="primary" disabled={busy} onClick={() => run(enablePush, "Done. You'll get a notification 15 minutes before your show.")}>
          {compact ? "🔔 Turn on ticket reminders" : "Turn on reminders"}
        </button>
      )}
      {state === "on" && (
        <>
          <span className="success">🔔 Reminders are on</span>
          <button disabled={busy} onClick={() => run(async () => { await api.pushTest(); }, "Test sent - check your notifications.")}>Send a test</button>
          <button className="link" disabled={busy} onClick={() => run(disablePush, "Reminders turned off.")}>Turn off</button>
        </>
      )}
      {(state === "insecure" || state === "unsupported" || state === "denied") && <span className="muted small">{note[state]}</span>}
      {msg && <span className="small"> {msg}</span>}
    </div>
  );
}
