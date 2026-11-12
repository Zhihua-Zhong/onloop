"""The overlay ledger's schema: one append-only event log and the projection tables rebuilt from it.

Its own file (`.onloop/ledger.db` by default, or `$ONLOOP_LEDGER`), separate from the older `onloop.db` and the work
graph's store, with its own version in `PRAGMA user_version` and forward-only migrations (index + 1 is the version).

Names. `deliveries` here is the design's message to a role (created, delivered into the session, seen being processed,
acknowledged). It is not the dispatcher's handler deliveries, which live in `onloop.db`.

Value sets that later steps rely on are fixed here and checked by the database:
- an item's `outcome` is one of the disposition outcomes: done, idle, in_review, blocked, handoff, continue;
- `ref` on an item or a wait carries the PR, issue or file reference an outcome points at;
- a resource of kind `waker` (a cron id, a monitor or a pid) is what a `continue` waits on, so a dead waker shows in
  the constraint query as a wait on a dead resource.
"""

from __future__ import annotations

_TS = "GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'"

SOURCES_1 = ("github", "hook", "healthchecks", "ui", "detector", "legacy")
SOURCES_3 = (*SOURCES_1, "config")  # version 3: onloopd.yaml's declarations, not a person's action
SOURCES_5 = (*SOURCES_3, "gate")  # version 5: the merge gate's own decisions, not a person's action
# version 6: the session carrier's own events (leases, runs, deliveries, timers), and what a person sends by the CLI
SOURCES = (*SOURCES_5, "carrier", "cli")
ITEM_KINDS = ("task", "decision", "followup", "alert")
ITEM_STATES = ("open", "ready", "blocked", "in_review", "done")
OUTCOMES = ("done", "idle", "in_review", "blocked", "handoff", "continue")
WAIT_ON_KINDS = ("role", "resource", "human", "event")
# Each migration keeps the value sets it was written with: a later version widens a copy, never the original.
WAIT_REASONS_1 = (
    "conflict",
    "missing_verdict",
    "needs_human",
    "running",
    "paused_by_human",
    "permission",
    "schedule",
    "continue",
)
WAIT_REASONS_2 = (*WAIT_REASONS_1, "missing_evidence")  # version 2
WAIT_REASONS = (*WAIT_REASONS_2, "blocked", "handoff")  # version 3
RESOURCE_STATES = ("alive", "dead", "unknown")  # version 2
RESOURCE_KINDS = ("lock", "queue", "human", "waker")
WAKER_KINDS = ("cron", "monitor", "pid")
ROLE_STATES = ("idle", "working", "paused_by_human", "blocked")

# The projection tables, each with its primary key: what replay clears and rebuilds, and what the byte-identical
# check compares. `ingest_cursors` is not a projection: it records how far each source has been read.
PROJECTIONS = {
    "items": "id",
    "waits": "id",
    "resources": "id",
    "leases": "id",
    "roles": "id",
    "deliveries": "id",
    "schedules": "id",
    "turns": "id",  # version 2
    "slot_requests": "id",  # version 4
    "slot_grants": "id",  # version 4
    "role_sessions": "id",  # version 6
    "session_leases": "id",  # version 6
    "runs": "id",  # version 6
    "messages": "id",  # version 6
    "timers": "id",  # version 6
    "carrier_state": "id",  # version 6
}

# Version 6 value sets (decision 0010).
DELIVERY_KINDS = ("message", "event", "corrective", "dead_waker")
DELIVERY_STATES = (
    "created",
    "claimed",
    "delivered",
    "processing",
    "acked",
    "failed",
    "escalated",
    "cancelled",
)
RUN_STATES = (
    "running",
    "ok",
    "refused",
    "error",
    "timeout",
    "silent",
    "killed",
    "usage_limited",
    "not_logged_in",
    "orphaned",
    "held_elsewhere",
    "contended",
)
TIMER_KINDS = ("corrective", "escalate", "retry", "lease_check")
MESSAGE_AUTHORS = ("human", "onloop", "role")
MESSAGE_CHANNELS = ("ui", "cli", "carrier")
CARRIER_PAUSES = ("kill_switch", "usage_limit", "not_logged_in", "overage")


