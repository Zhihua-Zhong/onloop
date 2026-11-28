"""The smart layer (0.7.0 plan, section 3): text written by a model over computed facts, validated, cached in the ledger.

    facts(ledger, now, end=None) -> dict          the computed facts the texts may use: Home's numbers, the decisions
                                                  with their recorded options, the unusual items; no age finer than the
                                                  page shows, so the facts (and their hash) hold for a while
    facts_hash(facts) -> str                      the hash a text is valid for
    generate(kind, facts, run=None) -> dict       one headless `claude -p` call, its JSON output parsed; raises Failed
    check(kind, output, facts) -> list[str]       why a text is not taken ([] when it is)
    latest(ledger, kind, hash) -> dict | None     the accepted text for these facts, from the ledger
    refresh(ledger, now, kinds, run=None) -> dict one pass: for each kind whose facts have no text yet, generate,
                                                  check, and log `text.generated` or `text.rejected`, within the caps
    enabled(config_value) -> bool                 `smart: off` in onloopd.yaml, or ONLOOP_SMART=off, turns it all off

**What is generated, and what never is.** The brief (2 to 3 sentences); for each decision a short title, its subject in
a few words, and, when its ask recorded options, the suggested answer, which must be one of them, with grounds that
cite the facts; and near-duplicate merge proposals between open decisions, which a person confirms (Decisions) and
which never merge by themselves. Never generated: any number (every number in a text must be one in its facts), the
order, "Do first", the options.

**How.** `claude -p --output-format json --tools "" --model <model>`, the prompt on stdin, through `procs.run` (no
console window), from an empty temporary folder, with no ANTHROPIC_API_KEY in its environment (the subscription, as the carrier's turns), and a
timeout. The prompt is this module's, versioned (`PROMPT_VERSION`); the facts are computed at run time and never stored
outside the ledger.

**Checks, or the text is dropped** (logged as `text.rejected` with the reason): valid JSON with exactly the keys asked
for; every digit run in the prose present in the facts; every suggested answer one of its decision's options; every id
it names an open decision's.

**The cache is the ledger.** An accepted text is a `text.generated` event (kind, facts hash, model, prompt version, the
output, its usage and time), source `config`: onloopd's own record, never the team's activity. A page shows the latest
text whose hash is today's facts' hash; when the facts change, the page shows the facts at once and the new text when
it arrives. Calls are capped per kind per day (`CAPS`); a call that fails, times out, or is rejected leaves the facts
alone on the page.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from typing import Any

from . import derive
from . import events as ev

PROMPT_VERSION = "0.7.0-4"
KINDS = ("brief", "decisions", "merges")
CAPS = {"brief": 8, "decisions": 20, "merges": 6}  # calls per kind per day (plan, section 3)
MODELS = {"brief": "sonnet", "decisions": "sonnet", "merges": "sonnet"}
TIMEOUT_S = 120
DEBOUNCE_S = 15 * 60  # a kind whose last attempt failed waits this long before it tries again
SOURCE = "config"


class Failed(Exception):
    """The call did not produce a text: the CLI is missing, not signed in, timed out, or answered something unreadable."""


def enabled(value: Any = None) -> bool:
    if str(os.environ.get("ONLOOP_SMART", "")).lower() in ("off", "0", "false", "no"):
        return False
    return str(value).lower() not in ("off", "false", "0", "no") if value is not None else True


# -- the facts ----------------------------------------------------------------------------------------------------------


def facts(ledger: Any, now: str, end: str | None = None) -> dict[str, Any]:
    """What the texts may say, computed. Ages are in the page's words ("9 days"), so the facts hold until something on the
    page would change."""
    from . import decisions as dec
    from . import home

    h = home.build(ledger, now, end)
    opened, rule_asks = dec.open_decisions(ledger, h["to"])
    n = h["numbers"]

    def k(v: Any) -> Any:  # tokens as the page shows them ("297k"), never a figure the page does not
        return None if v is None else (f"{round(v / 1000)}k" if v >= 1000 else v)

    return {
        "window": "the last 7 days",
        "open_decisions": h["decisions"][
            "count"
        ],  # what the person decides; several asks can be one decision
        "asks_waiting_on_you": h["decisions"]["waiting_on_you"],
        "asks_missing_their_effect": rule_asks,
        "delivered": {
            "this_week": n["delivered"]["value"],
            "week_before": None if h["young"] else n["delivered"]["last_week"],
        },
        "waited_hours": {
            "this_week": round(n["waited_h"]["value"]),
            "week_before": None if h["young"] else round(n["waited_h"]["last_week"]),
        },
        "tokens_per_delivery": {
            "this_week": k(n["cost_per_delivery"]["value"]),
            "week_before": None if h["young"] else k(n["cost_per_delivery"]["last_week"]),
        },
        "decisions": [
            {
                "id": d["id"],
                "rank": i + 1,
                "effect": d["title"],
                "asked_by": d["askers"] or ([d["asked_by"]] if d["asked_by"] else []),
                "asked_times": d["asks"],
                "waiting": derive.plain_age(d["age_s"]),
                "holds": d["holds"],
                "if_nobody_answers": d["default"],
                "cost_of_delay": d["cost_text"],
                "about": d["ref"],
                "options": d["options"],
            }
            for i, d in enumerate(opened[:12])
        ],
        "unusual": [{"title": u["title"], "detail": u["text"]} for u in h["unusual"][:5]],
    }


def facts_hash(f: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(f, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _slice(kind: str, f: dict[str, Any]) -> dict[str, Any]:
    """The facts one kind is written from. A decision's texts and the merges change only when the decisions do: not
    when an hour passes (`waiting`) or the order moves (`rank`), so they are not written again every hour."""
    if kind == "brief":
        return f
    return {
        "decisions": [{k: v for k, v in d.items() if k not in ("waiting", "rank")} for d in f["decisions"]]
    }


# -- the prompt -----------------------------------------------------------------------------------------------------------

_RULES = (
    "You write short text for onloop, a tool that shows a person what their team of AI agents did and what waits on "
    "them. Use only the facts given. Every number you write must appear in the facts exactly as given; never compute, "
    "round or invent a number, date or count. A decision is not an ask: several asks can be one decision. Plain words, no jargon, no markdown. Answer with one JSON object and "
    "nothing else."
)

_ASK = {
    "brief": (
        "Write today's brief: 2 or 3 sentences for the person, most important first: what waits on them (name the first "
        "decision in plain words), how the week went against the week before when given, and the most unusual thing if "
        'any. JSON: {"sentences": [{"text": "...", "about": "<a decision id or null>"}]}'
    ),
    "decisions": (
        "For every decision in the facts, near duplicates included, exactly once: a short title (at most 8 words, what the person decides, no ids), its subject in at most 4 "
        "words, and, only when it has options, the suggested answer (exactly one of its options) with its grounds: one "
        "sentence citing facts of that decision (what it holds, what happens if nobody answers, how often it was asked; "
        "not how long it waited). No suggestion when it has no options. JSON: "
        '{"decisions": [{"id": "...", "title": "...", "subject": "...", "suggested": "<an option or null>", '
        '"grounds": "<a sentence or null>"}]}'
    ),
    "merges": (
        "Find pairs of open decisions that ask the person the same thing in different words (a near duplicate), if any. "
        'Propose them; a person confirms. JSON: {"merges": [{"keep": "<id>", "merge": "<id>", "why": "<one sentence>"}]} '
        "(an empty list when there are none)"
    ),
}


def prompt(kind: str, f: dict[str, Any]) -> str:
    return (
        f"{_RULES}\n\n{_ASK[kind]}\n\nFacts:\n{json.dumps(_slice(kind, f), ensure_ascii=False, indent=1)}\n"
    )


# -- the call -------------------------------------------------------------------------------------------------------------

# (argv, stdin, env, cwd, timeout) -> (exit code, stdout, stderr). The prompt goes on stdin: on Windows the CLI is a
# batch file, and cmd.exe cuts an argument at its first newline.
Runner = Callable[[list[str], str, dict[str, str], str, float], tuple[int, str, str]]


def _run(argv: list[str], stdin: str, env: dict[str, str], cwd: str, timeout: float) -> tuple[int, str, str]:
    from . import procs

    r = procs.run(
        argv, input=stdin, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )
    return r.returncode, r.stdout, r.stderr


def _env() -> dict[str, str]:
    from .carrier import guards

    env = guards.child_env(dict(os.environ))  # no CLAUDE*, ANTHROPIC*, OTEL_* from onloopd's own environment
    env.pop("ANTHROPIC_API_KEY", None)
    return env


def generate(
    kind: str, f: dict[str, Any], run: Runner | None = None, claude: str | Sequence[str] = "claude"
) -> dict[str, Any]:
    """One headless call. Returns {output, model, usage, ms}; raises Failed. `claude` is the CLI's argv prefix, as the
    carrier's (`claude.cmd` on Windows, or an interpreter and a script)."""
    prefix = [claude] if isinstance(claude, str) else list(claude)
    if not prefix:
        raise Failed("no claude CLI configured")
    prefix[0] = shutil.which(prefix[0]) or prefix[0]
    argv = [*prefix, "-p", "--output-format", "json", "--tools", "", "--model", MODELS[kind]]
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="onloop-brief-") as empty:
        try:
            code, out, err = (run or _run)(argv, prompt(kind, f), _env(), empty, TIMEOUT_S)
        except FileNotFoundError as e:
            raise Failed("the claude CLI is not installed") from e
        except Exception as e:  # noqa: BLE001 - a timeout or a spawn that failed: no text, the facts stay
            raise Failed(f"{type(e).__name__}: {e}"[:200]) from e
    ms = int((time.monotonic() - started) * 1000)
    if code != 0:
        raise Failed(f"claude exited {code}: {(err or out)[:200]}")
    try:
        wrapper = json.loads(out)
        text = wrapper.get("result") if isinstance(wrapper, dict) else None
        if not isinstance(text, str):
            raise ValueError("no result")
        m = re.search(r"\{.*\}", text, re.S)
        output = json.loads(m.group(0) if m else text)
    except (ValueError, AttributeError) as e:
        raise Failed(f"the answer is not the JSON asked for: {e}"[:200]) from e
    usage = wrapper.get("usage") if isinstance(wrapper.get("usage"), dict) else {}
    return {
        "output": output,
        "model": MODELS[kind],
        "usage": {
            k: usage.get(k)
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens")
            if k in usage
        },
        "cost_usd": wrapper.get("total_cost_usd"),
        "ms": ms,
    }


