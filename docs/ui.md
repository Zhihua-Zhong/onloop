# The UI

onloopd serves one read-only UI over the ledger at `http://localhost:8790` (decision 0009). It has five views of the
same ledger. Every request it makes is a GET. Where a person can act, the page shows the exact `onloop` command with a
copy button. Writes come later, through the approval channel.

```text
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790
```

## The views

| View | The question it answers | Reads |
|---|---|---|
| **Now** (`#/now`, the first screen) | What is the biggest constraint, what waits on me, and where has work stalled? | `GET /api/now` |
| **Flow** (`#/flow`) | Where is the work, what does it wait on, and what does the constraint hold up? | `GET /api/flow` |
| **Team** (`#/team`) | Who is working, idle, paused or stalled, on what, and what happens next? | `/api/roles`, `/api/turns`, `/api/waits`, `/api/items`, `/api/alerts` |
| **Schedules** (`#/schedules`) | Did every scheduled job run, and which are failing or overdue? | `/api/schedules`, `/api/alerts`, `/api/events` |
| **Audit** (`#/audit`) | What happened, who did it, and from what source? | `GET /api/events` with filters |

### Now

- **The constraint banner** names the biggest constraint and its numbers, in one scannable line ("5 changes queued ·
  6 h 15 min waiting in total · oldest 2 h 5 min"), and the related blocked work, such as changes that conflict with
  main.
- **The next move** is one a person can make, named from the ledger, with its command or its link. In order: restart
  a dead waker; resume a role you paused; attach the evidence a step needs; for a lock or a queue, answer the item
  that holds it when it waits on you ("Merge the change only you may merge: it holds merge-check, so the 5 queued
  behind it move"); for you, the question that runs out first. When the ledger names no such move, there is no box,
  never general advice.
- **Waiting on you:** each card shows the default if nobody answers and the time left. A role you paused is one card,
  with the items queued behind it and `onloop role resume <role>`.
- **Stalled:** each card shows its age and "would wake (observe only)", because onloop observes and does not wake yet.
  "Acknowledged" means a later turn by that role.
- **Health:** failing and overdue schedules, onloopd's own heartbeat (the file it rewrites every tick; stale after 60
  s), and the dead-man switch: the schedule named as `heartbeat_schedule` in onloopd.yaml. Without that key the card
  says "dead-man not configured", a neutral note, never OK.

On a phone, an at-a-glance strip under the banner keeps the three counts on the first screen.

### Flow

The canvas (React Flow, laid out by ELK) draws:
- **Zones.** Each is a direction (the nearest topic above an item), else the owner role, else "unassigned". The zone's
  tooltip says which.
- **Cards.** One per item, coloured by state: amber waits on you, red is blocked, violet waits on someone else, blue
  is open, green is done.
- **Resources.** The locks, queues and the human that cards wait on, in their own zone.
- **No line crosses a zone it does not connect.** A waiting card names what it waits on in its footer pill ("Queued →
  merge-check", "Blocked → main"). The lines between a resource and its cards, and a dependency between two zones,
  are drawn only while that resource or one of its cards is selected (a tap) or hovered. Dependencies inside a zone
  are always drawn; they stay in their zone.

What the switch and buttons do:
- **The constraint overlay** (on by default) highlights the constraint and what it holds up downstream, and dims the
  rest. What waits on you is never dimmed.
- **The view switch** has four views: the graph, "Waiting on you" (only the cards that wait on you), a roadmap (time
  left to right), and a calendar window (the last day, now, the next day).
- **The opening view.**
  - On a wide screen, Flow opens on the constraint, at a zoom where titles read.
  - On a phone (under 768 px), it opens on the first zone that holds a human wait, at zoom 0.9.
  - "Show all" fits everything. "Map" shows a minimap.

### Live updates

The page listens to `GET /api/stream?plain=1`, and a burst of new events refetches what the open view reads. When the
stream is down for more than five seconds, a banner says when updates stopped, and the page keeps what it last read.
It reconnects by itself.

### Theme

Light or dark follows the system's `prefers-color-scheme`, live, until the person picks one with the toggle; that
choice is kept in this browser. `?theme=dark` or `?theme=light` in the URL overrides both (the screenshots use it).

## Serving and security

The UI has the same origin and the same rules as the API (decision 0008):
- the loopback address only;
- a request is answered only when its Host is `127.0.0.1:<port>` or `localhost:<port>`; <!-- scan:allow -->
- GET and HEAD only.

Every page answer carries:
- a strict Content-Security-Policy: scripts, styles and requests from the same origin only, no inline script, no
  framing;
- `nosniff`, `no-referrer` and `X-Frame-Options: DENY`.

Hashed assets under `/assets/` are cached as immutable. Everything else is `no-store`. A path that leaves the bundle,
names a dot file or has an unknown extension is a 404.

## Building it

The bundle is built at release time and is never committed:

```text
cd web
npm ci
npm run build          # writes src/onloop/ui/ (gitignored); the wheel carries it as package data
```

CI builds the UI, then the wheel, and `tools/check_wheel.py` fails the build if the wheel lacks `onloop/ui/index.html`
or its scripts. Without a build, onloopd still runs: `/` answers a page that says how to build the UI, and the API
works as before.

For development, `npm run dev` serves the UI on `localhost:5173` and forwards `/api` to an onloopd on port 8790.

## Tests and the vision loop

`npm run e2e` runs the browser tests against onloopd's real server over a demo ledger of generic sample data
(`web/e2e/demo_server.py`, built by `tools/demo_ledger.py`, on port 8792), at 1440 and 390 px, using the installed
Chrome. Every test fails on a console error (a CSP refusal shows up as one) or on any request that is not a GET.

`npm run screens` writes the vision loop's pictures: every view at both widths in light and dark, plus the states one
click away. Set `SCREENS_DIR` to keep them outside the repository.