def _in(column: str, values: tuple[str, ...], *, nullable: bool = False) -> str:
    listed = ", ".join(f"'{v}'" for v in values)
    check = f"{column} IN ({listed})"
    return f"({column} IS NULL OR {check})" if nullable else check


def _ts(column: str, *, nullable: bool = True) -> str:
    check = f"{column} {_TS}"
    return (
        f"{column} TEXT CHECK ({column} IS NULL OR {check})"
        if nullable
        else f"{column} TEXT NOT NULL CHECK ({check})"
    )


SCHEMA_1 = f"""
CREATE TABLE events (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  {_ts("ts_system", nullable=False)},
  source      TEXT NOT NULL CHECK ({_in("source", SOURCES_1)}),
  kind        TEXT NOT NULL,
  subject_ref TEXT NOT NULL,
  actor       TEXT,
  {_ts("at")},
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
CREATE INDEX events_kind ON events(kind, seq);
CREATE INDEX events_subject ON events(subject_ref, seq);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;

CREATE TABLE items (
  id                TEXT PRIMARY KEY,
  kind              TEXT NOT NULL CHECK ({_in("kind", ITEM_KINDS)}),
  source_ref        TEXT,
  title             TEXT,
  owner_role        TEXT,
  state             TEXT NOT NULL CHECK ({_in("state", ITEM_STATES)}),
  outcome           TEXT CHECK ({_in("outcome", OUTCOMES, nullable=True)}),
  ref               TEXT,
  parent_id         TEXT,
  evidence_required INTEGER NOT NULL DEFAULT 0 CHECK (evidence_required IN (0, 1)),
  evidence_ref      TEXT,
  detail            TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(detail)),
  {_ts("opened_at")},
  {_ts("updated_at")},
  last_seq          INTEGER NOT NULL
);

CREATE TABLE waits (
  id         TEXT PRIMARY KEY,
  item_id    TEXT,
  waiter     TEXT NOT NULL,
  on_kind    TEXT NOT NULL CHECK ({_in("on_kind", WAIT_ON_KINDS)}),
  on_ref     TEXT NOT NULL,
  reason     TEXT NOT NULL CHECK ({_in("reason", WAIT_REASONS_1)}),
  ref        TEXT,
  {_ts("since", nullable=False)},
  {_ts("cleared_at")},
  last_seq   INTEGER NOT NULL
);
CREATE INDEX waits_open ON waits(on_ref) WHERE cleared_at IS NULL;

CREATE TABLE resources (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL CHECK ({_in("kind", RESOURCE_KINDS)}),
  capacity   INTEGER NOT NULL DEFAULT 1 CHECK (capacity >= 1),
  plugin     TEXT,
  waker_kind TEXT CHECK ({_in("waker_kind", WAKER_KINDS, nullable=True)} AND (kind = 'waker') = (waker_kind IS NOT NULL)),
  waker_ref  TEXT,
  last_seq   INTEGER NOT NULL
);

CREATE TABLE leases (
  id          TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL,
  holder      TEXT NOT NULL,
  {_ts("granted_at", nullable=False)},
  ttl_s       INTEGER,
  {_ts("released_at")},
  holder_pid  INTEGER,
  last_seq    INTEGER NOT NULL
);
CREATE INDEX leases_live ON leases(resource_id) WHERE released_at IS NULL;

CREATE TABLE roles (
  id         TEXT PRIMARY KEY,
  session_id TEXT,
  state      TEXT NOT NULL CHECK ({_in("state", ROLE_STATES)}),
  paused_by  TEXT,
  budget     TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(budget)),
  last_seq   INTEGER NOT NULL
);

CREATE TABLE deliveries (
  id            TEXT PRIMARY KEY,
  to_role       TEXT NOT NULL,
  item_id       TEXT,
  {_ts("created_at", nullable=False)},
  {_ts("delivered_at")},
  {_ts("processing_at")},
  {_ts("acked_at")},
  attempts      INTEGER NOT NULL DEFAULT 0,
  escalated_to  TEXT,
  last_seq      INTEGER NOT NULL
);

CREATE TABLE schedules (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  period       TEXT,
  grace        TEXT,
  {_ts("last_success")},
  last_status  TEXT,
  owner_role   TEXT,
  last_seq     INTEGER NOT NULL
);

CREATE TABLE ingest_cursors (
  source   TEXT PRIMARY KEY,
  position INTEGER NOT NULL CHECK (position >= 0),
  {_ts("updated_at", nullable=False)}
)
"""

