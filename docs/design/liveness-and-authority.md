# Liveness and authority outside the agents

Status: design note, draft 1, 2026-10-29. Nothing is built, installed or enabled. Source: the project lead's requirement of 2026-10-29, relayed by the Lead:
one ordinary session used as a hub that prods the other workers is not reliable.

## The problem, in the three ways a worker stops that a hub cannot fix

| # | A worker stops because | The hub's nudge | Why it fails |
|---|---|---|---|
| a | it is blocked on a permission prompt | a message queues behind the prompt | only a human clears a prompt |
| b | it refuses an instruction that is not the project lead's own words | the nudge restates the instruction | the refusal is correct; relaying cannot make it authoritative |
| c | it has no clock | the hub must remember to wake it | the hub has no durable clock either: session crons are lost on restart and expire after 7 days |

Evidence (2026-10-29): the data engineer sat idle about 27 h. `list_sessions` reports only `isRunning`, so it cannot tell idle-with-queued-work from
blocked-on-a-prompt from refused. The per-role heartbeats set up around 10-10 have probably all lapsed (not re-checked here).

## Requirement 1: liveness is a record outside the agents

**State machine, one record per session** (`<liveness_dir>/<session_id>.json`, written atomically by tmp file plus rename, so a reader never sees half a file):

| state | meaning | how it is known |
|---|---|---|
| `running` | a turn is in progress | `UserPromptSubmit` hook sets it; `PreToolUse`/`PostToolUse` refresh `last_activity` |
| `idle` | the turn ended | `Stop` hook sets it |
| `blocked_on_prompt` | waiting for a human on a permission prompt | `Notification` hook with `notification_type = permission_prompt` sets it, with the tool name and the time; the next `PostToolUse` or `UserPromptSubmit` clears it |
| `refused` | declined an instruction, with a reason | **not observable by any hook** (see limits) |
| `gone` | no event and no running process for longer than a bound | the reader derives it; no writer |

The record carries `session_id`, `role`, `state`, `since`, `last_activity`, `tool` and `prompt_age` for a block, and `reason` for a refusal. It holds no
transcript text.

**Derived, never stored (discipline 5):** *idle with queued work* is `state == idle` AND the role has an open item in its queue (`ledger.yaml` `queue:`).
It is computed by the reader from two files on every run. Nobody types it.

**What is feasible today with Claude Code primitives (to verify on the installed version before building; I have not run these hooks here):**

- Hook events `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd` and `Notification` exist. A `Notification` hook
  receives JSON on stdin with the session id, transcript path, working directory, the message and the notification type; the types include a
  permission prompt and an idle prompt. The hook command is plain: `onloop liveness record --event <name>` reading stdin. Deterministic, no model, no tokens.
- Installing hooks is a change to the project lead's settings (user level, so it reaches every session). `onloop hooks --role R` already follows the rule
  "print it, the person who owns the settings pastes it". The liveness block follows the same rule: `onloop hooks --liveness` prints it and writes no file.
- **Limit 1, session id.** The hook's session id is the CLI session id. The desktop app's `local_...` id (what `SendMessage` and `ListAgents` use) is a
  different string. Open point: confirm that `list_sessions` exposes the CLI id or the transcript path so the two can be joined; if it does not, the join
  is the working directory plus the session title.
