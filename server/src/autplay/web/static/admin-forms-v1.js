"use strict";

// CORS-mode fetch supplies the exact Origin even under Referrer-Policy: no-referrer.
// Delegate on document so server-rendered POST results keep working without inline scripts.
document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== "post" || form.hasAttribute("data-passkey")) return;
  event.preventDefault();
  const action = new URL(form.action);
  if (action.origin !== location.origin || !action.pathname.startsWith("/admin/")) return;
  if (form.dataset.pending === "true" || !form.reportValidity()) return;
  form.dataset.pending = "true";
  const buttons = [...form.querySelectorAll("button[type=submit], input[type=submit]")];
  const data = new FormData(form, event.submitter);
  buttons.forEach((button) => { button.disabled = true; });
  let status = form.querySelector("[data-form-status]");
  if (!status) {
    status = document.createElement("p");
    status.dataset.formStatus = "";
    status.setAttribute("role", "status");
    form.append(status);
  }
  status.textContent = "";
  try {
    const multipart = form.enctype === "multipart/form-data";
    const response = await fetch(action.href, {
      method: "POST", mode: "cors", credentials: "same-origin", redirect: "follow",
      ...(multipart ? {} : {headers: {"Content-Type": "application/x-www-form-urlencoded"}}),
      body: multipart ? data : new URLSearchParams([...data].map(([key, value]) => [key, String(value)])),
      signal: AbortSignal.timeout(30000),
    });
    const destination = new URL(response.url);
    if (destination.origin !== location.origin || !destination.pathname.startsWith("/admin/")) throw new Error("ADMIN_ACTION_FAILED");
    if (response.redirected) {
      location.assign(destination.href);
      return;
    }
    if (!response.headers.get("content-type")?.startsWith("text/html")) throw new Error("ADMIN_ACTION_FAILED");
    const result = new DOMParser().parseFromString(await response.text(), "text/html");
    document.replaceChild(document.importNode(result.documentElement, true), document.documentElement);
    document.getElementById("main")?.focus();
  } catch {
    status.textContent = document.body.dataset.actionError;
  } finally {
    delete form.dataset.pending;
    buttons.forEach((button) => { button.disabled = false; });
  }
});
