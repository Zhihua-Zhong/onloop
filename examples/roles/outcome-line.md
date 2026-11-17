# Ending a turn: the outcome line

This paragraph is meant to live once, in a team's shared role template. Each role charter points to it instead of
copying it, so there is one text to keep current. It reaches a role as part of a file (the template, a charter), never
as a command-line argument: a multi-line argument through a `.cmd` shim is cut at its first newline. The contract it
states is [decision 0007](../../docs/decisions/0007-disposition-contract.md), with the grammar of
[decision 0012](../../docs/decisions/0012-outcome-line-version-2.md). It needs onloop 0.4.0 or later; an older onloop
refuses the ask keys and the `event:` waker.

---

**End every turn with an outcome line.** The last line of your final message, on its own, not in a code block, is
one of:

```
outcome: done ref=<what you finished, if it has a reference>
outcome: idle
outcome: in_review ref=<the pull request or issue under review>
outcome: blocked owner=<the role who must act> ref=<the issue, pull request or file>
outcome: handoff owner=<the role who now owns it> ref=<the issue, pull request or file>
outcome: continue ref=<what you will continue> waker=<pid:N | cron:SCHEDULE | monitor:NAME | event:KIND:ID>
```

Use `done` when this item is finished and `idle` when your queue is empty. Use `in_review` when your work waits on a
reviewer and you still own it; use `handoff` when another role now owns it. Use `continue` only when something
specific will wake you: a process id you started, a schedule declared to onloop, a monitor it can check, or an event
on the code host. If what will wake you is a CI run, a set of checks or a review, name it as
`waker=event:gh-run:<run id>`, `waker=event:gh-checks:<commit sha>` or `waker=event:gh-review:pr<number>`; never write
a placeholder pid. If you cannot name the waker, the outcome is `blocked`, with the role who must act as `owner`.
Write values without spaces, or in double quotes.

**Asking the person.** The person is not in this conversation; they see only what onloop lists for them, so an ask
to them is never a question in your reply or a footer you write. Ask with `onloop ask`, then end the turn blocked on
it:

```
onloop ask --owner human --ref pr#45 --effect "approve the schema change, so the import can run on real data" --goal demo/14 --weight critical --days 2 --holds "the import demo waits" --default "nothing merges"
outcome: blocked owner=human ref=task:<the id onloop ask printed>
```

Say what the act changes for the whole (`--effect "do X, so Y"`), the goal item it moves (`--goal demo/14`, or
`--goal none` for process work), its weight (`critical`, `supporting` or `skippable`), and when the delay starts to
cost: `--cost-from YYYY-MM-DD` if you know the date, `--days N` (0 if it costs now), or `--days ?` if it costs but you
do not know when. Whenever you give a cost of delay, say what waits (`--holds "…"`). If the delay never costs, give no
cost of delay and weight `skippable`. The same keys may also be written on the outcome line itself (`effect=`,
`goal=`, `weight=`, `cost_from=` or `days=`, `holds=`, `default=`).

---

Examples that onloop accepts (each is checked by the test suite):

```
outcome: done ref=#123
outcome: idle
outcome: in_review ref=pr#45 owner=reviewer
outcome: blocked owner=operator ref=docs/runbook.md
outcome: handoff owner=builder ref=issue#77
outcome: continue ref=pr#45 waker=pid:4242
outcome: continue ref=pr#46 waker=cron:nightly-build
outcome: continue ref="https://example.invalid/builds/9" waker=monitor:build-queue
outcome: continue ref=pr#47 waker=event:gh-run:9876543210
outcome: blocked owner=human ref=task:t-3f2a
outcome: blocked owner=human ref=pr#45 goal=demo/14 weight=critical days=2 effect="approve the schema change, so the import can run on real data" holds="the import demo waits" default="nothing merges"
outcome: blocked owner=human ref=issue#12 goal=demo/3 weight=supporting cost_from=2026-11-20 effect="accept the provider's terms, so the data feed can start" holds="the provider's onboarding waits"
```
