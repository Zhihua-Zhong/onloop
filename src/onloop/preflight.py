"""Pre-flight: the form problems of a pull request, reported before the gate runs.

Pre-flight is a deliberate subset of the gate. It reports only the refusals about *form* (the `P` codes: no authoring
role, an unknown role, a path owned by another role without the Cross-owner line), which the author can fix at once,
and it says which slots the change will need. It never removes or softens a gate refusal: the gate re-reads everything
and decides on its own. A test asserts every code produced here is a gate code.

It also reports two *warnings*, which are never refusals and never count against the form set: `W004` a changed file
gained carriage-return line endings, and `W005` a changed path that no policy path covers (nobody owns it). The gate
does not refuse for either unless the policy's `enforce:` block says `refuse`; then each is reported here as the form
refusal `P004` or `P005` instead of a warning, and the gate and the audit refuse for it.

One comment per pull request carries the report. It is found by its marker and edited in place, never added to.
"""

from __future__ import annotations

from typing import Any

from . import gate, slots
from . import policy as pol
from .classify import classify

MARKER = "<!-- onloop:preflight -->"
FORM_CODES = gate.FORM_CODES


WARNING_CODES = ("W004", "W005")


def comment_authors(policy: pol.Policy) -> list[str]:
    """Whose pre-flight comment onloop will find and edit: the agents App's, and the workflow's own `github-actions`."""
    return [policy.agents, "github-actions"]


def code_span(s: str) -> str:
    """`s` as a Markdown code span that cannot be broken out of: a longer fence than any backtick run inside it, and no
    line breaks. Inside a code span `@name` pings nobody and no markup is read, so a file name cannot reach the page."""
    s = " ".join(str(s).split("\n")).replace("\r", " ")
    longest = max((len(r) for r in __import__("re").findall(r"`+", s)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def warnings(policy: pol.Policy, files: list[str], patches: dict[str, str] | None) -> list[dict[str, str]]:
    """The advisory findings: never refusals. `patches` maps a path to the unified diff GitHub shows for it."""
    out = []
    crlf = gate.crlf_paths_from_patches(patches)
    if crlf and policy.enforce.get("line_endings") != "refuse":
        out.append(
            {
                "code": "W004",
                "label": "carriage-return line endings were added to",
                "paths": crlf,
                "text": "carriage-return line endings were added to: " + ", ".join(crlf),
            }
        )
    unowned = sorted(p for p in files if policy.match(p) is None)
    if unowned and policy.enforce.get("uncovered_paths") != "refuse":
        out.append(
            {
                "code": "W005",
                "label": "no policy path covers (nobody owns)",
                "paths": unowned,
                "text": "no policy path covers (nobody owns): " + ", ".join(unowned),
            }
        )
    return out


def report(
    facts: dict[str, Any], policy: pol.Policy, files: list[str], patches: dict[str, str] | None = None
) -> dict[str, Any]:
    """What pre-flight has to say about a pull request: the form problems, the warnings and the slots the change needs."""
    c = classify(files, policy)
    problems = [r for r in gate.ownership_refusals(facts, policy, c) if r.code in FORM_CODES]
    problems += gate.enforcement_refusals(policy, files, gate.crlf_paths_from_patches(patches))
    warn = warnings(policy, files, patches)
    return {
        "head": facts["head"],
        "number": facts["number"],
        "codes": sorted({r.code for r in problems}),
        "problems": [{"code": r.code, "text": r.text} for r in problems],
        "warnings": warn,
        "warning_codes": sorted({w["code"] for w in warn}),
        "slots": slots.need(policy, files),
    }


def clean(rep: dict[str, Any]) -> bool:
    return not rep["problems"]


def comment_body(rep: dict[str, Any]) -> str:
    """The text of the pre-flight comment. Built from the report only: never from the pull request's own words."""
    lines = [MARKER, f"**onloop pre-flight** for `{rep['head'][:7]}`"]
    if rep["problems"]:
        lines += ["", "The gate will refuse this pull request for form until these are fixed:"]
        lines += [f"- `{p['code']}` {p['text']}" for p in rep["problems"]]
    else:
        lines += ["", "No form problems."]
    if rep["warnings"]:
        lines += ["", "Warnings (the gate does not refuse for these):"]
        lines += [
            f"- `{w['code']}` {w['label']}: " + ", ".join(code_span(p) for p in w["paths"])
            for w in rep["warnings"]
        ]
    if rep["slots"]:
        lines += [
            "",
            "Slots this change needs: "
            + ", ".join(f"`{s}`" for s in rep["slots"])
            + " (`onloop slot request`).",
        ]
    lines += ["", "Pre-flight only reports. The gate decides."]
    return "\n".join(lines)


def text(rep: dict[str, Any]) -> list[str]:
    out = [f"pre-flight for #{rep['number']} at {rep['head'][:7]}"]
    out += [f"  {p['code']}: {p['text']}" for p in rep["problems"]] or ["  no form problems"]
    out += [f"  warning {w['code']}: {w['text']}" for w in rep["warnings"]]
    if rep["slots"]:
        out.append("  slots needed: " + ", ".join(rep["slots"]))
    return out


def self_test() -> tuple[bool, list[str]]:
    """Pre-flight must flag a missing role and a borrowed path, stay quiet on a clean change, and stay a gate subset."""
    raw = {
        "human": "h",
        "reviewer": "r",
        "agents": "a",
        "roles": {"x": {}, "y": {}},
        "paths": {"onloop.yaml": {"owner": "x", "reserved": "authority_widening"}, "y/": {"owner": "y"}},
        "slots": {},
    }
    policy = pol.from_dict(raw)

    def facts(title: str, body: str = "") -> dict[str, Any]:
        return {
            "number": 1,
            "head": "a" * 40,
            "title": title,
            "body": body,
            "commit_messages": [],
            "reviews": [],
        }

    results = [
        ("a missing role is flagged", report(facts("no role"), policy, ["y/a"])["codes"] == ["P001"]),
        ("a borrowed path is flagged", report(facts("[x] t"), policy, ["y/a"])["codes"] == ["P003"]),
        (
            "a borrowed path with its line is clean",
            clean(report(facts("[x] t", "Cross-owner: y - why"), policy, ["y/a"])),
        ),
        ("a clean change is quiet", clean(report(facts("[y] t"), policy, ["y/a"]))),
        ("every code is a gate code", set(FORM_CODES) <= set(gate.CODES)),
        (
            "a warning is not a refusal",
            report(facts("[y] t"), policy, ["zzz/a"], {"y/a": "+x\r"})["codes"] == []
            and report(facts("[y] t"), policy, ["zzz/a"], {"y/a": "+x\r"})["warning_codes"]
            == ["W004", "W005"],
        ),
    ]
    return all(ok for _, ok in results), [f"  {'ok' if ok else 'FAILED'} {name}" for name, ok in results]
