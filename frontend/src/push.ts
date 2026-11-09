import { api } from "./api";

/** unsupported = this browser can't do push; insecure = the page isn't on HTTPS
 *  (push only works on https:// or localhost); denied = the person blocked it. */
export type PushState = "unsupported" | "insecure" | "denied" | "off" | "on";

export async function pushState(): Promise<PushState> {
  if (!window.isSecureContext) return "insecure";
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || typeof Notification === "undefined") return "unsupported";
  if (Notification.permission === "denied") return "denied";
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = await reg?.pushManager.getSubscription();
    return sub && Notification.permission === "granted" ? "on" : "off";
  } catch { return "off"; }
}

function keyBytes(base64Url: string): Uint8Array {
  const pad = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const raw = atob((base64Url + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** Ask permission, subscribe this device, and tell the server about it. */
export async function enablePush(): Promise<void> {
  if ((await Notification.requestPermission()) !== "granted") throw new Error("Notifications were not allowed.");
  await navigator.serviceWorker.register("/sw.js");
  const reg = await navigator.serviceWorker.ready;
  const { public_key } = await api.pushPublicKey();
  const sub = (await reg.pushManager.getSubscription()) ??
    (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) as BufferSource }));
  const json = sub.toJSON();
  await api.pushSubscribe({ endpoint: sub.endpoint, keys: { p256dh: json.keys!.p256dh, auth: json.keys!.auth } });
}

export async function disablePush(): Promise<void> {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = await reg?.pushManager.getSubscription();
  if (sub) {
    await api.pushUnsubscribe(sub.endpoint).catch(() => {});
    await sub.unsubscribe();
  }
}
