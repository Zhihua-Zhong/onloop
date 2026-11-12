# Trust surfaces: build plan

*Status: plan, 2026-10-12, approved as the build plan. It turns the 0.4 row of section 6 of [architecture.md](architecture.md) into
modules, data contracts, tests and a measurable finish. Nothing here is built yet. A step is done when its test exists
and has been shown to fail under the mutation listed for it (section 9), as in [loop-without-waiting-plan.md](loop-without-waiting-plan.md).*

## 1. Goal and the numbers

0.4 is done when **the human opens one page on Monday and knows what is ready and what is theirs**, and when that page
can be shown to be a function of the event log, the ledger and the repository, with nothing typed into it (section 10).
Three numbers carry the claim:

- **no orphan figure**: every number on every page sits in a chip that names the record that produced it;
- **no stale ruling**: every item in the inbox matches the ledger and GitHub when the page is built, and a ruling the
  human made never appears in the lead's list;
- **no hidden whitespace**: every head move the reviewer sees is labelled by what changed, including CRLF-only and
  indentation-only pushes, and a push the log calls substantive is never shown with a softer word.

What 0.4 does **not** do: no editing of anything (the workbench is read-only; edges, canvas and copilot are 0.6 and
0.7), no graph, no merge train, no drift detector (0.5), no hosted server (architecture section 8). The page cannot
merge, approve, grant or widen authority, and no path from the page leads to the gate. Section 11 lists what I would
cut first.

Three queued items from the 0.3 reviews ride along because each is small and each closes a stated limit of 0.3:
GitHub-anchored human grants, opt-in refusals for line endings and uncovered paths, and slot capacity above 1
(section 8).

## 2. Principles carried into the design

1. **A page is a function.** `build(log, ledger, policy, repository files, claims files) -> bytes`. Same inputs, same
   bytes. No clock inside the function except the one passed in (`--now`), no network, no random, no write to the
   database. A test builds twice and compares bytes; a second test opens the database read-only on a read-only file
   system.
2. **GitHub and the repository stay the source of truth; the page is a cache of them.** The workbench stores nothing
   that the repository already says. Everything it adds to the files (deliverables, questions, claims, criteria) is a
   file in the repository, changed by a pull request through the gate, like the ledger.
3. **A figure without a chip is a defect.** Measured figures name a run, a commit, a denominator and a criteria file;
   generated text is marked fresh or stale. The renderer has no way to emit a bare number: figures only enter through
   `chip()`. A scanner over the generated HTML fails on any digit run outside a chip, a date or an identifier, and a
   mutation removes one chip to prove it.
