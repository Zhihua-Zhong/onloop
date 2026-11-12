# The loop without waiting: build plan

*Status: plan, 2026-10-12, for review. It turns section 6 of [architecture.md](architecture.md) into modules, a
schema, contracts and tests. Nothing here is built yet. A step is done when its test exists and has been shown to
fail under the mutation listed for it (section 9).*

## 1. Goal and the two numbers

0.3 is done when one real project runs a week in which **no slot is handed out by a person** and **no pull request is
refused for form**, and the event log can show both (section 10). Everything else in 0.3 serves those two numbers:

- the waiting came from slots handed out by message, and from a clean verdict taking several messages to reach the
  gate;
- the form refusals came from rules the gate enforces and the author finds out about only when the gate says no.

What 0.3 does **not** do: no workbench, no canvas, no copilot, no webhook, no Temporal, no multi-host, no drift
detector (0.5), no merge train (0.5). Section 11 lists what I would cut first if time runs short.

## 2. Principles carried into the design

1. **GitHub is the source of truth; the SQLite file is a cache, except for slots.** Events, snapshots, deliveries and
   KPIs can be rebuilt from GitHub by re-running intake. The lease and grant tables are authoritative: they say who
   holds a scarce thing now, and nothing on GitHub does. They are append-only in effect (a grant is closed, never
   edited) and every change also writes an event.
2. **An event is a hint, never evidence.** A handler that acts re-reads the live facts from GitHub and decides on
   them. The autogate never merges because an event said "approved"; it calls the gate, which reads the pull request
   again and binds to the head. A forged or replayed event can at worst cause a wasted look.
3. **One rule table.** The gate and the audit already share `gate.decide`. Pre-flight reuses the same functions, so a
   form rule cannot exist in one place and not the other. A test pins that the audit imports `decide` and that every
   pre-flight code maps to a gate refusal code.
4. **Every new field that grants authority is covered by the loosening rule** (section 7). A policy change that weakens
   a slot approver, narrows a slot's paths, turns on auto-gate or relaxes Cross-owner goes to the human, whatever file
   it lives in.
5. **Observe before act.** `automation.mode: observe` runs intake, pre-flight and the KPIs and writes events, but
   performs no GitHub write. It records what it would have done (an `effects` row of kind `observed:<kind>`), so the
   first week on a real project, which is observe mode, gives the baseline for section 10. Observe mode also
   *compares*: next to each observed effect it records what actually happened on the same subject (section 10).

5a. **One identity form.** Who someone is (a login and a type) is parsed once, where it comes in, stored in its canonical form,
   and compared only with `identity.same_identity`. Comparing two strings with `==` is how a self-grant check gets
   bypassed (a holder `agent-x/Bot`, a granter `agent-x`). Slots use it for holders and granters; M4's `reviewer_ack`
   and M5's autogate compare actors the same way.

## 3. Module list

All in package `onloop/`. Small files, one job each. `*` marks a change to an existing module.

| Module | Job | Depends on |
|---|---|---|
| `store.py` | Open the SQLite file (WAL), migrate by `user_version`, one clock, `append_event`, `transaction()` helper | stdlib `sqlite3` |
| `events.py` | Event kinds, natural keys, the id function, payload schemas as small dataclasses | |
| `intake.py` | Poll GitHub, diff against stored snapshots, append events | `github.py*`, `store`, `events` |
| `dispatcher.py` | At-least-once delivery, the effects ledger, retry and dead-lettering | `store`, `events` |
| `handlers/` | One file per handler: `preflight.py`, `autogate.py`, `autoready.py`, `slotwake.py`, `kpi_tick.py` | `gate*`, `slots`, `preflight` |
| `slots.py` | Slot definitions from policy, the queue, grants, leases, path scoping | `store`, `policy*` |
| `preflight.py` | Pure form rules over a pull request's facts and files; codes `P001`... | `gate*`, `classify` |
| `anchor.py` | Compose the full, short and hard-stop anchors from files; token budget | `ledger`, `policy*` |
| `hooks.py` | Print the hook configuration that weaves anchors into an interactive session | `anchor` |
| `kpi.py` | Three KPI queries over the event log, the acceptance query, and the shadow comparison (`onloop kpi --shadow`) | `store` |
| `handover.py` | A generated, labelled handover note from the log | `store`, `kpi` |
| `runner.py` | The loop: one tick = intake, dispatch, lease expiry, heartbeat; single-instance lock | all of the above |
| `gate.py*` | Refusal codes (`Refusal(code, text)`); emit events; reviewer-quoted Cross-owner (opt-in); skip the post-merge fetch when `post_merge_checks` is empty | |
| `audit.py*` | Same codes in its reasons | `gate` |
| `policy.py*` | New fields (section 7) and their loosening rules | |
| `cli.py*` | `run`, `events`, `slot`, `preflight`, `anchor`, `hooks`, `kpi`, `handover`, `acceptance` | |

