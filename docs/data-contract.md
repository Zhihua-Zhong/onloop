# Sending onloop your team's activity: the data contract

You export a small file once a day. onloop reads it and shows how your team worked. **It only reads.** It never writes
to your files, never connects to your systems, and never changes anything on your side.

## Start small: roles and turns

Two kinds of record are enough to begin. onloop then shows, for each role, when it worked, when it went quiet and for
how long, how busy each hour was, and how many roles worked at once.

- **A role** of your team, by its role name (`builder`, `reviewer`), never a person's name.
- **A turn that ended:** which role, which session, which prompt, when it started and when it ended. If your roles end
  their turns with an outcome line, send that line too, and only that line.

## Then add what you have

- **Pull requests:** opened, merged and closed without merging: the number, the title (at most 120 characters) and
  the current head commit. Never the body or who wrote it.
- **A goal checklist:** after each edit, every item by its number, ticked or not. Repeating the latest edit on a quiet
  day is fine.
- **Feedback:** a batch with its stages, each item's stage as your team sees it today, and the issue each item links
  to. onloop shows that stage as reported by your team.

## Contract 1.1: checks, verdicts, and who a change waits on

Send these only once your manifest says `"contract": "1.1"`.

```json
{"type": "onloop.pr.checks", "data": {"number": 12, "head_sha": "<40 hex>", "check": "unit tests", "conclusion": "success", "started_at": "2026-11-16T09:01:00.000Z", "completed_at": "2026-11-16T09:07:30.000Z"}, …}
{"type": "onloop.pr.reviewed", "data": {"number": 12, "head_sha": "<40 hex>", "reviewer": "reviewer", "verdict": "READY", "at": "2026-11-16T10:02:00.000Z"}, …}
{"type": "onloop.wait.opened", "data": {"on": "pr:12", "waits_on": "human", "waits_for": "approve", "since": "2026-11-16T10:05:00.000Z"}, …}
{"type": "onloop.wait.cleared", "data": {"on": "pr:12", "waits_on": "human", "since": "2026-11-16T10:05:00.000Z", "cleared_at": "2026-11-17T08:30:00.000Z"}, …}
```

(`…` is the same envelope as every other record.)

- **A reviewer or a waiter** is one of your role names, `human` or `team`. Never a login: onloop refuses a name that
  is not a declared role.
- **A verdict** is a short code such as `READY`, `HOLD` or `NOT READY`. Never the text of a review.
- **Repeat an open wait** in every export, with the same id. Only `wait.cleared` ends it: a wait you stop listing
  stays open.

## Contract 1.2: who opened a change

Send this only once your manifest says `"contract": "1.2"`, and only after onloop's lead says the 1.2 reader is out.

```json
{"type": "onloop.pr.opened", "dataschema": "onloop-contract/1.2/pr.opened", "data": {"number": 12, "title": "Example change", "head_sha": "<40 hex>", "by": "builder"}, …}
```

- **`by`** is one of your role names, `team` when you can't tell which role opened it, or `human` for a change the
  person opened. It is never a login, an account name or a branch name: derive the role in your exporter, and send
  only the role.
- **A PR you sent before without `by`** gets its role when you send it again with `by`, under the same id. Send the
  same `by` every time.
- **Only on `pr.opened`.** `by` on a merged or closed record is refused.

## Where to put it

Put one folder per export, named by its time in UTC, inside a `snapshots` folder:

```
<exchange folder>\
  snapshots\
    2026-11-17T020000Z\
      events.jsonl      the records, one per line
      manifest.json     write this file LAST: it tells onloop the export is complete
```

Export once a day, covering the last two days. Overlapping days are fine: onloop counts each record once.

## The manifest

```json
{
  "contract": "1.0",
  "exporttime": "2026-11-17T02:00:00.000Z",
  "producer": "example-team",
  "window": {"from": "2026-11-15T02:00:00.000Z", "to": "2026-11-17T02:00:00.000Z"},
  "timezone": "Asia/Tokyo",
  "files": {"events.jsonl": {"sha256": "<the file's SHA-256>", "counts": {"onloop.role.declared": 11, "onloop.turn.ended": 663}}},
  "complete": {"onloop.role.declared": true, "onloop.turn.ended": true}
}
```

## One line per record

```json
{"specversion": "1.0", "id": "role-builder", "source": "example-team/export", "type": "onloop.role.declared", "time": "2026-11-15T02:00:00.000Z", "exporttime": "2026-11-17T02:00:00.000Z", "subject": "role:builder", "dataschema": "onloop-contract/1.0/role.declared", "data": {"role": "builder", "kind": "agent"}}
{"specversion": "1.0", "id": "a1b2…", "source": "example-team/export", "type": "onloop.turn.ended", "time": "2026-11-16T09:12:44.120Z", "exporttime": "2026-11-17T02:00:00.000Z", "subject": "role:builder", "dataschema": "onloop-contract/1.0/turn.ended", "data": {"role": "builder", "session_id": "…", "prompt_id": "…", "started_at": "2026-11-16T09:08:02.511Z"}}
```

`time` is when it happened. `exporttime` is when you wrote the file. Times are UTC, as `YYYY-MM-DDTHH:MM:SS.mmmZ`.

## What must never be in it

- **The text of anything:** prompts, replies, tool output, or the body of a pull request, an issue or a document. Of
  a turn, only its outcome line.
- **Secrets:** tokens, keys, passwords, connection strings.
- **People:** no names, e-mail addresses or account names. Use role names.
- **Your machines:** no file contents, paths outside the repository, machine names or IP addresses.

onloop checks every field before it imports anything, and refuses the whole file if something looks like a secret or
an e-mail address. You can add your own `deny.txt` beside `snapshots` (one regular expression per line, for example
your internal project names). onloop then refuses any file that contains a match, and it never prints the match.

## Checking a file before you send it

The schemas are plain JSON Schema files (`src/onloop/contract/v1/` in onloop's source). Any JSON Schema validator can
check your export, and you do not need onloop installed.

## What a daily file can and cannot show

- **It can show** everything that happened up to the export, at its real time: a quiet hour on Tuesday morning shows
  as a quiet hour, even though you send the file on Wednesday.
- **It cannot show** anything after the export: the page says "data as of 02:00, 9 hours ago". It also cannot tell a
  turn still running from one that was never recorded.
- **It treats a missing day as missing:** a day with no export shows as missing, never as quiet.

## On onloop's side

```bash
onloop import snapshot <exchange folder> --ledger <exchange folder>\onloop\ledger.db
onloop daemon run --ledger <exchange folder>\onloop\ledger.db --port 8793
```

The first command prints counts per file and never a record. Then open `http://localhost:8793/#/activity`.
