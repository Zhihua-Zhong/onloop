# Slots, pre-flight and the KPIs

This page covers the commands that remain from the loop without waiting, and the two policy blocks they read,
`slots:` and `automation:`. The act-mode loop itself (`onloop run`, its dispatcher and handlers, `onloop intake`,
`onloop acceptance`, `onloop handover` and `onloop kpi --shadow`) was removed in 0.3.0: onloopd, the resident service
([decision 0008](decisions/0008-resident-service-and-dead-man-switch.md)), now reads GitHub into the overlay ledger,
detects what is stuck and reports what it would do (`onloop report observe`), and its conductor will act in a later
release.

## Commands

| Command | What it does |
|---|---|
| `onloop events` | List the legacy event log in `onloop.db` (read-only). `--kind`, `--subject pr#12`, `--since SEQ`, `--limit`, `--json`. |
| `onloop slot` | Ask for, wait for, grant, renew and release a slot; `need` prints the slots a set of paths needs; `status` shows holders and queues. |
| `onloop preflight N` | Report a pull request's *form* problems (the `P` codes) and the slots it needs, before the gate runs. `--post` creates or edits the one report comment. |
| `onloop kpi` | The three measures (lead time, idle time with work waiting, reverts), each with its definition. `onloop kpi weekly` gives the ledger's weekly counters. |
| `onloop watchdog --db FILE` | As before. |
| `onloop anchor`, `hooks` | Direction for a session, composed from the policy and the ledger only. |

### Slots

A slot is a scarce thing that one holder uses at a time (a database, a host, the gate itself). A change that touches a
slot's paths needs the slot; a request joins a queue; the slot's approver grants it; the holder works under a lease
and releases it.

The slots live in the overlay ledger (`.onloop/ledger.db`, or `--ledger FILE`): every request, grant, renewal, release
and expiry is a `slot.*` event, and the queue and the grants are its projections, so replay rebuilds them and the
constraint view shows who waits on which slot. The rule that one seat holds one live grant is the ledger's own unique
index: an event that would break it is refused and not logged. A slot command given the old `--db` says to use
`--ledger`. The instance id in an anchored grant's line (`/grant <instance>:<request>`) is the ledger file's own.

```bash
onloop slot need --paths db/migrate/001.sql        # which slots does this change need?
onloop slot request db --holder builder-1 --role builder --reason "migration" --ref pr#12
onloop slot wait db --holder builder-1 --ref pr#12 # exit 0 granted, 3 expired, 4 timed out
onloop slot grant 7 --by your-login                # for a slot whose approver is a person
onloop slot renew 3 --holder builder-1
onloop slot release 3 --holder builder-1
onloop slot status
```

The `--by` identity on a grant is recorded in the log, **attested and not authenticated**: the log says who claimed to
grant, not who proved it. The gate slot belongs to the system role `onloop`; a person cannot request as `onloop`.
`onloop slot status` shows each seat of a slot whose capacity is above 1.

### Pre-flight

`onloop preflight N` is a deliberate subset of the gate. It reports only refusals about *form* that the author can fix
at once (no authoring role, an unknown role, a path owned by another role without a Cross-owner line) and which slots
the change needs. It never removes or softens a gate refusal. It also gives two warnings that are never refusals:
`W004` (a changed file gained carriage-return line endings) and `W005` (a changed path no policy path covers).
A policy can make the gate refuse for either (`enforce:` in `docs/configuration.md`); pre-flight then reports `P004` or
`P005` as a form refusal instead.

## Policy reference: `slots:`

```yaml
slots:
  db:
    paths: ["db/**"]
    approver: human
    timeout_minutes: 60
    wait_minutes: 1440
    max_renewals: 3
    conditions: ["no migration is running"]
    commands: ["db/backup.sh"]
```

| Field | Meaning | Default | Loosening (the human approves it) |
|---|---|---|---|
| `paths` | Path patterns the slot covers; required, non-empty (`**` for everything). | none | covering fewer paths than the base |
| `approver` | `auto`, `human`, or `role:<name>`. | `human` | a freer approver (human to a role to auto), or one role to another |
| `timeout_minutes` | Length of a lease. | 60 | raising it |
| `wait_minutes` | How long a request may wait before it expires. | 1440 | none |
| `max_renewals` | How many times a holder may renew. | 3 | raising it |
| `human_grant` | `attested` (the default) or `anchored`. For a slot with `approver: human`, `anchored` makes `onloop slot grant ID --anchor URL` the only way to grant it: the URL is a comment by the human (a `User`, as GitHub reports it) on the request's own pull request or issue, with a line of its own `/grant <instance>:<request id>` (the instance is the database's own id, shown by `slot status` and printed when the request is made; the old `/grant <id>` form and another database's id are refused), written after the request and never edited. It is checked against GitHub when the grant is made, from the API's answer and not from the URL's text. The request's `ref` must be exactly `pr#N` or `issue#N`. A bare `--by` is refused, and the grant event stores the comment id and its time. | `attested` | `anchored` to `attested` |
| `capacity` | How many holders the slot has at once, 1 to 8. Each live grant sits in its own numbered seat and the database refuses two in one seat; the lowest free seat is taken inside the transaction that checks the queue. Every lease has its own expiry and renewals, and the queue stays first come first served. The `gate` slot is always 1: a policy that says otherwise does not load. | 1 | raising it |
| `conditions` | Sentences that must hold to be granted. | none | dropping or changing one |
| `commands` | Commands the holder runs under the lease. | none | dropping or changing one |

Removing a slot is a loosening. Adding a slot, or tightening one, is not.

## Policy reference: `automation:`

```yaml
automation:
  mode: observe
  intake_seconds: 60
  autogate: false
  autoready: false
  cross_owner: author
  allow_working_tree: false
  max_stop_minutes: 60
  max_defer_hours: 4
  external_gate_check: merge-gate
```

`cross_owner` is read by the gate and `max_defer_hours` by `onloop watchdog --db` (only an `onloop.db` written
before 0.3.0 holds deferred deliveries). The other fields were the act-mode loop's; they are still accepted and
checked, so an existing policy keeps loading, but nothing reads them since 0.3.0.

| Field | Meaning | Default | Loosening (the human approves it) |
|---|---|---|---|
| `cross_owner` | Who may vouch for a path owned by another role: `author` (the Cross-owner line in the description) or `reviewer_ack` (the reviewer's approval on the head names it). | `author` | `reviewer_ack` to `author` |
| `max_defer_hours` | How long a deferred delivery may wait before the watchdog flags it, at least 1. | 4 | raising it |
| `mode`, `intake_seconds`, `autogate`, `autoready`, `allow_working_tree`, `max_stop_minutes`, `external_gate_check` | Not read since 0.3.0. | as before | as before |

A change to either block that loosens it is classified `authority_widening`, so it needs the human, and the gate reads
the policy from the base branch, not from the change.

## Security notes

- Text in the ledger reaches every agent's prompt through the anchors, so a change to `ledger.yaml` is a change to
  what agents are told. The reviewer reads ledger changes with that in mind, not only as bookkeeping.
- Anchors are composed from `onloop.yaml` and `ledger.yaml` only, never from an event, issue, pull request or message.
- `onloop hooks` prints the hook configuration and writes nothing; the person who owns the settings pastes it.
