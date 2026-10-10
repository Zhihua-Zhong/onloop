# Configuration reference

onloop reads two files from the root of the repository: `onloop.yaml` (the policy) and `ledger.yaml` (the ledger).
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

## ledger.yaml

`onloop ledger check` validates the file. Passing `--policy` (the default is `onloop.yaml`) makes unknown role names a
problem; if the policy cannot be read, role names are not checked. Every `id` in the file must be unique across
workstreams, decisions and queue items together.

| Field | Type | Default | Meaning |
|---|---|---|---|
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

## Command reference

Every command accepts `--help`. `--policy PATH` (default `onloop.yaml`) is accepted by every command except `init`
and `selftest`. Commands that talk to GitHub accept `--repo OWNER/NAME`; the default is `$GITHUB_REPOSITORY`, else
the repository `gh` finds from the current directory. They run `gh`, so `gh` must be installed and authenticated.

| Command | Flags | Exit code |
|---|---|---|
| `onloop --version` | | 0 |
| `onloop init` | `--dir DIR` (default `.`), `--human`, `--reviewer`, `--agents`, `--force` | 0 |
| `onloop policy check` | `--policy` | 0 valid, 1 problems |
| `onloop codeowners` | `--file` (default `CODEOWNERS`), `--check` (default) or `--write` | 0 matches or written, 1 differs |
| `onloop classify` | `--base` (default `origin/main`), `--head` (default `HEAD`), `--paths P...`, `--head-policy FILE`, `--json` | 0 |
| `onloop gate N` | `--base` (default `main`), `--dry-run`, `--repo` | 0 merged or would merge, 1 refused, 2 merged then reverted or an error |
| `onloop audit` | `--since`, `--base` (default `main`), `--repo` | 0 history holds, 1 alarms |
| `onloop as-app` | `--identity agents\|reviewer` (default `agents`), `--repo`, then `-- COMMAND...` | the command's own exit code, 2 for a refused command or missing setup |
| `onloop ledger check` | `--file`, `--policy`, `--base REF` | 0 valid, 1 problems |
| `onloop ledger render` | `--file`, `--policy`, `--html` | 0 |
| `onloop ledger next` | `--file`, `--policy`, `--role R` (required) | 0 an item, 1 empty queue |
| `onloop watchdog` | `--file`, `--no-github`, `--repo` | 0 nothing stuck, 1 findings |
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
