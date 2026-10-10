# Concepts

This page explains how the pieces of onloop fit together and why each exists. For field-by-field detail see the
[configuration reference](configuration.md); for what the design does and does not protect, see the
[security model](security-model.md).

## The shape of the loop

A team of agents works through pull requests. Each pull request passes through the same steps:

```
builder opens PR  ->  reviewer verdict  ->  [human approval, if the class asks]  ->  gate merges
                                                                                        |
                          audit re-judges the merge later  <---------------------------+
                          watchdog reports anything stuck
```

Three identities take part, and they must be three different GitHub accounts:

| Identity | What it is | What it does |
|---|---|---|
| `human` | A person's account | Approves the reserved classes. Never used by an agent. |
| `reviewer` | A GitHub App | Gives the verdict on every pull request. Cannot author, edit or merge. |
| `agents` | A GitHub App | Commits, pushes, opens pull requests and runs the merge, under the roles in the policy. |

A role is a job (lead, operator, reviewer, builder in the starter files), not an account. Several roles may share the
`agents` identity. Roles matter because the policy gives each path an owning role, and the gate checks that the
author of a pull request owns what it changes.

## Policy

`onloop.yaml` is the single source of truth for who may change what and who must approve it. It contains:

- the three identities;
- the roles, optionally with a CODEOWNERS handle each;
- the reserved classes and their approvers;
- the review tiers;
- the `paths` table, mapping each path to an owner role, a tier and a reserved class;
- the checks the gate requires and runs;
- optionally, App identity details for `onloop as-app`.

A path key is either an exact file (`onloop.yaml`) or a directory prefix ending in `/` (`docs/charter/`). There are no
glob patterns. When several keys cover a file, the longest wins. A file that no key covers has no owner, the default
tier and no reserved class.

The policy must list `onloop.yaml` itself and give it a reserved class. `onloop policy check` fails without it. A
policy that does not protect itself would let a pull request remove its own approval requirement.

## Reserved classes and approvers

A reserved class is a kind of change that needs more than the team's own judgement. Six always exist, and all six
name the human as approver by default:

| Class | Meant for |
|---|---|
| `claims` | Statements the project publishes or stands behind |
| `safety_widening` | Anything that loosens a safety rule or removes a guard |
| `authority_widening` | Anything that gives an agent or a role more authority, including the policy itself |
| `destructive` | Operations that cannot be undone |
| `money_accounts` | Spending, billing, accounts and credentials |
| `leaves_project` | Anything that sends project material outside the project |

You decide which paths belong to which class. The names are a vocabulary, not a detector: onloop does not read file
contents to decide that a change is destructive. You assign paths, and the classifier follows the assignment.

You can add classes under `classes:` and name `human` or `reviewer` as the approver. You can also move a built-in
class to `reviewer`, but see the loosening rule below: moving a class from human to reviewer in a pull request is
itself classed as authority widening.

The reviewer is always required. The human is required in addition when any touched path is in a class whose
approver is `human`. An unknown class name is read as the strictest approver, the human.

## Tiers

`tiers` is an ordered list of review depths, lowest first (default `routine`, `full`). Each path carries one. The
classifier reports the highest tier among the paths a change touches. The tier is information for the reviewer and
for the role charters (for example, "at tier `full` re-run the tests yourself"). In 0.1.0 the gate does not add
conditions by tier; the approver requirement comes from reserved classes.

## Classification

Classification answers one question: for this change, who must approve? It takes three inputs:

1. the paths the change touches, from `git diff --name-only --no-renames base...head`. This is the change's own diff,
   not a file list reported by a host. A rename is a delete plus an add, so moving a file out of a reserved
   directory is seen as touching that directory;
2. the policy on the base. The base policy decides, because the head is what is being judged;
3. when the change touches `onloop.yaml`, the policy as the head leaves it.

The result carries the touched classes, the approvers, the tier, the owning roles, the unowned paths, any loosening
found, and a sentence of reasons.

### Base against head

A pull request that changes the rules is never judged by the rules it changes. When `onloop.yaml` is in the diff, the
classifier compares the two policies. Each of these counts as a loosening:

- the human, reviewer or agents identity changes;
- a class is removed, or a class that the human approved now names the reviewer;
- a path loses its reservation, moves to a class whose approver is not the human, or disappears while no covering
  path keeps the reservation;
- a path lowers its tier, or disappears and the path now covering it has a lower tier;
- a check named in `checks` or `required_checks` is dropped.

If there is any loosening, the change is classed `authority_widening` and needs the human, even when the head policy
sets `authority_widening` to the reviewer. If the head policy cannot be read or does not validate, that is treated as
a loosening too. A change that only tightens the policy is still a change to `onloop.yaml`, which is itself reserved,
so it follows the class the policy file carries.

## The merge gate

`onloop gate N` reads the pull request, classifies it, and merges only if there are no reasons to refuse. Every
condition is checked against the head SHA read at the start:

1. The pull request is open, is not a draft, and is aimed at the base branch.
2. The reviewer App has an approving review on this head. Reviews of other commits are ignored.
3. Nobody trusted (the reviewer or the human) has a standing request for changes on this head.
4. When the classification includes `human`, the human's account has an approving review on this head.
5. Every name in `required_checks` has a successful check run on this head. A check that is missing counts as failing.
6. The authoring role is known and owns every path the change touches. The role is the `[role]` prefix of the title,
   or else the last `Role:` trailer in the commit messages. A path owned by another role needs a
   `Cross-owner: <role> - <reason>` line in the pull request body or a commit message.
7. Every command under `checks` exits zero in a clean checkout of the head, and no `onloop.checks` plugin refuses.

If anything fails the gate prints each reason and exits 1. If everything holds it merges with
`--match-head-commit <sha>`: if anyone pushed in between, GitHub refuses and nothing merges. After the merge it runs
the `post_merge_checks`; if one fails it reverts the merge with a commit carrying a `Gate-Revert: #N <sha>` trailer
and exits 2.

Review states are read strictly. `COMMENTED` and `PENDING` reviews assert nothing. A dismissed approval is gone. A
request for changes that was dismissed by someone other than the reviewer or the human still blocks; only a trusted
identity may lift it.

The policy used is the one on `origin/<base>`, never the head's. A pull request cannot relax its own gate.

### Plugins

The gate can be extended without editing onloop. A package registers a callable in the `onloop.checks` entry-point
group. It receives `(facts, policy, classification)` and returns a list of refusal sentences. A plugin that raises is
a refusal, never a pass.

## The audit

The gate decides before a merge. The audit checks afterwards, from the record, in case something got onto `main` some
other way. `onloop audit` walks the first-parent history of the base branch from a starting commit and accepts only:

- a merge by the `agents` App whose second parent is the head of its pull request, and which the gate's own decision
  would have allowed, using the policy as it read at the merge's first parent and discarding any review submitted at
  or after the merge;
- a merge by the human's account whose second parent is the pull request's head;
- a gate revert: a commit with a `Gate-Revert` trailer, whose parent is an accepted merge, and whose tree equals the
  tree before that merge.

Everything else is an alarm with the commit and the reason: a commit pushed straight to the branch, a merge no pull
request explains, a merge of something other than the head, a merge by someone else, a merge whose evidence does not
hold. The policy is read at the time of the merge, so a later change to the policy neither excuses nor condemns an
earlier merge.

The audit starts at `--since`, else `audit.since` in the policy, else the commit that first added `onloop.yaml`.

## Agent identity

If the agents worked under the human's account, "the human approved" would mean nothing. `onloop as-app` removes the
reason to share it: each call runs a `git` or `gh` command as an App installation.

For one call it signs a short-lived JWT with the App's private key, exchanges it for an installation access token,
runs the command with the token in the command's environment only, and exits with the command's status. The command
is committed under the App's bot name. `git` is configured for that call to read the token from the environment, so
it never falls back to a stored login.

Guards refuse commands that would print or swap the credential, and the reviewer identity may run only
`gh pr review` and `gh pr comment`. They stop accidents. The [security model](security-model.md) states their limits.

## Ledger and queues

`ledger.yaml` is the team's shared state, readable by a person in a minute:

- `focus`: one sentence on what matters now;
- `workstreams`: what is under way, with an owner role and a state;
- `decisions`: the questions waiting for the human, each with a size and a recommendation;
- `queues`: for each role, the items it pulls from next;
- `changelog`: an append-only list of what happened;
- `sla`: optional limits for the watchdog.

The point of the queues is a rule: a role that finishes an item takes the next one from its own queue
(`onloop ledger next --role builder`) instead of waiting to be told. A role with nothing to pull is reported as idle,
and refilling it is the lead's job. Without this, a team of agents stops as soon as the person who assigns work is
busy.

The point of the decisions list is to ask the human well. A pending decision needs a recommendation, so the human
answers yes or no. The decision-record template that `onloop init` writes includes a table of **decisions by size**:
small decisions stay with the role that owns them, medium ones go to the lead, and large ones, which are those in a
reserved class, go to the human once a day as a list.

`onloop ledger check` validates the file. Given `--base REF` it also checks that the changelog at that ref is still a
prefix of the current one, so history cannot be rewritten. `onloop ledger render` prints a Markdown status page, or
a minimal HTML page with `--html`.

## Watchdog

`onloop watchdog` reads the ledger and, unless `--no-github` is given, the open pull requests. It reports:

- a workstream or queue item that has stayed in one state longer than its limit in `sla`;
- a role whose queue has no `todo` or `doing` item;
- an open pull request that has waited too long for a reviewer verdict on its current head.

It prints one line per finding and exits 1 if there are any, so it can run on a schedule and fail loudly.

## Generated CODEOWNERS

`onloop codeowners --write` generates the file from the policy; `--check` (the default) fails if the committed file
differs, with a diff. GitHub applies the last matching line, so the generator orders lines from the shortest path to
the longest. A path reserved to a class the human approves is owned by `@<human>`. Any other path is owned by the
handle in `roles.<name>.codeowner`; a role without a handle writes no line. The file is itself a reserved path in the
starter policy, so hand-editing it is both caught by the check and needs the human.