New dependencies: none. SQLite is in the standard library. `gh` stays the only network client.

## 4. SQLite schema (`.onloop/onloop.db`, git-ignored)

The path is `$ONLOOP_DB`, else `.onloop/onloop.db` in the repository's top level. `onloop init` adds it to
`.gitignore`. `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`. The schema version is
`PRAGMA user_version`; a migration is a numbered function, applied in one transaction, never edited afterwards.

```sql
-- the append-only log. `id` makes appending idempotent; `seq` is the order everyone reads.
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,            -- sha256(repo|kind|subject|natural_key), hex
  repo        TEXT NOT NULL,
  kind        TEXT NOT NULL,                   -- 'pr.opened', 'review.submitted', 'slot.granted', ...
  subject     TEXT NOT NULL,                   -- 'pr#12', 'slot:db', 'main'
  actor       TEXT,                            -- login and type, 'octocat/User'; 'onloop' for ours
  at          TEXT NOT NULL,                   -- when it happened, if GitHub says; else observed_at
  observed_at TEXT NOT NULL,
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
CREATE INDEX events_kind    ON events(kind, seq);
CREATE INDEX events_subject ON events(subject, seq);

-- where intake stopped, per source: an ETag for a conditional request, or a last-seen id
CREATE TABLE cursors (name TEXT PRIMARY KEY, value TEXT NOT NULL, etag TEXT, updated_at TEXT NOT NULL);

-- what intake saw last, per open pull request: the diff of this against the live facts is the event stream
CREATE TABLE pr_snapshots (
  number INTEGER PRIMARY KEY, head TEXT NOT NULL, state TEXT NOT NULL, draft INTEGER NOT NULL,
  ready_count INTEGER NOT NULL DEFAULT 0, drafted_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT, reviews_hash TEXT NOT NULL, checks_hash TEXT NOT NULL, patch_id TEXT,
  facts TEXT NOT NULL CHECK (json_valid(facts)), head_seen_at TEXT NOT NULL, seen_at TEXT NOT NULL
);

-- delivery of one event to one handler
CREATE TABLE deliveries (
  event_id TEXT NOT NULL REFERENCES events(id), handler TEXT NOT NULL,
  status   TEXT NOT NULL CHECK (status IN ('pending','running','done','dead')) DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL, claimed_until TEXT, last_error TEXT,
  PRIMARY KEY (event_id, handler)
);
CREATE INDEX deliveries_due ON deliveries(status, next_at);

-- every write onloop makes to the outside world, keyed so that a retry finds it and does not repeat it
CREATE TABLE effects (
  key TEXT PRIMARY KEY,                        -- '<event_id>:<handler>:<n>'
  event_id TEXT NOT NULL, handler TEXT NOT NULL, kind TEXT NOT NULL,   -- 'comment','ready','merge'
  detail TEXT NOT NULL, at TEXT NOT NULL
);

-- slots: the queue and the grants. `slots` themselves live in onloop.yaml, not here.
CREATE TABLE slot_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, holder TEXT NOT NULL, role TEXT NOT NULL,
  reason TEXT NOT NULL, ref TEXT NOT NULL,     -- 'pr#12' or a free-text ticket id
  paths TEXT NOT NULL CHECK (json_valid(paths)),
  requested_at TEXT NOT NULL, wait_until TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('waiting','granted','cancelled','expired')) DEFAULT 'waiting'
);
CREATE UNIQUE INDEX one_waiting_request ON slot_requests(slot, holder, ref) WHERE status = 'waiting';

CREATE TABLE grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT NOT NULL, holder TEXT NOT NULL, role TEXT NOT NULL,
  request_id INTEGER NOT NULL REFERENCES slot_requests(id),
  granted_at TEXT NOT NULL, granted_by TEXT NOT NULL,     -- 'onloop:auto' or 'login/User' for a person
  expires_at TEXT NOT NULL, renewed_at TEXT, released_at TEXT, release_reason TEXT,
  conditions TEXT NOT NULL CHECK (json_valid(conditions)),
  commands   TEXT NOT NULL CHECK (json_valid(commands))
);
CREATE UNIQUE INDEX one_live_grant ON grants(slot) WHERE released_at IS NULL;   -- capacity 1

-- one runner at a time
CREATE TABLE runner (id INTEGER PRIMARY KEY CHECK (id = 1), pid INTEGER, host TEXT,
                     started_at TEXT, heartbeat_at TEXT);
```

