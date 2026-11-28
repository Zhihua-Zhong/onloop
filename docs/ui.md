# The UI

onloopd serves one UI over the ledger at `http://localhost:8790` (decision 0009). Since 0.7.0 it is prototype F's
shell: a sidebar with the main pages (Home, Decisions, Agents, Timeline) and, folded under **Details**, history and
operations (Map, Replay, Outcome, Conversation, Schedules, Audit, Rule alerts), and "Agents now": every role with its
state. On a phone the same list opens as a drawer.
Every request it makes is a GET but two: the Conversation view's compose box, a message to a role
([decision 0011](decisions/0011-ui-write-path.md)), and a decision's Done or Decline (decision 0013). Where a person can act otherwise, the page shows the exact
`onloop` command with a copy button. Approvals come later, through the approval channel.

```text
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790
```

## Design principles: outcome first, never process first

A page that narrates what the system did (events, steps, rule alerts and logs in time order) reads as a ledger: the
person has to work out what it means for them. Every main screen is designed the other way round.

1. **Start from the person's question, written down first:** is it going well, what needs me, what came out. A screen
   that cannot name its question is not a main screen.
2. **Main screens show state and results.** History, logs, the audit trail and conversations are one click down, under
   Details.
3. **Count instead of listing.** Repeats collapse to one line with a count (and, where it helps, one action for all of
   them). Internal rule or format alerts never reach a main screen; they belong with the history.
4. **No dead panels.** An empty panel either hides or says how to fill it.
5. **Few things, well spaced:** about three primary navigation items, a few numbers and one picture per screen, with
   whitespace.
6. **Name the reference.** Each screen names the mainstream product page it follows (an overview such as Linear's,
   Vercel's or Datadog's home) and matches its restraint.

A UI change states, for each screen it touches, the question the screen answers and what it moved under Details. The
acceptance includes a 30-second purpose test: a cold reader, given no questions, says what the product does, what it is
for and what to do next.

## The views

| Page | The question it answers | Reads |
|---|---|---|
| **Home** (`#/`, the first screen) | Is it going well, what needs me, what came out? | `GET /api/home` |
| **Decisions** (`#/decisions`) | What must I decide, in what order, and what did I answer? | `GET /api/decisions`; answers `POST /ui/tasks/resolve`, `/ui/tasks/withdraw` |
| **Agents** (`#/agents`, `#/agents/<role>`) | Who is working, on what, at what cost, and is anyone stuck? | `GET /api/agents`, `GET /api/agents/<role>` |
| **Timeline** (`#/timeline?days=1\|2\|7\|14`) | Who worked when, where did work wait on me, what came out when? | `GET /api/timeline` |
| Details · **Replay** (`#/details/replay`) | What was happening at a given moment: playback, the cursor, each step's products, the table | `GET /api/replay`, `GET /api/products` |
| Details · **Map** (`#/details/map`, zoom 0) | Where is it jammed, and is it me? | `GET /api/map`, `GET /api/digest` |
| Details · **Outcome** (`#/details/outcome`) | Do merged changes move the goals, and what does each one take to land (hours, review rounds, human touches)? ([tasks.md](tasks.md)) | `GET /api/kpi` |
| Details · **Conversation** (`#/details/conversation?role=R`) | What did onloop send each carried role, how far did it get, and what would it send where it only observes? | `GET /api/conversation`; the compose box `POST /ui/messages` |
| Details · **Schedules** (`#/details/schedules`) | Did every scheduled job run, and which are failing or overdue? | `/api/schedules`, `/api/alerts`, `/api/events` |
| Details · **Audit** (`#/details/audit`) | What happened, who did it, and from what source? | `GET /api/events` with filters |
| Details · **Rule alerts** (`#/details/rules`) | Which of the system's own checks fired, how many, and where are their rows? | `GET /api/rules` |

Home is the person's first screen; the Timeline is the main picture of the work; the map's zoom 0 the overview of where
it is jammed (decision 0019).

### How the views link

- **From Home:** Delivered opens the Timeline, Cost per delivery opens Agents, Waited on you goes to the decisions; the
  brief's "Decide" button opens the first decision; "Open the timeline" under the lanes; the count of asks that do not
  say what they change links to Details · Rule alerts.
- **From the map:** zoom 0's "Who worked when: Timeline →" opens the Timeline over the last 24 hours.
- **From the Timeline:** "Play it back, or see it as a table" opens Details · Replay over the same window.
- **From Agents:** a card opens that role's page: its week, its decisions with you, its lane.
- **Waiting on you** is one count: Home's (the decisions it lists plus the asks it counts under Rule alerts), the
  map's and the task list's, from one derivation (`tests/test_agreement.py`).
- **Jobs due** are on Details · Schedules; their count and the dead-man switch on Details · Rule alerts.
- **An old link** opens the same view at its new address, and the address is rewritten in place (below).

### Home

Prototype F's Home, on the ledger (0.7.0 plan, sections 1 and 2). `GET /api/home` reads 14 days at most: this week and
the week before. On a frozen copy (a read-only server whose ledger nothing writes) the week ends at its last event.

