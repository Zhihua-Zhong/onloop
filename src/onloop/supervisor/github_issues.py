"""The GitHub Issues adapter: reads a repository's issues and turns each into a work-graph node. Read only.

What the labels mean, and how a child names its parent, is the instance's configuration (`SourceConfig`), never this
module's: one repository writes `kind/topic`, another `type: epic`. The mapping is a pure function of the issue JSON
(`node_of`), so a test feeds it recorded JSON and no network.

Parents, in order of authority:
1. GitHub's own sub-issues: an issue whose `sub_issues_summary.total` is above zero has its children listed by
   `GET /repos/{repo}/issues/{n}/sub_issues`. Only those issues are asked, so a repository without sub-issues costs no call.
2. A line in the child's body matching one of `parent.body_patterns` (the first group is the parent's number).

`depends_on` comes from GitHub's issue dependencies (`blocked_by`, asked only when `issue_dependencies_summary` says
there are some) and from body lines matching `depends_on.body_patterns`, from which every `#N` is taken.

The only calls are `gh api` GETs. Nothing is written to GitHub.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import events as ev
from .. import github
from ..github import GhRunner
from . import nodes as model


class AdapterError(RuntimeError):
    pass


@dataclass
class SourceConfig:
    repo: str
    name: str = ""  # the source name in the store; default "github:<repo>"
    id_prefix: str = "#"
    role_prefix: str = "role/"
    kind_labels: dict[str, str] = field(default_factory=dict)  # label -> kind, first match in this order wins
    state_labels: dict[str, str] = field(default_factory=dict)  # label -> open state, first match wins
    class_labels: dict[str, str] = field(default_factory=dict)  # label -> class
    weight_labels: dict[str, float] = field(default_factory=dict)  # label -> weight, the largest wins
    closed_states: dict[str, str] = field(
        default_factory=lambda: {"completed": "done", "not_planned": "cancelled", "duplicate": "cancelled"}
    )
    closed_default: str = "done"
    use_sub_issues: bool = True
    use_dependencies: bool = True
    parent_patterns: list[str] = field(default_factory=list)
    depends_patterns: list[str] = field(default_factory=list)
    default_pattern: str = r"(?im)^\s*Default:\s*(.+?)\s*$"
    expires_pattern: str = r"(?im)^\s*Expires:\s*(.+?)\s*$"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repo or ""):
            raise AdapterError(f"source repo must be OWNER/NAME, not {self.repo!r}")
        self.name = self.name or f"github:{self.repo}"
        for label, kind in self.kind_labels.items():
            if kind not in model.KINDS:
                raise AdapterError(
                    f"kind label {label!r} maps to {kind!r}, not one of {', '.join(model.KINDS)}"
                )
        for label, state in self.state_labels.items():
            if state not in model.OPEN_STATES:
                raise AdapterError(
                    f"state label {label!r} maps to {state!r}, not one of {', '.join(model.OPEN_STATES)}"
                )
        for label, cls in self.class_labels.items():
            if cls not in model.CLASSES:
                raise AdapterError(
                    f"class label {label!r} maps to {cls!r}, not one of {', '.join(model.CLASSES)}"
                )
        for reason, state in {**self.closed_states, "default": self.closed_default}.items():
            if state not in model.CLOSED_STATES:
                raise AdapterError(
                    f"closed state {reason!r} maps to {state!r}, not one of {', '.join(model.CLOSED_STATES)}"
                )
        for label, w in self.weight_labels.items():
            if not isinstance(w, (int, float)) or isinstance(w, bool) or w <= 0:
                raise AdapterError(f"weight label {label!r} needs a positive number")
        self._parent = [self._compile(p, "parent") for p in self.parent_patterns]
        self._depends = [self._compile(p, "depends_on") for p in self.depends_patterns]
        self._default = self._compile(self.default_pattern, "default")
        self._expires = self._compile(self.expires_pattern, "expires")

    @staticmethod
    def _compile(pattern: str, what: str) -> re.Pattern[str]:
        try:
            return re.compile(pattern)
        except re.error as e:
            raise AdapterError(f"the {what} pattern {pattern!r} is not a regular expression: {e}") from e

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceConfig:
        labels = raw.get("labels") or {}
        parent = raw.get("parent") or {}
        depends = raw.get("depends_on") or {}
        fields = raw.get("fields") or {}
        closed = dict(raw.get("closed_states") or {})
        kw: dict[str, Any] = {
            "repo": raw.get("repo", ""),
            "name": raw.get("name", ""),
            "id_prefix": raw.get("id_prefix", "#"),
            "role_prefix": labels.get("role_prefix", "role/"),
            "kind_labels": dict(labels.get("kind") or {}),
            "state_labels": dict(labels.get("state") or {}),
            "class_labels": dict(labels.get("class") or {}),
            "weight_labels": dict(labels.get("weight") or {}),
            "use_sub_issues": bool(parent.get("sub_issues", True)),
            "use_dependencies": bool(depends.get("dependencies", True)),
            "parent_patterns": list(parent.get("body_patterns") or []),
            "depends_patterns": list(depends.get("body_patterns") or []),
        }
        if closed:
            kw["closed_default"] = closed.pop("default", "done")
            kw["closed_states"] = closed
        if "default" in fields:
            kw["default_pattern"] = fields["default"]
        if "expires" in fields:
            kw["expires_pattern"] = fields["expires"]
        unknown = sorted(
            set(raw)
            - {
                "type",
                "repo",
                "name",
                "id_prefix",
                "labels",
                "parent",
                "depends_on",
                "fields",
                "closed_states",
            }
        )
        if unknown:
            raise AdapterError(f"unknown source keys: {', '.join(unknown)}")
        return cls(**kw)

    def node_id(self, number: int | str) -> str:
        return f"{self.id_prefix}{number}"


# -- the pure mapping -----------------------------------------------------------------------------------------------


def _labels(issue: dict[str, Any]) -> list[str]:
    return [(lb.get("name") if isinstance(lb, dict) else str(lb)) or "" for lb in issue.get("labels") or []]


def _first(mapping: dict[str, Any], labels: list[str]) -> Any:
    for label, value in mapping.items():  # the configuration's order decides, not the issue's
        if label in labels:
            return value
    return None


def _match(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    if not m:
        return None
    return (m.group(1) if m.groups() else m.group(0)).strip() or None


def body_parent(cfg: SourceConfig, body: str) -> str | None:
    for p in cfg._parent:
        m = p.search(body)
        if m and m.groups() and m.group(1):
            return cfg.node_id(int(m.group(1)))
    return None


def body_depends(cfg: SourceConfig, body: str) -> list[str]:
    out: list[str] = []
    for p in cfg._depends:
        for m in p.finditer(body):
            for n in re.findall(r"#(\d+)", m.group(0)):
                nid = cfg.node_id(int(n))
                if nid not in out:
                    out.append(nid)
    return out


def node_of(
    cfg: SourceConfig,
    issue: dict[str, Any],
    *,
    parent: str | None = None,
    blocked_by: list[int] | None = None,
) -> dict[str, Any]:
    """One issue as a node. `parent` and `blocked_by` come from GitHub's own links when there are any."""
    labels = _labels(issue)
    body = issue.get("body") or ""
    nid = cfg.node_id(issue["number"])
    if issue.get("state") == "closed":
        state = cfg.closed_states.get(issue.get("state_reason") or "", cfg.closed_default)
    else:
        state = _first(cfg.state_labels, labels) or "open"
    roles = [
        lb[len(cfg.role_prefix) :] for lb in labels if cfg.role_prefix and lb.startswith(cfg.role_prefix)
    ]
    weights = [float(cfg.weight_labels[lb]) for lb in labels if lb in cfg.weight_labels]
    spawned_by = parent or body_parent(cfg, body)
    if spawned_by == nid:
        spawned_by = None
    depends = [cfg.node_id(n) for n in blocked_by or []]
    for d in body_depends(cfg, body):
        if d not in depends and d != nid:
            depends.append(d)
    url = issue.get("html_url") or ""
    return model.check_node(
        {
            "id": nid,
            "source": cfg.name,
            "kind": _first(cfg.kind_labels, labels) or "task",
            "title": issue.get("title") or "",
            "state": state,
            "class": _first(cfg.class_labels, labels) or model.DEFAULT_CLASS,
            "weight": max(weights) if weights else model.DEFAULT_WEIGHT,
            "owner": sorted(roles)[0] if roles else None,
            "spawned_by": spawned_by,
            "depends_on": depends,
            "created": ev.norm_ts(issue.get("created_at")),
            "updated": ev.norm_ts(issue.get("updated_at")),
            "url": url if url.startswith("https://") else None,
            "default": _match(cfg._default, body),
            "expires": _match(cfg._expires, body),
        }
    )