Notes that are easy to get wrong:

- **One clock, one format.** `onloop_now()` is a SQL function that defaults to the database's own clock,
  `strftime('%Y-%m-%dT%H:%M:%fZ','now')`; tests replace it. Every timestamp the code writes is UTC text in that one
  format, so comparing the text is comparing the time, and a test rejects any other format on every timestamp column.
  There is no Python `datetime.now()` in a comparison. The clock decides only **when a lease expires**. Double grants are
  prevented by the unique index below, not by the clock: a skewed clock can shorten or lengthen a lease, never create a
  second holder.
- **Capacity.** 0.3 has capacity 1 per slot, enforced by `one_live_grant`. A slot with capacity above 1 is a 0.4
  question.
- **Authoritative tables.** `slot_requests` and `grants` are never rebuilt from GitHub. A backup is a file copy of the
  database taken with `.backup`. `onloop slot status --json` is the reader of record.

## 5. Event intake (polling)

The server is local, so GitHub cannot call it. Intake turns polls into events by **diffing snapshots**, not by
trusting the events API:

- The repository events API lags and drops. It is used only as a hint to poll sooner, never as the record.
- Each tick (default 60 s, `automation.intake_seconds`): list open pull requests with a conditional request (ETag in
  `cursors`; a 304 costs no rate limit). For each one whose `updated_at` changed, or that has a required check still
  pending, read the facts with the existing `GitHub.pr()` and compare with `pr_snapshots`.
- **Check runs do not bump a pull request's `updated_at`.** A pull request waiting on CI must therefore be polled
  while any required check is pending, or a green check is never seen. This is the one detail that makes polling
  silently miss events if forgotten, and it has its own test.
- Closed and merged pull requests: one extra poll of the pull requests that left the open list since the last tick.
- Merges to the base: `commits` on the base branch since the cursor, so `main.advanced` follows a merge made by hand.

| Event kind | Natural key (so a re-poll appends nothing) | Payload |
|---|---|---|
| `pr.opened` | number | title, head, draft, base |
| `pr.ready` / `pr.drafted` | number, direction, **transition count** | head; the count is how many times intake has seen that direction on this pull request, kept in `pr_snapshots.ready_count` and `drafted_count` |
| `pr.head_moved` | number, new head | old, new, `substantive`, `basis` (see below) |
| `review.submitted` | review id, state | author, state, commit, body hash |
| `review.dismissed` | review id | actor, previous state |
| `verdict.reversed` | number, head, review id | from, to (an approval became a request for changes, or the reverse) |
| `check.completed` | check run id, conclusion | name, conclusion, head |
| `pr.merged` / `pr.closed` | number, merge sha or closed_at | merged_by, merge_sha |
| `main.advanced` | sha | |
| `gate.merged` / `gate.refused` | number, head, attempt time | exit code, refusals `(code, text)` |
| `slot.requested/granted/released/expired` | request or grant id, transition | holder, slot, by |
| `runner.tick` | tick time | counts; used for liveness only |

**Ready toggles.** Keyed on (number, head, direction) the second ready on one head after ready, draft, ready would be
dropped as a duplicate, and the lead-time KPI reads `pr.ready`. So the key carries a transition count, bumped in the same
transaction that stores the snapshot. A test toggles one head twice and expects two events. If the snapshot table is
lost and rebuilt, the counts restart and the first events dedupe against the log; that is acceptable for a cache.

**Head moved.** `substantive` is computed from `git patch-id --verbatim` (not `--stable`, which ignores whitespace and so cannot tell a dedent out of a Python block; needs git 2.39, and when the id is unknown the event says `substantive: true, basis: "unknown"`: it fails closed): the patch id of the diff from `old`'s own
merge-base with the base branch, against the patch id of the diff from `new`'s own merge-base. Each head uses **its own**
merge-base, because the base moves between two polls and a shared merge-base gives the wrong answer for a push that
only merges the base. The stored `patch_id` is therefore taken at the time of each snapshot, with the merge-base at that
time. The merge-only test moves the base between the two heads. Equal means the pull request's own change did not move (a merge of the base, a rebase that
changed nothing).  A rewrite of line endings rewrites every line, so it is a different patch and is recorded as substantive; saying
"only line endings" is the delta pane's job in 0.4. Unequal means the
reviewer must look again. This is what 0.4's delta pane will read; 0.3 only records it.

**Reversal.** `verdict.reversed` is appended when the latest standing verdict of an identity on a head flips. The gate
already reads the latest verdict per head; the event exists so the autogate and the KPIs can see the flip.

## 6. The dispatcher contract

Delivery is **at least once, in `seq` order per subject, handlers idempotent by event id.**

