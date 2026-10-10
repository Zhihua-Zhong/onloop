# FAQ

## Do I need to run a server?

No. onloop is a command-line tool and a set of GitHub Actions workflows. The gate is a command that someone or
something runs (`onloop gate N`), usually the operator role on a schedule or when a pull request has its verdict.
There is no daemon and no database. An event-driven dispatcher is on the roadmap, not in 0.1.0.

## Does it replace branch protection and rulesets?

No. Use a ruleset to block direct pushes to `main`, require pull requests, require the `policy` check, and restrict
who may bypass. onloop adds the policy, the per-class approvers, the head binding, the self-protecting policy check
and the audit. If a ruleset stops a direct push, the audit has nothing to report. The audit exists for the day
something got through. See [the README](../README.md#how-it-relates-to-rulesets-and-codeowners).

## Why generate CODEOWNERS instead of writing it?

Two files that describe the same ownership drift apart. The policy is the source of truth and `onloop codeowners
--check` fails when CODEOWNERS differs from it, so drift is a red check, not a surprise. Edit `onloop.yaml`, then run
`onloop codeowners --write`.

## Why are there two Apps, and why not the human's own token for the agents?

If agents act as the human, the human's approval cannot be told from an agent's, and "a human reviewed it" is no
longer true. The `agents` App authors and merges. The `reviewer` App gives the verdict and can do nothing else. The
`human` is a person. The gate checks the account type of each approval, not just the name.

## Can one App be both the agents and the reviewer?

`policy check` rejects it. If the author and the reviewer are the same identity, the review is not independent.

## Why does the pull request title need `[builder]`?

The gate must know which role is authoring, so it can check that the role owns the paths it changes. It reads a
`[role]` prefix in the title, else the last `Role:` line in the commit messages. A pull request that touches paths
owned by another role must say so with `Cross-owner: <role> - <reason>` in its body or a commit message. This makes
reaching across ownership a visible choice.

## A reviewer approved, then the builder pushed again. Why was the merge refused?

The approval was bound to the old head. Reviews and checks on any other commit count for nothing. Ask the reviewer to
review the new head. This is deliberate: it is what stops a reviewed change from being swapped for an unreviewed one.

## What does `authority_widening` mean for my own policy edits?

Changing `onloop.yaml` is a change to a path reserved to `authority_widening` in the starter policy, so it needs the
human anyway. The loosening rule goes further: if the edit relaxes the policy, it needs the human even if you have set
`authority_widening` to `reviewer` in the same edit. See [concepts](concepts.md#base-against-head).

## Can I let the reviewer approve a class alone?

Yes. Add a class under `classes:` with the approver `reviewer`, or move a built-in class there, and assign paths to
it. Adding a new class is not a loosening, but moving a path to it from a class the human approves, or moving a
built-in class from `human` to `reviewer`, is, and the pull request that does it needs the human.

## Is a glob like `docs/**/*.md` supported in `paths`?

No. A key is an exact file or a directory prefix ending in `/`. The longest match wins. Glob support may come later;
it would have to be matched exactly by the generated CODEOWNERS lines, which use GitHub's own pattern rules.

## Is the tier enforced?

The classifier reports the highest tier among the paths a change touches, and role charters can use it. The gate does
not add conditions by tier in 0.1.0. What forces the human is a reserved class.

## The gate says a required check is missing, but the workflow is green.

`required_checks` lists GitHub check-run names. For a workflow, that is the job name (`policy` in the starter gate
workflow), not the workflow's name. Look at the pull request's checks list for the exact spelling.

## What do the `checks` in `onloop.yaml` do compared with `required_checks`?

`required_checks` names checks that GitHub Actions or another service has already run on the head; the gate waits for
them to be green. `checks` are commands the gate runs itself, in a clean worktree of the head, before it merges. Use
`required_checks` for slow or sandboxed checks and `checks` for quick ones you want to run in the gate's environment.

## What if the gate merged something and then the post-merge check failed?

The gate reverts the merge: a new commit on the base branch that restores the earlier tree and carries a
`Gate-Revert: #N <sha>` line. It exits 2. The audit accepts that commit only if it reverts a merge the audit already
accepted and the tree equals the tree before it.

## The audit alarms on a squash merge.

The audit expects each merge on the main line to have two parents, with the pull request's head as the second. A
squash or rebase merge has one parent, so it looks like a direct push. Allow merge commits only in the repository
settings.

## Where does the audit start?

At `--since` if you give one, else `audit.since` in the policy, else the commit that first added `onloop.yaml`.
History before that is not judged.

## What does the watchdog do that I can't see in the pull request list?

It reads the ledger. It reports a workstream stuck in one state, a role with an empty queue (nothing for it to pull),
and open pull requests that have waited too long for a verdict. Agents rarely report that they are idle; the watchdog
makes it somebody's problem.

## Why a ledger file and not an issue tracker?

The ledger is reviewed in a pull request like any other file, it is versioned with the code, and its changelog is
append-only and checked. Nothing stops you from also using an issue tracker. The ledger holds what the team and the
human need to agree on, and keeps the queues in the repository where the agents already work.

## How do I add a project-specific check?

For a command, put it under `checks:` in `onloop.yaml`. For logic that needs the pull request facts or the
classification, register a callable in the `onloop.checks` entry-point group. It receives `(facts, policy,
classification)` and returns a list of refusal sentences.

## Does onloop work with GitHub Enterprise or other hosts?

It shells out to the `gh` CLI, so it works wherever `gh` is configured for the host. The App token exchange in
`as-app` targets github.com. Other hosts are not tested in 0.1.0.

## Does it send anything anywhere?

onloop itself talks only to GitHub, through `gh` and, for `as-app`, one call to exchange the App's token. It has no
telemetry.

## How do I know the checkers work?

Run `onloop selftest`. Each checker (the classifier, the gate, the audit, the identity guard, CODEOWNERS, the ledger,
the watchdog) is given cases it must catch, such as a loosened policy, a stale-head approval, a merge by the wrong
identity or a rewritten changelog, and the command fails if any is let through. Run it in CI. If you write a checker
of your own, give it a self-test that proves it bites.

## Which Python versions work?

3.11 and later. The only run-time dependency is PyYAML. `as-app` additionally needs cryptography, installed
with `pip install 'onloop[app]'`.

## How is the App's JWT signed, and why not use a JWT library?

`onloop as-app` signs the App's JWT as RS256 with the `cryptography` package directly. GitHub's requirements for an
App JWT are small: the algorithm must be RS256, the claims are `iat` (issued at, set a minute in the past to allow for
clock drift), `exp` (at most ten minutes after `iat`; onloop uses nine) and `iss` (the App's ID), and the private key is
the PEM file the App's settings page gives you. That is three base64url parts and one RSA PKCS#1 v1.5 signature, so a
JWT library would add a dependency and an import chain without adding anything the signature needs. The test verifies
the signature with the matching public key. The token that comes back is limited to the one repository the command
runs against.

## Windows: the installation fails with an import error about an "unknown location"

Enable long paths, or keep the virtual environment's path short. Windows stops at 260 characters in a path by default,
and some packages (`cryptography` among them) have deep directory trees. When a file lands past the limit, Python finds
an empty directory and reports a namespace package with an "unknown location". Create the environment close to the
drive root, for example `C:/venvs/onloop`, or turn on long paths in Windows and in git.
