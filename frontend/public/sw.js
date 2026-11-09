// Service worker: wakes up when the server pushes a message and shows a normal
// system notification - even when the website is closed.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (e) { d = { title: "Ticketshow", body: event.data && event.data.text() }; }
  event.waitUntil(
    self.registration.showNotification(d.title || "Ticketshow", {
      body: d.body || "",
      tag: d.tag,                       // same booking never stacks two notifications
      icon: "/icon.svg",
      badge: "/icon.svg",
      requireInteraction: true,         // stays until you act on it
      data: d,
      actions: [{ action: "directions", title: "Directions" }, { action: "here", title: "I'm here" }],
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  const d = event.notification.data || {};
  event.notification.close();
  // "Directions" (or tapping the notification itself) opens Google Maps; "I'm here" opens My bookings.
  const url = event.action === "here" ? "/bookings" : d.url || "/bookings";
  event.waitUntil(self.clients.openWindow(url));
});
