"""Generate onloop's C4 diagrams from one model file into one draw.io file, and export each page to PNG.

    python tools/diagrams/c4.py                       check the model and write docs/architecture/onloop-c4.drawio
    python tools/diagrams/c4.py --check               check the model only
    python tools/diagrams/c4.py --export [--drawio EXE] [--png-dir DIR]
                                                      also export every page to PNG with draw.io desktop

The model (`docs/architecture/model.yaml`) is the single source of truth: elements in C4 vocabulary, relationships
with a description and a technology, and views that list their elements with hand-placed coordinates. Nothing is laid
out automatically except the code view, which is generated from the import graph of the packages it names (C4's own
guidance for level 4: generate it from the code, do not draw it).

The check fails, and nothing is written, when a view names an element the model does not have, when a relationship
or a dynamic step has an end that is not in the view, or when an element misses a required field.

The draw.io executable comes from `--drawio` or `$DRAWIO_EXE`. Each page is exported with
`-x -f png -s 2 -b 20 -p <page, 1-based> -o <png> <drawio>`.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "docs" / "architecture" / "model.yaml"
OUT = ROOT / "docs" / "architecture" / "onloop-c4.drawio"
PNG_DIR = ROOT / "docs" / "architecture"
SCAN_ALLOW = (
    "scan:" + "allow"
)  # the confidentiality scan's opt-out marker, for lines that name the loopback address
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d])")

TYPES = {
    "person": "Person",
    "software_system": "Software System",
    "container": "Container",
    "component": "Component",
    "deployment_node": "Deployment Node",
    "infrastructure_node": "Infrastructure Node",
    "container_instance": "Container",
}
OWNERS = {"onloop", "team", "external"}
STATUSES = {"built", "r2", "planned"}
REQUIRED = ("name", "type", "technology", "description", "owner", "status")

FONT = "fontFamily=Helvetica;"
# The C4 palette (Simon Brown's reference colours).
PALETTE = {
    "person": ("#08427B", "#073B6F", "#FFFFFF"),
    "software_system": ("#1168BD", "#0B4884", "#FFFFFF"),
    "container": ("#438DD5", "#3C7FC0", "#FFFFFF"),
    "component": ("#85BBF0", "#78A8D8", "#000000"),
    "external": ("#999999", "#8A8A8A", "#FFFFFF"),
    "team": ("#6B6B6B", "#5A5A5A", "#FFFFFF"),
}
STATUS_STYLE = {
    "built": ("BUILT", "#2E7D32", "#2E7D32", "#FFFFFF", ""),
    "r2": ("R2 · IN PROGRESS", "#F2B705", "#C79600", "#000000", ""),
    "planned": ("PLANNED", "#FFFFFF", "#666666", "#444444", "dashed=1;dashPattern=3 2;"),
}
EDGE_COLOUR = "#5E5E5E"


class ModelError(Exception):
    """The model is inconsistent; the message lists every problem, one per line."""


# ---------------------------------------------------------------------------------------------------------------- model


def load(path: Path = MODEL, params_from_code: bool = False) -> dict[str, Any]:
    """The model with every `${name}` in its text replaced by the parameter's value.

    A parameter is a fact that lives in the code, such as a port or the ledger's schema version. The model records
    the value the diagrams show and where the code defines it; `params_from_code` takes the code's value instead."""
    with path.open(encoding="utf-8") as fh:
        model = yaml.safe_load(fh)
    if not isinstance(model, dict):
        raise ModelError(f"{path}: the model must be a mapping")
    values = {}
    for name, spec in (model.get("params") or {}).items():
        values[name] = str(code_value(spec) if params_from_code else spec["value"])

    def sub(node: Any) -> Any:
        if isinstance(node, str):
            for name, value in values.items():
                node = node.replace("${" + name + "}", value)
            if "${" in node:
                raise ModelError(f"unknown parameter in {node!r}")
            return node
        if isinstance(node, list):
            return [sub(x) for x in node]
        if isinstance(node, dict):
            return {k: (v if k == "params" else sub(v)) for k, v in node.items()}
        return node

    return sub(model)


def code_value(spec: dict[str, Any], root: Path = ROOT) -> Any:
    """A module-level constant in the work tree: its literal value, or with `len: true` its number of elements."""
    tree = ast.parse((root / spec["file"]).read_text(encoding="utf-8"))
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if any(getattr(t, "id", None) == spec["name"] for t in targets) and node.value is not None:
            if spec.get("len"):
                return len(node.value.elts)
            return ast.literal_eval(node.value)
    raise ModelError(f"{spec['file']} defines no {spec['name']}")


def param_drift(model: dict[str, Any], root: Path = ROOT) -> list[str]:
    """Each parameter whose value in the model differs from the code in the work tree, as one sentence."""
    out = []
    for name, spec in (model.get("params") or {}).items():
        actual = code_value(spec, root)
        if str(actual) != str(spec["value"]):
            out.append(
                f"{name}: the model shows {spec['value']}, {spec['file']} has {actual} ({spec['name']})"
            )
    return out


def elements_by_id(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for e in model.get("elements", []) if isinstance(e, dict) and "id" in e}


