"use strict";

(() => {
  const decode = (value) => Uint8Array.from(atob(value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - value.length % 4) % 4)), (char) => char.charCodeAt(0));
  const encode = (value) => btoa(String.fromCharCode(...new Uint8Array(value))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const serialize = (credential) => {
    const response = {clientDataJSON: encode(credential.response.clientDataJSON)};
    if (credential.response.attestationObject) {
      response.attestationObject = encode(credential.response.attestationObject);
    } else {
      response.authenticatorData = encode(credential.response.authenticatorData);
      response.signature = encode(credential.response.signature);
      response.userHandle = credential.response.userHandle ? encode(credential.response.userHandle) : null;
    }
    return JSON.stringify({id: credential.id, rawId: encode(credential.rawId), type: credential.type, response});
  };
  const post = async (path, fields) => {
    const response = await fetch(path, {
      method: "POST", mode: "cors", credentials: "same-origin", redirect: "error",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams(fields), signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) throw new Error("PASSKEY_REQUEST_FAILED");
    return response.json();
  };
  document.querySelectorAll("form[data-passkey]").forEach((form) => {
    const button = form.querySelector("button[type=submit]");
    const status = form.querySelector("[data-passkey-status]");
    if (!window.isSecureContext || !window.PublicKeyCredential || !navigator.credentials) {
      button.disabled = true;
      status.textContent = form.dataset.unsupported;
      return;
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (button.disabled || !form.reportValidity()) return;
      button.disabled = true;
      status.textContent = "";
      const fields = Object.fromEntries(new FormData(form));
      const registration = form.dataset.passkey === "register";
      const prefix = registration ? "/admin/passkeys" : "/admin/login/passkey";
      try {
        const start = registration ? {csrf_token: fields.csrf_token, operation_id: fields.operation_id} : {preauth_nonce: fields.preauth_nonce};
        const options = await post(`${prefix}/options`, start);
        const publicKey = options.publicKey;
        publicKey.challenge = decode(publicKey.challenge);
        if (registration) {
          publicKey.user.id = decode(publicKey.user.id);
          publicKey.excludeCredentials = (publicKey.excludeCredentials || []).map((item) => ({...item, id: decode(item.id)}));
        }
        const credential = await navigator.credentials[registration ? "create" : "get"]({publicKey});
        if (!credential) throw new Error("PASSKEY_CANCELLED");
        await post(`${prefix}/verify`, {...fields, ceremony_id: options.ceremony_id, operation_id: options.operation_id, credential: serialize(credential)});
        window.location.assign(`${registration ? "/admin/passkeys" : "/admin/"}?lang=${document.documentElement.lang}`);
      } catch {
        status.textContent = form.dataset.error;
      } finally {
        button.disabled = false;
      }
    });
  });
})();
