"""The session carrier (decision 0010): onloop carries the role conversations itself.

A role is a Claude Code session id plus a lease. onloop delivers messages into it by headless
`claude -p --resume <id>` on the user's own subscription login, one process and one composed message per run, and
records each step in the ledger. Act modes are off by default: without an opt-in, the carrier only logs
`would.deliver`.

    config.py     the `carrier:` block of onloopd.yaml, and the refusal of a role that names a shell
    listing.py    `claude agents --json`, filtered to the bound role sessions in one function
    jobs.py       a child in its own Windows job object (or POSIX process group), and process identity
    stream.py     reading a run's stream-json: the replay, the result, the rate-limit event, the denials
    lease.py      one live lease per session id: acquire, renew, release, recover
    deliveries.py the outbox: create, compose, acknowledge by evidence, retry, escalate
    messages.py   the text onloop stores: scrubbed, kept 30 days, then only its hash
    guards.py     the kill switch, the metered-key refusal, the login check, usage limits and budgets
    runner.py     one run: spawn, deliver, watch, read
    loop.py       the carrier itself: what onloopd runs on its tick
"""