# -- the checks -----------------------------------------------------------------------------------------------------------

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(f: Any) -> set[str]:
    return set(_NUM.findall(json.dumps(f, ensure_ascii=False)))


def _prose_numbers_ok(texts: list[str], f: dict[str, Any]) -> str | None:
    known = _numbers(f)
    for t in texts:
        for n in _NUM.findall(t or ""):
            if n not in known and n.replace(",", "") not in known:
                return f"the number {n} is not in the facts"
    return None


def check(kind: str, output: Any, f: dict[str, Any]) -> list[str]:
    if not isinstance(output, dict) or set(output) != {kind if kind != "brief" else "sentences"}:
        return [f"expected exactly the key {'sentences' if kind == 'brief' else kind!r}"]
    ids = {d["id"]: d for d in f["decisions"]}
    problems: list[str] = []
    if kind == "brief":
        items = output["sentences"]
        if not isinstance(items, list) or not 1 <= len(items) <= 4:
            return ["the brief is 1 to 4 sentences"]
        for s in items:
            if not isinstance(s, dict) or set(s) != {"text", "about"} or not isinstance(s["text"], str):
                return ["each sentence is {text, about}"]
            if s["about"] is not None and s["about"] not in ids:
                problems.append(f"a sentence names {s['about']!r}, which is no open decision")
        bad = _prose_numbers_ok([s["text"] for s in items], f)
        if bad:
            problems.append(bad)
    elif kind == "decisions":
        items = output["decisions"]
        if not isinstance(items, list):
            return ["decisions is a list"]
        named = [d.get("id") for d in items if isinstance(d, dict)]
        missing = [i for i in ids if i not in named]
        if missing or len(named) != len(set(named)):
            problems.append(
                f"each open decision once: missing {missing[:3]}" if missing else "a decision named twice"
            )
        for d in items:
            if not isinstance(d, dict) or set(d) != {"id", "title", "subject", "suggested", "grounds"}:
                return ["each decision is {id, title, subject, suggested, grounds}"]
            fact = ids.get(d["id"])
            if fact is None:
                problems.append(f"{d['id']!r} is no open decision")
                continue
            if not isinstance(d["title"], str) or not d["title"].strip() or len(d["title"]) > 80:
                problems.append(f"{d['id']}: the title is not a short line")
            if d["suggested"] is not None and d["suggested"] not in fact["options"]:
                problems.append(f"{d['id']}: the suggestion {d['suggested']!r} is not one of its options")
            if d["suggested"] is not None and not d["grounds"]:
                problems.append(f"{d['id']}: a suggestion needs its grounds")
            if d["suggested"] is None and d["grounds"]:
                problems.append(f"{d['id']}: grounds without a suggestion")
            bad = _prose_numbers_ok([d["title"], d["subject"] or "", d["grounds"] or ""], {"decision": fact})
            if bad:
                problems.append(f"{d['id']}: {bad}")
    else:
        items = output["merges"]
        if not isinstance(items, list):
            return ["merges is a list"]
        for m in items:
            if not isinstance(m, dict) or set(m) != {"keep", "merge", "why"}:
                return ["each merge is {keep, merge, why}"]
            if m["keep"] not in ids or m["merge"] not in ids or m["keep"] == m["merge"]:
                problems.append(f"a merge names {m['keep']!r} and {m['merge']!r}: not two open decisions")
            bad = _prose_numbers_ok([m["why"] or ""], f)
            if bad:
                problems.append(bad)
    return problems


