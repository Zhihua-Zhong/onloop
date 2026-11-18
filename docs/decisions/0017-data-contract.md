# Decision 0017: the data contract, a team's daily export replayed read-only

- **Status:** Accepted on 2026-11-17
- **Date:** 2026-11-17
- **Decided by:** the project lead and the onloop lead, on the data contract plan
- **Size:** medium (a new reader, a new event source, one view; nothing acts)

## Context

The first customer wants onloop tested on their team's real work before anything is installed on their side, and the
two projects stay strictly apart. onloop does not reach into the customer's systems, not even their code host. The
only interface is a file export that the customer writes and onloop reads.

## Decision

1. **The export is a daily snapshot.** It is written in a neutral folder outside both projects:
   `snapshots/<UTC time>/` (for example `2026-11-17T020000Z`), holding `manifest.json` and JSON-lines files. The
   manifest is written last. A folder without one is not read yet.
2. **One record is a CloudEvents 1.0 envelope,** one per line: `specversion`, `id`, `source`, `type`, `time`,
   `exporttime`, `subject`, `dataschema` and `data`.
   - `time` is when the thing happened at the source, and `exporttime` is when the export wrote it (two times, as
     OpenTelemetry's log model keeps them). onloop adds its own third time when it imports.
   - The snapshot is only the delivery: every record carries its own time, so a stall shorter than a day is still
     visible after the fact.
3. **The schemas are JSON Schema 2020-12** and ship in the wheel (`src/onloop/contract/v1/`). Every object refuses a
   field it does not name, so free text can enter only through the fields made for it.
   - The manifest is `contract`, `exporttime`, `producer`, `window {from, to}`, `timezone`,
     `files {<name>: {sha256, counts {<type>: n}}}` and `complete {<type>: bool}`.
   - Contract 1.x adds only optional fields and types. A reader refuses a newer minor version than it knows.
4. **v1 reads these record types:**
   - `onloop.role.declared` (`role`, `kind`);
   - `onloop.turn.ended` (`role`, `session_id`, `prompt_id`, `started_at`, and `outcome_line` only if the turn has
     one);
   - `onloop.pr.opened`, `.merged` and `.closed` (`number`, `title`, `head_sha`). A producer exports one repository,
     so a PR event is keyed by the producer, the kind and the number. The head commit is the PR's current one, so it
     never keys an event;
   - `onloop.goal.revision` (`goal`, `items [{id, ticked}]`). It is read through R5's own edit diff. A revision is
     keyed by its edit time, so the latest edit repeated on a quiet day adds nothing;
   - `onloop.feedback.batch` (`batch`, `from`, `stages`, `items`), `onloop.feedback.item` (`batch`, `item`,
     `label`, `stage_index`, `stage`, `skipped_stages`, `waits_on`) and `onloop.feedback.link` (`batch`, `item`,
     `issue`). An item's stage is **declared by the team**, not derived by onloop. Each export's stamp is its own
     event, so a stage's age can be computed. The overview (R6) projects these, shows a declared stage with a
     "reported by the team" mark, and flags any disagreement with the stage that links prove.

   A type not listed is refused by name. A new key or type comes with a contract version change.

   **Contract 1.1 (2026-11-18)** adds four types. Nothing in 1.0 changes, and the reader reads both versions:
   - `onloop.pr.checks` (`number`, `head_sha`, `check`, `conclusion`, `started_at`, `completed_at`), logged as the
     live ingest's `check.completed`. Never a check's log;
   - `onloop.pr.reviewed` (`number`, `head_sha`, `reviewer`, `verdict`, `at`), logged as `review.submitted`.
     - The reviewer is a declared role, `human` or `team`, never a login. A login fits the role-name pattern, so
       the reader refuses any reviewer that is not a declared role.
     - The verdict is a short code (`A–Z`, `_` and space, at most 24 characters), never a comment body.
     - An imported verdict never resolves the person's approval at the merge gate; that stays the code host's;
   - `onloop.wait.opened` (`on: pr:<n>`, `waits_on`, `waits_for`, `since`) and `onloop.wait.cleared`, into the
     ledger's one wait model (the constraint, Now, the task list and the map).
     - A wait on the person opens a task on their list ("approve the change (pr#12), so it can move").
     - A wait is cleared only by `wait.cleared`: one missing from a later export stays open.
     - `team` is held as a pseudo-role named "team" until the overview's owner classes (schema 9).

   A 1.1 type in a snapshot whose manifest says 1.0 is refused.
5. **The reader is `onloop import snapshot <folder> --ledger <ledger>`.**
   - It imports each file whole or not at all, and it is idempotent: a cursor per snapshot file and its hash.
   - A file is refused for any of these, and the report names the line, field path and rule, never the value:
     - a bad record;
     - a privacy hit (secret-shaped or personal-shaped strings, and the producer's own `deny.txt`);
     - a hash or counts that differ from the manifest;
     - a changed file under the same name.
   - It writes only the ledger and `import-report.jsonl` beside it. The ledger may not be inside a git work tree,
     inside the snapshots, or in onloop's own state folder.
6. **A replayed turn has the id the live hook receiver would give the same turn.** It is logged under the new source
   `import` (ledger schema 8), so every view can tell replayed evidence from live evidence. Projections read it like
   the live kind. Schema 8 also keeps a turn's `started_at` when the source gives it.
7. **A team without outcome lines gets one summary, not one stall per turn.** When no turn in the ledger has an outcome
   line, the no-outcome detector opens one alert, "Outcome lines not adopted: 0 of N turns end with one", with no
   wake. The per-turn alert returns once the team writes outcome lines.
8. **The Activity view answers "is the team moving, and where did it go quiet" without outcome lines.**
   - It has one lane per role, with its turns as marks on a time axis, and side sessions (a lead's forks) under their
     role.
   - It shows each role's longest silence, turns per hour and roles working at once, and the window's start where
     there is no data.
   - The window ends at the data's export time, and the page says how old that is.

## Consequences

- The customer's data never enters the repository or onloop's state. Neither of the people who build onloop, nor any
  model they work through, sees it until the customer agrees. Tests and hands-on passes use a generic export of the
  same shape (`tools/contract_example.py`).
- A replay is not live. The Activity page says "data as of" with its age, and nothing in a replay acts.
- The full `jsonschema` package is not a dependency. The built-in validator covers the subset the contract uses. A
  cross-check with the package runs only where it is already installed (it was not, on 2026-11-17).

## Alternatives considered

| Alternative | Verdict |
|---|---|
| OTLP itself, as the export format | Rejected. It is shaped for spans, metrics and logs; typed domain records are clearer and checkable |
| Fetching the customer's GitHub directly | Rejected by the project lead: the export is the only interface |
| Quarantining a bad record and importing the rest | Rejected. A half-imported day gives counts that look right and are not |
| A stall alert per turn without an outcome line | Rejected for a team that has not adopted outcome lines: a thousand alerts are noise |
