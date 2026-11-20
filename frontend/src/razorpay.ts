// Razorpay's payment window ("Standard Checkout"). We only ever hold the PUBLIC key id and an order id the
// server made for the exact amount; card details are typed into Razorpay's own window, never into our page.
import type { RazorpayOrder } from "./api";

const SCRIPT = "https://checkout.razorpay.com/v1/checkout.js";

type Handler = (r: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }) => void;
type Options = {
  key: string; order_id: string; amount: number; currency: string; name: string; description: string;
  prefill?: { email?: string; contact?: string }; theme?: { color: string };
  handler: Handler; modal: { ondismiss: () => void };
};
type Instance = { open: () => void; on: (event: string, cb: (r: { error?: { description?: string } }) => void) => void };
declare global { interface Window { Razorpay?: new (o: Options) => Instance } }

let loading: Promise<void> | null = null;
/** Load Razorpay's script once, only when someone actually pays. */
export function loadRazorpay(): Promise<void> {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.Razorpay) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT; s.async = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; s.remove(); reject(new Error("Could not load the payment window. Check your connection (some office networks block it).")); };
    document.head.appendChild(s);
  });
  return loading;
}

export type RazorpayResult =
  | { kind: "paid"; orderId: string; paymentId: string; signature: string }
  | { kind: "closed"; failure: string | null };   // the window was closed; `failure` = why the last try failed, if it did

/** Open the window for an order and wait until the customer pays or closes it. */
export async function payWithRazorpay(order: RazorpayOrder, email?: string | null): Promise<RazorpayResult> {
  await loadRazorpay();
  const Ctor = window.Razorpay;
  if (!Ctor) throw new Error("The payment window is not available.");
  return new Promise((resolve) => {
    let failure: string | null = null;
    const rz = new Ctor({
      key: order.key_id, order_id: order.order_id, amount: order.amount, currency: order.currency,
      name: order.name, description: order.description, prefill: email ? { email } : undefined,
      theme: { color: "#e63e62" },
      handler: (r) => resolve({ kind: "paid", orderId: r.razorpay_order_id, paymentId: r.razorpay_payment_id, signature: r.razorpay_signature }),
      modal: { ondismiss: () => resolve({ kind: "closed", failure }) },
    });
    rz.on("payment.failed", (r) => { failure = r.error?.description ?? "The payment did not go through."; });
    rz.open();
  });
}
