import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorBanner } from "../components/ui";
import { errText } from "../hooks";

type Step = "start" | "email" | "phone" | "otp";

/** "Get Started" sign-in modal: email + password (real), mobile OTP (demo SMS),
 *  Google / Apple (need OAuth keys: shown but disabled). */
export default function AuthModal({ onClose }: { onClose: () => void }) {
  const { loginWithPassword, signupWithPassword, loginWithOtp } = useAuth();
  const [step, setStep] = useState<Step>("start");
  const [signup, setSignup] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [devOtp, setDevOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(errText(e)); } finally { setBusy(false); }
  }

  const fullPhone = "+91" + phone.replace(/\D/g, "");
  const phoneOk = /^\d{10}$/.test(phone.replace(/\D/g, ""));

  const submitEmail = (e: FormEvent) => { e.preventDefault(); run(() => (signup ? signupWithPassword : loginWithPassword)(email, password)); };
  const sendOtp = () => run(async () => {
    const r = await api.otpRequest(fullPhone);
    setDevOtp(r.dev_otp); setCode(""); setStep("otp");
  });
  const submitOtp = (e: FormEvent) => { e.preventDefault(); run(() => loginWithOtp(fullPhone, code)); };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal signin" role="dialog" aria-label="Sign in" onClick={(e) => e.stopPropagation()}>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
        {step !== "start" && <button className="back link" onClick={() => { setStep("start"); setError(null); }}>‹ Back</button>}
        <h2>{step === "start" ? "Get Started" : step === "email" ? (signup ? "Create your account" : "Sign in with email") : step === "otp" ? "Enter the code" : "Your mobile number"}</h2>
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
            <form className="phone-row" onSubmit={(e) => { e.preventDefault(); if (phoneOk) sendOtp(); }}>
              <span className="cc">🇮🇳 +91</span>
              <input inputMode="numeric" placeholder="Continue with mobile number" value={phone} maxLength={14}
                onChange={(e) => setPhone(e.target.value)} aria-label="Mobile number" />
              <button className="primary" disabled={!phoneOk || busy}>{busy ? "..." : "Continue"}</button>
            </form>
          </>
        )}

        {step === "email" && (
          <form onSubmit={submitEmail}>
            <label>Email<input type="email" required autoFocus autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label>Password<input type="password" required minLength={8} autoComplete={signup ? "new-password" : "current-password"}
              value={password} onChange={(e) => setPassword(e.target.value)} /></label>
            <button className="primary big full" disabled={busy}>{busy ? "Please wait..." : signup ? "Sign up" : "Log in"}</button>
            <p className="muted center">
              {signup ? "Have an account? " : "New here? "}
              <button type="button" className="link" onClick={() => { setSignup(!signup); setError(null); }}>{signup ? "Log in" : "Create an account"}</button>
            </p>
          </form>
        )}

        {step === "otp" && (
          <form onSubmit={submitOtp}>
            <p className="muted center">We sent a 6-digit code to {fullPhone}</p>
            {devOtp && <div className="banner info">Demo mode - no SMS is sent. Your code is <strong>{devOtp}</strong></div>}
            <input className="otp" inputMode="numeric" autoFocus maxLength={6} placeholder="------" value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} aria-label="One-time code" />
            <button className="primary big full" disabled={code.length !== 6 || busy}>{busy ? "Verifying..." : "Verify & continue"}</button>
            <p className="muted center"><button type="button" className="link" onClick={sendOtp} disabled={busy}>Resend code</button></p>
          </form>
        )}

        <p className="terms muted small">By continuing, you agree to this demo's terms. No real payments are made.</p>
      </div>
    </div>
  );
}
