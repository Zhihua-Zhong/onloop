# Decision 0012: the outcome line, version 2 (it amends 0007)

- **Status:** Accepted on 2026-11-16
- **Date:** 2026-11-16
- **Decided by:** the onloop lead, on the R5 plan, its spikes, and the first customer's answers and refinements
- **Size:** medium (it widens one line's grammar; every line valid before stays valid, with one listed exception)
- **Amends:** [decision 0007](0007-disposition-contract.md). 0007 keeps the contract: one line, fail closed, observe
  first, and the paragraph lives once. This decision replaces 0007's grammar table.

## Context

Two needs reach the outcome line.

- **An ask to the person must say what it is worth.** A manager cannot weigh, order or skip asks that only say what to
  click. Every human ask states its effect ("do X, so Y"), the goal item it moves, a weight, and its cost of delay:
  what waits, and from when.
- **A wait on something outside the machine has no honest waker.** A role that waited on a CI run wrote `pid:0` or an
  undeclared `monitor:` name, and onloop flagged both as dead within seconds. The contract made a fake waker the easy
  answer.

The parser runs once, when the Stop hook is received, and the parse is frozen in the `hook.stop` event. Replay never
re-parses. A new grammar therefore cannot change how an old event reads.

## Decision

1. **Ask keys** are allowed on `blocked`, `handoff` and `in_review`:

   | Key | Form | Meaning |
   |---|---|---|
   | `effect` | text, at most 200 characters | "do X, so Y": what the act changes for the whole |
   | `goal` | `<goal id>/<item number>`, or `none` | the goal item it moves; `none` is process work, on purpose |
   | `weight` | `critical`, `supporting` or `skippable` | the author's weight |
   | `cost_from` | `YYYY-MM-DD` | the day the delay starts to cost |
   | `days` | `0`–`365`, or `?` | the days until the delay starts to cost (0: it costs now); `?`: it costs, date not known |
   | `holds` | text, at most 120 characters | what waits on it |
   | `default` | text, at most 120 characters | what happens if nobody answers |

   - `cost_from` and `days` together are an error.
   - `goal` is also allowed on `done` and `continue`.
   - `resolves=<task id>` is allowed on `done` only. An author or the coordinator uses it to close a returned task.
2. **The keys make a human task** when `owner=human` ([decision 0013](0013-human-task-list.md)). With another owner,
   they are kept on the turn and make no task.
3. **A missing effect is not a parse error.** The line is valid, and the task is marked incomplete by a separate check.
   Rejecting at the parser would bury an urgent ask. The same holds for a stated cost of delay without `holds`.
4. **`days` is converted once, at ingest,** to `cost_from` = the ingest day + n. The ingest day is the calendar day of
   the event's ledger time (never a time the sender claims), in `kpi.timezone`. The converted date and the key as
   written (`cost_given`) are frozen in the event. **No count of days is ever stored or sorted on.** Days are computed
   when read.
5. **External waits.** One waker kind is added: `waker=event:<kind>:<locator>`.
   - The kinds are those of the configured read adapters ([decision 0013](0013-human-task-list.md)). The GitHub
     adapter's are `gh-run:<run id>`, `gh-checks:<commit sha>` and `gh-review:pr<number>`. Each takes an optional
     `<owner>/<repo>/` prefix.
   - The locator takes letters, digits and `._:/#-`, up to 200 characters.
   - onloop checks the event through its adapter. It is **alive** while pending, **fires** when complete, and is
     **dead** when the source says it does not exist, or when no adapter for the kind is configured. It is **unknown**
     on a network error or a rate limit, never read as dead.
6. **One narrowing:** `pid:0` is refused (no process has pid 0). It is the only line that version 1 accepted and
   version 2 refuses.
7. **The `hook.stop` payload gains `grammar: 2`** and an `ask` object with the keys above. A payload without `grammar`
   is version 1.
8. **`onloop ask` is the primary way to ask the person** (spike R5-S4b: 8 of 11 one-line asks were complete on Haiku,
   under the agreed threshold of 9).
   - `onloop ask --owner human --ref … --effect … [--goal …] [--weight …] [--cost-from … | --days …] [--holds …]
     [--default …]` checks every field when it is asked, and prints the task id.
   - The turn then ends with `outcome: blocked owner=human ref=task:<id>`.
   - The keys on the line stay accepted, and reconciliation reads both.
9. **The paragraph reaches a role as a file** (the shared role template), never as a command-line argument. A
   multi-line argument through a `.cmd` shim is cut at its first newline (spike R5-S4).

## Backward compatibility

- An event logged before this decision keeps its parse.
- A line of the version-1 shape parses to the same value, ref, owner and waker, with every new field empty. The only
  exception is `waker=pid:0`. A test runs the version-1 corpus through both grammars.
- A line that version 1 refused and version 2 accepts is one that carries a new key.
- **An older onloop (0.3.x, 0.4.0) refuses a version-2 line** ("`blocked` takes no goal"). It counts the turn as one
  without an outcome, never as `done`. Roles write the new keys only once their onloop is 0.5.0 or later. The shared
  paragraph names that version.

## Alternatives considered

| Option | Verdict | Why |
|---|---|---|
| Succeed 0007 with a new contract | Rejected | One line should not need two documents |
| Refuse an ask without an effect at the parser | Rejected | It would hide urgent asks; the customer asked for "show last, flag, return" |
| Store `days` as a count | Rejected | A count is relative to a day nobody records; the customer asked for a date |
| A `waits=` key beside `waker=` | Rejected | An external wait is what wakes the role; one key, one checker |

## Consequences

- `disposition.parse` returns the ask fields and the version; `format_line` writes them in a fixed order: owner, ref,
  waker, goal, weight, cost_from or days, effect, holds, default, resolves.
- `examples/roles/outcome-line.md` gains an ask example, an `event:` example and the `onloop ask` paragraph.
- The customer adopts the keys after their freeze, and with their agreement.
