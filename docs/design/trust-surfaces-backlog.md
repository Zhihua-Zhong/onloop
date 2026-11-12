# Trust surfaces: backlog

Not built. Nothing here is started until the shadow run (0.3) has real-week evidence; the Lead's ruling of 2026-10-11 is that two
releases are built and none is measured on a real project. Every entry comes from one test: the 0.4 workbench built from a
project's real ledger, read-only, with no events database and no GitHub reads (2026-10-11). The ledger had 19 open decisions
and the page listed the same 19, item for item. It did not say what can be opened this week.

Fixed in 0.4 itself, not here: ids sort by number (it listed D12 before D2), and a check over nothing is `NOT EXERCISED`, not PASS.

## 1. A `notice` kind: shown, not asked

Finding: the ledger's D35 says in its own text that it is a hazard, "not a project-lead decision, listed here because the ledger
has no other place". The inbox lists it as a ruling for the human. onloop has no way to say "this is for you to know, not to
answer". A `notice` is shown in the inbox under its own heading, is not counted as waiting, and has no recommendation.

## 2. `ruled_in_part`

Finding: D39 is partly ruled; the project lead ruled on (b) and left (c) and (d) open. The page shows it as wholly pending and
loses the partial ruling. A decision may carry `ruled_in_part: {on, by, text}`; the page shows what is settled and what is open.

## 3. Structured `blocks:` and `urgent:`

Finding: "Demo 1 after the D4 window" and "URGENT" (D12) are sentences. The page cannot show that Demo 1 waits on D4, or put the
urgent item first. A deliverable gets `waits_on: [D4]`, a decision gets `urgent: true`; the page links the two and sorts urgent
first. The check that every id named exists is part of `ledger check`.

## 4. Closed decisions age out

Finding: 23 closed decisions with no author sit under "Unattributed", every one that was ever closed, with no age limit, so the
heading buries what is current. After N days (a policy field, default 30) a closed decision leaves the list and the heading
shows a count: "23 older, not shown". The count is a listing item, so it is never silent.

## Also seen (project side, not onloop)

The ledger has no `deliverables`, no `size`, and no author on closed rows; `focus` is a list where onloop wants one sentence; `queue`
and `workstreams` have other shapes. Those are the project's to route, and the adapter that read them (kept outside this
repository) lists them.
