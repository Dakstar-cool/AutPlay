"use strict";
(() => {
  const locale = document.documentElement.lang === "ru" ? "ru-RU" : "en-US";
  const formatter = new Intl.DateTimeFormat(locale, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit",
    minute: "2-digit", hourCycle: "h23", timeZoneName: "short"
  });
  function localize(root) {
    const nodes = root.querySelectorAll("time[data-local-time]");
    for (const node of nodes) {
      const instant = node.getAttribute("datetime");
      if (!instant || node.dataset.renderedInstant === instant) continue;
      const date = new Date(instant);
      if (!Number.isFinite(date.getTime())) continue;
      node.dataset.renderedInstant = instant;
      node.textContent = formatter.format(date);
    }
  }
  localize(document);
  new MutationObserver(() => localize(document)).observe(document.body, {
    childList: true, subtree: true, attributes: true, attributeFilter: ["datetime"]
  });
})();
