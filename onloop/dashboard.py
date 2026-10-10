"""The dashboard: one self-contained HTML page that shows the loop as it stands.

It reads the policy, the ledger and, when it is given them, the pull requests and the audit history, and writes a
single file with its own CSS and JS: no CDN, no server, no external request. Every number on the page is computed from
those inputs by `build_model`; nothing on the page is typed. With no GitHub data the stage counts say so instead of
showing zeros.

`render(model)` turns the model into the page; `page(...)` is the shared shell the demo's other page uses too.
"""
from __future__ import annotations

import datetime as dt
import html
from typing import Any

from . import __version__, gate, watchdog
from . import policy as pol
from .classify import Classification, classify

PANELS = ("loop", "policy", "queues", "human", "watchdog", "merges")
RECENT = 12


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x), quote=True)


# -- the model: everything the page says, computed ------------------------------------------------------------------

def _classified(policy: pol.Policy, p: dict[str, Any]) -> Classification | None:
    files = p.get("files")
    return classify(list(files), policy) if files is not None else None


def pr_row(policy: pol.Policy, p: dict[str, Any]) -> dict[str, Any]:
    """What the page needs to know about one pull request: its class, its evidence and what it still waits for."""
    c = _classified(policy, p)
    ev = gate.evidence(p, policy)
    row: dict[str, Any] = {"number": p["number"], "title": p.get("title") or "", "state": p.get("state"),
                           "draft": bool(p.get("draft")), "head": p["head"][:7], "classified": c is not None,
                           "needs_human": bool(c and c.needs_human), "reviewer_ok": ev.reviewer_approved,
                           "human_ok": ev.human_approved, "classes": c.classes if c else [],
                           "approvers": c.approvers if c else [], "files": len(p.get("files") or []),
                           "pushed": p.get("head_pushed_at")}
    if p.get("state") == "OPEN":
        if p.get("draft"):
            row["waiting"] = "draft"
        elif ev.blocking:
            row["waiting"] = "changes requested"
        elif not ev.reviewer_approved:
            row["waiting"] = "reviewer"
        elif c is not None and c.needs_human and not ev.human_approved:
            row["waiting"] = "human"
        elif gate.check_results(p, policy):
            row["waiting"] = "checks"
        else:
            row["waiting"] = "gate" if c is not None else "unknown"
    return row


def stage_counts(policy: pol.Policy, prs: list[dict[str, Any]] | None, history: list[dict] | None) -> list[dict]:
    """The six stages of the loop, each with the figures that can be computed from what was supplied."""
    rows = [pr_row(policy, p) for p in prs] if prs is not None else None
    cl = [r for r in rows or [] if r["classified"]]
    needing = [r for r in cl if r["needs_human"]]
    merges = [h for h in history or [] if h["kind"] in ("merge", "human merge")]
    open_rows = [r for r in rows or [] if r["state"] == "OPEN"]
    alarms = [h for h in history or [] if not h["ok"]]

    def fig(label: str, value: int | None) -> dict:
        return {"label": label, "value": value}

    none = rows is None
    return [
        {"id": "author", "name": "Author", "what": "an agent opens a pull request as a role",
         "figures": [fig("pull requests seen", None if none else len(rows)),
                     fig("still open", None if none else len(open_rows))]},
        {"id": "classify", "name": "Classify", "what": "the diff decides who must approve",
         "figures": [fig("reviewer only", None if none else len(cl) - len(needing)),
                     fig("reserved: human needed", None if none else len(needing))]},
        {"id": "reviewer", "name": "Reviewer", "what": "a separate identity reads the exact head",
         "figures": [fig("approved on the head", None if none else sum(1 for r in cl if r["reviewer_ok"])),
                     fig("waiting for a verdict", None if none else sum(1 for r in open_rows
                                                                       if r.get("waiting") == "reviewer"))]},
        {"id": "human", "name": "Human", "what": "only when a reserved class asks",
         "figures": [fig("approved on the head", None if none else sum(1 for r in needing if r["human_ok"])),
                     fig("waiting for the human", None if none else sum(1 for r in open_rows
                                                                       if r.get("waiting") == "human"))]},
        {"id": "gate", "name": "Gate", "what": "merges only the exact head that was approved",
         "figures": [fig("merged", None if history is None else len(merges)),
                     fig("ready to merge now", None if none else sum(1 for r in open_rows
                                                                    if r.get("waiting") == "gate"))]},
        {"id": "audit", "name": "Audit", "what": "re-judges every merge afterwards",
         "figures": [fig("commits that hold", None if history is None else len(history) - len(alarms)),
                     fig("alarms", None if history is None else len(alarms))]},
    ]


