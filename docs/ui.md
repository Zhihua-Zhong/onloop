# The UI

onloopd serves one UI over the ledger at `http://localhost:8790` (decision 0009). It has eight views of the same
ledger, in this order in the bar: Now, Replay, Map, then Outcome, Team, Conversation, Schedules and Audit. Every
request it makes is a GET but one: the Conversation view's compose box, a message to a role
([decision 0011](decisions/0011-ui-write-path.md)). Where a person can act otherwise, the page shows the exact
`onloop` command with a copy button. Approvals come later, through the approval channel.

```text
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790
```

## The views

| View | The question it answers | Reads |
|---|---|---|
| **Now** (`#/now`, the first screen) | What is the biggest constraint, what waits on me, and where has work stalled? | `GET /api/now` |
| **Replay** (`#/replay`) | Who did what, how long it took, what it cost in tokens, and what each step produced? | `GET /api/replay`, `GET /api/products` |
| **Map** (`#/map`, zoom 0) | Where is it jammed, and is it me? | `GET /api/map`, `GET /api/digest` |
| **Outcome** (`#/outcome`) | Do merged changes move the goals, and what does each one take to land (hours, review rounds, human touches)? ([tasks.md](tasks.md)) | `GET /api/kpi` |
| **Team** (`#/team`) | Who is working, idle, paused or stalled, on what, and what happens next? | `/api/roles`, `/api/turns`, `/api/waits`, `/api/items`, `/api/alerts` |
| **Conversation** (`#/conversation?role=R`) | What did onloop send each carried role, how far did it get, and what would it send where it only observes? | `GET /api/conversation`; the compose box `POST /ui/messages` |
| **Schedules** (`#/schedules`) | Did every scheduled job run, and which are failing or overdue? | `/api/schedules`, `/api/alerts`, `/api/events` |
| **Audit** (`#/audit`) | What happened, who did it, and from what source? | `GET /api/events` with filters |

Now is the person's inbox, Replay the main view, and the map's zoom 0 the overview (decision 0019).

### How the views link

- **From Now:** the constraint banner's "Map" link opens the map at the stage that holds the constraint, with the
  holder's row selected (`#/map/changes/opened?who=merge-check` on the demo). When the team's turns carry no outcome
  lines, the stall card's "See when the team worked" opens Replay over the last 7 days.
- **Waiting on you** is one list, Now's; the map and the task list count it the same way, from one derivation.
- **Jobs due** are on Schedules.
- **An old link** to a retired view opens where its parts moved, and the address is rewritten to that view (below).

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
- **Expires within a day,** beside Stalled: open items whose expiry is past or within a day.
- **Stalled:** each card shows its age and "would wake (observe only)", because onloop observes and does not wake yet.
  "Acknowledged" means a later turn by that role.
- **Health:** failing and overdue schedules, onloopd's own heartbeat (the file it rewrites every tick; stale after 60
  s), and the dead-man switch: the schedule named as `heartbeat_schedule` in onloopd.yaml. Without that key the card
  says "dead-man not configured", a neutral note, never OK.

On a phone, an at-a-glance strip under the banner keeps the three counts on the first screen.

### Map

The full guide is [map.md](map.md). Zoom 0 of one map (decision 0018). Each level has its own URL (`#/map`,
`#/map/<goal>`, `#/map/<goal>/<stage>`, and `?who=<holder>` for that holder's row selected), so Back, Forward and a
link land on the same level.

- **The signals:**
  - "N wait on you": the same count as Now and the task list;
  - "N jammed": live work only, with "N idle" beside it when changes have had no move for a week;
  - "Team quiet at most": the team's longest pause in the data's last day. An item's own "no move" is its reason
    line, never this;
  - the since-you-looked sentence.
- **A block per goal, grouped by origin, never by role:**
  - each checklist, done out of its denominator;
  - each feedback batch, accepted out of given, marked "Stages reported by the team" when an export declares them.
    An item the team's export marks as the person's step reads "marked yours by the team", never "waits on you":
    only the person's own tasks say that, so the blocks never add up past the header's count;
  - "Changes in flight", with its stages. A stage this team's export does not report is hatched and says "not
    reported", never 0.
- **Colour:** a block takes the worst colour of its live children: jammed (!), waits on you (◆), moving (▶), not
  started (□). Every colour has its shape. A red or amber block says in one line which child and why, never an idle
  one. A tally reads "1 jammed | 1 waits on you", with the jam first, in red, set apart. Idle work
  (open, no move for 7 days) is hatched greyish red and never counted as jammed.
- **Waits, by who holds them:** oldest first. An age shown as "≥ 9 days" began before anything onloop has seen, and
  the row says "began before the first export".
- **The first view** opens at scale 1, never fitted smaller, with the blocks sharing the canvas's width (up to 760
  px each). Widening or narrowing the window re-lays them.
