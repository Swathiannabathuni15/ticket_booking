import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Unexpected error");

/** Load something once (and on reload()), tracking loading + error. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(errText(e));
    } finally {
      setLoading(false);
    }
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, deps);
  return { data, error, loading, reload, setData };
}

/** Seconds left until an ISO/epoch deadline, ticking every second. */
export function useCountdown(deadlineMs: number | null): number | null {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (deadlineMs === null) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [deadlineMs]);
  return deadlineMs === null ? null : Math.max(0, Math.ceil((deadlineMs - now) / 1000));
}

export const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
