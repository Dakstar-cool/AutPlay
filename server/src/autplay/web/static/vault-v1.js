"use strict";
(() => {
  const page = document.querySelector("[data-vault-page]");
  if (!page) return;
  const link = page.querySelector("[data-vault-refresh]");
  const message = page.querySelector("[data-refresh-status]");
  const login = page.querySelector("[data-vault-login]");
  let active = null;
  link.addEventListener("click", async (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (active) return;
    const controller = new AbortController();
    active = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    link.setAttribute("aria-disabled", "true");
    page.setAttribute("aria-busy", "true");
    message.hidden = false;
    message.textContent = page.dataset.loading;
    message.classList.remove("vault-refresh-error");
    login.hidden = true;
    const view = page.querySelector("[data-vault-view]");
    try {
      const result = await fetch(page.dataset.refreshUrl, {
        credentials: "same-origin", cache: "no-store",
        signal: controller.signal, headers: { "Accept": "text/html" },
      });
      if (result.redirected || result.status === 401 || result.status === 403) {
        message.textContent = result.status === 403 ? page.dataset.denied : page.dataset.expired;
        login.hidden = false;
        throw new Error("authority unavailable");
      }
      if (!result.ok) throw new Error("Vault status unavailable");
      const received = new DOMParser().parseFromString(await result.text(), "text/html");
      const updated = received.querySelector("[data-vault-view]");
      if (!updated || updated.dataset.unavailable !== "false") throw new Error("invalid Vault status");
      view.replaceWith(updated);
      message.textContent = page.dataset.updated;
    } catch (_) {
      if (login.hidden) message.textContent = page.dataset.failed;
      message.classList.add("vault-refresh-error");
      view.classList.add("vault-stale");
      const badge = view.querySelector(".vault-overview .pill");
      if (badge) {
        badge.textContent = page.dataset.stale;
        badge.className = "pill pill-warn";
      }
    } finally {
      clearTimeout(timeout);
      active = null;
      link.removeAttribute("aria-disabled");
      page.removeAttribute("aria-busy");
    }
  });
  window.addEventListener("pagehide", () => active?.abort());
})();
