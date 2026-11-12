"""Check that a built wheel carries the UI bundle (decision 0009): a wheel without it must fail, never ship.

    python tools/check_wheel.py dist/onloop-*.whl

Exit 0 when every wheel named has `onloop/ui/index.html` and at least one script under `onloop/ui/assets/`; exit 1,
naming what is missing, otherwise.
"""

from __future__ import annotations

import sys
import zipfile

REQUIRED = "onloop/ui/index.html"
ASSETS = "onloop/ui/assets/"


def problems(path: str) -> list[str]:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except (OSError, zipfile.BadZipFile) as e:
        return [f"{path}: not a readable wheel ({e})"]
    out = []
    if REQUIRED not in names:
        out.append(f"{path}: has no {REQUIRED}; build the UI first (cd web && npm ci && npm run build)")
    if not any(n.startswith(ASSETS) and n.endswith(".js") for n in names):
        out.append(f"{path}: has no script under {ASSETS}")
    return out


def main(argv: list[str] | None = None) -> int:
    paths = sys.argv[1:] if argv is None else argv
    if not paths:
        print("usage: check_wheel.py WHEEL...", file=sys.stderr)
        return 2
    found = [p for path in paths for p in problems(path)]
    for p in found:
        print(p, file=sys.stderr)
    if not found:
        print(f"{len(paths)} wheel(s) carry the UI")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
