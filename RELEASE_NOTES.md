# onloop 0.3.0

onloop is a toolkit for running a team of AI coding agents on a GitHub repository with a human **on** the loop rather
than **in** it. The agents open, review and merge pull requests themselves; the human approves only a short list of
reserved classes and can overturn the rest.

**0.3.0 observes.** It adds onloopd, a resident service on the loopback address. onloopd keeps one ledger of what the
team does: the roles' turns, the scheduled jobs' results and the repository's pull requests. It shows where work
waits, and records what onloop *would* do. It acts on nothing: delivery into the role sessions comes in 0.4.0.

## Install

```bash
pip install "onloop @ git+https://github.com/Zhihua-Zhong/onloop@v0.3.0"
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
