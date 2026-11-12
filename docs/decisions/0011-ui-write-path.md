# Decision 0011: the UI's first write, and what a message is worth

- **Status:** Accepted on 2026-11-10
- **Date:** 2026-11-10
- **Decided by:** the onloop lead, on the R4 plan
- **Size:** medium

## Context

[Decision 0009](0009-one-read-only-ui.md) made the UI read-only. The Conversation view needs a compose box: a person
sends a message, and onloop delivers it to the role ([decision 0010](0010-session-lease-and-delivery.md)).

Two ways to authorize that write were weighed:

- **The WebAuthn approval channel** (story 15). Its spike showed that it proves a present human signed an exact text,
  but only when three anchors sit outside what an agent can write: the key pin, the consumed set, and the source of
  the text shown. The verifier belongs in the code host's CI. That is not carrier work.
- **A local secret.** onloopd's write token keeps out other programs and web pages. It does not keep out agents that
  run as the same user and can read it ([decision 0008](0008-resident-service-and-dead-man-switch.md)).

## Decision

1. **A message is evidence, not authority.** It is a request to a role. Approvals of one-way doors (a merge of a
   reserved class, an ordered scope) do not travel as messages; they stay with the code host's review until story 15.
   Every message is shown with its channel and with "local user, not verified".
2. **Two channels in R4.**
   - `onloop say <role> "…"` posts with onloopd's write token, as the hooks do.
   - The compose box posts `POST /ui/messages` with the page's own session secret: an `HttpOnly`, `SameSite=Strict`
     cookie drawn when onloopd serves the page, plus a per-load CSRF token read from a same-origin GET. The request
     must also carry an `Origin` of this onloopd (`http://localhost:<port>`, or the loopback address with its port), and the usual Host check
     applies. Anything else is 403.
3. **Story 15 moves to R5.** It needs the merge-check verifier in CI and a trusted rendering source.
4. The CSP stays as it is: `default-src 'self'`, no inline script, `frame-ancestors 'none'`.

## Consequences

- The UI is no longer GET-only. It has exactly one write, the message, behind the rules above, and a browser test
  checks each refusal.
- A same-user agent could post a message through either channel. That is acceptable only because a message carries
  no authority; the label on every message says so.
