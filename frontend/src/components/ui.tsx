import type { ReactNode } from "react";

export function ErrorBanner({ message, onClose }: { message: string | null; onClose?: () => void }) {
  if (!message) return null;
  return (
    <div className="banner error" role="alert">
      <span>{message}</span>
      {onClose && <button className="link" onClick={onClose}>dismiss</button>}
    </div>
  );
}

export function Notice({ children }: { children: ReactNode }) {
  return <div className="banner info" role="status">{children}</div>;
}

export function Loading({ text = "Loading..." }: { text?: string }) {
  return <p className="muted">{text}</p>;
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{status.replace("_", " ")}</span>;
}
