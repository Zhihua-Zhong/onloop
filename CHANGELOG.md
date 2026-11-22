# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Upgrading

- **The ledger moves from schema 9 to schema 10** (decision 0019): the events table takes the source `otel`, Claude
  Code's own telemetry received by onloopd, and gains an index on each event's own time, for the replay's windows.
  Every event is kept, unchanged. Back the ledger file up first if you may want to go back; 0.5.x refuses a
  schema-10 file.
- **A supervisor step no longer loads the user settings file** (decision 0019). Its `setting_sources` default is now
  `project`, and a role that names `user` is refused at load: the user file's `env` wins over the step's own, so the
  step could send its telemetry to a collector outside onloop. The role table already gives a step its tools and
  model. A step's environment has no inherited OTel variable; with an onloopd next to the default ledger it gets
  onloop's OTel environment toward it, and otherwise exports nothing.
- **A supervisor step runs with `--restricted`**, as a carried turn does: Claude Code ignores every settings file for
  it, so a repository's `.claude/settings.json` cannot widen the role's tools. Its file tools stay inside `cwd`; a
  folder a step must read beyond it is declared in the role as `add_dirs` and passed as `--add-dir`.

### Security

- **Every GitHub Action is pinned by its full commit SHA**, with the version in a comment, in onloop's own CI and in
  the workflows `onloop init` writes (checkout v4.4.0, setup-python v5.6.0, setup-node v4.4.0). A test keeps every
  `uses:` pinned, one SHA per action. `.github/dependabot.yml` proposes grouped weekly updates for the Actions, pip
  and npm.
- **A vulnerability audit in CI**: the wheel job runs `pip-audit` on what the built wheel installs (any advisory fails,
  since Python advisories carry no severity) and `npm audit` on the UI's production dependencies (high or critical
  fails), through `tools/audit_gate.py`. `.github/audit-allowlist.yml` lets an advisory pass with a reason and an
  expiry; an expired entry fails the build. Empty to start. A local `npm audit` on 2026-11-22 (production
  dependencies) found 0 vulnerabilities.
- **The browser tests run in CI**: an `e2e` job builds the UI, installs onloop with it, installs Playwright's
  Chromium, and runs `npm run e2e` against onloopd's server over the demo ledger; the report and traces are kept as
  an artifact when a test fails. Until now the specs ran nowhere.
- **Python 3.13 and 3.14** are in CI's test matrix on Linux and Windows, beside 3.11 and 3.12, and in the package's
  classifiers. `requires-python` stays `>=3.11`.
- **Releases are built by CI from their tag**: on a `v*` tag, the `release` workflow checks the tag names the
  package's version, builds the UI, the wheel and the sdist, writes `SHA256SUMS`, attests where each file was built
  (GitHub artifact attestations), and attaches all of it to a draft release, which a person publishes.
  [docs/release.md](docs/release.md) has the steps and how to verify a release with `gh attestation verify`.

### Fixed

- **A writer no longer dies on a ledger another process holds**: every writable open is WAL with
  `synchronous=NORMAL` and a 15 s busy timeout (it was 5 s); a write whose lock stays held is tried twice more after
  a backoff, then fails that one write cleanly, rolled back, and the process goes on. onloopd's tick is skipped, not
  onloopd. `GET /health` counts the retried attempts, the failed writes and the longest write transaction
  (`receipts.locks`). The longest wait, 46.5 s, stays under the heartbeat's 60 s staleness less a tick.
- **A carrier booting in another carrier's grant window no longer releases its lease** (R7a's round 2, moved into
  0.6.0): a lease is granted before its run has a pid, and a second carrier's boot took it for an orphan, so two
  turns could run on one session. Each carrier boot now registers its instance (pid and creation time), and
  `recover` keeps a lease whose granting instance still runs, releasing it once that instance is gone; `expires_at`
  stays the backstop for a hung carrier. `test_carrier_race` had failed on this, under load.
- **Rule E reads the branch's own commits** (`git log --first-parent`, in time order): a merged side branch's `Node:`
  commit no longer takes a request made on the branch it was merged into.
- **Rule E no longer guesses on a shared branch** (the dogfood's round 2): clause 2 places a request on the next
  commit's node only when one role alone worked on that branch since the previous `Node:` commit. When several did,
  the requests stay between steps, and Replay says "branch shared by N roles" (`between_shared` in
  `GET /api/replay`). The figures are derived again from the ledger, so earlier windows read correctly at once.
- **A carried turn's telemetry is no longer dropped** (the dogfood's round 2, 2026-11-22): Claude Code numbers its
  records per process, and a carried turn is a new process resuming the same session, so the sequence started again
  and onloopd took the second run's records for the first run's, dropping them as redelivered. A model request is now
  keyed on its request id, any other record on its session, prompt id and sequence. A ledger written before keeps
  what it has; the dropped records were never stored.

### Removed

- **Flow and Activity are retired** (decision 0019, section 4), now that their parts have moved to Now, Replay and the
  map. The views, `GET /api/flow`, `GET /api/activity`, `views.flow` and the layout engine (elkjs) go. An old link
  opens where the parts went, and its address is rewritten to that view: `#/flow` becomes `#/map`, `#/activity`
  `#/replay`; both API routes answer 404. The navigation reads
  Now, Replay, Map, then the rest. `derive.data_as_of` replaces `report.activity.data_as_of`.

### Added

- **The map's zoom 0 opens into Replay**: "Who worked when: Replay →", over the last 24 hours (decision 0019's
  navigation).
- **`onloop say ROLE TEXT --step node:a1`**: a message names the step it is about, and the carried turn that delivers
  it is recorded as that step, so its cost needs no rule (as `onloop open --step`).
