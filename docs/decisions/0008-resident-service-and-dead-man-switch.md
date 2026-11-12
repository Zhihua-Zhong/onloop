# Decision 0008: one resident service, with a dead-man switch outside it

- **Status:** Accepted on 2026-11-09 for the service itself (`onloopd`). The two operating parts, Task Scheduler and
  Healthchecks, are **partly verified** by their spike (2026-11-09): Healthchecks, its pings, its webhook and the
  watchdog work; Task Scheduler works only with the trigger below; sleep, reboot and logon are **still untested**.
- **Date:** 2026-11-09
- **Decided by:** the project lead, approving the technical design of 2026-11-09
- **Size:** large (it adds a long-running process and the way it is kept alive)

## Context

[Decision 0004](0004-onloop-hosts-the-conversation.md) makes onloop the loop owner: something that runs outside any
agent session and stays up, holds goals and a clock, and delivers events into role sessions. Today onloop is a set
of commands that run and exit (`onloop step` in a CI job, `onloop run` as a foreground loop), so nothing watches the
team when no command is running, and nothing notices when onloop itself has stopped.

The session spike of 2026-11-09 settled what the service must do around each role session: hold a lease per session
id (two concurrent resumes fork the session silently), scrub the inherited `CLAUDE*` and `ANTHROPIC*` variables, check
the login first, and read `is_error`, `terminal_reason` and `permission_denials` from every result line. Those belong
to one long-lived owner, not to many short commands that cannot see each other.

## Decision

1. **One resident service, `onloopd`.** A single Python process per host that holds the ledger
   ([decision 0006](0006-overlay-ledger.md)), ingests events, runs the detectors, conducts deliveries, carries the
   role sessions and serves the API and the UI. It binds **the loopback address only** and refuses to start on any other address.
   It answers a request only when its Host names it as the loopback address or as `localhost`, with its port;
   `localhost` is accepted because WebAuthn refuses an IP literal as the relying-party id, so a page that uses passkeys
   is served as `http://localhost:<port>`. Every other Host is refused (no DNS rebinding).
   Its port is **8790** by default (so a later passkey page is `http://localhost:8790`), overridden by
   `onloop daemon run --port N` or `port: N` in `onloopd.yaml`. A port that another program holds is an error that
   names the port and the override; onloopd never falls back to another port, because the hooks and the scheduled
   jobs post to the one it was given. On Windows it does not set `SO_REUSEADDR`, which there would let two programs
   share a port.
   It speaks REST and Server-Sent Events. It is stopped by its own pid, and stopping it loses nothing: all state is in
   the ledger.
2. **Kept alive by the host's own scheduler** *(partly verified)*. On Windows, one Task Scheduler task, registered
   as the user (not an administrator), with **two triggers: at logon, and every 1 minute indefinitely**, and the
   settings `MultipleInstances=IgnoreNew`, `ExecutionTimeLimit PT0S` and `StartWhenAvailable`. Its action runs
   `onloopd` directly. While onloopd runs, the minute trigger finds it running and does nothing; when it has exited,
   the next minute starts it again (measured respawn 43 to 53 s). Task Scheduler's own "restart on failure" does
   **not** restart a process that exits with an error code: it covers only a failure to launch, so it is not relied
   on. A second task, every 5 minutes, is scoped to **hangs**: when onloopd is running but its heartbeat is stale, it
   ends and runs the task again, writes an alert and pings the dead-man check's `/fail` with the reason (measured
   recovery 114 s). Registering these tasks is a later step's code, not this decision's.
3. **A dead-man switch outside it** *(partly verified)*. A self-hosted Healthchecks instance receives a ping from
   `onloopd` every minute and the scheduled jobs' own pings. It runs as the official Docker image, published on
   port 8000 of the loopback address only, with `--restart unless-stopped`, `REGISTRATION_OPEN=False` and
   `INTEGRATIONS_ALLOW_PRIVATE_IPS=True`, and its webhook goes to
   `http://host.docker.internal:8790/sources/healthchecks`. It therefore depends on Docker Desktop starting at
   logon. When a ping is late or a job reports failure, Healthchecks calls `onloopd`'s webhook (measured 1 to 3 s
   after the deadline). If `onloopd` itself stops pinging, the webhook cannot help, so the dead-man check needs a
   **second notification channel that does not pass through onloopd** (mail, a push service, a chat webhook: the
   team's choice); without it "both gone" is detected and nobody is told.

   What the webhook carries is read with care: onloopd stamps each result with its own receive time; Healthchecks'
   `$NOW` is the check's **due time**, not the time it sent, and is kept as such. After a wake or an outage
   Healthchecks and the jobs catch up in bursts, so a result that repeats the schedule's last status within a short
   window is coalesced, not logged again. Healthchecks keeps the **head** of a ping body (10,000 bytes), so the job
   convention is to send the **last** 10,000 bytes of the log as the body; onloopd keeps the same 10,000 bytes.
   onloopd's side is built behind one schedule-source interface: the webhook (`POST /sources/healthchecks`) and,
   for a job that can run `curl` without Healthchecks, onloop's own ping endpoints in the same shape
   (`POST /sources/schedules/<id>/ping|fail|start`). The schedules are declared in `onloopd.yaml`.
4. **Writes come only from the loopback address, with a token, and are evidence, not authority.** onloopd takes
   POST only on its write routes (`/hooks/...` for the role sessions' hooks, `/sources/...` for schedule results),
   with `Authorization: Bearer <token>`. The token is drawn at every start and written next to the ledger
   (`onloopd.token`); onloopd warns, and does not refuse to start, if the file is readable by anyone but the user and
   the system. **The token is not a security boundary against the agents**: they run as the same OS user, so they
   can read it and post as a hook would. It keeps out other programs and web pages. What arrives on a write route is
   therefore a record of what a session or a scheduler said, never an authority to act, and any authority (an
   approval) needs a factor the agents cannot drive. Every event gets onloopd's own time; a time the sender claims is
   kept apart and never used as the event's time.
5. **What stays out.** Nothing binds to another interface, nothing is installed machine-wide, and no administrator
   rights are needed. A failure of the whole machine is not detected by anything on it; a dead-man switch off the
   machine is the customer's choice.

## Consequences

- The first refactor step builds the process skeleton only: the loopback-only HTTP server with health and read-only
  endpoints over the ledger, and the existing poll loop wired in as an ingest source. The session carrier, the hook
  receiver and the detectors come in later steps.
- The spike verified Healthchecks, the webhook, the watchdog and the one-minute trigger. **Sleep, reboot and the
  logon trigger are untested**: they need one supervised logoff and logon, and one sleep and resume, by the project
  lead. Until the tasks are registered (a later step), `onloopd` is started by hand or by `onloop daemon`, and a
  stopped `onloopd` is noticed only by a person.
- One process means one writer to the ledger, which keeps SQLite's single-writer model simple.