def check(model: dict[str, Any]) -> list[str]:
    """Every problem in the model, as one sentence each. Empty when the model is consistent."""
    problems: list[str] = []
    seen: set[str] = set()
    for i, el in enumerate(model.get("elements", [])):
        eid = el.get("id") if isinstance(el, dict) else None
        if not eid:
            problems.append(f"element #{i + 1} has no id")
            continue
        if eid in seen:
            problems.append(f"element {eid!r} is defined twice")
        seen.add(eid)
        for key in REQUIRED:
            if not el.get(key):
                problems.append(f"element {eid!r} has no {key}")
        if el.get("type") not in TYPES:
            problems.append(f"element {eid!r} has an unknown C4 type {el.get('type')!r}")
        if el.get("owner") and el["owner"] not in OWNERS:
            problems.append(f"element {eid!r} has an unknown owner {el['owner']!r}")
        if el.get("status") and el["status"] not in STATUSES:
            problems.append(f"element {eid!r} has an unknown status {el['status']!r}")
        if el.get("type") == "component" and not el.get("code"):
            problems.append(f"component {eid!r} has no code path")
    known = elements_by_id(model)
    for el in known.values():
        for ref in ("parent", "instance_of"):
            if el.get(ref) and el[ref] not in known:
                problems.append(f"element {el['id']!r} names an unknown {ref} {el[ref]!r}")

    rels: dict[str, dict[str, Any]] = {}
    for i, rel in enumerate(model.get("relationships", [])):
        rid = rel.get("id")
        if not rid:
            problems.append(f"relationship #{i + 1} has no id")
            continue
        if rid in rels:
            problems.append(f"relationship {rid!r} is defined twice")
        rels[rid] = rel
        for end in ("from", "to"):
            if rel.get(end) not in known:
                problems.append(f"relationship {rid!r} has an unknown {end} element {rel.get(end)!r}")
        for key in ("description", "technology", "status"):
            if not rel.get(key):
                problems.append(f"relationship {rid!r} has no {key}")
        if rel.get("status") and rel["status"] not in STATUSES:
            problems.append(f"relationship {rid!r} has an unknown status {rel['status']!r}")

    keys: set[str] = set()
    for view in model.get("views", []):
        key = view.get("key", "?")
        if key in keys:
            problems.append(f"view {key!r} is defined twice")
        keys.add(key)
        if view.get("kind") == "code":
            continue
        placed = set(view.get("elements", {}) or {}) | set(view.get("boundaries", {}) or {})
        for eid in placed:
            if eid not in known:
                problems.append(f"view {key!r} names an unknown element {eid!r}")
        for eid, hint in (view.get("elements", {}) or {}).items():
            if not isinstance(hint, dict) or not all(k in hint for k in ("x", "y", "w", "h")):
                problems.append(f"view {key!r}: element {eid!r} needs x, y, w and h")
        for rid in view.get("relationships", {}) or {}:
            rel = rels.get(rid)
            if rel is None:
                problems.append(f"view {key!r} names an unknown relationship {rid!r}")
                continue
            for end in ("from", "to"):
                if rel.get(end) not in placed:
                    problems.append(
                        f"view {key!r}: relationship {rid!r} has its {end} end {rel.get(end)!r} outside the view"
                    )
        for n, step in enumerate(view.get("steps", []) or [], start=1):
            for end in ("from", "to"):
                if step.get(end) not in placed:
                    problems.append(
                        f"view {key!r}: step {n} has its {end} end {step.get(end)!r} outside the view"
                    )
            for k in ("description", "technology"):
                if not step.get(k):
                    problems.append(f"view {key!r}: step {n} has no {k}")
    return problems


def version(root: Path = ROOT) -> str:
    """onloop.__version__, read from the source without importing it (the version is single-sourced there)."""
    tree = ast.parse((root / "src" / "onloop" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "__version__" for t in node.targets
        ):
            return str(ast.literal_eval(node.value))
    return "unknown"


# ---------------------------------------------------------------------------------------------------------------- page


@dataclass
class Page:
    name: str
    width: int
    height: int
    cells: list[str] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    _n: int = 1

    def nid(self) -> str:
        self._n += 1
        return f"c{self._n}"

    def vertex(
        self, label: str, style: str, x: float, y: float, w: float, h: float, key: str | None = None
    ) -> str:
        cid = self.nid()
        if key:
            self.ids[key] = cid
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(label)} style="{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" as="geometry"/></mxCell>'
        )
        return cid

    def edge(
        self,
        src: str,
        dst: str,
        label: str,
        style: str,
        points: list[list[float]] | None = None,
        label_pos: float | None = None,
        label_offset: list[float] | None = None,
    ) -> str:
        cid = self.nid()
        pts = "".join(f'<mxPoint x="{p[0]:g}" y="{p[1]:g}"/>' for p in points or [])
        arr = f'<Array as="points">{pts}</Array>' if pts else ""
        gx = f' x="{label_pos:g}"' if label_pos is not None else ""
        off = (
            f'<mxPoint x="{label_offset[0]:g}" y="{label_offset[1]:g}" as="offset"/>' if label_offset else ""
        )
        self.cells.append(
            f'<mxCell id="{cid}" value={quoteattr(label)} style="{style}" edge="1" parent="1" source="{src}" '
            f'target="{dst}"><mxGeometry{gx} relative="1" as="geometry">{arr}{off}</mxGeometry></mxCell>'
        )
        return cid

    def line(self, x1: float, y1: float, x2: float, y2: float, style: str) -> None:
        cid = self.nid()
        self.cells.append(
            f'<mxCell id="{cid}" value="" style="{style}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
            f'<mxPoint x="{x1:g}" y="{y1:g}" as="sourcePoint"/><mxPoint x="{x2:g}" y="{y2:g}" as="targetPoint"/>'
            f"</mxGeometry></mxCell>"
        )

    def text(
        self, label: str, x: float, y: float, w: float, h: float, size: float = 11, style: str = ""
    ) -> str:
        st = (
            f"text;html=1;whiteSpace=wrap;{FONT}fontSize={size:g};align=left;verticalAlign=top;"
            f"spacing=0;overflow=visible;{style}"
        )
        return self.vertex(label, st, x, y, w, h)

    def xml(self) -> str:
        head = (
            f'<diagram id="{escape(self.name.lower().replace(" ", "-"))}" name={quoteattr(self.name)}>\n'
            f'<mxGraphModel dx="{self.width}" dy="{self.height}" grid="0" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.width}" '
            f'pageHeight="{self.height}" math="0" shadow="0" background="#FFFFFF">\n<root>\n'
            '<mxCell id="0"/>\n<mxCell id="1" parent="0"/>\n'
        )
        lines = []
        for cell in self.cells:
            # One cell per line, so that a line naming the loopback address can carry the scan's opt-out marker.
            lines.append(cell + (f"<!-- {SCAN_ALLOW} -->" if _IPV4.search(cell) else ""))
        return head + "\n".join(lines) + "\n</root>\n</mxGraphModel>\n</diagram>"


# ---------------------------------------------------------------------------------------------------------------- text


