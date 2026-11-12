# onloop documentation

- [README](../README.md): what onloop is, why the human is on the loop, and a five-minute quickstart
- [Concepts](concepts.md): policy, reserved classes, classification, the gate, the audit, agent identity, the
  ledger and the watchdog
- [Architecture](architecture.md): the package's layers, the supported surface, the extension points and where state lives
- [Configuration reference](configuration.md): every field of `onloop.yaml` and `ledger.yaml` (the people's work queue), and every command
- [Slots, pre-flight and the KPIs](automation.md): events, slot, preflight, kpi, and the `slots:` and
  `automation:` policy blocks
- [Security model](security-model.md): what is trusted, what is bound to the head, and what is not protected
- [FAQ](faq.md)
- [The UI](ui.md): the five read-only views onloopd serves (Now, Flow, Team, Schedules and Audit), how it is built,
  served and tested
- [The supervisor](supervisor.md): the role table and the governed step runner: one bounded headless `claude -p` call per CI job (`onloop step`), the planner, the person's task queue, the digest and the attention rules
- Decisions: [0001](decisions/0001-scope-of-the-loop-without-waiting.md) the 0.3 plan, [0002](decisions/0002-shadow-run-terms.md) the shadow run's terms, [0003](decisions/0003-roles-run-as-ci-jobs.md) role steps run as CI jobs, [0004](decisions/0004-onloop-hosts-the-conversation.md) onloop hosts the conversation, [0005](decisions/0005-package-layout.md) the package layout, [0006](decisions/0006-overlay-ledger.md) the overlay ledger, [0007](decisions/0007-disposition-contract.md) the disposition contract, [0008](decisions/0008-resident-service-and-dead-man-switch.md) the resident service, [0009](decisions/0009-one-read-only-ui.md) the read-only UI
- [Minimal example](../examples/minimal/README.md): a toy configuration that validates
- [Changelog](../CHANGELOG.md)
- [Contributing](../CONTRIBUTING.md) and [Security policy](../SECURITY.md)