- **Today's brief:** written by onloop over this page's facts (the smart layer, below), else the facts themselves,
  marked "facts only": what waits on you, how many of those asks say what they change and the first in order; what was
  merged; the most unusual thing. Every number in it is one the page shows. Its one button opens the first decision,
  named by its written short title when there is one.
- **Three numbers against last week:** Delivered (changes merged), Waited on you (each ask's own open time in the week,
  summed), and Cost per delivery (the week's tokens, without cache reads, divided by Delivered), each with its seven
  days as a sparkline. With less than two weeks of data there is no week before, and the page says so instead of a
  change.
- **Decisions:** the asks that say what they change, in the task list's one order, the first five shown and the rest
  one click away, each folded to one line (what it changes, what waits on it, who asked, how long ago, "asked N×").
  Opened, it shows its facts and its answers: the options its ask recorded, else Done and Decline. The brief's one
  button reads "Decide the first of N", never a cut sentence; "All decisions" opens Decisions. The asks that do not say what they change still wait on you: Home
  counts them and links to Details · Rule alerts, and never lists them.
- **Unusual this week:** each role held to its own last 14 days: how long it goes quiet (the median of its days'
  longest gap between turns), its tokens in a day, and how long its asks wait. Three times its usual is unusual.
  Computed, never generated; when nothing is, the card says so and how it is judged.
- **Who worked when:** the Timeline's lanes over 24 h, 48 h or 7 days: working time and time waiting on you per role,
  each turn's end a mark, each change merged a green flag, and the now line. The role names stay in their column while
  the picture scrolls; on a phone it opens at now.

### Agents

Prototype F's cards (0.7.0 plan, sections 1 and 2), one per role seen in the last two weeks:
- **Its state, in plain words:** working (a carried run open and alive, a recorded step open, or a turn or model request
  in the last 10 minutes), waits on you (it asked something still open), paused by you (with the command that resumes
  it), stopped (a carried run started over an hour ago and did nothing since), or idle. "Stopped" is the only stuck: a
  turn that broke a format rule (no outcome line, an ask with no effect) never makes a role look stuck; Rule alerts
  counts those.
- **What it is on:** its open step, else what its last turn said ("blocked on pr#45, 30 min ago").
- **Its week:** turns by day, turns and tokens against the week before, and the changes merged that it opened (when the
  code host says who opened them), with the model it ran on last.
- **A role's page:** its week by day, its decisions with you (open, and answered with whether the answer reached it),
  and its lane over the week.

Its tokens and turns are the Timeline's and Home's (`tests/test_agreement.py`). onloop records no subagent of a session
today; none is shown, and none is made up.

### Timeline

0.6's Replay lanes, restyled with prototype F's flags and callouts (0.7.0 plan, sections 1 and 7.3), over 24 h, 48 h,
7 or 14 days:
- **The lanes:** working time and time waiting on you per role, each turn's end a mark, each change merged a green flag,
  and Home's "Unusual this week" as numbered callouts where each happened.
- **So far in this window:** each role's working time, waiting time, turns and tokens, and how much of the window
  something waited on you.
- **Speed:** `GET /api/timeline` reads the window only. Each role's figures are summed from per-day figures cached in
  onloopd: a past day is computed once, again only when it gains an event, and today on each read. Nothing is read from
  the ledger's start (0.6's Replay did, and took over 10 s on the dogfood).
- **Replay** keeps the playback, the cursor, each step's products and the table, under Details.

### Decisions

Prototype F's split view (0.7.0 plan, sections 1 and 4; decision 0020): the list on the left, the decision on the
right; on a phone the decision opens as a sheet.

- **Open:** the decisions in the task list's one order, sortable by longest waiting or most asked, and filtered by
  text. One subject, one decision: each row names every role that asked and "asked N×". The asks that say nothing of
  what they change are counted with a link to Rule alerts.
- **A decision:** what it changes, what waits on it, if nobody answers, the cost of delay, what it is about, and the
  asks merged into it (who, when, and "nothing new" for a repeat). Its answers are the options its ask recorded; with
  none, Done and Decline. The smart layer may write its short title and subject and suggest one of the options, with
  its grounds: that option is the one primary button. Nothing is suggested when the ask recorded no options.
- **Answering** sends at once; a toast says when the asker sees it ("operator sees it at its next prompt") and offers
  Undo for 9 s, held while the pointer is on it.
- **Done:** the person's answers of the last 14 days, each with whether it has reached the role that asked. Until it
  has, Withdraw opens the decision again; after, a change of mind is a new answer.
- **The count** is Home's and the sidebar badge's.

### The smart layer

onloop writes a few short texts over the facts it computed (0.7.0 plan, section 3; `src/onloop/brief.py`), and shows
each one marked as written: a sparkle and "Written by onloop from … · facts <hash>", the first seven characters of the
hash of the facts it was written from.

- **What it writes:** the brief (2 or 3 sentences); for each open decision a short title and its subject in a few words;
  when the ask recorded options, the suggested answer with its grounds, which is the one primary button (the other
  options are outline, Decline is plain); and near-duplicate merge proposals between open decisions.
