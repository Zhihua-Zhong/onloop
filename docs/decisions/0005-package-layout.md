# Decision 0005: the package layout

- **Status:** Accepted on 2026-11-09 for change 1 (the `src/` layout), carried out in the first refactor step.
  Changes 2 and 3 stay Proposed until a later step takes them up.
- **Date:** proposed 2026-11-05; accepted 2026-11-09
- **Decided by:** the project lead, approving the technical design of 2026-11-09 and its four-step refactor plan; the
  onloop lead assigned change 1 to the first step
- **Size:** medium (it moves files and changes import paths; no behaviour changes)

## Context

The housekeeping pass of 2026-11-05 kept the layout as it is, by instruction: a flat `onloop/` package beside
`tests/`, with three subpackages. [docs/architecture.md](../architecture.md) now names five layers and a test enforces
their direction, but the file tree does not show them. Three things in the layout work against what onloop is
becoming ([decision 0004](0004-onloop-hosts-the-conversation.md)):

1. **The package sits at the repository root.** Tests import `onloop` from the checkout, so they pass against the
   source even where the installed package differs: a file missing from the wheel, or a path computed from the source
   tree. The panel did exactly that until 2026-11-05: it served the canvas from `web/dist` beside the package, which an
   installed onloop does not have, and no test could see it. The PyPA guide recommends the `src/` layout for this
   reason.
2. **`onloop.supervisor` no longer names what it holds.** After the Temporal layer was removed, it holds the role table,
   the governed step runner, the planner, the asks and the attention rules. There is no supervising process.
3. **Thirty-four modules share one flat namespace across all five layers.** The polling loop (`runner`, `intake`,
   `dispatcher`, `handlers/`) is spread among the rules it uses, so retiring it, or making it the event intake, is a
   larger change than it needs to be.

## Proposal

Three changes, each its own pull request, in this order; any of them can be accepted alone.

1. **Move to the `src/` layout:** `src/onloop/`, with `[tool.setuptools.packages.find] where = ["src"]`. The canvas
   path becomes explicit configuration, or the built canvas is shipped as package data, instead of a path computed
   from the source tree. CI installs the package (not editable) before running the tests.

   *As carried out (2026-11-09):* the package is `src/onloop/` and CI installs it non-editable. The canvas is still
   found only in a source checkout (`web/` three levels above the panel module) and an installed onloop says it is
   not part of the install; packaging the canvas is left to the UI step.
2. **Rename `onloop.supervisor` to `onloop.roles`**, keeping `onloop step`, `onloop supervise --once`, `onloop digest`
   and `onloop attention` as they are, and the configuration file's keys unchanged.
3. **Group the polling loop as `onloop.loop`** (`loop.runner`, `loop.intake`, `loop.dispatcher`, `loop.handlers`), after
   moving the two helpers other layers borrow from it (`dispatcher.plus` to `events`, which already holds the time
   format). This waits on the lead's decision whether the loop is retired or becomes the event intake.

## Consequences

- Import paths change; nothing outside the repository imports onloop's modules except through the `onloop.checks`
  entry point, which does not move.
- About 580 mutation-table entries name files by path and must follow; `tests/test_mutation_table.py` says which.
- `git log --follow` still finds a moved file's history.
- Not proposed: renaming the `onloop` distribution or the `onloop` command.
