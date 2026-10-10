# Minimal example

A toy configuration for a small repository: three identities, four roles, one class of our own, and a ledger. It
contains no source code. Its job is to show what the three files look like when they are filled in and consistent
with each other.

| File | What it is |
|---|---|
| `onloop.yaml` | The policy. The single source of truth for who may change what and who must approve it. |
| `CODEOWNERS` | Generated from the policy by `onloop codeowners --write`. Never edited by hand. |
| `ledger.yaml` | Focus, workstreams, a pending decision, one queue per role, and an append-only changelog. |

All commands run from the root of the onloop repository. Adjust the path to `onloop.yaml` if you copy the example
into a repository of your own, where the defaults (`onloop.yaml`, `CODEOWNERS`, `ledger.yaml` in the root) apply and
the `--policy` and `--file` flags are not needed.

## Validate it

```bash
onloop policy check --policy examples/minimal/onloop.yaml
onloop codeowners --policy examples/minimal/onloop.yaml --file examples/minimal/CODEOWNERS --check
onloop ledger check --file examples/minimal/ledger.yaml --policy examples/minimal/onloop.yaml
```

The first prints `policy ok: 10 paths, 4 roles`. The second prints `CODEOWNERS matches the policy`. The third prints
`ledger ok`.

## Read the policy

- `human`, `reviewer` and `agents` are three different GitHub accounts. The logins here are placeholders.
- Two roles declare a `codeowner` handle. CODEOWNERS lists that handle for the role's paths. A role with no handle
  (`operator`, `reviewer`) writes no line.
- `classes: {copy_edits: reviewer}` adds a reserved class that the reviewer approves alone. The six built-in
  classes (`claims`, `safety_widening`, `authority_widening`, `destructive`, `money_accounts`, `leaves_project`)
  always exist and the human approves each of them.
- `docs/claims/` is reserved to `claims`, so CODEOWNERS names the human for it. `docs/copy/` is reserved to
  `copy_edits`, so it names the owning role's handle instead.
- The policy file itself, `.github/`, `CODEOWNERS` and `docs/charter/` are reserved to `authority_widening` at tier
  `full`. Changing any of them needs the human.
- `checks` has one project check. The gate runs `python -m pytest -q` in a clean checkout of the pull request head.
  `required_checks` lists `policy`, the GitHub check that the starter gate workflow produces.

## Try the classifier

```bash
onloop classify --policy examples/minimal/onloop.yaml --paths src/app.py
onloop classify --policy examples/minimal/onloop.yaml --paths docs/copy/intro.md
onloop classify --policy examples/minimal/onloop.yaml --paths docs/claims/results.md src/app.py
```

The first needs the reviewer only. The second also needs the reviewer only, because its class names the reviewer as
approver. The third needs the human and the reviewer, because one of the two paths is in a class the human approves.

To see the policy-loosening rule act, copy `onloop.yaml` to a scratch file, remove the `reserved: claims` entry from
`docs/claims/`, and run:

```bash
onloop classify --policy examples/minimal/onloop.yaml --head-policy scratch.yaml --paths onloop.yaml
```

The output lists the relaxation and asks for the human, whatever the scratch policy says about itself.

## Read the ledger

```bash
onloop ledger render --file examples/minimal/ledger.yaml --policy examples/minimal/onloop.yaml
onloop ledger next --file examples/minimal/ledger.yaml --policy examples/minimal/onloop.yaml --role builder
```

The builder is already doing `Q4`, so that is what `next` returns. Ask for the `lead` and it returns `Q1`, the first
item in the lead's queue that is `todo`.

## Regenerate CODEOWNERS

After editing `onloop.yaml`:

```bash
onloop codeowners --policy examples/minimal/onloop.yaml --file examples/minimal/CODEOWNERS --write
```
