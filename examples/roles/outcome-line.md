# Ending a turn: the outcome line

This paragraph is meant to live once, in a team's shared role template. Each role charter points to it instead of
copying it, so there is one text to keep current. The contract it states is
[decision 0007](../../docs/decisions/0007-disposition-contract.md).

---

**End every turn with an outcome line.** The last line of your final message, on its own, not in a code block, is
one of:

```
outcome: done ref=<what you finished, if it has a reference>
outcome: idle
outcome: in_review ref=<the pull request or issue under review>
outcome: blocked owner=<the role who must act> ref=<the issue, pull request or file>
outcome: handoff owner=<the role who now owns it> ref=<the issue, pull request or file>
outcome: continue ref=<what you will continue> waker=<pid:N | cron:SCHEDULE | monitor:NAME>
```

Use `done` when this item is finished and `idle` when your queue is empty. Use `in_review` when your work waits on a
reviewer and you still own it; use `handoff` when another role now owns it. Use `continue` only when something
specific will wake you: a process id you started, a schedule declared to onloop, or a monitor it can check. If you
cannot name that waker, the outcome is `blocked`, with the role who must act as `owner`. Write values without
spaces, or in double quotes.

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
```