def policy_rows(policy: pol.Policy) -> list[dict[str, Any]]:
    rows = []
    for key, rule in sorted(policy.paths.items()):
        approver = policy.approver_of(rule.reserved) if rule.reserved else None
        rows.append({"path": key, "owner": rule.owner, "tier": rule.tier, "reserved": rule.reserved,
                     "approver": approver, "human": approver == "human"})
    return rows


def queue_columns(policy: pol.Policy, led: dict[str, Any]) -> list[dict[str, Any]]:
    cols = []
    queues = led.get("queues") or {}
    for role in policy.roles:
        items = queues.get(role) or []
        open_items = [i for i in items if i.get("state") in ("doing", "todo")]
        open_items.sort(key=lambda i: 0 if i.get("state") == "doing" else 1)       # stable: queue order within a state
        cols.append({"role": role, "open": len(open_items), "done": sum(1 for i in items if i.get("state") == "done"),
                     "current": open_items[0] if open_items else None,
                     "next": open_items[1] if len(open_items) > 1 else None,
                     "blocked": [i for i in items if i.get("state") == "blocked"]})
    return cols


def build_model(policy: pol.Policy, led: dict[str, Any], *, prs: list[dict[str, Any]] | None = None,
                history: list[dict] | None = None, now: dt.datetime, title: str = "onloop",
                source: str = "") -> dict[str, Any]:
    """Everything the page shows. `prs` is None when GitHub was not read; `history` is `audit.history(...)`."""
    open_prs = [p for p in prs or [] if p.get("state") == "OPEN"]
    rows = [pr_row(policy, p) for p in prs or []]
    human_prs = [r for r in rows if r.get("waiting") == "human"]
    pending = [d for d in led.get("decisions") or [] if d.get("status") == "pending"]
    findings = watchdog.scan(led, open_prs if prs is not None else [], now, roles=list(policy.roles),
                             reviewer=policy.reviewer)
    return {"title": title, "source": source, "now": now, "version": __version__, "focus": led.get("focus", ""),
            "has_github": prs is not None, "has_history": history is not None,
            "stages": stage_counts(policy, prs, history), "policy": policy_rows(policy),
            "classes": sorted(policy.classes.items()), "required": list(policy.required_checks),
            "tiers": list(policy.tiers), "identities": {"human": policy.human, "reviewer": policy.reviewer,
                                                        "agents": policy.agents},
            "queues": queue_columns(policy, led), "decisions": pending, "human_prs": human_prs,
            "open_rows": [r for r in rows if r["state"] == "OPEN"],
            "watchdog": findings, "merges": list(reversed(history or []))[:RECENT],
            "workstreams": led.get("workstreams") or [],
            "changes": list(reversed(led.get("changelog") or []))[:5]}


# -- the page ---------------------------------------------------------------------------------------------------------

