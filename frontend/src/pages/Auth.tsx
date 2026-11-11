import { useState, type FormEvent } from "react";
import { api, type OtpSent, type OtpTarget } from "../api";
import { useAuth } from "../auth";
import { ErrorBanner } from "../components/ui";
import { errText } from "../hooks";

type Step = "start" | "email" | "emailcode" | "otp";

/** "Get Started" sign-in window: mobile number + code (SMS), email + code, or email
 *  + password. Google / Apple need OAuth keys: shown but disabled. */
export default function AuthModal({ onClose }: { onClose: () => void }) {
  const { loginWithPassword, signupWithPassword, loginWithOtp } = useAuth();
  const [step, setStep] = useState<Step>("start");
  const [signup, setSignup] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [target, setTarget] = useState<OtpTarget | null>(null);
  const [sent, setSent] = useState<OtpSent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  const digits = phone.replace(/\D/g, "");
  const phoneOk = /^\d{10}$/.test(digits);

  const submitPassword = (e: FormEvent) => { e.preventDefault(); run(() => (signup ? signupWithPassword : loginWithPassword)(email, password)); };
  const sendCode = (t: OtpTarget) => run(async () => {
    setSent(await api.otpRequest(t)); setTarget(t); setCode(""); setStep("otp");
  });
  const submitCode = (e: FormEvent) => { e.preventDefault(); if (target) run(() => loginWithOtp(target, code)); };

  const title = { start: "Get Started", email: signup ? "Create your account" : "Sign in with email", emailcode: "Sign in with an email code", otp: "Enter the code" }[step];
  const back = () => { setStep(step === "emailcode" ? "email" : "start"); setError(null); };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal signin" role="dialog" aria-label="Sign in" onClick={(e) => e.stopPropagation()}>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
        {step !== "start" && <button className="back link" onClick={back}>‹ Back</button>}
        <h2>{title}</h2>
        <ErrorBanner message={error} />

        {step === "start" && (
          <>
            <button className="provider" disabled title="Needs a Google OAuth client ID - not configured in this demo">
              <span className="g">G</span> Continue with Google <small>(not set up)</small>
            </button>
            <button className="provider" onClick={() => setStep("email")}>✉ Continue with Email</button>
            <button className="provider" disabled title="Needs an Apple developer account - not configured in this demo">
              <span></span> Continue with Apple <small>(not set up)</small>
            </button>
            <div className="or">OR</div>
            <form className="phone-row" onSubmit={(e) => { e.preventDefault(); if (phoneOk) sendCode({ phone: "+91" + digits }); }}>
              <span className="cc">🇮🇳 +91</span>
              <input type="tel" inputMode="numeric" autoComplete="tel-national" placeholder="Mobile number" value={phone} maxLength={14}
                onChange={(e) => setPhone(e.target.value)} aria-label="Mobile number" />
              <button className="primary" disabled={!phoneOk || busy}>{busy ? "..." : "Get code"}</button>
            </form>
          </>
        )}

        {step === "email" && (
          <form onSubmit={submitPassword}>
            <label>Email<input type="email" required autoFocus autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label>Password<input type="password" required minLength={8} autoComplete={signup ? "new-password" : "current-password"}
              value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <button className="primary big full" disabled={busy}>{busy ? "Please wait..." : signup ? "Sign up" : "Log in"}</button>
            <p className="muted center">
              {signup ? "Have an account? " : "New here? "}
              <button type="button" className="link" onClick={() => { setSignup(!signup); setError(null); }}>{signup ? "Log in" : "Create an account"}</button>
            </p>
            <p className="center"><button type="button" className="link" onClick={() => { setStep("emailcode"); setError(null); }}>Email me a code instead</button></p>
          </form>
        )}

        {step === "emailcode" && (
          <form onSubmit={(e) => { e.preventDefault(); sendCode({ email: email.trim() }); }}>
            <label>Email<input type="email" required autoFocus autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <p className="muted small">No password needed. We'll send a 6-digit code to this address.</p>
            <button className="primary big full" disabled={busy}>{busy ? "Sending..." : "Send code"}</button>
          </form>
        )}

        {step === "otp" && sent && target && (
          <form onSubmit={submitCode}>
            {sent.channel === "screen" ? (
              <div className="banner info">Demo mode: no SMS or email is sent. Your code is <strong>{sent.dev_otp}</strong></div>
            ) : (
              <p className="muted center">We {sent.channel === "sms" ? "sent a text message" : "emailed a code"} to <strong>{sent.destination}</strong>. It can take a minute to arrive.</p>
            )}
            <input className="otp" type="text" inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={6}
              placeholder="------" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} aria-label="One-time code" />
            <button className="primary big full" disabled={code.length !== 6 || busy}>{busy ? "Verifying..." : "Verify & continue"}</button>
            <p className="muted center"><button type="button" className="link" onClick={() => sendCode(target)} disabled={busy}>Resend code</button></p>
          </form>
        )}

        <p className="terms muted small">By continuing, you agree to this demo's terms. No real payments are made.</p>
      </div>
    </div>
  );
}