4. **Text from outside is data.** PR titles, review bodies, ledger text and file names reach the page. All of it is
   escaped, none of it becomes markup or a link. Links are built only from an allow-list of origins read from the
   policy (the repository's own host). The page ships a Content-Security-Policy that allows no external request and
   one inline script by hash.
5. **The page never talks to an agent.** Nothing the workbench reads is composed into an anchor, a prompt or a hook,
   and nothing it renders is read back as an instruction. The new ledger sections (deliverables, questions) are not
   read by `anchor.py`; a test pins that by mutation, because ledger text reaching every agent's prompt is the
   injection path 0.3 closed.
6. **Softer is never allowed.** A label on a head move, a reversal or a refusal may add detail but never make the
   event sound smaller than the log says. If the log says `substantive: true`, the page says substantive first and the
   detail second.
7. **One rule table, still.** P004 and P005 (section 8) join the gate's table, pre-flight and the audit through the
   same functions. The workbench reads codes, never re-derives them.

## 3. The technology choice, and the lean first version

Architecture section 5 names TypeScript, React, Vite, FastAPI and Server-Sent Events for the workbench. Those are the
right tools for the editing workbench of 0.5 to 0.7. **0.4 does not need any of them**, and I recommend not adding
them yet. The first version is read-only, its readers open it once a day, and the data changes when a person or the
runner appends to a log. That is what `onloop dashboard` already does in 0.2: one self-contained page with its own
CSS and JS, no CDN, no server, opens from disk.

| Question | Answer for 0.4 | Why |
|---|---|---|
| How is a page made? | `onloop workbench build --out DIR` writes a small static site from the event log, ledger, policy and the new files | One command, one output directory, hostable on GitHub Pages like the 0.2 demo, openable from an email |
| What is the contract between data and view? | `DIR/model.json`, a versioned JSON model built by pure Python functions, and pages that render from it | A React client in 0.5 reads the same model; nothing in 0.4 is thrown away. This seam is the one design decision that matters. Precedent: dbt docs, coverage.py HTML and Allure are generated static report sites |
| Does it update? | `--watch` rebuilds when the database or a file changes; `onloop workbench serve` serves `DIR` on the loopback interface only, rebuilding on request | No push channel to build, no port exposed by default; the human decides on anything wider (architecture section 8) |
| New dependencies? | None. `html`, `string.Template`, `http.server`, `sqlite3` read-only | Same as 0.3: the standard library and `gh` |
| When do React and FastAPI come in? | When the first interaction arrives: the graph with semantic zoom in 0.5, or the first write (edges as pull requests) in 0.6. The trigger is a requirement, not a calendar date | A framework chosen before an interaction needs it is cost with no reader |

This is decided (section 13.1). Were it ever reversed, nothing in sections 4 to 10 would change: only `render.py`
would become a client and `serve` would become FastAPI.

## 4. Module list

All in package `onloop/`, a new subpackage `workbench/`. `*` marks a change to an existing module.

| Module | Job | Depends on |
|---|---|---|
| `workbench/model.py` | Pure functions from (store, policy, ledger, files, `now`) to the model: deliverables, inbox, claims, criteria, delta rows | `store` (read-only), `ledger*`, `policy*`, `kpi` |
| `workbench/deliverables.py` | The L0 page's rows: owner, state, fallback, live link, last verification, amber rule | `ledger*` |
| `workbench/inbox.py` | The three sections; the routing of a human ruling out of the lead's list | `ledger*`, `events` |
| `workbench/claims.py` | Read `claims.yaml` and the producing records; check each claim's definition against its source | |
| `workbench/criteria.py` | Criteria objects: pre-registration order, PASS or FAIL computed | `claims`, `gitutil` for file times |
| `workbench/delta.py` | The reviewer's delta pane rows from `pr.head_moved` and the stored delta basis | `store`, `events*` |
| `workbench/render.py` | Model to HTML, escaping, chips, CSP, the one inline script | |
| `workbench/check.py` | `onloop workbench check`: the acceptance conditions of section 10 | all of the above |
| `intake.py*` | At a substantive-or-not head move, also store the delta basis (section 6); store review threads | `gitutil*` |
| `gitutil.py*` | `delta_basis(old, new)`: tree equality, whitespace-only and CR-only detection, per-file counts | |
| `gate.py*` | `P004`, `P005`, reading `enforce:` | `policy*` |
| `preflight.py*` | Report P004 and P005 as form refusals when enforced, W004 and W005 otherwise | `gate*` |
| `slots.py*` | `capacity`, seats, anchored human grants | `store*`, `github*`, `identity` |
| `policy.py*` | `enforce:`, `slots.<name>.capacity`, `slots.<name>.human_grant`, `workbench:` and their loosening rules | |
| `ledger.py*` | Optional `deliverables:`, `questions:` and decision fields; validation | |
| `cli.py*` | `workbench build`, `workbench check`, `workbench serve`; `slot grant --anchor` | |

## 5. Data the workbench reads, and the files it adds

Everything is a file in the repository or a table that 0.3 already has. Nothing is a new source of truth.

### 5.1 The ledger gains three optional sections

```yaml
deliverables:
  - id: V1
    title: Grid map
    owner: builder              # a role
    state: ready                # planned | building | ready | blocked
    link: https://example.test/map      # the live link; shown only if the origin is allowed
    fallback: screenshots in docs/img   # required when state is ready
    verified: {on: 2026-10-14, by: operator, evidence: "https://example.test/pr/12#c3"}   # by: a role other than the owner
    due: 2026-10-16
questions:                      # only the human can answer these: outside facts
  - id: Q1
    title: Which month does the sponsor want?
    asked: 2026-10-10
    why_it_matters: decides the demo date
decisions:                      # the existing list, with optional fields added
  - {id: D4, size: small, status: decided, decided_by: lead, decided_on: 2026-10-12,
     evidence: "https://example.test/pr/9", recommendation: yes}
```

Validation (one sentence per problem, as `ledger check` does today): a ready deliverable needs a fallback; an owner
must be a role; `verified.by` must be a role **other than the deliverable's owner** (an owner cannot vouch for their own
work); `verified.evidence` must be a URL or a commit; `decided_by` must be a role or the human; a pending
decision still needs a recommendation. All new fields are optional, so a 0.3 ledger validates unchanged.

