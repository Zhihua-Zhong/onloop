# Configuration reference

onloop reads two files from the root of the repository: `onloop.yaml` (the policy) and `ledger.yaml` (the people's work queue,
which the `onloop ledger` commands read; not the overlay ledger, `ledger.db`).
Both are YAML and are parsed with a safe YAML parser. `onloop policy check` and `onloop ledger check` report every problem
as a plain sentence and exit 1 if there is any.

`onloop init` writes a starter version of each. [examples/minimal/](../examples/minimal/) holds a filled-in pair that
validates.

## onloop.yaml

A top-level key that is not listed here is ignored.

### Identities

| Field | Type | Default | Meaning |
|---|---|---|---|
| `version` | integer | none | Written by the starter file as `1`. Not read in 0.1.0. |
| `human` | string | required | The GitHub login of the person on the loop. The only account that approves classes whose approver is `human`. |
| `reviewer` | string | required | The login of the reviewer GitHub App, written bare, without a `[bot]` suffix. (The REST API reports an App as `slug[bot]`; onloop reads a Bot's login without the suffix, so the bare slug is what matches. A `User` with the same name never matches.) |
| `agents` | string | required | The login of the GitHub App the team commits, pushes and merges as, written bare. |

Rules enforced by `policy check`: each must be a non-empty string; `human` must differ from both Apps; `reviewer` and
`agents` must differ from each other.

### roles

A mapping from a role name to a mapping of settings. At least one role is required. The value must be a mapping; write
`{}` for a role with no settings.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `roles.<name>.charter` | string | none | Path of the role's charter. Documentation only; onloop does not read it. |
| `roles.<name>.leads` | list of role names | `[]` | The roles this one coordinates. The stop anchor asks whether each has a next item. |
| `roles.<name>.anchor` | `session` or `every_message` | `session` | `every_message` adds a short anchor before each incoming message (for coordinating roles). Not authority: changing it needs no approval. |
| `roles.<name>.kpis` | list of names, or mapping of name to target | `[]` | The measures the full anchor shows this role. Not authority. |
| `roles.<name>.codeowner` | string | none | The CODEOWNERS handle (`@user` or `@org/team`) for paths this role owns. Without it, the role's paths get no CODEOWNERS line. |

Role names are matched case-insensitively against the authoring role in a pull request title (`[Builder]` and
`[builder]` are the same), so write them in lower case. A role name used in `[role]` or `Role:` may contain letters,
digits, `_` and `-`.

### classes

| Field | Type | Default | Meaning |
|---|---|---|---|
| `classes` | mapping of class name to `human` or `reviewer` | `{}` | Extra reserved classes, and overrides for the built-in ones. |

Six classes always exist and default to `human`: `claims`, `safety_widening`, `authority_widening`, `destructive`,
`money_accounts`, `leaves_project`. Listing a built-in name changes its approver. Any approver other than `human` or
`reviewer` is a problem. `authority_widening` is also the class a policy loosening is filed under (see the
[concepts page](concepts.md#base-against-head)); a loosening needs the human whatever approver this class names.

### tiers and defaults

| Field | Type | Default | Meaning |
|---|---|---|---|
| `tiers` | list of strings | `[routine, full]` | Review depths, lowest first. Must be non-empty. |
| `defaults.tier` | string | the first tier | The tier of a path that does not name one, and of a file no path covers. Must be one of `tiers`. |

### paths

A mapping from a path key to a rule. At least one entry is required, and `onloop.yaml` must be an entry with a
`reserved` class.

A key is a file path relative to the repository root, or a directory prefix ending in `/`. There are no glob
patterns. Backslashes are read as `/` and a leading `./` is dropped. A file is matched by the longest key that covers
it; an exact file key beats a directory key only by being longer. A file that no key covers has no owner, the default
tier and no reserved class.

| Rule field | Type | Default | Meaning |
|---|---|---|---|
| `owner` | role name | none | The role that owns the path. Must be a key of `roles`. The gate requires the authoring role to be this one, or a `Cross-owner:` line. |
| `tier` | one of `tiers` | `defaults.tier` | The review depth of the path. |
| `reserved` | class name | none | The reserved class. Must be a built-in or a key of `classes`. |

A rule may contain only those three fields; anything else is a problem. A rule value must be a mapping.

### Checks

| Field | Type | Default | Meaning |
|---|---|---|---|
| `required_checks` | list of strings | `[]` | GitHub check-run names that must be successful on the exact head before the gate merges. A missing check counts as failing. |
| `checks` | list of `{name, run}` | `[]` | Your project's own checks. The gate runs each `run` command through the shell in a clean detached worktree of the head, using the commands in the policy on the base. A non-zero exit refuses the merge. |
| `post_merge_checks` | list of `{name, run}` | `[]` | Commands the gate runs, in a clean worktree of the merge commit, after a merge. They are read from the policy on the base, never from the head. A non-zero exit makes the gate revert the merge. `policy check` validates the shape: each entry needs a non-empty `name` and `run`. |

Each `checks` entry needs a non-empty `name` and `run`. Dropping a check, or changing its command, in a pull request is a policy loosening; so is dropping a name from
`required_checks`. Checks are compared by name and command.

The starter policy requires one check, `policy`, which is the job name in the starter gate workflow.

### audit

| Field | Type | Default | Meaning |
|---|---|---|---|
| `audit.since` | commit-ish | the commit that first added `onloop.yaml` | The commit after which `onloop audit` starts. `--since` on the command line overrides it. |

### apps

Used only by `onloop as-app`. Keys are the identities `agents` and `reviewer`.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `apps.<identity>.app_id` | integer or string | required | The GitHub App ID. |
| `apps.<identity>.installation_id` | integer or string | required | The installation ID of the App on the repository. |
| `apps.<identity>.slug` | string | required | The App's slug. The command is committed as `<slug>[bot]`. |
| `apps.<identity>.key_env` | string | required | The name of an environment variable whose value is the path of the App's private key file. |
| `apps.<identity>.bot_user_id` | integer | none | The bot account's numeric user ID. When set, it is put in the author's no-reply address so GitHub attributes commits to the App. |

The IDs are not secrets. The key never appears in the file, only the name of the variable that points to it.

### slots and automation

`slots:` names the scarce things one holder uses at a time; `automation:` holds the settings the watchdog and the pre-flight read.
Every field of both, its default, and what counts as a loosening is in [automation.md](automation.md). A loosening of
either block is classified `authority_widening`.

### enforce

Optional. Turns the two warnings pre-flight gives into gate refusals. With no `enforce:` block both stay warnings and the
gate decides exactly as it did before.

| Field | Values | Default | Meaning | Loosening |
|---|---|---|---|---|
| `line_endings` | `warn` or `refuse` | `warn` | `refuse` adds gate code `P004`: the change adds more lines ending in a carriage return than it removes (read from `git diff`, not from the host). A file that is merely touched, or whose CRLF lines are rewritten as CRLF, is not refused. The count is net per file, so one edit that removes a CRLF line and, elsewhere in the same file, adds a bare CR line nets to zero and is not found (a known limit). If the diff cannot be read, the gate refuses. | `refuse` to `warn`, or removing the field |
| `uncovered_paths` | `warn` or `refuse` | `warn` | `refuse` adds gate code `P005`: a changed path no `paths:` entry matches. | `refuse` to `warn`, or removing the field |

Both codes are form refusals, so pre-flight reports them as problems (not warnings) and the audit judges a past merge by
the same table.

### workbench

The workbench was removed (decision 0009). The block is still accepted and checked, so an existing policy
loads, and the classifier still flags a change that loosens it. Only `indent_sensitive` is still read.

| Field | Type | Default | Meaning | Loosening |
|---|---|---|---|---|
| `indent_sensitive` | list of extensions or file names | `.py .yaml .yml .md .mk Makefile` | Files where an indentation-only push counts as substantive in the GitHub ingest. | dropping an entry |
| `stale_days`, `allowed_origins`, `human_roles` | as before | | Accepted, not read. | as before |

### post_merge

The convention for a step that must happen after a merge (publish a package, run a migration). Read by onloopd's
GitHub ingest when a pull request is merged. Each line of the pull request's body that the pattern matches becomes a
follow-up item owned by the named role, which needs evidence; the pull request stays blocked, waiting on that role
with reason `missing_evidence`, until every follow-up has its evidence (`onloop item evidence <id> <ref>`).

| Field | Type | Default | Meaning |
|---|---|---|---|
| `pattern` | regular expression | `^Post-merge:\s*(?P<role>[a-z][a-z0-9_-]{0,39}):\s*(?P<step>\S.*?)\s*$` | Matched against each line of the body. It must name the groups `role` and `step`. Point it at a section of your pull request template to use that instead. |

With the default, a body line `Post-merge: operator: publish the package to the registry` makes one follow-up for
`operator`. At most 20 steps are read from one body.

### Complete example

```yaml
version: 1
human: owner-a
reviewer: rev-bot
agents: agent-bot

roles:
  lead:    {charter: docs/charter/roles/lead.md}
  builder: {charter: docs/charter/roles/builder.md, codeowner: "@your-org/builders"}
  reviewer: {}

classes:
  copy_edits: reviewer

tiers: [routine, full]
defaults:
  tier: routine

paths:
  src/:          {owner: builder}
  docs/:         {owner: lead}
  docs/claims/:  {owner: lead, reserved: claims}
  onloop.yaml:   {owner: lead, tier: full, reserved: authority_widening}

required_checks: [policy]
checks:
  - {name: tests, run: "pytest -q"}
post_merge_checks:
  - {name: smoke, run: "python -m myproject --version"}

audit:
  since: 1a2b3c4

apps:
  agents:   {app_id: 111111, installation_id: 222222, slug: agent-bot, key_env: ONLOOP_AGENTS_KEY}
  reviewer: {app_id: 333333, installation_id: 444444, slug: rev-bot,   key_env: ONLOOP_REVIEWER_KEY}
```

## ledger.yaml: the people's work queue

`onloop ledger check` validates the file. Passing `--policy` (the default is `onloop.yaml`) makes unknown role names a
problem; if the policy cannot be read, role names are not checked. Every `id` in the file must be unique across
workstreams, decisions and queue items together.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `north_star` | string | none | One sentence the full anchor opens with. |
| `focus` | string | required | One sentence: what matters now. |
| `workstreams` | list | `[]` | What is under way. Fields below. |
| `decisions` | list | `[]` | Questions waiting for the human. Fields below. |
| `queues` | mapping of role name to list of items | `{}` | What each role pulls next. Fields below. |
| `changelog` | list | required | Append-only history. Fields below. |
| `sla` | mapping of state name to days | see below | Limits for the watchdog. Optional. |

### workstreams

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required, unique | For example `W1`. |
| `title` | string | none | What it is. |
| `owner` | role name | required when the policy is read | Must be a role. |
| `state` | `planned`, `active`, `blocked` or `done` | required | Where it stands. |
| `since` | date (`YYYY-MM-DD`) | none | When it entered this state. If present it must be a date. The watchdog measures from it. |
| `note` | string | none | Free text. |

### decisions

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required, unique | For example `D1`. |
| `title` | string | none | The question. |
| `size` | `small`, `medium` or `large` | required | See "decisions by size" in the decision template. |
| `status` | `pending` or `decided` | required | A `pending` decision is shown under "Waiting for the human". |
| `asked` | date | none | When it was put to the human. Not validated. |
| `recommendation` | string | required while `pending` | The answer you recommend. A pending decision without one is a problem: ask for a yes or no with a default. |

Optional attribution, checked by `onloop ledger check` (a ledger without them is valid):

| Field | Type | Meaning |
|---|---|---|
| `decided_by` | a role or the human | Who decided it. A decision the human made is shown in their own record, never in the lead's list. |
| `decided_on` | date | When. |
| `evidence` | https link or commit | Where the decision can be checked. |

### deliverables

What can be opened this week. Optional; checked by `onloop ledger check`.

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Unique across the whole ledger. |
| `title` | string | Required. |
| `owner` | role name | Required. |
| `state` | `planned`, `building`, `ready` or `blocked` | Required. |
| `link` | https URL | The live link. A ready deliverable without one is red. |
| `fallback` | string | What is shown if the live link fails. Required when `ready`. |
| `due` | date | Sorts the page; overdue and not ready is red. |
| `verified` | `{on, by, evidence}` | Who looked, when, and where it can be checked. `by` must be a role **other than the owner**. This is a recorded claim, not telemetry: onloop cannot see a person open a page. |


### questions

Facts only the human can supply. Optional.

| Field | Type | Meaning |
|---|---|---|
| `id`, `title` | string | Required. |
| `status` | `open` or `answered` | Default `open`. |
| `asked` | date | The inbox shows the age. |
| `why_it_matters` | string | Free text. |

### queues

Each key is a role name; each value is a list of items.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required, unique | For example `Q1`. |
| `title` | string | none | The work. |
| `state` | `todo`, `doing`, `blocked` or `done` | required | Where it stands. |
| `ref` | string | none | A link or reference, such as a pull request number. Free text. |
| `since` | date | none | When it entered this state. Read by the watchdog. Not validated. |

`onloop ledger next --role R` returns the item in `doing` if there is one, else the first in `todo`. With neither, it
prints that the queue is empty and exits 1.

### changelog

A list; each entry needs a `date` (`YYYY-MM-DD`), a `who` and a `summary`. Add entries at the end. With
`onloop ledger check --base REF`, the changelog in `ledger.yaml` at `REF` must be a prefix of the current one; a
changed or removed entry is a problem. `render` shows the eight most recent entries, newest first.

### sla

A mapping from a state name to the number of days an item may stay in that state before the watchdog reports it.
Defaults: `doing: 3`, `blocked: 2`. Entries you write are added to the defaults. The limit applies to a workstream or
queue item that has a `since` date and is in a state that has a limit. Workstream states are `planned`, `active`,
`blocked` and `done`; queue item states are `todo`, `doing`, `blocked` and `done`. So by default only `blocked`
workstreams and `doing` or `blocked` queue items are watched; add `active: 14` to watch active workstreams.

## onloopd.yaml: what onloopd watches

onloopd's own file, next to the ledger (`.onloop/onloopd.yaml`), or named with `onloop daemon run --config FILE`. It is
checked whole when onloopd starts: any problem is reported as a sentence and onloopd does not start. An unknown key is
a problem, so a typo cannot silently turn a check off.

### port

Optional. onloopd's port on the loopback address, a whole number from 1024 to 65535. The default is 8790; `onloop
daemon run --port N` overrides both. If another program holds the port, onloopd does not start and says which port
and how to choose another; it never moves to a different port by itself, because the hooks and the jobs post to this
one.

### schedules

The scheduled jobs onloopd expects to hear from. Each is declared in the ledger when onloopd starts (again only when
its settings change, keeping its results).

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | Lowercase letters, digits, `.`, `_` or `-`, at most 64. The id in the job's ping URL. |
| `name` | string | the id | A name for people, and what a Healthchecks webhook may name the check by. |
| `period` | duration | required | How often the job runs: a number and a unit (`s`, `m`, `h`, `d`, `w`), as in `15m` or `1h30m`. At least `1m`. |
| `grace` | duration | `1h` | How late a run may be before it counts as missed. |
| `owner` | role name | required | The role an alert about the job goes to. |

A job reports its runs in one of two ways, both with the token in `onloopd.token` as `Authorization: Bearer <token>`
and a JSON object as the body (`{}` at least):

- `POST /sources/schedules/<id>/ping`, `/fail`, `/start` or `/<exit code>` (0 is a success), with an optional
  `{"log": "..."}`. Send the **last** 10,000 bytes of the job's log: onloopd keeps 10,000 bytes, and Healthchecks
  keeps only the first 10,000 bytes of a ping body, so the tail is what both keep;
- `POST /sources/healthchecks`, a Healthchecks webhook whose body is set to
  `{"check": "$NAME", "status": "$STATUS", "now": "$NOW"}`. `$NOW` is the check's due time, not the time it was
  sent; onloopd keeps it as such and stamps its own receive time.

After a sleep or an outage, results arrive in bursts. A result that repeats the schedule's last status within a
quarter of its period (at least 15 s, at most 15 min) is answered and not logged again; a `start`, or a change of
status, always counts.

A result for a schedule that is not declared is refused with 404.

### monitors

Optional. What a turn's `outcome: continue ... waker=monitor:<id>` names. On every tick onloopd probes each waker that
something waits on; a monitor is alive while its URL answers 2xx within 2 s. A `pid:` waker is alive while that
process runs, and a `cron:` waker while the schedule of that id is declared above and is not overdue (its last
success, plus its period and grace). A dead waker is one alert naming every item that waits on it.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `id` | string | required | Lowercase letters, digits, `.`, `_` or `-`, at most 64. |
| `url` | URL | required | `http` or `https` on the loopback address (`localhost`, or a loopback IP address); any other is refused. |

### github

Optional. The repository onloopd reads into the ledger on its tick, by GETs only (`gh api`, with the `gh` login of
the user onloopd runs as). It gives the same events under the same ids as `onloop intake`, so the two can run side by
side: an event already imported from `onloop.db` is not logged again.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `repo` | `owner/name` | required | The repository. |
| `base` | branch name | `main` | The base branch whose tip is followed. |
| `every` | duration | `1m` | How often it reads; at least `30s`. An unchanged list costs no rate limit (a conditional request). |
| `workdir` | path | none | A clone to take patch ids in, so a push that only merges the base is not substantive. Without one, every push counts as substantive (fail closed). |
| `policy` | path | none | The `onloop.yaml` whose required checks and identities the read uses. onloopd does not start if it does not load. |

### heartbeat_schedule

Optional. The id of one of the schedules above that is onloop's own heartbeat: a job outside onloopd pings it, so
the schedule detector sees onloopd going quiet (the dead-man switch, decision 0008). onloopd logs it as a `config.set`
event (source `config`), and the Now view's health card shows that schedule's state. Without it, the card says
"dead-man not configured", a neutral note, never OK. A name that is not a declared schedule stops onloopd at start.

```yaml
schedules:
  - id: nightly-backup
    name: Nightly backup
    period: 1d
    grace: 1h
    owner: ops
  - id: onloop-heartbeat
    period: 1m
    owner: ops
heartbeat_schedule: onloop-heartbeat
monitors:
  - id: build-watch
    url: http://localhost:9000/health
github:
  repo: example-org/example-repo
  every: 1m
```

### The carrier

Optional. The session carrier ([carrier.md](carrier.md), [decision 0010](decisions/0010-session-lease-and-delivery.md)):
the role sessions onloop delivers messages into. Without this block there is no carrier at all; with it and no
opt-in, or before `onloop carrier start`, the carrier only logs `would.deliver`.

| Key | Default | Meaning |
|---|---|---|
| `claude` | `claude.cmd` on Windows, else `claude` | the CLI to run |
| `coordinator` | none (escalations go to you) | the carried role escalations are sent to |
| `wall_per_day` | `4h` | carrier wall time across all roles per day |
| `message_retention` | `30d` | how long the text onloop stores is kept before only its hash remains |
| `roles.<role>.session_id` | required | the role's existing Claude Code session (a lowercase UUID); onloop adopts it |
| `roles.<role>.cwd` | required | the folder the session runs in; file tools are confined to it |
| `roles.<role>.tools` | required | the built-in tools a run may see. **A shell (Bash, PowerShell or any other) is refused**: with a shell named, read-only commands run without the allowlist even under `--restricted`. A future decision will define a safe shell |
| `roles.<role>.allowed_tools` | `tools` | the patterns pre-approved; never a shell either |
| `roles.<role>.model` | the CLI's | the model |
| `roles.<role>.max_turns` | 20 | model turns per run |
| `roles.<role>.wall`, `silence` | `20m`, `5m` | one run's wall budget, and its longest stretch without output |
| `roles.<role>.runs_per_hour`, `runs_per_day`, `wall_per_day` | 4, 24, `2h` | the role's budget |
| `roles.<role>.opt_in` | none | what onloop may do: `message`, `event`, `corrective`, `dead_waker`, `dead_waker_wake` (which needs `dead_waker`) |

```yaml
carrier:
  coordinator: lead
  roles:
    builder:
      session_id: 00000000-0000-4000-8000-000000000001
      cwd: C:/work/app
      tools: [Read, Grep, Glob, Edit, Write]
      opt_in: [message, corrective]
    lead:
      session_id: 00000000-0000-4000-8000-000000000002
      cwd: C:/work/app
      tools: [Read, Grep]
      opt_in: [message, event]
```

## Command reference

Every command accepts `--help`. `--policy PATH` (default `onloop.yaml`) is accepted by every command that reads the
policy. Commands that talk to GitHub accept `--repo OWNER/NAME`; the default is `$GITHUB_REPOSITORY`, else
the repository `gh` finds from the current directory. They run `gh`, so `gh` must be installed and authenticated.

| Command | Flags | Exit code |
|---|---|---|
| `onloop --version` | | 0 |
| `onloop init` | `--dir DIR` (default `.`), `--human`, `--reviewer`, `--agents`, `--force` | 0 |
| `onloop policy check` | `--policy` | 0 valid, 1 problems |
| `onloop codeowners` | `--file` (default `CODEOWNERS`), `--check` (default) or `--write` | 0 matches or written, 1 differs |
| `onloop classify` | `--base` (default `origin/main`), `--head` (default `HEAD`), `--paths P...`, `--head-policy FILE`, `--json` | 0 |
| `onloop gate N` | `--base` (default `main`), `--dry-run`, `--repo` | 0 merged or would merge, 1 refused, 2 merged then reverted, 3 merged but the merge could not be fetched or named, so nothing was checked or reverted (look at the branch by hand) |
| `onloop audit` | `--since`, `--base` (default `main`), `--repo` | 0 history holds, 1 alarms |
| `onloop as-app` | `--identity agents\|reviewer` (default `agents`), `--repo`, then `-- COMMAND...` | the command's own exit code, 2 for a refused command or missing setup |
| `onloop ledger check` | `--file`, `--policy`, `--base REF` | 0 valid, 1 problems |
| `onloop ledger render` | `--file`, `--policy`, `--html` | 0 |
| `onloop ledger next` | `--file`, `--policy`, `--role R` (required) | 0 an item, 1 empty queue |
| `onloop watchdog` | `--file`, `--no-github`, `--repo` | 0 nothing stuck, 1 findings |
| `onloop events` | `--ledger FILE` (read-only), `--kind`, `--subject`, `--source`, `--actor`, `--since TIME` and `--until TIME` (the ledger's own time), `--after SEQ`, `--limit N` (up to 500), `--json`: the ledger's events, oldest first, with the filters `/api/events` takes | 0, 1 a filter that is not valid or no ledger |
| `onloop preflight N` | `--post`, `--json`, `--repo` | 0 |
| `onloop slot` | `need`, `request`, `wait`, `grant`, `renew`, `release`, `status`; see [automation.md](automation.md) | `wait`: 0 granted, 3 expired, 4 timed out |
| `onloop anchor` | `--role R` (required), `--kind full\|short\|hardstop\|stop` (default `full`), `--ledger` | 0, 2 unknown role |
| `onloop hooks` | `--role R` (required), `--ledger`, `--command` | 0 (prints the hooks configuration, writes nothing), 2 |
| `onloop liveness record` | `--event E` (required), `--dir` (default `$ONLOOP_LIVENESS_DIR`, else `./liveness`), `--only-under ROOT` (repeatable): read a hook payload on stdin and rewrite that session's record. See [design/liveness-and-authority.md](design/liveness-and-authority.md) | always 0: it never fails a session |
| `onloop kpi` | `--ledger FILE` (read-only), `--since`, `--until`, `--policy`, `--json`: lead time, idle time with work waiting and reverts, each with its definition, counted from the overlay ledger's events | 0, 1 |
| `onloop overlay import` | `--ledger FILE` (default `$ONLOOP_LEDGER`, else `.onloop/ledger.db`), `--store FILE` (the legacy `onloop.db`), `--workgraph FILE` (the legacy work-graph store); at least one of the last two. Read-only on the legacy stores, by cursor, idempotent. The upgrade path from an install that used them; run it once | 0, 1 a store could not be read, 2 nothing named or a ledger error |
| `onloop overlay replay` | `--ledger FILE`, `--check` (rebuild into a scratch copy and compare, leaving the ledger unchanged). Prints whether the projections are byte-identical, with their sha256 | 0 identical, 1 different, 2 a ledger error |
| `onloop overlay status` | `--ledger FILE` | 0, 2 no ledger |
| `onloop daemon run` | `--ledger FILE`, `--port N` (default: `port` in onloopd.yaml, else 8790; 0 picks a free one; a port in use stops it with an error naming the port and this override, never a fallback to another port), `--host` (the loopback address; any other is refused), `--tick SECONDS` (default 10), `--config FILE` (default: `onloopd.yaml` next to the ledger, if there is one). Runs onloopd in the foreground and serves the UI at `http://localhost:<port>` ([ui.md](ui.md)). See [decision 0008](decisions/0008-resident-service-and-dead-man-switch.md) | 0 stopped cleanly, 2 refused (another host, already running on this ledger, or an `onloopd.yaml` with a problem) |
| `onloop ingest github --once` | `--repo owner/name` (required; any repository the `gh` login can read), `--base` (default `main`), `--workdir` (a clone for patch ids), `--policy`, `--ledger FILE`: one read of the repository into the ledger, as onloopd's tick does. It reads only: REST GETs, plus the GraphQL query for review threads, never a mutation, and never pushes or writes to the remote | 0, 1 the read failed (nothing written), 2 a bad repository or branch |
| `onloop kpi weekly` | `--coordinator ROLE` (default `lead`), `--json`, `--ledger FILE`: per ISO week, the asks to the human (every wait on the human, with its wait: open ones to now) and the messages onloop delivered to the coordinator, split into mechanical (timer, detector, relay), judgment (human, ask) and unclassified; beside it, the imported baseline for that week | 0 |
| `onloop kpi baseline import` | `FILE.json`, `--ledger FILE`: import a team's own weekly aggregates. The shape is [schemas/kpi-baseline.schema.json](schemas/kpi-baseline.schema.json), with an [example](schemas/kpi-baseline.example.json); the file is checked whole and refused with every problem listed. Counts only: onloop never reads a team's logs or transcripts. Importing the same file again adds nothing; a week imported again with other numbers replaces it | 0, 2 not UTF-8 JSON, or not the shape |
| `onloop report observe` | `--since TIME` (a date or a UTC time), `--until TIME` (default now), `--json`, `--ledger FILE`: each thing onloop would have done (`would.*`), next to what happened then (the role's next turn and how it ended, or the schedule's next success), and each role's turns with how many had no valid outcome. All times are the ledger's. Also `GET /api/report/observe?since=&until=` | 0, 2 a bad time |
| `onloop role pause` / `resume` | `ROLE`, `--ledger FILE`: pause a role, so every item it owns that is not done waits on the human (reason `paused_by_human`) until it is resumed; its own turns do not end the pause | 0 (also when it already was), 2 a bad role name |
| `onloop item evidence` | `ID REF`, `--ledger FILE`: attach the evidence (a URL, a pull request, a log line) a follow-up such as `pr#12/post-merge/1` needs. The follow-up is then done, and its pull request too once no follow-up lacks evidence | 0, 1 no such item, no evidence asked, or evidence already attached, 2 a bad reference |
| `onloop daemon hook` | `--event stop\|session-start\|notification`, `--role ROLE`, `--ledger FILE`, `--timeout SECONDS` (default 2): post the hook's payload (stdin) to onloopd. For a role's Claude Code hook settings. When onloopd cannot be reached, the post is spooled to `hook-spool.jsonl` and logged once when it is back | always 0; a failure is a line in `hook-errors.log` next to the ledger |
| `onloop carrier start` / `stop` / `status` | `--ledger FILE`: start the session carrier (it acts on what roles opted in to; removes `carrier.off`); stop it (the kill switch: writes `carrier.off`, live runs end within 5 s); show its state, roles, live leases and the last day's runs. See [carrier.md](carrier.md) | 0, 2 a ledger error |
| `onloop role opt-in` / `opt-out` | `ROLE ACTION`, `--ledger FILE`: let the carrier act for a carried role on `message`, `event`, `corrective`, `dead_waker` or `dead_waker_wake`, or stop it at once. Opting in cannot go past onloopd.yaml's `opt_in` | 0, 2 not a carried role or not an action |
| `onloop say` | `ROLE TEXT` (`-` reads stdin), `--ledger FILE`: a message to a carried role, posted to onloopd with its write token. A request from a local user, not verified, never an approval ([decision 0011](decisions/0011-ui-write-path.md)) | 0 queued, 2 refused or onloopd not reachable |
| `onloop daemon stop` | `--ledger FILE`, `--timeout SECONDS` (default 30): ask onloopd to stop and wait for its pid to exit | 0 stopped cleanly, 1 not running, 3 stopped by its process tree after the timeout |
| `onloop daemon status` | `--ledger FILE`: the last heartbeat and the `/health` answer | 0, 1 no heartbeat or no answer |
| `onloop supervise` | `--config FILE` and `--once` (both required): print the command of every role step that would start now; starts nothing, writes nothing. See [supervisor.md](supervisor.md) | 0, 1 the source could not be read, 2 a configuration error |
| `onloop step` | `--config FILE`, `--role R`, `--pr N` (all required), `--live` (run the step; refused with a metered credential or an unlogged CLI; default: print it), `--state DIR` (the step's output and `last-step.json`), `--out DIR` (where `result_file` goes). See [supervisor.md](supervisor.md#one-step-from-a-ci-job) | 0 ran ok or not due, 1 the step failed, 2 refused or a configuration error |
| `onloop digest` | `--config FILE` (required), `--since`, `--hours` (default 24), `--refresh` (read the source instead of the last snapshot), `--mark`, `--json` | 0, 1 no snapshot or the source could not be read |
| `onloop attention` | `--config FILE` (required), `--notify` (send and log; default print only) | 0 nothing to tell, 1 a rule fired |
| `onloop selftest` | | 0 every checker bites, 1 one does not |

Notes:

- `classify` has two modes. With `--paths`, it classifies the paths you give against the policy in `--policy`; add
  `--head-policy FILE` to say what the policy would be after the change. Without `--paths`, it reads `onloop.yaml`
  at `--base` from git, takes the changed files from `git diff --base...--head`, and reads the head policy from
  `--head` if the diff touches it; `--policy` is not used.
- `gate` reads the policy from `origin/<base>` and diffs against it, so the working repository must have fetched
  both that branch and the pull request's head commit.
- `audit` needs the full history (`fetch-depth: 0` in a workflow).
- `codeowners --check` is the default when neither `--check` nor `--write` is given.
- `--json` on `classify` prints `files`, `classes`, `approvers`, `tier`, `owners`, `unowned`, `loosening` and
  `reasons`. `approvers` lists `human` first when it is present.

## Environment

| Variable | Used by | Meaning |
|---|---|---|
| `GITHUB_REPOSITORY` | gate, audit, as-app, watchdog | Default for `--repo`. |
| the variable named by `apps.<identity>.key_env` | as-app | Path of the App's private key file. |
| `GH_TOKEN` | the `gh` CLI | Used by `gh` for the gate, the audit and the watchdog. In the audit workflow it is the workflow's own token. |

## Extension points

| Entry-point group | Callable | Effect |
|---|---|---|
| `onloop.checks` | `(facts, policy, classification) -> list[str]` | Each returned sentence is a reason for the gate to refuse. An exception is also a refusal. |
