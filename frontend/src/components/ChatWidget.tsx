import { useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { errText } from "../hooks";

type Msg = { who: "me" | "bot" | "err"; text: string };

/** Booking assistant. It can search and HOLD seats; payment always happens in
 *  the normal checkout page - the assistant has no way to pay. */
export default function ChatWidget() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([{ who: "bot", text: "Hi! Ask me to find a movie or hold seats. I can't pay - you do that at checkout." }]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  if (!user) return null;

  async function send(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy) return;
    setText(""); setBusy(true);
    setMsgs((m) => [...m, { who: "me", text: t }]);
    try { const { reply } = await api.chat(t); setMsgs((m) => [...m, { who: "bot", text: reply }]); }
    catch (err) { setMsgs((m) => [...m, { who: "err", text: errText(err) }]); }
    finally { setBusy(false); setTimeout(() => end.current?.scrollIntoView(), 0); }
  }

  return (
    <div className="chat">
      {open && (
        <div className="chat-panel card">
          <div className="chat-log">
            {msgs.map((m, i) => <p key={i} className={`chat-msg ${m.who}`}>{m.text}</p>)}
            {busy && <p className="chat-msg bot muted">Thinking...</p>}
            <div ref={end} />
          </div>
          <form onSubmit={send} className="friend">
            <input value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} placeholder="e.g. 2 seats for Skyline Drift" />
            <button className="primary" disabled={busy}>Send</button>
          </form>
        </div>
      )}
      <button className="primary chat-toggle" onClick={() => setOpen(!open)}>{open ? "Close" : "Ask assistant"}</button>
    </div>
  );
}