1. A handler is a function `handle(ctx, event) -> None` registered for a set of kinds, with a name. `ctx` gives it a
   store connection, a GitHub client, a git object, the policy at the base, and `ctx.effect(kind, detail, fn)`.
2. The dispatcher inserts a `deliveries` row for every (event, handler) pair at append time (same transaction).
3. A tick claims due rows with one `UPDATE ... SET status='running', claimed_until=now+lease WHERE status='pending' AND
   next_at<=now ... RETURNING`, runs the handler, then sets `done`. A crash leaves `running` with an expired
   `claimed_until`; the next tick reclaims it. That is the at-least-once.
4. **Effects are keyed.** Every write to the outside world goes through `ctx.effect(kind, detail, fn)`. It computes
   `key = event_id:handler:n` (n counts effects within the handler call), looks it up in `effects`, returns early when
   present, otherwise runs `fn()` and inserts the row. A retry after a crash between the write and the ack finds the
   row and does not comment, mark ready or merge twice. The one effect that is not idempotent by nature, the merge, is
   also head-bound on GitHub's side (`--match-head-commit`), so a replay of a stale event cannot merge a newer head.
5. Failure: `attempts += 1`, `next_at = now + min(2^attempts × 30 s, 1 h)`; after 8 attempts the row is `dead` and a
   `handler.dead` event is appended (the watchdog reads it). A dead row is never retried silently.
6. Ordering: events are delivered in `seq` order per (subject, handler). A handler is not started for event n+1 of
   `pr#12` while an earlier delivery for the same (subject, handler) is `running`, **or `pending` with a backoff**; if
   it were, a retry of n would run after n+1 and undo it. A delivery that goes `dead` releases the block, and the
   release is logged as `handler.unblocked`. Across subjects there is no order.
7. **A read never writes state.** `onloop events`, `slot status`, `kpi`, `handover` and `preflight` open the database
   read-only (`mode=ro`) except where the command's job is the write (`slot request/grant/release`, `run`).
8. **Observe mode holds below `ctx.effect` too (M3/M5).** Today observe mode is enforced only through `ctx.effect`: a
   handler that calls the GitHub client directly could still write. Handlers will therefore be given a client whose
   write methods raise in observe mode (`comment`, `ready`, `merge`, and any later write), so the rule does not rest on
   every handler remembering to use `ctx.effect`. A test registers a handler that writes directly and expects the raise.
9. **A key means one write.** `ctx.effect` compares the stored kind and details when a key is found, and raises
   `EffectKeyMismatch` if they differ, so a retry whose first effect is another write is loud, not silently skipped.
10. **A merge found on replay.** If `gate.run` finds the pull request already merged with a `merge_sha`, it records
    `gate.postmerge_pending` before refusing (G010). Resuming the post-merge checks for that merge is the autogate's job
    (M5).

## 6a. The runner's identity and its budget

- **Identity.** The runner is the project's agents App and never a person's credential. Everything it writes goes out
  as that App. A merge goes only through `gate.run` under that App.
- **The token is renewed.** An installation token lives an hour. The runner holds only the *path* of the agents App's
  key (`apps.agents.key_env`) and re-mints the token before it expires (after 45 minutes), putting it into the
  environment of the `gh` and `git` calls it makes. Started with only a preset `GH_TOKEN` it warns, because that token
  cannot be renewed.
- **No identity, no start.** `onloop run` refuses to start without the agents App's key path; it never falls back to
  `gh`'s stored login (a person's, on a shared host). A bare `GH_TOKEN` is accepted only with `--once`, after one
  `GET /installation/repositories` proves it is an installation token (nothing is guessed from its prefix).
- **The runner never holds the reviewer's key.** It refuses to start if any variable matching `*REVIEWER*KEY*`, or the
  `key_env` of a reviewer identity named in the policy, is set in its environment.
- **The gate is a slot like any other.** The autogate takes the `gate` slot through `slots.py`, with no bypass. One gate
  run at a time is a rule of the first project, and the lease is what enforces it when two events arrive together.
- **Rate budget.** Every `gh api` response carries `X-RateLimit-Remaining` and `X-RateLimit-Reset`. Intake reads them
  and stretches `intake_seconds` (doubling, up to 10 minutes) when fewer than 20% of the hour's budget remains, and
  returns to the configured interval when it recovers. A `runner.tick` event records the interval used. Test with
  `FakeGitHub` returning a falling budget: the interval grows, then recovers.

## 7. Policy additions and their loosening rules

