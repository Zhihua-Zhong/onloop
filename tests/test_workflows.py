"""The repository's GitHub workflows and the ones `onloop init` writes, read as files (0.6.0's hardening): nothing runs
them here, so these checks are what this machine can verify before the first run on GitHub."""

import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
TEMPLATE_WORKFLOWS = sorted(
    (ROOT / "src" / "onloop" / "templates" / "dot_github" / "workflows").glob("*.yml")
)
PINNED = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\s+#\s*v\d+(\.\d+)*\s*$")


def _uses(path):
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if re.match(r"^\s*-?\s*uses:", ln)]


@pytest.mark.parametrize("path", REPO_WORKFLOWS + TEMPLATE_WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_by_its_full_commit_sha_with_the_version_beside_it(path):
    """A tag can be moved to other code; a commit SHA cannot. The comment names the version Dependabot updates."""
    lines = _uses(path)
    assert lines, f"{path.name} uses no action"
    assert [ln for ln in lines if not PINNED.match(ln)] == []


@pytest.mark.parametrize("path", REPO_WORKFLOWS + TEMPLATE_WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_is_yaml_with_jobs_and_steps(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(doc, dict) and isinstance(doc.get("jobs"), dict) and doc["jobs"]
    for name, job in doc["jobs"].items():
        assert isinstance(job.get("steps"), list) and job["steps"], name


def test_one_action_is_pinned_to_one_sha_everywhere():
    """The same action at two SHAs would be two versions in one repository, one of them never updated."""
    seen: dict[str, set[str]] = {}
    for path in REPO_WORKFLOWS + TEMPLATE_WORKFLOWS:
        for ln in _uses(path):
            m = PINNED.match(ln)
            if m:
                seen.setdefault(m.group(1), set()).add(m.group(2))
    assert {a: s for a, s in seen.items() if len(s) > 1} == {}


def test_dependabot_proposes_weekly_grouped_updates_for_actions_pip_and_npm():
    doc = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert doc["version"] == 2
    by = {u["package-ecosystem"]: u for u in doc["updates"]}
    assert set(by) == {"github-actions", "pip", "npm"}
    assert by["npm"]["directory"] == "/web" and by["pip"]["directory"] == "/"
    for u in by.values():
        assert u["schedule"]["interval"] == "weekly" and u["groups"]


def _steps(job):
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    return [s.get("run", "") for s in doc["jobs"][job]["steps"]]


def test_the_wheel_job_audits_the_wheels_dependencies_and_the_uis_production_ones_through_the_gate():
    runs = "\n".join(_steps("wheel"))
    assert "tools/audit_gate.py check" in runs
    assert "npm audit --json --omit=dev" in runs and "audit_gate.py npm" in runs
    assert "pip-audit --path" in runs and "audit_gate.py pip-ignores" in runs
    assert runs.index("python -m build --wheel") < runs.index("pip-audit --path")  # it audits the built wheel


def test_the_e2e_job_builds_the_ui_before_installing_onloop_and_keeps_the_report_on_failure():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = doc["jobs"]["e2e"]["steps"]
    runs = [s.get("run", "") for s in steps]
    build = next(i for i, r in enumerate(runs) if "npm run build" in r)
    install = next(i for i, r in enumerate(runs) if "pip install" in r and "." in r)
    browser = next(i for i, r in enumerate(runs) if "playwright install" in r)
    e2e = next(i for i, r in enumerate(runs) if r.strip() == "npm run e2e")
    assert build < install < e2e and browser < e2e
    assert "chromium" in runs[browser] and steps[e2e]["env"]["PW_CHANNEL"] == ""
    keep = steps[-1]
    assert keep["if"] == "failure()" and keep["uses"].startswith("actions/upload-artifact@")
    assert "web/playwright-report" in keep["with"]["path"]


def test_the_test_matrix_covers_every_python_the_package_says_it_supports():
    import re

    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    matrix = doc["jobs"]["test"]["strategy"]["matrix"]["python"]
    listed = re.findall(r"Programming Language :: Python :: (3\.\d+)", (ROOT / "pyproject.toml").read_text())
    assert matrix == listed == ["3.11", "3.12", "3.13", "3.14"]
