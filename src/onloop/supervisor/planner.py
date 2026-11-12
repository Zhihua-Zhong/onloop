"""Plain functions over the state directory: plan and attend. The command line calls them.

    read_snapshot(cfg) -> Snapshot | None                       the last snapshot.json, if there is one
    queues_of(cfg, snap) -> {role: [item]}                      each role's queue, with the asks drained from the snapshot
    plan_once(cfg, snap, records, mode, now) -> Plan            what would start now; `onloop supervise --once` prints it
    attention_state(cfg, snap, plan, view, heartbeat) -> dict   what the attention rules read

The state directory holds `steps.jsonl`, `alerts.jsonl`, `heartbeat.json` and `snapshot.json`. These functions write
nothing.
"""

from __future__ import annotations

from typing import Any

from . import asks as asks_mod
from . import schedule, sources, steplog
from .config import SupervisorConfig
from .items import WorkItem, queue_for

HEARTBEAT = "heartbeat.json"
STEPS = "steps.jsonl"
ALERTS = "alerts.jsonl"
SNAPSHOT = "snapshot.json"


def read_snapshot(cfg: SupervisorConfig) -> sources.Snapshot | None:
    raw = steplog.read_json(cfg.state_dir / SNAPSHOT)
    return sources.Snapshot.from_dict(raw) if isinstance(raw, dict) else None


def queues_of(cfg: SupervisorConfig, snap: sources.Snapshot) -> dict[str, list[WorkItem]]:
    drained = asks_mod.drain_asks(snap.asks, snap.open_ids)
    return {role.name: queue_for(role, snap.items, drained.get(role.name, [])) for role in cfg.roles}


def plan_once(
    cfg: SupervisorConfig, snap: sources.Snapshot, records: list[dict[str, Any]], mode: str, now: str
) -> schedule.Plan:
    view = steplog.step_view(records, mode)
    return schedule.plan_steps(cfg.roles, queues_of(cfg, snap), view, now, max_parallel=cfg.max_parallel)


def attention_state(
    cfg: SupervisorConfig, snap: sources.Snapshot, plan: schedule.Plan, view: dict[str, Any], heartbeat: Any
) -> dict[str, Any]:
    """What the attention rules read: the asks, each role's due work and last step, and the heartbeat."""
    return {
        "asks": snap.asks,
        "roles": {
            r.name: {
                "pending": plan.pending.get(r.name, {}).get("count", 0),
                "pending_since": plan.pending.get(r.name, {}).get("since"),
                "last_step_at": view["last_start"].get(r.name),
            }
            for r in cfg.roles
        },
        "heartbeat": heartbeat,
    }