# Version 2:
# - `turns`, one row per ended role turn: its parsed outcome line, or the reason it did not parse (decision 0007);
# - `resources.state` and `checked_at`, so a waker found dead is a resource state the constraint query shows;
# - `waits.reason` also takes `missing_evidence`; SQLite cannot widen a CHECK in place, so `waits` is copied row for row;
# - `schedules.last_result_at` and `log_tail`, for the schedule detector;
# - `ingest_state`, per-source state that is not a projection (the GitHub ingest's snapshots and ETag).
SCHEMA_2 = f"""
CREATE TABLE waits_v2 (
  id         TEXT PRIMARY KEY,
  item_id    TEXT,
  waiter     TEXT NOT NULL,
  on_kind    TEXT NOT NULL CHECK ({_in("on_kind", WAIT_ON_KINDS)}),
  on_ref     TEXT NOT NULL,
  reason     TEXT NOT NULL CHECK ({_in("reason", WAIT_REASONS_2)}),
  ref        TEXT,
  {_ts("since", nullable=False)},
  {_ts("cleared_at")},
  last_seq   INTEGER NOT NULL
);
INSERT INTO waits_v2 (id, item_id, waiter, on_kind, on_ref, reason, ref, since, cleared_at, last_seq) SELECT id, item_id, waiter, on_kind, on_ref, reason, ref, since, cleared_at, last_seq FROM waits ORDER BY id;
DROP INDEX waits_open;
DROP TABLE waits;
ALTER TABLE waits_v2 RENAME TO waits;
CREATE INDEX waits_open ON waits(on_ref) WHERE cleared_at IS NULL;

ALTER TABLE resources ADD COLUMN state TEXT NOT NULL DEFAULT 'unknown' CHECK ({_in("state", RESOURCE_STATES)});
ALTER TABLE resources ADD COLUMN {_ts("checked_at")};
ALTER TABLE schedules ADD COLUMN {_ts("last_result_at")};
ALTER TABLE schedules ADD COLUMN log_tail TEXT;

CREATE TABLE turns (
  id          TEXT PRIMARY KEY,
  role        TEXT NOT NULL,
  session_id  TEXT,
  {_ts("ended_at", nullable=False)},
  outcome     TEXT CHECK ({_in("outcome", OUTCOMES, nullable=True)}),
  ref         TEXT,
  owner       TEXT,
  waker       TEXT,
  line        TEXT,
  parse_error TEXT,
  last_seq    INTEGER NOT NULL,
  CHECK ((outcome IS NULL) <> (parse_error IS NULL))
);
CREATE INDEX turns_role ON turns(role, ended_at);

CREATE TABLE ingest_state (
  source     TEXT NOT NULL,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL CHECK (json_valid(value)),
  {_ts("updated_at", nullable=False)},
  PRIMARY KEY (source, key)
)
"""

