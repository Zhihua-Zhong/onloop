"""The UI's built files, as onloopd serves them (decision 0009).

    resolve(root, url_path) -> Static            the file a GET names, or why there is none

The bundle is built at release time (`npm ci && npm run build` in `web/`) into `src/onloop/ui/` and carried in the wheel
as package data; it is never committed. onloopd serves it from the same origin as the API, read-only:
- `/` and any path whose last segment has no extension answer `index.html` (the views are hash routes; this keeps a
  reload working);
- `/assets/...` are the hashed files, cached as immutable; everything else is `no-store`;
- a path that leaves the directory, names a dot file, or has an extension not listed here is a 404;
- every answer carries a strict Content-Security-Policy: scripts, styles and requests from this origin only, no
  inline script, no framing.

Without a build (a checkout where nobody ran `npm run build`), `/` answers a plain page that says how to build it.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from urllib.parse import unquote

UI_DIR = pathlib.Path(__file__).resolve().parent.parent / "ui"
INDEX = "index.html"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    )
)

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>onloop: the UI is not built</title></head>
<body><h1>The UI is not built in this install</h1>
<p>onloopd is running and its API answers under <code>/api/</code>. The pages are built at release time.</p>
<p>From a checkout: <code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code>, then reload.</p>
</body></html>
"""


@dataclass(frozen=True)
class Static:
    status: int  # 200, 404, or 503 when there is no build
    path: pathlib.Path | None = None
    content_type: str = "text/plain; charset=utf-8"
    immutable: bool = False


def built(root: pathlib.Path) -> bool:
    return (root / INDEX).is_file()


def resolve(root: pathlib.Path, url_path: str) -> Static:
    """The file `url_path` names under `root`, never outside it."""
    if not built(root):
        return Static(503)
    raw = unquote(url_path)
    if "\\" in raw or "\x00" in raw or not raw.startswith("/"):
        return Static(404)
    parts = [p for p in raw.split("/") if p]
    if any(p.startswith(".") for p in parts):
        return Static(404)  # `..`, `.git`, `.env`: never served
    if not parts or "." not in parts[-1]:
        return Static(200, root / INDEX, CONTENT_TYPES[".html"])
    ext = pathlib.PurePosixPath(parts[-1]).suffix.lower()
    if ext not in CONTENT_TYPES:
        return Static(404)
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if base not in target.parents or not target.is_file():
        return Static(404)
    return Static(200, target, CONTENT_TYPES[ext], immutable=parts[0] == "assets")
