# Security model

onloop is a control for a particular failure: a team of agents that approves its own work, widens its own
authority, or acts under a person's account. This page says what the design trusts, what it binds, what it checks,
and, as plainly, what it does not protect against.

## What is trusted

| Trusted | Why it has to be |
|---|---|
| GitHub itself: its API, its review and check records, its Apps | onloop reads every fact from it. |
| The base branch's `onloop.yaml` | The gate and the audit judge by it. It is protected by its own reserved class. |
| The human's account and the credentials of the two Apps | Approval means something only if these are held by who they should be. |
| The machine that runs `onloop gate` | It has write access through the agents App and executes the project's checks. |
| The `onloop` package and any `onloop.checks` plugin installed there | They decide whether a merge happens. |

Not trusted: the contents of a pull request, its title and body (they are inputs, validated), its head policy, its
checks as reported by the author, and any review or check that is not bound to the head.

## What the gate binds to the head SHA

The gate reads the head commit once and judges only evidence attached to that commit:

- an approving review counts only if its `commit_id` is the head;
- a request for changes counts only if it is on the head;
- a check run counts only if it was read for the head;
- the merge itself is `--match-head-commit <head>`, so a push after the decision makes GitHub refuse the merge.

A review of an earlier commit, a green check on another commit, or an approval given before a force-push proves
nothing about what would be merged. That is what makes "approved" mean "approved this".

The diff is the change's own: the paths come from `git diff --name-only --no-renames base...head` in the checked-out
repository, not from a list the host reports. Renames are split into a delete and an add so that moving a file out of
a reserved directory is still seen.

## Why the policy protects itself

The policy decides who must approve. If a pull request could edit it without extra approval, one pull request could
remove the requirement that would stop the next. Three rules prevent this.

