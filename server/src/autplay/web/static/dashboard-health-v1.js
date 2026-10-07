/* Refresh only authenticated health cards; expire readings after loss of contact. */
"use strict";
(() => {
  const grid = document.querySelector("[data-health-refresh]");
  if (!grid) return;
  let stopped = false;
  let lastSuccess = performance.now();
  let expiration = null;
  let expiresAt = null;
  function scheduleExpiry() {
    clearTimeout(expiration);
    const card = grid.querySelector('[data-component="component_worker"]');
    const ttl = Number(card?.dataset.validFor);
    expiresAt = card?.hasAttribute("data-valid-for") && Number.isFinite(ttl)
      ? performance.now() + Math.max(0, ttl) * 1000 : null;
    if (expiresAt !== null) expiration = setTimeout(expire, Math.max(0, ttl) * 1000);
  }
  function expire() {
    const card = grid.querySelector('[data-component="component_worker"]');
    if (!card) return;
    const badge = card.querySelector(".pill");
    badge.textContent = grid.dataset.staleLabel;
    badge.className = "pill pill-warn";
    card.querySelector(".health-detail").textContent = grid.dataset.staleDetail;
    card.querySelector(".health-metrics")?.remove();
  }
  async function refresh() {
    if (stopped) return;
    if (document.hidden) { setTimeout(refresh, 10000); return; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
      const result = await fetch(grid.dataset.healthRefresh, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
      });
      if (!result.ok || result.redirected) { expire(); stopped = true; return; }
      const documentValue = new DOMParser().parseFromString(await result.text(), "text/html");
      const updated = documentValue.querySelectorAll("[data-component]");
      if (updated.length !== 4) throw new Error("health response unavailable");
      for (const card of updated) {
        const current = grid.querySelector(`[data-component="${card.dataset.component}"]`);
        if (current && !current.contains(document.activeElement)) current.replaceWith(card);
      }
      lastSuccess = performance.now();
      scheduleExpiry();
    } catch (_) {
      if (performance.now() - lastSuccess >= 30000) expire();
    } finally {
      clearTimeout(timeout);
      if (!stopped) setTimeout(refresh, 10000);
    }
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && expiresAt !== null && performance.now() >= expiresAt) expire();
  });
  window.addEventListener("pagehide", () => { stopped = true; clearTimeout(expiration); });
  scheduleExpiry();
  setTimeout(refresh, 10000);
})();
