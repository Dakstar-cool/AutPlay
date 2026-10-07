"use strict";

(() => {
  const svgNamespace = "http://www.w3.org/2000/svg";
  document.querySelectorAll("[data-server-connection]").forEach((card) => {
    const origin = card.dataset.serverOrigin;
    const qrContainer = card.querySelector("[data-server-qr]");
    const qrStatus = card.querySelector("[data-qr-status]");
    try {
      const url = new URL(origin);
      if (origin.length > 2048 || !["https:", "http:"].includes(url.protocol) || url.origin !== origin) {
        throw new Error("SERVER_ORIGIN_INVALID");
      }
      const qr = qrcodegen.QrCode.encodeText(origin, qrcodegen.QrCode.Ecc.MEDIUM);
      const border = 4;
      const size = qr.size + border * 2;
      const svg = document.createElementNS(svgNamespace, "svg");
      svg.setAttribute("viewBox", `0 0 ${size} ${size}`);
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", card.dataset.qrLabel);
      svg.setAttribute("shape-rendering", "crispEdges");
      const background = document.createElementNS(svgNamespace, "rect");
      background.setAttribute("width", String(size));
      background.setAttribute("height", String(size));
      background.setAttribute("fill", "#fff");
      svg.append(background);
      const modules = [];
      for (let y = 0; y < qr.size; y++) {
        for (let x = 0; x < qr.size; x++) {
          if (qr.getModule(x, y)) modules.push(`M${x + border},${y + border}h1v1h-1z`);
        }
      }
      const path = document.createElementNS(svgNamespace, "path");
      path.setAttribute("d", modules.join(" "));
      path.setAttribute("fill", "#000");
      svg.append(path);
      qrContainer.replaceChildren(svg);
    } catch {
      qrStatus.textContent = card.dataset.qrError;
    }

    const copyButton = card.querySelector("[data-copy-server-address]");
    copyButton.addEventListener("click", async () => {
      const status = card.querySelector("[data-copy-status]");
      try {
        await navigator.clipboard.writeText(origin);
        status.textContent = card.dataset.copySuccess;
      } catch {
        const input = card.querySelector("[data-server-address]");
        input.focus();
        input.select();
        status.textContent = card.dataset.copyError;
      }
    });
  });
})();
