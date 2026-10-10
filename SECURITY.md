# Security policy

## Reporting a vulnerability

Please report a vulnerability privately, not in a public issue or pull request.

Use GitHub's private vulnerability reporting for this repository: open the repository's **Security** tab and choose
**Report a vulnerability**. This creates a private advisory that only you and the maintainers can read. If the option
is not shown, open a public issue that says only that you have a security report and want a private channel. Do not
put details in it.

Please include:

- the version or commit you tested;
- what you expected and what happened;
- the smallest configuration and steps that reproduce it;
- what an attacker gains, and what they need to start (for example, the ability to open a pull request).

We aim to acknowledge a report within a few days and to say what we will do about it within two weeks. We will credit
you in the advisory and the changelog if you wish.

## What counts

onloop is a control that decides whether a pull request may merge. These are in scope and serious:

- a way to get a pull request merged without the approvals the policy asks for;
- a way to make the gate or the audit accept evidence that is not bound to the head commit;
- a policy change that loosens the policy but is not classed as authority widening;
- a way for `onloop as-app` to print, store or send a token, or to run a command its guards should refuse;
- a way to make a checker report success when its self-test would fail.

The [security model](docs/security-model.md) lists what onloop does not protect against, such as a compromised
account or a merge that bypasses the gate on purpose. Those are known limits, not vulnerabilities, though a sharper
statement of them is welcome as a documentation fix.

## Supported versions

Only the latest release receives security fixes.

## Handling secrets

Never include a private key, token or credential in a report, an issue, a test or a fixture. If you find one in the
repository's history, report it as a vulnerability.
