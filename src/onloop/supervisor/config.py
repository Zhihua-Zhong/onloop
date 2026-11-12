"""The supervisor's configuration: the role table, the event source, the person's task queue and the attention rules.

One YAML file. Relative paths (the state directory, a role's working directory, a charter, a fixture) are read from
the configuration file's own folder, so an instance folder can move as one piece. Every key is checked: an unknown key
is an error, not a silent default. See examples/supervisor/supervisor.yaml for a complete file.

    state_dir: .onloop/supervisor          # step log, heartbeat, alerts, the last snapshot
    claude: [claude]                       # how to start Claude Code (a list: the executable and any fixed prefix)
    interval: 60s                          # read but unused since the Temporal layer was removed (decision 0003)
    jitter: 10s                            # read but unused, as interval
    max_parallel: 2                        # steps running at once on one worker, over all roles
    stop_grace: 30s                        # read but unused, as interval
    source: {type: github, repo: OWNER/NAME, ledger: ledger.yaml}
    asks: {label: kind/ask, ...}
    roles: [{name, cwd, permission_mode, allowed_tools, prompt, budget, wip, every, retry, triggers}]
    attention: {item_stale: {threshold: 24h, quiet: 12h}, ..., max_repeats: 3}
    notifier: {type: log}
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import string
from dataclasses import dataclass, field
from typing import Any

import yaml

from . import nodes as wg_model

# `bypassPermissions` is left out on purpose: a role that may do anything without asking is a widening of authority,
# and the supervisor will not start one. The other modes are the ones `claude --help` lists.
PERMISSION_MODES = ("dontAsk", "acceptEdits", "plan", "auto", "manual")
ITEM_KINDS = ("pr", "issue", "ask", "ledger")
SOURCE_TYPES = ("github", "fixture")
NOTIFIER_TYPES = ("log", "stub", "webhook", "desktop")
ATTENTION_RULES = ("item_stale", "waiting_on_person", "role_starved", "heartbeat_stale")
# What a prompt template may name, as $name or ${name}. Item text from GitHub arrives through these and is data.
PROMPT_VARS = frozenset(
    {
        "role",
        "repo",
        "step_id",
        "item_id",
        "item_kind",
        "item_number",
        "item_title",
        "item_url",
        "item_state",
        "item_head",
        "item_head_short",
        "done_when",
        "queue_size",
        "charter",
    }
)
# What a role's result_file path may name: identifiers only, never text from the tracker.
RESULT_FILE_VARS = frozenset({"role", "step_id", "item_kind", "item_number", "item_head", "item_head_short"})
# What a role's after_result command may name: those, and the result just written (the .md and its .md.json).
AFTER_RESULT_VARS = RESULT_FILE_VARS | {"result_file", "result_json"}
SETTING_SOURCES = ("user", "project", "local")
CHECK_NAMES = ("ruleset", "all")
VERSIONS = ("updated", "head")
REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
_LOGIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}(\[bot\])?$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ConfigError(ValueError):
    pass


def _dur(value: Any, what: str) -> float:
    try:
        return wg_model.parse_duration(value)
    except wg_model.NodeError as e:
        raise ConfigError(f"{what}: {e}") from e


def _keys(raw: Any, allowed: set[str], what: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{what} must be a mapping")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ConfigError(f"{what}: unknown keys {', '.join(map(str, unknown))}")
    return raw


def _strs(raw: Any, what: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(s, str) and s for s in raw):
        raise ConfigError(f"{what} must be a list of non-empty strings")
    return tuple(raw)


def _int(raw: Any, what: str, lo: int, hi: int) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or not lo <= raw <= hi:
        raise ConfigError(f"{what} must be a whole number from {lo} to {hi}")
    return raw


def _path(base: pathlib.Path, raw: Any) -> pathlib.Path:
    p = pathlib.Path(str(raw)).expanduser()
    return p if p.is_absolute() else (base / p)


@dataclass(frozen=True)
class AnswerSpec:
    """What counts as an answer on a pull request's current head, from one GitHub login.

    A review by `login` on exactly the head sha, in one of `review_states`, answers it. So does an issue comment by
    `login` whose body contains `comment_marker` and the full head sha. Answers on an earlier head do not count."""

    login: str
    review_states: tuple[str, ...] = ("APPROVED", "CHANGES_REQUESTED")
    comment_marker: str | None = None

    @property
    def key(self) -> str:
        return f"{self.login}|{','.join(self.review_states)}|{self.comment_marker or ''}"

    @classmethod
    def from_dict(cls, raw: Any, where: str) -> AnswerSpec:
        if isinstance(raw, str):
            raw = {"login": raw}
        raw = _keys(raw, {"login", "review_states", "comment_marker"}, where)
        login = raw.get("login")
        if not isinstance(login, str) or not _LOGIN.match(login):
            raise ConfigError(f"{where}: login must be a GitHub login")
        states = _strs(raw.get("review_states"), f"{where}.review_states") or cls.review_states
        if set(states) - set(REVIEW_STATES):
            raise ConfigError(f"{where}: review_states must be from {', '.join(REVIEW_STATES)}")
        marker = raw.get("comment_marker")
        if marker is not None and (not isinstance(marker, str) or not marker):
            raise ConfigError(f"{where}: comment_marker must be a non-empty string")
        return cls(login, tuple(states), marker)


@dataclass(frozen=True)
class Trigger:
    """Which work items belong in a role's queue. An item is in the queue when it matches any one trigger."""

    kind: str
    labels_any: tuple[str, ...] = ()  # the item carries at least one of these (empty: no condition)
    labels_none: tuple[str, ...] = ()  # the item carries none of these
    draft: bool | None = None  # pull requests: only drafts (true) or only ready ones (false)
    owned: bool = False  # pull requests and issues: only those whose role label names this role
    version: str = "updated"  # pull requests: a step per change (updated) or per head commit (head)
    unanswered_by: AnswerSpec | None = (
        None  # pull requests: only those whose current head this login has not answered
    )
    require_checks: str | None = None  # pull requests: "ruleset" or "all": only heads whose checks are green

    @classmethod
    def from_dict(cls, raw: Any, where: str) -> Trigger:
        raw = _keys(
            raw,
            {"kind", "labels_any", "labels_none", "draft", "owned", "version", "unanswered_by", "require"},
            where,
        )
        kind = raw.get("kind")
        if kind not in ITEM_KINDS:
            raise ConfigError(f"{where}: kind must be one of {', '.join(ITEM_KINDS)}")
        draft = raw.get("draft")
        if draft is not None and (not isinstance(draft, bool) or kind != "pr"):
            raise ConfigError(f"{where}: draft is true or false, and only for kind pr")
        owned = raw.get("owned", False)
        if not isinstance(owned, bool):
            raise ConfigError(f"{where}: owned is true or false")
        version = raw.get("version", "updated")
        if version not in VERSIONS or (version != "updated" and kind != "pr"):
            raise ConfigError(f"{where}: version is one of {', '.join(VERSIONS)}, and head only for kind pr")
        answered = raw.get("unanswered_by")
        if answered is not None and kind != "pr":
            raise ConfigError(f"{where}: unanswered_by is only for kind pr")
        require = _keys(raw.get("require"), {"checks", "names"}, f"{where}.require")
        checks = None
        if require:
            if kind != "pr" or require.get("checks") != "green":
                raise ConfigError(f"{where}: require is only for kind pr, as {{checks: green}}")
            checks = require.get("names", "ruleset")
            if checks not in CHECK_NAMES:
                raise ConfigError(f"{where}: require.names is one of {', '.join(CHECK_NAMES)}")
        return cls(
            kind,
            _strs(raw.get("labels_any"), f"{where}.labels_any"),
            _strs(raw.get("labels_none"), f"{where}.labels_none"),
            draft,
            owned,
            version,
            AnswerSpec.from_dict(answered, f"{where}.unanswered_by") if answered is not None else None,
            checks,
        )


