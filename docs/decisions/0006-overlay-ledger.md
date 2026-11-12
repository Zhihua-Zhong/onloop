# Decision 0006: one overlay ledger, reached by a strangler migration

- **Status:** Accepted on 2026-11-09
- **Date:** 2026-11-09
- **Decided by:** the project lead, approving the technical design of 2026-11-09; the onloop lead chose the migration
  path (a separate ledger file, the old stores retired by the end of the UI step)
- **Size:** large (it changes where every record onloop keeps lives)

## Context

onloop keeps two stores today, each an SQLite file with its own log and projections:

- `onloop.db` (`store.py`): the poll loop's event log, its snapshots and cursors, the dispatcher's handler deliveries
  and effects, the slot tables and the runner row;
- the work graph's store (`workgraph/store.py`): its own event log (`wg_events`) and node projection.

The technical design of 2026-11-09 puts one record at the centre of onloop: an event log with projections for items,
waits, resources, leases, roles, deliveries and schedules, which the resident service ([decision
0008](0008-resident-service-and-dead-man-switch.md)) reads and serves. The two stores do not fit it as they are: the
old `events` table has another shape (a content-hash id, `subject`, no `source`), the old `deliveries` table means a
handler delivery and not a message to a role, and the work graph has a log of its own. At the start of the first
refactor step, 68 raw-SQL sites in 12 modules (and about 85 in 12 test files) read the old tables directly.

## Decision

1. **One ledger, in its own file.** `.onloop/ledger.db` by default (`$ONLOOP_LEDGER`), on the shared SQLite base, with
   its own schema version and forward-only migrations (`src/onloop/overlay/`). Every write appends one event and
   updates the projections in the same transaction. The projections can be dropped and rebuilt from the log at any
   time, and a rebuild gives the same rows byte for byte (`onloop overlay replay --check`).
2. **Unambiguous names.** The ledger's `deliveries` table is the design's: a message to a role, with `created_at`,
   `delivered_at` (written to the session's stdin), `processing_at` (the replay of the message was seen) and
   `acked_at` (the turn ended with a valid outcome). Where both are meant, the old table is called *the dispatcher's
   handler deliveries*. No ledger table reuses an old name with another meaning in code or docs.
3. **A strangler migration.**
   - The ledger is the only record onloopd reads.
   - The old stores are legacy: read-only for new code. After the first refactor step, no new code writes to
     `onloop.db` or to the `wg_*` tables, except the existing poll loop and the existing commands that already do.
   - The legacy stores reach the ledger by a forward-only import, by cursor: each old event goes in verbatim under
     its old sha256 id, which is the ledger's dedup key, with `source = "legacy"`. The cursor lives in the ledger and
     moves in the same transaction as the batch, so a second run or a crash mid-batch ends in the same projections.
   - onloopd runs that import on every tick, which is how the poll loop is wired in as an ingest source.
   - The old stores are retired at the end of the UI step (R3), when the checklist below is empty.
4. **What later steps rely on is fixed in the schema now.** The disposition outcomes (`done`, `idle`, `in_review`,
   `blocked`, `handoff`, `continue`) are the only values `items.outcome` takes, and items and waits carry a `ref` (a
   PR, issue or file). A resource of kind `waker` (a cron id, a monitor or a pid) is what a `continue` waits on, so a
   waker that has died shows in the constraint query as a wait on a dead resource, with no table of its own.

## Retirement checklist

Every module that reads or writes the legacy stores at the end of the first step. Each moves to the ledger (or is
retired) in the step named; the old store files are dropped when the list is empty.

| Module | Legacy tables it reads or writes | Moves in | Status after R3 |
|---|---|---|---|
| `intake.py` | `events`, `pr_snapshots`, `cursors` (writes) | R2 (GitHub ingest writes the ledger) | **Done.** Its pure diff (`compute`) feeds `ingest/github.py`, which writes the ledger; its tests run against the ledger |
| `dispatcher.py` | `events`, handler `deliveries`, `effects` (16 SQL sites) | R2 | **Retired** (removed with `onloop run`) |
| `runner.py` | `runner` | R2 (onloopd is the one runner) | **Retired** |
| `handlers/` (`common`, `postmerge`, `preflight`, `autogate`, `autoready`) | `events`, `effects` | R2 | **Retired** |
| `slots.py` | `events`, `slot_requests`, `grants` (24 SQL sites) | R2 (resources and leases in the ledger) | **Done.** Writes `slot.*` events; the queue and grants are ledger projections (schema 4) |
| `kpi.py` | `events`, handler `deliveries`, `effects` | R2 | **Done.** Counts from the ledger's events; `--shadow` retired; reverts are "unknown", not 0, where the gate recorded nothing |
| `acceptance.py`, `handover.py` | `events`, handler `deliveries`, `grants` | R2 | **Retired** (with `onloop acceptance` and `onloop handover`) |
| `records.py` | `events` (writes `record.written`) | R3, with its readers | **Retired** with the workbench (decision 0009) |
| `cli.py` | `watchdog` (one read of handler `deliveries`), `events`, `record`, the gate's decisions (writes) | R3 | **Done.** `watchdog --db` and `record` removed; `events` reads the ledger; `onloop gate` writes its decisions to the ledger under the source `gate` (schema 5) |
| `workbench/` (`model`, `check`, `build`, `claims`, `criteria`) | `events` (read-only) | R3 (the workbench pages retire) | **Retired** (decision 0009) |
| `workgraph/` (`store`, `instance`, `flow`, `panel`, `decide`) | `wg_events`, `wg_nodes`, `wg_projection`, `wg_sync_runs` | R3 (the UI reads the ledger) | **Retired**; the node model and the GitHub-issues mapping moved into `supervisor/`, which reads no store |
| `daemon/main.py` | `onloop.db`, `wg_events` (read on every tick) | R3 | **Done.** onloopd reads neither; `onloop overlay import` reads each once, the upgrade path |
| `store.py` | all of `onloop.db` | dropped after R2 and R3 | **Dropped.** Its writer survives only in the tests (`tests/legacy_poll_store.py`), to build an old file for the import's tests |

**The checklist is closed (R3, 2026-11-09).** No module in the package reads or writes `onloop.db` or the work graph's
store; the overlay ledger is onloop's one record. The one-shot import (`overlay/importer.py`) reads them, read-only, when
asked.

## Consequences

- During R2 and R3 two old files live beside the ledger, and their own tests stay as they are. The byte-identical
  rebuild check covers the ledger's projections only.
- A reader moved to the ledger reads one record with one clock: the ledger stamps `ts_system` itself on append, and
  an event's own time, when it has one, is kept as `at`.
- `onloop workgraph replay` keeps working on the work graph's store until R3; its successor for the ledger is
  `onloop overlay replay`.
- The schema is small enough to read in one file (`src/onloop/overlay/schema.py`), and the projection rules sit in
  one module (`src/onloop/overlay/projections.py`), the only place that knows what an event means.
