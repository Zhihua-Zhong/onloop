# onloop 0.6.0

onloop is a toolkit for running a team of AI coding agents on a GitHub repository with a person **on** the loop
rather than **in** it. The agents open, review and merge pull requests themselves, and the person approves a short
list of reserved changes and can overturn the rest.

**0.6.0 shows who did what, how long it took and what it cost.** The Replay view draws the team's work over time, one
lane per role, from Claude Code's own telemetry, and charges each model request's tokens to the step it was spent on.
Nothing acts that 0.5.0 left off.

## Install

```bash
pip install "onloop @ https://github.com/Zhihua-Zhong/onloop/releases/download/v0.6.0/onloop-0.6.0-py3-none-any.whl"
onloop selftest
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790/#/replay
```

You need Python 3.11 or later, git, and the GitHub CLI for the commands that talk to GitHub. The page ships inside the
wheel. A source checkout builds it first, with `cd web && npm ci && npm run build`. Each file of the release can be
checked with `gh attestation verify` ([docs/release.md](docs/release.md)).

## Upgrading from 0.5

- **Back up your ledger file first.** The first time 0.6.0 opens a 0.5 ledger to write, it moves it to schema 10. It
  keeps every event, and 0.5 cannot open the file afterwards.
- **Flow and Activity are retired.** Their old links open the map and Replay; `GET /api/flow` and `GET /api/activity`
  answer 404.
- **A role that names `user` in `setting_sources` is refused:** remove it. Supervisor steps no longer read your
  settings files.
- **Start your own sessions with `onloop open <role>`** to see them in Replay.

## What is new

- **The Replay view** (`#/replay`): one lane per role over an hour, 6 hours, a day or a week, with a cursor, playback,
  and "Where the time went" ([docs/replay.md](docs/replay.md)).
- **Tokens by step:** each model request is charged to the step recorded on its session, else by rule E to the next
  `Node:` commit on its branch, else "between steps", never to the nearest step.
- **Telemetry, cross-checked:** onloopd receives Claude Code's own telemetry, drops its identity attributes, and
  compares each carried run's own token count with what arrived.
- **What a step produced**, as of the cursor, and **your wait** on Now, the map and Replay as one count.
- **Releases built and attested by CI**, with every GitHub Action pinned and the dependencies audited.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Known limits

- **Only sessions onloop starts are measured.** A session started any other way sends onloopd nothing.
- **Rule E places nothing on a shared branch:** when several roles worked on a branch since its last `Node:` commit,
  their requests stay between steps. Give each role its own branch, or name the step with `--step`.
- **A turn whose start was not recorded** counts no working time; it shows as a mark at its end.
- **Products are read for node and branch steps only.**
- **The browser tests have a CI job (`e2e`) from this release.** The pages were also checked by hand in a built-in
  browser.
- **The session carrier** is as in 0.4.0: off by default.

# onloop 0.5.0

onloop is a toolkit for running a team of AI coding agents on a GitHub repository with a person **on** the loop
rather than **in** it. The agents open, review and merge pull requests themselves, and the person approves a short
list of reserved changes and can overturn the rest.

**0.5.0 shows where the work stands at a glance.** The map answers one question: where is the work stuck, and is it
me? Nothing acts that 0.4.0 left off.

## Install

```bash
pip install "onloop @ https://github.com/Zhihua-Zhong/onloop/releases/download/v0.5.0/onloop-0.5.0-py3-none-any.whl"
onloop selftest
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790/#/map
```

You need Python 3.11 or later, git, and the GitHub CLI for the commands that talk to GitHub. The page ships inside the
wheel. A source checkout builds it first, with `cd web && npm ci && npm run build`.

## Upgrading from 0.4

- **Back up your ledger file first.** The first time 0.5.0 opens a 0.4 ledger, it moves it to a newer format (from
  schema 7 to schema 9). It keeps every event, and 0.4 cannot open the file afterwards.
- **A team's daily export** may be any contract from 1.0 to 1.2. 1.1 adds check runs, verdicts and waits; 1.2 adds
  the role that opened each change. An older export still imports; the map says what it cannot see.
- Your onloopd.yaml keeps working. The new `overview: stuck:` block, the map's thresholds, is optional.

## What is new

- **The map** (`#/map`): one picture with levels.
  - **Zoom 0** shows one block per goal (each checklist, each feedback batch, and the changes in flight), with the
    worst colour of its children and, when red or amber, which child and why.
  - **Zoom 1** shows a goal's stages.
  - **Zoom 2** shows the goal's stage × role grid, who holds what in every stage at once, with the bottleneck
    marked, and the items of one holder below it.
  - Every level counts what Now and the task list count.
- **Idle work is set apart.** An open change with no move for a week is idle, parked or abandoned. It is drawn
  hatched, and never counted as jammed.
- **The data contract:** `onloop import snapshot` replays a team's daily export into the ledger, read-only, and checks
  every record against the schemas and the privacy rules.
