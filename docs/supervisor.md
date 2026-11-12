# The supervisor

*Status: Unreleased; dry run by default. Role steps run as CI jobs ([decision 0003](decisions/0003-roles-run-as-ci-jobs.md)):
a job runs [`onloop step`](#one-step-from-a-ci-job) for one item. onloop is moving towards hosting the role sessions
itself ([decision 0004](decisions/0004-onloop-hosts-the-conversation.md)); the step runner on this page is what such a
session is started with.*

A team of agents that runs as interactive chat sessions stops silently in three ways: a session acts only when
something sends it a turn; a scheduler that caps concurrent runs lets one hung run block its own next ones; and a
permission prompt blocks an unattended run until a person clicks. In each case the person tends to notice before any
machine does.

The supervisor is the part of onloop with no model that runs a role step. It decides whether a step is due and runs
it as one bounded, headless `claude -p` call with that role's fixed permissions and tools. State the person reads lives
in files and on GitHub. A step that fails, stalls or overruns says so in its exit code and its summary, never by
silence.

```
onloop step --role R --pr N        one governed step for one pull request, from a CI job (dry run unless --live)
onloop supervise --once            the planner: print the exact command of every step that would start now
onloop digest                      what moved, what is done, what waits on the person
onloop attention                   every attention rule, from the state directory (exit 1 when one fires)
```

All four read one configuration file, `supervisor.yaml`: the role table, the source, the person's asks and the
attention rules. See [the example](../examples/supervisor/supervisor.yaml).

## Quick look, offline

```bash
onloop step --config examples/supervisor/supervisor.yaml --role reviewer --pr 12    # prints the step's command; starts nothing
onloop supervise --config examples/supervisor/supervisor.yaml --once               # every due step's command; starts nothing
onloop digest --config examples/supervisor/supervisor.yaml --refresh
onloop attention --config examples/supervisor/supervisor.yaml                      # exit 1 when a rule fires
```

The example reads `examples/supervisor/fixture.json` instead of GitHub. Its state goes to `.onloop/` (git-ignored).

## One step from a CI job

`onloop step --config supervisor.yaml --role reviewer --pr 123 [--live] [--state DIR] [--out DIR]` runs one step for a
CI job whose event, timeout and concurrency group the CI system already gives:

1. **Due or not.** It reads the source by GETs: the head, its required checks, and whether the answering login has
   answered this head. The role's triggers then decide. A pull request that is not due is exit 0, with the reason
   (`the required checks at the head are pending`, `the head is already answered`, `it is a draft`). So an event that
   arrives before the checks are green costs one read.
2. **The governed command** ([the step](#the-step)), stopped by its own pid on the wall budget or on `silence`.
   `--live` refuses `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`, and an unlogged CLI. When
   `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) is set, it is taken as the subscription credential, and
   `claude auth status` is not asked.
3. **The result file**, if the role names one. Then `after_result`, if the role has one. A CI job usually posts in a
   later step of its own instead, so the posting credential never reaches the model step.

`--state` holds the step's stream-json output and `last-step.json`, the summary, which is also printed as one JSON
line. `--out` is where `result_file` is written. Exit codes: 0 ran ok, or not due; 1 the step failed; 2 refused, or a
configuration error.

Under GitHub Actions (`GITHUB_ACTIONS=true`) it also writes one line on what happened to the step log and the job
summary, and a notice annotation when the step did not run, so a skipped step does not look like a passed one in the
run list.

## The role table

Each role in `supervisor.yaml` names its working directory, its permission mode and allowed tools, a prompt template
(`prompt`, or `prompt_file` read from beside the configuration), a step budget (`wall`, `turns`, and `silence`, which
defaults to the smaller of `wall` and ten minutes), a WIP limit, an optional `every` (look again at a still-queued item
after this long), a retry policy, and its triggers.

| Trigger | The role's queue holds |
|---|---|
| `{kind: pr, draft: false, labels_any: [...], labels_none: [...]}` | open pull requests that match |
| `{kind: issue, labels_any: [ops], owned: true}` | open issues that match (`owned`: the `role/<name>` label names this role) |
| `{kind: ask}` | the person's asks this role owns, once their dependencies clear |
| `{kind: ledger}` | the role's `todo` and `doing` rows in `ledger.yaml` (`source.ledger`) |

Two keys change what a pull request step is keyed on, and when it leaves the queue:

- `version: head` keys a pull request on its head commit instead of `updated_at`. `updated_at` moves on every comment,
  label and review, the role's own included, so a reviewer keyed on it would be due again after each of its reviews;
  keyed on the head, only a new push makes it due.
- `unanswered_by: {login, review_states, comment_marker}` keeps a pull request out of the queue once that login has
  answered its current head: a review on exactly that sha in one of `review_states` (default approve or request
  changes), or an issue comment by the login holding `comment_marker` and the full head sha. An answer on an earlier
  head does not count.

With either key, the source also lists the open pull requests (`GET /repos/{repo}/pulls`, for the head sha) and, for
each, its reviews and issue comments. The prompt can name the head as `$item_head` (and its first eight characters as
`$item_head_short`).

`require: {checks: green, names: ruleset | all}` admits a pull request only while the checks at its head are green.
With `ruleset` (the default) the names that count are the required status checks of the base branch's ruleset
(`GET /repos/{repo}/rules/branches/{base}`); each must have reported and passed (success, neutral or skipped), and one
not yet reported is pending. With `all`, or a ruleset that requires none, every reported check run and commit status
counts, and a head with nothing reported is not green. Pending and red heads are not due; a new push is judged on its
own checks. A check re-run in a later suite counts only by its latest run.

`priority_paths: [globs]` puts first the pull requests whose changed paths match (`GET /repos/{repo}/pulls/{n}/files`;
fnmatch, so `*` also matches `/`). With `priority_when: all` (the default) every changed path must match one glob; with
`any`, one path is enough. A pull request with more than 300 changed paths is never first under `all`. After priority,
the oldest change goes first (an `expedite` item goes before all of them). The planner (`onloop supervise --once`)
orders by it; a CI job runs the pull request its event names.

`bypassPermissions` is refused: a role's tools are listed, not waived. The template may use only the named variables
(`$item_id`, `$item_title`, `$item_url`, `$item_head`, `$done_when`, `$charter`, ...); an unknown one is a
configuration error. Item text from GitHub enters a prompt only through these, on one line, and the example templates
call it data.

The top-level keys `interval`, `jitter` and `stop_grace` are still read, so older files load, but nothing uses them
since the Temporal layer was removed. `max_parallel` caps the steps the planner starts at once.

## The step

For the example reviewer the runner produces exactly:

```
claude -p --output-format stream-json --verbose --max-turns 30 --permission-mode dontAsk --tools Read,Grep,Glob,Bash
  --allowedTools 'Read,Grep,Glob,Bash(gh pr view *),Bash(gh pr diff *),Bash(gh pr checks *),Bash(gh pr review *)'
  --strict-mcp-config --setting-sources user --session-id <a new uuid per run>
```

- The prompt goes on stdin, never on the command line.
- `--tools` limits the built-in tools to the base names of `allowed_tools`, and `--allowedTools` pre-approves exactly
  those patterns. Under `dontAsk`, anything else is denied instead of prompted for. So a step that needs more fails and
  says so; it never waits on a person.
- `--strict-mcp-config` with no `--mcp-config` gives the step no MCP servers.
- `--setting-sources` loads only the role's `setting_sources` (default `user`), so a settings file in the
  working directory cannot add allow rules that widen the role's tools.
- A role's `result_schema` (a JSON Schema file) is passed as `--json-schema`. With `result_file` (a path from the
  configuration's folder, naming only `$item_number`, `$item_head_short`, `$item_head`, `$step_id`, `$role`,
  `$item_kind`), onloop writes each live step's outcome, turns, duration, permission denials, structured result and
  final text there, beside a `.json` copy; the step needs no write tool for it.
- A role's `after_result` (`{command: [program, args...], timeout: 2m}`) runs after a live step that ended ok, on
  the result just written. Its arguments may name only the result file's variables and `$result_file`,
  `$result_json` (the `.md` and its `.md.json`). It runs from the configuration's folder with the step's environment,
  with no shell; its output goes to `<result>.after.txt`, and past its timeout it is stopped by its own pid. A nonzero
  exit makes the step `after_failed`; the summary names the output file, so a person can run the command again by
  hand. This is how a role posts without a write tool: the step stays read-only, and a command the configuration
  names does the one write, from the structured result.
- The session id is new for each run and is in the summary, so the transcript can be joined to the step. It is never
  derived from the step id: the CLI refuses a session id it has seen before, so a step run again would fail before it
  started.
- `--verbose` is passed because Claude Code requires it with `stream-json` in print mode; the result is read from the
  last `result` line.

**Dry run is the default.** The command is built and printed, and nothing is executed. `--live` executes, and refuses
to start while `ANTHROPIC_API_KEY` is set: the agents run on the subscription only. It also asks `claude auth status`
(no model call) and refuses when the CLI is not logged in, or is logged in with an API key, because the step would
otherwise fail at its first turn. Log the CLI in on the host as the account the steps run under. Run the job from a
clean environment: started from inside an agent session, a step inherits that session's variables.

**Budget.** The wall budget, or a hard deadline if it is sooner, ends the step; so does `silence` with no output.
Either is stopped **by its own pid only**: on POSIX the process group it leads, on Windows `taskkill /PID <pid> /T`.
Nothing is ever looked up by image name. The outcome is `overrun` or `stalled`, and the summary says how the process
was stopped.

**When a step is due.** Steps are keyed on (role, item, item version, round). The version changes whenever the item
does: a push (or, without `version: head`, a label or an edit), or a dependency that clears. The planner reads step
records from `<state_dir>/steps.jsonl`: an item whose version no record has seen is due; a role is not started past its
WIP limit; after a final failed attempt the item waits until it changes, or until `every` passes. A dry-run record
never stands in for a live one. `onloop step` decides from the source alone and writes no step records.

## Records

| file | written by | holds |
|---|---|---|
| `<state>/last-step.json` | `onloop step` | the summary: due or not and why, the item and head, the step id, outcome, turns, duration, session id, exit code, error, output path, result file, after_result |
| `<state>/steps/<step id>-<attempt>.json`, `.err` | `onloop step --live` | the step's stream-json output and its stderr |
| `<state_dir>/steps.jsonl` | nothing in onloop today | step records (`step.started`, `step.killed`, `step.ended`), idempotent on (type, step id, attempt); read by the planner and `onloop attention` |
| `<state_dir>/snapshot.json` | nothing in onloop today | the last look at the source; read by `onloop digest` (without `--refresh`) and `onloop attention` |
| `<state_dir>/heartbeat.json` | nothing in onloop today | read by the `heartbeat_stale` rule of `onloop attention` |
| `<state_dir>/alerts.jsonl` | `onloop attention --notify` | the notices sent, so a quiet period holds across runs |

The three files that nothing writes were written by the Temporal worker, which is gone. They stay readable so a loop
owner can write them again ([decision 0004](decisions/0004-onloop-hosts-the-conversation.md)).

Outcomes: `ok`, `failed`, `max_turns`, `overrun`, `stalled`, `interrupted`, `spawn_failed`, `after_failed`,
`dry_run`.

## The person's task queue

An ask is a GitHub issue carrying `asks.label`, with:

- an id (`ask#N`);
- the person's words paraphrased (the title);
- an owner (the `role/<name>` label);
- a state, from the work graph's state labels: `open`, `blocked`, `waiting-human`, or `waiting-external` for CI and
  other outside waits;
- a `Done when:` line and a `Default:` line;
- dependencies (GitHub's own, or `Depends on: #12`);
- its last-moved time.

The mapping is the supervisor's issue reader (`src/onloop/supervisor/github_issues.py`, GETs through `gh api` only), configured under `asks:`; see [the issue mapping](#the-issue-mapping) below.

- **Drain.** An ask enters its owner's queue when it is `open` and every dependency is closed. Waiting on CI is a
  state, so such an ask is not in any queue and nothing is idle about it.
- **Digest.** `onloop digest` lists what moved, what is done (with its "done when") and what waits on the person, each
  with its default. It reads the last snapshot, or with `--refresh` the source, and writes nothing; `--mark` records
  the time so the next digest starts there.
- **Private items.** Items in `asks.private_file` stay on the host. They appear in the digest and the attention rules,
  and never become a step, because a step acts on shared state.

## Attention: initiative without a model

| rule | fires when | subject |
|---|---|---|
| `item_stale` | an open ask (not waiting on the person) has not moved for `threshold` | the ask |
| `waiting_on_person` | at least `threshold` asks wait on the person | the person |
| `role_starved` | a role has had due work but no step started for `threshold` | the role |
| `heartbeat_stale` | the heartbeat is older than `threshold`, or missing | the supervisor |

For each subject, a notice fires at most once per `quiet` period and at most `max_repeats` times while the condition
holds. The last notice says the cap was reached, and the count starts again once the condition clears. Runs of
`onloop attention --notify` share the alert log, so a quiet period holds across them.
[examples/deploy/crontab.example](../examples/deploy/crontab.example) runs it from cron.

Notifiers sit behind one interface:

- `log` prints to stderr;
- `webhook` POSTs JSON over https, or over http to the loopback address; put the URL in `url_env`;
- `desktop` raises a toast on Windows, `notify-send` on Linux or `osascript` on macOS, chosen at send time;
- `stub` is for tests.

## The issue mapping

Each issue the supervisor reads becomes a node with these fields:

| Field | Meaning |
|---|---|
| `id` | the source's id for it, for example `#42` |
| `kind` | `topic`, `task`, `decision` or `hypothesis` |
| `title`, `url` | as the source has them; only `https://` links are kept |
| `state` | open: `open`, `blocked`, `waiting-human`, `waiting-external`; closed: `done`, `cancelled`, `missing` |
| `class` | `expedite`, `standard` (default) or `background` |
| `weight` | a positive number, default 1 |
| `owner` | the role that owns it |
| `spawned_by` | the parent node |
| `depends_on` | nodes it waits for |
| `created`, `updated` | when the source says it was made and last changed |
| `default`, `expires` | for an item waiting on the human: what happens if nobody answers, and until when |

`asks.labels` says what the labels mean: `role_prefix` (`role/builder` means owner `builder`), and maps for `kind`,
`state` and `class`. The first match wins. `asks.depends_on` names GitHub's own dependencies and body patterns (every
`#N` on a matched line). `asks.fields` gives the patterns for `default`, `expires` and `done_when`. A parent comes
from GitHub's sub-issues first, then from a body pattern. Pull requests are skipped. The example is
`examples/supervisor/supervisor.yaml`.

## History

- **A plain polling loop**, then **a Temporal execution layer** (role queues as workflows, the step as an activity
  with a heartbeat). The Temporal layer was superseded by CI jobs running `onloop step` on 2026-11-03
  ([decision 0003](decisions/0003-roles-run-as-ci-jobs.md)) and removed on 2026-11-05.

## What it does not do yet

- It writes nothing to GitHub itself; the steps do, under the role's tools, or a role's `after_result` command does.
- Nothing in onloop writes `steps.jsonl`, `snapshot.json` or `heartbeat.json` any more (see [Records](#records)).
- It reads no liveness records of interactive sessions (see [design/liveness-and-authority.md](design/liveness-and-authority.md)).
- A `max_turns` outcome counts as a failure.
- `cost_usd` is what the CLI reports. On a subscription it is notional.
- Item titles reach the prompt as data, cut to one line. A hostile title is still text the model reads, so roles that
  read public repositories should keep their tools narrow.
