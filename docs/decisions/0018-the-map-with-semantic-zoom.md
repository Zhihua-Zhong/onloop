# Decision 0018: one map with semantic zoom

- **Status:** Accepted on 2026-11-18
- **Date:** 2026-11-18
- **Decided by:** the project lead (the master plan and R6 brief v2, approved 2026-11-18), with the onloop lead's rulings
  Q22–Q30
- **Size:** large (a new view, a derivation layer, a detector); nothing acts

## Context

A person on the loop no longer sees how the agents work. They feel the pain only when everything stalls, and then
have to ask the agents to explain, piece by piece. Meanwhile the views multiplied, and their counts disagreed
(finding #20).

## Decision

1. **One map, with semantic zoom.** Each level shows different content, not the same content scaled:

   | Zoom | URL | A block is |
   |---|---|---|
   | 0 | `#/map` | a goal: a checklist, a feedback batch, or "changes in flight" |
   | 1 | `#/map/<goal>` | a stage of that goal's flow |
   | 2 | `#/map/<goal>/<stage>` | a role or a resource holding items in that stage, and what each waits on |
   | 3 | `#/map/<goal>/<stage>/<role>` | a turn of that role (reserved; phase C) |
   | 4 | `#/map/<goal>/<stage>/<role>/<turn>` | a tool call (reserved; phase C) |

   A stage with no source of evidence is shown as "not reported", never as zero.
2. **Colour is a roll-up, computed at read time.** A leaf is:
   - **red** when its agents' step is past its stuck threshold, or a wait on a role is past its threshold;
   - **amber** when an open task of the person's holds it;
   - **moving** (blue) when it moved within the threshold;
   - **grey** when there is no evidence yet.

   A block takes the worst of its children, in that order, and a red block names its red child and the reason one
   level down. No colour is ever typed.
3. **`block.turned` events.** A detector on the tick logs each change of a block's colour with its reason. The events
   are kept for good (ruling Q26). Alerts and the "since you looked" sentence read them.
4. **One derivation layer.** Every count on every view comes from `onloop.derive`. One test asserts that Now, Flow,
   Activity and the map agree.
5. **Interaction.**
   - The wheel and the pinch zoom the canvas, and a drag pans it.
   - A double-click, Enter, or the block's title opens a level. A geometric zoom never changes the level by itself
     (ruling Q23).
   - Escape and the breadcrumb go up.
   - At 390 px, a tap opens a level.
   - Every level has a list form for the keyboard and screen readers.
6. **The libraries are already in use:** React Flow (MIT) for the viewport and ELK.js (EPL-2.0, unmodified, named in
   `NOTICE`, ruling Q22) for the layered layout.

   **Amended 2026-11-18 (accepted by the lead at 3a9da3a): zoom 0–2 use no ELK.**
   - Zoom 0 is a fixed grid. Zoom 1 is a single chain of stages, and zoom 2 a set of holders, so the same fixed grid
     lays out both, at scale 1.
   - This keeps the map's chunk at about 29 kB; ELK's bundled worker is about 1.4 MB.
   - ELK stays the option for a flow that branches. Flow uses it today.
7. **Where the views go.** Flow becomes zoom 1 for changes, and Activity becomes zoom 3, with a team-activity strip
   at zoom 0 for a team without outcome lines (ruling Q29). Both stay until the map covers them, and their removal
   waits on the project lead. Now stays as the person's inbox.
8. **onloop's own downtime** (`daemon.gap`) is a "no record" band on every time axis, never counted as stuck or quiet
   (ruling Q30).

## Consequences

- R6 builds zoom 0–2 (to 2026-12-01, release 0.5.0). Zoom 3–4 wait for contract 1.1's tool events.
- A count shown in two places is the same count, by construction and by test.

## Alternatives considered

| Alternative | Verdict |
|---|---|
| A fifth separate view (the overview page) | Replaced by zoom 0 of the map |
| Geometric zoom that switches level at a threshold | Rejected (Q23): a level the person did not choose is a surprise |
| A hand-rolled canvas and layout | Rejected: React Flow and ELK are already in use, and mainstream |
| Colours set by the team | Rejected: colour is derived from evidence and ages |