- **Feedback from both channels:** `onloop feedback` gives a batch in one sitting, and a team's export reports its
  own, each item at the stage the team declares.
- **The Activity view:** one lane per role, from the data's start to its end, with each role's longest quiet and
  turns per hour.
- **A page that says when it is live:** a ticking clock, ages that rise with it, and a Live dot that turns amber when
  onloopd brings no news and red when it does not answer.

## Known limits

- **Who holds a change that waits on nobody** depends on the export. With contract 1.2's `by`, the change is held
  by its role; without `by`, or with `by: team`, it is held by "the team".
- **Zoom 3 and 4** (a role's turns, a turn's tool calls) wait for the tool events.
- **The idle threshold is fixed at 7 days,** and only the changes in flight have an idle split.
- **The grid's flow numbers** ("N reached it in the week") are counted for changes only.
- **Merged by role** needs contract 1.2's `by`. Without it, every merged change is the team's.
- **Waits on you compete with a jam.** An old wait on the person can read as a candidate for "most stuck" beside a
  jam. The tally sets the jam apart; whether an old wait on you should rank with the jams is open.
- **Wide screens:** the blocks share the width up to 760 px each.
- **The browser tests were not run for this release.** The pages were checked by hand in a built-in browser and in
  Chrome, and by their API tests.
- **The session carrier** is as in 0.4.0: off by default, and the 0.4.0 notes below still apply.

# onloop 0.4.0

onloop is a toolkit for running a team of AI coding agents on a GitHub repository with a person **on** the loop
rather than **in** it. The agents open, review and merge pull requests themselves, and the person approves a short
list of reserved changes and can overturn the rest.

**0.4.0 can act, and every act is off until you switch it on.** Without a `carrier:` block in onloopd.yaml, onloop
observes and records, as 0.3 did.

## Install

```bash
pip install "onloop @ https://github.com/Zhihua-Zhong/onloop/releases/download/v0.4.0/onloop-0.4.0-py3-none-any.whl"
onloop selftest
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790
```

You need Python 3.11 or later, git, and the GitHub CLI for the commands that talk to GitHub. The page ships inside the
wheel. A source checkout builds it first, with `cd web && npm ci && npm run build`.

## Upgrading from 0.3

- **Back up your ledger file first.** The first time 0.4.0 opens a 0.3 ledger, it moves it to a newer format (from
  schema 5 to schema 7). It keeps every event, and 0.3 cannot open the file afterwards.
- Every outcome line 0.3 accepted means the same, except a placeholder waker `pid:0`, which is now refused. Write the
  new keys of the outcome line only once everything that reads your ledger runs 0.4.0.
- Your onloopd.yaml keeps working. The new blocks (`carrier:`, `kpi:`, `adapters:`, `goals:` and `jobs:`) are all
  optional.

## What is new

- **The person's task list.** Everything that waits on you is one list, in one order: weight first, then how soon the
  delay starts to cost, then age. Each ask says what it changes, which goal it moves and what waits on it. A task is
  never dropped: it closes only with a resolution and its evidence. It goes back to whoever asked after 7 days, and
  to the coordinator after 14. A "since you were away" line says what changed since your last visit.
- **Goals and outcome measures.** A goal checklist is read from its edit history. Progress is shown as goal items
  done per merged change, with the checklist's size and the date of it, so splitting work into more pieces shows as
  a cost, not as progress.
- **The session carrier** (off by default). onloop can deliver messages into your team's role sessions itself, one
  session at a time, and it records what each turn did. Until a role opts in and the carrier is started, it only
  records what it would have sent.
- **The Conversation view**, where you can read the carried roles' threads and send them a message.
- **Live pages that keep up.** A page refreshes at least every 2 seconds while events keep arriving.

The full list is in [CHANGELOG.md](CHANGELOG.md).

## Known limits

- **The session carrier is off by default, and you switch it on role by role.** Nothing is delivered without a
  `carrier:` block in onloopd.yaml, a role's opt-in, and `onloop carrier start`.
- **It drives your own, unmodified `claude` command-line tool, under your own login.** onloop never asks for, stores
  or passes on a Claude token, and it refuses to run if a metered API key is set.
- **Check your use with Anthropic.** Anthropic's terms say that the advertised usage limits of its plans assume
  ordinary, individual use of Claude Code. A team that runs many roles around the clock should confirm with Anthropic
  that its use fits its plan before it turns the carrier on.
- **The browser tests were not run for this release.** They need a browser we have stopped using while an account
  issue is investigated. The pages were checked by hand in a separate browser, and by their API tests.
