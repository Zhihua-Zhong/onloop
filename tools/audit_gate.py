"""The vulnerability audit's gate in CI (0.6.0's hardening): the allowlist, and what fails the build.

    python tools/audit_gate.py check                    the allowlist is valid: every entry has an id, an ecosystem, a
                                                        reason and an expiry date that has not passed
    python tools/audit_gate.py pip-ignores              pip-audit's `--ignore-vuln ID` arguments for the pip entries
    npm audit --json --omit=dev | python tools/audit_gate.py npm
                                                        exit 1 when any advisory of severity high or critical is not
                                                        on the allowlist; lists each one

The allowlist is `.github/audit-allowlist.yml`, empty to start:

    entries:
      - id: GHSA-xxxx-xxxx-xxxx          # the advisory: a GHSA id (npm, pip) or a PYSEC / CVE id (pip)
        ecosystem: npm                   # npm or pip
        reason: "why it does not apply, or why it waits"
        expires: 2027-01-31              # after this day the entry fails `check`, and the advisory fails the build

pip-audit reports no severity (most Python advisories carry none), so every pip advisory fails the build unless it is
on the allowlist. npm advisories below high are reported by npm and do not fail it.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALLOWLIST = ROOT / ".github" / "audit-allowlist.yml"
FAILING = ("high", "critical")
ECOSYSTEMS = ("npm", "pip")


def load(path: pathlib.Path = ALLOWLIST) -> list[dict[str, Any]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("entries") or []
    if not isinstance(entries, list):
        raise SystemExit(f"{path.name}: `entries` is a list")
    return entries


def problems(entries: list[dict[str, Any]], today: dt.date) -> list[str]:
    out = []
    for i, e in enumerate(entries):
        where = f"entry {i + 1}"
        if not isinstance(e, dict):
            out.append(f"{where}: not a mapping")
            continue
        if not str(e.get("id") or "").strip():
            out.append(f"{where}: no id")
        if e.get("ecosystem") not in ECOSYSTEMS:
            out.append(f"{where}: ecosystem is npm or pip")
        if not str(e.get("reason") or "").strip():
            out.append(f"{where} ({e.get('id')}): no reason")
        exp = e.get("expires")
        if isinstance(exp, str):
            try:
                exp = dt.date.fromisoformat(exp)
            except ValueError:
                exp = None
        if not isinstance(exp, dt.date):
            out.append(f"{where} ({e.get('id')}): expires is a date, YYYY-MM-DD")
        elif exp < today:
            out.append(f"{where} ({e.get('id')}): expired on {exp.isoformat()}")
    return out


def allowed(entries: list[dict[str, Any]], ecosystem: str) -> set[str]:
    return {
        str(e["id"]).strip()
        for e in entries
        if isinstance(e, dict) and e.get("ecosystem") == ecosystem and e.get("id")
    }


def npm_failures(report: dict[str, Any], allow: set[str]) -> list[str]:
    """Each high or critical advisory in an `npm audit --json` report (npm 7 or later) not on the allowlist, as one line:
    the package, the severity, the advisory's id and title."""
    out = set()
    for name, v in (report.get("vulnerabilities") or {}).items():
        for via in v.get("via") or []:
            if not isinstance(via, dict):
                continue  # a package name: the advisory is listed under that package itself
            sev = str(via.get("severity") or "").lower()
            if sev not in FAILING:
                continue
            url = str(via.get("url") or "")
            ident = url.rstrip("/").rsplit("/", 1)[-1] if url else str(via.get("source") or "")
            if ident in allow or str(via.get("source")) in allow:
                continue
            out.add(f"{name}: {sev} {ident} {via.get('title') or ''}".rstrip())
    return sorted(out)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("check", "pip-ignores", "npm"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    entries = load()
    bad = problems(entries, dt.date.today())
    if bad:
        for b in bad:
            print(f"audit allowlist: {b}", file=sys.stderr)
        return 1
    if argv[0] == "check":
        print(f"audit allowlist: {len(entries)} entries, all valid")
        return 0
    if argv[0] == "pip-ignores":
        print(" ".join(f"--ignore-vuln {i}" for i in sorted(allowed(entries, "pip"))))
        return 0
    report = json.load(sys.stdin)
    fails = npm_failures(report, allowed(entries, "npm"))
    for f in fails:
        print(f"npm audit: {f}", file=sys.stderr)
    print(f"npm audit: {len(fails)} high or critical advisories not on the allowlist")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