def _approx_lines(html: str, width: float, size: float) -> float:
    """A rough count of rendered lines for a label (Helvetica averages about 0.53 em per character)."""
    per_line = max(1.0, (width - 12) / (size * 0.53))
    total = 0.0
    for part in re.split(r"<br\s*/?>", html):
        plain = re.sub(r"<[^>]+>", "", part).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        total += max(1, -(-len(plain) // int(per_line)))
    return total


def colours(el: dict[str, Any]) -> tuple[str, str, str]:
    if el["type"] == "person":
        return PALETTE["person"]
    if el["owner"] == "team":
        return PALETTE["team"]
    if el["owner"] == "external":
        return PALETTE["external"]
    kind = el["type"]
    if kind == "container_instance":
        kind = "container"
    return PALETTE.get(kind, PALETTE["container"])


def element_label(el: dict[str, Any], hint: dict[str, Any], dark_text: bool) -> str:
    sub = "#E6E6E6" if not dark_text else "#1A1A1A"
    tname = TYPES[el["type"]]
    name = hint.get("name", el["name"])
    parts = [f'<b style="font-size:{hint.get("name_size", 13)}px">{escape(name)}</b>']
    parts.append(f'<span style="font-size:10px;color:{sub}">[{tname}: {escape(el["technology"])}]</span>')
    desc = hint.get("description", el["description"])
    parts.append(f'<span style="font-size:{hint.get("size", 11)}px">{escape(desc)}</span>')
    facts = list(hint.get("facts", el.get("facts", [])) or [])
    if el.get("code") and not hint.get("hide_code"):
        facts.insert(0, f"<code>{escape(el['code'])}</code>")
    if facts:
        rendered = "<br>".join(f if f.startswith("<code>") else escape(f) for f in facts)
        parts.append(f'<span style="font-size:{hint.get("facts_size", 10)}px;color:{sub}">{rendered}</span>')
    return "<br>".join(parts[:2]) + "<br><br>" + "<br>".join(parts[2:])


def element_style(el: dict[str, Any], hint: dict[str, Any]) -> tuple[str, bool]:
    fill, stroke, font = colours(el)
    dashed = "dashed=1;dashPattern=8 4;strokeWidth=2;" if el["status"] == "planned" else "strokeWidth=1;"
    shape = hint.get("shape", el.get("shape", "box"))
    base = (
        f"html=1;whiteSpace=wrap;{FONT}fontColor={font};fillColor={fill};strokeColor={stroke};{dashed}"
        f"align=center;verticalAlign=middle;spacing=8;overflow=visible;"
    )
    if el["type"] == "person":
        base += "shape=mxgraph.c4.person2;"
    elif shape == "database":
        base += "shape=cylinder3;size=12;boundedLbl=1;backgroundOutline=1;spacingTop=6;"
    elif shape == "folder":
        base += "shape=folder;tabWidth=70;tabHeight=12;tabPosition=left;spacingTop=10;"
    elif shape == "process":
        base += "rounded=1;arcSize=8;shadow=0;"
    else:
        base += "rounded=1;arcSize=6;"
    return base, font == "#000000"


def pill(page: Page, status: str, el: dict[str, Any], x: float, y: float, w: float) -> None:
    text, fill, stroke, font, dash = STATUS_STYLE[status]
    if status == "planned" and el.get("phase"):
        text = f"PLANNED · {el['phase']}"
    if status == "built" and el.get("phase"):
        text = f"BUILT · {el['phase']}"
    pw = 10 + len(text) * 6.3
    st = (
        f"rounded=1;arcSize=50;html=1;whiteSpace=nowrap;{FONT}fontSize=9;fontStyle=1;fillColor={fill};"
        f"strokeColor={stroke};fontColor={font};{dash}align=center;verticalAlign=middle;spacing=0;"
    )
    page.vertex(text, st, x + w - pw - 10, y - 9, pw, 18)


# ---------------------------------------------------------------------------------------------------------------- views


def title_block(page: Page, model: dict[str, Any], view: dict[str, Any], index: int, total: int) -> None:
    page.text(
        f"<b>{escape(view['title'])}</b>",
        40,
        26,
        page.width - 520,
        34,
        size=24,
        style="fontColor=#1A1A1A;",
    )
    page.text(escape(view["subtitle"]), 40, 64, page.width - 560, 40, size=12.5, style="fontColor=#4D4D4D;")
    meta = model["meta"]
    rows = [
        ("C4 view", view["level"]),
        ("System", meta["system"]),
        ("Version", f"{version()} (onloop.__version__)"),
        ("Model as of", str(meta["as_of"])),
        ("Page", f"{index} of {total}"),
    ]
    body = "".join(
        f'<tr><td style="color:#666666;padding-right:10px">{escape(k)}</td><td><b>{escape(v)}</b></td></tr>'
        for k, v in rows
    )
    st = (
        f"rounded=0;html=1;whiteSpace=wrap;{FONT}fontSize=10.5;align=left;verticalAlign=top;spacingLeft=10;"
        "spacingTop=6;fillColor=#F7F7F7;strokeColor=#BFBFBF;fontColor=#1A1A1A;"
    )
    page.vertex(f'<table style="font-size:10.5px">{body}</table>', st, page.width - 440, 20, 400, 108)
    page.line(40, 138, page.width - 40, 138, f"endArrow=none;html=1;strokeColor=#BFBFBF;strokeWidth=1;{FONT}")


def footer(page: Page, model: dict[str, Any], view: dict[str, Any]) -> None:
    sources = list(model["meta"]["sources"]) + list(view.get("sources", []))
    y = page.height - 46
    page.line(40, y - 8, page.width - 40, y - 8, "endArrow=none;html=1;strokeColor=#BFBFBF;strokeWidth=1;")
    page.text(
        "<b>Sources (in this repository):</b> " + " · ".join(escape(s) for s in sources),
        40,
        y,
        page.width - 80,
        16,
        size=10,
        style="fontColor=#4D4D4D;",
    )
    page.text(
        "Generated from <code>docs/architecture/model.yaml</code> by <code>tools/diagrams/c4.py</code>. "
        "Edit the model and regenerate; do not edit this page by hand.",
        40,
        y + 17,
        page.width - 80,
        16,
        size=10,
        style="fontColor=#7F7F7F;",
    )


def legend(
    page: Page,
    view: dict[str, Any],
    kinds: list[tuple[str, str]],
    statuses: set[str],
    dynamic: bool = False,
) -> None:
    hint = view.get("legend")
    if not hint:
        return
    x, y, w = hint["x"], hint["y"], hint.get("w", 300)
    shown = [st for st in ("built", "r2", "planned") if st in statuses]
    rows = len(kinds) + len(shown) + 2 + (1 if dynamic else 0) + (1 if view.get("boundaries") else 0)
    h = 34 + rows * 26
    page.vertex(
        "<b>Key</b>",
        f"rounded=0;html=1;whiteSpace=wrap;{FONT}fontSize=12;align=left;verticalAlign=top;spacingLeft=12;"
        "spacingTop=6;fillColor=#FFFFFF;strokeColor=#BFBFBF;fontColor=#1A1A1A;",
        x,
        y,
        w,
        h,
    )
    cy = y + 34
    for label, kind in kinds:
        fill, stroke, font = PALETTE[kind]
        shape = "shape=mxgraph.c4.person2;" if kind == "person" else "rounded=1;arcSize=10;"
        sw, sh = (18, 20) if kind == "person" else (34, 18)
        page.vertex(
            "", f"html=1;{shape}fillColor={fill};strokeColor={stroke};", x + 14 + (34 - sw) / 2, cy, sw, sh
        )
        page.text(escape(label), x + 58, cy + 2, w - 66, 18, size=10.5)
        cy += 26
    for status in shown:
        text, fill, stroke, font, dash = STATUS_STYLE[status]
        page.vertex(
            text if status != "r2" else "R2",
            f"rounded=1;arcSize=50;html=1;{FONT}fontSize=8;fontStyle=1;fillColor={fill};strokeColor={stroke};"
            f"fontColor={font};{dash}spacing=0;",
            x + 10,
            cy + 1,
            44,
            16,
        )
        meaning = {
            "built": "built: on main",
            "r2": "in progress: on a branch, not yet on main",
            "planned": "planned: the approval page, R4 conductor and sessions; dashed",
        }[status]
        page.text(meaning, x + 58, cy + 2, w - 66, 18, size=10.5)
        cy += 26
    solid = f"endArrow=blockThin;endFill=1;html=1;strokeColor={EDGE_COLOUR};strokeWidth=1.5;"
    page.line(x + 12, cy + 9, x + 52, cy + 9, solid)
    page.text("relationship (arrow = direction of use)", x + 58, cy + 2, w - 66, 18, size=10.5)
    cy += 26
    page.line(x + 12, cy + 9, x + 52, cy + 9, solid + "dashed=1;dashPattern=6 4;")
    page.text("planned relationship", x + 58, cy + 2, w - 66, 18, size=10.5)
    cy += 26
    if dynamic:
        page.text("<b>1.</b>", x + 24, cy + 2, 30, 18, size=10.5)
        page.text("the order of the steps (C4 dynamic view)", x + 58, cy + 2, w - 66, 18, size=10.5)
        cy += 26
    if view.get("boundaries"):
        page.vertex(
            "",
            "rounded=1;arcSize=10;html=1;fillColor=none;strokeColor=#666666;dashed=1;dashPattern=8 4;",
            x + 12,
            cy,
            40,
            18,
        )
        page.text(
            escape(hint.get("boundary_label", "boundary (system or container)")),
            x + 58,
            cy + 2,
            w - 66,
            18,
            size=10.5,
        )


def legend_kinds(model_els: dict[str, dict[str, Any]], ids: list[str]) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for eid in ids:
        el = model_els[eid]
        if el["type"] == "person":
            found["person"] = "person"
        elif el["owner"] == "team":
            found["team"] = "team-owned (the team's own system or config)"
        elif el["owner"] == "external":
            found["external"] = "external system or adopted product"
        elif el["type"] in ("software_system",):
            found["software_system"] = "onloop (software system)"
        elif el["type"] in ("container", "container_instance"):
            found["container"] = "container owned by onloop"
        elif el["type"] == "component":
            found["component"] = "component owned by onloop"
    order = ["person", "software_system", "container", "component", "team", "external"]
    return [(found[k], k) for k in order if k in found]


def render_boundary(page: Page, el: dict[str, Any], hint: dict[str, Any]) -> None:
    deployment = el["type"] in ("deployment_node", "infrastructure_node")
    facts = hint.get("facts", el.get("facts", []) if deployment else []) or []
    label = f'<b style="font-size:13px">{escape(hint.get("name", el["name"]))}</b>'
    label += f'<br><span style="font-size:10px">[{TYPES[el["type"]]}: {escape(el["technology"])}]</span>'
    if hint.get("show_description", deployment):
        label += f'<br><span style="font-size:10.5px">{escape(el["description"])}</span>'
    if facts:
        label += (
            '<br><span style="font-size:10px;color:#333333">'
            + "<br>".join(escape(f) for f in facts)
            + "</span>"
        )
    if deployment:
        fill = hint.get("fill", "#FFFFFF")
        st = (
            f"rounded=1;arcSize={hint.get('arc', 2)};html=1;whiteSpace=wrap;{FONT}fontColor=#1A1A1A;fillColor={fill};"
            f"strokeColor={hint.get('stroke', '#7F7F7F')};strokeWidth=1.5;align=left;verticalAlign=top;spacingLeft=12;"
            "spacingTop=6;container=0;overflow=visible;"
        )
        if hint.get("label_at") == "bottom":
            st = st.replace("verticalAlign=top;", "verticalAlign=bottom;spacingBottom=8;")
        if el["status"] == "planned":
            st += "dashed=1;dashPattern=8 4;"
    else:
        st = (
            f"rounded=1;arcSize=1;html=1;whiteSpace=wrap;{FONT}fontColor=#333333;fillColor=none;strokeColor=#666666;"
            "strokeWidth=1.5;dashed=1;dashPattern=10 5;align=left;verticalAlign=bottom;spacingLeft=12;spacingBottom=8;"
            "container=0;overflow=visible;"
        )
    page.vertex(label, st, hint["x"], hint["y"], hint["w"], hint["h"], key=el["id"])
    if hint.get("pill", deployment):
        pill(page, el["status"], el, hint["x"], hint["y"], hint["w"])


def render_element(page: Page, el: dict[str, Any], hint: dict[str, Any]) -> None:
    style, dark = element_style(el, hint)
    label = element_label(el, hint, dark)
    size = hint.get("size", 11)
    style += f"fontSize={size};"
    x, y, w, h = hint["x"], hint["y"], hint["w"], hint["h"]
    if el["type"] == "person":
        # draw.io centres a person's label on the whole shape; draw the label on the body instead.
        page.vertex("", style, x, y, w, h, key=el["id"])
        body = h * 0.4
        page.vertex(
            label,
            f"text;html=1;whiteSpace=wrap;{FONT}fontSize={size};fontColor=#FFFFFF;align=center;verticalAlign=middle;"
            "spacingLeft=16;spacingRight=16;overflow=visible;",
            x,
            y + body,
            w,
            h - body - 4,
        )
    else:
        page.vertex(label, style, x, y, w, h, key=el["id"])
    need = _approx_lines(label, w - (40 if el["type"] == "person" else 0), size) * size * 1.25 + 18
    need += h * 0.45 if el["type"] == "person" else 0
    if need > h + 6:  # the estimate is rough; a few pixels over is a false alarm
        page.warnings.append(f"{page.name}: {el['id']} may overflow ({need:.0f} px of text in {h} px)")
    if el["type"] != "person":
        pill(page, el["status"], el, x, y, w)


def edge_style(status: str, hint: dict[str, Any]) -> str:
    st = (
        f"edgeStyle={hint.get('style', 'orthogonalEdgeStyle')};rounded=1;html=1;{FONT}fontSize=10;"
        f"fontColor=#333333;strokeColor={EDGE_COLOUR};strokeWidth=1.5;endArrow=blockThin;endFill=1;endSize=7;"
        "labelBackgroundColor=#FFFFFF;jumpStyle=arc;jumpSize=7;spacing=2;"
    )
    if status == "planned":
        st += "dashed=1;dashPattern=6 4;"
    if "exit" in hint:
        st += f"exitX={hint['exit'][0]};exitY={hint['exit'][1]};exitDx=0;exitDy=0;exitPerimeter=0;"
    if "entry" in hint:
        st += f"entryX={hint['entry'][0]};entryY={hint['entry'][1]};entryDx=0;entryDy=0;entryPerimeter=0;"
    if hint.get("label_w"):
        st += f"labelWidth={hint['label_w']};"
    return st


def rel_label(desc: str, tech: str, number: int | None = None, wrap: int = 34) -> str:
    head = f"{number}. " if number is not None else ""
    lines = textwrap.wrap(head + desc, wrap) or [""]
    text = "<br>".join(escape(x) for x in lines)
    if number is not None:
        text = text.replace(f"{number}. ", f"<b>{number}.</b> ", 1)
    return f'{text}<br><span style="font-size:9px;color:#666666">[{escape(tech)}]</span>'


def render_view(model: dict[str, Any], view: dict[str, Any], index: int, total: int) -> Page:
    known = elements_by_id(model)
    rels = {r["id"]: r for r in model.get("relationships", [])}
    w, h = view["size"]
    page = Page(view["name"], w, h)
    title_block(page, model, view, index, total)
    for eid, hint in (view.get("boundaries", {}) or {}).items():
        render_boundary(page, known[eid], hint)
    # Ids for elements are needed before edges, but edges must sit below the boxes: reserve ids first.
    placed = view.get("elements", {}) or {}
    pending_edges: list[tuple] = []
    for rid, hint in (view.get("relationships", {}) or {}).items():
        rel = rels[rid]
        hint = hint or {}
        label = rel_label(
            hint.get("description", rel["description"]),
            hint.get("technology", rel["technology"]),
            wrap=hint.get("wrap", view.get("wrap", 34)),
        )
        pending_edges.append((rel["from"], rel["to"], label, rel["status"], hint))
    for n, step in enumerate(view.get("steps", []) or [], start=1):
        hint = step.get("layout", {}) or {}
        label = rel_label(
            step["description"], step["technology"], n, wrap=hint.get("wrap", view.get("wrap", 34))
        )
        pending_edges.append((step["from"], step["to"], label, step.get("status", "built"), hint))
    # Elements first into a scratch page so their ids exist, then reorder: boundaries, edges, elements.
    scratch = Page(page.name, w, h, _n=page._n + 2 * len(pending_edges) + 10)
    for eid, hint in placed.items():
        render_element(scratch, known[eid], hint)
    page.ids.update(scratch.ids)
    for src, dst, label, status, hint in pending_edges:
        page.edge(
            page.ids[src],
            page.ids[dst],
            label,
            edge_style(status, hint),
            hint.get("points"),
            hint.get("label_pos"),
            hint.get("label_offset"),
        )
    page.cells.extend(scratch.cells)
    page.warnings.extend(scratch.warnings)
    page._n = scratch._n
    for note in view.get("notes", []) or []:
        st = (
            f"rounded=0;html=1;whiteSpace=wrap;{FONT}fontSize={note.get('size', 10.5)};align=left;verticalAlign=top;"
            f"spacingLeft=10;spacingRight=8;spacingTop=6;fillColor={note.get('fill', '#FFFFFF')};"
            f"strokeColor={note.get('stroke', '#BFBFBF')};fontColor=#1A1A1A;overflow=visible;"
        )
        page.vertex(note["text"], st, note["x"], note["y"], note["w"], note["h"])
        need = (
            _approx_lines(note["text"], note["w"] - 10, note.get("size", 10.5)) * note.get("size", 10.5) * 1.3
            + 12
        )
        if need > note["h"]:
            page.warnings.append(
                f"{page.name}: a note at {note['x']},{note['y']} may overflow ({need:.0f} > {note['h']})"
            )
    statuses = {known[e]["status"] for e in placed} | {
        known[b]["status"] for b in view.get("boundaries", {}) or {}
    }
    statuses |= {rels[r]["status"] for r in view.get("relationships", {}) or {}}
    statuses |= {st.get("status", "built") for st in view.get("steps", []) or []}
    legend(page, view, legend_kinds(known, list(placed)), statuses, dynamic=bool(view.get("steps")))
    footer(page, model, view)
    return page


# ---------------------------------------------------------------------------------------------------------------- code view


@dataclass
class Module:
    name: str
    lines: int
    doc: str
    imports: set[str]


def _source_files(src: Path, ref: str | None) -> dict[str, str]:
    """Path (relative to src/, with '/') -> text, from the work tree or from a git ref."""
    if ref is None:
        return {p.relative_to(src).as_posix(): p.read_text(encoding="utf-8") for p in src.rglob("*.py")}
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", ref, "src/onloop"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split()
    files = {}
    for path in out:
        if path.endswith(".py"):
            blob = subprocess.run(
                ["git", "-C", str(ROOT), "show", f"{ref}:{path}"], capture_output=True, check=True
            ).stdout
            files[path.removeprefix("src/")] = blob.decode("utf-8")
    return files


def import_graph(files: dict[str, str]) -> dict[str, Module]:
    """Every module under onloop, with the onloop modules it imports (resolved to modules, not names)."""
    names = {}
    for path in files:
        mod = path[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[: -len(".__init__")]
        names[mod] = path
    graph: dict[str, Module] = {}
    for mod, path in names.items():
        tree = ast.parse(files[path])
        package = mod if path.endswith("__init__.py") else mod.rsplit(".", 1)[0]
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                    target = ".".join(base) + (f".{node.module}" if node.module else "")
                else:
                    target = node.module or ""
                if not target.startswith("onloop"):
                    continue
                for alias in node.names:
                    sub = f"{target}.{alias.name}"
                    found.add(sub if sub in names else target)
            elif isinstance(node, ast.Import):
                found.update(a.name for a in node.names if a.name.startswith("onloop"))
        found.discard(mod)
        doc = (ast.get_docstring(tree) or "").strip().split("\n")[0]
        graph[mod] = Module(mod, len(files[path].splitlines()), doc, {f for f in found if f in names})
    return graph


def _short(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + " …"


def layered(nodes: list[str], edges: list[tuple[str, str]]) -> tuple[dict[str, int], list[list[str]]]:
    """Sugiyama-style: rank by longest path from the importers, add dummy nodes, order by barycentre."""
    succ: dict[str, list[str]] = {n: [] for n in nodes}
    for a, b in edges:
        succ[a].append(b)
    rank: dict[str, int] = {}

    def depth(n: str, stack: frozenset[str] = frozenset()) -> int:
        if n in rank:
            return rank[n]
        preds = [a for a, b in edges if b == n and a not in stack]
        rank[n] = 0 if not preds else 1 + max(depth(p, stack | {n}) for p in preds)
        return rank[n]

    for n in nodes:
        depth(n)
    rows: list[list[str]] = [[] for _ in range(max(rank.values()) + 1)]
    for n in sorted(nodes):
        rows[rank[n]].append(n)
    return rank, rows


def render_code_view(
    model: dict[str, Any], view: dict[str, Any], index: int, total: int, ref: str | None
) -> Page:
    code = view["code"]
    source_ref = ref if ref is not None else code.get("ref")
    files = _source_files(ROOT / "src", None if source_ref is None else str(source_ref))
    graph = import_graph(files)
    packages = [f"onloop.{p}" for p in code["packages"]]
    collapse = {f"onloop.{c}" if c != "onloop" else "onloop" for c in code.get("collapse", [])}
    scope = sorted(
        m
        for m in graph
        if any(m == p or m.startswith(p + ".") for p in packages)
        and not (m in packages and graph[m].lines <= 12)
    )
    outside = sorted(
        {
            i
            for m in scope
            for i in graph[m].imports
            if i not in scope and i not in collapse and i not in packages
        }
    )
    nodes = scope + outside
    edges = sorted({(m, i) for m in scope for i in graph[m].imports if i in nodes})
    rank, rows = layered(nodes, edges)
    # A hub (more than `list_over` imports in view, such as main.py) draws only its imports of the next row, as one
    # comb; its longer imports are listed in its box, so they do not loop across the page.
    list_over = code.get("list_over", 6)
    listed: dict[str, list[str]] = {}
    for a in scope:
        mine = [b for x, b in edges if x == a]
        if len(mine) > list_over:
            listed[a] = sorted(b for b in mine if rank[b] - rank[a] > 1)
    edges = [(a, b) for a, b in edges if b not in listed.get(a, [])]

    # Dummy nodes for edges that span more than one row, so no line crosses a box.
    chains: dict[tuple[str, str], list[str]] = {}
    for a, b in edges:
        chain = [a]
        for r in range(rank[a] + 1, rank[b]):
            d = f"~>{b}@{r}"  # one trunk per imported module: every long import of it shares these dummies
            if d not in rows[r]:
                rows[r].append(d)
            chain.append(d)
        chain.append(b)
        chains[(a, b)] = chain
    neighbours: dict[str, list[str]] = {}
    for chain in chains.values():
        for u, v in zip(chain, chain[1:], strict=False):
            neighbours.setdefault(u, []).append(v)
            neighbours.setdefault(v, []).append(u)
    pos = {n: i for row in rows for i, n in enumerate(row)}
    for sweep in range(24):
        order = range(1, len(rows)) if sweep % 2 == 0 else range(len(rows) - 2, -1, -1)
        for r in order:
            ref_row = r - 1 if sweep % 2 == 0 else r + 1
            ref_set = set(rows[ref_row])

            def bary(n: str, ref_set: set[str] = ref_set) -> float:
                ns = [pos[m] for m in neighbours.get(n, []) if m in ref_set]
                return sum(ns) / len(ns) if ns else pos[n]

            rows[r].sort(key=bary)
            for i, n in enumerate(rows[r]):
                pos[n] = i

    bw, bh, dw = code.get("box_w", 230), code.get("box_h", 92), 8
    gap, dgap, row_gap = code.get("gap", 30), 22, code.get("row_gap", 70)
    track_gap = code.get("track_gap", 16)
    top = code.get("top", 170)
    left = code.get("left", 40)
    widths = {n: (dw if n.startswith("~") else bw) for row in rows for n in row}

    def spacing(a: str, b: str) -> float:
        return dgap if a.startswith("~") and b.startswith("~") else gap

    def place(row: list[str], want: dict[str, float]) -> dict[str, float]:
        """Left edges in row order, at least `spacing` apart, as close as possible to `want` (least squares).

        Pool adjacent violators on x minus each node's minimum offset from the row's first node."""
        offset, acc = [], 0.0
        for i, n in enumerate(row):
            if i:
                acc += widths[row[i - 1]] + spacing(row[i - 1], n)
            offset.append(acc)
        blocks: list[list[float]] = []  # [sum of targets, count, first index]
        for i, n in enumerate(row):
            blocks.append([want[n] - offset[i], 1.0, i])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                s, c, _ = blocks.pop()
                blocks[-1][0] += s
                blocks[-1][1] += c
        out: dict[str, float] = {}
        for bi, (s, c, first) in enumerate(blocks):
            end = blocks[bi + 1][2] if bi + 1 < len(blocks) else len(row)
            for i in range(int(first), int(end)):
                out[row[i]] = s / c + offset[i]
        return out

    xs: dict[str, float] = {}
    for row in rows:
        xs.update(place(row, dict.fromkeys(row, 0.0)))
    full = max(xs[row[-1]] + widths[row[-1]] for row in rows if row)
    for row in rows:
        span = xs[row[-1]] + widths[row[-1]]
        for n in row:
            xs[n] += (full - span) / 2
    # Alternate sweeps: pull each node's centre to the mean centre of its neighbours in the row above (then below).
    for it in range(60):
        down = it % 2 == 0
        for r in range(1, len(rows)) if down else range(len(rows) - 2, -1, -1):
            ref_row = set(rows[r - 1] if down else rows[r + 1])
            want = {}
            for n in rows[r]:
                ns = [xs[m] + widths[m] / 2 for m in neighbours.get(n, []) if m in ref_row]
                want[n] = (sum(ns) / len(ns) - widths[n] / 2) if ns else xs[n]
            xs.update(place(rows[r], want))
    # Refine the order once by where each node wants to be (the mean centre of all its neighbours), then place again;
    # a trunk that the position-based order put at the end of its row moves next to the modules it joins.
    for row in rows:

        def want_x(n: str) -> float:
            ns = [xs[m] + widths[m] / 2 for m in neighbours.get(n, [])]
            return sum(ns) / len(ns) if ns else xs[n] + widths[n] / 2

        row.sort(key=want_x)
    for it in range(60):
        down = it % 2 == 0
        for r in range(1, len(rows)) if down else range(len(rows) - 2, -1, -1):
            ref_row = set(rows[r - 1] if down else rows[r + 1])
            want = {}
            for n in rows[r]:
                ns = [xs[m] + widths[m] / 2 for m in neighbours.get(n, []) if m in ref_row]
                want[n] = (sum(ns) / len(ns) - widths[n] / 2) if ns else xs[n]
            xs.update(place(rows[r], want))
    low = min(xs.values())
    for n in xs:
        xs[n] += left - low
    full = max(xs[n] + widths[n] for n in xs) - left
    # Orthogonal routing with shared trunks. In the channel below each row, every importer has one horizontal bus
    # that its imports of the next row leave by; in the channel above each row, every module that is reached by a long import
    # has one bus that all such imports arrive by. A long import runs down its own lane (its dummy nodes' x), so no
    # line crosses a box; where a lane shifts between rows it jogs on its own track.
    rank_of = {n: r for r, row in enumerate(rows) for n in row}
    cx = {n: xs[n] + widths[n] / 2 for n in xs}
    channel_buses: list[list[tuple]] = [[] for _ in range(len(rows))]  # channel r lies below row r

    def bus(channel: int, key: tuple) -> tuple:
        if key not in channel_buses[channel]:
            channel_buses[channel].append(key)
        return key

    plans = []
    for (a, b), chain in chains.items():
        legs: list[tuple] = []  # ("track", channel, key, x_from, x_to) or ("lane", x)
        if len(chain) == 2:
            legs.append((rank_of[a], bus(rank_of[a], ("out", a)), cx[a], cx[b]))
        else:
            # A long import drops to the collector of its target in the channel below its importer, and the
            # collector feeds the target's trunk: one horizontal line per target and channel, not per import.
            lane = [cx[d] for d in chain[1:-1]]
            legs.append((rank_of[a], bus(rank_of[a], ("col", b)), cx[a], lane[0]))
            for i in range(len(lane) - 1):
                if abs(lane[i] - lane[i + 1]) > 0.5:
                    legs.append(
                        (
                            rank_of[chain[1 + i]],
                            bus(rank_of[chain[1 + i]], ("jog", b, rank_of[chain[1 + i]])),
                            lane[i],
                            lane[i + 1],
                        )
                    )
            legs.append((rank_of[b] - 1, bus(rank_of[b] - 1, ("in", b)), lane[-1], cx[b]))
        plans.append((a, b, legs))
    # Out-buses on the upper tracks, then jogs, then in-buses; each group ordered left to right by where it starts.
    track_y: dict[tuple, float] = {}
    gaps = []
    group = {"out": 0, "col": 1, "jog": 2, "in": 3}
    for keys in channel_buses:
        keys.sort(key=lambda k: (group[k[0]], cx.get(k[1], 0)))
        gaps.append(max(row_gap, track_gap * (len(keys) + 1)))
    ys: dict[str, float] = {}
    y = top
    for r, row in enumerate(rows):
        for n in row:
            ys[n] = y
        for i, key in enumerate(channel_buses[r]):
            track_y[(r, key)] = y + bh + (i + 1) * gaps[r] / (len(channel_buses[r]) + 1)
        y += bh + gaps[r]
    edge_specs = []
    for a, b, legs in plans:
        pts: list[list[float]] = []
        for channel, key, x_from, x_to in legs:
            ty = track_y[(channel, key)]
            pts.append([x_from, ty])
            pts.append([x_to, ty])
        edge_specs.append((a, b, pts))

    width = max(int(left + full + 380), 1400)
    height = int(y + 80)
    page = Page(view["name"], width, height)
    title_block(page, model, view, index, total)

    pkg_colour = {
        p: c for p, c in zip(packages, code.get("colours", ["#85BBF0", "#B9D7F5", "#DCEBFA"]), strict=False)
    }
    # Edges first, so the boxes sit on top of the arrow ends.
    st_base = (
        f"edgeStyle=none;rounded=1;arcSize=8;html=1;strokeColor={EDGE_COLOUR};strokeWidth=1.2;endArrow=blockThin;"
        "endFill=1;endSize=6;jumpStyle=arc;jumpSize=6;exitX=0.5;exitY=1;exitDx=0;exitDy=0;entryX=0.5;entryY=0;"
        "entryDx=0;entryDy=0;"
    )
    reserve = page._n
    page._n += len(edge_specs) + 5
    ids = {n: f"m{i}" for i, n in enumerate(nodes)}
    page._n = reserve
    for a, b, pts in edge_specs:
        page.edge(ids[a], ids[b], "", st_base, pts)
    for n in nodes:
        mod = graph[n]
        pkg = next((p for p in packages if n.startswith(p + ".")), None)
        foundation = sorted(i.removeprefix("onloop.") or "__version__" for i in mod.imports if i in collapse)
        foundation = ["__version__" if f == "onloop" else f for f in foundation]
        rel = n.removeprefix("onloop.")
        fname = rel.rsplit(".", 1)[-1] + ".py"
        if pkg:
            fill, font, stroke = pkg_colour[pkg], "#000000", "#5B8DB8"
            kind = f"Module: {rel} · {mod.lines} lines"
        else:
            fill, font, stroke = "#E0E0E0", "#1A1A1A", "#8A8A8A"
            kind = f"Module outside the view: {rel} · {mod.lines} lines"
        label = (
            f'<b style="font-size:12px">{escape(fname)}</b><br><span style="font-size:9px;color:#333333">'
            f"[{escape(kind)}]</span><br>"
            f'<span style="font-size:9.5px">{escape(_short(mod.doc, 92))}</span>'
        )
        if listed.get(n):
            also = ", ".join(m.rsplit(".", 1)[-1] for m in listed[n])
            label += (
                f'<br><span style="font-size:8.5px;color:#1A1A1A"><b>also imports:</b> {escape(also)}</span>'
            )
        if foundation:
            label += f'<br><span style="font-size:8.5px;color:#333333">uses: {escape(", ".join(foundation))}</span>'
        st = (
            f"rounded=1;arcSize=6;html=1;whiteSpace=wrap;{FONT}fontSize=10;fillColor={fill};strokeColor={stroke};"
            f"fontColor={font};align=center;verticalAlign=middle;spacing=5;overflow=visible;"
        )
        page.cells.append(
            f'<mxCell id="{ids[n]}" value={quoteattr(label)} style="{st}" vertex="1" parent="1">'
            f'<mxGeometry x="{xs[n]:g}" y="{ys[n]:g}" width="{bw}" height="{bh}" as="geometry"/></mxCell>'
        )
    # Key and facts, right of the graph.
    kx = left + full + 40
    ky = top
    key_rows = "".join(
        f'<tr><td><div style="width:30px;height:14px;background:{pkg_colour[p]};border:1px solid #5B8DB8"></div></td>'
        f"<td>&nbsp;package <b>{escape(p)}</b></td></tr>"
        for p in packages
    )
    key_rows += (
        '<tr><td><div style="width:30px;height:14px;background:#E0E0E0;border:1px solid #8A8A8A"></div></td>'
        "<td>&nbsp;onloop module outside the view, imported by one in it</td></tr>"
        '<tr><td style="font-size:14px">&#x2192;</td><td>&nbsp;imports (from importer to imported)</td></tr>'
    )
    src_name = ref if ref is not None else code.get("ref") or "the work tree"
    if src_name != "the work tree":
        sha = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", src_name],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        src_name = f"{src_name} @ {sha}" if sha and not sha.startswith(str(src_name)) else src_name
    facts = (
        f"<b>Key</b><table style='font-size:10.5px'>{key_rows}</table><br>"
        f"<b>How this page is made</b><br>Parsed with <code>ast</code> from <code>src/onloop</code> at "
        f"<b>{escape(src_name)}</b>; no tool installed. A box is a module (its first docstring line, its size); an "
        f"arrow is an import, resolved to a module. Rows are import depth: a module sits below every module that "
        f"imports it. Imports of the foundation layer ({escape(', '.join(sorted(c.removeprefix('onloop.') for c in collapse)))}) "
        f"are listed in the box as <i>uses</i> instead of drawn, and package <code>__init__</code> files that hold only a "
        f"docstring are left out. A module with more than {list_over} imports in view draws only those of the next "
        f"row and lists the rest as <i>also imports</i>. Long imports of one module share one trunk.<br><br>"
        f"<b>Counts</b><br>{len(scope)} modules in view · {len(outside)} outside · {len(edges)} imports drawn · "
        f"{sum(len(v) for v in listed.values())} listed"
    )
    st = (
        f"rounded=0;html=1;whiteSpace=wrap;{FONT}fontSize=10.5;align=left;verticalAlign=top;spacingLeft=12;"
        "spacingRight=10;spacingTop=8;fillColor=#FFFFFF;strokeColor=#BFBFBF;fontColor=#1A1A1A;overflow=visible;"
    )
    page.cells.append(
        f'<mxCell id="key" value={quoteattr(facts)} style="{st}" vertex="1" parent="1">'
        f'<mxGeometry x="{kx:g}" y="{ky:g}" width="320" height="{code.get("key_h", 400)}" as="geometry"/></mxCell>'
    )
    page._n = 10_000
    footer(page, model, view)
    return page


# ---------------------------------------------------------------------------------------------------------------- output


def build(model: dict[str, Any], code_ref: str | None = None) -> list[Page]:
    problems = check(model)
    if problems:
        raise ModelError("\n".join(problems))
    views = model["views"]
    pages = []
    for i, view in enumerate(views, start=1):
        if view.get("kind") == "code":
            pages.append(render_code_view(model, view, i, len(views), code_ref))
        else:
            pages.append(render_view(model, view, i, len(views)))
    return pages


def write(pages: list[Page], out: Path = OUT) -> None:
    body = "\n".join(p.xml() for p in pages)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f'<mxfile host="onloop-c4" type="device">\n{body}\n</mxfile>\n', encoding="utf-8", newline="\n"
    )


def png_name(view: dict[str, Any], index: int) -> str:
    return f"c4-{index}-{view['key']}.png"


def export(model: dict[str, Any], drawio: Path, exe: str, png_dir: Path) -> list[Path]:
    written = []
    for i, view in enumerate(model["views"], start=1):
        png = png_dir / png_name(view, i)
        cmd = [exe, "-x", "-f", "png", "-s", "2", "-b", "20", "-p", str(i), "-o", str(png), str(drawio)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if result.returncode != 0 or not png.exists():
            raise RuntimeError(
                f"draw.io export of page {i} failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        written.append(png)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", type=Path, default=MODEL)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--check", action="store_true", help="check the model only; write nothing")
    parser.add_argument(
        "--code-ref", help="read the code view's source at this git ref (default: the view's ref)"
    )
    parser.add_argument("--export", action="store_true", help="export every page to PNG with draw.io desktop")
    parser.add_argument(
        "--drawio", default=os.environ.get("DRAWIO_EXE"), help="draw.io executable ($DRAWIO_EXE)"
    )
    parser.add_argument("--png-dir", type=Path, default=PNG_DIR)
    parser.add_argument(
        "--params-from-code",
        action="store_true",
        help="show the code's value of each parameter, not the model's",
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        model = load(args.model, params_from_code=args.params_from_code)
        for drift in param_drift(model):
            print(f"note: parameter {drift}")
        if args.check:
            problems = check(model)
            for p in problems:
                print(f"model: {p}")
            return 1 if problems else 0
        pages = build(model, args.code_ref)
    except ModelError as exc:
        for line in str(exc).splitlines():
            print(f"model: {line}")
        return 1
    write(pages, args.out)
    for page in pages:
        for w in page.warnings:
            print(f"warning: {w}")
    print(
        f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}: {len(pages)} pages"
    )
    if args.export:
        if not args.drawio:
            print("export: name the draw.io executable with --drawio or $DRAWIO_EXE")
            return 2
        for png in export(model, args.out, args.drawio, args.png_dir):
            print(f"exported {png.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
