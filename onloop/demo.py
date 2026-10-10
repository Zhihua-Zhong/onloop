"""The live demo: pages built from real onloop output, at build time.

`build(example_dir, out_dir)` reads `onloop.yaml` and `ledger.yaml` from the example, then

  * builds a small repository history and a handful of pull requests in memory (the same fakes the tests use);
  * runs the real classifier, the real gate (as a dry run) and the real audit on them;
  * writes `index.html` (the dashboard over that history) and `how-it-works.html` (six scripted pull requests, each
    with the exact output of `onloop classify` and `onloop gate --dry-run`).

Nothing on either page is typed: the verdicts, the counts and the refusal sentences are what the code returned. The
inputs are made up (a pull request is a dict), and the project checks in `checks:` are not run, because there is no
repository to run them in; the page says so.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import pathlib

import yaml

from . import audit, dashboard, gate, ledger
from . import policy as pol
from .classify import classify, summary_lines
from .testing import FakeGit, FakeGitHub

NOW = dt.datetime(2026, 10, 11, 9, 0, tzinfo=dt.UTC)          # a fixed clock: the same inputs give the same pages
NAV = [("index.html", "Dashboard"), ("how-it-works.html", "How it works")]


def _sha(label: str) -> str:
    return hashlib.sha1(label.encode("utf-8")).hexdigest()


def _review(login: str, type_: str, head: str, rid: int, at: str, state: str = "APPROVED") -> dict:
    return {"id": rid, "author": {"login": login, "type": type_}, "state": state, "commit": head, "submitted_at": at}


class Example:
    """The example configuration and the identities it names."""

    def __init__(self, directory: pathlib.Path):
        self.dir = directory
        self.policy_text = (directory / pol.POLICY_FILE).read_text(encoding="utf-8")
        self.policy = pol.loads(self.policy_text)
        self.ledger = ledger.load(directory / ledger.LEDGER_FILE)

    def reviewer(self, head: str, rid: int = 1, at: str = "2026-10-10T10:00:00Z", **kw) -> dict:
        return _review(self.policy.reviewer, "Bot", head, rid, at, **kw)

    def human(self, head: str, rid: int = 2, at: str = "2026-10-10T11:00:00Z") -> dict:
        return _review(self.policy.human, "User", head, rid, at)

    def loosened_policy_text(self) -> str:
        """The policy with the reservation on docs/claims/ removed: what a pull request would do to relax the rules."""
        raw = yaml.safe_load(self.policy_text)
        raw["paths"]["docs/claims/"] = {"owner": raw["paths"]["docs/claims/"]["owner"]}
        return yaml.safe_dump(raw, sort_keys=False)


# -- the scripted pull requests ---------------------------------------------------------------------------------------

def _scenarios(ex: Example) -> list[dict]:
    """(label, what the story is, a pull request and its files) for each case the page walks through."""
    green = [{"name": "policy", "conclusion": "SUCCESS"}]

    def facts(n: int, title: str, label: str, **over) -> dict:
        head = _sha(label)
        base = {"number": n, "state": "OPEN", "draft": False, "base": "main", "head": head, "title": title, "body": "",
                "commit_messages": [], "dismissals": [], "checks": list(green), "reviews": [ex.reviewer(head)]}
        base.update(over)
        return base

    claims_head = _sha("claims")
    stale_head = _sha("an-earlier-version")
    return [
        {"id": "routine", "heading": "A routine change",
         "story": "A builder fixes a bug in src/. Nothing reserved is touched, so the reviewer's approval on the "
                  "exact head is all the gate needs.",
         "facts": facts(11, "[builder] Fix off-by-one in the parser", "routine"), "files": ["src/parser.py"]},
        {"id": "claims", "heading": "A claims file, no human approval yet",
         "story": "docs/claims/ is reserved to the human. The reviewer has approved, which is not enough: the gate "
                  "refuses until the human approves the same head.",
         "facts": facts(12, "[lead] State the accuracy claim", "claims",
                        reviews=[ex.reviewer(claims_head)]), "files": ["docs/claims/accuracy.md"]},
        {"id": "claims-ok", "heading": "The same claim, approved by the human",
         "story": "The human approves the same head. Both approvals are bound to that commit, so the gate would "
                  "merge it.",
         "facts": facts(12, "[lead] State the accuracy claim", "claims",
                        reviews=[ex.reviewer(claims_head), ex.human(claims_head)]),
         "files": ["docs/claims/accuracy.md"]},
        {"id": "loosening", "heading": "A change that relaxes the rules",
         "story": "The pull request edits onloop.yaml so that docs/claims/ is no longer reserved. A change to the rules "
                  "is never judged by the rules it changes: the classifier compares the two policies, calls this "
                  "authority widening, and asks for the human.",
         "facts": facts(13, "[lead] Open up the claims directory", "loosening"), "files": [pol.POLICY_FILE],
         "head_policy": ex.loosened_policy_text()},
        {"id": "stale", "heading": "An approval of an older commit",
         "story": "The reviewer approved an earlier version. The builder pushed again; the approval no longer binds "
                  "to the head, so it counts for nothing.",
         "facts": facts(14, "[builder] Handle empty input", "stale",
                        reviews=[ex.reviewer(stale_head, at="2026-10-10T08:00:00Z")]),
         "files": ["src/parser.py"]},
        {"id": "checks", "heading": "A red check hiding behind a green one",
         "story": "Two workflows report a check of the same name on the head, one red and one green. One green run "
                  "does not hide a red one, whatever order GitHub lists them in.",
         "facts": facts(15, "[builder] Cache parsed results", "checks",
                        checks=[{"name": "policy", "conclusion": "FAILURE"}, {"name": "policy",
                                                                               "conclusion": "SUCCESS"}]),
         "files": ["src/cache.py"]},
    ]


def run_scenario(ex: Example, sc: dict) -> dict:
    """Run the real classifier and the real gate (a dry run) on one scripted pull request."""
    f = sc["facts"]
    head, n = f["head"], f["number"]
    commits = {"origin/main": {"parents": [], "message": "base", "tree": "T0", "files": {pol.POLICY_FILE: ex.policy_text}},
               head: {"parents": ["origin/main"], "message": f["title"], "tree": "T1",
                      "files": {pol.POLICY_FILE: sc["head_policy"]} if sc.get("head_policy") else {}}}
    git = FakeGit(commits, {("origin/main", head): sc["files"]})
    git.pr_heads[n] = head
    client = FakeGitHub({n: f})
    head_policy, problem = gate.plan_policy_change(git, sc["files"], head)
    c = classify(sc["files"], ex.policy, head_policy, problem)
    code, notes = gate.run(n, client, git, dry_run=True)
    reviews = []
    for r in f["reviews"]:
        who = r["author"]["login"]
        binds = "the head" if r["commit"] == head else f"an older commit ({r['commit'][:7]})"
        reviews.append(f"{who} {r['state'].lower()} {binds}")
    return {"id": sc["id"], "heading": sc["heading"], "story": sc["story"], "number": n, "title": f["title"],
            "head": head[:7], "files": sc["files"], "reviews": reviews,
            "checks": [f"{k['name']}: {(k['conclusion'] or 'pending').lower()}" for k in f["checks"]],
            "classify": summary_lines(c), "gate": [("refused: " if code == 1 else "") + x for x in notes],
            "code": code, "merges": code == 0}


def _term(cmd: str, lines: list[str], code: int | None = None) -> str:
    body = "\n".join(["$ " + cmd] + lines + ([] if code is None else [f"(exit {code})"]))
    return f'<pre class="term">{dashboard._e(body)}</pre>'


def how_it_works(ex: Example, results: list[dict]) -> str:
    count = len(results)
    cards = []
    for r in results:
        verdict = dashboard._pill("would merge", "good") if r["merges"] else dashboard._pill("refused", "bad")
        needs = dashboard._pill("human needed", "human") if "human" in r["classify"][0] else ""
        meta = (f'<p class="legend">Changes <span class="mono">{dashboard._e(", ".join(r["files"]))}</span>. '
                f'Reviews: {dashboard._e("; ".join(r["reviews"]) or "none")}. '
                f'Checks: {dashboard._e("; ".join(r["checks"]) or "none")}.</p>')
        cards.append(f'<section class="card" id="{dashboard._e(r["id"])}" data-scenario="{dashboard._e(r["id"])}">'
                     f'<h2>{dashboard._e(r["heading"])} {verdict} {needs}</h2>'
                     f'<p class="legend"><span class="mono">#{r["number"]}</span> {dashboard._e(r["title"])}</p>'
                     f'<p>{dashboard._e(r["story"])}</p>{meta}'
                     + _term(f'onloop classify --base origin/main --head {r["head"]}', r["classify"])
                     + _term(f'onloop gate {r["number"]} --dry-run', r["gate"], r["code"]) + "</section>")
    intro = ('<div class="focus"><b>How it works</b>' + f'{count} scripted pull requests against '
             '<span class="mono">examples/minimal</span>. The classifier and the gate below are the real ones; '
             'the page was built by running them. The pull requests are made up, and the project checks in '
             '<span class="mono">checks:</span> are not run here because there is no repository to run them in.</div>')
    foot = (f'<footer>Built {dashboard._e(NOW.strftime("%Y-%m-%d"))} by onloop {dashboard._e(dashboard.__version__)}. '
            'Change an input in <span class="mono">onloop/demo.py</span> and the verdicts change with it.</footer>')
    return dashboard.page("onloop: how it works", intro + "".join(cards) + foot, heading="onloop",
                          sub="how a pull request is judged", nav=NAV)


# -- the demo repository, for the dashboard ---------------------------------------------------------------------------

def _history(ex: Example) -> tuple[FakeGit, FakeGitHub, str]:
    """A made-up main branch: five clean merges, a gate revert and a direct push, so every audit outcome appears."""
    root = _sha("root")
    commits: dict[str, dict] = {root: {"parents": [], "message": "Start", "tree": "T0",
                                       "files": {pol.POLICY_FILE: ex.policy_text}}}
    changed: dict[tuple[str, str], list[str]] = {}
    order, prs, commit_pr = [], {}, {}
    prev, tree = root, 0

    def merge(n: int, title: str, files: list[str], by: tuple[str, str], at: str, human: bool = False) -> str:
        nonlocal prev, tree
        head, m = _sha(f"head{n}"), _sha(f"merge{n}")
        reviews = [ex.reviewer(head, at=f"2026-10-0{n % 9 + 1}T09:00:00Z")]
        if human:
            reviews.append(ex.human(head, at=f"2026-10-0{n % 9 + 1}T10:00:00Z"))
        tree += 1
        commits[head] = {"parents": [prev], "message": title, "tree": f"H{n}", "files": {pol.POLICY_FILE: ex.policy_text}}
        commits[m] = {"parents": [prev, head], "message": f"Merge pull request #{n}", "tree": f"T{tree}",
                      "files": {pol.POLICY_FILE: ex.policy_text}}
        changed[(prev, head)] = files
        prs[n] = {"number": n, "state": "MERGED", "draft": False, "base": "main", "head": head, "title": title,
                  "body": "", "commit_messages": [], "dismissals": [], "reviews": reviews,
                  "checks": [{"name": "policy", "conclusion": "SUCCESS"}],
                  "merged_by": {"login": by[0], "type": by[1]}, "merged_at": at, "merge_sha": m, "files": files}
        commit_pr[m] = n
        order.append(m)
        prev = m
        return m

    agent = (ex.policy.agents, "Bot")
    merge(1, "[builder] Add the parser", ["src/parser.py"], agent, "2026-10-05T12:00:00Z")
    merge(2, "[builder] Test the parser", ["tests/test_parser.py"], agent, "2026-10-06T12:00:00Z")
    merge(3, "[lead] Record decision D0", ["docs/decisions/0001.md"], agent, "2026-10-07T12:00:00Z")
    merge(4, "[lead] State the accuracy claim", ["docs/claims/accuracy.md"], (ex.policy.human, "User"),
          "2026-10-08T12:00:00Z", human=True)
    merge(5, "[builder] Handle empty input", ["src/parser.py"], agent, "2026-10-09T12:00:00Z")
    bad = merge(6, "[builder] Cache parsed results", ["src/cache.py"], agent, "2026-10-10T12:00:00Z")
    before_bad = commits[bad]["parents"][0]
    revert = _sha("revert6")
    commits[revert] = {"parents": [bad], "message": f"Revert #6\n\nGate-Revert: #6 {bad}\n",
                       "tree": commits[before_bad]["tree"], "files": {pol.POLICY_FILE: ex.policy_text}}
    order.append(revert)
    push = _sha("push")
    commits[push] = {"parents": [revert], "message": "hotfix: skip the tests", "tree": "T99",
                     "files": {pol.POLICY_FILE: ex.policy_text}}
    order.append(push)
    return FakeGit(commits, changed, order), FakeGitHub(prs, commit_pr), root


def _open_prs(ex: Example) -> list[dict]:
    """Four open pull requests, one in each state the dashboard distinguishes."""
    def pr(n: int, title: str, files: list[str], **over) -> dict:
        head = _sha(f"open{n}")
        d = {"number": n, "state": "OPEN", "draft": False, "base": "main", "head": head, "title": title, "body": "",
             "commit_messages": [], "dismissals": [], "reviews": [ex.reviewer(head, at="2026-10-11T07:00:00Z")],
             "checks": [{"name": "policy", "conclusion": "SUCCESS"}], "files": files,
             "head_pushed_at": "2026-10-11T06:00:00Z"}
        d.update(over)
        return d
    waiting = _sha("open9")
    return [pr(7, "[builder] Draft the contributor guide", ["docs/guide.md"], reviews=[], draft=True),
            pr(8, "[lead] State the revised claim", ["docs/claims/accuracy.md"]),
            pr(9, "[builder] Add a retry to the fetcher", ["src/fetch.py"], reviews=[],
               head=waiting, head_pushed_at="2026-10-09T17:00:00Z"),
            pr(10, "[builder] Rename the parser module", ["src/parser.py"])]


def dashboard_page(ex: Example) -> str:
    git, client, root = _history(ex)
    hist = audit.history(git, client, since=root)
    prs = _open_prs(ex) + [client.prs[n] for n in sorted(client.prs)]
    model = dashboard.build_model(ex.policy, ex.ledger, prs=prs, history=hist, now=NOW, title="onloop",
                                  source="examples/minimal and a made-up repository history")
    page = dashboard.render(model, nav=NAV)
    merges = sum(1 for h in hist if h["kind"] in ("merge", "human merge"))
    pushes = sum(1 for h in hist if h["kind"] == "direct push")
    banner = ('<div class="focus"><b>Demo</b>This is the dashboard over <span class="mono">examples/minimal</span> and '
              f'a made-up repository: {merges} merges, {len(_open_prs(ex))} open pull requests and {pushes} direct '
              'push, so an alarm shows. The figures are computed by the real code from those inputs; see '
              '<a href="how-it-works.html">how it works</a>.</div>')
    return page.replace('<div class="focus">', banner + '<div class="focus">', 1)


def build(example_dir: str | pathlib.Path, out_dir: str | pathlib.Path) -> list[pathlib.Path]:
    ex = Example(pathlib.Path(example_dir))
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = [run_scenario(ex, s) for s in _scenarios(ex)]
    written = []
    for name, text in (("index.html", dashboard_page(ex)), ("how-it-works.html", how_it_works(ex, results))):
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        written.append(out / name)
    return written
