"""Windows reads files and subprocess output in the locale's code page unless told otherwise. ruff's PLW1514 catches `open()` and
`read_text()`; this catches the same mistake in `subprocess.run(..., text=True)`, which ruff does not."""

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_ruff_is_told_to_require_an_encoding_on_every_text_read_and_write():
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["ruff"]["lint"]
    assert "PLW1514" in cfg["select"] and cfg["preview"] is True and cfg["explicit-preview-rules"] is True


def test_no_subprocess_call_in_text_mode_leaves_its_encoding_to_the_locale():
    bad = []
    for f in sorted((ROOT / "src" / "onloop").rglob("*.py")):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(?:run|Popen|check_output)\((?:[^()]|\([^()]*\))*\)", text):
            call = m.group(0)
            if ("text=True" in call or "universal_newlines" in call) and "encoding=" not in call:
                bad.append(f"{f.name}:{text[: m.start()].count(chr(10)) + 1}")
    assert bad == []
