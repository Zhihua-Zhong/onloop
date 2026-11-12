"""Nodes of the work graph: what one is, and the vocabulary its fields take."""

from __future__ import annotations

import re
from typing import Any

KINDS = ("topic", "task", "decision", "hypothesis")
CLASSES = ("expedite", "standard", "background")
DEFAULT_CLASS = "standard"
DEFAULT_WEIGHT = 1.0

# States. A source maps its own words onto these; anything it cannot map stays `open`.
OPEN_STATES = ("open", "blocked", "waiting-human", "waiting-external")
CLOSED_STATES = ("done", "cancelled", "missing")
STATES = OPEN_STATES + CLOSED_STATES
WAITING_STATES = ("waiting-human", "waiting-external")
HUMAN_STATE = "waiting-human"

# Every field a node has. `id`, `source` and `created` never change after the node appears; the rest may.
FIELDS = (
    "id",
    "source",
    "kind",
    "title",
    "state",
    "class",
    "weight",
    "owner",
    "spawned_by",
    "depends_on",
    "created",
    "updated",
    "url",
    "default",
    "expires",
)
MUTABLE = tuple(f for f in FIELDS if f not in ("id", "source", "created"))

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class NodeError(ValueError):
    pass


def check_node(node: dict[str, Any]) -> dict[str, Any]:
    """A complete node with every field in its vocabulary, or NodeError naming what is wrong."""
    missing = [f for f in FIELDS if f not in node]
    if missing:
        raise NodeError(f"node {node.get('id')!r} lacks {', '.join(missing)}")
    extra = sorted(set(node) - set(FIELDS))
    if extra:
        raise NodeError(f"node {node['id']!r} has unknown fields {', '.join(extra)}")
    if not isinstance(node["id"], str) or not node["id"]:
        raise NodeError("a node needs a non-empty text id")
    if node["kind"] not in KINDS:
        raise NodeError(f"node {node['id']}: kind {node['kind']!r} is not one of {', '.join(KINDS)}")
    if node["state"] not in STATES:
        raise NodeError(f"node {node['id']}: state {node['state']!r} is not one of {', '.join(STATES)}")
    if node["class"] not in CLASSES:
        raise NodeError(f"node {node['id']}: class {node['class']!r} is not one of {', '.join(CLASSES)}")
    if (
        not isinstance(node["weight"], (int, float))
        or isinstance(node["weight"], bool)
        or node["weight"] <= 0
    ):
        raise NodeError(f"node {node['id']}: weight must be a positive number")
    if not isinstance(node["depends_on"], list) or not all(isinstance(d, str) for d in node["depends_on"]):
        raise NodeError(f"node {node['id']}: depends_on must be a list of node ids")
    if node["spawned_by"] == node["id"]:
        raise NodeError(f"node {node['id']} cannot be its own parent")
    return node


def is_open(node: dict[str, Any]) -> bool:
    return node["state"] in OPEN_STATES


def parse_duration(value: Any) -> float:
    """Seconds from `90m`, `2h`, `7d`, `1w`, or a bare number of seconds."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    m = _DURATION.match(str(value))
    if not m or float(m.group(1)) <= 0:
        raise NodeError(f"not a duration: {value!r} (write it as 2h, 24h, 7d)")
    return float(m.group(1)) * _UNIT[m.group(2)]