**"When someone last actually opened it" is a claim, not telemetry.** onloop has no way to see a person open a page. The
page therefore shows the `verified` record (who, when, evidence) and says so in its legend. A deliverable whose
verification is older than `workbench.stale_days` (default 7) turns amber; one with no verification is red. The page prints `by` beside the colour. It never
turns green on its own.

### 5.2 Claims and criteria

```yaml
# claims.yaml
claims:
  - id: C1
    text: median lead time, hours          # what a reader sees
    definition: lead-time-open-to-merge    # the id of a definition (below)
    source: {file: out/kpi.json, pointer: /lead_time/median_hours}
    denominator: {file: out/kpi.json, pointer: /lead_time/n}
    produced_by: {run: run-2026-10-12-a, commit: 6da2e47}
definitions:
  lead-time-open-to-merge:
    table: kpi.lead_time
    says: hours from pr.opened (pr.ready for a draft) to pr.merged, merges in the window
# criteria/K1.yaml
id: K1
for_claims: [C1]
written_at: 2026-10-10T09:00:00Z      # also checked against the file's commit time
rules: [{claim: C1, op: "<=", value: 48}]
baseline: {run: run-2026-10-05-a, repeat: run-2026-10-05-b}   # the baseline run twice
```

A **producing record** is a JSON file a measured run wrote, and it counts only if it is **registered**: the command
that wrote it appended a `record.written` event carrying the file's sha256, the definition id, the run id and the
commit, **in the same process that computed the numbers**. Only onloop's own measuring commands do this, as
**measured**: `kpi --record` and `acceptance --record`. A file registered afterwards through `onloop record` is
**external, attested**: the event names who registered it, the page shows it as external and attested, and it is never
displayed as measured, so registering cannot launder a typed number into a measurement. A file that merely sits at the
path, however well formed, is not a record: it is an assertion. The workbench resolves a claim only when the file's current sha256
equals the registered one, so an edited record stops resolving. It then checks three things, and shows the result, not
only the value:

