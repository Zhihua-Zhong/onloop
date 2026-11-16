# Decision 0013: the human task list

- **Status:** Accepted on 2026-11-16
- **Date:** 2026-11-16
- **Decided by:** the project lead (onloop is the system of record, 2026-11-10) and the onloop lead, on the R5 plan
- **Size:** large (a new record with its own invariants, a new view, and the first read adapters)

## Context

What waits on the person lived in several places that disagreed: an agent's memory, reply footers, a labelled issue
list, a status note. Items were dropped without a trace. In onloop's own ledger, the same thing happened: a role's
next turn cleared every `blocked` wait it had opened, whether or not the person had acted. So an ask could vanish the
moment the agent ended another turn.

The person returns at irregular times and wants the whole current list, in one order, at once.

## Decision

1. **onloop's ledger is the system of record for everything that waits on the person.** A tracker (a GitHub issue
   label today; Linear or Slack later) is a **read adapter**. Its interface has no write method. Reply footers and
   digests are generated from the list.
2. **A human task is its own record** (`human_tasks`, ledger schema 7), opened by:
   - an outcome line with `owner=human`;
   - `onloop ask`;
   - a blocking notification;
   - a work-graph node waiting on the person;
   - an adapter's ask.

   A wait clearing (the role's next turn) never closes the task.
3. **Every close has a resolution and evidence.** The database refuses anything else.

   | Resolution | Evidence |
   |---|---|
   | `done` | the person's mark (`human:ui`, `human:cli`: "local user, not verified"), the code host's act, or the adapter's close |
   | `declined` | the person's mark, with an optional note |
   | `superseded` | the newer task, or the author's turn that closed the ref without the person ("resolved without you") |
   | `expired` | the author's or coordinator's turn (`done resolves=<task>`), or `onloop task resolve … expired` |

   A task that stands for a human approval at the merge gate resolves only on the code host's approval. A click in
   onloop is evidence, not authority ([decision 0011](0011-ui-write-path.md)).
4. **Incomplete asks.** An ask without an effect, or with a cost of delay and no `holds`, is shown last, marked with
   the reason, and returned to its author once. A `critical` one keeps its place and is flagged.
5. **One order.**
   1. Weight bands: `critical`, `supporting`, unweighted, `skippable`, then incomplete.
   2. In a band, the days left: computed when read, as `max(0, cost_from − today)` in `kpi.timezone`, where a past
      date is 0 ("costing now"); then a cost not known (`days=?`); then no cost stated.
   3. Then age, oldest first; then id.

   The API, Now, the digest and `onloop tasks` all use one function.
6. **Expiry, in the manner of actions/stale.**
   - After 7 days unresolved, the task returns to its author role. After 14, it returns to the coordinator, who closes
     it or keeps it with a reason (a keep restarts both clocks).
   - Returns are `task.returned` events, logged by a detector on onloopd's tick. Their due times come from the task
     row, so replay never reads a clock.
   - A return reaches the role as a carrier delivery (kind `event`, de-duplicated by `return:<task>:<stage>`) only
     when the role opted in. Otherwise onloop logs `would.deliver`.
   - The task never leaves the person's list. Open PRs get the same 7 and 14 days, as alerts; a push resets the
     clock.
   - **onloop never closes anything by itself.**
7. **Reconciliation.** A detector compares the asks it can see with the list:
   - the outcome lines that name the person;
   - PRs that need a human approver, or carry an `Effect:` line;
   - the adapter's open asks;
   - blocking notifications.

   It opens an alert for each ask missing from the list. It reads nothing but the outcome line of a turn: an ask
   written only in prose is not seen, and the shared paragraph forbids it.
8. **A "since you were away" digest,** keyed on the person's last visit (a `visit.recorded` event from the UI or
   `onloop tasks --digest`). It shows what was added, what was resolved (with "resolved without you" apart), and what returned
   or expired. Then it shows the ordered list.
9. **Three writes from the UI**, behind decision 0011's rules (the session cookie, the CSRF token and the Origin check),
   all evidence and none authority:
   - resolve (`done` or `declined`);
   - keep (with a reason);
   - record a visit.
10. **Read adapters are plugins** in the entry-point group `onloop.adapters`.
    - Each declares its capabilities (`asks`, `goals`, `events`) and is given a read-only client. That client allows a
      REST `GET`, or a GraphQL document whose operations are all queries.
    - Cursors are opaque and owned by the adapter.
    - Evidence carries the source's time when it has one, and onloop's observation time.
    - An adapter's live calls to a host need that host's own login. The GitHub goal and event checks wait for a test
      repository the project lead approves; until then they run against fakes.
11. **Story 15 (WebAuthn approvals) moves from R5 to R6 or later.** R5's writes are evidence only, and story 15 still
    needs a verifier in CI and a trusted rendering source.

## Alternatives considered

| Option | Verdict | Why |
|---|---|---|
| The tracker as system of record, onloop reading it | Rejected by the project lead | Two writable lists bring the original problem back |
| Camunda Tasklist, Temporal | Not adopted; semantics borrowed | A second engine and store beside the ledger (decisions 0003, 0004) |
| actions/stale | Not adopted; thresholds borrowed | It writes labels and closes items; onloop only returns them |
| Closing a task when its wait clears | Rejected | That is the silent drop this decision exists to stop |

## Consequences

- Ledger schema 7: `human_tasks`, `visits`, the goal tables ([decision 0014](0014-goals-and-outcome-kpis.md)),
  `links`; `events.source` takes `adapter`; `resources.waker_kind` takes `event`.
- Now's "Waiting on you" becomes the ordered list. `GET /api/tasks` and `GET /api/digest` are added, along with
  `onloop ask`, `onloop tasks`, `onloop task resolve|keep` and `onloop tasks --digest` (`onloop digest` stays the older supervisor's).
