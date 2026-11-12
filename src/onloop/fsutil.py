"""Writing a file so a reader never sees half of it."""

from __future__ import annotations

import os
import pathlib
import threading


def write_atomic(path: str | os.PathLike, text: str, *, newline: str | None = None) -> None:
    """Write `text` (UTF-8) to `path` through a temporary file beside it, then rename it into place.

    A reader sees the old file or the new one, never a part. The parent folder is made if needed. The temporary name
    carries the process and thread, so two writers of one path do not share it. `newline` is passed to `open`: None
    writes the platform's line ending, "\\n" writes LF everywhere.
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    os.replace(tmp, p)