- **Limit 2, role.** A hook does not know which role the session plays. Proposal: the role's launch prompt ends with one line, `onloop liveness register
  --role <name>`, which writes `role` into the record. A session that never registered shows as `role: unknown` and is a finding itself.
- **Limit 3, refusals.** No hook fires when a model declines. Two routes, both partial: (i) the role records its own refusal with `onloop liveness refuse
  --reason ...` (a convention the refusal itself must follow, so it can be missed); (ii) a reader scans the tail of the transcript for a marker the
  role is told to emit. Say honestly: `refused` is best-effort, `blocked_on_prompt` and `idle` are exact.
- **Limit 4, permission prompts cannot be cleared by any agent.** Detection is only half of (a). The other half is fewer prompts: an allowlist for the
  commands the roles repeat (the `fewer-permission-prompts` skill produces one from the transcripts). That too is a settings change for the project lead.
  Sessions started in bypass mode do not prompt; whether that is acceptable for a role is the project lead's call, not mine.

## Requirement 2: authority is routed to the project lead's own turn, not verified inside an agent

*Revised 2026-10-29. The first draft had each role verify a pointer to the project lead's turn with a reader of the transcript. That is withdrawn.*

**What happened.** The Data engineer refused to act on the project lead's own turn when it read it from another session's transcript. The refusal is
correct: anything an agent reads through a tool is observed content, not an instruction, even when the words are the project lead's. A verifier inside
the agent cannot turn observed text into authority, the harness does not allow it, and no design should try. The same is expected of a decision-panel row.
(Decision 0028's wording, "the Operator acts on a go only after reading the project lead's own turn in the Lead session's transcript", describes a route
of this kind; whether it needs amending is for the project lead, and this note only flags it.)

**So authority has one channel: the project lead's own turn in the session that must act.** onloop's job is therefore routing, not verification:
get the exact words to the right window, and notice whether they arrived.

1. **The ask card names the window.** It carries the target session's title and `local_` id, a deep link to that window
   (`claude://claude.ai/epitaxy/<local_id>`, the `link` that `get_session` returns), and a **paste-ready line**: self-contained, saying what is
   authorised, against which hash or sha, and once. The scope stays in the words.
2. **The card carries the line's hash** (sha256 of the line with surrounding whitespace removed).
3. **Liveness records whether the line landed.** The recorder keeps, per session, `last_prompt` as a hash and a length (never the text). The card's check
   is: *the record for the target `host_session_id` shows a `UserPromptSubmit` whose `last_prompt.sha256` equals the card's hash.* Three outcomes:
   landed in the target; **landed in another session** (a record elsewhere has that hash: say which, this is the failure of 2026-10-29, where the line went
   to the Product Engineer instead of the Data engineer and nothing noticed for an hour); not landed after a set time (remind the project lead through the
   Lead; the reminder repeats the window and the line).
4. **The role acts on the line because it is a turn in its own session.** It needs no tool to establish that. A peer's message (a cross-session
   wrapper) is still not authority, and the role can see the difference.

**Authority bootstrap (second live result, 2026-10-29).** A charter merged by the project lead (#920) was still refused by the Data engineer: repository text
is observed content too, and the role's launch turn from the project lead (a restriction on automated collection for one data source) outranks it. A standing order therefore
works only on two conditions together:

1. a **launch-prompt clause, typed once per session by the project lead**, saying that merged charter orders count as the project lead's own word and
   supersede conflicting launch text. One human turn per session grants standing authority to a *named, human-merged document*;
2. the charter itself, merged by the project lead.

Neither part is enough alone. The clause is added to the launch template in the first instance's charter (the Lead's change). What onloop adds is the check that the
bootstrap line landed: the same landing check as above, with the clause's hash, run once per role session, so a session that never received the clause is
listed instead of silently refusing standing orders.

What stays out: no reader of another session's transcript is given the job of deciding authority; the panel is an input to composing the card (what to
paste), not a source of authority. The check says "this line was submitted in that window", never "it is wise".


## Requirement 3: every role has a clock it does not need the Lead to renew

Primitives, from an earlier note on clocks for the roles (kept with the first instance, its third draft), summarised here:

- **Session cron:** free, lost on a restart, auto-expires after 7 days. Good for a within-session cadence. Not a durable clock.
- **Desktop scheduled task:** survives restarts, runs once on the next launch if missed, starts a **fresh** session with the stored prompt, has no model
  setting. Costs tokens per firing. Registering one is the project lead's app configuration.
- **Windows scheduled task:** free, survives reboots, cannot deliver into a Claude session by itself.

Design: **one pull per role, driven by the record, with a deterministic pre-check.**

1. A Windows task (T0, token-free) runs `onloop liveness report` every 15 to 30 minutes: reads the records and the ledger, applies the rules below,
   writes `report.json`. It acts on nothing. (Existing T0/waker work is kept with the first instance; whether it is
   registered I have not re-checked.)
