# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

The session carrier (decision 0010) and the Conversation view (decision 0011) are meant for 0.4.0.
Act modes are off by default.

### Upgrading

- Ledger schema 6: a ledger opened by this onloop migrates once and keeps every event; an older onloop then refuses
  it. Nothing in it changes until onloopd.yaml has a `carrier:` block.
- The UI is no longer GET-only: the Conversation view's compose box posts `/ui/messages`, with the page's session
  cookie, a CSRF token and an Origin check. Nothing else writes.

### Added

- **The session carrier** ([carrier.md](docs/carrier.md), decision 0010). onloop delivers messages into the role
  sessions itself, with headless `claude -p --resume`, on the user's own subscription login:
  - one live lease per session id, a unique index in the ledger; a session open in another process is never resumed
    (`claude agents --json`, filtered to the carried sessions in one function; nothing else from it is kept);
  - one process and one composed message per run, in a Windows job object, `--restricted`, `dontAsk`, no prompts,
    and the role's tools, never a shell (a role that names one is refused);
  - a delivery is acknowledged by the role's Stop-hook turn, whatever it said; a missing outcome line is recorded on
    the turn and starts the corrective only for a role that opted in;
  - the corrective wake at most once per turn, then the coordinator with the item's age; handoffs and review requests
    to a carried role; a dead waker, to the coordinator first;
  - observe-only by default (`would.deliver`, with the exact command and text);
  - the kill switch (`onloop carrier stop`, or the `carrier.off` file), budgets per role and across roles, the
    metered-key refusal, the login check, and the usage limit: a rejection pauses until its reset, a warning halves
    the rate, overage pauses at once;
  - recovery after a crash (the lease released once, a delivery acknowledged by evidence or resent with a mark), a
    hook spool across a respawn, and sleep (a corrective for a turn older than an hour escalates instead).
- `onloop carrier start|stop|status`, `onloop role opt-in|opt-out`, `onloop say`, and the `carrier:` block of
  onloopd.yaml.
- The **Conversation** view: the carried roles with unread counts and what needs you, one role's thread (your
  messages and how far each got, onloop's notes, what it would send, runs, turns by their outcome line), and a compose
  box that is open only when a message can go somewhere, and otherwise says why, with the command that opens it.
  `GET /api/conversation`.
- The text onloop writes into a session is stored, scrubbed of secret-shaped strings, for 30 days, then only its
  hash. Nothing a role writes is stored.

### Changed

- Now's stall card and Team's lane say what the carrier did about a turn without an outcome line ("onloop asked for
  its outcome line (being processed)"); "would wake (observe only)" only where it did nothing.
- `onloop daemon hook` spools a post it cannot deliver to `hook-spool.jsonl`; onloopd logs it once when it is back.

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
