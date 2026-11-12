"""The text onloop stores (decision 0010, point 10): only what onloop itself writes into a session.

    post(ledger, thread, body, author, channel, retention_s) -> message id   scrub, hash, log `message.posted`
    expire(ledger, now) -> [ids]                                             clear bodies past their retention

Stored: a person's message (`onloop say`, the compose box) and onloop's own notes. Never stored: anything a role
writes. Before a body is logged, secret-shaped strings are replaced with `[redacted]` and counted: PEM blocks,
key-like tokens and the generic key markers of the confidentiality scan. A body is at most 8 KB of valid UTF-8. When
its retention passes, `message.expired` clears the body and keeps its sha256, so the log still shows that a message
existed and which.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from .. import events as ev
from ..overlay.ledger import Ledger

MAX_BYTES = 8 * 1024
REDACTED = "[redacted]"
_SECRETS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
    re.compile(r"-----BEGIN [A-Z0-9 ]+-----.*?-----END [A-Z0-9 ]+-----", re.S),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|token|password|passwd|bearer)\b(\s*[:=]\s*|\s+)['\"]?[A-Za-z0-9_./+=-]{8,}['\"]?"
    ),
)


class MessageRefused(ValueError):
    pass


def scrub(text: str) -> tuple[str, int]:
    """The text with each secret-shaped string replaced, and how many were."""
    count = 0
    for pat in _SECRETS:
        text, n = pat.subn(REDACTED, text)
        count += n
    return text, count


def check(body: object) -> str:
    if not isinstance(body, str) or not body.strip():
        raise MessageRefused("a message is non-empty text")
    if len(body.encode("utf-8")) > MAX_BYTES:
        raise MessageRefused(f"a message is at most {MAX_BYTES} bytes")
    return body


def post(
    ledger: Ledger,
    *,
    thread: str,
    body: str,
    author: str,
    channel: str,
    retention_s: int,
    delivery_id: str | None = None,
    source: str | None = None,
    message_id: str | None = None,
) -> str:
    """Log one message. The caller holds the ledger's writer (onloopd's lock, or its own process)."""
    text, n = scrub(check(body))
    mid = message_id or f"msg-{uuid.uuid4().hex[:16]}"
    now = ledger.now()
    ledger.append(
        {
            "id": f"message.posted:{mid}",
            "source": source or ("carrier" if channel == "carrier" else channel),
            "kind": "message.posted",
            "subject_ref": f"role:{thread}",
            "actor": author,
            "payload": {
                "id": mid,
                "thread": thread,
                "author": author,
                "channel": channel,
                "body": text,
                "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "redacted": n,
                "expires_at": ev.shift_ts(now, retention_s),
                "delivery_id": delivery_id,
            },
        }
    )
    return mid


def expire(ledger: Ledger, now: str) -> list[str]:
    rows = ledger.select(
        "SELECT id, thread FROM messages WHERE expired_at IS NULL AND expires_at <= ? ORDER BY id", (now,)
    )
    for r in rows:
        ledger.append(
            {
                "id": f"message.expired:{r['id']}",
                "source": "carrier",
                "kind": "message.expired",
                "subject_ref": f"role:{r['thread']}",
                "actor": "carrier",
                "payload": {"id": r["id"]},
            }
        )
    return [r["id"] for r in rows]