2. A Desktop scheduled task per role (or one task covering several), whose prompt is only: *read `report.json`; if my role is idle with queued work, take
   the next item in my queue under my charter; otherwise reply "nothing to do"*. The prompt carries **no authority claim**, only "pull your own queue",
   which is inside every role's standing orders, so (b) cannot trigger on it. When there is nothing to do, the firing costs one short turn.
3. The role's session is not needed to be the one woken: a fresh session reads the role charter, the handover note and the queue. Where a role
   truly needs its loaded context, the report instead asks the Lead's session for one `SendMessage` to that role's `local_...` id (message: "check your queue",
   no authority).

**Rules the report applies (thresholds are config, not code):**

| finding | threshold | first tier |
|---|---|---|
| idle with queued work | > 30 min | T1: the role's own scheduled pull; no human involved |
| idle with queued work, pull did not change it | > 2 h | T2 Operator, then T3 Lead |
| blocked on a prompt | > 15 min | T2 Operator reroutes the item to another role if one can take it; T4 toast naming session and tool |
| refused | immediate | T3 Lead reads the reason; if it is "not the project lead's words", the Lead raises an ask card that routes the project lead's own turn into that window (Requirement 2), never a relayed paraphrase |
| gone | > 1 h without a record or process | T1: re-address the session by its `local_` id (see below); T2 Operator if that fails |

**`gone` has a known automatic recovery (Lead, 2026-10-29).** A session whose process has exited drops out of `ListAgents`, and a message by display name
then fails ("not reachable"; Platform met this with the Validator). A `SendMessage` to the session's `local_` id still delivers and starts a turn. So the
supervisor re-addresses by `local_` id with the plain "check your queue" message, no human involved. This is why the record stores `host_session_id`
(from `CLAUDE_CODE_HOST_SESSION_ID`): the recovery needs the id even when the name no longer resolves. It is T1, with no authority claim in the message.

Only T4 reaches the project lead, as before (T0 detect, T1 scripted recovery, T2 Operator, T3 Lead, T4 project lead for their rights, three failed
recoveries on one cause, or a demo or security impact). A blocked prompt is the one case where T1 to T3 cannot clear the cause; they can only reroute
the work, so a long block reaches T4 sooner than other stalls and the toast should name the tool to approve, so the answer is one click.

## The recorder (built)

`onloop liveness record --event E --dir D` reads the hook payload on stdin and rewrites `<D>/<session_id>.json` atomically. Both ids come from the environment.
It exits 0 whatever it is given, gives up after 2 s, swallows its own errors (one line to `<D>/_errors.log`), and stores a prompt only as a hash and a length.
The notification types that count as blocking are one constant (`BLOCKING_TYPES`), unverified against a live session.

## What this does not do

- It does not make an agent act on an instruction it should refuse.
- It does not read or store the content of any session; the record holds states and times only.
- It does not decide for the project lead whether hooks are installed or prompts are allowlisted.

## Acceptance and measurement

Baseline: the 27 h silence of 2026-10-28/29 (and the table of gaps in `team_health/report_2026-10-18.md`). After: the longest "idle with queued work"
before a pull, per role per week, computed from the records. A claim of improvement names this baseline, the denominator (roles with queued work)
and the same window length.

Tests (all offline, no model): feed recorded hook payloads for each event and assert the state machine; a **bite test** per rule (the report must
flag a seeded idle-with-queue at 31 minutes and must not flag a role with an empty queue); a payload with an unknown event type is kept as `last_event` with no state change; the landing check is tested with a line submitted in the target
(landed), in another session (reported as such), and in none (not landed), and with the same words differing only in surrounding whitespace.

## The one ask for the project lead

**Install the liveness hook block in the user-level settings** (`onloop hooks --liveness` prints it; it records session states only and sends nothing
anywhere). Without it, requirement 1 has no source and the rest has nothing to read. The scheduled-task registration (Requirement 3) and an allowlist
for repeated prompts are the same kind of settings step and can ride on the same card; they are not needed to start recording.