@dataclass(frozen=True)
class RoleSpec:
    name: str
    cwd: pathlib.Path
    permission_mode: str
    allowed_tools: tuple[str, ...]
    prompt: str  # a string.Template: $item_id, ${item_title}, ...
    wall: float  # step budget: wall seconds
    turns: int  # step budget: agent turns (`--max-turns`)
    silence: float  # step budget: longest stretch with no output before the step is stalled
    wip: int  # steps of this role running at once
    triggers: tuple[Trigger, ...]
    every: float | None = None  # look at a handled item again after this long, if it is still queued
    retry_attempts: int = 2  # attempts per step id before it is given up
    retry_after: float = 900.0  # wait after a failed attempt (its initial interval)
    model: str | None = None
    charter: pathlib.Path | None = None  # a file whose text the template may include as $charter
    priority_paths: tuple[str, ...] = ()  # pull requests whose changed paths match these go first
    priority_when: str = "all"  # all: every changed path matches one; any: at least one does
    setting_sources: tuple[str, ...] = ("user",)  # Claude Code settings the step loads (`--setting-sources`)
    result_schema: str | None = None  # a JSON Schema (as JSON text) for the step's structured result
    result_file: str | None = None  # where the supervisor writes the step's result, from beside the config
    result_base: pathlib.Path | None = None
    after_result: tuple[str, ...] = ()  # a command run on the result of a live step that ended ok
    after_timeout: float = 120.0  # its time limit

    @classmethod
    def from_dict(cls, raw: Any, base: pathlib.Path, where: str) -> RoleSpec:
        raw = _keys(
            raw,
            {
                "name",
                "cwd",
                "permission_mode",
                "allowed_tools",
                "prompt",
                "prompt_file",
                "budget",
                "wip",
                "every",
                "retry",
                "model",
                "charter",
                "triggers",
                "priority_paths",
                "priority_when",
                "setting_sources",
                "result_schema",
                "result_file",
                "after_result",
            },
            where,
        )
        name = raw.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise ConfigError(f"{where}: name must be lowercase letters, digits, - or _ (got {name!r})")
        where = f"role {name}"
        mode = raw.get("permission_mode", "dontAsk")
        if mode == "bypassPermissions":
            raise ConfigError(
                f"{where}: permission_mode bypassPermissions is refused; a role's tools are listed "
                "in allowed_tools instead"
            )
        if mode not in PERMISSION_MODES:
            raise ConfigError(f"{where}: permission_mode must be one of {', '.join(PERMISSION_MODES)}")
        tools = _strs(raw.get("allowed_tools"), f"{where}.allowed_tools")
        for t in tools:
            if "," in t or t != t.strip():
                raise ConfigError(
                    f"{where}: tool {t!r} may not contain a comma or surrounding spaces "
                    "(the list is passed as one comma-separated argument)"
                )
        prompt = raw.get("prompt")
        if raw.get("prompt_file") is not None:
            if prompt is not None:
                raise ConfigError(f"{where}: give prompt or prompt_file, not both")
            pf = _path(base, raw["prompt_file"])
            try:
                prompt = pf.read_text(encoding="utf-8")
            except OSError as e:
                raise ConfigError(f"{where}: prompt_file {pf} could not be read: {type(e).__name__}") from e
        if not isinstance(prompt, str) or not prompt.strip():
            raise ConfigError(f"{where}: prompt (or prompt_file) is required")
        tpl = string.Template(prompt)
        if not tpl.is_valid():
            raise ConfigError(
                f"{where}: prompt has a malformed $ placeholder (write $$ for a literal dollar)"
            )
        unknown = sorted(set(tpl.get_identifiers()) - PROMPT_VARS)
        if unknown:
            raise ConfigError(
                f"{where}: prompt names unknown variables {', '.join(unknown)}; "
                f"known: {', '.join(sorted(PROMPT_VARS))}"
            )
        budget = _keys(raw.get("budget"), {"wall", "turns", "silence"}, f"{where}.budget")
        if "wall" not in budget or "turns" not in budget:
            raise ConfigError(f"{where}: budget needs wall (a duration) and turns (a number)")
        wall = _dur(budget["wall"], f"{where}.budget.wall")
        silence = (
            _dur(budget["silence"], f"{where}.budget.silence") if "silence" in budget else min(wall, 600.0)
        )
        if silence > wall:
            raise ConfigError(f"{where}: budget.silence cannot be longer than budget.wall")
        retry = _keys(raw.get("retry"), {"attempts", "after"}, f"{where}.retry")
        triggers = raw.get("triggers")
        if not isinstance(triggers, list) or not triggers:
            raise ConfigError(f"{where}: triggers must be a non-empty list")
        model = raw.get("model")
        if model is not None and (not isinstance(model, str) or not model or model.startswith("-")):
            raise ConfigError(f"{where}: model must be a model name")
        sources_ = (
            _strs(raw.get("setting_sources"), f"{where}.setting_sources")
            if "setting_sources" in raw
            else ("user",)
        )
        if not sources_ or set(sources_) - set(SETTING_SOURCES):
            raise ConfigError(
                f"{where}: setting_sources is a non-empty list from {', '.join(SETTING_SOURCES)}"
            )
        schema = None
        if raw.get("result_schema") is not None:
            sp = _path(base, raw["result_schema"])
            try:
                parsed = json.loads(sp.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise ConfigError(f"{where}: result_schema {sp} is not a readable JSON file: {e}") from e
            if not isinstance(parsed, dict):
                raise ConfigError(f"{where}: result_schema must be a JSON object (a JSON Schema)")
            schema = json.dumps(parsed, separators=(",", ":"), sort_keys=True)
        when = raw.get("priority_when", "all")
        if when not in ("all", "any"):
            raise ConfigError(f"{where}: priority_when is all or any")
        result_file = raw.get("result_file")
        if result_file is not None:
            rt = string.Template(str(result_file))
            if not rt.is_valid() or set(rt.get_identifiers()) - RESULT_FILE_VARS:
                raise ConfigError(f"{where}: result_file may name only {', '.join(sorted(RESULT_FILE_VARS))}")
            if (
                pathlib.PurePath(str(result_file)).is_absolute()
                or ".." in pathlib.PurePath(str(result_file)).parts
            ):
                raise ConfigError(
                    f"{where}: result_file must be a relative path inside the configuration's folder"
                )
        after, after_timeout = (), 120.0
        if raw.get("after_result") is not None:
            ar = _keys(raw["after_result"], {"command", "timeout"}, f"{where}.after_result")
            after = _strs(ar.get("command"), f"{where}.after_result.command")
            if not after:
                raise ConfigError(f"{where}: after_result.command is a program and its arguments, as a list")
            if result_file is None:
                raise ConfigError(f"{where}: after_result needs result_file, the file it is run on")
            for arg in after:
                at = string.Template(arg)
                if not at.is_valid() or set(at.get_identifiers()) - AFTER_RESULT_VARS:
                    raise ConfigError(
                        f"{where}: after_result.command may name only {', '.join(sorted(AFTER_RESULT_VARS))}"
                    )
            after_timeout = _dur(ar.get("timeout", "2m"), f"{where}.after_result.timeout")
        return cls(
            name=name,
            cwd=_path(base, raw.get("cwd", ".")),
            permission_mode=mode,
            allowed_tools=tools,
            prompt=prompt,
            wall=wall,
            turns=_int(budget["turns"], f"{where}.budget.turns", 1, 500),
            silence=silence,
            wip=_int(raw.get("wip", 1), f"{where}.wip", 1, 16),
            triggers=tuple(Trigger.from_dict(t, f"{where}.triggers[{i}]") for i, t in enumerate(triggers)),
            every=_dur(raw["every"], f"{where}.every") if raw.get("every") is not None else None,
            retry_attempts=_int(retry.get("attempts", 2), f"{where}.retry.attempts", 1, 10),
            retry_after=_dur(retry.get("after", "15m"), f"{where}.retry.after"),
            model=model,
            charter=_path(base, raw["charter"]) if raw.get("charter") else None,
            priority_paths=_strs(raw.get("priority_paths"), f"{where}.priority_paths"),
            priority_when=when,
            setting_sources=sources_,
            result_schema=schema,
            result_file=str(result_file) if result_file is not None else None,
            result_base=base,
            after_result=after,
            after_timeout=after_timeout,
        )


@dataclass(frozen=True)
class SourceSpec:
    type: str
    repo: str = ""
    fixture: pathlib.Path | None = None  # type fixture: a JSON file read on every look (see sources.py)
    ledger: pathlib.Path | None = None  # optional: ledger.yaml, whose queues feed `kind: ledger` triggers
    # Derived from the role table, never configured: whether to read open pull requests' head commits (a trigger uses
    # version: head or unanswered_by), and which answers to look for on each one's head.
    heads: bool = False
    answers: tuple[AnswerSpec, ...] = ()
    # Also derived: the (role, trigger) pairs whose pull requests need their checks (a trigger with `require`) or their
    # changed paths (a role with `priority_paths`), and every priority glob.
    watch: tuple[tuple[str, Trigger], ...] = ()
    checks: bool = False
    globs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AsksConfig:
    """The person's task queue. Its items are GitHub issues carrying `label`, plus an optional private file."""

    label: str | None = None
    mapping: dict[str, Any] = field(
        default_factory=dict
    )  # the work graph's issue mapping: labels, depends_on, fields
    done_when_pattern: str = r"(?im)^\s*Done when:\s*(.+?)\s*$"
    private_file: pathlib.Path | None = None  # local only: never written to GitHub or the shared log


@dataclass(frozen=True)
class AttentionRule:
    name: str
    threshold: float  # seconds, or for waiting_on_person a count of items
    quiet: float  # seconds between two notices for the same subject


@dataclass(frozen=True)
class AttentionConfig:
    rules: dict[str, AttentionRule]
    max_repeats: int = 3  # notices per subject while its condition holds; reset when it clears


@dataclass(frozen=True)
class NotifierSpec:
    type: str = "log"
    url: str | None = None
    url_env: str | None = None  # read the webhook URL from this variable, so it is not in the file


DEFAULT_ATTENTION = {
    "item_stale": ("24h", "12h"),
    "waiting_on_person": (1, "4h"),
    "role_starved": ("30m", "1h"),
    "heartbeat_stale": ("10m", "30m"),
}


@dataclass(frozen=True)
class SupervisorConfig:
    path: pathlib.Path
    state_dir: pathlib.Path
    claude: tuple[str, ...]
    interval: float
    jitter: float
    max_parallel: int
    stop_grace: float
    source: SourceSpec
    asks: AsksConfig
    roles: tuple[RoleSpec, ...]
    attention: AttentionConfig
    notifier: NotifierSpec

    def role(self, name: str) -> RoleSpec:
        for r in self.roles:
            if r.name == name:
                return r
        raise KeyError(name)


def _source(raw: Any, base: pathlib.Path) -> SourceSpec:
    raw = _keys(raw, {"type", "repo", "fixture", "ledger"}, "source")
    kind = raw.get("type")
    if kind not in SOURCE_TYPES:
        raise ConfigError(f"source.type must be one of {', '.join(SOURCE_TYPES)}")
    repo = str(raw.get("repo") or "")
    if kind == "github" and not _REPO.match(repo):
        raise ConfigError("source.repo must be OWNER/NAME for a github source")
    if kind == "fixture" and not raw.get("fixture"):
        raise ConfigError("source.fixture is required for a fixture source")
    return SourceSpec(
        kind,
        repo,
        _path(base, raw["fixture"]) if raw.get("fixture") else None,
        _path(base, raw["ledger"]) if raw.get("ledger") else None,
    )


def _asks(raw: Any, base: pathlib.Path) -> AsksConfig:
    raw = _keys(raw, {"label", "labels", "depends_on", "fields", "private_file"}, "asks")
    label = raw.get("label")
    if label is not None and (not isinstance(label, str) or not label):
        raise ConfigError("asks.label must be a label name")
    fields = dict(_keys(raw.get("fields"), {"default", "expires", "done_when"}, "asks.fields"))
    done_when = fields.pop("done_when", AsksConfig.done_when_pattern)
    try:
        re.compile(done_when)
    except re.error as e:
        raise ConfigError(f"asks.fields.done_when is not a regular expression: {e}") from e
    mapping = {k: raw[k] for k in ("labels", "depends_on") if k in raw}
    if fields:
        mapping["fields"] = fields
    return AsksConfig(
        label, mapping, done_when, _path(base, raw["private_file"]) if raw.get("private_file") else None
    )


def _attention(raw: Any) -> AttentionConfig:
    raw = _keys(raw, set(ATTENTION_RULES) | {"max_repeats"}, "attention")
    rules = {}
    for name, (thr, quiet) in DEFAULT_ATTENTION.items():
        given = _keys(raw.get(name), {"threshold", "quiet"}, f"attention.{name}")
        t = given.get("threshold", thr)
        if name == "waiting_on_person":
            threshold = float(_int(t, "attention.waiting_on_person.threshold", 1, 1000))
        else:
            threshold = _dur(t, f"attention.{name}.threshold")
        rules[name] = AttentionRule(
            name, threshold, _dur(given.get("quiet", quiet), f"attention.{name}.quiet")
        )
    return AttentionConfig(rules, _int(raw.get("max_repeats", 3), "attention.max_repeats", 1, 100))


def _notifier(raw: Any) -> NotifierSpec:
    raw = _keys(raw, {"type", "url", "url_env"}, "notifier")
    kind = raw.get("type", "log")
    if kind not in NOTIFIER_TYPES:
        raise ConfigError(f"notifier.type must be one of {', '.join(NOTIFIER_TYPES)}")
    url = raw.get("url")
    if kind == "webhook" and not (url or raw.get("url_env")):
        raise ConfigError("a webhook notifier needs url or url_env")
    return NotifierSpec(kind, url, raw.get("url_env"))


def from_dict(raw: Any, path: str | pathlib.Path = "supervisor.yaml") -> SupervisorConfig:
    p = pathlib.Path(path)
    base = p.resolve().parent
    raw = _keys(
        raw,
        {
            "state_dir",
            "claude",
            "interval",
            "jitter",
            "max_parallel",
            "stop_grace",
            "source",
            "asks",
            "roles",
            "attention",
            "notifier",
        },
        str(p),
    )
    claude = raw.get("claude", ["claude"])
    if isinstance(claude, str):
        claude = [claude]
    claude_t = _strs(claude, "claude")
    if not claude_t:
        raise ConfigError("claude must name the executable")
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, list) or not roles_raw:
        raise ConfigError("roles must be a non-empty list")
    roles = tuple(RoleSpec.from_dict(r, base, f"roles[{i}]") for i, r in enumerate(roles_raw))
    names = [r.name for r in roles]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ConfigError(f"roles named twice: {', '.join(dupes)}")
    source = _source(raw.get("source"), base)
    trig = [t for r in roles for t in r.triggers]
    answers = tuple(dict.fromkeys(t.unanswered_by for t in trig if t.unanswered_by is not None))
    watch = tuple(
        (r.name, t)
        for r in roles
        for t in r.triggers
        if t.kind == "pr" and (t.require_checks or r.priority_paths)
    )
    globs = tuple(dict.fromkeys(g for r in roles for g in r.priority_paths))
    checks = any(t.require_checks for t in trig)
    source = dataclasses.replace(
        source,
        answers=answers,
        watch=watch,
        checks=checks,
        globs=globs,
        heads=bool(answers) or checks or bool(globs) or any(t.version == "head" for t in trig),
    )
    for r in roles:
        if any(t.kind == "ledger" for t in r.triggers) and source.ledger is None:
            raise ConfigError(f"role {r.name}: a ledger trigger needs source.ledger")
    asks = _asks(raw.get("asks"), base)
    for r in roles:
        if any(t.kind == "ask" for t in r.triggers) and not (asks.label or asks.private_file):
            raise ConfigError(f"role {r.name}: an ask trigger needs asks.label or asks.private_file")
    return SupervisorConfig(
        path=p,
        state_dir=_path(base, raw.get("state_dir", ".onloop/supervisor")),
        claude=claude_t,
        interval=_dur(raw.get("interval", "60s"), "interval"),
        jitter=_dur(raw["jitter"], "jitter") if raw.get("jitter") else 0.0,
        max_parallel=_int(raw.get("max_parallel", 2), "max_parallel", 1, 32),
        stop_grace=_dur(raw.get("stop_grace", "30s"), "stop_grace"),
        source=source,
        asks=asks,
        roles=roles,
        attention=_attention(raw.get("attention")),
        notifier=_notifier(raw.get("notifier")),
    )


def load(path: str | pathlib.Path) -> SupervisorConfig:
    p = pathlib.Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {p}: {e}") from e
    return from_dict(raw, p)