# -- the ledger -----------------------------------------------------------------------------------------------------------


def latest(ledger: Any, kind: str, fhash: str) -> dict[str, Any] | None:
    rows = ledger.select(
        "SELECT ts_system, payload FROM events WHERE kind = 'text.generated' AND subject_ref = ? "
        "AND json_extract(payload, '$.facts_hash') = ? ORDER BY seq DESC LIMIT 1",
        (f"text:{kind}", fhash),
    )
    if not rows:
        return None
    p = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
    return {**p, "at": rows[0]["ts_system"]}


def newest(ledger: Any, kind: str) -> dict[str, Any] | None:
    rows = ledger.select(
        "SELECT ts_system, payload FROM events WHERE kind = 'text.generated' AND subject_ref = ? ORDER BY seq DESC LIMIT 1",
        (f"text:{kind}",),
    )
    if not rows:
        return None
    p = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
    return {**p, "at": rows[0]["ts_system"]}


def _today_count(ledger: Any, kind: str, now: str) -> int:
    day = now[:10]
    rows = ledger.select(
        "SELECT count(*) AS n FROM events WHERE kind IN ('text.generated', 'text.rejected', 'text.failed') "
        "AND subject_ref = ? AND substr(ts_system, 1, 10) = ?",
        (f"text:{kind}", day),
    )
    return int(rows[0]["n"]) if rows else 0


