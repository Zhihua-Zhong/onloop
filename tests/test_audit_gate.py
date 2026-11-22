"""The vulnerability audit's gate (tools/audit_gate.py): the allowlist's rules, and which npm advisories fail the build.
The npm reports here are made up, in the shape `npm audit --json` writes (npm 7 and later)."""

import datetime as dt
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("audit_gate", ROOT / "tools" / "audit_gate.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

TODAY = dt.date(2026, 11, 22)


def report(*advisories):
    """{package: [(severity, ghsa id), ...]} as npm writes it, with one transitive entry naming a package."""
    vulns = {}
    for pkg, sev, ghsa in advisories:
        v = vulns.setdefault(pkg, {"name": pkg, "severity": sev, "via": []})
        v["via"].append(
            {
                "source": 1000 + len(v["via"]),
                "title": "a made-up flaw",
                "severity": sev,
                "url": f"https://github.com/advisories/{ghsa}",
            }
        )
    vulns["parent"] = {"name": "parent", "severity": "high", "via": ["left-pad"]}
    return {"vulnerabilities": vulns}


def test_the_repositorys_allowlist_is_valid_and_empty_to_start():
    entries = gate.load()
    assert entries == [] and gate.problems(entries, TODAY) == []


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        (
            {"id": "GHSA-a", "ecosystem": "npm", "reason": "r", "expires": "2026-11-21"},
            "expired on 2026-11-21",
        ),
        ({"id": "GHSA-a", "ecosystem": "npm", "reason": " ", "expires": "2027-01-01"}, "no reason"),
        ({"id": "GHSA-a", "ecosystem": "npm", "reason": "r"}, "expires is a date"),
        (
            {"id": "GHSA-a", "ecosystem": "maven", "reason": "r", "expires": "2027-01-01"},
            "ecosystem is npm or pip",
        ),
        ({"ecosystem": "pip", "reason": "r", "expires": "2027-01-01"}, "no id"),
    ],
)
def test_an_allowlist_entry_needs_an_id_an_ecosystem_a_reason_and_an_expiry_not_past(entry, why):
    assert any(why in p for p in gate.problems([entry], TODAY))


def test_an_entry_that_expires_today_still_holds():
    assert (
        gate.problems([{"id": "X", "ecosystem": "pip", "reason": "r", "expires": "2026-11-22"}], TODAY) == []
    )


def test_a_high_or_critical_npm_advisory_fails_unless_it_is_allowlisted_and_lower_ones_never_do():
    r = report(
        ("vite", "high", "GHSA-hhhh"),
        ("esbuild", "critical", "GHSA-cccc"),
        ("lodash", "moderate", "GHSA-mmmm"),
    )
    assert gate.npm_failures(r, set()) == [
        "esbuild: critical GHSA-cccc a made-up flaw",
        "vite: high GHSA-hhhh a made-up flaw",
    ]
    assert gate.npm_failures(r, {"GHSA-cccc", "GHSA-hhhh"}) == []


def test_pip_entries_become_pip_audits_ignore_arguments():
    entries = [
        {"id": "PYSEC-2026-1", "ecosystem": "pip", "reason": "r", "expires": "2027-01-01"},
        {"id": "GHSA-n", "ecosystem": "npm", "reason": "r", "expires": "2027-01-01"},
    ]
    assert gate.allowed(entries, "pip") == {"PYSEC-2026-1"}