STYLE = """
:root{--bg:#f6f7f9;--panel:#fff;--ink:#16202c;--muted:#5b6776;--line:#d9dee5;--accent:#1d5fd1;--accent-ink:#fff;
--good:#1b7a43;--good-bg:#e4f4ea;--warn:#8a5a00;--warn-bg:#fdf1d6;--bad:#a42a2a;--bad-bg:#fbe5e5;
--human:#6a2fb0;--human-bg:#efe6fa;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f141b;--panel:#171e28;--ink:#e6ebf1;
--muted:#9aa7b6;--line:#2a3441;--accent:#6aa3ff;--accent-ink:#0b1220;--good:#5fd18a;--good-bg:#12301f;--warn:#f0c15a;
--warn-bg:#33270a;--bad:#ff8f8f;--bad-bg:#3a1717;--human:#c4a1ff;--human-bg:#2a1d3f}}
:root[data-theme="dark"]{--bg:#0f141b;--panel:#171e28;--ink:#e6ebf1;--muted:#9aa7b6;--line:#2a3441;--accent:#6aa3ff;
--accent-ink:#0b1220;--good:#5fd18a;--good-bg:#12301f;--warn:#f0c15a;--warn-bg:#33270a;--bad:#ff8f8f;--bad-bg:#3a1717;
--human:#c4a1ff;--human-bg:#2a1d3f}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:72rem;margin:0 auto;padding:0 16px 48px}
header.top{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px;padding:18px 0 6px}
header.top h1{font-size:1.35rem;margin:0}
header.top .sub{color:var(--muted);flex:1 1 12rem}
nav.links{display:flex;gap:14px;flex-wrap:wrap}
a{color:var(--accent)}
button.theme{font:inherit;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:6px;
padding:4px 10px;cursor:pointer}
.focus{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:6px;
padding:10px 14px;margin:10px 0 18px}
.focus b{color:var(--muted);font-weight:600;margin-right:6px}
section.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:0 0 16px}
section.panel>h2{font-size:1.05rem;margin:0 0 4px}
section.panel>p.lede{color:var(--muted);margin:0 0 12px;font-size:.92rem}
.grid{display:grid;gap:12px}
.cols-4{grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))}
.flow{list-style:none;margin:0;padding:0;display:grid;gap:10px;grid-template-columns:repeat(6,minmax(0,1fr))}
@media (max-width:1000px){.flow{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:560px){.flow{grid-template-columns:repeat(2,minmax(0,1fr))}}
.stage{position:relative;border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--bg)}
.stage h3{margin:0;font-size:.95rem}
.stage .what{color:var(--muted);font-size:.82rem;margin:2px 0 8px;min-height:2.4em}
.stage.optional{border-style:dashed;border-color:var(--human)}
.stage.optional h3{color:var(--human)}
.fig{display:flex;align-items:baseline;gap:8px}
.fig b{font-size:1.35rem;font-variant-numeric:tabular-nums}
.fig span{color:var(--muted);font-size:.82rem}
.fig b.na{color:var(--muted);font-weight:400}
.stage .step{position:absolute;top:8px;right:10px;color:var(--muted);font-size:.75rem}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase;letter-spacing:.03em}
td.mono,.mono{font-family:var(--mono);font-size:.85rem}
tr.human td{background:var(--human-bg)}
.pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:.78rem;border:1px solid var(--line);
background:var(--bg);white-space:nowrap}
.pill.human{background:var(--human-bg);color:var(--human);border-color:var(--human)}
.pill.good{background:var(--good-bg);color:var(--good);border-color:var(--good)}
.pill.warn{background:var(--warn-bg);color:var(--warn);border-color:var(--warn)}
.pill.bad{background:var(--bad-bg);color:var(--bad);border-color:var(--bad)}
.col{border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:var(--bg)}
.col h3{margin:0 0 6px;font-size:.95rem;display:flex;justify-content:space-between;gap:8px}
.col .label{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.03em;margin-top:8px}
.col .item{margin:2px 0 0}
.empty{color:var(--muted);font-style:italic}
ul.plain{list-style:none;margin:0;padding:0}
ul.plain li{padding:8px 0;border-bottom:1px solid var(--line)}
ul.plain li:last-child{border-bottom:0}
.rec{color:var(--muted);font-size:.9rem;margin-top:2px}
footer{color:var(--muted);font-size:.82rem;padding:8px 0 0}
pre.term{background:#0e141b;color:#dce6f2;border-radius:6px;padding:10px 12px;margin:6px 0;overflow-x:auto;
font:12.5px/1.5 var(--mono);white-space:pre-wrap;word-break:break-word}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin:0 0 16px}
.card h2{margin:0 0 2px;font-size:1.05rem}
.verdict{font-weight:600}
.legend{color:var(--muted);font-size:.85rem;margin:6px 0 0}
@media (max-width:600px){body{font-size:14.5px}.wrap{padding:0 16px 40px}header.top h1{font-size:1.2rem}
section.panel,.card{padding:12px}}
"""

SCRIPT = """
(function(){
  var root=document.documentElement,key="onloop-theme";
  function get(){try{return localStorage.getItem(key)}catch(e){return null}}
  function put(v){try{localStorage.setItem(key,v)}catch(e){}}
  var saved=get();if(saved==="light"||saved==="dark")root.setAttribute("data-theme",saved);
  var b=document.getElementById("theme");
  if(b)b.addEventListener("click",function(){
    var dark=root.getAttribute("data-theme")==="dark"||
      (!root.getAttribute("data-theme")&&window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next=dark?"light":"dark";root.setAttribute("data-theme",next);put(next);});
})();
"""