```yaml
north_star: "One sentence the anchor opens with."     # in ledger.yaml, next to focus

roles:
  lead:     {leads: [operator, builder], anchor: every_message, kpis: [...]}   # which roles it coordinates
  builder:  {anchor: session}                                                # default

slots:
  db:
    paths: ["db/**", "pipelines/**"]       # a change touching none of these needs no slot
    timeout_minutes: 90                    # the lease; renewed by `onloop slot renew`
    wait_minutes: 240                      # a request older than this is expired and reported
    approver: auto                         # auto | role:operator | human
    conditions: ["no other writer", "dry run first"]
    commands: ["python governance/verify.py"]
  gate:
    paths: ["**"]
    timeout_minutes: 20
    approver: auto

automation:
  mode: observe                            # observe | act
  intake_seconds: 60
  autogate: false                          # when true, a clean verdict runs the gate on the head
  autoready: false
  cross_owner: author                      # author | reviewer_ack
```

`policy.loosening()` is extended, and each rule has a test that a loosening is flagged and a tightening is not:

| Change | Verdict |
|---|---|
| a slot's `approver` moves from `human` to `role:*` or `auto`, or from `role:*` to `auto` | loosening |
| a slot loses entries from `paths` (a change that needed the slot no longer does) | loosening |
| a slot is removed | loosening |
| a slot's `timeout_minutes` or `max_renewals` is raised | loosening |
| a slot's `conditions` or `commands` lose an entry or change one (adding is a tightening) | loosening |
| a slot's approver changes between two different `role:` values | loosening |
| `automation.autogate` or `autoready` goes false to true | loosening |
| `automation.mode` goes observe to act | loosening |
| `automation.cross_owner` goes `reviewer_ack` to `author` | loosening |
| a role's `leads` or `anchor` changes | not authority; no rule |

`conditions` and `commands` are read from the policy **at the base**, never from the request text, so a requester
cannot invent the commands a grant prints. Section 12 lists the risks.

## 8. Slots in detail (the largest waiting)

**Commands** (all through `slots.py`; the CLI is a thin shell):