1. **The policy must reserve itself.** `policy check` fails unless `onloop.yaml` is a path with a reserved class.
2. **The base decides.** The gate and the audit read the policy from the base side (the branch being merged into, or
   the merge's first parent), never from the head being judged. A pull request is judged by the rules in force
   before it.
3. **The loosening rule.** When a pull request changes `onloop.yaml`, the classifier compares the two policies. Any
   relaxation is classed as `authority_widening` and requires the human account, regardless of which approver the head
   policy gives that class. A head policy that cannot be read or validated counts as a relaxation. The relaxations are
   listed under [concepts](concepts.md#base-against-head): changing an identity, removing a class or moving it from
   human to reviewer, removing or lowering a path's reservation or tier, and dropping a check.

The comparison covers what it lists. It is not a proof that no other edit can weaken the policy, so keep
`onloop.yaml` reserved at a high tier and have the reviewer state, in its verdict, what a policy change loosens. The
reviewer role charter in the starter files says to do so.

## Why a Bot-typed identity check matters

A login is a name. On GitHub, any person can register a user account whose login equals the name written in your
policy. GitHub, however, sets the account type itself: a GitHub App's actor is of type `Bot`, and a person's is `User`.

So the gate accepts the reviewer's approval only from an actor of type `Bot` whose login is `reviewer`, and the
human's approval only from an actor of type `User` whose login is `human`. An approval from a user account that merely
shares the reviewer's login is ignored, and so is an App posing as the human. The audit applies the same test to who
performed the merge: the `agents` identity must be a `Bot`, and the human must be a `User`.

## Agent identity (`as-app`) and its guards

The goal is separation: no agent holds the human's account, and the reviewer is not the author. `onloop as-app` runs
one `git` or `gh` command as an App installation (the `agents` identity may also run `onloop gate`, `onloop audit` and
`onloop watchdog`, so the merge itself is made by the App):

1. It reads the key file named by the environment variable in `apps.<identity>.key_env`.
2. It refuses a key that sits inside a git work tree, so the key cannot be committed by accident.
3. It signs a short-lived JWT (RS256, valid for under ten minutes) and exchanges it for an installation access token limited
   to the one repository.
4. It runs the command with the token in that command's environment only (`GH_TOKEN` and `GITHUB_TOKEN`), with the
   App's bot name as author and committer, and with git's credential helpers cleared and replaced by one that reads the
   token at run time. Nothing is written to disk and the token is on no command line.

The guards refuse, before anything runs:

- any program other than `git`, `gh` and, for the `agents` identity only, `onloop gate`, `onloop audit` and
  `onloop watchdog`;
- `gh auth token`, `gh auth status --show-token`, and any other `gh auth` command;
- `git credential`;
- the git options `-c`, `--config-env` and `--exec-path`, which could swap the credential helper or the transport;
- for the reviewer identity, everything except `gh pr review` and `gh pr comment`, and the flags `--edit-last` and
  `--delete-last`, so a reviewer cannot rewrite what it said.

### Stated limits

The guards stop accidents, not malice. In particular:

- A command that has been handed a token can use it. `gh api`, a git hook, a `gh` alias or extension, or a program
  spawned by `git` runs with the token in its environment and can do what the App's permissions allow, including
  printing the token.
- The `agents` identity may run any `gh` or `git` subcommand not listed above. Limit what it can do with the App's
  permissions and with the repository ruleset, not with this wrapper.
- **Merging is guarded only at the front door.** The wrapper refuses a top-level `gh pr merge` for the `agents`
  identity, so the ordinary way to merge is `onloop gate`, which makes its own child merge once every condition holds
  on the head. It cannot stop the same App from merging through `gh api`, a script, or a token it was handed. What
  does stop that is GitHub: enable "Require review from Code Owners" (and "Dismiss stale approvals") in the ruleset
  on `main`. Because CODEOWNERS is generated from the policy, a reserved path then needs the human's own review
  however the merge is attempted, and the audit alarms on whatever still gets through.
- The `-c` refusal also refuses legitimate uses of an option spelled `-c`, such as `git commit -c <commit>`. That
  errs on the safe side.
- The guards do not stop a process that reads the key file directly. Any process running as the same operating-system
  user that can read the file can mint tokens.

## Key handling

- Store each App's private key outside every git work tree. `as-app` enforces this itself and refuses a key inside
  one. Also add `*.pem` and `*.key` to your `.gitignore`; `onloop init` does not write one.
- Give the reviewer's key to a place the authoring agents cannot read: a different operating-system user, a different
  machine, or a secrets manager that releases it only to the reviewer's process. If the builder can read the
  reviewer's key it can approve its own pull requests, and the separation is gone.
- Keep the human's credentials where no agent process runs.
- Rotate a key by generating a new one in the App's settings and deleting the old. An App ID and installation ID are
  not secrets.
- `apps:` holds the name of the environment variable, never the path or the key.

## What onloop does not protect against

- **A compromised human account, App key, or operator machine.** Whoever holds them can approve and merge.
- **Merges that bypass the gate.** A person with admin rights, a ruleset bypass, or a direct push can put a commit on
  `main`. The audit alarms on it afterwards; it does not prevent it. Use rulesets to prevent it.
- **A change inside an unreserved path that is harmful.** The reviewer decides routine changes. onloop makes the
  review independent and bound to the head; it does not make it correct.
- **A path assigned to the wrong class.** Classes are assigned by path. onloop does not read a file to decide that it
  is destructive or that it is a claim.
- **Running untrusted code.** The gate runs the project's `checks` (for example the test suite) on the pull request's
  head, on the operator's machine, through a shell. That is the head's code executing with whatever the machine and
  its environment can reach. Run the gate on a machine and an account with no secrets beyond what the gate needs, or
  in a disposable container. The commands themselves come from the base policy, not the head's, so a pull request
  cannot change what is run, but it can change what that command runs.
- **A malicious plugin or `post_merge_checks` command.** Both execute with the gate's permissions.
- **Secrets in the repository.** onloop does not scan for them.
- **Content.** It does not judge the quality or meaning of a change.
- **Squash or rebase merges.** The audit treats a commit with fewer than two parents on the main line as a direct
  push. Allow merge commits only.
- **Time.** An approval is bound to a commit, not to a moment; there is no expiry.
- **Availability.** A stopped gate, a missing check or an unreachable API makes the gate refuse, not pass; but nothing
  restarts it.

## Failure behaviour

The gate and the audit fail closed:

- a missing policy, an unreadable policy, a missing required check, a missing authoring role, a plugin that raises, or
  a project check that cannot start each produce a refusal;
- an unknown reserved class is read as needing the human;
- an unknown tier is read as the highest.

Every checker carries a self-test that proves it still bites, and `onloop selftest` runs them all. A checker that
quietly narrows is worse than one that fails, so the project's CI runs the self-tests on every change, and you should
run them in your own CI as the starter gate workflow does.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