- **A goal with no title** shows its id in plain words ("Release plan").
- **Moving around:**
  - the wheel or a pinch zooms the canvas, and a drag pans it;
  - zooming never changes the level. From 1.6×, each block shows an "Open" button;
  - a double-click, Enter on a focused block, or a click on its title opens the block;
  - Escape goes up, and does nothing at zoom 0;
  - Tab moves across the blocks in reading order.
- **Zoom 1** (`#/map/<goal>`): the goal's stages, in order, linked one to the next:
  - a checklist: to do, in progress, done;
  - a feedback batch: the team's own stages, then accepted;
  - changes: opened, checks, review, merged in the last week.

  Each stage has its count, colour, oldest item and, when red or amber, which item and why. A stage with no evidence
  is hatched, "not reported". A change's checks read "checks: 1 failed (e2e) · 4 passed · 1 skipped": skipped runs are
  counted, never named, and a failed or running check is named exactly as recorded.
- **Zoom 2** (`#/map/<goal>/<stage>`): the goal's stage × role grid. It has a column per stage (Idle first, for
  changes) and a row per holder (you, "marked yours by the team", the team or a role). The first row is the holder
  zoom 0's reason line names, then the rest worst first.
  - The stage you opened is outlined, and the bottleneck is marked.
  - A cell opens its stage at that holder.
  - Below the table, that holder's items in the stage each say what they wait on and since when, with a link to your
    task or the change's history. "Details" under an item gives its owner, its wait (why, and for how long), its default
    and expiry, its source link, and the command to copy when the wait is on missing evidence or a paused role.
  - On a phone only the non-empty columns show. The full guide is in [map.md](map.md).
- **Zoom 3 and 4** (a role's turns, a turn's tool calls) are reserved in the URLs and say they come with the tool
  events.
- **"Show as a list"** gives the same content as tables, at every level and any width.
- **When onloopd is down,** the page keeps the map as last read, with a line saying so, and tries again every few
  seconds. It shows the map again as soon as onloopd answers.
- **Under 768 px,** the page is one column: the signals, "Most waiting work", the batches, the checklists, the changes,
  then the waits.

### Replay

The full guide is [replay.md](replay.md). One lane per role, in proportion to time, with a cursor that gives every
role's state, time and tokens at that moment. With no moment in the link it opens live: the window ends now and the
cursor stays there.
- **The window:** the last 1 h, 6 h, 24 h or 7 days. Every figure is the window's, and says so.
- **The lanes:** working, harness, waiting on you and queued time. A turn with no recorded start is a mark at its end.
  Above the lanes, two density rows: turns per hour, and how many roles had a turn in each hour ("Roles active").
- **The window starts where the data does:** before the first event the lanes are shaded "No data before <time>".
- **On a team's export,** the header says "data as of", as the map does.
- **Quiet:** each role's "Quiet for" is in the table at the cursor, and its longest quiet in "Where the time went".
- **Steps:** a click on a step shows what it produced as of the cursor.

### Retired: Flow and Activity

Decision 0019 retired both views in 0.6.0; their parts moved first. An old link opens where they went, and the address
is rewritten in place (no new history entry), keeping the rest of the link: `#/flow` becomes `#/map`, and `#/activity`
becomes `#/replay`. Parameters the new view does not know (Flow's `mode`, a selected card) are ignored there.
`GET /api/flow` and `GET /api/activity` answer 404.

| Was | Now |
|---|---|
| Activity's lanes, turn marks and "No data before" | Replay's lanes |
| Activity's turns per hour and roles at once | Replay's two density rows |
| Activity's quiet stretches | Replay's "Quiet for" at the cursor, and each role's longest quiet in "Where the time went" |
| Activity's data-as-of line (where a team's export ends) | Replay's "data as of", the same `data_as_of` as the map's |
| Activity's "Show as a table" | Replay's "Show as a table" |
| Activity's 24 h, 48 h and 7 days | Replay's 1 h, 6 h, 24 h and 7 days; 48 h is gone |
| Activity's side-session sub-lanes | Gone: a session shows through its steps |
| Flow's constraint overlay, toolbar count and constraint chip | Now's constraint and its counts, linked to the map's cell that holds it |
| Flow's card details | The map's zoom 2 item details |
| Flow's "waiting on you" view | Now's "Waiting on you" |
| Flow's roadmap | Ages on the map (zoom 2) and Now; expiries in Now's "Expires within a day" |
| Flow's calendar | Done work: Replay's window and the digest ("since you looked") on Now and the map. Jobs due: Schedules |
| Flow's dependency graph | Gone: "what waits on what" returns in R7b, from the plan, not as a view |

### Live updates