1. the value at `source.pointer` exists and is a number;
2. the record's own `definition` field equals the claim's `definition` (this catches a right-looking number from the
   wrong table: the validator's failure mode in the architecture review);
3. the denominator is present, and the claim states it.

A **criteria object** is valid only if it was written before the run it judges: its `written_at` and the commit time
of its file are both earlier than the run record's `started_at`. A criteria file edited after the run shows as
`late`, never as PASS. A baseline that was not run twice shows its determinism as `not measured`; when it was, the
page shows `identical` or the difference. Verdicts are computed (`PASS`, `FAIL`, `LATE`, `NO BASELINE`); nobody types
one.

### 5.3 What the log already gives

`pr.opened`, `pr.ready`, `pr.head_moved`, `review.submitted`, `review.dismissed`, `verdict.reversed`,
`gate.merged`, `gate.refused`, `slot.*`, `preflight.reported`, `handler.dead`, and the new `record.written` (a new
event kind, additive: path, sha256, definition id, run id, commit). The inbox's third section and the
delta pane are queries over these.

## 6. The reviewer's delta pane

**What exists.** `pr.head_moved` carries `old`, `new`, `substantive` and `basis` (`patch-id` or `unknown`).
`substantive` comes from `git patch-id --verbatim`, which keeps whitespace and line endings in the id, so a CRLF-only
or indentation-only push is *already* substantive. That is the safe direction (a dedent changes what Python does).
What the reviewer cannot see today is **why**, so a one-character dedent and a rewritten function look the same.

**What 0.4 adds.** At the moment intake sees a head move, it also stores a `delta` in the event payload, computed by
`gitutil.delta_basis(old, new)` against the fetched refs:

| Field | Meaning |
|---|---|
| `tree_equal` | the two heads have the same tree: a merge-only or message-only push |
| `kind` | `merge_only` (tree equal), `crlf_only` (equal after `--ignore-cr-at-eol`), `whitespace_only` (every changed line is equal to its old line after stripping leading whitespace, trailing whitespace and CR; computed line by line, never by a `git diff` whitespace flag), `substantive` |
| `files` | per changed file: lines added, lines removed, lines whose only change is a carriage return, lines that differ only in leading or trailing whitespace |
| `indent_sensitive` | the changed files whose extension is in `workbench.indent_sensitive` (default `.py .yaml .yml .md .mk`, `Makefile`) and that have indentation-only lines |

**The whitespace class is deliberately narrow.** `git diff -w` ignores whitespace *inside* a line, so a space removed
inside a string literal, or between two tokens, would be called whitespace-only while it changes what the code does.
The delta therefore never uses `-w`, `--ignore-all-space` or `-b` (`-b` also collapses runs of whitespace *inside* a
line). It compares the two versions of each changed line after stripping leading whitespace, trailing whitespace and
CR, and nothing else, and the label says what that means: "lines differ only in leading or trailing whitespace".
Two fixtures must come out `substantive`: a space removed inside a string, and two spaces reduced to one inside a
string. Two mutations must fail them: one switches the comparison to `-w`, one reintroduces `-b`.

`delta` is **display only**. It never changes `substantive` and never feeds a decision: the reviewer-wait KPI and the
autogate still read `substantive`. The payload's old fields do not change, so a 0.3 log reads as before; a head move
with no `delta` shows `delta unavailable (git too old, or refs not fetched)` and keeps its `substantive` label.

**The pane** shows, for each head move on a pull request under review, newest first:

- the label, with the log's word first: `substantive (patch-id)`; then the detail, for example `crlf-only: 14 lines in
  2 files` or `indentation-only in a file where indentation is meaningful (.py)`;
- principle 6: a `crlf_only` or `whitespace_only` delta never reads `harmless`. It reads `substantive by patch-id;
  lines differ only in leading or trailing whitespace`, with the files, because a reviewer must look;
- `merge-only (trees equal)` only when `tree_equal` is true *and* `substantive` is false;
- the last reviewed head (from `review.submitted`) and whether the current head is the one the latest verdict names;
- the open must-fixes against the new diff: the reviewer's unresolved inline threads, and for each whether the lines it
  was anchored on are in the delta (`touched` / `anchor lines unchanged`). The second label says only what was
  checked: the lines the thread was anchored on did not change in this push. It does not say the must-fix was met.

The threads need a small intake addition: review comments with `path`, `line`, `original_commit`, resolved or not,
become `review.thread_opened` and `review.thread_resolved` events (new kinds, additive). This is the largest piece of
the pane and the first cut (section 11).

## 7. The inbox, the Deliverables page and the evidence drawer

**Deliverables (L0).** One table, owner and state, a readiness checklist (owner set, state, fallback present, link
reachable by shape, verified within `stale_days`), each item green, amber or red, with the colour computed by a function
that the page prints in its legend. Sorted by due date. A deliverable with no owner is red and says why.

**The inbox, three sections** (architecture 4.8):

1. **Rulings**: `decisions` with `status: pending`, grouped by size, each with its recommendation and the class it
   falls in (read from the policy: which classes only the human approves). Plus open pull requests the policy says
   only the human can approve.
2. **Questions only you can answer**: the ledger's `questions`, with age and `why_it_matters`.
3. **Decided by the lead this week**: decisions with `decided_by` other than the human and `decided_on` in the last
   seven days, each with its evidence link, and overturnable (the page says how: a pull request that reopens it).
   **A decision whose `decided_by` is the human is never in this list**; it is routed to "your record", generated
   from the same decisions. A decision with no `decided_by` and no evidence is shown under a fourth heading,
   `unattributed`, in red. It is the case the architecture asks to make impossible to miss.

Everything else stays off the page by default and is behind a link, as the architecture says.

**The evidence drawer.** Each chip opens a panel for one figure. For a measured figure it shows the claim's text, its
definition id and sentence, the producing record, the commit, the denominator and the criteria with PASS or FAIL. For
a generated line it shows `fresh` or `stale` and the inputs hash it was built from. The drawer is a static
`<details>` element and a hash-anchored section, so it works without script.

## 8. The three items queued by the reviews of the loop without waiting

### 8.1 GitHub-anchored human grants

**Problem.** `onloop slot grant ID --by NAME` records who *claimed* to grant; the log is attested, not authenticated.
For a slot whose approver is `human`, that is the weak point (security notes, 0.3).

**Design.** A slot may set `human_grant: anchored`. Then `slot grant` for that slot requires `--anchor URL`, the URL of
a comment on the pull request or issue named by the request's `ref`, and verifies **against GitHub, live**:

0. the request's `ref` is exactly `pr#N` or `issue#N`; an anchored slot **refuses any other ref**;
1. the comment exists on that reference. The repository, the number and the author are read from **the API response**
   (the comment's own `repository`, `issue_url` and `user`), never parsed out of the URL string, so a URL that names
   another repository or another pull request fails;
2. its author is the policy's `human`, type `User` (the same test the gate uses for the human: `identity.is_login`),
   as the API reports it;
3. its body contains the line `/grant <request id>` (parsed by the one sanitiser, `gate.live_lines`, so a quoted or
   fenced copy does not count);
4. the comment is not older than the request, and not edited after the grant is recorded (the comment's `updated_at`
   is stored in the event).

The grant event stores the comment id and `updated_at`. An anchored slot refuses a bare `--by`. Optionally, in act mode
a small handler turns such a comment into the grant by itself, through the same verification; the event that wakes it
is a hint, and the handler re-reads the comment. `human_grant: attested` stays the default, so nothing changes for a
0.3 policy. **Loosening:** setting `human_grant` from `anchored` to `attested`. Making it anchored tightens.

### 8.2 Opt-in refusals for line endings and uncovered paths

**Problem.** Pre-flight reports `W004` (a changed file gained carriage-return line endings) and `W005` (a changed path
no policy path covers) as warnings. The gate does not refuse for either.

**Design.** A new top-level policy block, default empty:

```yaml
enforce:
  line_endings: refuse      # refuse | warn (default warn)
  uncovered_paths: refuse
```

When a field is `refuse`, the gate adds `P004` (line endings) or `P005` (uncovered path) to its table, pre-flight
reports them as form refusals instead of warnings, and the audit reads the same codes. They join the **form set**, so
F1 counts them. The detection rules are the ones W004 and W005 already use: `P004` fires only when a changed line
**gained** a carriage return, so an old CRLF file that is merely touched is not refused; `P005` fires only for a path
no policy rule matches. **Loosening:** `refuse` to `warn`, or removing the field. A test pins that every pre-flight
code is still a gate code and that the audit imports the same table.

### 8.3 Slot capacity above 1

**Problem.** A slot is one-at-a-time. A test runner or a pool of two hosts can serve two holders, and today they queue
for no reason.

**Design.** `slots.<name>.capacity` (whole number, default 1, at most 8). Schema (`user_version` + 1, additive):
`grants.seat INTEGER NOT NULL DEFAULT 1` and the unique index becomes `UNIQUE(slot, seat) WHERE released_at IS NULL`.
The grant picks the lowest free seat inside the same transaction that checks the queue, so the index, not the code,
enforces `capacity`. The queue stays FIFO by `requested_at`, then id. Each lease keeps its own expiry and its own
renewals. `slot status` shows seats. **The `gate` slot stays at 1**: a policy that sets its capacity higher is a
problem at load time. **Loosening:** raising `capacity`.

## 9. Test strategy

Mutation-first, as in 0.3: each step has a mutation, and the step is not done until the test fails under it
(`tools/mutation_check.py`, one table, run whole before a milestone clears).

| Step | Test | Mutation that must fail it |
|---|---|---|
| Build is a function | build twice, equal bytes; build with a read-only database | read the clock inside `build`; open the database read-write |
| No network | patch `socket`; build and check | add a `urlopen` in `render` |
| No orphan figure | scan generated HTML for digits outside chips, dates, ids | drop one `chip()` |
| Escaping | PR title and ledger text with `<script>`, `javascript:` and `@name`; assert escaped, no live link | skip `escape` in one field |
| Links | only allow-listed origins are links | allow any `https` |
| CSP | the meta tag exists and the inline script's hash matches | change the script, keep the hash |
| Ledger sections are not anchors | `anchor.py` output for a ledger full of `deliverables` and `questions` contains none of it | have `full()` read `deliverables` |
| Deliverables colour | ready without fallback is red; verification older than `stale_days` is amber; none is red; never green by itself | treat missing `verified` as green |
| Inbox routing | a human-decided decision is in "your record" and never in the lead's list; unattributed is red | filter on `decided_by != lead` only |
| Claims definition | a claim whose source record names another definition is flagged | skip the definition comparison |
| Registered records | a well-formed file with no `record.written` event does not resolve; an edited file stops resolving; a file registered through `onloop record` shows as external and attested and never as measured | resolve on the path alone; skip the sha256 comparison; show an `onloop record` file as measured |
| Import boundary | nothing outside `workbench/` imports it; the gate, handlers and `anchor` never read `model.json` | import `workbench` from `gate` |
| Criteria order | criteria edited after the run is `LATE`, never PASS | compare to the wrong timestamp |
| Determinism | baseline run once reads `not measured` | treat one run as identical |
| Delta basis | fixtures: merge-only, CRLF-only, indentation-only in a `.py`, a space removed inside a string and two spaces reduced to one inside a string (both must be `substantive`), substantive; the label never softer than `substantive` | label a `whitespace_only` push `harmless`; switch the whitespace test to `-w`; reintroduce `-b` |
| Delta is display only | `substantive` and the KPIs are unchanged with and without `delta` | let `delta.kind` set `substantive` |
| Threads | a thread on a touched line is `touched`, on a line the push did not change `anchor lines unchanged` | invert the test |
| Anchored grant | comment by a User of the human's name, by the agents App, quoted, fenced, older than the request, edited afterwards, on another repository, on another pull request, with a ref that is not `pr#N` or `issue#N`: all refused | accept `User` with any login; skip the edit check |
| P004 and P005 | gained-CR fires, touched-CRLF does not, uncovered path fires; every pre-flight code is a gate code | fire on a merely touched CRLF file |
| Capacity | N+1 requests: N seats, the last waits; two-process race cannot exceed N; `gate` capacity 2 refused | drop `seat` from the unique index |
| Loosening | each new field's loosening in the `automation_loosening` style, both directions | make one a no-op |
| Selftest | `onloop selftest` covers workbench check, claims, criteria, delta, inbox | remove one from the list |

Fixtures are real: the delta tests use a throwaway git repository with real commits, because patch ids, tree equality
and `--ignore-cr-at-eol` are git's behaviour and a fake would test nothing. The rehearsal of 0.3 gains a workbench
step: the scripted week is built into pages, and the numbers on them are compared with `kpi --shadow` and `acceptance`.

## 10. The acceptance test, defined so it can be measured

**Setup.** One real project, the first one onloop was extracted from. Its ledger gains `deliverables:` and
`questions:`; its published figures move into `claims.yaml`. `onloop workbench build --out site` runs from the same
database the runner writes. The check is a command, `onloop workbench check --since DATE`, which reads the site
directory, the database and the files, and prints PASS or FAIL with the numbers. No number is typed into a report.

**Pass conditions** (all must hold on the build the human opens on the Monday):

| # | Condition | Computed from |
|---|---|---|
| W1 | **Reproducible**: building twice from the same inputs gives identical bytes; the database is opened read-only; no network call | the check rebuilds and compares |
| W2 | **No orphan figure**: 0 numbers outside a chip, a date or an identifier | HTML scan |
| W3 | **Every chip resolves**: each measured chip names a producing record that is registered (a `record.written` event whose sha256 equals the file's), a definition that matches, a denominator, and (where one applies) a criteria object; 0 unresolved. A record registered through `onloop record` resolves as **external**, and is counted apart from measured | log, model, files |
| W4 | **Inbox matches truth**: the pending rulings, open human-only pull requests and deliverables on the page equal the ledger and GitHub's open pull requests, item for item: 0 missing, 0 extra | ledger, `gh`, model |
| W5 | **No misrouting**: 0 decisions with `decided_by` equal to the human in the lead's list; 0 decisions decided this week without an evidence link outside the `unattributed` heading | ledger |
| W6 | **Delta labels are complete**: every `pr.head_moved` in the window has a delta label or the explicit `unavailable`; an independent recomputation with `git` agrees with the stored `kind` on a sample of at least 10 head moves (all of them if fewer) | log, git |
| W7 | **Never softer**: 0 head moves where the page's first word is weaker than the event's `substantive` | log, HTML |
| W8 | **The page is not an input**: 0 events in the log written by the workbench; `anchor` output unchanged by the new ledger sections; an import-boundary test passes (nothing outside `workbench/` imports it, and the gate, the handlers and `anchor` never read `model.json` or the site directory) | log, anchor, imports |

**The human's check on Monday** (not computed; recorded as the human's own, labelled so). It takes place inside the
existing Monday touchpoint, not in an extra meeting. The human opens the page without being sent anything else and
gives three answers: what is ready, what is mine, what changed since last Monday. They are written into the ledger
changelog, and a cross-check by the Operator compares each answer with the ledger and GitHub: **3 of 3 agree**. This
is the real purpose and the only part that needs a person; W1 to W8 exist so that a green page can be believed.

**If the human declines the Monday check**, 0.4 is done when W1 to W8 hold, and the release notes say that the
reader-facing claim was not tested. Nothing else in the plan depends on the check.

**Baselines (before and after).** The measure of improvement is the size of the Monday message the human got before:
the number of separate items in the previous four Monday decision lists, taken from the ledger changelog and labelled
estimated, since the old way left no events. After: the number of items the page's inbox shows on the Monday build.
A claim of improvement names those four dates, the denominator, and the build.

**Leaving the build week** depends on criteria, not the calendar: W1 to W8 hold on two consecutive builds seven days
apart and, if the human takes part, the Monday check is 3 of 3.

**Amendment, 2026-10-11 (W6).** W6 as written asks that *every* `pr.head_moved` in the window is on the page. The page draws
open pull requests only, so a move of a merged or closed one can never be on it, and W6 would fail on any real project
for a reason that is not a fault of the page. W6 now requires the moves of pull requests that are still open (the last
lifecycle event in the log is not a merge or a close), and reports the others apart as `moves_of_closed_prs`, so nothing
is dropped silently. Found while writing the rehearsal's workbench step (the scripted week has 3 moves, all of merged
pull requests); made in commit 38207f4, before any acceptance run of the workbench, real or rehearsed. Raised by the
Control Plane engineer and accepted by the Lead; for the Reviewer to judge. Only the on-page part is open-only: the
git recomputation sample still covers all moves in the window, merged or not (corrected after review).

**Amendment, 2026-10-11 (not exercised).** Found by running the workbench on a real ledger with no events: W6 and W7 printed PASS
over zero head moves. Every check now reports the items it looked at; one that looked at none is `NOT EXERCISED (0 items)`, and
`workbench check` exits non-zero unless `--allow-empty` is given. This tightens W1 to W8; made before any acceptance run, on the
Lead's ruling. An acceptance run never uses `--allow-empty`: a check that looked at nothing is a gap in the evidence, and the
run is judged on the evidence it has. W6 counts its recomputation half apart: with moves on the page but none carrying a stored
delta kind, it is NOT EXERCISED too.

## 11. Milestones and the order I would cut

Small commits on local branches, one branch per milestone, the Reviewer reads each, nothing is pushed, and a public
release goes to the project lead.

| M | Content | Branch |
|---|---|---|
| M1 | `workbench/model.py` skeleton, `render.py` with `chip()` and escaping, `build` (determinism, read-only, CSP), the orphan scanner | `m1-workbench` |
| M2 | Ledger `deliverables:`, `questions:` and decision fields with validation; the Deliverables page; the inbox in three sections with routing | `m2-deliverables` |
| M3 | `claims.yaml`, producing records, definition check; criteria objects with pre-registration order and determinism; the evidence drawer | `m3-claims` |
| M4 | `gitutil.delta_basis`, the stored `delta`, the pane without threads; fixtures with real git | `m4-delta` |
| M5 | Review threads as events and the touched/untouched must-fix list | `m5-threads` |
| M6 | `enforce:` with P004 and P005; `capacity` and seats; anchored human grants | `m6-queued` |
| M7 | `workbench check` (W1 to W8), `serve`, selftest additions, docs, the release candidate, the Monday week | `m7-release` |

M4 and M6 do not depend on M1 to M3 and can be built in either order. M6 is the only milestone that changes a table
(`user_version` + 1) and the gate's code table, so it gets the Reviewer's full read.

**If the schedule slips, cut in this order:** (1) review threads (M5), keeping the pane without the must-fix list: if threads are cut, the pane prints
"must-fixes not shown in this build" in the place of the list, so that no reader takes the absence for none;
(2) `serve` (the build and `--watch` stay); (3) the optional handler that turns a human comment into a grant (the
verified `--anchor` stays); (4) slot capacity (the one item with a schema change and no reader waiting for it);
(5) the criteria object's determinism field (the order check stays). **Never cut:** the escaping and CSP, the orphan
scanner, the "never softer" rule, the routing of the human's rulings, W3 (the definition check and the registered
record), W8 (the page is not an input), and the mutation table. These are the parts
whose failure looks like a working page.