def page(title: str, body: str, *, heading: str, sub: str = "", nav: list[tuple[str, str]] | None = None) -> str:
    links = "".join(f'<a href="{_e(href)}">{_e(label)}</a>' for href, label in nav or [])
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="color-scheme" content="light dark">\n<title>{_e(title)}</title>\n'
            f'<style>{STYLE}</style>\n</head>\n<body>\n<div class="wrap">\n'
            f'<header class="top"><h1>{_e(heading)}</h1><span class="sub">{_e(sub)}</span>'
            f'<nav class="links">{links}</nav>'
            f'<button class="theme" id="theme" type="button" aria-label="Switch between light and dark">Theme</button>'
            f'</header>\n{body}\n</div>\n<script>{SCRIPT}</script>\n</body>\n</html>\n')


def _pill(text: str, kind: str = "") -> str:
    return f'<span class="pill {kind}">{_e(text)}</span>'


def _loop_panel(m: dict[str, Any]) -> str:
    cards = []
    for i, s in enumerate(m["stages"], 1):
        optional = s["id"] == "human"
        figs = "".join(
            f'<div class="fig"><b class="{"na" if f["value"] is None else ""}">'
            f'{"n/a" if f["value"] is None else f["value"]}</b><span>{_e(f["label"])}</span></div>'
            for f in s["figures"])
        cards.append(f'<li class="stage{" optional" if optional else ""}" data-stage="{_e(s["id"])}">'
                     f'<span class="step">{i}</span>'
                     f'<h3>{_e(s["name"])}</h3><p class="what">{_e(s["what"])}</p>{figs}</li>')
    note = ("" if m["has_github"] and m["has_history"] else
            '<p class="legend">n/a: no GitHub data was read for this figure (run with <span class="mono">--github'
            '</span>).</p>')
    return ('<section class="panel" data-panel="loop"><h2>The loop</h2><p class="lede">Every change takes this path. '
            'The human is on it only when the diff touches a reserved class.</p>'
            f'<ol class="flow">{"".join(cards)}</ol>{note}</section>')


def _policy_panel(m: dict[str, Any]) -> str:
    rows = []
    for r in m["policy"]:
        reserved = _pill(r["reserved"], "human" if r["human"] else "good") if r["reserved"] else '<span class="empty">-</span>'
        appr = _pill(r["approver"], "human" if r["human"] else "") if r["approver"] else '<span class="empty">reviewer</span>'
        rows.append(f'<tr class="{"human" if r["human"] else ""}"><td class="mono">{_e(r["path"])}</td>'
                    f'<td>{_e(r["owner"] or "-")}</td><td>{_e(r["tier"])}</td><td>{reserved}</td><td>{appr}</td></tr>')
    ids = m["identities"]
    classes = " ".join(_pill(f"{n}: {a}", "human" if a == "human" else "") for n, a in m["classes"])
    return ('<section class="panel" data-panel="policy"><h2>Policy map</h2><p class="lede">Which role owns each path, '
            'how closely it is reviewed, and who must approve it when it is reserved. Rows needing the human are '
            'shaded.</p><div class="tablewrap"><table><thead><tr><th>Path</th><th>Owner</th><th>Tier</th>'
            f'<th>Reserved class</th><th>Approver</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="legend">Classes: {classes}</p>'
            f'<p class="legend">Required checks: {_e(", ".join(m["required"]) or "none")}. '
            f'Identities: human <span class="mono">{_e(ids["human"])}</span>, reviewer '
            f'<span class="mono">{_e(ids["reviewer"])}</span>, agents <span class="mono">{_e(ids["agents"])}'
            '</span>.</p></section>')


def _item(it: dict[str, Any] | None) -> str:
    if not it:
        return '<p class="item empty">nothing</p>'
    return (f'<p class="item"><span class="mono">{_e(it.get("id"))}</span> {_e(it.get("title"))} '
            f'{_pill(it.get("state", ""), "warn" if it.get("state") == "doing" else "")}</p>')


