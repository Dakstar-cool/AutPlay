# ADR-032: M6 server-rendered Web tooling

- Status: Accepted
- Date: 2026-08-21
- Scope: M6 optional administrative Web presentation and test tooling

## Context

M6 needs an accessible responsive administrative browser surface while remaining an optional
CPU-only adapter in the existing Python modular monolith. The accepted security contract prohibits
CDN/runtime public-host dependencies, JavaScript-readable authentication state and an Android/Web
source-of-truth merge. Adding a client SPA toolchain would create a second build/runtime boundary
without a product need.

## Decision

1. Render HTML on the server with Jinja 3.1.6, pinned as a direct production dependency. Enable
   strict undefined variables and HTML/XML autoescaping. Templates and EN/RU resources live inside
   the `autplay` Python package.
2. Use semantic native HTML and one bundled project-owned CSS asset. Add no client application
   framework, CDN asset, remote font, inline handler, analytics or runtime Node.js dependency.
3. Pin Playwright for Python 1.62.0 in the development dependency group only. Its pinned Chromium
   revision drives disposable local browser behavior, responsive and accessibility assertions; it
   is absent from the production dependency set and CPU runtime image.
4. Accessibility qualification combines executable browser checks (landmarks, labels, keyboard
   order, focus visibility, responsive widths, 200% zoom, reduced motion and computed AA contrast)
   with the separately required hands-on physical Android TalkBack checklist. No automated tool is
   represented as proof of human screen-reader quality.
5. Dependency/license records identify Jinja as BSD-3-Clause and Playwright as Apache-2.0. Exact
   hashes remain owned by the frozen `server/uv.lock`; runtime assets are package-local and covered
   by the release inventory/secret scan.

## Consequences

The Web UI works without JavaScript and therefore cannot hold authentication state outside HttpOnly
cookies. Presentation is small, auditable and compatible with FastAPI/Starlette. Browser tests need
an explicit local Playwright browser installation, but production installation does not. Template
changes must preserve strict rendering, localization-key parity, CSP and XSS tests.

## Rejected alternatives

- React/Vue/Svelte or another SPA: unnecessary client state/build/runtime surface and increased XSS
  exposure for an administrative companion.
- CDN-delivered CSS, icons, fonts or scripts: violates the offline/private bundled-assets boundary.
- Hand-built string concatenation: makes systematic autoescaping and template reuse easier to get
  wrong.
- Node Playwright as a production or repository-wide runtime: adds a second package manager where
  Python test tooling is sufficient.
- Screenshot-only accessibility review: does not prove semantics, keyboard behavior or spoken
  traversal.