## 12. Review-integrity risks (for the Reviewer)

Each is a way 0.4 could lower the bar while looking like transparency. For each: what I rely on, and the test.

1. **The page launders a number.** A chip can point at a file that says what the claim wants. The definition check
   compares the record's own definition id with the claim's, and the record must be registered by the command that
   wrote it (a `record.written` event with its sha256, definition and run id), not typed. Test: a claim with a
   mismatched definition is flagged; a record with no `record.written` event, or whose file changed afterwards, does not
   resolve.
2. **A green that nobody earned.** Deliverables and criteria cannot be green by default: absence is red or `LATE`.
   Test: delete the verification, the fallback and the baseline; assert none turns green.
3. **Injection through page content.** PR titles and ledger text are hostile input. Escaping, no links outside the
   allow-list, a CSP that forbids external requests. Test: the `<script>`/`javascript:` fixture.
4. **Injection through the new ledger sections into agents.** Anchors read only the north star, the focus and the
   queues. Test: the ledger-sections mutation. The reviewer reads ledger changes as a prompt change (0.3 docs).
5. **A softer word.** The delta pane can make a risky push look routine. Principle 6 and test W7: the first word is
   never weaker than `substantive`; a whitespace-only push in an indentation-sensitive file is called out.
6. **The delta becomes a decision.** It is display only; `substantive` and the KPIs ignore it. Test: remove `delta`
   from every event and assert the KPIs and the autogate's behaviour are byte-identical.
