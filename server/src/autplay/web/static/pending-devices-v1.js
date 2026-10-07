/* Refresh pending requests while preserving names being edited. */
"use strict";
(() => {
  let attempts = 0;
  let stopped = false;
  async function refresh() {
    if (stopped || attempts >= 180) return;
    const section = document.querySelector("[data-pending-devices]");
    if (!section) return;
    if (document.hidden || section.contains(document.activeElement) ||
        Array.from(section.querySelectorAll("input[name=device_name]"))
          .some(input => input.value !== input.defaultValue)) {
      setTimeout(refresh, 5000);
      return;
    }
    attempts += 1;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
      const result = await fetch(window.location.href, {
        credentials: "same-origin", cache: "no-store", signal: controller.signal,
      });
      if (!result.ok || result.redirected) { stopped = true; return; }
      const updated = new DOMParser().parseFromString(await result.text(), "text/html")
        .querySelector("[data-pending-devices]");
      if (updated && !section.contains(document.activeElement) &&
          !Array.from(section.querySelectorAll("input[name=device_name]"))
            .some(input => input.value !== input.defaultValue)) {
        section.replaceChildren(...updated.childNodes);
      }
    } catch (_) {
      // The existing page and manual refresh stay usable when the network fails.
    } finally {
      clearTimeout(timeout);
      if (!stopped) setTimeout(refresh, 5000);
    }
  }
  window.addEventListener("pagehide", () => { stopped = true; });
  setTimeout(refresh, 5000);
})();