def _queues_panel(m: dict[str, Any]) -> str:
    cols = []
    for c in m["queues"]:
        idle = c["current"] is None
        blocked = "".join(f'<p class="item">{_pill("blocked", "bad")} <span class="mono">{_e(b.get("id"))}</span> '
                          f'{_e(b.get("title"))}</p>' for b in c["blocked"])
        badge = _pill("idle", "bad") if idle else _pill(str(c["open"]) + " open")
        cols.append(f'<div class="col" data-role="{_e(c["role"])}"><h3><span>{_e(c["role"])}</span>'
                    f'{badge}</h3>'
                    f'<div class="label">Now</div>{_item(c["current"])}'
                    f'<div class="label">Next</div>{_item(c["next"])}'
                    f'{"<div class=label>Blocked</div>" + blocked if blocked else ""}</div>')
    return ('<section class="panel" data-panel="queues"><h2>Queues</h2><p class="lede">What each role pulls next. '
            f'An empty queue is reported to the lead.</p><div class="grid cols-4">{"".join(cols)}</div></section>')


def _human_panel(m: dict[str, Any]) -> str:
    items = []
    for d in m["decisions"]:
        items.append(f'<li><span class="mono">{_e(d.get("id"))}</span> {_pill(d.get("size", ""))} '
                     f'<b>{_e(d.get("title"))}</b><div class="rec">Recommendation: {_e(d.get("recommendation"))}'
                     f'{" (asked " + _e(d.get("asked")) + ")" if d.get("asked") else ""}</div></li>')
    for r in m["human_prs"]:
        items.append(f'<li><span class="mono">#{_e(r["number"])}</span> {_pill("approval", "human")} '
                     f'<b>{_e(r["title"])}</b><div class="rec">Reserved: {_e(", ".join(r["classes"]))}. '
                     f'The reviewer has approved; the gate waits for you on {_e(r["head"])}.</div></li>')
    body = f'<ul class="plain">{"".join(items)}</ul>' if items else '<p class="empty">Nothing is waiting for the human.</p>'
    return ('<section class="panel" data-panel="human"><h2>Waiting on the human</h2><p class="lede">Decisions that '
            'carry a recommendation, and pull requests only the human can approve.</p>' + body + '</section>')


def _watchdog_panel(m: dict[str, Any]) -> str:
    found = m["watchdog"]
    lines = "".join("<li>" + _pill("stuck", "warn") + " " + _e(f) + "</li>" for f in found)
    body = (f'<ul class="plain">{lines}</ul>'
            if found else '<p class="empty">Nothing is stuck.</p>')
    note = "" if m["has_github"] else ('<p class="legend">Pull requests waiting for a verdict are not checked: no '
                                       'GitHub data was read.</p>')
    return ('<section class="panel" data-panel="watchdog"><h2>Watchdog</h2><p class="lede">Items stuck past their '
            'limit, idle roles, and pull requests that have waited too long for a verdict.</p>' + body + note +
            '</section>')


def _merges_panel(m: dict[str, Any]) -> str:
    rows = []
    for h in m["merges"]:
        verdict = _pill("holds", "good") if h["ok"] else _pill("ALARM", "bad")
        what = f'#{h["pr"]} {h["title"]}' if h.get("pr") else (h["title"] or "-")
        why = "" if h["ok"] else f'<div class="rec">{_e(h["why"])}</div>'
        rows.append(f'<tr><td class="mono">{_e(h["sha"][:7])}</td><td>{_e(what)}{why}</td><td>{_e(h["kind"])}</td>'
                    f'<td>{verdict}</td></tr>')
    body = (f'<div class="tablewrap"><table><thead><tr><th>Commit</th><th>Change</th><th>How</th><th>Audit</th></tr>'
            f'</thead><tbody>{"".join(rows)}</tbody></table></div>' if rows else
            '<p class="empty">No merges were read. Run with <span class="mono">--github</span>.</p>')
    return ('<section class="panel" data-panel="merges"><h2>Recent merges</h2><p class="lede">Each merge re-judged '
            'against the policy as it read at the merge, not as it reads now.</p>' + body + '</section>')


def render(m: dict[str, Any], *, nav: list[tuple[str, str]] | None = None) -> str:
    stamp = m["now"].strftime("%Y-%m-%d %H:%M UTC")
    focus = f'<div class="focus"><b>Focus</b>{_e(m["focus"])}</div>' if m["focus"] else ""
    body = (focus + _loop_panel(m) + _policy_panel(m) + _queues_panel(m) + _human_panel(m) + _watchdog_panel(m) +
            _merges_panel(m) + f'<footer>Generated {_e(stamp)} by onloop {_e(m["version"])} from '
            f'{_e(m["source"] or "the policy and the ledger")}. Every figure is computed from those inputs.</footer>')
    return page(f"{m['title']} dashboard", body, heading=m["title"], sub="the loop, as it stands", nav=nav)