7. **Routing hides a ruling.** A decision the human made must be findable. It is shown under "your record", and the
   unattributed heading is red. Test: W5.
8. **An anchored grant that is not anchored.** A comment by a look-alike User, a quoted line, an edited comment, a
   comment older than the request, a comment from another repository or another pull request (identity comes from the
   API response, not from the URL). Each is refused; the comment's `updated_at` is stored so a later edit is visible.
9. **A capacity bug becomes a double grant.** The unique index is the guard, not the code. Test: the two-process race
   with capacity 2 and 3 requests.
10. **P004 and P005 strand an old repository.** Both are opt-in, default `warn`, and P004 fires only on gained
    carriage returns. Test: a touched CRLF file with no new CR is not refused.
11. **The workbench writes.** It opens the database read-only and appends nothing. Test: W8, plus a read-only file
    system build.
12. **A stale page believed as live.** Every page shows its build time and inputs hash in the header and says
    `generated, not live`; `check` fails if the header is missing. The page is a snapshot, and says so. A rebuild that
    fails does not replace the last good page: that page stays, marked **stale** with the error, so a broken build is
    visible rather than silent.

## 13. Decisions

To be recorded in `docs/decisions/` (decided 2026-10-12; item 1 is decided, the rest are agreed as recommended):

1. **Technology: decided.** Static pages built from a versioned `model.json`; React and FastAPI arrive with the first
   interaction (the 0.5 graph or 0.6 editing), behind the same model. Precedent: generated static report sites are the
   mainstream form for read-only views (dbt docs, coverage.py HTML and Allure all work this way). Static pages run
   nothing on the host, so there is no host decision to take.
