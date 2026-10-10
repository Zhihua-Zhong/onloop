"""The dashboard and the demo: pages built from real inputs, with every panel fed and nothing typed or fetched."""
import copy
import pathlib
import re

import pytest

from onloop import audit, cli, dashboard, demo, ledger
from onloop import policy as pol

EXAMPLE = pathlib.Path(__file__).resolve().parent.parent / "examples" / "minimal"


@pytest.fixture(scope="module")
def ex():
    return demo.Example(EXAMPLE)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    demo.build(EXAMPLE, out)
    return {p.name: p.read_text(encoding="utf-8") for p in out.glob("*.html")}


def panel(page: str, name: str) -> str:
    m = re.search(rf'<section class="panel" data-panel="{name}">(.*?)</section>', page, re.S)
    assert m, f"panel {name} is missing"
    return m.group(1)


# -- the demo ----------------------------------------------------------------------------------------------------

def test_the_demo_writes_both_pages(built):
    assert sorted(built) == ["how-it-works.html", "index.html"]


def test_every_panel_has_data(built):
    page = built["index.html"]
    for name in dashboard.PANELS:
        body = panel(page, name)
        assert "Nothing" not in body and "No merges were read" not in body, name
    assert len(re.findall(r'data-stage="', page)) == 6
    assert len(re.findall(r'data-role="', page)) == len(pol.loads((EXAMPLE / "onloop.yaml").read_text("utf-8")).roles)


def test_no_figure_on_the_demo_loop_is_missing(built):
    loop = panel(built["index.html"], "loop")
    assert "n/a" not in loop


def test_no_external_url_or_request_in_any_page(built):
    for name, text in built.items():
        assert not re.search(r"https?:|//[a-z0-9.-]+\.[a-z]{2,}|@import|url\(|<link|<img|<iframe|src=|xmlns", text, re.I), name
        assert re.findall(r'<script[^>]*>', text) == ["<script>"], name          # inline only


def test_the_pages_carry_a_dark_theme_and_a_phone_viewport(built):
    for text in built.values():
        assert "prefers-color-scheme:dark" in text and 'data-theme="dark"' in text and 'name="viewport"' in text
        assert "<title>" in text


def test_the_scenarios_get_the_verdicts_the_rules_give(ex):
    got = {s["id"]: demo.run_scenario(ex, s) for s in demo._scenarios(ex)}
    assert [k for k, v in got.items() if v["merges"]] == ["routine", "claims-ok"]
    assert got["claims"]["code"] == 1 and "human" in got["claims"]["gate"][0]
    assert got["loosening"]["code"] == 1 and any("loosens the policy" in x for x in got["loosening"]["classify"])
    assert "authority_widening" in "".join(got["loosening"]["classify"])
    assert "no reviewer approval" in got["stale"]["gate"][0] and "older commit" in got["stale"]["reviews"][0]
    assert "not green" in got["checks"]["gate"][0] and got["checks"]["checks"] == ["policy: failure", "policy: success"]


def test_a_changed_input_changes_the_verdict(ex):
    sc = next(s for s in demo._scenarios(ex) if s["id"] == "routine")
    assert demo.run_scenario(ex, sc)["merges"]
    sc["facts"] = dict(sc["facts"], reviews=[])
    assert not demo.run_scenario(ex, sc)["merges"]                        # the page is what the code returns


