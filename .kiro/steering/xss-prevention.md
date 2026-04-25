---
title: XSS Prevention in Client-Side JS
inclusion: fileMatch
fileMatchPattern: "chatapp/app/static/js/*.js"
---

# XSS Prevention Rules for innerHTML Usage

When building HTML from template literals in JavaScript files under
`chatapp/app/static/js/`:

## Required

- **User-typed fields** (names, descriptions, comments, prompts,
  search queries, feedback text) MUST be escaped before interpolation
  into an innerHTML sink. Use one of:
  - `window.escapeHTML(str)` — returns entity-escaped plain text
  - `window.safeHTML(html)` — runs through DOMPurify if available,
    falls back to full escaping
  - A local `escHtml` / `escH` helper that performs equivalent escaping

- **Markdown content** from the agent backend MUST be rendered via
  `DOMPurify.sanitize(marked.parse(content))`. Never assign raw
  markdown HTML to innerHTML without DOMPurify.

## Allowed without escaping

- Server-controlled enum values (system types, status codes, UUIDs)
- Static SVG icon strings defined in the same file
- HTML fragments built entirely from string literals with no dynamic data

## Helpers

The shared helpers live in `chatapp/app/static/js/utils.js` and are
loaded globally via `base.html`:

```javascript
window.safeHTML(html)   // DOMPurify.sanitize or full escape
window.escapeHTML(str)  // entity-escape for plain text
window.escapeAttr(str)  // same as escapeHTML, for attribute values
```

## Semgrep

Semgrep's `insecure-innerhtml` and `insecure-document-method` rules
cannot trace data flow through escape helpers. Findings on audited
sinks are downgraded to INFO in `.semgrep.yml`. New sinks that handle
user data should still be reviewed manually.
