# Decision 0010: the session lease and delivery

- **Status:** Accepted on 2026-11-10
- **Date:** 2026-11-10
- **Decided by:** the onloop lead, on the R4 plan and the spikes run on CLI 2.1.321
- **Size:** large (onloop starts to act: it writes into role sessions)

## Context

[Decision 0004](0004-onloop-hosts-the-conversation.md) makes onloop the host of the role conversations: a role is a
Claude Code session id, and onloop delivers messages into it. [Decision 0007](0007-disposition-contract.md) defines the
outcome line and its rollout: observe first, then an opt-in corrective wake, at most one per turn, then the
coordinator. [Decision 0008](0008-resident-service-and-dead-man-switch.md) makes onloopd the one resident service.

onloop drives a session with headless `claude -p --resume <id>` on the user's own subscription login. The spikes
measured these facts:

- **Two resumes of one session id fork it silently** (2.1.223 and again 2.1.321). Both runs exit 0, report the same
  session id, print no note, and the transcript branches. The refusal Claude Code's docs describe applies to its
  background supervisor, not to `-p`.
- **A process kept open after its turn forks too,** on its next turn, when anything else wrote to the session in the
  meantime.
- **`claude agents --json` lists every running session, `-p` processes included,** with pid, session id and
  `busy`/`idle`. It also lists the user's other sessions, with their folders and names.
- **Messages written into a running turn are merged into one** (one replay, one result, one Stop hook).
- **A replay of the user message acknowledges processing, not receipt.**
- **A refused tool exits 0 with `is_error: false`.** Only `permission_denials` shows it. The Stop hook still fires.
- **Without a Windows job, a child that is mid-turn outlives its killed parent** and finishes its turn. A job object
  with KILL_ON_JOB_CLOSE ends the whole tree within 0.5 s.

## Decision

1. **One live lease per session id, enforced by the ledger.** `session_leases` has a unique index on `session_id`
   where `released_at IS NULL`. Taking a lease, starting the run and claiming its deliveries is one transaction; a
   second carrier's insert fails, and it spawns nothing. The lease records the `claude.exe` pid and its creation time,
   so a reused pid is never mistaken for the holder.
2. **A session held outside onloop is never resumed.** Before every spawn, and again when the run ends, onloop reads
   `claude agents --json` and keeps only the rows whose session id is a bound role session (one function,
   `carrier.listing.held`). Nothing else from the listing is logged, stored or returned, not even a count. A held
   session waits (`held_elsewhere`). A second pid seen at the end of a run marks it `contended` and pauses the role,
   because the transcript may have forked. **Known limit:** the seconds between the check and the spawn are not
   covered; nothing in the CLI closes them.
3. **One process per turn, one composed message per run.** All due deliveries for a role go into one user message,
   each block led by its delivery id. onloop writes it once, waits for one `result`, then closes stdin. It never
   writes into a live run and never keeps a process open across turns.
4. **Deliveries are a transactional outbox** in the ledger: `created → claimed → delivered → processing → acked`,
   with `failed`, `escalated` and `cancelled`.
   - `delivered_at` is the stdin write; `processing_at` is the replay of the composed message (matched by its hash).
   - **`acked_at` is a Stop-hook turn on that session that ended after `delivered_at`, whatever the turn said.**
     If no hook arrives within 30 s of the result line, the result line acks it and the run is marked
     `hook_missing`.
   - **The outcome is separate.** Whether the turn wrote a valid outcome line is a field of the turn (the existing
     `turns.parse_error`), read by decision 0007's detector. A missing outcome never fails a delivery and never counts
     as an attempt. It starts the corrective path only for a role that opted in.
   - A run that ends with no Stop-hook turn and no result (killed, crashed) fails its deliveries as `orphaned`. They
     are retried with a `resent_after_interruption` mark the role can read.
   - An acked delivery is never re-run automatically, refused or not.
   - Delivery is therefore **at least once, with dedupe by evidence**.
5. **The corrective wake is at most one per turn,** by a unique index on `deliveries(for_turn_id)` for kind
   `corrective`. Its text is fixed. After it, the item goes to the coordinator with its age.
6. **Timers are a ledger projection** evaluated on onloopd's tick. They survive a crash because they are rows.
7. **Recovery.** At boot, before the carrier runs, every live lease is checked. A holder still alive (only possible
   when the job could not be made) is waited for, then stopped. A dead holder's lease is released as `orphaned`, and its
   deliveries are acked or resent by the evidence above.
8. **The hook spool.** When onloopd is down (a respawn takes 43–53 s), `onloop daemon hook` appends the hook's post to
   `hook-spool.jsonl` next to the ledger. onloopd ingests it at boot, under the hook's own event id, so a duplicate is
   dropped.
9. **Sleep.** Leases are never expired while their holder is alive. Silence and wall limits use the monotonic clock.
   After a wall-clock jump, overdue timers fire once, and no corrective wake goes to a turn older than one hour; it
   escalates instead.
10. **Message text.** onloop stores the text it writes into a session (the human's messages, its own notes), scrubbed
    of secret-shaped strings, for 30 days, then keeps only the hash. It never stores what a role writes.
11. **Act modes are off by default.** Without an opt-in, the carrier only logs `would.deliver` with the exact command.
12. **No shell for carried roles.** A role whose tools name Bash, PowerShell or any other shell is refused: with a shell
    named, read-only commands run without the allowlist even under `--restricted`. A future decision will define a safe
    shell.

## Alternatives considered

| Option | Verdict | Why |
|---|---|---|
| Claude Code background sessions (`--bg`, the supervisor) | Not now | A research preview; stops on reboot; stops idle sessions; a second supervisor beside onloopd. Its listing is used as a detector |
| The Agent SDK | Rejected | The same binary underneath, an added dependency, and its product terms point at an API key |
| A job queue (Celery, RQ, Huey) | Rejected | A second store that cannot see the ledger's leases and waits in one transaction |
| Durable timers from Temporal or DBOS | Rejected | A second service or store for a handful of timers |
| Exactly-once delivery | Rejected | It would need the role's transcript, which onloop does not read |

## Consequences

- Ledger schema 6: the `carrier` source; `role_sessions`, `session_leases`, `runs`, `messages`, `timers`,
  `carrier_state`; `deliveries` widened; the unique indexes above.
- `onloop carrier start|stop|status`, `onloop say`, a `carrier.off` kill-switch file, and a `carrier:` block in
  onloopd.yaml, off by default.
- **Open: Q1, the subscription-use policy.** Whether a carrier that drives several roles around the clock is
  "ordinary, individual usage" of a subscription is open with the project lead, deferred to the end. It is a gate:
  - before the pilot's rollout step 3 (the first act beyond observe-only), no customer pilot goes past step 2;
  - before the 0.4.0 release, which ships the carrier.