def _last_attempt(ledger: Any, kind: str, fhash: str) -> str | None:
    rows = ledger.select(
        "SELECT max(ts_system) AS t FROM events WHERE kind IN ('text.rejected', 'text.failed') AND subject_ref = ? "
        "AND json_extract(payload, '$.facts_hash') = ?",
        (f"text:{kind}", fhash),
    )
    return rows[0]["t"] if rows else None


def _event(kind: str, what: str, payload: dict[str, Any]) -> dict[str, Any]:
    import uuid

    return {
        "id": f"text-{uuid.uuid4().hex}",
        "source": SOURCE,
        "kind": what,
        "subject_ref": f"text:{kind}",
        "actor": "onloopd",
        "payload": payload,
    }


def refresh(
    ledger: Any,
    now: str,
    kinds: tuple[str, ...] = KINDS,
    run: Runner | None = None,
    claude: str | Sequence[str] = "claude",
    end: str | None = None,
    guard: Callable[[], Any] | None = None,
) -> dict[str, str]:
    """One pass of the smart layer: what happened per kind (`cached`, `generated`, `rejected`, `failed`, `capped`,
    `waiting`, `nothing to write`). `guard` is onloopd's writer lock: every read and write of the ledger is under it,
    and the call to the model is not, so the tick and the pages never wait on the model."""
    g = guard or contextlib.nullcontext
    with g():
        f = facts(ledger, now, end)
    out: dict[str, str] = {}
    for kind in kinds:
        fh = facts_hash(_slice(kind, f))
        if kind == "decisions" and not f["decisions"] or kind == "merges" and len(f["decisions"]) < 2:
            out[kind] = "nothing to write"
            continue
        with g():
            hit, today, last = (
                latest(ledger, kind, fh),
                _today_count(ledger, kind, now),
                _last_attempt(ledger, kind, fh),
            )
        if hit:
            out[kind] = "cached"
            continue
        if today >= CAPS[kind]:
            out[kind] = "capped"
            continue
        if last and (ev.parse_ts(now) - ev.parse_ts(last)).total_seconds() < DEBOUNCE_S:
            out[kind] = "waiting"
            continue
        base = {"kind": kind, "facts_hash": fh, "prompt_version": PROMPT_VERSION}
        try:
            got = generate(kind, f, run, claude)
        except Failed as e:
            event, out[kind] = _event(kind, "text.failed", {**base, "reason": str(e)[:300]}), "failed"
        else:
            problems = check(kind, got["output"], f)
            if problems:
                event = _event(
                    kind,
                    "text.rejected",
                    {**base, "model": got["model"], "reasons": problems[:5], "ms": got["ms"]},
                )
                out[kind] = "rejected"
            else:
                event, out[kind] = _event(kind, "text.generated", {**base, **got}), "generated"
        with g():
            ledger.append(event)
    return out


# -- what the pages read --------------------------------------------------------------------------------------------------


def for_pages(ledger: Any, now: str, end: str | None = None) -> dict[str, Any]:
    """The texts valid for today's facts, and whether a newer one is on its way (the facts changed since the last)."""
    f = facts(ledger, now, end)
    out: dict[str, Any] = {}
    for kind in KINDS:
        fh = facts_hash(_slice(kind, f))
        hit = latest(ledger, kind, fh)
        last = newest(ledger, kind)
        out[kind] = {
            "facts_hash": fh,
            "text": hit["output"] if hit else None,
            "written_at": hit["at"] if hit else None,
            "model": hit["model"] if hit else None,
            "stale": hit is None and last is not None,  # a text exists, but for other facts: "updating"
        }
    return out