def test_the_demo_is_deterministic(tmp_path):
    demo.build(EXAMPLE, tmp_path / "a")
    demo.build(EXAMPLE, tmp_path / "b")
    for name in ("index.html", "how-it-works.html"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


# -- the figures are computed ---------------------------------------------------------------------------------------

def figures(model: dict) -> dict:
    return {s["id"]: {f["label"]: f["value"] for f in s["figures"]} for s in model["stages"]}


def demo_model(ex, prs=None, history="demo"):
    git, client, root = demo._history(ex)
    hist = audit.history(git, client, since=root) if history == "demo" else history
    prs = demo._open_prs(ex) + [client.prs[n] for n in sorted(client.prs)] if prs is None else prs
    return dashboard.build_model(ex.policy, ex.ledger, prs=prs, history=hist, now=demo.NOW)


def test_the_stage_figures_follow_the_inputs(ex):
    m = demo_model(ex)
    f = figures(m)
    assert f["author"]["pull requests seen"] == 10 and f["author"]["still open"] == 4
    assert f["classify"]["reserved: human needed"] == 2                  # #4 and #8 touch docs/claims/
    assert f["human"]["waiting for the human"] == 1 and f["reviewer"]["waiting for a verdict"] == 1
    assert f["gate"]["merged"] == 6 and f["gate"]["ready to merge now"] == 1
    assert f["audit"]["alarms"] == 1 and f["audit"]["commits that hold"] == 7
    fewer = demo_model(ex, prs=[p for p in demo._open_prs(ex) if p["number"] != 8])
    assert figures(fewer)["human"]["waiting for the human"] == 0         # remove the input and the figure goes with it


def test_without_github_the_figures_say_so_instead_of_showing_zeros(ex):
    m = dashboard.build_model(ex.policy, ex.ledger, now=demo.NOW)
    assert all(fig["value"] is None for s in m["stages"] for fig in s["figures"])
    page = dashboard.render(m)
    assert page.count(">n/a<") == 12 and "--github" in page
    for name in dashboard.PANELS:
        panel(page, name)                                                # every panel still renders


def test_the_queues_show_now_and_next_and_flag_an_idle_role(ex):
    led = copy.deepcopy(ex.ledger)
    led["queues"]["reviewer"] = []
    cols = {c["role"]: c for c in dashboard.queue_columns(ex.policy, led)}
    assert cols["operator"]["current"]["id"] == "Q6" and cols["operator"]["next"]["id"] == "Q2"      # doing first
    assert cols["reviewer"]["current"] is None
    page = dashboard.render(dashboard.build_model(ex.policy, led, now=demo.NOW))
    assert ">idle<" in page


def test_the_policy_map_shades_exactly_the_rows_that_need_the_human(ex):
    rows = {r["path"]: r for r in dashboard.policy_rows(ex.policy)}
    assert rows["docs/claims/"]["human"] and rows["onloop.yaml"]["human"] and rows[".github/"]["human"]
    assert not rows["docs/copy/"]["human"] and rows["docs/copy/"]["approver"] == "reviewer"
    assert not rows["src/"]["human"] and rows["src/"]["reserved"] is None
    page = panel(dashboard.render(dashboard.build_model(ex.policy, ex.ledger, now=demo.NOW)), "policy")
    assert page.count('<tr class="human">') == sum(1 for r in rows.values() if r["human"])


def test_the_watchdog_panel_lists_what_the_watchdog_finds(ex):
    m = demo_model(ex)
    text = " ".join(m["watchdog"])
    assert "W3" in text and "Q6" in text and "#9" in text and "waited" in text


def test_text_from_the_inputs_is_escaped(ex):
    led = copy.deepcopy(ex.ledger)
    led["focus"] = "<script>alert(1)</script>"
    led["decisions"][0]["title"] = '<img src=x onerror="y">'
    page = dashboard.render(dashboard.build_model(ex.policy, led, now=demo.NOW))
    assert "<script>alert" not in page and "<img" not in page and "&lt;script&gt;" in page


# -- the audit history -----------------------------------------------------------------------------------------------

def test_history_records_every_outcome_and_audit_is_its_failures(ex):
    git, client, root = demo._history(ex)
    hist = audit.history(git, client, since=root)
    assert [h["kind"] for h in hist] == ["merge"] * 3 + ["human merge", "merge", "merge", "revert", "direct push"]
    assert [h["ok"] for h in hist] == [True] * 7 + [False]
    assert audit.audit(git, client, since=root) == [(h["sha"], h["why"]) for h in hist if not h["ok"]]
    assert hist[-1]["title"] == "hotfix: skip the tests" and hist[-2]["pr"] == 6


def test_a_window_that_starts_after_the_reverted_merge_still_judges_the_revert(ex):
    git, client, root = demo._history(ex)
    last_two = audit.history(git, client, since=root, limit=2)
    assert [h["kind"] for h in last_two] == ["revert", "direct push"] and last_two[0]["ok"]


# -- the command line ------------------------------------------------------------------------------------------------

def test_the_dashboard_command_writes_one_file(tmp_path, capsys):
    out = tmp_path / "site"
    assert cli.main(["dashboard", "--out", str(out), "--policy", str(EXAMPLE / "onloop.yaml"),
                     "--file", str(EXAMPLE / "ledger.yaml")]) == 0
    assert [p.name for p in out.iterdir()] == ["index.html"]
    page = (out / "index.html").read_text(encoding="utf-8")
    for name in dashboard.PANELS:
        panel(page, name)
    assert "Get the first pull request through the gate." in page


def test_the_demo_command_needs_an_example(tmp_path, capsys):
    assert cli.main(["demo", "--example", str(tmp_path), "--out", str(tmp_path / "o")]) == 2
    assert cli.main(["demo", "--example", str(EXAMPLE), "--out", str(tmp_path / "o")]) == 0
    assert (tmp_path / "o" / "index.html").exists()


def test_the_example_ledger_is_valid_for_the_example_policy(ex):
    assert ledger.check(ex.ledger, list(ex.policy.roles)) == []