- **Some readers are not live yet:** the GitHub goal reader (a checklist's edit history) and the GitHub checks behind
  waits on outside events (a CI run, checks, a review) wait for a test repository. Until then, goals are read through
  other adapters, and a wait on a GitHub event reads "unknown".
- The batching merge queue is not in 0.4.0.

# onloop 0.3.1

0.3.1 is a patch release of 0.3.0. On Linux, a process that has exited but is not yet reaped (a zombie) no longer
counts as running, so `onloop daemon stop` reports a clean stop, and the dead-waker probe sees a dead `pid:` waker. CI
now also runs on release branches and tags. Nothing else changed; the notes for 0.3.0 follow.

# onloop 0.3.0

onloop is a toolkit for running a team of AI coding agents on a GitHub repository with a human **on** the loop rather
than **in** it. The agents open, review and merge pull requests themselves; the human approves only a short list of
reserved classes and can overturn the rest.

**0.3.0 observes.** It adds onloopd, a resident service on the loopback address. onloopd keeps one ledger of what the
team does: the roles' turns, the scheduled jobs' results and the repository's pull requests. It shows where work
waits, and records what onloop *would* do. It acts on nothing: delivery into the role sessions comes in 0.4.0.

## Install

```bash
pip install "onloop @ https://github.com/Zhihua-Zhong/onloop/releases/download/v0.3.1/onloop-0.3.1-py3-none-any.whl"
onloop selftest
onloop daemon run --ledger .onloop/ledger.db      # then open http://localhost:8790
```

Python 3.11 or later, git, and the GitHub CLI for the commands that talk to GitHub. The UI ships inside the wheel. A
source checkout builds it first with `cd web && npm ci && npm run build`.

## What is in 0.3.0

- **onloopd and the overlay ledger** (decisions 0006 and 0008). The ledger is one SQLite file with an append-only event
  log and projections that `onloop overlay replay` rebuilds byte for byte. onloopd:
  - binds the loopback address only;
  - serves read-only JSON and an event stream;
  - takes writes only with its token.
- **The role sessions' hooks** (decision 0007). Each turn ends with an outcome line (`done`, `idle`, `in_review`,
  `blocked`, `handoff` or `continue`). `onloop daemon hook` posts every Stop to onloopd, which records the turn, its
  outcome or why it has none, and what it waits on. It never stores the message text.
- **Detectors, observe-only:**
  - a turn without a valid outcome line;
  - a `continue` whose waker is dead;
  - a scheduled job that failed or did not run.

  Each finding is one alert, and the action onloop would take is logged as `would.*`. `onloop report observe` sets
  each one next to what happened afterwards.
- **Scheduled jobs and the dead-man switch.** `onloopd.yaml` declares the jobs onloopd watches; they report by ping or
  through a Healthchecks webhook. `heartbeat_schedule` names onloop's own heartbeat.
- **The GitHub ingest.** onloopd, or `onloop ingest github --once`, reads a repository into the ledger, by GETs only.
- **The UI** (decision 0009): five read-only views of the ledger, served by onloopd at `http://localhost:8790`.
  - **Now:** the constraint, what waits on you, what stalled, health.
  - **Flow:** the canvas, with the constraint overlay.
  - **Team.**
  - **Schedules.**
  - **Audit.**
- **The supervisor** (decision 0003). `onloop step` runs one bounded headless `claude -p` step per CI job, dry run by
  default; `onloop supervise --once`, `onloop digest` and `onloop attention` go with it.
- **The merge gate records its decisions in the ledger**, and `onloop kpi` counts lead time, idle time with work
  waiting, and reverts from it.

## Upgrading from 0.2.0

- Run `onloop overlay import --store .onloop/onloop.db` (and `--workgraph FILE` if you used the work graph) once. It
  reads the old stores only, by cursor, so running it again adds nothing.
- `onloop events` reads the ledger: `--db` is now `--ledger`, and `--since` is a time.
- The slots move onto the ledger (`--ledger` in place of `--db`). Release any slot held in `onloop.db` first.
- A source checkout must be installed (`pip install -e .`): the package lives in `src/onloop/`.

## Removed

These are removed, each superseded by the ledger, onloopd and the UI:
- the act-mode loop (`onloop run`, the dispatcher and its handlers, `onloop intake`, `onloop handover`, `onloop
  acceptance`, `onloop kpi --shadow`);
- the workbench and records;
- the work graph's store and panel;
- the static dashboard and the demo site;
- `onloop.db`.

No team used the act-mode loop. See CHANGELOG.md for the full list.

## Status

Alpha. The interfaces may change before 1.0.

## Known limits

- onloopd observes only. Delivery into the role sessions, the corrective wake and the Conversation view are 0.4.0.
- Use merge commits only: the gate merges with `--merge`, and the audit expects a two-parent merge.
- `as-app` guards stop accidents, not malice. Limit what the App may do with its permissions and a repository
  ruleset.
- onloopd's write token keeps out other programs and web pages, not agents that run as the same user (decision
  0008). What it receives is evidence, never authority.
- No PyPI release.

Documentation is in the repository: `README.md`, `docs/` and `examples/minimal/`. Licence: Apache-2.0.
