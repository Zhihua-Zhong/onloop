# The session carrier

onloop carries the role conversations itself ([decision 0010](decisions/0010-session-lease-and-delivery.md)). A
role is a Claude Code session id plus a lease. onloop writes messages into that session with headless
`claude -p --resume <id>`, on the user's own subscription login of the unmodified CLI, and records every step in the
ledger. **It is off by default**: until a role opts in and the carrier is started, onloop only records what it would
send.

## What it does

| Delivery kind | Opt-in | What onloop sends |
|---|---|---|
| `message` | `message` | A person's message, from `onloop say` or the Conversation view's compose box. It is a request from a local user, not verified, and never an approval ([decision 0011](decisions/0011-ui-write-path.md)) |
| `event` | `event` | A handoff or review request from another role (`outcome: handoff owner=<this role>`, `outcome: in_review owner=<this role>`), and the escalations sent to the coordinator |
| `corrective` | `corrective` | After a turn without a valid outcome line: one fixed sentence asking for it, at most once per turn (the ledger refuses a second), then, 10 min later, the coordinator with the item's age ([decision 0007](decisions/0007-disposition-contract.md)) |
| `dead_waker` | `dead_waker`, then `dead_waker_wake` | A `continue` whose waker is not alive: the coordinator is told first; the role itself only with the second opt-in |

## One run

1. The carrier picks a role with due deliveries, and checks: the kill switch, the carrier's state, the role's
   opt-in and pause, its budget, `claude auth status` (cached 5 min), and `claude agents --json`. A session open
   anywhere else is never resumed (`held_elsewhere`); after 30 min its deliveries go to you.
2. In one ledger transaction it takes the session's lease (the ledger refuses a second live lease on one session id),
   starts the run and claims the deliveries.
3. It starts `claude -p --resume <id>` in its own Windows job object, with `--restricted`, `--permission-mode
   dontAsk`, `--permission-prompts none`, the role's tools (never a shell), and a hooks file that posts the role's
   Stop hook to onloopd. The environment has no `CLAUDE*` or `ANTHROPIC*` variable.
4. It writes **one** composed message, every due delivery as a block led by its id, and records `delivered`. The
   replay of that message is `processing`. After the one result line it closes stdin, so the process exits.
5. A delivery is **acknowledged** by a Stop-hook turn on its session that ended after it was written, whatever the
   turn said. Whether the turn had a valid outcome line is recorded on the turn, apart; it never fails a delivery.
6. The lease is released, and the run's status recorded: `ok`, `refused` (a tool was refused: alert, not re-run),
   `error`, `timeout`, `silent`, `killed`, `usage_limited`, `not_logged_in`, `orphaned` or `contended` (another
   process held the session during the run: alert, and the role is paused).

## Stopping it

- **The kill switch.** `onloop carrier stop` writes `carrier.off` next to the ledger and logs the stop. Live runs end
  within 5 s, by their job objects. Writing the file by hand works too, even when onloopd is wedged. `onloop carrier
  start` removes it.
- **Per role.** `onloop role opt-out <role> <action>`, at once. Opting in cannot go past what onloopd.yaml lists.
- **Pause a role.** `onloop role pause <role>`: the carrier skips it.

## Spend

The carrier runs on the subscription only. It does not start with `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` in
onloopd's environment, and it never asks for, stores or passes a Claude.ai token. Every run's rate-limit event is
read:
- `rejected` (or an API status 429) pauses the carrier until the limit resets, with one alert and no probing;
- `allowed_warning` alerts and halves every role's runs per hour for an hour;
- `isUsingOverage: true` pauses the carrier at once.

Budgets: per role (runs per hour and day, wall time per day, max turns and wall time per run) and across the carrier
(wall time per day). `onloop carrier status` shows the last day's runs.

## Recovery

- **onloopd crashes mid-run.** The job object ends the run with it. At the next start the lease is released as
  `orphaned`, and each delivery is acknowledged if a Stop hook answered it, else resent with a mark the role can read.
  Delivery is at least once.
- **onloopd is being respawned** (43–53 s). A Stop hook that cannot reach onloopd is spooled to
  `hook-spool.jsonl`, and logged once when onloopd is back.
- **The machine sleeps.** Silence and wall limits use the monotonic clock. Overdue timers fire once after the wake,
  and a corrective for a turn older than an hour escalates instead.

## Configuration

See the `carrier:` block in [configuration.md](configuration.md#the-carrier). A minimal one:

```yaml
carrier:
  coordinator: lead
  roles:
    builder:
      session_id: 00000000-0000-4000-8000-000000000001
      cwd: C:/work/app
      tools: [Read, Grep, Glob, Edit, Write]
      opt_in: [message, corrective]
```

## What is stored

The text onloop writes into a session: your messages and its own notes, with secret-shaped strings replaced by
`[redacted]`, for 30 days (`message_retention`), then only their sha256. Nothing a role writes is stored: the
Conversation view shows each turn by its outcome line, and `claude --resume <id>` opens the full turn in your own
client. `claude agents --json` lists every Claude session on the machine; onloop keeps nothing of it but whether a
bound role session is held.