- `onloop slot need --paths P...` prints the slots a change needs (path scoping by glob against each slot's `paths`).
  A docs-only change prints nothing and needs no host slot. This is also what `preflight` and the autogate call.
- `onloop slot request SLOT --holder H --role R --reason T --ref pr#12` appends to the queue. If the slot is free and
  the approver is `auto`, the grant is made in the same transaction (granted by `onloop:auto`). Otherwise the request
  waits; the approver's inbox line is `onloop slot grant ID` (a person or the named role).
- `onloop slot wait SLOT --holder H --ref R [--timeout S]` blocks until granted, then prints the grant: slot,
  expiry, conditions, exact commands. Exit codes: 0 granted, 3 expired, 4 timed out waiting. This is the wake-up for an
  interactive session: run it in the background and the session is woken when it exits. A headless agent calls it
  directly.
- `onloop slot renew GRANT`, `onloop slot release GRANT [--reason]`, `onloop slot status [--json]`.
- Each tick of the runner expires leases past `expires_at` (`slot.expired`, the holder is named) and grants the next
  waiting request in FIFO order.
- **A dead runner must not hold a slot.** `slot request` and `slot wait` also expire overdue leases, each in its own
  transaction, before they look at `one_live_grant`. `slot status` is a read (section 6, item 7): it opens the database
  read-only and reports the effective state, so an overdue lease shows as free, without writing the expiry down. Expiry that happened only on a runner tick would leave a
  lease past its time blocking the slot for as long as the runner is down, which is the waiting 0.3 exists to remove.
  Test: runner stopped, a lease past expiry, a new request is granted, and `slot.expired` names the old holder.

**FIFO and starvation.** One queue per slot, first request first. A request is never skipped because a later one is
cheaper. A holder that renews more than `max_renewals` (default 3) is flagged to the watchdog, not cut off.

**Wake-up honesty.** onloop cannot interrupt a session that is not running a command. The wake-up is the exit of
`slot wait`, or nothing else in 0.3: a notification file for sessions that cannot run a long command is out of scope.

**Who may grant, renew and release.** `slot grant` refuses what the API can see: a holder granting its own request; an
`approver: human` slot granted by anyone but the policy's human login; a `role:<r>` slot granted by someone who does not
state role `<r>` (`--as-role`). Only the holder renews. A release by anyone else needs `--by` and is recorded under that
identity. **A human grant in local mode is attested, not authenticated:** nothing proves who typed `--by`. It records a
decision made elsewhere (a project's database write window stays the project lead's go in chat) and does not verify it.
`max_renewals` is advisory: past it a renewal is allowed and flagged to the watchdog, because cutting a holder off in the
middle of a window is worse than the flag.

**What a person still does:** a slot whose `approver` is `human` or `role:*` still needs that identity to run
`slot grant`. The acceptance test (section 10) is therefore about slots configured `auto`; a project that keeps a
human approver on a slot has chosen to wait, and the log shows it.

## 9. Test strategy

Three layers, all offline and without a network.

1. **Pure units:** pre-flight rules, path scoping, the event id function, the KPI queries over fixture event
   streams, the loosening rules, the anchor composer.
2. **Real git, fake GitHub.** The existing pattern (`merge_world` in `tests/test_gate_merge.py`: a bare origin and two
   clones) extends to intake and the autogate: `FakeGitHub` gains a scripted timeline (a list of states over time that
   `pr()` and `open_prs()` return as a test advances its clock), including the awkward cases: a push between two polls, a
   review dismissed, a check turning red after green, a 304.
3. **Real SQLite, real processes.** A slot race with two OS processes against one file (each sleeps a random moment,
   then requests; exactly one grant). A crash test: kill the dispatcher between an effect and its ack (a hook raises
   `SystemExit` at a named point), restart, and assert one comment, one merge, one `effects` row.

**Mutation checks.** `tools/mutation_check.py` holds a table of (file, exact old text, mutation, the test that must
fail). CI runs it. A mutation that passes the suite is a failed build. The table for 0.3 begins with:

| Mutation | Must fail |
|---|---|
| `ctx.effect` skips the `effects` lookup | the crash test (two comments) |
| the lease compares `expires_at < now` the other way round | the expiry test; the race test |
| `one_live_grant` index dropped | the race test (two grants) |
| the autogate decides on the event's head instead of the live head | the stale-event test (merges a moved head) |
| `reviewer_ack` accepts a review by a `User` named like the reviewer | the identity test |
| path scoping uses `in` instead of a glob | the docs-only test |
| intake stops polling a PR with pending checks | the pending-check test |
| `patch_id` is taken from the new head only | the merge-only head-move test |
| idle time counts overlapping waits twice | the KPI overlap test |
| `loosening()` ignores `automation.autogate` | the loosening test |
| a read command opens the database read-write and appends | the read-never-writes test |
| the anchor composer appends the event payload | the anchor-from-files test |

Every milestone also keeps: ruff clean, the whole suite in a fresh short-path venv on Windows and Linux, `onloop
selftest` extended with a self-test for each new checker (slots, pre-flight, KPIs, intake), and the confidentiality
scan.

## 10. The acceptance test, defined so it can be measured

**Setup.** One real project (the project onloop was extracted from is the first), the runner on, two consecutive weeks, `automation.mode: observe` for
week 0 and `act` for week 1, every slot in the **acceptance set** `approver: auto`, every slot use going through `onloop slot`. A slot that keeps a human approver is outside the set: `onloop acceptance` lists it, and A1 and A2 exclude it. (In the first project the database write window stays `approver: human`, with the project lead.) The result is a
command, `onloop acceptance --since DATE --days 7`, that reads only the event log and prints PASS or FAIL with the
numbers. No number is typed into a report.

**Pass conditions for week 1** (all must hold):

| # | Condition | Computed from |
|---|---|---|
| A1 | `slot.granted` events, for slots in the acceptance set, with `granted_by` other than `onloop:auto`: **0** | events |
| A2 | every `slot.requested` for a slot in the acceptance set has a `slot.granted` before its `wait_until`: **0 expired requests** | events |
| A3 | the week is not vacuous: **at least 5** slot requests and **at least 20** `gate.merged` + `gate.refused` events | events |
| F1 | `gate.refused` events carrying a refusal code in the **form set**: **0** | events |
| F2 | every PR that was ever refused for form, had pre-flight run on its head before the gate: no gate refusal for form lacks a `preflight.reported` for the same head | events |
| R1 | the runner was alive: no gap between `runner.tick` events longer than 5 × `intake_seconds`, except a recorded stop | events |

The **form set** is the list of refusal codes that pre-flight also produces: `P001` no authoring role, `P002` unknown
role, `P003` Cross-owner missing or not acknowledged. The gate and pre-flight share the code table, and a test fails
if a pre-flight refusal code is not a gate code. Pre-flight also reports two **warnings**, listed apart and never counted
in F1: `W004` carriage-return line endings were added, `W005` a changed path no policy path covers. The gate does not
refuse for either; gate refusals for both are an opt-in policy field for 0.4. A refusal for substance
(a missing approval, a red check, a head that moved) is not form and does not count against F1.

**Baselines (before and after).** Week 0 in observe mode records, for the same project: how many gate refusals there
were and how many pre-flight would have flagged (that is the baseline for F1), the median and p90 time a request for
a slot waited in the old way (from the ledger changelog and the Operator's slot lines, labelled estimated, since the
old way has no events), and lead time and reverts for the prior four weeks, rebuilt from GitHub history by
`onloop kpi --backfill`. A claim of improvement names the baseline, the denominator and the week.

**Observe mode compares (shadow traffic).** Week 0 is a candidate-against-control run: onloop is the candidate, the
people and agents who act in the old way are the control. For each `observed:<kind>` effect, the comparison records
what actually happened on the same subject and who did it:

| Observed by onloop | Control: what happened on the same subject within 24 h | Reported as |
|---|---|---|
| `observed:merge` of pr#N at head H | `pr.merged` for pr#N, same head, by a person or the old process | **agreement** (with the delay: onloop would have merged earlier by X) |
| | `pr.merged` of a different head, or `pr.closed`, or nothing | **disagreement: would merge, did not** |
| | (no observed effect) but `pr.merged` happened | **missed**: onloop saw no clean verdict a person acted on |
| `observed:ready`, `observed:comment` | the same `pr.ready` or a pre-flight-relevant refusal | agreement or disagreement as above |
| `observed:grant` of slot S to holder X | a `slot.granted` by a person for the same request, or the holder acting without a request | agreement, disagreement, or **missed** |

`onloop kpi --shadow --since DATE` prints the counts for each row and lists every disagreement and every missed event
with its evidence (event ids). A disagreement of the dangerous kind, *onloop would have merged or granted what was not
allowed*, is listed first and is the number that matters for leaving observe mode.

**Leaving observe mode depends on criteria, not the calendar.** Week 0 ends, and `automation.mode` may be changed to
`act`, only when all three hold, after at least 7 days:

1. the volume of A3 was reached: at least 5 slot requests and 20 gate events;
2. zero disagreements of the dangerous kind: nothing onloop would have merged or granted that was not allowed;
3. no runner gap (R1).

If any criterion fails, observe mode continues. Changing the mode to `act` is itself a loosening, so it is a pull
request the human approves; the shadow report is attached to it.

**The three KPIs** (`kpi.py`, SQL over `events`, each with its definition in the output):

- **Lead time:** from `pr.opened` (or `pr.ready` when it started as a draft) to `pr.merged`, median and p90, over
  merged PRs in the window; the denominator is the count.
- **Idle with work waiting:** per role, the union of intervals in which something was waiting on that role: a slot
  request waiting for its approver, a verdict waiting for the reviewer (`pr.ready` or `pr.head_moved` substantive,
  until `review.submitted` on that head), a clean verdict waiting for the gate (clean to `pr.merged` or
  `gate.refused`). **Overlaps are counted once** (a mutation checks this). Output in hours.
- **Reverts:** merges followed by a revert (a `Gate-Revert:` trailer or a `Revert` commit naming the merge) over
  merges in the window, with both counts shown.

*Not a KPI:* review rounds per PR (architecture 4.3).

## 11. Milestones and the order I would cut

Small commits on local branches, one branch per milestone, the Reviewer reads each milestone, nothing is pushed, and a
public release goes to the project lead.

| M | Content | Branch |
|---|---|---|
| M1 | `store`, `events`, `intake` (snapshot diff, pending-check polling, head-moved with patch id), `onloop events`, `FakeGitHub` timeline | `m1-intake` |
| M2 | `dispatcher` with the effects ledger, `runner --once` and the loop, structured refusal codes in `gate` and `audit`, `gate.*` events, observe mode | `m2-dispatch` |
| M3 | `slots`: queue, grants, leases, path scoping, `onloop slot ...`, loosening rules for slots, the race and expiry tests | `m3-slots` |
| M4 | `preflight` (CLI and handler, one edited comment per PR), the workflow template, reviewer-quoted Cross-owner as `cross_owner: reviewer_ack`, and the carried-over fixes below | `m4-preflight` |
| M5 | `autogate` (triggers: `review.submitted`, `check.completed`, `verdict.reversed`, `pr.ready`; takes the `gate` slot) and `autoready`, `verdict.reversed`, loosening rules for `automation.*` | `m5-autogate` |
| M6 | `anchor`, `hooks`, the three KPIs, `handover`, `acceptance` | `m6-kpi` |
| M7 | docs, `onloop selftest` additions, the shadow week, the live week, the 0.3.0 release candidate | `m7-release` |

**Carried over from 0.2.0 and 0.1.1** (into M4, one commit each): the CHANGELOG full stop in the 0.2.0 entry; the
dashboard's `waiting = "checks"` label becomes `gate refuses` when `gate.decide` refuses for a reason other than
checks; the post-merge fetch is skipped when no `post_merge_checks` are configured.

**If the schedule slips, cut in this order:** (1) `handover` and `hooks install` (print-only stays); (2) `autoready`;
(3) nothing: the notification file is already out; keep `slot wait`; (4) `onloop kpi --backfill`; (5) reviewer-quoted Cross-owner
(P003 then checks only that the line exists, as today). **Never cut:** the effects ledger, the one-live-grant index,
live re-read in the autogate, the loosening rules, observe mode, and the mutation table. These are the parts whose
failure is quiet.

## 12. Review-integrity risks (for the Reviewer)

Each is a way 0.3 could lower the bar while looking like automation. For each: what I rely on, and the test.

1. **The autogate merges on a stale or forged event.** It never reads the event's facts; it calls `gate.run`, which
   re-reads and binds to the head, and GitHub refuses a merge whose head moved. Test: replay an old `review.submitted`
   after a push; assert refusal and no merge.
2. **A replay repeats an effect.** The effects ledger. Test: the crash test above.
3. **Self-grant.** A requester cannot name its own grant's commands or conditions; they come from the policy at the
   base. A grant by `onloop:auto` is only possible when the policy says `approver: auto`, and that line is
   human-reserved when loosened. Test: request with commands in the reason text; assert the grant carries the policy's.
4. **Lease theft or double grant.** The partial unique index and one SQLite clock. Test: the two-process race; the
   clock-injection test.
5. **The reviewer ack is forged.** `reviewer_ack` counts only a review by the reviewer identity (Bot, the policy's
   login), on the current head, whose body contains the line. Test: the same text from a `User`, from the agents App,
   and on an older head, all refused.
6. **Pre-flight softens the gate.** Pre-flight only adds a comment and a check; it never removes a gate refusal. It is
   deliberately a subset of the gate's rules, and a test asserts every pre-flight code is a gate code.
7. **Auto-ready skips the author's review of their own draft.** `autoready` defaults off, applies only to pull
   requests carrying an explicit `Ready: when-green` trailer by an agent identity, and never to a pull request whose
   classification needs the human.
8. **The log is trusted as evidence.** It is not: KPIs and the acceptance test read it, and nothing that gates reads
   it. The one authoritative part, slots, records `granted_by` and is checked by the acceptance test (A1).
9. **A local database anyone can edit.** The file has the permissions of the repository checkout; it is a local-trust
   store, like `.git`. onloop does not claim otherwise. The authoritative decisions (merge, approval) are on GitHub.
10. **Injection through event or issue text into an anchor.** Anchors are composed from `onloop.yaml`, the ledger and
    the agent files only, never from events, issues or PR text. Test: the anchor-from-files mutation.
11. **A dead handler hides.** After 8 attempts a delivery is `dead` and a `handler.dead` event is appended; `onloop
    events --dead` lists them and the watchdog fails on any.
12. **Observe mode that acts, or that fails.** In observe mode `ctx.effect` does not call `fn`; it writes an
    `effects` row of kind `observed:<kind>` with the detail, and returns. It does not raise: a raise would fail the
    delivery, retry it eight times and fill the dead-letter queue with an ordinary week. Test: run every handler with
    the mode set; assert no GitHub write was attempted, no delivery is `dead`, and an `observed:*` row exists for each
    effect the handler would have made.

## 13. Decisions

Recorded in [docs/decisions/0001-scope-of-the-loop-without-waiting.md](../decisions/0001-scope-of-the-loop-without-waiting.md):

1. The autogate fires on `review.submitted`, `check.completed`, `verdict.reversed` and `pr.ready` (a pull request
   approved while still a draft), always through `gate.run`. Turning it on is a loosening.
2. `cross_owner: reviewer_ack` is opt-in in 0.3; the default changes at 1.0 with a changelog note.
3. No notification file in 0.3. The exit of `onloop slot wait` is the wake-up. It stays on the cut list.
4. The database lives outside every worktree, at `$ONLOOP_DB`.
5. A shadow week on a real project is the project owner's decision (a long-running process on their host). It cannot
   start before M2, and it should not overlap a demo week.

## 14a. Later (not in this plan)

- Anchor a human slot grant to a GitHub comment by the human login, so that it is authenticated and not only attested
  (0.4). Until then the grant records a decision; it does not prove it.
- A notification file for sessions that cannot run a long command; capacity above 1 per slot; the delta pane that reads
  `pr.head_moved`.

## 14. Estimate

By milestone, in working sessions of about three hours each, including the Reviewer's read and fixes: M1 two, M2 two,
M3 two, M4 two, M5 one, M6 two, M7 one plus the two measured weeks. About twelve sessions of work and two calendar
weeks of measurement. The plan's own risk is M1: polling that misses an event is silent, so it gets the most tests.