The page listens to `GET /api/stream?plain=1`, and a burst of new events refetches what the open view reads. When the
stream is down for more than five seconds, a banner says when updates stopped, and the page keeps what it last read.
It reconnects by itself.

**The clock and liveness.** A stalled page must never look like a quiet one (the project lead's finding on 8796, where
"1 s ago" still said so two minutes later):
- The header has a clock that ticks every second, and "data as of N s ago": how long since this page last read from
  onloopd.
- Every relative age on every page rises with that clock between answers: the page adds the seconds since it read the
  answer to each age the server gave (`web/src/lib/tick.ts`). Ages counted to an export's end stay put, since the
  export's end does not move.
- The page reads `GET /api/heartbeat` every 10 s: onloopd's last heartbeat, the server's clock, and its tick period
  (10 s by default). News is the newest of a stream event and a heartbeat.
- The Live dot turns amber with "No news for N s" past two tick periods without news: onloopd answers, but its tick may
  have stopped. It turns red with "onloopd not answering for N s" once neither the stream nor the heartbeat has
  answered for five seconds. A phone shows "quiet N s" and "offline N s".
- While onloopd does not answer, Now keeps what it last read instead of an error.

### Theme

Light or dark follows the system's `prefers-color-scheme`, live, until the person picks one with the toggle; that
choice is kept in this browser. `?theme=dark` or `?theme=light` in the URL overrides both (the screenshots use it).

### The state palette

`web/src/styles/tokens.css` is the only place a colour is chosen. The state colours are the only saturated colours on
the canvas, and each has its own step in dark mode, validated against the dark surface.

| Token | Means on Now and the task list | On the map | Shape on the map |
|---|---|---|---|
| `state-blocked` (red) | blocked | jammed | ! |
| `state-human` (amber) | waits on you | waits on you, or marked yours by the team | ◆ |
| `state-open` (blue) | open, in progress | moving | ▶ |
| `state-void` (grey) | cancelled or missing | not started, or not reported (hatched) | □ |
| `state-done` (green) | done | done | ■ |
| `state-external` (violet) | — | the bar of a waits row held by a role or the team | — |
| `state-stall` (magenta) | stalled | — | — |
| `state-idle` (greyish red, hatched) | — | idle: an open change with no move for 7 days, never counted as jammed | none |

A colour is never the only sign. Every state also has a shape or a word, and the list form gives the same content as
text.

### Conversation

The roles onloop carries ([carrier.md](carrier.md)) on the left, each with an unread count and one pill for what
needs you (paused, a run refused, no outcome line, observe only). The selected role's thread on the right, oldest
first:
- your messages, marked "local user, not verified", with how far each got: queued, delivered, being processed,
  acknowledged (by the role's next turn), or not sent and why;
- onloop's own notes, such as the one corrective per turn asking for the outcome line;
- what onloop would have sent where it only observes, with the reason;
- each run, with its status, and each turn by its outcome line only. A role's own words are never stored; the header
  shows `claude --resume <id>` to read them in your own client.

The compose box at the bottom is open only when a message can go somewhere: the carrier is configured and started,
its kill switch is off, and the role is carried and opted in to messages (`GET /api/conversation` says so per role,
in `compose`). Then it says what will happen: an idle role is woken, a busy one gets it queued. Otherwise it is
closed and says why in plain words, with the command or the onloopd.yaml setting that opens it, for example
"Messages are off: the carrier is not configured (docs/carrier.md)" on a default install, or "designer has not opted
in to messages" with `onloop role opt-in designer message`. A message closed as observe-only says so on its bubble:
"not sent: observe-only (would deliver)". A banner says when the carrier is not configured, stopped or not started.
Now's stall card and Team's lane use the same words for what the carrier did.

## Serving and security

The UI has the same origin and the same rules as the API (decision 0008):
- the loopback address only;
- a request is answered only when its Host is `127.0.0.1:<port>` or `localhost:<port>`; <!-- scan:allow -->
- GET and HEAD, and one POST: `/ui/messages`, the compose box. It needs the page's session cookie (`HttpOnly`,
  `SameSite=Strict`, set when `/` is served), the CSRF token from `GET /api/ui/csrf` in `X-CSRF-Token`, and an
  `Origin` of this onloopd; anything else is 403. A message is evidence, not authority: it is shown as "local user,
  not verified" and never counts as an approval.

Every page answer carries (except a step's product file, below):
- a strict Content-Security-Policy: scripts, styles and requests from the same origin only, no inline script, no
  framing;
- `nosniff`, `no-referrer` and `X-Frame-Options: DENY`.

A step's product file (`GET /api/products/file`) carries instead `Content-Security-Policy: sandbox`, with no script
and `frame-ancestors 'self'`, so only this page may frame it and its HTML never runs with onloopd's origin.

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
