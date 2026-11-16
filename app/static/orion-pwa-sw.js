/* Orion PWA: notifications only. NEVER cache authenticated responses or data. */
"use strict";
self.addEventListener("install", event => { event.waitUntil(self.skipWaiting()); });
self.addEventListener("activate", event => { event.waitUntil(self.clients.claim()); });
self.addEventListener("push", event => {
  // An Apple push event must result in a visible notification. Only server-
  // defined pilot content is accepted; no private booking information here.
  const shown = self.registration.showNotification("Orion Agenda", {
    body: "Notificações ativadas! Este é um teste do painel Orion.",
    icon: "/pwa/icon-192.png",
    badge: "/pwa/icon-192.png",
    tag: "orion-admin-push-test",
    renotify: true,
    data: { url: "/painel" }
  });
  event.waitUntil(Promise.all([shown, self.registration.setAppBadge?.(1)].filter(Boolean)));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  event.waitUntil((async () => {
    const pages = await self.clients.matchAll({type:"window",includeUncontrolled:true});
    const current = pages.find(page => {
      try { return new URL(page.url).pathname === "/painel"; } catch (_) { return false; }
    });
    if (current) return current.focus();
    return self.clients.openWindow("/painel");
  })());
});