- **What it never writes:** a number that is not in the facts, an option the ask did not record, the order, "Do first".
- **The checks:** a text is taken only when it is the JSON asked for, every digit run in it is one of its facts, every
  suggestion is one of its decision's options, and every id is an open decision's. Otherwise it is logged as
  `text.rejected` with the reason, and the page shows the facts.
- **The cache is the ledger:** an accepted text is a `text.generated` event keyed by its facts' hash. When the facts
  change (an answer, a new ask, the brief's numbers) the page shows the facts at once, marked "facts · updating", and
  the new text when it is written. A decision's texts and the merges are written from the decisions alone, without
  their ages or order, so an hour passing does not make them stale.
- **The call:** onloopd runs headless `claude -p --output-format json --tools "" --model sonnet` in a thread of its
  own, the prompt on stdin, from an empty temporary folder, without a console window and without ANTHROPIC_API_KEY (the
  subscription, as the carrier's turns). Calls are capped per kind per day (brief 8, decisions 20, merges 6); a failed
  call is logged as `text.failed` and is not tried again for 15 minutes for the same facts.
- **Facts only:** with `smart: off` in onloopd.yaml, or ONLOOP_SMART=off, with no `claude` CLI, or when a call fails,
  times out, is rejected or capped, every page shows the facts, as before 0.7.0's step 5: the brief's facts with
  "facts only", the decisions' own titles, every option outline, no merge card.
- **Merges are the person's:** a proposal shows both decisions and why; Merge closes the second into the first as
  superseded, with its asks counted in the first (`task.merged`); Keep apart hides it in this browser. Nothing merges by
  itself.

What 0.6's Now showed on the first screen and Home does not: the constraint in the system's words (Details · Map and
Rule alerts), the log since the last visit, the asks with no effect stated, the turns that ended without a valid outcome
line, the items about to expire, and the schedules' health. All of them are counted under **Details · Rule alerts**,
each count with a link to its rows.

### Map

The full guide is [map.md](map.md). Zoom 0 of one map (decision 0018). Each level has its own URL (`#/details/map`,
`#/details/map/<goal>`, `#/details/map/<goal>/<stage>`, and `?who=<holder>` for that holder's row selected), so Back, Forward and a
link land on the same level.

- **The signals:**
  - "N wait on you": the same count as Home and the task list;
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
- **Zoom 1** (`#/details/map/<goal>`): the goal's stages, in order, linked one to the next:
  - a checklist: to do, in progress, done;
  - a feedback batch: the team's own stages, then accepted;
  - changes: opened, checks, review, merged in the last week.

  Each stage has its count, colour, oldest item and, when red or amber, which item and why. A stage with no evidence
  is hatched, "not reported". A change's checks read "checks: 1 failed (e2e) · 4 passed · 1 skipped": skipped runs are
  counted, never named, and a failed or running check is named exactly as recorded.
- **Zoom 2** (`#/details/map/<goal>/<stage>`): the goal's stage × role grid. It has a column per stage (Idle first, for
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

### Old links

0.7.0 gave every view a new address. An old link opens the same view, and the address is rewritten in place (no new
history entry), keeping the rest of the link: `#/now` and `#/tasks` become `#/decisions`, `#/team` `#/agents`, `#/replay` `#/timeline`
(or `#/details/replay` when it names a moment with `t=` or `to=`), and
`#/map/...`, `#/outcome`, `#/conversation`, `#/schedules` and `#/audit?...` become `#/details/...`.

### Retired: Flow and Activity

Decision 0019 retired both views in 0.6.0; their parts moved first. An old link opens where they went, and the address
is rewritten in place, keeping the rest of the link: `#/flow` becomes `#/details/map`, and `#/activity` becomes
`#/timeline`. Parameters the new view does not know (Flow's `mode`, a selected card) are ignored there.
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
- While onloopd does not answer, a page keeps what it last read instead of an error.
- A server with no writer serves a copy, and says so: the header reads "Read-only copy · data up to <time>" with a
  grey dot, never "Live" (`GET /api/heartbeat`'s `read_only`, `frozen` and `data_up_to`). A read-only view of a
  ledger an onloopd still writes, its heartbeat fresh beside it, reads "Read-only view · Live".

### Theme

Light or dark follows the system's `prefers-color-scheme`, live, until the person picks Light or Dark in the theme
menu; that choice is kept in this browser, and System forgets it. `?theme=dark` or `?theme=light` in the URL overrides both (the screenshots use it).

### The state palette

`web/src/styles/tokens.css` is the only place a colour is chosen. The state colours are the only saturated colours on
the canvas, and each has its own step in dark mode, validated against the dark surface.

| Token | Means on Home and the task list | On the map | Shape on the map |
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
Team's lane uses the same words for what the carrier did.

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
(`web/e2e/demo_server.py`, built by `tools/demo_ledger.py`), at 1440 px on port 8792 and at 390 px on port 8793,
each width with its own server and fresh ledger, using the installed Chrome. Every test fails on a console error (a CSP refusal shows up as one) or on any request that is not a GET.

`npm run screens` writes the vision loop's pictures: every view at both widths in light and dark, plus the states one
click away. Set `SCREENS_DIR` to keep them outside the repository.