# Version 3:
# - `events.source` also takes `config`, so what onloopd.yaml declares is not logged as a person's action (`ui`);
# - `waits.reason` also takes `blocked` and `handoff`, the waits those outcomes open (decision 0007).
# SQLite cannot widen a CHECK in place, so both tables are copied row for row, `events` with its seq, its indexes and
# its append-only triggers (DROP TABLE fires no trigger).
SCHEMA_3 = f"""
CREATE TABLE events_v3 (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  {_ts("ts_system", nullable=False)},
  source      TEXT NOT NULL CHECK ({_in("source", SOURCES_3)}),
  kind        TEXT NOT NULL,
  subject_ref TEXT NOT NULL,
  actor       TEXT,
  {_ts("at")},
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
INSERT INTO events_v3 (seq, id, ts_system, source, kind, subject_ref, actor, at, payload) SELECT seq, id, ts_system, source, kind, subject_ref, actor, at, payload FROM events ORDER BY seq;
DROP TABLE events;
ALTER TABLE events_v3 RENAME TO events;
CREATE INDEX events_kind ON events(kind, seq);
CREATE INDEX events_subject ON events(subject_ref, seq);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;

CREATE TABLE waits_v3 (
  id         TEXT PRIMARY KEY,
  item_id    TEXT,
  waiter     TEXT NOT NULL,
  on_kind    TEXT NOT NULL CHECK ({_in("on_kind", WAIT_ON_KINDS)}),
  on_ref     TEXT NOT NULL,
  reason     TEXT NOT NULL CHECK ({_in("reason", WAIT_REASONS)}),
  ref        TEXT,
  {_ts("since", nullable=False)},
  {_ts("cleared_at")},
  last_seq   INTEGER NOT NULL
);
INSERT INTO waits_v3 (id, item_id, waiter, on_kind, on_ref, reason, ref, since, cleared_at, last_seq) SELECT id, item_id, waiter, on_kind, on_ref, reason, ref, since, cleared_at, last_seq FROM waits ORDER BY id;
DROP INDEX waits_open;
DROP TABLE waits;
ALTER TABLE waits_v3 RENAME TO waits;
CREATE INDEX waits_open ON waits(on_ref) WHERE cleared_at IS NULL
"""

# Version 4: the slots move onto the ledger. `slot_requests` and `slot_grants` are projections of the `slot.*` events
# onloop's own slot commands log (an imported legacy slot event projects only into waits and leases, so the two id
# spaces never meet). The invariant the slots rest on is the database's: one live grant per seat
# (`one_live_seat`), and one waiting request per holder and ref. A projection that would break it fails the append, so
# the event is not logged. `instance` names this ledger file for anchored grants ("/grant <instance>:<request>"); it
# is drawn once, at this migration, and is not a projection.
SCHEMA_4 = f"""
CREATE TABLE slot_requests (
  id           INTEGER PRIMARY KEY CHECK (id >= 1),
  slot         TEXT NOT NULL,
  holder       TEXT NOT NULL,
  role         TEXT NOT NULL,
  reason       TEXT NOT NULL,
  ref          TEXT NOT NULL,
  paths        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(paths)),
  {_ts("requested_at", nullable=False)},
  {_ts("wait_until", nullable=False)},
  status       TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'granted', 'cancelled', 'expired')),
  last_seq     INTEGER NOT NULL
);
CREATE UNIQUE INDEX one_waiting_request ON slot_requests(slot, holder, ref) WHERE status = 'waiting';

CREATE TABLE slot_grants (
  id             INTEGER PRIMARY KEY CHECK (id >= 1),
  slot           TEXT NOT NULL,
  holder         TEXT NOT NULL,
  role           TEXT NOT NULL,
  request_id     INTEGER NOT NULL,
  {_ts("granted_at", nullable=False)},
  granted_by     TEXT NOT NULL,
  {_ts("expires_at", nullable=False)},
  {_ts("renewed_at")},
  {_ts("released_at")},
  release_reason TEXT,
  conditions     TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(conditions)),
  commands       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(commands)),
  seat           INTEGER NOT NULL DEFAULT 1 CHECK (seat BETWEEN 1 AND 8 AND (seat = 1 OR slot <> 'gate')),
  last_seq       INTEGER NOT NULL
);
CREATE UNIQUE INDEX one_live_seat ON slot_grants(slot, seat) WHERE released_at IS NULL;

CREATE TABLE instance (
  id  INTEGER PRIMARY KEY CHECK (id = 1),
  uid TEXT NOT NULL CHECK (length(uid) = 16 AND uid NOT GLOB '*[^0-9a-f]*')
);
INSERT INTO instance(id, uid) VALUES (1, lower(hex(randomblob(8))))
"""

