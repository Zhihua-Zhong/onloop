"""The supervisor: the role table and the governed step runner for a team of agent roles. See docs/supervisor.md.

It decides which role step is due and runs each step as one bounded, headless `claude -p` call with that role's fixed
permissions and tools. State lives in files and on GitHub, never in an agent. `onloop step` runs one step from a CI
job (decision 0003).

The work is done by plain functions that take an input and return a result, each idempotent on a step id:

    step.run_step(role, item, ...) -> StepResult
    schedule.plan_steps(...) -> Plan
    asks.drain_asks(asks, open_ids) -> {role: [item]}
    attention.evaluate_attention(state, now, rules, history) -> [Alert]
    steplog.StepLog.write(record) / steplog.write_step_record(path, record)

onestep.py runs one step for `onloop step`; commands.py holds the planner (`onloop supervise --once`), `digest` and
`attention`.
"""