def nodes_of(
    cfg: SourceConfig,
    issues: list[dict[str, Any]],
    sub_issues: dict[int, list[int]] | None = None,
    blocked_by: dict[int, list[int]] | None = None,
) -> list[dict[str, Any]]:
    """Every issue (pull requests skipped) as a node, sorted by number. `sub_issues` maps a parent number to its
    children's numbers; `blocked_by` maps an issue number to the numbers it is blocked by."""
    parent_of: dict[int, int] = {}
    for p, children in sorted((sub_issues or {}).items()):
        for c in children:
            parent_of.setdefault(c, p)
    out = []
    for issue in sorted((i for i in issues if "pull_request" not in i), key=lambda i: i["number"]):
        n = issue["number"]
        parent = cfg.node_id(parent_of[n]) if n in parent_of else None
        out.append(node_of(cfg, issue, parent=parent, blocked_by=(blocked_by or {}).get(n)))
    return out


# -- reading GitHub -------------------------------------------------------------------------------------------------


def _get(runner: GhRunner | None, path: str) -> list[Any]:
    try:
        return github.get_list(path, runner)
    except github.GitHubError as e:
        raise AdapterError(str(e)) from e


def fetch(cfg: SourceConfig, runner: GhRunner | None = None) -> dict[str, Any]:
    """Everything the mapping needs, read with GETs only: {issues, sub_issues, blocked_by}. Raises on any failed read, so a
    partial picture is never synced as a complete one."""
    run = runner
    issues = _get(run, f"repos/{cfg.repo}/issues?state=all&per_page=100")
    sub: dict[int, list[int]] = {}
    blocked: dict[int, list[int]] = {}
    for i in issues:
        if "pull_request" in i:
            continue
        n = i["number"]
        if cfg.use_sub_issues and (i.get("sub_issues_summary") or {}).get("total"):
            sub[n] = [c["number"] for c in _get(run, f"repos/{cfg.repo}/issues/{n}/sub_issues?per_page=100")]
        if cfg.use_dependencies and (i.get("issue_dependencies_summary") or {}).get("blocked_by"):
            blocked[n] = [
                c["number"]
                for c in _get(run, f"repos/{cfg.repo}/issues/{n}/dependencies/blocked_by?per_page=100")
            ]
    return {"issues": issues, "sub_issues": sub, "blocked_by": blocked}


def observe(cfg: SourceConfig, runner: GhRunner | None = None) -> list[dict[str, Any]]:
    data = fetch(cfg, runner)
    return nodes_of(cfg, data["issues"], data["sub_issues"], data["blocked_by"])