# Version 5: `events.source` also takes `gate`, so the merge gate's decisions (gate.merged, gate.refused,
# gate.postmerge_checked, gate.reverted) are logged as onloop's own, not as a person's (`ui`). It is what the KPIs
# count refusals and reverts from, now that `onloop.db` is gone (decision 0009). As in version 3, SQLite cannot widen a
# CHECK in place, so `events` is copied row for row with its seq, its indexes and its append-only triggers.
SCHEMA_5 = f"""
CREATE TABLE events_v5 (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  {_ts("ts_system", nullable=False)},
  source      TEXT NOT NULL CHECK ({_in("source", SOURCES_5)}),
  kind        TEXT NOT NULL,
  subject_ref TEXT NOT NULL,
  actor       TEXT,
  {_ts("at")},
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
INSERT INTO events_v5 (seq, id, ts_system, source, kind, subject_ref, actor, at, payload) SELECT seq, id, ts_system, source, kind, subject_ref, actor, at, payload FROM events ORDER BY seq;
DROP TABLE events;
ALTER TABLE events_v5 RENAME TO events;
CREATE INDEX events_kind ON events(kind, seq);
CREATE INDEX events_subject ON events(subject_ref, seq);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END
"""

# Version 6: the session carrier (decision 0010). `events.source` also takes `carrier` and `cli` (copied row for row,
# as in versions 3 and 5). The invariants are the database's, so a projection that would break one fails the append:
# - one live lease per session id (`one_live_session_lease`), and one role per session id;
# - at most one corrective delivery per turn (`one_corrective_per_turn`);
# - one delivery per cause (`one_delivery_per_cause`), so the same cause seen twice makes no second delivery;
# - a run's status, a delivery's kind and state, a timer's kind and a message's author and channel are fixed sets.
# Acknowledgement and outcome are apart: a delivery is acked by a Stop-hook turn (`acked_by_turn`), and whether that
# turn wrote a valid outcome line stays on the turn (`turns.parse_error`), never on the delivery.
SCHEMA_6 = f"""
CREATE TABLE events_v6 (
  seq         INTEGER PRIMARY KEY AUTOINCREMENT,
  id          TEXT NOT NULL UNIQUE,
  {_ts("ts_system", nullable=False)},
  source      TEXT NOT NULL CHECK ({_in("source", SOURCES)}),
  kind        TEXT NOT NULL,
  subject_ref TEXT NOT NULL,
  actor       TEXT,
  {_ts("at")},
  payload     TEXT NOT NULL CHECK (json_valid(payload))
);
INSERT INTO events_v6 (seq, id, ts_system, source, kind, subject_ref, actor, at, payload) SELECT seq, id, ts_system, source, kind, subject_ref, actor, at, payload FROM events ORDER BY seq;
DROP TABLE events;
ALTER TABLE events_v6 RENAME TO events;
CREATE INDEX events_kind ON events(kind, seq);
CREATE INDEX events_subject ON events(subject_ref, seq);
CREATE TRIGGER events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'the ledger log is append-only'); END;

ALTER TABLE deliveries ADD COLUMN kind TEXT CHECK ({_in("kind", DELIVERY_KINDS, nullable=True)});
ALTER TABLE deliveries ADD COLUMN state TEXT NOT NULL DEFAULT 'created' CHECK ({_in("state", DELIVERY_STATES)});
ALTER TABLE deliveries ADD COLUMN run_id TEXT;
ALTER TABLE deliveries ADD COLUMN {_ts("next_attempt_at")};
ALTER TABLE deliveries ADD COLUMN last_error TEXT;
ALTER TABLE deliveries ADD COLUMN dedupe_key TEXT;
ALTER TABLE deliveries ADD COLUMN for_turn_id TEXT;
ALTER TABLE deliveries ADD COLUMN message_id TEXT;
ALTER TABLE deliveries ADD COLUMN acked_by_turn TEXT;
ALTER TABLE deliveries ADD COLUMN resent INTEGER NOT NULL DEFAULT 0 CHECK (resent IN (0, 1));
CREATE UNIQUE INDEX one_corrective_per_turn ON deliveries(for_turn_id) WHERE kind = 'corrective';
CREATE UNIQUE INDEX one_delivery_per_cause ON deliveries(dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX deliveries_open ON deliveries(to_role, created_at) WHERE state IN ('created', 'claimed', 'delivered', 'processing', 'failed');

CREATE TABLE role_sessions (
  id          TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  cwd         TEXT,
  created_by  TEXT NOT NULL DEFAULT 'adopted' CHECK (created_by IN ('adopted', 'onloop')),
  opted_in    TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(opted_in)),
  last_seq    INTEGER NOT NULL
);
CREATE UNIQUE INDEX one_role_per_session ON role_sessions(session_id);

CREATE TABLE session_leases (
  id                TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  role              TEXT NOT NULL,
  run_id            TEXT NOT NULL,
  holder_instance   TEXT NOT NULL,
  holder_pid        INTEGER,
  holder_started_at INTEGER,
  {_ts("granted_at", nullable=False)},
  {_ts("expires_at", nullable=False)},
  {_ts("renewed_at")},
  {_ts("released_at")},
  release_reason    TEXT,
  last_seq          INTEGER NOT NULL
);
CREATE UNIQUE INDEX one_live_session_lease ON session_leases(session_id) WHERE released_at IS NULL;

CREATE TABLE runs (
  id                TEXT PRIMARY KEY,
  role              TEXT NOT NULL,
  session_id        TEXT NOT NULL,
  lease_id          TEXT NOT NULL,
  pid               INTEGER,
  claude_pid        INTEGER,
  claude_started_at INTEGER,
  {_ts("started_at", nullable=False)},
  {_ts("ended_at")},
  exit_code         INTEGER,
  is_error          INTEGER CHECK (is_error IS NULL OR is_error IN (0, 1)),
  terminal_reason   TEXT,
  api_error_status  TEXT,
  denials           INTEGER NOT NULL DEFAULT 0,
  num_turns         INTEGER,
  wall_s            REAL,
  rate_limit        TEXT CHECK (rate_limit IS NULL OR json_valid(rate_limit)),
  stop_turn_id      TEXT,
  hook_missing      INTEGER NOT NULL DEFAULT 0 CHECK (hook_missing IN (0, 1)),
  status            TEXT NOT NULL DEFAULT 'running' CHECK ({_in("status", RUN_STATES)}),
  detail            TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(detail)),
  last_seq          INTEGER NOT NULL
);
CREATE INDEX runs_role ON runs(role, started_at);

CREATE TABLE messages (
  id          TEXT PRIMARY KEY,
  thread      TEXT NOT NULL,
  author      TEXT NOT NULL CHECK ({_in("author", MESSAGE_AUTHORS)}),
  channel     TEXT NOT NULL CHECK ({_in("channel", MESSAGE_CHANNELS)}),
  {_ts("created_at", nullable=False)},
  body        TEXT,
  body_sha256 TEXT NOT NULL CHECK (length(body_sha256) = 64),
  redacted    INTEGER NOT NULL DEFAULT 0,
  {_ts("expires_at", nullable=False)},
  {_ts("expired_at")},
  delivery_id TEXT,
  last_seq    INTEGER NOT NULL,
  CHECK ((expired_at IS NULL) = (body IS NOT NULL))
);
CREATE INDEX messages_thread ON messages(thread, created_at);

CREATE TABLE timers (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL CHECK ({_in("kind", TIMER_KINDS)}),
  subject      TEXT NOT NULL,
  {_ts("due_at", nullable=False)},
  {_ts("fired_at")},
  {_ts("cancelled_at")},
  detail       TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(detail)),
  last_seq     INTEGER NOT NULL
);
CREATE INDEX timers_due ON timers(due_at) WHERE fired_at IS NULL AND cancelled_at IS NULL;

CREATE TABLE carrier_state (
  id            INTEGER PRIMARY KEY CHECK (id = 1),
  enabled       INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  paused_reason TEXT CHECK ({_in("paused_reason", CARRIER_PAUSES, nullable=True)}),
  {_ts("paused_until")},
  set_by        TEXT,
  {_ts("changed_at")},
  last_seq      INTEGER NOT NULL
)
"""

MIGRATIONS: list[str] = [
    SCHEMA_1,
    SCHEMA_2,
    SCHEMA_3,
    SCHEMA_4,
    SCHEMA_5,
    SCHEMA_6,
]  # index + 1 is the version; a migration is never edited afterwards


def statements(script: str) -> list[str]:
    """Split on ';' at the end of a line; a trigger body keeps its inner ';' because it is followed by ' END'."""
    out, buf = [], []
    for line in script.strip().splitlines():
        buf.append(line)
        if line.rstrip().endswith(";"):
            out.append("\n".join(buf).strip().rstrip(";"))
            buf = []
    if "".join(buf).strip():
        out.append("\n".join(buf).strip())
    return out
