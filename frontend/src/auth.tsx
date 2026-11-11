import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, SESSION_EXPIRED, tokenStore, type OtpTarget, type User } from "./api";
import AuthModal from "./pages/Auth";

type AuthCtx = {
  user: User | null;
  loading: boolean;
  /** Open the "Get Started" modal; after a successful sign-in go to `after` (if given). */
  openSignIn: (after?: string) => void;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  signupWithPassword: (email: string, password: string) => Promise<void>;
  loginWithOtp: (target: OtpTarget, code: string) => Promise<void>;
  logout: () => void;
};

const Ctx = createContext<AuthCtx>(null!);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(!!tokenStore.get());
  const [modal, setModal] = useState<{ after?: string } | null>(null);

  useEffect(() => {
    if (!tokenStore.get()) return;
    api.me()
      .then((u) => {
        setUser(u);
        // Slide the session forward: opening the site renews the 30-day login.
        api.refresh().then((r) => tokenStore.set(r.access_token)).catch(() => { /* keep the current token */ });
      })
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false));
  }, []);

  // Any 401 from the API client means the token died: drop the session.
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener(SESSION_EXPIRED, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED, onExpired);
  }, []);

  const finish = useCallback(async (token: string) => {
    tokenStore.set(token);
    setUser(await api.me());
    const after = modal?.after;
    setModal(null);
    if (after) nav(after, { replace: true });
  }, [modal, nav]);

  const loginWithPassword = useCallback(async (email: string, password: string) => {
    await finish((await api.login(email, password)).access_token);
  }, [finish]);

  const signupWithPassword = useCallback(async (email: string, password: string) => {
    await api.signup(email, password);
    await loginWithPassword(email, password);
  }, [loginWithPassword]);

  const loginWithOtp = useCallback(async (target: OtpTarget, code: string) => {
    await finish((await api.otpVerify(target, code)).access_token);
  }, [finish]);

  const logout = useCallback(() => { tokenStore.clear(); setUser(null); }, []);
  const openSignIn = useCallback((after?: string) => setModal({ after }), []);

  return (
    <Ctx.Provider value={{ user, loading, openSignIn, loginWithPassword, signupWithPassword, loginWithOtp, logout }}>
      {children}
      {modal && <AuthModal onClose={() => setModal(null)} />}
    </Ctx.Provider>
  );
}
