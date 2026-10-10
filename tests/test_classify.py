import copy
import json
import subprocess
import sys

import yaml

from onloop import policy as pol
from onloop.classify import classify
from onloop.cli import main


def test_each_reserved_class_asks_the_approver_it_names(policy):
    assert classify(["src/a.py"], policy).approvers == ["reviewer"]
    assert classify(["docs/claims/x.md"], policy).approvers == ["human", "reviewer"]
    assert classify(["docs/copy/x.md"], policy).approvers == ["reviewer"]
    assert classify(["onloop.yaml"], policy).approvers == ["human", "reviewer"]


def test_the_tier_is_the_highest_of_the_paths_and_owners_are_collected(policy):
    c = classify(["src/a.py", "core/b.py", "README.md"], policy)
    assert c.tier == "full"
    assert c.owners == ["builder"]
    assert c.unowned == ["README.md"]


def test_a_loosening_is_authority_widening_and_needs_the_human_even_when_its_class_names_the_reviewer(policy, raw):
    loose = copy.deepcopy(raw)
    loose["paths"]["docs/claims/"].pop("reserved")
    c = classify(["onloop.yaml"], policy, pol.from_dict(loose))
    assert c.loosening and "authority_widening" in c.classes and c.needs_human
    soft = copy.deepcopy(raw)
    soft["classes"] = {"copy_edits": "reviewer", "authority_widening": "reviewer"}
    soft_loose = copy.deepcopy(soft)
    soft_loose["paths"]["docs/claims/"].pop("reserved")
    assert classify(["onloop.yaml"], pol.from_dict(soft), pol.from_dict(soft_loose)).needs_human


def test_an_unreadable_head_policy_is_authority_widening(policy):
    c = classify(["onloop.yaml"], policy, None, "does not parse")
    assert c.loosening and c.needs_human


def test_the_rules_in_force_are_the_base_policys_not_the_heads(policy, raw):
    """A head policy that reserves nothing cannot excuse the change that deletes the reservation."""
    head = copy.deepcopy(raw)
    head["paths"]["docs/claims/"] = {"owner": "lead"}
    assert classify(["docs/claims/x.md", "onloop.yaml"], policy, pol.from_dict(head)).needs_human


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_the_cli_classifies_a_real_diff_and_catches_a_loosening(tmp_path, raw):
    repo = tmp_path
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester")
    _git(repo, "config", "user.name", "t")
    (repo / "onloop.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "base")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "plain")
    plain = subprocess.run([sys.executable, "-m", "onloop.cli", "classify", "--base", "base", "--head", "feature",
                            "--json"], cwd=repo, capture_output=True, text=True)
    assert json.loads(plain.stdout)["approvers"] == ["reviewer"]
    loose = copy.deepcopy(raw)
    loose["paths"]["docs/claims/"].pop("reserved")
    (repo / "onloop.yaml").write_text(yaml.safe_dump(loose), encoding="utf-8")
    _git(repo, "commit", "-qam", "loosen")
    out = subprocess.run([sys.executable, "-m", "onloop.cli", "classify", "--base", "base", "--head", "feature",
                          "--json"], cwd=repo, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["approvers"] == ["human", "reviewer"] and got["loosening"]
    assert main(["classify", "--paths", "src/a.py", "--policy", str(repo / "onloop.yaml")]) == 0


def test_a_file_moved_out_of_a_reserved_path_still_touches_the_reserved_path(tmp_path, raw):
    """Real git: a rename is reported as a delete plus an add (`--no-renames`), so the old, reserved path counts."""
    from onloop.gitutil import Git
    repo = tmp_path
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tester")
    _git(repo, "config", "user.name", "t")
    (repo / "docs" / "claims").mkdir(parents=True)
    (repo / "docs" / "claims" / "x.md").write_text("a claim, long enough to be recognised as a rename\n" * 5,
                                                   encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "base")
    (repo / "src").mkdir()
    _git(repo, "mv", "docs/claims/x.md", "src/x.md")
    _git(repo, "commit", "-q", "-m", "move")
    files = Git(cwd=str(repo)).changed_files("base", "HEAD")
    assert files == ["docs/claims/x.md", "src/x.md"]
    assert classify(files, pol.from_dict(raw)).needs_human