2. `deliverables:` and `questions:` live in `ledger.yaml`, not in a new file. Alternative: a separate
   `deliverables.yaml`. Recommendation: the ledger, which already has a validator, a changelog rule and an owner.
3. "Last opened" is a recorded verification with evidence, not telemetry. Stated on the page.
4. `enforce:` is a top-level policy block; the default stays `warn`, so no existing repository changes behaviour.
5. Anchored grants are per slot (`human_grant`), default `attested` in 0.4; the default changes at 1.0 with a changelog
   note, as `reviewer_ack` did.
6. Slot capacity is capped at 8 and the `gate` slot is fixed at 1.
7. **The Monday check** runs inside the existing Monday touchpoint, not a new meeting. If the human declines it, 0.4's
   done-when degrades honestly to W1 to W8 alone (section 10): the page is then shown to be a faithful function of its
   inputs, and the claim "the human knows what is ready and what is theirs" is stated as untested, not as met.

## 14. Later (not in this plan)

- Merge train, the ticket graph with semantic zoom and the drift detector (0.5); canvas editing, edges as authority
  and the copilot (0.6, 0.7).
- A notification when a deliverable turns amber. It would be the first thing the workbench says without being asked,
  so it goes through the same watcher rules as every promise.
- A scheduled build that publishes to the repository's Pages site. It exposes the page; that is the human's decision.
- An act-mode handler that turns an anchored `/grant` comment into the grant by itself, through the same verification
  (nothing in act mode is approved yet).
- `kpi --backfill` from 0.3's cut list, if the Monday baseline needs history older than the log.

## 15. Estimate

By milestone, in working sessions of about three hours each, including the Reviewer's read and fixes: M1 two, M2 two,
M3 two, M4 two, M5 two, M6 three, M7 two plus the Monday weeks. About fifteen sessions and two calendar weeks of
measurement. The plan's own risk is M4: the delta's meaning rests on git behaviour across versions and line-ending
settings, so it gets real repositories and the most fixtures. The second is M6, the only one that changes a table and
the gate's refusal set.
