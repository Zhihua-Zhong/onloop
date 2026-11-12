# Decision 0009: one read-only UI over the ledger, served by onloopd

- **Status:** Accepted on 2026-11-09
- **Date:** 2026-11-09
- **Decided by:** the onloop lead, on the R3 plan, after the project lead confirmed the scope
- **Size:** large (it replaces four older pages and removes the stores behind them)

## Context

Before R3, onloop had four ways to look at the team:
- the workbench pages (`onloop workbench`);
- the static dashboard (`onloop dashboard`);
- the demo site (`onloop demo`);
- the work-graph panel and its canvas (`onloop panel`, `web/`).

Each read its own store: `onloop.db`, the work-graph store, or files. Since [decision 0006](0006-overlay-ledger.md),
the overlay ledger holds the events and their projections, and onloopd serves them read-only at `/api/*`
([decision 0008](0008-resident-service-and-dead-man-switch.md)). The first customer confirmed that the workbench,
records, the dashboard, the demo and the work graph are unused.

## Decision

1. **One UI, five views of the same ledger:** **Now**, **Flow**, **Team**, **Schedules** and **Audit**.
   - **Now** is the first screen. Within five seconds it answers three questions: what the biggest constraint is, what
     waits on the human, and where work has stalled.
   - **Flow** is the existing canvas (React Flow and ELK: zones and cards). It now reads the ledger, and it adds
     resource nodes, the constraint overlay, a view switch and a mobile opening.
   - **Conversation** waits for R4. It needs the session carrier and message text, and the ledger stores neither.
2. **Read-only.** The UI sends GET requests only. Where a person can act, it shows the exact `onloop` command, with a
   copy button. Writes come later, through the approval channel.
3. **Served by onloopd** from the same origin as the API: `http://localhost:8790` by default. The same rules apply:
   - the loopback address only, and the same Host check;
   - GET and HEAD only;
   - a strict Content-Security-Policy: `default-src 'self'`, no inline script, `frame-ancestors 'none'`.
4. **The bundle is built at release or CI time and is never committed.**
   - `npm ci && npm run build` writes `src/onloop/ui/`, which is gitignored.
   - The wheel carries it as package data.
   - A CI check builds the wheel and fails if it lacks `onloop/ui/index.html`, so a wheel without the UI never ships.
   - When onloopd runs from a checkout with no build, `/` answers with a plain page that says how to build it.
5. **Zones on Flow.** A card's zone is its item's `direction` (imported work-graph nodes have one). Without a
   direction, the zone is the owner role; without either, it is "unassigned". The canvas shows the zone's source.

### Serving the bundle: alternatives considered

| Option | Verdict | Why |
|---|---|---|
| **onloopd serves the built files from package data** | **Chosen** | One process, one port, one Host check and one CSP. The page and the API share an origin, so no CORS is needed. Installing needs no Node |
| A second static server on its own port | Rejected | A second port, a second Host check, and a cross-origin API that would need CORS. ADR 0008 asks for one resident service |
| Commit `web/dist` to the repository | Rejected | Build output in every review diff, and a committed bundle that can drift from its source |
| A setuptools build hook that runs `npm` | Rejected | Building from the sdist would need Node. It is not the mainstream Python route, and a failed hook could leave a wheel without the UI |
| The Vite dev server, or Node at run time | Rejected | Node on every machine that runs onloop, and a second process |
| Server-rendered HTML from Python, as the workbench did | Rejected | It would lose the canvas the project lead singled out, and duplicate the rendering of every view |

## Consequences

- `workbench/`, `records.py`, `dashboard.py`, `demo.py` and the work graph's store, flow, panel and decide form are
  removed, with their commands.
- `workgraph/model.py` and `workgraph/github_issues.py` move into `supervisor/`, which still uses them.
- `store.py` goes once nothing reads `onloop.db`. That closes decision 0006's checklist.
- The upgrade path from an older install is the one-shot `onloop ledger import --store/--workgraph`. onloopd no
  longer ingests the legacy stores on every tick.
- The release now needs Node at build time, and only there.