- **What came in, and the cross-check** (the onloop lead's rule: a silent drop is never invisible again). `GET /health`
  counts every write by route and answer and every telemetry record received, of a kind onloop does not keep
  (dropped on purpose), unplaced, kept, stored and already logged, since onloopd started; counts only. A carried run's `run.ended` carries the CLI's own count of its tokens (`usage`), and
  Replay says how much of it the window's telemetry brought, naming any run below 95%, and any above 102% or with
  telemetry against a count of nothing (`GET /api/replay`'s `coverage`, `low` and `high`). A carried run's telemetry
  is tagged with its run id (`onloop.run`), so a batch that lands after the next run started is still its own run's.
- **A carried turn's cost reaches a node by rule E:** a turn whose deliveries name no item records no step (a run is
  not a step), and `run.started` records the repository and branch the run works on, so its requests go to the next
  commit's `Node:` trailer there, labelled "attributed by rule".
- **The map's items carry what Flow's card panel showed** (decision 0019, section 4): at zoom 2, "Details" under an
  item gives its owner, what it waits on (why, and for how long), its default and expiry, its source link, and the
  command to copy when a wait is on missing evidence or a paused role.
- **Now, after Flow and Activity** (decision 0019, section 4): the constraint links to the map's cell that holds it
  (its goal and stage, with the holder's row selected; `constraint_map` in `GET /api/now`), and "See when the team
  worked" opens Replay. Open items whose expiry is past or within a day (Flow's Roadmap showed them) are listed under
  "Expires within a day" beside Stalled; `GET /api/now` answers them as `expiring`.
- **Activity's parts, moved to Replay** (decision 0019, section 4): every turn that ended in the window, a short mark
  at its end when its start is not known (an imported snapshot's turns, or a Stop with no SessionStart); turns per hour
  and roles active above the lanes; "Quiet for" in the table and each role's longest quiet in "Where the time went";
  "Data as of" when the ledger replays a team's export; and "No data before" where the window starts before the data.
- **docs/ui.md** names the views in the bar's order, says how they link (Now to the map's cell and to Replay, one
  "Waiting on you"), describes Replay, the map's item details and Now's "Expires within a day", and lists where each
  part of Flow and Activity went.
- **Now's "See when the team worked"** opens Replay over the last 7 days, a window Replay offers (it opened 48 h, which
  Replay has no button for). docs/replay.md describes what moved into Replay (turn marks, the density rows, "No data
  before", "Data as of", "Quiet for") and rule E's limit when two roles share a branch.
- **docs/replay.md**: the Replay view, cost by step and rule E, the telemetry behind it, `onloop open` and a step's
  products, with the API and the limits. `onloop open` is in the configuration reference.
- **The Replay view, after the lead's check** (four fixes): Play at the window's end starts over from its start,
  and previous-change from the end goes to the last change before it; playback keeps the wall clock (600x is 600x,
  even in a throttled or background tab); the figures are the window's by default, labelled "In this 6 h window, up
  to …", with the ledger's totals on request and never a lane-sum of waits; waiting is a light tint and working full
  colour, with lanes, table and breakdown ordered by work and the header naming who did most and its tokens.
- **Products at the cursor** (decision 0019, a4): a node or branch step on the Replay view opens what it produced as
  of the cursor: the files its commits changed, each at its latest commit then, read from git in a repository onloop
  recorded. Markdown is rendered from a tree (its raw HTML stays text), images show, and HTML shows in a sandboxed
  frame; onloopd serves a product with `Content-Security-Policy: sandbox`, so it never has onloopd's origin and no
  script runs. `GET /api/products` and `GET /api/products/file`.
- **Your wait made visible** (decision 0019, a7): Now says how many items wait on you (the same count as its list),
  how long the oldest has waited, and how much of the last day anything waited on you, with a link to Replay.
  Replay's "Where the time went" starts with the same for its window. `derive.your_wait` is the one derivation; the
  agreement test holds Now, the map and Replay to one count.
- **Who is working now** (decision 0019, a6): the Replay view opens live when no moment is in its link. The window
  ends now and is read again every 15 s, the cursor stays at now, and "Working now" lists each step onloop recorded
  that is open, with its role, how long it has run and its tokens so far. Moving the cursor leaves live; "Go live"
  returns. `GET /api/replay` answers `working_now` for the window's end.
- **Where the time went** (decision 0019, a2 and a3), under the Replay view's lanes: per role, its four kinds of
  time in the window as one bar, its tokens and cache reads, and its tokens by step, each labelled "recorded" or
  "attributed by rule", with what is left "between steps" shown with its share.
- **Rule E's clause 2** attributes a request no recorded step covers to the `Node:` trailer of the next commit on
  the branch its session works on. `session.opened` and `step.started` now record the session's repository path and
  branch name; the rule reads a commit's time and trailer only. Clause 1 (the turn's opening message) is unavailable
  by design: prompts are never logged.
- **The Replay view** (decision 0019, a1), `#/replay`: the team's work as lanes in proportion to time, over the last
  hour, 6 hours, a day or a week. A cursor shows each lane's state, its four kinds of time and its tokens at that
  moment, with the step of its latest request; it plays at 1x, 10x, 60x or 600x, and jumps from one state change to
  the next. The page indexes onloopd's window once (off its own thread past 100 000 events), so a frame is two binary
  searches per lane; the lanes are drawn per pixel column, whatever the number of events. "Show as a table" gives
  the same numbers. The demo ledger has a made-up telemetry story to show it.
- **`GET /api/replay?from=&to=&lanes=`** (decision 0019): the replay's window, at most 31 days, in columns: each
  lane at `from` and at `to` (its state, its four kinds of time so far, its tokens so far), and the state changes and
  model requests inside, each request with its recorded step. A frame anywhere in the window needs nothing before
  it. A model request counts as working over its own duration, so one still running at `to` shows in the window.
- **The replay's timeline** (decision 0019, `derive.timeline`): per role, its time cut into working, harness,
  waiting on the person and queued, each second counted once in the first kind that covers it, and its tokens
  (input, output and cache creation, with cache reads apart) by the step recorded on the session, else "between
  steps". Nothing is charged to the nearest step.
- **onloopd receives Claude Code's own telemetry** (decision 0019): `POST /v1/logs` and `POST /v1/metrics`, OTLP/HTTP
  with JSON encoding, with onloopd's token. The identity attributes Claude Code sends when signed in are dropped on
  arrival. A model request, a prompt and a tool call become `cost.request`, `turn.prompted` and `tool.ended` events,
  each with only its named fields: tokens, model and duration, a prompt's length but never its text, a tool's name and
  result but never its parameters. Metrics are answered and stored nowhere.
- **`onloop open <role>`** (decision 0019): your own interactive Claude Code session for an onloop role, measured like
  the sessions onloop starts. It runs with `--setting-sources project,local`, so the user settings file and its OTel
  keys are not loaded for it, and with onloop's own OTel environment, so its telemetry goes to onloopd only.
  `--step KIND:REF` names what it works on (`task:`, `pr:`, `branch:`, `node:`); `--dry-run` prints the command.
  Its start and end are `session.opened`, `step.started` and `step.ended` events.
- **A carried turn is a step:** the carrier logs `step.started` and `step.ended` around each run, on the one item its
  deliveries are about, else the run. Its turn now runs with onloop's OTel environment, and never with an OTel
  variable inherited from onloopd's own environment.

## [0.5.0] - 2026-11-20

0.5.0 shows where a team's work stands at a glance. It adds the map, one picture with levels from the goals down to
who holds what; the data contract, which replays a team's daily export read-only; feedback from both channels; and a
page that says whether it is live, quiet or not answering. Nothing acts that 0.4.0 left off.

### Upgrading

- **The ledger moves from schema 7 to schema 9.** The first time 0.5.0 opens a 0.4.0 ledger, it migrates it once, in
  two steps: schema 8 lets an event come from a team's replayed export (`source: import`) and gives a turn its start
  time, and schema 9 adds the feedback tables. Every event is kept, unchanged. After that, 0.4.x refuses the file,
  and the message says to keep the file and run an onloop that reads it. Back the ledger file up first if you may want
  to go back.
- **For a team that produces a daily export:** contract 1.2 is the newest, and 0.5.0 reads 1.0, 1.1 and 1.2
  ([data-contract.md](docs/data-contract.md)). 1.1 adds check runs, verdicts and who a change waits on; 1.2 adds `by`
  on `pr.opened`, the role that opened the change. An export without them still imports: the map then says "not
  reported" for a stage it has no evidence for, and holds a change that waits on nobody by the team.
- **For a 0.4 user:** nothing you have changes its meaning. The page gains the Map and Activity views, and a clock
  and a Live dot in the header. `GET /api/map`, `GET /api/activity` and `GET /api/heartbeat` are new, and read-only.
  onloopd.yaml gains an optional `overview: stuck:` block, the map's thresholds. The session carrier, the task list
  and the page's three narrow writes are as in 0.4.0.

### Added

- **The data contract** ([data-contract.md](docs/data-contract.md), decision 0017): a team's daily export, replayed
  read-only. `onloop import snapshot <folder> --ledger <ledger>` reads `snapshots/<UTC time>/` (manifest last), checks
  every record against the v1 schemas and the privacy rules, imports each file whole or not at all, and prints counts,
  never values. v1 reads roles, ended turns, pull requests (opened, merged, closed), goal checklist revisions, and
  feedback batches, items and links (an item's stage as the team reports it). Contract 1.1 adds check runs, verdicts
  (by role, never a login; a short code, never a body) and who a pull request waits on. A wait on the person is a
  task on their list, and a wait ends only when the export clears it. Ledger schema 8 adds the `import` source and a turn's start time.
- **Feedback, from both channels** (ledger schema 9). `onloop feedback "item" "item" … [--file F] [--by lead]` gives
  a batch in one sitting. Each item's title is its first line, scrubbed of secret-shaped strings; the body is kept
  only as a hash. A team's export reports its own batches, and each item's stage is kept as the team declares it,
  marked "reported by the team", with the time it reached that stage.
- **The map's zoom 0** (`GET /api/map`, decision 0018): one block per goal, grouped by origin: each checklist, each
  feedback batch, and "Changes in flight".
  - A block's colour is the worst of its children (jammed, waits on you, moving, not started), each with a shape.
    A red or amber block names its child and why in one line.
  - A stage with no evidence is "not reported", never 0.
  - An age whose start onloop cannot see shows as a lower bound ("≥ 9 days", "began before the first export").
  - Signals: waiting on you (the same count as Now), stuck (live work only), idle, and the team's longest pause in
    the last day.
  - The waits, oldest first.
  - The stuck thresholds are set with `overview: stuck:` in onloopd.yaml.
- **The map's zoom 1 and zoom 2** (`#/map/<goal>[/<stage>]`, `GET /api/map?path=`):
  - zoom 1 is a goal's stages, each with its count, colour, oldest item and reason, and a change's checks (skipped runs
    counted, never named);
  - zoom 2 is the goal's **stage × role grid** (mock-up v2): a column per stage, a row per holder, worst first, each
    cell a bar as long as its count in its worst colour. For changes, each column says how many reached it in the
    week, Merged counts by the role that opened each, and the live stage with the most jams is the bottleneck. A
    cell opens its stage at that holder, whose items, with where to act, follow the table. The first row is always
    the holder zoom 0's reason line names;
  - `block.turned` events record each block's and stage's change of colour, on onloopd's tick.
- **Moving around the map:** a camera move into the block on opening (none with reduced motion); a second click during
  the move is ignored; the chain of stages never crosses the canvas. See [map.md](docs/map.md).
- **Contract 1.2:** `pr.opened` may carry `by`, the role that opened the change (a declared role, `team` or `human`,
  never a login). A PR first sent without it gets its role from a later copy. The map holds a change that waits on
  nobody by that role, and names it in the reason line ("builder: no move on pr#9 for 5 days"). Only without `by`
  (a 1.0 or 1.1 export) or with `by: team` is the holder "the team".
- **The page recovers when onloopd comes back:** a view in error tries again every few seconds, and the last good data
  stays on screen meanwhile.
- **The Activity view** (`#/activity`, `GET /api/activity`): one lane per role with its turns on a time axis, side
  sessions under their role, each role's longest silence, turns per hour and roles working at once, for 24 hours, 48
  hours or 7 days, with a table view. It works without outcome lines. The window starts where the data does ("the 3
  days since the data began"), roles with no turns are one line, and a silence is red only while it is still going.
- **Idle work is set apart at every level.** An open change with no move for 7 days is idle: parked or abandoned. It
  is never counted as jammed, in the header, a tally ("3 jammed · 2 idle · 1 moving") or a reason line. It is
  drawn hatched greyish red, and a tally's jam comes first, set apart from what waits on you.
- **A clock and live ages on every page.**
  - The header has a clock that ticks every second, and "data as of N s ago".
  - Every relative age rises between answers.
  - `GET /api/heartbeat` gives onloopd's last heartbeat and its tick period. The Live dot turns amber with "No news
    for N s" past two ticks, and red with "onloopd not answering for N s".
  - Now keeps its last answer on screen while onloopd is down.

### Changed

- A team that writes no outcome lines gets one summary ("Outcome lines not adopted: 0 of N turns end with one")
  instead of a stall for each role's latest turn.

### Fixed

- **A widened window re-lays the map,** even in a page that draws no frames, and past 1920 px the blocks use the
  width (up to 760 px each).
- **"team" and "the team" are one holder** on every view. A wait on `team` and a change with no opener are both
  "the team".
- **Now and Flow count "waiting on you" the same way** (finding #20). Both read the open task list. Flow's "You"
  node says how many tasks wait on you, and its tab says how many cards on the canvas wait on you. An agreement test
  checks Now, Flow and the task list on the demo ledger and on asks that are not cards.
- **Done and Decline can be undone for 8 seconds** (finding #19). The mark is sent only when the count ends. Undo, or
  leaving the page, sends nothing, so the task stays open.
- **A gap in onloop's own record shows as a gap, not as quiet** (finding #14). A gap while onloopd runs (the machine
  slept or the process was paused) is now recorded too, besides the gap at start. Now's health card lists the last
  week's gaps with their cause (a crash, a shutdown or sleep). The quiet reading says how much of the quiet time onloop
  was down, and no longer counts onloopd's own records as the team's activity.
- **The daily report runs by itself** (finding #15). Without a `jobs:` key in onloopd.yaml, and without the file,
  onloopd writes yesterday's report to `reports/` next to the ledger at 07:30. `jobs: []` turns it off.
- **An ask logged before 0.4.0 is no longer closed by its author's next turn.** After an upgrade, every outcome-line
  ask from 0.3 read "resolved without you", because a task opened by a version-1 event ended when its wait cleared, and
  a version-1 wait clears at the role's next turn. That is the silent drop decision 0013 rejects. Such a task now stays
  open, listed as incomplete (it does not say what it changes), until the person resolves it, its author resolves it
  with `done resolves=` or closes its ref with `done ref=`. It is returned to its author after 7 days, as any
  task is. A restated ask on the same ref amends the
  open task instead of opening another. A ledger already on 0.4.0 shows this after `onloop overlay replay`.
- **`onloop ask` always opens the task whose id it prints.** On the same ref as an open ask from the same author, it
  used to amend the older task, so the printed id, and a `ref=task:<id>` that names it, pointed at nothing. The older
  ask is now superseded by the new task, with the `onloop ask` event as its evidence.
- **A task's title is in plain words** (finding #26). An ask that does not say what it changes read as its raw outcome
  line ("outcome: blocked owner=human ref=r5-planning"). It now reads "An ask with no effect stated, about:
  r5-planning", with who asked and when; the line itself is shown on hover and in the expanded card. `onloop tasks`
  and `--brief` lead with the same words and put the codes last, in parentheses. A first plain-language check, for
  task titles, fails a title that starts with a code, shows a code outside parentheses, or uses a jargon word.
- Now links a task's or a wait's reference only when it is a web address. A file path was a link that opened "not
  found".
- At phone width, a task row's text wraps to two lines instead of being cut, so rows that start alike can be told
  apart.

## [0.4.0] - 2026-11-17

0.4.0 is the first release in which onloop can act, and every act is off until you switch it on. It adds the session
carrier, which can deliver messages into a team's role sessions; the person's task list, in which every ask says what
it is worth; goals with progress KPIs that each carry their denominator; and live pages that keep up with a busy team.
Without a `carrier:` block in onloopd.yaml, onloop observes and records, as 0.3 did.

### Upgrading

- **The ledger moves from schema 5 to schema 7.** The first time 0.4.0 opens a 0.3.x ledger, it migrates it once, in
  two steps: schema 6 adds the session carrier's tables, and schema 7 the task list and goals. It keeps every event
  and rebuilds what it shows from them. An ask logged under 0.3 replays as a task that ends when its wait clears, as
  0.3 read it. After that, 0.3.x refuses the file, and the message says to keep the file and run an onloop that reads
  it. Back the ledger file up first if you may want to go back.
- **The outcome line, version 2** ([decision 0012](docs/decisions/0012-outcome-line-version-2.md)). Every line that
  0.3 accepted means the same, except a placeholder waker `pid:0`, which is now refused. The new keys (what an ask is
  worth, and waits on outside events) are refused by 0.3.x, so teams write them only once every reader is on 0.4.0.
- **The page can now write, in three narrow places:**
  - the Conversation view's message box (`/ui/messages`);
  - a task's Done or Decline, and its keep (`/ui/tasks/resolve`, `/ui/tasks/keep`);
  - the visit that "since you were away" counts from (`/ui/visits`).

  Each needs the page's own session cookie, a CSRF token and a same-origin request. Each is recorded as a request
  from a local user, not verified, and never as an approval.
- **New, optional blocks in onloopd.yaml:**
  - `carrier:` (the session carrier; nothing is delivered without it);
  - `kpi:` (`timezone`, for calendar days);
  - `adapters:` (read-only trackers);
  - `goals:` (goal checklists);
  - `jobs:` (onloopd's own daily report).

  An existing onloopd.yaml keeps working unchanged.

### Added

- **The person's task list** ([tasks.md](docs/tasks.md), decision 0013). onloop's ledger is the one record of what
  waits on the person:
  - an ask says what it changes, which goal item it moves, how much it weighs, from when its delay costs, what waits
    on it and what happens if nobody answers: `onloop ask`, or the same keys on the outcome line;
  - one order: weight, then the days left (counted when read, from a stored date), then age. An ask that does not say
    what it changes is listed last and sent back to its author once; a critical one keeps its place, flagged;
  - a task closes only with a resolution (done, declined, superseded, expired) and its evidence, and the ledger
    refuses anything else. A wait clearing never closes one, and an approval at the merge gate is done only on the
    code host;
  - a task goes back to its author after 7 days and to the coordinator after 14; open pull requests get the same two
    alerts; onloop closes nothing itself;
  - a reconciliation check reports any ask seen in outcome lines, pull requests, a tracker or a prompt that is
    missing from the list;
  - `onloop tasks [--digest] [--brief]`, `onloop task resolve|keep`, `GET /api/tasks` and `GET /api/digest`. Now's
    "Waiting on you" is the list, one row per task, with a "since you were away" line above it.
- **Read adapters** (`adapters:`, the entry points `onloop.adapters`). They have no write method, and their client
  sends only GETs and GraphQL queries. GitHub issues with a label are built in.
- **Goals and outcome KPIs** ([decision 0014](docs/decisions/0014-goals-and-outcome-kpis.md)). A goal checklist is
  read from its edit history, with its size and the date of it. `Goal:` lines in pull-request bodies link work to it.
  The new Outcome view and `onloop kpi` show goal items done per merged change, the share of changes linked to a goal,
  the fixed cost of each change, and the open work. Two changes that move the same item get a hint to combine them.
- **Waits on outside events**: `waker=event:<kind>:<id>` (a CI run, a set of checks, a review), checked through a read
  adapter. The GitHub checks behind these are not live yet, and such a waker reads "unknown".
- **onloopd's own jobs** (`jobs:`): the daily report, declared and watched as a schedule, caught up once after a gap.
  At start, onloopd records how long it was down and, on Windows, why. A quiet ledger is read as idle or waiting.
- **The session carrier** ([carrier.md](docs/carrier.md), decision 0010). onloop delivers messages into the role
  sessions itself, with headless `claude -p --resume`, under the user's own subscription login:
  - one live lease per session id, enforced by the ledger; a session open in another process is never resumed;
  - one process and one composed message per run, in a Windows job object, `--restricted`, with no prompts and only
    the role's tools, never a shell (a role that names one is refused);
  - a delivery is acknowledged by the role's next turn, whatever it said; a missing outcome line starts the
    corrective only for a role that opted in;
  - at most one corrective wake per turn, then the coordinator; handoffs and review requests to a carried role; a dead
    waker goes to the coordinator first;
  - off by default: without an opt-in it only records what it would send, with the exact command and text;
  - the kill switch (`onloop carrier stop`, or a `carrier.off` file), budgets per role and across roles, a refusal to
    run with a metered API key, the login check, and the usage limit (a rejection pauses until its reset, a warning
    halves the rate, overage pauses at once);
  - recovery after a crash, a hook spool across a restart, and sleep.
- `onloop carrier start|stop|status`, `onloop role opt-in|opt-out`, `onloop say`, and the `carrier:` block of
  onloopd.yaml.
- The **Conversation** view: the carried roles, one role's thread (your messages and how far each got, onloop's
  notes, what it would send, runs, and turns by their outcome line), and a message box that is open only when a
  message can go somewhere. `GET /api/conversation`.
- The text onloop writes into a session is stored, scrubbed of secret-shaped strings, for 30 days, then only its hash.
  Nothing a role writes is stored.

### Changed

- Now leads with the task list when tasks wait on the person; the constraint is one line above it. Stalled and Health
  sit beside it.
- Now's stall card and Team's lane say what the carrier did about a turn without an outcome line, and "would wake
  (observe only)" only where it did nothing.
- `onloop daemon hook` keeps a post it cannot deliver in `hook-spool.jsonl`; onloopd logs it once when it is back.
- A development build no longer reports an older version.

### Fixed

- A page that follows live events now refreshes at least every 2 seconds while events keep arriving. Before, it
  refreshed only once the stream paused, so a busy team's Now and Flow could stay as they were for as long as the
  stream ran.
- The lead-time KPI says "no merged PR in the window" where it printed `None`.
- Opening a ledger made by a newer onloop now says what to do, not only that it is newer.

### Removed

- Nothing is removed in 0.4.0.

## [0.3.1] - 2026-11-12

### Fixed

- On Linux, a process that has exited but that its parent has not reaped yet (a zombie) no longer counts as running.
  `onloop daemon stop` no longer waits out its timeout and reports a forced stop (exit 3) for an onloopd that has
  stopped. onloopd's start no longer refuses to run over the pid of a dead one. The dead-waker probe no longer misses
  a `pid:` waker that has exited. Windows was not affected.

### Changed

- The README and the release notes install the release's wheel, which carries the UI; a `git+` install builds from
  source without it.
- CI also runs on release branches (`release/**`) and release tags (`v*`), so a release commit is tested before it
  is published.

## [0.3.0] - 2026-11-11

### Upgrading

- The merge gate now records its decisions in `ledger.db` (source `gate`, ledger schema 5), not in `onloop.db`.
  History already in `onloop.db` is carried over by the one-shot import, `onloop overlay import --store
  .onloop/onloop.db`: run it once before you upgrade. A gate run where no ledger file exists still decides as before,
  and warns that its decision was not recorded; it never creates a ledger.
- The old stores are not read on every tick any more. An install that used `onloop.db` or a work graph imports each
  once into the ledger: `onloop overlay import --store .onloop/onloop.db --workgraph workgraph.db`. The import reads
  them only, by cursor, so running it again adds nothing.
- A ledger built before this release shows work-graph items without their zones on Flow until its projections are
  rebuilt: `onloop overlay replay`.
- `onloop events` reads the ledger: `--db` is now `--ledger`, and `--since` is a time. The sequence cursor is
  `--after`.
- The UI ships inside the wheel. A source checkout builds it with `cd web && npm ci && npm run build`; without it,
  onloopd still serves the API, and `/` says how to build the UI.

### Added

- The UI (decision 0009, docs/ui.md). onloopd serves it at `http://localhost:8790`, from its own origin, read-only,
  under a strict Content-Security-Policy. It has five views of the ledger:
  - **Now:** the biggest constraint with its numbers and a suggested next move; what waits on the human, with the
    default and the time left; what has stalled ("would wake (observe only)", and whether the role took a later
    turn); and health (failing and overdue jobs, onloopd's heartbeat, the dead-man switch).
  - **Flow:** the canvas on the ledger. Zones come from the direction, else the owner, else "unassigned". The locks,
    queues and the human are nodes linked to the cards that wait on them. It has the constraint overlay, a view
    switch (graph, waiting on you, roadmap, calendar) and a phone opening on the first zone with a human wait.
  - **Team:** one lane per role.
  - **Schedules:** every job, failing first, with its log tail.
  - **Audit:** the event timeline, with filters.

  Actions are shown as commands to copy. Live updates come from the event stream, and a banner shows when it drops.
- `GET /api/now` and `GET /api/flow`, the read models behind Now and Flow, and `GET /api/stream?plain=1`, the event
  stream without event names, for a browser.
- `heartbeat_schedule:` in onloopd.yaml names the schedule that is onloop's own heartbeat (its dead-man switch). It is
  logged as a `config.set` event; without it, Now says "dead-man not configured".
- CI builds the UI and the wheel, and `tools/check_wheel.py` fails a wheel that lacks the UI.
- `tools/demo_ledger.py` writes a demo ledger of generic sample data through the ledger's own events, for the UI's
  tests and screenshots.
- The architecture as a C4 diagram set (docs/architecture.md, "Diagrams"): one model,
  `docs/architecture/model.yaml`, generated by `tools/diagrams/c4.py` into one draw.io file with seven pages (system
  context, container, component for onloopd, a generated code view, deployment, and two dynamic views), each exported
  to PNG. The generator refuses a view that names an unknown element or a relationship whose end is outside it.
- The overlay ledger (decision 0006): one SQLite file, `.onloop/ledger.db` (or `$ONLOOP_LEDGER`), with an append-only
  event log and the projections items, waits, resources, leases, roles, deliveries (a message to a role: created,
  delivered, processing, acknowledged) and schedules. Every write logs the event and updates the projections in one
  transaction; `onloop overlay replay` rebuilds the projections from the log and says whether they are byte-identical
  (`--check` does it in a scratch copy). The schema fixes the disposition outcomes (`done`, `idle`, `in_review`,
  `blocked`, `handoff`, `continue`), a `ref` on items and waits, and `waker` resources (a cron id, a monitor or a pid).
- `onloop overlay import`: a forward-only, read-only import of the legacy `onloop.db` and the work graph's store into
  the overlay ledger, by cursor, under each old event's id, so running it twice or after a crash changes nothing.
  `onloop overlay replay` is the overlay ledger's successor to `onloop workgraph replay`, which stays for the legacy
  work graph.
- The constraint query: open waits grouped by what they wait on, with the waiting downstream of each, the largest
  first.
- onloopd, the resident service (decision 0008), as a skeleton: `onloop daemon run|stop|status`. It binds the loopback
  address only (any other is refused), serves `/health` and the read-only `/api/items`, `/api/waits`, `/api/leases`,
  `/api/constraint`, `/api/events` and a Server-Sent Events stream. It is stopped through its pid file. Its Task
  Scheduler and Healthchecks parts wait for their spike.
- onloopd receives the role sessions' Claude Code hooks (decision 0007): `POST /hooks/stop`, `/hooks/session-start`
  and `/hooks/notification`, posted by `onloop daemon hook --event E --role R`, which always exits 0. Each Stop is a
  turn with its parsed outcome line, or the reason it has none; the message text is never stored. A `continue` waits
  on its waker, and a permission prompt waits on the human, until the role's next Stop. Write routes take only the
  token in `onloopd.token`, a JSON object of at most 1 MiB, and use the ledger's own time.
- `onloopd.yaml` declares the scheduled jobs onloopd watches, and jobs report their runs to
  `POST /sources/schedules/<id>/ping|fail|start` or through a Healthchecks webhook at `POST /sources/healthchecks`.
- onloopd reads GitHub into the overlay ledger itself when `onloopd.yaml` names a repository (`github: {repo: ...}`):
  intake's reads and diff, with the same event ids, its snapshots and ETag kept in the ledger, and a merged pull
  request's body kept for its post-merge steps.
- Detectors on onloopd's tick, observe-only (nothing is woken, sent or restarted): a role turn that ends without a
  valid outcome line; a `continue` whose waker is not alive (a `pid`, a `cron` schedule declared in `onloopd.yaml`
  that is overdue, or a loopback `monitor` that does not answer 2xx); a scheduled job that failed or did not run in
  its period plus grace. Each finding is one alert item while it lasts, and the action onloop would take is logged as
  a `would.*` event.
- Post-merge steps (story 10): lines `Post-merge: <role>: <step>` in a merged pull request's body (the pattern is
  `post_merge.pattern` in onloop.yaml) become follow-ups that need evidence, and the pull request stays blocked
  until `onloop item evidence <id> <ref>` attaches it to each.
- The constraint view types its reasons: per bottleneck, the count per reason, each wait with its waiter, item and
  time waited, and a resource's probed state; a `continue` on a dead waker also counts as `dead_waker`. onloopd
  serves `/api/turns`, `/api/alerts`, `/api/schedules` and `/api/roles`.
- `onloop report observe --since D` and `/api/report/observe`: the observe-only report. Each `would.*` record next
  to what happened afterwards (would have woken a role at T; its next turn came 42 min later, in review), and per
  role the turns that ended without a valid outcome.
- `onloop ingest github --once --repo owner/name`: one read-only read of any repository into the ledger.
- `onloop kpi weekly`: asks to the human per ISO week with each one's wait (story 14), and messages to the
  coordinator split into mechanical and judgment (story 12), beside the team's own baseline, which
  `onloop kpi baseline import FILE.json` takes in the shape of `docs/schemas/kpi-baseline.schema.json`.
- `onloop role pause|resume ROLE`: a paused role's items wait on the human (`paused_by_human`) until it is resumed.
- Decision records 0006 (the overlay ledger) and 0008 (the resident service); 0007 is reserved for the disposition
  contract.
- The supervisor (docs/supervisor.md): the role table and the governed step runner for a team of agent roles, with no
  model of its own.
  - `onloop step --config F --role R --pr N [--live]` runs one step for one pull request, for a CI job. It reads
    whether the step is due from the role's triggers, by GETs, and exits 0 with the reason when it is not. Under GitHub
    Actions it says in the job's log and summary when a step did not run.
  - The step is one headless `claude -p` call (`--output-format stream-json --verbose --max-turns N --permission-mode
    M --tools ... --allowedTools ... --strict-mcp-config --setting-sources ...`, the prompt on stdin, a new session id
    per run), stopped by its own pid only (its process group on POSIX, `taskkill /PID /T` on Windows) past its wall
    budget, a hard deadline or its `silence` limit. **Dry run is the default**; `--live` refuses `ANTHROPIC_API_KEY`
    and `ANTHROPIC_AUTH_TOKEN`, and a CLI that `claude auth status` says is not logged in or uses an API key. It takes
    `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) as the subscription credential.
  - The role table names the working directory, permission mode, allowed tools, prompt template (or `prompt_file`),
    budget (wall, turns, silence), WIP limit, retry policy and triggers; `bypassPermissions` is refused. A step is
    keyed on (role, item, item version, round). A pull request trigger can key on the head commit (`version: head`),
    drop a pull request once a login has answered its current head (`unanswered_by`), require green checks at the
    head (`require: {checks: green}`, the base branch ruleset's required checks by default; a check re-run in a later
    suite counts only by its latest run), and put first the pull requests whose changed paths match `priority_paths`.
  - A role can ask for a structured result (`result_schema`, passed as `--json-schema`) and have each live step's
    result written to `result_file`, so the step needs no write tool, and can name an `after_result` command that runs
    on that result (no shell, stopped by its pid past its timeout; a nonzero exit makes the step `after_failed`).
  - `onloop supervise --config F --once` prints the exact command of every step that would start now, and starts
    nothing.
- The person's task queue: issues carrying a configured label are asks (id, paraphrased ask, owner, state, "done
  when", default, dependencies, last moved), mapped by the work graph's issue reader. An ask is drained into its
  owner's queue when its last dependency closes; waiting on CI is a state, not idleness. `onloop digest` says what
  moved, what is done and what waits on the person, with a default for each, from the last snapshot or, with
  `--refresh`, from the source, writing nothing. Items in a private file are digested and watched but never become a
  step.
- Attention rules computed without a model, each with a threshold, a quiet period and a repeat cap: an ask that has
  not moved, asks waiting on the person, a role with due work and no step, a stale supervisor heartbeat.
  `onloop attention` evaluates them from the state directory (exit 1 when one fires). Notifiers sit behind one
  interface: log, webhook (https or loopback), desktop (a toast on Windows, notify-send, osascript), stub.
  `onloop selftest` proves the quiet period and the cap.
- examples/supervisor/ (a role table with author, reviewer and operator, and an offline fixture) and
  examples/deploy/crontab.example (the attention check from cron).

- The work graph: deciding from the panel (docs/workgraph.md, "Deciding from the panel" and "Who can decide").
  Each node waiting on the human gets a decision card with its question, default and expiry, and three choices: yes; no,
  keep the default; or an answer in the person's own words. A choice is appended to the log as `decision_recorded` (node,
  choice, text, time, panel session id, and a one-off submission id, so a form sent twice is one decision) and becomes a
  proposed write-back: the exact comment and the label change (the waiting label off, `decisions.decided_label` on) that
  would go to the source. Write-back goes through a writer command named in the instance configuration
  (`decisions.writer`), run without a shell and only when `enabled` is literally true; the default is false, and with it
  no process is started. Decisions change no node and do not count as movement. `POST /decide` is taken only from
  the loopback address, under the panel's own Host and Origin, a same-origin fetch, a form body and a random token drawn at each
  start; every request with another Host is refused. The documentation states that this stops forgery from a web page
  but not a process running as the same user, so a panel decision is a record of intent, not an authorisation, and
  recommends the OAuth device flow under the person's own GitHub identity as the next step (none of the identity options
  is implemented). Store schema version 2 widens the log's kinds by copying it row for row. `onloop panel --read-only`
  shows the panel without forms. The GitHub adapter is unchanged and still issues GETs only; tests prove that no process
  is started on a decision while write-back is off, and ten new mutation entries prove the token, Origin, Host and referrer-policy checks
  and the off default bite.
- The work graph and its control panel, read-only (docs/workgraph.md). An event-sourced store (`onloop/workgraph/store.py`):
  an append-only log of `node.created` and `node.changed` that the database refuses to update or delete, and the current
  state as a projection of it that `onloop workgraph replay` derives again and compares. A node is a topic, task, decision
  or hypothesis with a state, a class (expedite, standard, background), a weight, an owner role, `spawned_by`,
  `depends_on`, times, a link, and a default and expiry when it waits on the human. `onloop workgraph sync` reads GitHub
  Issues read only (`gh api` GETs) through an adapter whose label meanings and parent rules (sub-issues first, then body
  patterns) come from the instance configuration, and appends only differences: a second run with nothing new appends
  nothing, and a failed read appends nothing. `onloop workgraph status` and `onloop panel` show, computed and never
  dispatched, each direction's open count, oldest open age, last movement and a starved flag past its class interval
  (2 h, 24 h, 7 days by default), what waits on the human with its default and expiry, and a weighted fair-share next up
  per role. The panel is one server-rendered page on the loopback address (GET and HEAD only, the store opened read-only, no CDN)
  with four views: Directions, Tree, Waiting on you and Activity. `onloop selftest` proves the starvation rule and the
  fair share; the core imports no adapter, and a test says so.
- `onloop workbench check` judges a built site: W1 (reproducible, read-only, no network), W2 (no orphan figure), W3 (every
  claim resolves; measured and external counted apart), W4 (the inbox and deliverables equal the ledger), W5 (no
  misrouting), W6 (delta labels complete and confirmed by git on a sample), W7 (never softer than the log), W8 (the page is
  not an input) and L1 (every list is a listing), with the numbers, and exits non-zero on a fail. `onloop workbench serve`
  serves the site on the loopback address only, rebuilding when the page is asked for. Claims, criteria, activity and the problems
  list are listings too, so no section is left to convert. See docs/workbench.md.
- `workbench check` reports `NOT EXERCISED (0 items)` for a check that looked at nothing, and exits 1 unless `--allow-empty` is
  given; the output names the check either way. A pass over nothing was reading as a pass.
- Ids are ordered by their numbers (D2 before D12) in the inbox, the questions and the deliverables.
- `tests/test_mutation_table.py` checks in seconds that every mutation entry's old text occurs exactly once in its file, so a
  commit that moves the text fails on that commit and not in the next full run. `workbench check` rows always carry an
  integer `items`, and W6 reports its recomputation half as not exercised when there is nothing to sample.
- Every text read and write names its encoding: ruff's PLW1514 is on, and a test covers `subprocess` in text mode, which ruff
  does not. Four `subprocess.run(..., text=True)` calls left the encoding to the locale.
- The rehearsal week gains a workbench step: the page built from it counts the same events and merges as the log and `kpi`.
  `workbench check` W6 asks the page only for the head moves of pull requests that are still open, and reports the others
  apart; the git sample still covers all moves.
- `onloop selftest` also proves the workbench's checks (W2, W3, W4, W5, W7, L1), the inbox routing, the delta's wording, the claim
  pointer and the criteria's LATE rule: each fails on a broken input.
- Anchored human grants: a slot with `human_grant: anchored` (needs `approver: human`) is granted only with
  `onloop slot grant ID --anchor URL`, the URL of the human's own `/grant <instance>:<request id>` comment on the request's pull
  request or issue. It is verified live from GitHub's answer (repository and number from the comment's `issue_url`, author
  from `user` as a `User`, the line through the same sanitiser the gate uses, written after the request, never edited),
  the request's ref must be exactly `pr#N` or `issue#N`, a bare `--by` is refused, and the event stores the comment id and
  time. Each database has a random instance id (schema version 3, made on creation or migration, never changed), and the
  line names it, so a comment written for another database, or in the old `/grant <id>` form, is refused whatever the
  clock says; the created-after check stays. `slot request` prints the line to write, `slot status` the instance. The
  default, `attested`, is unchanged. Stopping the anchor is a loosening.
- Slot capacity: `slots.<name>.capacity` (1 to 8, default 1). Each live grant takes a numbered seat and the unique index
  `(slot, seat)` over live grants replaces `one_live_grant`, so the database, not the code, refuses two holders of a seat;
  at capacity 1 that is exactly the old rule. The `gate` slot cannot have a second seat, in the policy or in the database.
  The queue is first come first served by the time a request joined, then its id. Schema version 2 migrates a 0.3
  database in one transaction and keeps every live grant (seat 1), request and pending delivery; a database from a newer
  onloop is refused untouched. Raising a slot's capacity is a loosening. The CLI also tells a read-only open of an old
  database to run any writing command first.
- `enforce:` in the policy turns the two pre-flight warnings into gate refusals: `line_endings: refuse` adds `P004` (the
  change adds carriage returns, read from `git diff`) and `uncovered_paths: refuse` adds `P005` (a path no policy path
  covers). Both default to `warn`; with no block a repository's gate decisions are unchanged, which a test proves on a
  fixed set of pull requests. Pre-flight reports them as form refusals when enforced, the audit reads the same table, and
  weakening either is a loosening.
- The reviewer's verdict is read in one place, `onloop/verdicts.py`: only the policy's reviewer App as a Bot, only APPROVED or
  CHANGES_REQUESTED. The delta pane, the KPI waits and the shadow report all ask there, other reviews are listed apart as
  "other reviews, not a verdict", and a test fails if any other module reads `review.submitted` events directly.
- The Deliverables page and the inbox are listings too: every list in them, and each row's reasons, says whether it is whole, and
  "open pull requests for the human" is a listing that is not in this build, with its reason, instead of a sentence.
- Every list the delta pane draws says whether it is whole (`workbench/listing.py`): a listing is its items and a status
  (ok, failed, truncated, not in this build) with a reason, and the renderer refuses a list that is not one. The thread list
  therefore says exactly one of "could not be read for this push", "more than 100 threads: the list is incomplete", or
  (only when the read was whole and nothing is open) "No open review threads from the reviewer." Intake logs how each read of
  the threads went (`review.threads_read`) before the push it describes, and a thread the reviewer opened and someone else
  resolved stays on the pane as "resolved by <login>, not by the reviewer" until the reviewer's next verdict. The other
  sections of the page convert to listings one at a time; a test names those still to do and fails on any new bare list in a
  converted one.
- Review threads on the delta pane. Intake reads each pull request's review threads (GraphQL, first 100; a failed read
  leaves the threads unknown, never fails the pull request) and logs `review.thread_opened` and `review.thread_resolved`
  (new event kinds). For each push the pane lists the reviewer's open threads and whether the lines each was anchored on
  are in the push: `touched` or `anchor lines unchanged`. It says only what was checked, never that the must-fix was
  met, and it says `not checked` (with the reason) when the thread was anchored at an earlier head, has no line, or the
  push's file or hunk list was cut. The delta now carries the old-side line ranges of each file's hunks.
- The reviewer's delta pane. At a head move intake also stores a `delta` (display only) on `pr.head_moved`: equal trees,
  line-ending-only, leading-or-trailing-whitespace-only (compared line by line, never with `git diff -w` or `-b`, so a
  space removed inside a string stays substantive), or substantive, with per-file counts and the files where indentation
  is meaningful (`workbench.indent_sensitive`). The pane puts the log's own word first, never reads a push as harmless,
  says when the latest review is of an older head, and shows `delta unavailable` when git could not say. `substantive`
  and every KPI are unchanged with and without it.
- Claims, records and criteria on the workbench (`docs/claims.md`). `kpi --record` and `acceptance --record` write a
  record and register it as measured in the process that computed it; `onloop record` registers an existing file as
  external and attested, never as measured. `claims.yaml` figures resolve only against a registered record whose
  definition matches the claim and that has a denominator; `criteria/*.yaml` verdicts (PASS, FAIL, LATE, NO RESULT,
  NO BASELINE) are computed, and criteria not provably written before the run are LATE. `workbench build --root`
  adds the section and an evidence drawer. Criteria are anchored by content: LATE unless the commit each run was measured at holds the
  criteria file byte for byte; dates decide nothing.
- Workbench pages from the ledger: `deliverables:` (owner, state, live link, fallback, due, and a verification by someone
  other than the owner), `questions:`, and `decided_by`, `decided_on` and `evidence` on decisions, all optional. The
  Deliverables page colours each row from a rule it prints (absence is never green), and the inbox has three sections:
  rulings, questions only the human can answer, and decided by the lead this week, with a decision the human made
  routed to their own record and an unattributed one shown in red. New policy block `workbench:` (`stale_days`,
  `allowed_origins`); raising the first or adding an origin is a loosening. None of the new ledger sections reaches an
  anchor.
- `onloop workbench build --out DIR`: read-only pages built from the event log, the ledger and the policy, as a static
  site (`index.html`, `model.json`, `status.json`). The same inputs and the same `--now` give the same bytes; the store is
  opened read-only; the page asks for nothing (a Content-Security-Policy with one inline script by hash). Every figure sits
  in a chip that says where it came from, and a scan finds any digit outside a chip, a date or an identifier. Text from
  outside is escaped and a URL is a link only on an allow-listed origin. A failed rebuild keeps the last good page, marked
  stale with the error. Nothing outside the package imports it.
- The loop without waiting, in one line: `onloop run`, `intake`, `events`, `slot`, `preflight`, `kpi`, `acceptance`,
  `anchor`, `hooks`, `handover`, and the `slots:` and `automation:` policy blocks. It starts in observe mode and writes
  nothing until the human leaves it. See `docs/automation.md`, which also lists every field and what loosens it.
- R1 of the acceptance test: a recorded `runner.stopped` (now carrying a `reason`) excuses at most
  `automation.max_stop_minutes` (default 60) of a gap; a longer silence is a gap. The report gives total downtime and
  every stop with its reason.
- `kpi --shadow`: a "would merge" followed by nothing inside the window is *unexplained* and keeps the exit code at 1
  until a person classifies it (`--classified FILE`, each line with a note, `classified_by` and evidence). `automation.external_gate_check` names a check run posted by
  a gate outside onloop; a failing run on the same head explains the row as an external refusal.
- The reviewer's wait, and the gate's wait, in `kpi` end and start only on a review by the policy's reviewer App
  (matched as a Bot, as the gate does); a drive-by approval, or a User with the same name, does not count.
- `onloop watchdog --db` flags any delivery deferred longer than `automation.max_defer_hours` (default 4).
- Policy fields `automation.max_stop_minutes`, `max_defer_hours` and `external_gate_check`; raising or setting any of
  them is a loosening.
- A rehearsal test runs a scripted week of about twenty pull requests through the real runner with `--observe-only`
  and checks the verdicts of `kpi --shadow` and `acceptance`, including "no evidence" on day one.
- `onloop selftest` covers the new checkers: slots, pre-flight, the shadow comparison, the acceptance test and anchors.

- The autogate: `autogate` (on `review.submitted`, `check.completed`, `verdict.reversed`, `pr.ready`) reads the pull
  request live, asks `gate.run` as a dry run, and only then makes the real run inside the `merge` effect, bound to the
  head it decided about (`gate.run(expect_head=...)`) and holding the `gate` slot as the system role `onloop`. A stale
  event merges nothing. `autoready` takes a draft written by the agents App out of draft when it carries a `Ready:
  when-green` line, the gate would refuse it for nothing but the missing approval, and its classification does not
  need the human. `postmerge` resumes the post-merge checks of a merge the agents App made and the log has no record of;
  a failing check reverts through the new `revert` effect, whose `verify` looks for the `Gate-Revert` trailer on the
  base branch. Both flags default off; in observe mode every write is recorded as `observed:<kind>`.
- `Git.fetch_pr(n, base)`: the one fetch observe mode may make, into `refs/onloop/pr/<n>` and `refs/onloop/base` only.
  The gate, intake and revert read the base from `refs/onloop/base`; the revert still pushes to the real remote base
  branch. `onloop run` refuses a directory with a working tree unless `automation.allow_working_tree` says otherwise.
- `onloop run --observe-only`: a hard stop. Handlers only observe whatever the policy says, and a policy that says act
  makes the runner refuse to start.
- An onloop merge commit carries `Onloop-Merge: <event id>`. The post-merge resume acts only on a merge that has that
  trailer, names an event in this log, and was made by the agents identity (an account may be shared with other tools).
- `dispatcher.Defer`: a handler that must wait for something held by someone else (the gate slot) leaves its delivery
  pending without counting an attempt; releasing or expiring the slot wakes the waiting deliveries, first event first, and
  an unwoken one looks again after five minutes. A long wait makes no dead rows. A policy error stays a counted failure.
- The measurements: `onloop kpi` prints the three KPIs, each with its definition: lead time (median and p90, from open,
  or from ready for a draft, to merge), idle with work waiting (per role, overlaps counted once) and reverts (onloop's
  reverts over merges). `onloop acceptance --since DATE --days 7` counts A1-A3, F1, F2 and R1 from the event log and exits 1
  unless the whole window has passed and every condition holds. New events: `gate.reverted`, `runner.stopped`.
- Anchors: `onloop anchor` composes the full, short (at most 150 tokens), hard-stop and stop anchors from `onloop.yaml`
  and `ledger.yaml` only; the module cannot import the store or the GitHub client. `onloop hooks` prints the hook
  configuration that weaves them into a session and writes nothing. Roles take `leads`, `anchor` and `kpis`; the ledger
  takes `north_star`. `onloop handover` writes a note from the event log, labelled as generated.
- New events: `autogate.decided`, `autoready.decided`, `postmerge.resumed`, `gate.postmerge_checked`.

- `onloop.store`: one SQLite file in WAL mode (`$ONLOOP_DB`, else `.onloop/onloop.db`) with the event log, the intake
  snapshots, the delivery and effects tables, and the slot tables. One clock and one timestamp format, enforced by the
  database; read-only opens for every command that only reads.
- `onloop.events` and `onloop.intake`: GitHub polled into events by diffing snapshots (opened, ready, drafted, head
  moved with a patch id against the head's own merge-base, review submitted or dismissed, verdict reversed, check
  completed, merged, closed, base advanced). A conditional request makes an unchanged poll cost no rate limit, and a
  pull request whose checks are still settling is polled by its head, because check runs do not change `updated_at`.
- `onloop intake` (one poll) and `onloop events` (read-only list).
- `onloop.dispatcher`: at-least-once delivery of events to handlers with a lease, retry with backoff, dead-lettering
  after 8 attempts, per-(subject, handler) order (a later event waits for an earlier one that is pending with a
  backoff, and a dead delivery releases the block with `handler.unblocked`), and `ctx.effect`: every write to the
  outside world is keyed by event, handler and call number, so a retry does not repeat it.
- `EFFECT_KINDS`: the four writes a handler can make (comment, ready, merge, grant), each either idempotent on GitHub's
  side with the reason written down, or required to pass `verify`. An unknown kind, or a non-idempotent kind without a
  `verify`, is refused even in observe mode. `dispatcher.comment` carries a marker so a crash between the post and the
  mark does not post twice; `GitHub.comments` and `GitHub.ready` back it.
- Observe mode: `ctx.effect` records an `observed:<kind>` row and performs nothing, so an observation week fills no
  dead-letter queue. `onloop kpi --shadow` compares each observed effect with what actually happened (agreement,
  disagreement, missed, pending), lists dangerous disagreements first, and exits 1 when there is one, or when too little
  was observed to be evidence (nothing observed, fewer rows than the floor, or a control kind with no observed row).
- `onloop.runner` and `onloop run [--once]`: intake, dispatch and a heartbeat per tick; one runner at a time; the
  polling interval stretches when the rate-limit budget runs low and returns when it recovers. The runner renews its
  installation token before it expires (it keeps only the key's path), refuses to start with a reviewer key in its
  environment, refuses to start without an App identity (it never falls back to `gh`'s own login; a bare `GH_TOKEN` is
  accepted only for `--once`, after `GET /installation/repositories` proves it is an installation token), and `--takeover` is refused while the recorded process still runs on the host (every takeover is logged).
- `automation.cross_owner: reviewer_ack`: a path borrowed from another role needs, besides the `Cross-owner:` line in
  the pull request, the reviewer identity to quote that line in its standing approval of the current head. The same text
  from a user of that name, from the agents App, or on an older head, does not count, and the body is read live. An effect key reused by a
  different write raises. A replay that finds the agents' own merge records `gate.postmerge_pending`; any other merge, `gate.already_merged`.
- `onloop slot` (`need`, `request`, `wait`, `grant`, `renew`, `release`, `status`) and `slots:` in `onloop.yaml`: scarce
  things handed out by a first-come queue instead of by message. A slot has path patterns, a lease, a wait limit, an
  approver (`auto`, `human`, `role:<name>`), and the conditions and commands a holder is told, read from the policy and
  never from the request. Who someone is (`login/Type`) is parsed once, where it comes in (`onloop.identity`), and
  compared only with `same_identity`, so `agent-x/Bot` and `agent-x` are the same holder. One live grant per slot is a database index; a dead runner holds nothing, because `request` and
  `wait` expire overdue leases themselves; `wait` exits 0 granted, 3 expired, 4 timed out. Loosening a slot (removing it,
  fewer paths, a freer approver, a longer lease) is authority widening.
- `onloop preflight N [--post]` and the pre-flight handler: before the gate runs, report the form problems it would
  refuse (`P001` no authoring role, `P002` unknown role, `P003` a borrowed path without its Cross-owner line, or without
  the reviewer's quote when `cross_owner: reviewer_ack`; the reviewer and the human are matched by `onloop.identity`, so
  the case of a login does not matter) and the slots the change needs, in one comment per pull request
  that is edited in place. It also warns, apart from the refusals and never as one, when carriage-return line endings
  were added (`W004`) or a changed path has no owner in the policy (`W005`). Pre-flight is a subset of the gate and never
  softens it. `onloop init` writes a
  `onloop-preflight.yml` workflow that reads the policy from the base branch.
- In observe mode a handler's GitHub client raises on any write, so a handler that forgets `ctx.effect` fails loudly
  instead of writing.
- Refusal codes: every gate refusal carries a stable code (`G0xx` substance, `P00x` form); `gate.run(record=...)`
  reports real decisions (`gate.refused` with its codes, `gate.merged`) to the event log. The audit names the codes of a
  merge the gate should not have made.
- `automation:` in `onloop.yaml` (`mode`, `intake_seconds`, `autogate`, `autoready`, `cross_owner`), validated, with
  loosening rules: turning on the autogate or autoready, moving from observe to act, or moving `cross_owner` from
  `reviewer_ack` back to `author` is authority widening.
- `tools/mutation_check.py`: applies each mutation in its table, runs the tests that must fail, and fails if one passes.

### Changed

- `onloop events` lists the overlay ledger's events (`--ledger`), with the filters `/api/events` takes: `--kind`,
  `--subject`, `--source`, `--actor`, `--since` and `--until` (the ledger's own time), `--after SEQ` and `--limit`.
- `/api/events` takes the same filters, and `latest=1&before=N` for the newest first, paged by `next_before`. A
  filter that is not valid is a 400. The plain `?after=N` page is unchanged.
- An imported work-graph node keeps its parent (`spawned_by`) as the item's `parent_id`, and a topic is marked, so
  Flow can find each item's direction.
- `onloop kpi` counts lead time, idle time and reverts from the overlay ledger's events (`--ledger`), where the
  GitHub ingest, the legacy import and the slots write them.
- The slots move onto the overlay ledger (ledger schema version 4): `onloop slot` writes `slot.*` events whose
  projections are the request queue and the grants, with the one-live-grant-per-seat rule kept as the ledger's unique
  index. The commands take `--ledger` in place of `--db` (which is refused with a pointer). Slots held in an older
  `onloop.db` are not carried over: release them before upgrading.
- The package lives in `src/onloop/` (decision 0005, accepted). A source checkout must be installed (`pip install -e .`)
  to import it, and CI runs the tests against a non-editable install.
- Nothing in the package reads or writes the legacy stores (`onloop.db` and the work graph's) any more; decision
  0006's checklist is closed. `onloop overlay import` reads each once, read-only, as the upgrade path.
- `onloop gate` records `gate.merged`, `gate.refused`, `gate.postmerge_checked` and `gate.reverted` in the overlay
  ledger, under the new event source `gate` (ledger schema 5, migrated on first open). Its decisions, exit codes,
  refusal codes and output are unchanged; without a ledger, or when an append fails, it prints one warning line.
- `onloop kpi`: a window with merges and no gate decision in the ledger reports the reverts as unknown, not 0.
- Direction (decision 0004, docs/decisions/0004-onloop-hosts-the-conversation.md): onloop becomes the operations
  layer for a team of AI agents, a loop owner that hosts the role sessions itself (`claude -p --resume`). 0.3.0
  observes and 0.4.0 acts; the release candidates cut before this decision (0.3.0rc1, 0.4.0rc1) are not published.
- Role steps run as CI jobs (decision 0003, docs/decisions/0003-roles-run-as-ci-jobs.md). A Temporal execution layer
  for the supervisor was built on this line, superseded by that decision and removed before any release, with its
  deploy files, its `temporal:` configuration block, `onloop supervise` without `--once`, `onloop digest --query` and
  the `temporal` extra.

### Removed

- The workbench (`onloop workbench build|check|serve`) and records (`onloop record`, `onloop kpi --record`). The first
  customer confirmed neither is used; Now, Team and Audit cover what the workbench showed. The policy's `workbench:`
  block is still accepted, and only its `indent_sensitive` is still read.
- The work graph's store and its panel (`onloop workgraph sync|replay|status`, `onloop panel`), and onloopd's
  per-tick read of a work graph (`onloop daemon run --workgraph`). Flow reads the ledger instead. The node model and
  the GitHub-issues mapping the supervisor uses move into `onloop.supervisor`.
- The static dashboard (`onloop dashboard`), the demo site (`onloop demo`) and the workflow that published the demo
  to GitHub Pages. The UI replaces them; `examples/minimal` stays as the example configuration.
- `onloop.store`, the module behind `onloop.db`: nothing reads or writes that file any more.
- `onloop watchdog --db`, which read handler deliveries nothing has written since the dispatcher was retired
  (`automation.max_defer_hours` is still accepted, not read), and onloopd's per-tick read of `onloop.db`
  (`onloop daemon run --store`). `onloop overlay import` is the upgrade path.
- `onloop intake` and `onloop handover`. Intake no longer writes `onloop.db`: its pure diff (`intake.compute`) feeds
  onloopd's GitHub ingest, which writes the overlay ledger (`onloop ingest github --once` reads once by hand). The
  handover note read the dispatcher's deliveries and the slot tables; the observe-only report and the constraint view
  take its place. The agents identity may run `onloop ingest` in place of `run` and `intake`.
- `onloop run` and the act-mode loop behind it: the dispatcher, the handlers (autogate, autoready, post-merge,
  pre-flight) and the runner, with `onloop kpi --shadow`, the shadow comparison over the dispatcher's observed
  effects. onloopd (decision 0008) observes instead, and its conductor acts in a later release. `onloop preflight N`
  and the slots stay. No team used the loop (0.1.0 and 0.2.0 shipped it).
- `onloop acceptance` and its module: the act-mode week's test, superseded by the observe-only report
  (`onloop report observe`) and the weekly KPIs (`onloop kpi weekly`). No team used it (0.1.0 and 0.2.0 shipped it).

### Fixed

- `onloop kpi --shadow` takes `--now`, so a report can be reproduced as of a given time; the shadow-runner test that
  depended on the wall clock passes it.

## [0.2.0] - 2026-10-11

### Added

- `onloop dashboard --out DIR`: one self-contained HTML page (inline CSS and JS, no CDN, no server, no external
  request; light and dark theme; fits a phone) with six panels: the loop with a count at each stage, the policy map,
  queues by role, what waits on the human, the watchdog and recent merges with their audit verdicts. Every figure is
  computed from the policy, the ledger and, with `--github`, the pull requests and the audit history; with no GitHub
  data a figure reads `n/a`, never zero.
- `onloop demo --out DIR`: the demo site, built from `examples/minimal` by running the real classifier, gate and audit
  on made-up pull requests and a made-up history: a dashboard, and a "how it works" page with the exact output for six
  scripted pull requests (routine, claims, claims approved by the human, a policy loosening, a stale-head approval, a
  red check behind a green one).
- `.github/workflows/pages.yml`: builds the demo and deploys it with `actions/deploy-pages`. The repository owner
  enables Pages once.
- `audit.history()`: one record per commit (`kind`, `ok`, `why`, pull request, title) with an optional window;
  `audit.audit()` is now its failures. A revert is judged against the merge it names even when the window starts after
  that merge.
- `GitHub.pr_files()` and `classify.summary_lines()`.
- `examples/minimal/ledger.yaml` is richer: three workstreams (one blocked), two decisions, two items per queue.
- The starter workflows' default install spec is now derived from the package version, so a release cannot ship a
  workflow that installs an older one; a test pins it.

## [0.1.0] - 2026-10-10

First release.

### Added

- `onloop.yaml`, the policy: three identities (`human`, `reviewer`, `agents`), roles, review tiers, a `paths` table
  mapping each path to an owner role, a tier and a reserved class, project `checks`, `required_checks`,
  `post_merge_checks`, `audit.since` and `apps`. `onloop policy check` validates it, including that the policy
  reserves itself.
- Six built-in reserved classes (`claims`, `safety_widening`, `authority_widening`, `destructive`,
  `money_accounts`, `leaves_project`), each with an approver of `human` or `reviewer`, and user-defined classes.
- `onloop init --install-spec`: what the starter workflows `pip install`; the default is the tagged release on GitHub, since there is no PyPI release.
- `onloop codeowners`: CODEOWNERS generated from the policy, with `--check` and `--write`.
- `onloop classify`: computes the reserved classes touched, the approvers required, the tier and the owners from a
  change's own diff or from a list of paths. Compares the base policy with the head policy and classes any
  relaxation as authority widening. Output as text or `--json`.
- `onloop gate`: merges a pull request only when every condition holds on the exact head SHA (reviewer verdict,
  human approval when required, green required checks, ownership of the changed paths by the authoring role, the
  project's checks and `onloop.checks` plugins), merges with `--match-head-commit`, runs `post_merge_checks` and
  reverts the merge when one fails. `--dry-run` decides without merging.
- `onloop audit`: re-judges every merge on the main branch against the policy at its own first parent, and alarms on
  direct pushes, unexplained merges, merges of something other than the head, merges by the wrong identity, and
  merges whose evidence does not hold. Accepts verified gate reverts.
- `onloop as-app`: runs one `git` or `gh` command as a GitHub App installation with a short-lived token held only in
  that command's environment, with guards against printing or swapping the credential and a restricted command set
  for the reviewer identity.
- `onloop ledger`: `check` (including an append-only changelog check against a base ref), `render` as Markdown or
  HTML, and `next` to pull a role's next item.
- `onloop watchdog`: reports items stuck past their limit, roles with an empty queue, and pull requests waiting for a
  reviewer verdict.
- `onloop init`: writes a starter policy, four role charters, a decision-record template with "decisions by size",
  the ledger, CODEOWNERS and two workflows (gate checks and main audit).
- `onloop selftest`: runs a self-test for every checker proving that it still catches what it should.
- Documentation: README, concepts, configuration reference, security model and FAQ under `docs/`, and a validating
  `examples/minimal/` configuration.
- Apache-2.0 licence, `NOTICE`, `SECURITY.md` and `CONTRIBUTING.md`.

### Not included

- An event-driven dispatcher that launches headless agents from GitHub events.
- A team dashboard.
- A PyPI release.
