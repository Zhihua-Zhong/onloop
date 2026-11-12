#!/usr/bin/env python3
"""Mutation checks: prove the tests bite.

Each entry names a file, the exact text to change, the change, and the tests that must then fail. The script applies
one mutation at a time, runs those tests, and restores the file. A mutation the tests do not catch, or whose old text
is no longer in the file, is a failure. The table grows with each check that is added; it is the evidence that a quiet failure
(a missed event, a double effect, a replayed grant) would be noticed.

Run from the repository root: `python tools/mutation_check.py` (all), `--list`, or `--only SUBSTRING`.
Exit codes: 0 every mutation was caught, 1 one survived or could not be applied.
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (label, file, old text, new text, pytest selectors that must fail)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "the integrity skip accepts a digest of any length",
        "tools/confidentiality_scan.py",
        "[A-Za-z0-9+/]{86}==|",
        "[A-Za-z0-9+/]+={0,2}|",
        ["tests/test_confidentiality_scan.py::test_every_other_lockfile_line_is_still_scanned"],
    ),
    (
        "the scanner's own self-test no longer exercises the lockfile skip",
        "tools/confidentiality_scan.py",
        'check("lockfile-short-value-caught", bool(',
        'check("lockfile-short-value-caught", not bool(',
        ["tests/test_confidentiality_scan.py::test_self_test_passes"],
    ),
    (
        "the scanner skips no integrity line",
        "tools/confidentiality_scan.py",
        "if skip is not None and skip(line):",
        "if False:",
        ["tests/test_confidentiality_scan.py::test_an_sri_integrity_line_in_a_lockfile_is_skipped"],
    ),
    (
        "the integrity skip is not anchored to the whole line",
        "tools/confidentiality_scan.py",
        r"""",?\s*$')""",
        r"""",?')""",
        ["tests/test_confidentiality_scan.py::test_every_other_lockfile_line_is_still_scanned"],
    ),
    (
        "the integrity skip applies to every file",
        "tools/confidentiality_scan.py",
        'if rel_path.rsplit("/", 1)[-1] != "package-lock.json":',
        "if False:",
        ["tests/test_confidentiality_scan.py::test_no_other_file_gets_the_skip"],
    ),
    (
        "the clock may break the timestamp format",
        "src/onloop/sqlitedb.py",
        "return ev.check_ts(text)",
        "return text",
        ["tests/test_legacy_poll_store.py::test_a_clock_that_breaks_the_format_is_refused"],
    ),
    (
        "a failed transaction is committed",
        "src/onloop/sqlitedb.py",
        'self.conn.execute("ROLLBACK")',
        'self.conn.execute("COMMIT")',
        ["tests/test_legacy_poll_store.py::test_a_failed_transaction_leaves_nothing"],
    ),
    (
        "the events command opens the ledger read-write",
        "src/onloop/cli.py",
        "with Ledger(_ledger_path(args), readonly=True) as led:\n            rows = views.events_query",
        "with Ledger(_ledger_path(args)) as led:\n            rows = views.events_query",
        ["tests/test_ui_api.py::test_the_events_command_opens_the_ledger_read_only"],
    ),
    (
        "intake stops polling a pull request whose checks are pending",
        "src/onloop/intake.py",
        'if _settling(snap["facts"], required) and spent <',
        "if False and spent <",
        [
            "tests/test_intake.py::test_a_check_that_turns_green_is_seen_though_the_pull_request_did_not_change"
        ],
    ),
    (
        "intake polls a settling pull request for ever",
        "src/onloop/intake.py",
        "and spent < settle_ticks:",
        "and True:",
        ["tests/test_intake.py::test_a_pull_request_with_no_checks_yet_is_polled_until_the_tick_cap"],
    ),
    (
        "a ready toggle ignores its transition count",
        "src/onloop/intake.py",
        "f\"ready:{counts['ready']}\"",
        '"ready"',
        ["tests/test_intake.py::test_toggling_ready_twice_on_the_same_head_gives_two_ready_events"],
    ),
    (
        "the patch id is taken against the base tip, not the head's own merge-base",
        "src/onloop/gitutil.py",
        '"--no-textconv", mb, head]',
        '"--no-textconv", base, head]',
        ["tests/test_patch_id_git.py::test_merging_the_base_into_the_branch_keeps_the_patch_id"],
    ),
    (
        "an unknown patch id reads as clean",
        "src/onloop/intake.py",
        "substantive = (old_patch != new_patch) if known else True",
        "substantive = (old_patch != new_patch) if known else False",
        [
            "tests/test_intake.py::test_a_head_whose_patch_cannot_be_computed_is_substantive_with_basis_unknown"
        ],
    ),
    (
        "an unknown patch id reads as unknown (None)",
        "src/onloop/intake.py",
        "substantive = (old_patch != new_patch) if known else True",
        "substantive = (old_patch != new_patch) if known else None",
        [
            "tests/test_intake.py::test_a_head_whose_patch_cannot_be_computed_is_substantive_with_basis_unknown"
        ],
    ),
    (
        "verdict reversals are never reported",
        "src/onloop/intake.py",
        "if {before[ident], after[ident]} == _VERDICTS:",
        "if False:",
        ["tests/test_intake.py::test_an_approval_that_becomes_a_request_for_changes_is_a_reversal"],
    ),
    (
        "a verdict by anyone counts for a reversal",
        "src/onloop/intake.py",
        "gate.standing_reviews(old, policy), gate.standing_reviews(new, policy)",
        "_anyone(old), _anyone(new)",
        ["tests/test_intake.py::test_a_verdict_by_the_wrong_identity_is_not_a_reversal"],
    ),
    (
        "checks of the old head complete again on the new head",
        "src/onloop/intake.py",
        'if old is not None and old["head"] == new["head"] else {}',
        "if old is not None else {}",
        ["tests/test_intake.py::test_checks_of_the_old_head_do_not_complete_on_the_new_one"],
    ),
    (
        "a pull request that left the list is not finalised",
        "src/onloop/intake.py",
        'if closing:\n            if new["state"]',
        'if False:\n            if new["state"]',
        ["tests/test_intake.py::test_a_merged_pull_request_is_a_merged_event_once"],
    ),
    (
        "the ledger ingest's events and state are not one transaction",
        "src/onloop/ingest/github.py",
        "    with ledger.tx():\n        for e in plan.events:",
        "    if True:\n        for e in plan.events:",
        ["tests/test_ingest_github.py::test_a_failure_while_writing_leaves_neither_events_nor_state"],
    ),
    (
        "a valid outcome raises a stall alert",
        "src/onloop/detect/no_outcome.py",
        '        if t["parse_error"] is None:\n            continue',
        "        if False:\n            continue",
        ["tests/test_detect_no_outcome.py::test_a_valid_outcome_raises_nothing"],
    ),
    (
        "an earlier bad turn keeps its alert after a valid one",
        "src/onloop/detect/no_outcome.py",
        "SELECT 1 FROM turns u WHERE u.role = t.role AND ",
        "SELECT 1 FROM turns u WHERE 0 AND u.role = t.role AND ",
        ["tests/test_detect_no_outcome.py::test_one_wake_per_turn_and_the_next_turn_closes_it"],
    ),
    (
        "a dead pid counts as alive",
        "src/onloop/detect/wakers.py",
        '        if self._alive(int(ref)):\n            return Probed("alive"',
        '        if True:\n            return Probed("alive"',
        ["tests/test_detect_wakers.py::test_story_11_a_stopped_waker_is_one_alert_naming_both_items"],
    ),
    (
        "an overdue schedule counts as alive",
        "src/onloop/detect/wakers.py",
        "        if late > 0:",
        "        if late > 10**9:",
        ["tests/test_detect_wakers.py::test_a_cron_that_misses_its_window_is_a_dead_waker"],
    ),
    (
        "a monitor answering 500 counts as alive",
        "src/onloop/detect/wakers.py",
        "        if 200 <= code < 300:",
        "        if 200 <= code < 600:",
        ["tests/test_detect_wakers.py::test_a_monitor_answering_500_is_dead_and_200_alive_again"],
    ),
    (
        "a waker's state is logged on every tick",
        "src/onloop/detect/wakers.py",
        '        if got.state != r["state"]:',
        "        if True:",
        ["tests/test_detect_wakers.py::test_story_11_a_stopped_waker_is_one_alert_naming_both_items"],
    ),
    (
        "a failed scheduled run raises nothing",
        "src/onloop/detect/schedules.py",
        '        failed = s["last_status"] in ("fail", "down")',
        "        failed = False",
        [
            "tests/test_detect_schedules.py::test_a_failed_run_alerts_its_owner_with_the_log_tail_and_a_success_closes_it"
        ],
    ),
    (
        "a missed scheduled run raises nothing",
        "src/onloop/detect/schedules.py",
        "            if late > 0:",
        "            if late > 10**9:",
        ["tests/test_detect_schedules.py::test_story_3_eight_missed_nights_are_one_alert_on_the_first_night"],
    ),
    (
        "a merge with a post-merge step is done at once",
        "src/onloop/overlay/projections.py",
        '    _set(conn, "items", parent, {"state": "blocked"}, e["seq"])',
        "    pass",
        ["tests/test_post_merge.py::test_story_10_the_merge_is_not_done_until_the_step_has_evidence"],
    ),
    (
        "one step's evidence finishes the pull request",
        "src/onloop/overlay/projections.py",
        "    if not left:",
        "    if True:",
        ["tests/test_post_merge.py::test_two_steps_need_both"],
    ),
    (
        "a paused role's turn ends its pause",
        "src/onloop/overlay/projections.py",
        '        fields.pop("state", None)',
        "        pass",
        [
            "tests/test_constraint_api.py::test_story_2b_a_paused_role_with_four_queued_items_waits_on_the_human"
        ],
    ),
    (
        "a continue on a dead waker is not typed as one",
        "src/onloop/overlay/constraint.py",
        '            if w.reason == "continue" and state == "dead":',
        "            if False:",
        ["tests/test_constraint_api.py::test_a_continue_on_a_dead_waker_is_typed_as_one"],
    ),
    (
        "a grant event replaces the grant that holds the seat",
        "src/onloop/overlay/projections.py",
        '"INSERT INTO slot_grants (id, slot,',
        '"INSERT OR REPLACE INTO slot_grants (id, slot,',
        ["tests/test_slots.py::test_a_grant_event_for_a_taken_seat_is_refused_and_not_logged"],
    ),
    (
        "an imported node loses its parent",
        "src/onloop/overlay/projections.py",
        'out["parent_id"] = f"wg:{node[\'spawned_by\']}" if node["spawned_by"] else None',
        'out["parent_id"] = None',
        [
            "tests/test_overlay_import.py::test_an_imported_node_keeps_its_parent_and_a_topic_is_marked_for_the_flow_zones"
        ],
    ),
    (
        "a schedule's alert counts as a stall",
        "src/onloop/report/views.py",
        'if detail.get("detector") == "schedule":',
        'if detail.get("detector") == "never":',
        [
            "tests/test_ui_api.py::test_now_lists_the_stalls_with_their_age_the_would_be_wake_and_no_acknowledgement"
        ],
    ),
    (
        "a would-be wake reads as if it happened",
        "src/onloop/report/views.py",
        '"label": f"would {action} (observe only)",',
        '"label": f"{action}",',
        [
            "tests/test_ui_api.py::test_now_lists_the_stalls_with_their_age_the_would_be_wake_and_no_acknowledgement"
        ],
    ),
    (
        "an earlier turn counts as the acknowledgement",
        "src/onloop/report/views.py",
        '"SELECT ended_at, outcome FROM turns WHERE role = ? AND ended_at > ? ORDER BY ended_at LIMIT 1",',
        '"SELECT ended_at, outcome FROM turns WHERE role = ? AND ended_at < ? ORDER BY ended_at LIMIT 1",',
        [
            "tests/test_ui_api.py::test_now_lists_the_stalls_with_their_age_the_would_be_wake_and_no_acknowledgement"
        ],
    ),
    (
        "an old heartbeat reads as fresh",
        "src/onloop/report/views.py",
        '"stale": age is None or age > HEARTBEAT_STALE_S,',
        '"stale": age is None,',
        [
            "tests/test_ui_api.py::test_health_shows_the_failing_and_late_schedules_the_heartbeat_and_the_dead_man_switch"
        ],
    ),
    (
        "a card without a direction skips its owner's zone",
        "src/onloop/report/views.py",
        'if zone is None and item.get("owner_role"):',
        "if zone is None and False:",
        ["tests/test_ui_api.py::test_flow_zones_come_from_the_direction_then_the_owner_then_unassigned"],
    ),
    (
        "the time filter reads the wrong side",
        "src/onloop/report/views.py",
        'for name, op in (("since", ">="), ("until", "<")):',
        'for name, op in (("since", "<"), ("until", ">=")):',
        ["tests/test_ui_api.py::test_events_filter_by_kind_subject_source_actor_and_the_ledgers_own_time"],
    ),
    (
        "a paused role's items are separate asks",
        "src/onloop/report/views.py",
        '        if w["reason"] == "paused_by_human":\n            paused.setdefault',
        '        if w["reason"] == "never":\n            paused.setdefault',
        ["tests/test_ui_api.py::test_now_lists_what_waits_on_the_human_with_the_default_and_the_time_left"],
    ),
    (
        "the downstream set stops at the first wait",
        "src/onloop/report/views.py",
        "todo.append(n)",
        "pass",
        ["tests/test_ui_api.py::test_downstream_follows_waits_through_roles_and_dependencies"],
    ),
    (
        "the UI serves a file outside its directory",
        "src/onloop/daemon/ui.py",
        "if base not in target.parents or not target.is_file():",
        "if not target.is_file():",
        ["tests/test_ui_serving.py::test_resolve_never_leaves_the_root"],
    ),
    (
        "the UI serves dot files",
        "src/onloop/daemon/ui.py",
        'if any(p.startswith(".") for p in parts):',
        'if any(p == "never" for p in parts):',
        ["tests/test_ui_serving.py::test_nothing_outside_the_bundle_or_hidden_or_unknown_is_served"],
    ),
    (
        "the UI drops its CSP",
        "src/onloop/daemon/ui.py",
        '    "Content-Security-Policy": CSP,\n',
        "",
        ["tests/test_ui_serving.py::test_the_index_and_the_assets_are_served_with_their_types_and_the_csp"],
    ),
    (
        "the version 5 migration drops the log",
        "src/onloop/overlay/schema.py",
        "INSERT INTO events_v5 (seq, id, ts_system, source, kind, subject_ref, actor, at, payload) SELECT seq, id, ts_system, source, kind, subject_ref, actor, at, payload FROM events ORDER BY seq;",
        "",
        ["tests/test_overlay_schema.py::test_a_version_4_ledger_migrates_to_version_5_and_keeps_every_event"],
    ),
    (
        "the gate's decisions are logged as a person's",
        "src/onloop/cli.py",
        '"source": "gate",\n                    "kind": kind,',
        '"source": "ui",\n                    "kind": kind,',
        ["tests/test_gate_recording.py::test_with_a_ledger_the_decision_is_recorded_as_the_gates_own"],
    ),
    (
        "a failed gate record fails the gate",
        "src/onloop/cli.py",
        '            failed.append(f"{kind} for {subject}: {type(e).__name__}: {e}"[:300])',
        "            raise",
        ["tests/test_gate_recording.py::test_an_append_that_fails_leaves_the_decision_and_warns"],
    ),
    (
        "the gate creates a ledger",
        "src/onloop/cli.py",
        '    if not path.exists():\n        return None, f"no ledger at {path}"',
        '    if False:\n        return None, f"no ledger at {path}"',
        [
            "tests/test_gate_recording.py::test_without_a_ledger_the_gate_decides_as_before_and_says_it_did_not_record"
        ],
    ),
    (
        "merges without gate decisions count as no reverts",
        "src/onloop/kpi.py",
        "    known = seen or not merges",
        "    known = True",
        [
            "tests/test_kpis.py::test_merges_without_any_gate_decision_leave_the_gate_counts_unknown_never_zero"
        ],
    ),
    (
        "a queue with nothing human-held gets general advice",
        "src/onloop/report/views.py",
        "                    item=holder,\n                )\n        return None",
        '                    item=holder,\n                )\n        return _move(f"free {ref}")',
        ["tests/test_ui_api.py::test_without_a_concrete_move_there_is_no_next_move"],
    ),
    (
        "the next move ignores who holds the queue",
        "src/onloop/report/views.py",
        '            if holder and holder["id"] in human:',
        "            if holder and False:",
        ["tests/test_ui_api.py::test_now_names_the_constraint_with_its_numbers_and_what_else_is_blocked"],
    ),
    (
        "a 304 is treated as a failure",
        "src/onloop/github.py",
        "(p.returncode != 0 and status != 304)",
        "(p.returncode != 0)",
        ["tests/test_patch_id_git.py::test_a_304_is_not_modified_even_though_gh_exits_non_zero"],
    ),
    (
        "a review body is stored in the log",
        "src/onloop/intake.py",
        '"body_hash": ev.body_hash(r.get("body"))',
        '"body_hash": r.get("body")',
        ["tests/test_intake.py::test_a_review_becomes_one_event_with_its_head_and_author"],
    ),
    (
        "the patch id ignores whitespace again (--stable)",
        "src/onloop/gitutil.py",
        '["git", "patch-id", "--verbatim"]',
        '["git", "patch-id", "--stable"]',
        ["tests/test_patch_id_git.py::test_a_dedent_that_changes_what_python_does_is_a_different_patch"],
    ),
    (
        "an old git is trusted to have --verbatim",
        "src/onloop/gitutil.py",
        "if not self.supports_verbatim():",
        "if False:",
        ["tests/test_patch_id_git.py::test_an_old_git_has_no_safe_id_so_the_push_counts_as_substantive"],
    ),
    (
        "a reopened pull request is silent",
        "src/onloop/intake.py",
        'if not closing and old_row and old_row["state"] != "OPEN":',
        "if False:",
        ["tests/test_intake.py::test_a_reopened_pull_request_is_an_event_and_its_snapshot_is_refreshed"],
    ),
    (
        "polling gives up by the clock, not the tick count",
        "src/onloop/intake.py",
        'spent = _spent(state, n, snap["head"])',
        'spent = 10 ** 6 if now > snap["head_seen_at"] else 0',
        ["tests/test_intake.py::test_a_long_queue_is_not_given_up_on_by_the_clock"],
    ),
    (
        "a finished check that is not required ends the polling",
        "src/onloop/intake.py",
        "        return any(name not in done for name in required)",
        "        return False",
        [
            "tests/test_intake.py::test_polling_continues_while_a_required_check_is_pending_though_another_check_finished"
        ],
    ),
    # -- dispatcher, effects, observe mode, codes, runner ---------------------------------------------------------
    (
        "a dry run records its decision",
        "src/onloop/gate.py",
        "    if dry_run:\n        record = None",
        "    if False:\n        record = None",
        ["tests/test_gate_codes.py::test_a_dry_run_records_nothing"],
    ),
    (
        "a missing authoring role is not a form refusal",
        "src/onloop/gate.py",
        'Refusal(\n                "P001"',
        'Refusal(\n                "G090"',
        ["tests/test_gate_codes.py::test_every_code_is_reachable_and_documented"],
    ),
    (
        "turning the autogate on is not a loosening",
        "src/onloop/policy.py",
        "if head.get(flag) and not base.get(flag):",
        "if False:",
        ["tests/test_policy_automation.py::test_loosening_the_automation_is_authority_widening"],
    ),
    (
        "a merge found on replay is not recorded as pending post-merge checks",
        "src/onloop/gate.py",
        'if record and base is not None and facts.get("state") == "MERGED" and facts.get("merge_sha"):',
        "if False:",
        [
            "tests/test_gate_codes.py::test_a_replay_that_finds_the_agents_own_merge_records_the_post_merge_checks_as_pending"
        ],
    ),
    (
        "a merge by a person counts as the agents' own",
        "src/onloop/gate.py",
        'ours = facts.get("merged_by") == {"login": base.agents, "type": "Bot"}',
        "ours = True",
        ["tests/test_gate_codes.py::test_a_merge_by_a_person_is_already_merged_not_pending"],
    ),
    (
        "the gate fetches the merge though no post-merge check is configured",
        "src/onloop/gate.py",
        'if not (base.raw.get("post_merge_checks") or []):',
        "if False:",
        ["tests/test_gate_merge.py::test_with_no_post_merge_checks_the_merge_is_not_fetched_at_all"],
    ),
    (
        "the reviewer acknowledgement accepts any author",
        "src/onloop/gate.py",
        'if is_reviewer(r.get("author") or {}, policy)\n        and r.get("commit")',
        'if True\n        and r.get("commit")',
        [
            "tests/test_reviewer_ack.py::test_the_same_text_from_a_user_named_like_the_reviewer_is_refused",
            "tests/test_reviewer_ack.py::test_the_same_text_from_the_agents_app_is_refused",
        ],
    ),
    (
        "the acknowledgement may sit on an older head",
        "src/onloop/gate.py",
        'and r.get("commit") == facts["head"]',
        "and True",
        ["tests/test_reviewer_ack.py::test_a_quote_on_an_older_head_is_refused"],
    ),
    (
        "a withdrawn approval still acknowledges",
        "src/onloop/gate.py",
        'if not any(\n        s == "APPROVED" and is_reviewer({"login": lg, "type": t}, policy) for (lg, t), s in standing.items()\n    ):',
        "if False:",
        ["tests/test_reviewer_ack.py::test_a_later_change_request_by_the_reviewer_withdraws_it"],
    ),
    # -- slots ----------------------------------------------------------------------------------------------------
    (
        "a lease is never seen as over",
        "src/onloop/slots.py",
        '"WHERE released_at IS NULL AND expires_at <= ? ORDER BY id"',
        '"WHERE released_at IS NULL AND expires_at > ? ORDER BY id"',
        [
            "tests/test_slots.py::test_a_lease_runs_out_and_the_next_request_is_granted_with_the_old_holder_named",
            "tests/test_slots.py::test_a_lease_that_has_not_run_out_is_not_taken",
        ],
    ),
    (
        "the unique index on live grants is dropped",
        "src/onloop/overlay/schema.py",
        "CREATE UNIQUE INDEX one_live_seat ON slot_grants(slot, seat) WHERE released_at IS NULL",
        "CREATE INDEX one_live_seat ON slot_grants(slot, seat) WHERE released_at IS NULL",
        ["tests/test_slots.py::test_the_database_allows_one_live_grant_per_slot"],
    ),
    (
        "path scoping uses substring instead of a glob",
        "src/onloop/slots.py",
        "return _glob(pattern).match(path) is not None",
        "return pattern in path",
        ["tests/test_slots.py::test_path_patterns"],
    ),
    (
        "a grant prints the request's text as its commands",
        "src/onloop/slots.py",
        '"commands": list(spec["commands"]),',
        '"commands": [req["reason"]],',
        ["tests/test_slots.py::test_the_conditions_and_commands_come_from_the_policy_not_from_the_request"],
    ),
    (
        "a person may grant a request that is not first in the queue",
        "src/onloop/slots.py",
        'if first["id"] != request_id:',
        "if False:",
        [
            "tests/test_slots.py::test_a_human_slot_waits_for_a_person_and_only_the_head_of_the_queue_can_be_granted"
        ],
    ),
    (
        "the newest request is granted before the oldest",
        "src/onloop/slots.py",
        "\"SELECT * FROM slot_requests WHERE slot = ? AND status = 'waiting' ORDER BY requested_at, id LIMIT 1\",",
        "\"SELECT * FROM slot_requests WHERE slot = ? AND status = 'waiting' ORDER BY requested_at DESC, id DESC LIMIT 1\",",
        ["tests/test_slots.py::test_a_second_request_waits_and_the_release_grants_it_in_order"],
    ),
    (
        "a new request does not expire a dead runner's lease",
        "src/onloop/slots.py",
        "with store.tx():\n        _expire(store, repo)\n        live = next(\n            (\n                g\n                for g in store.conn.execute(",
        "with store.tx():\n        live = next(\n            (\n                g\n                for g in store.conn.execute(",
        [
            "tests/test_slots.py::test_with_the_runner_stopped_a_new_request_expires_the_dead_lease_and_is_granted"
        ],
    ),
    (
        "a slot whose approver is loosened is not flagged",
        "src/onloop/policy.py",
        'if _approver_rank(now["approver"]) > _approver_rank(was["approver"]) or (',
        "if False or (",
        ["tests/test_slots.py::test_loosening_a_slot_is_flagged"],
    ),
    (
        "a slot that loses paths is not flagged",
        "src/onloop/policy.py",
        'lost = sorted(set(was["paths"]) - set(now["paths"]))',
        "lost = []",
        ["tests/test_slots.py::test_loosening_a_slot_is_flagged"],
    ),
    (
        "a removed slot is not flagged",
        "src/onloop/policy.py",
        '        if now is None:\n            out.append(f"slot {name!r} is removed")',
        '        if False:\n            out.append(f"slot {name!r} is removed")',
        ["tests/test_slots.py::test_loosening_a_slot_is_flagged"],
    ),
    (
        "a longer lease is not flagged",
        "src/onloop/policy.py",
        'if now["timeout_minutes"] > was["timeout_minutes"]:',
        "if False:",
        ["tests/test_slots.py::test_loosening_a_slot_is_flagged"],
    ),
    (
        "the renewal limit is never flagged",
        "src/onloop/slots.py",
        'over = n > spec["max_renewals"]',
        "over = False",
        ["tests/test_slots.py::test_renewing_extends_the_lease_and_over_the_limit_is_flagged_not_cut_off"],
    ),
    (
        "a holder may grant its own request",
        "src/onloop/slots.py",
        'if identity.same_identity(by, req["holder"]):',
        "if False:",
        ["tests/test_slots.py::test_a_holder_cannot_grant_its_own_request"],
    ),
    (
        "anyone may grant a human slot",
        "src/onloop/slots.py",
        'if approver == "human" and not identity.is_login(by, policy.human, "User"):',
        "if False:",
        ["tests/test_slots.py::test_a_human_slot_is_granted_only_by_the_policys_human"],
    ),
    (
        "anyone may grant a role slot",
        "src/onloop/slots.py",
        'if approver.startswith("role:") and by_role != approver[5:]:',
        "if False:",
        ["tests/test_slots.py::test_a_role_slot_needs_a_granter_who_states_that_role"],
    ),
    (
        "anyone may renew a lease",
        "src/onloop/slots.py",
        'if not identity.same_identity(g["holder"], holder):',
        "if False:",
        ["tests/test_slots.py::test_only_the_holder_renews"],
    ),
    (
        "anyone may release a lease without saying who they are",
        "src/onloop/slots.py",
        'if by is None and (holder is None or not identity.same_identity(g["holder"], holder)):',
        "if False:",
        ["tests/test_slots.py::test_a_release_by_someone_else_needs_by_and_is_recorded_under_that_identity"],
    ),
    (
        "a release by someone else is recorded under the holder",
        "src/onloop/slots.py",
        '"released_by": by or g["holder"],\n            },\n            by or g["holder"],\n        )',
        '"released_by": g["holder"]},g["holder"],\n        )',
        ["tests/test_slots.py::test_a_release_by_someone_else_needs_by_and_is_recorded_under_that_identity"],
    ),
    (
        "dropping a condition or command is not a loosening",
        "src/onloop/policy.py",
        "dropped = [x for x in was[key] if x not in now[key]]",
        "dropped = []",
        ["tests/test_slots.py::test_loosening_what_a_grant_says_or_how_long_it_is_flagged"],
    ),
    (
        "raising max_renewals is not a loosening",
        "src/onloop/policy.py",
        'if now["max_renewals"] > was["max_renewals"]:',
        "if False:",
        ["tests/test_slots.py::test_loosening_what_a_grant_says_or_how_long_it_is_flagged"],
    ),
    (
        "a change between role approvers is not a loosening",
        "src/onloop/policy.py",
        'was["approver"] != now["approver"]\n            and was["approver"].startswith("role:")',
        'False\n            and was["approver"].startswith("role:")',
        ["tests/test_slots.py::test_a_change_between_different_role_approvers_is_a_loosening"],
    ),
    (
        "the holder is not normalised on the way in",
        "src/onloop/slots.py",
        "holder = identity.canonical(holder)  # one form, once, on the way in",
        "",
        [
            "tests/test_slots.py::test_the_holder_is_stored_in_one_canonical_form",
            "tests/test_slots.py::test_asking_in_another_form_is_the_same_request",
        ],
    ),
    (
        "a self-grant compares the strings, not the identities",
        "src/onloop/slots.py",
        'if identity.same_identity(by, req["holder"]):',
        'if by == req["holder"]:',
        [
            "tests/test_slots.py::test_a_typed_holder_cannot_be_bypassed_by_the_bare_login_on_a_role_slot",
            "tests/test_slots.py::test_a_typed_holder_cannot_grant_itself_the_human_slot_with_the_bare_login",
        ],
    ),
    (
        "the human check ignores the account type",
        "src/onloop/slots.py",
        'not identity.is_login(by, policy.human, "User")',
        "not identity.is_login(by, policy.human)",
        ["tests/test_slots.py::test_an_app_with_the_humans_login_is_not_the_human"],
    ),
    (
        "a renewal compares the strings, not the identities",
        "src/onloop/slots.py",
        'if not identity.same_identity(g["holder"], holder):',
        'if g["holder"] != holder:',
        ["tests/test_slots.py::test_the_real_holder_can_renew_and_release_in_any_form"],
    ),
    (
        "identities of different types are the same",
        "src/onloop/identity.py",
        "x.type is None or y.type is None or x.type == y.type",
        "True",
        [
            "tests/test_identity.py::test_same_identity",
            "tests/test_slots.py::test_another_account_with_the_same_login_but_a_different_type_cannot",
        ],
    ),
    (
        "login case matters",
        "src/onloop/identity.py",
        "login = login.strip().lower().removesuffix",
        "login = login.strip().removesuffix",
        ["tests/test_identity.py::test_canonical_form"],
    ),
    (
        "pre-flight reports a substance refusal as form",
        "src/onloop/preflight.py",
        "if r.code in FORM_CODES]",
        "]",
        ["tests/test_preflight.py::test_a_non_form_refusal_from_the_shared_rules_is_filtered_out"],
    ),
    (
        "a github error reads as no problems",
        "src/onloop/cli.py",
        '        _err(f"onloop: {e}")\n        return 2\n    return 0 if preflight_mod.clean(rep) else 1',
        '        _err(f"onloop: {e}")\n        return 0\n    return 0 if preflight_mod.clean(rep) else 1',
        ["tests/test_preflight.py::test_a_github_error_is_not_read_as_no_problems"],
    ),
    (
        "the reviewer is compared by exact login",
        "src/onloop/gate.py",
        'bool(a.get("login")) and identity.is_login(a["login"], policy.reviewer)',
        'a.get("login") == policy.reviewer',
        ["tests/test_reviewer_ack.py::test_the_reviewers_login_is_compared_without_case_in_every_rule"],
    ),
    (
        "a carriage return on a removed line is a warning",
        "src/onloop/gate.py",
        'line.startswith("+") and line.endswith("\\r")',
        'line.endswith("\\r")',
        ["tests/test_preflight.py::test_a_removed_line_with_a_carriage_return_is_not_flagged"],
    ),
    (
        "an owned path is called unowned",
        "src/onloop/preflight.py",
        "unowned = sorted(p for p in files if policy.match(p) is None)",
        "unowned = sorted(p for p in files)",
        ["tests/test_preflight.py::test_a_path_no_policy_entry_covers_is_a_warning"],
    ),
    (
        "a warning makes the change unclean",
        "src/onloop/preflight.py",
        'return not rep["problems"]',
        'return not rep["problems"] and not rep["warnings"]',
        ["tests/test_preflight.py::test_carriage_returns_added_to_a_file_are_a_warning_not_a_problem"],
    ),
    (
        "a quoted fence acknowledges",
        "src/onloop/gate.py",
        "        if m:\n            fence = m.group(1)\n            continue\n",
        "        if m:\n            continue\n",
        ["tests/test_reviewer_ack.py::test_a_quoted_line_is_not_an_acknowledgement"],
    ),
    (
        "an earlier approval speaks for the reviewer",
        "src/onloop/gate.py",
        'key=lambda r: (r.get("submitted_at") or "", r.get("id") or 0))',
        'key=lambda r: -(r.get("id") or 0))',
        ["tests/test_reviewer_ack.py::test_a_later_approval_without_the_line_withdraws_it"],
    ),
    (
        "any author may own the marker",
        "src/onloop/github.py",
        "            if authors is not None:\n                who = actor",
        "            if False:\n                who = actor",
        ["tests/test_preflight.py::test_the_real_client_matches_comment_authors_by_identity"],
    ),
    (
        "the author's fenced line waives ownership",
        "src/onloop/gate.py",
        'for line in live_lines(t or "") for m in CROSS_OWNER.findall(line)}',
        'for m in CROSS_OWNER.findall(t or "")}',
        [
            "tests/test_reviewer_ack.py::test_a_fenced_or_quoted_cross_owner_line_in_the_body_does_not_waive_ownership",
            "tests/test_reviewer_ack.py::test_a_fenced_line_in_a_commit_message_does_not_waive_ownership",
        ],
    ),
    (
        "an unterminated comment hides nothing",
        "src/onloop/gate.py",
        'r"<!--.*?(?:-->|\\Z)"',
        'r"<!--.*?-->"',
        ["tests/test_reviewer_ack.py::test_an_unterminated_html_comment_runs_to_the_end"],
    ),
    (
        "a path is written raw into the comment",
        "src/onloop/preflight.py",
        '", ".join(code_span(p) for p in w["paths"])',
        '", ".join(p for p in w["paths"])',
        ["tests/test_preflight.py::test_a_path_with_a_mention_and_a_backtick_stays_inside_its_code_span"],
    ),
    (
        "fetch_pr takes its destination from the caller",
        "src/onloop/gitutil.py",
        'f"+refs/heads/{branch}:{BASE_REF}",\n            f"+refs/pull/{number}/head:{PR_REF.format(n=number)}",\n        )',
        'f"+refs/heads/{branch}:refs/heads/{branch}",\n            f"+refs/pull/{number}/head:{PR_REF.format(n=number)}",\n        )',
        [
            "tests/test_preflight.py::test_the_fixed_refspecs_are_the_whole_command",
            "tests/test_preflight.py::test_fetch_pr_writes_only_into_the_onloop_namespace",
        ],
    ),
    (
        "fetch_pr fetches tags",
        "src/onloop/gitutil.py",
        '"fetch",\n            "--no-tags",\n            "--no-recurse-submodules",\n            "origin",\n            f"+refs/heads/{branch}:{BASE_REF}",\n            f"+refs/pull',
        '"fetch",\n            "--no-recurse-submodules",\n            "origin",\n            f"+refs/heads/{branch}:{BASE_REF}",\n            f"+refs/pull',
        ["tests/test_preflight.py::test_the_fixed_refspecs_are_the_whole_command"],
    ),
    (
        "a branch name is not checked",
        "src/onloop/gitutil.py",
        '    if (\n        not isinstance(base, str)\n        or not _BRANCH.match(base)\n        or ".." in base\n        or base.endswith((".lock", "/", "."))\n    ):',
        "    if False:",
        ["tests/test_preflight.py::test_fetch_pr_will_not_be_steered"],
    ),
    (
        "allow_working_tree is not a loosening",
        "src/onloop/policy.py",
        'if head.get("allow_working_tree") and not base.get("allow_working_tree"):',
        "if False:",
        ["tests/test_preflight.py::test_turning_allow_working_tree_on_is_a_loosening"],
    ),
    (
        "a revert is pushed to onloop's namespace instead of the real base",
        "src/onloop/gate.py",
        'wt._run("push", "origin", f"HEAD:{base_branch}")',
        'wt._run("push", "origin", f"HEAD:{BASE_REF}")',
        [
            "tests/test_gate_merge.py::test_a_revert_pushes_to_the_real_remote_base_branch_not_the_onloop_namespace"
        ],
    ),
    (
        "a draft is measured from its opening",
        "src/onloop/kpi.py",
        'if opened["payload"].get("draft"):',
        "if False:",
        ["tests/test_kpis.py::test_a_pull_request_that_started_as_a_draft_is_measured_from_ready"],
    ),
    (
        "p90 is the median",
        "src/onloop/kpi.py",
        'out["p90_hours"] = round(_rank(values, 0.9), 2)',
        'out["p90_hours"] = round(statistics.median(values), 2)',
        ["tests/test_kpis.py::test_lead_time_is_the_median_and_p90_of_open_to_merge_in_hours"],
    ),
    (
        "lead time ignores the window",
        "src/onloop/kpi.py",
        'if merged is None or not (lo <= merged["at"] < hi):',
        "if merged is None:",
        ["tests/test_kpis.py::test_the_window_is_by_merge_time"],
    ),
    (
        "overlapping waits are counted twice",
        "src/onloop/kpi.py",
        "if end is None or a > end:",
        "if True:",
        ["tests/test_kpis.py::test_overlapping_waits_on_one_role_are_counted_once"],
    ),
    (
        "a review of an older head ends the wait",
        "src/onloop/verdicts.py",
        'e["payload"].get("commit") == e["payload"].get("head")',
        "True",
        ["tests/test_kpis.py::test_a_review_on_an_older_head_does_not_end_the_wait"],
    ),
    (
        "a harmless push restarts the wait",
        "src/onloop/kpi.py",
        'if e["payload"].get("substantive"):',
        "if True:",
        ["tests/test_kpis.py::test_a_harmless_push_does_not_restart_the_wait"],
    ),
    (
        "a draft waits on the reviewer",
        "src/onloop/kpi.py",
        'draft = bool(e["payload"].get("draft"))',
        "draft = False",
        [
            "tests/test_kpis.py::test_a_draft_waits_on_nobody_until_it_is_ready",
            "tests/test_kpis.py::test_a_draft_that_is_pushed_to_and_never_readied_waits_on_nobody",
        ],
    ),
    (
        "an open wait ends where it began",
        "src/onloop/kpi.py",
        "out.setdefault(role, []).append((a, b or end_of_time, what))",
        "out.setdefault(role, []).append((a, b or a, what))",
        ["tests/test_kpis.py::test_open_waits_run_to_the_end_of_the_window_and_the_window_clips"],
    ),
    (
        "the idle window is not clipped",
        "src/onloop/kpi.py",
        "clipped = [(max(a, lo), min(b, hi), what)",
        "clipped = [(a, b, what)",
        ["tests/test_kpis.py::test_open_waits_run_to_the_end_of_the_window_and_the_window_clips"],
    ),
    (
        "an approved head does not wait on the gate",
        "src/onloop/kpi.py",
        'if state == "APPROVED":',
        "if False:",
        ["tests/test_kpis.py::test_an_approved_head_waits_on_the_gate_until_it_merges_or_is_refused"],
    ),
    (
        "a slot request waits on nobody",
        "src/onloop/kpi.py",
        'ended.setdefault(e["payload"].get("request"), e["at"])\n    for e in store.events(kind="slot.requested")',
        'ended.setdefault(e["payload"].get("request"), e["at"])\n    for e in []',
        ["tests/test_kpis.py::test_a_slot_request_waits_on_its_approver_until_granted_or_expired"],
    ),
    (
        "the revert rate has the wrong denominator",
        "src/onloop/kpi.py",
        "len(reverted) / len(merges)",
        "len(reverted) / max(1, len(reverted))",
        ["tests/test_kpis.py::test_reverts_are_counted_over_merges_with_both_numbers_shown"],
    ),
    (
        "the full anchor drops the hard stops",
        "src/onloop/anchor.py",
        'lines.append("Only a person decides: " + ", ".join(hard_stops(policy)) + ".")',
        "pass",
        [
            "tests/test_anchor.py::test_the_full_anchor_has_the_direction_the_role_the_measures_the_stops_and_the_next_item"
        ],
    ),
    (
        "the short anchor drops the question",
        "src/onloop/anchor.py",
        "out = text + stops + tail",
        "out = text + stops",
        ["tests/test_anchor.py::test_the_short_anchor_keeps_the_question_when_it_has_to_cut"],
    ),
    (
        "the short anchor has no fallback for many stops",
        "src/onloop/anchor.py",
        "if tokens(out) > budget:",
        "if False:",
        [
            "tests/test_anchor.py::test_a_policy_with_very_many_hard_stops_still_gets_a_short_anchor_inside_the_budget"
        ],
    ),
    (
        "a class the reviewer may approve is a hard stop",
        "src/onloop/anchor.py",
        'if approver == "human")',
        "if True)",
        ["tests/test_anchor.py::test_the_hardstop_anchor_lists_the_classes_only_a_person_may_approve"],
    ),
    (
        "the stop anchor never finds an idle role",
        "src/onloop/anchor.py",
        "idle = [r for r in led if ledger_mod.next_item(ledger, r) is None]",
        "idle = []",
        ["tests/test_anchor.py::test_the_stop_anchor_names_the_led_roles_with_nothing_queued"],
    ),
    (
        "the anchor module can read the store",
        "src/onloop/anchor.py",
        "import math\n",
        "import math\nfrom . import store\n",
        ["tests/test_anchor.py::test_an_anchor_is_a_function_of_the_policy_and_the_ledger_only"],
    ),
    (
        "every role gets the per-message hook",
        "src/onloop/hooks.py",
        'if mode == "every_message":',
        "if True:",
        [
            "tests/test_anchor.py::test_hooks_weave_the_four_moments_and_the_per_message_one_only_for_a_coordinating_role"
        ],
    ),
    (
        "a hook command does not quote its paths",
        "src/onloop/hooks.py",
        '"--policy",\n                shlex.quote(policy_path)',
        '"--policy",\n                policy_path',
        ["tests/test_anchor.py::test_a_hook_command_quotes_what_it_was_given"],
    ),
    (
        "a bad anchor mode is accepted",
        "src/onloop/policy.py",
        'if spec.get("anchor", "session") not in ("session", "every_message"):',
        "if False:",
        ["tests/test_anchor.py::test_a_role_with_a_bad_anchor_leads_or_kpis_is_refused"],
    ),
    (
        "bad kpis are accepted",
        "src/onloop/policy.py",
        "if not (isinstance(kpis, dict) or",
        "if False and (isinstance(kpis, dict) or",
        ["tests/test_anchor.py::test_a_role_with_a_bad_anchor_leads_or_kpis_is_refused"],
    ),
    (
        "raising max_stop_minutes is not a loosening",
        "src/onloop/policy.py",
        'for name in ("max_stop_minutes", "max_defer_hours"):\n        was, now',
        "for name in ():\n        was, now",
        ["tests/test_policy_automation.py::test_loosening_the_automation_is_authority_widening"],
    ),
    (
        "a bad max_stop_minutes is accepted",
        "src/onloop/policy.py",
        'for name, low in (("max_stop_minutes", 0), ("max_defer_hours", 1)):',
        "for name, low in ():",
        ["tests/test_policy_automation.py::test_a_bad_automation_field_is_refused"],
    ),
    (
        "a User with the reviewers name is the reviewer",
        "src/onloop/verdicts.py",
        'identity.is_login(who, reviewer, "Bot")',
        "identity.is_login(who, reviewer)",
        ["tests/test_kpis.py::test_a_user_with_the_reviewers_name_is_not_the_reviewer"],
    ),
    (
        "any review ends the reviewer's wait",
        "src/onloop/verdicts.py",
        "if reviewer is None:",
        "if True:",
        [
            "tests/test_kpis.py::test_a_review_by_anyone_but_the_reviewer_neither_ends_the_wait_nor_starts_the_gates"
        ],
    ),
    (
        "reviewer identity compared as text",
        "src/onloop/verdicts.py",
        'identity.is_login(who, reviewer, "Bot")',
        "who == reviewer",
        [
            "tests/test_kpis.py::test_a_reviewer_name_without_a_type_matches_the_app_and_a_lookalike_login_does_not"
        ],
    ),
    (
        "the kpi command drops the reviewer",
        "src/onloop/cli.py",
        "approvers, reviewer = _approver_roles(loaded), loaded.reviewer",
        "approvers, reviewer = _approver_roles(loaded), None",
        ["tests/test_kpis.py::test_the_kpi_command_hands_the_policys_reviewer_to_the_waits"],
    ),
    (
        "idle drops the reviewer",
        "src/onloop/kpi.py",
        "slot_approvers=slot_approvers, reviewer=reviewer),",
        "slot_approvers=slot_approvers),",
        ["tests/test_kpis.py::test_the_kpi_command_hands_the_policys_reviewer_to_the_waits"],
    ),
    (
        "a deferral at the limit is flagged",
        "src/onloop/watchdog.py",
        "if waited > defer_hours:",
        "if waited >= defer_hours:",
        ["tests/test_ledger_watchdog.py::test_a_delivery_inside_the_limit_or_exactly_at_it_is_not_flagged"],
    ),
    (
        "every deferral is flagged at once",
        "src/onloop/watchdog.py",
        "if waited > defer_hours:",
        "if waited > -1:",
        ["tests/test_ledger_watchdog.py::test_a_delivery_inside_the_limit_or_exactly_at_it_is_not_flagged"],
    ),
    (
        "deferrals are never flagged",
        "src/onloop/watchdog.py",
        "if waited > defer_hours:",
        "if False:",
        ["tests/test_ledger_watchdog.py::test_a_delivery_deferred_past_the_limit_is_flagged_with_its_reason"],
    ),
    (
        "the intake self-test lets an unknown patch id be harmless",
        "src/onloop/intake.py",
        "substantive = (old_patch != new_patch) if known else True",
        "substantive = (old_patch != new_patch) if known else False",
        ["tests/test_selftest.py::test_every_new_checker_has_a_self_test_that_passes[intake]"],
    ),
    (
        "the anchor self-test drops the question from the short anchor",
        "src/onloop/anchor.py",
        "out = text + stops + tail",
        "out = text + stops",
        ["tests/test_selftest.py::test_every_new_checker_has_a_self_test_that_passes[anchor]"],
    ),
    (
        "the selftest command skips the outcome-line checker",
        "src/onloop/cli.py",
        '("turn outcome line", disposition_mod),',
        "",
        ["tests/test_selftest.py::test_the_selftest_command_runs_every_checker_and_exits_0"],
    ),
    (
        "whitespace is compared as -w would (inside the line too)",
        "src/onloop/gitutil.py",
        "if removed.strip() == added.strip():",
        'if removed.replace(b" ", b"") == added.replace(b" ", b""):',
        ["tests/test_delta.py::test_a_space_removed_inside_a_string_is_substantive"],
    ),
    (
        "whitespace is compared as -b would (runs collapsed)",
        "src/onloop/gitutil.py",
        "if removed.strip() == added.strip():",
        'if b" ".join(removed.split()) == b" ".join(added.split()):',
        ["tests/test_delta.py::test_two_spaces_reduced_to_one_inside_a_string_is_substantive"],
    ),
    (
        "a carriage return anywhere in the line counts as line ending",
        "src/onloop/gitutil.py",
        'if removed.rstrip(b"\\r") == added.rstrip(b"\\r"):',
        'if removed.replace(b"\\r", b"") == added.replace(b"\\r", b""):',
        ["tests/test_delta.py::test_the_line_comparison"],
    ),
    (
        "identical bytes are classed as a line-ending change",
        "src/onloop/gitutil.py",
        "if removed == added:",
        "if False:",
        ["tests/test_delta.py::test_the_line_comparison"],
    ),
    (
        "unequal removed and added counts are paired anyway",
        "src/onloop/gitutil.py",
        "if len(old) != len(new):",
        "if False:",
        ["tests/test_delta.py::test_an_unequal_number_of_removed_and_added_lines_is_substantive"],
    ),
    (
        "a rewritten line does not make the push substantive",
        "src/onloop/gitutil.py",
        ' or total["other"] or not entries',
        " or not entries",
        [
            "tests/test_delta.py::test_a_rewritten_line_is_substantive",
            "tests/test_delta.py::test_a_whitespace_change_beside_a_rewritten_line_in_the_same_file_is_substantive",
        ],
    ),
    (
        "an added, deleted or mode-changed file is not structural",
        "src/onloop/gitutil.py",
        "structural = len(plain) != len(entries)",
        "structural = False",
        [
            "tests/test_delta.py::test_a_new_empty_file_is_substantive",
            "tests/test_delta.py::test_a_mode_change_is_substantive",
            "tests/test_delta.py::test_a_whitespace_change_beside_a_new_empty_file_is_substantive",
            "tests/test_delta.py::test_a_whitespace_change_beside_a_mode_change_is_substantive",
        ],
    ),
    (
        "equal trees are not detected",
        "src/onloop/gitutil.py",
        '            == self._run("rev-parse", f"{new}^{{tree}}").strip()',
        '            != self._run("rev-parse", f"{new}^{{tree}}").strip()',
        ["tests/test_delta.py::test_a_push_with_the_same_tree_is_merge_only"],
    ),
    (
        "whitespace-only is never reported",
        "src/onloop/gitutil.py",
        'elif total["ws_only"]:',
        "elif False:",
        ["tests/test_delta.py::test_trailing_whitespace_is_whitespace_only"],
    ),
    (
        "indentation is never flagged",
        "src/onloop/gitutil.py",
        'if kind == "ws_only" and a[',
        "if False and a[",
        ["tests/test_delta.py::test_indentation_only_in_a_python_file_is_whitespace_only_and_flagged"],
    ),
    (
        "an extension is never indent-sensitive",
        "src/onloop/gitutil.py",
        'return any(base == s or (s.startswith(".") and base.endswith(s)) for s in indent_sensitive)',
        "return any(base == s for s in indent_sensitive)",
        ["tests/test_delta.py::test_indentation_only_in_a_python_file_is_whitespace_only_and_flagged"],
    ),
    (
        "a commit id is not validated",
        "src/onloop/gitutil.py",
        "if not isinstance(rev, str) or not _SHA.match(rev):",
        "if False:",
        ["tests/test_delta.py::test_a_commit_that_is_not_hex_is_refused_before_git_runs"],
    ),
    (
        "the file list is not capped",
        "src/onloop/gitutil.py",
        "sorted(files.items())[:FILES_SHOWN]",
        "sorted(files.items())[:FILES_SHOWN + 1]",
        ["tests/test_delta.py::test_the_files_listed_are_capped_and_the_total_is_kept"],
    ),
    (
        "a diff that does not match the listing is trusted",
        "src/onloop/gitutil.py",
        " or len(chunks) != len(entries):",
        " or False:",
        ["tests/test_delta.py::test_a_diff_that_does_not_match_the_raw_listing_is_substantive"],
    ),
    (
        "the helper drops the author filter",
        "src/onloop/verdicts.py",
        'return bool(who) and identity.is_login(who, reviewer, "Bot")',
        "return True",
        ["tests/test_verdicts.py::test_only_the_reviewer_app_as_a_bot_is_the_reviewer"],
    ),
    (
        "the helper lets a User with the reviewer's name through",
        "src/onloop/verdicts.py",
        'identity.is_login(who, reviewer, "Bot")',
        "identity.is_login(who, reviewer)",
        ["tests/test_verdicts.py::test_only_the_reviewer_app_as_a_bot_is_the_reviewer"],
    ),
    (
        "a comment counts as a verdict",
        "src/onloop/verdicts.py",
        "return state if state in VERDICT_STATES else None",
        "return state",
        ["tests/test_verdicts.py::test_only_approved_and_changes_requested_are_verdicts"],
    ),
    (
        "another kind of event counts as a verdict",
        "src/onloop/verdicts.py",
        'if e["kind"] != KIND or not by_reviewer(e, reviewer):',
        "if not by_reviewer(e, reviewer):",
        ["tests/test_verdicts.py::test_another_kind_of_event_is_not_a_verdict"],
    ),
    (
        "the actor is not read before the payload author",
        "src/onloop/verdicts.py",
        'return e.get("actor") or e["payload"].get("author")',
        'return e["payload"].get("author")',
        ["tests/test_verdicts.py::test_the_actor_is_read_before_the_payload_author"],
    ),
    (
        "a verdict of another head is current",
        "src/onloop/verdicts.py",
        'e["payload"].get("commit") == e["payload"].get("head")',
        "True",
        ["tests/test_verdicts.py::test_a_current_verdict_is_bound_to_the_head_it_was_submitted_on"],
    ),
    (
        "the standing verdict ignores the head",
        "src/onloop/verdicts.py",
        'verdict_state(e, reviewer) and e["payload"].get("commit") == head]',
        "verdict_state(e, reviewer)]",
        ["tests/test_verdicts.py::test_a_comment_on_the_new_head_is_not_a_standing_verdict_on_it"],
    ),
    (
        "the latest verdict is any review",
        "src/onloop/verdicts.py",
        "found = [e for e in events if verdict_state(e, reviewer)]",
        'found = [e for e in events if verdict_state(e, reviewer) or e["kind"] == KIND]',
        [
            "tests/test_verdicts.py::test_the_latest_verdict_is_the_last_one_by_the_reviewer_whatever_head_it_names"
        ],
    ),
    (
        "the verdicts are listed among the other reviews",
        "src/onloop/verdicts.py",
        'if e["kind"] == KIND and not verdict_state(e, reviewer)]',
        'if e["kind"] == KIND]',
        ["tests/test_verdicts.py::test_other_reviews_are_everything_that_is_not_the_reviewers_verdict"],
    ),
    (
        "changes requested ignores the head",
        "src/onloop/verdicts.py",
        '== "CHANGES_REQUESTED" and e["payload"].get("head") == head\n    ]',
        '== "CHANGES_REQUESTED" ]',
        [
            "tests/test_verdicts.py::test_changes_requested_counts_only_from_the_reviewer_and_only_on_that_head"
        ],
    ),
    (
        "the body parent outranks the sub-issue",
        "src/onloop/supervisor/github_issues.py",
        "    spawned_by = parent or body_parent(cfg, body)",
        "    spawned_by = body_parent(cfg, body) or parent",
        [
            "tests/test_supervisor_github_issues.py::test_parents_come_from_sub_issues_first_then_from_the_body"
        ],
    ),
    (
        "a pull request becomes a node",
        "src/onloop/supervisor/github_issues.py",
        'for i in issues if "pull_request" not in i)',
        "for i in issues)",
        ["tests/test_supervisor_github_issues.py::test_pull_requests_are_not_nodes"],
    ),
    # -- the outcome line (decision 0007) ----------------------------------------------------------------------
    (
        "two live leases may hold one session id",
        "src/onloop/overlay/schema.py",
        "CREATE UNIQUE INDEX one_live_session_lease",
        "CREATE INDEX one_live_session_lease",
        ["tests/test_overlay_schema_v6.py::test_one_live_lease_per_session_id"],
    ),
    (
        "a session lease overwrites the live one",
        "src/onloop/overlay/projections.py",
        '"INSERT INTO session_leases (id',
        '"INSERT OR REPLACE INTO session_leases (id',
        ["tests/test_overlay_schema_v6.py::test_one_live_lease_per_session_id"],
    ),
    (
        "a turn may get a second corrective delivery",
        "src/onloop/overlay/schema.py",
        "CREATE UNIQUE INDEX one_corrective_per_turn",
        "CREATE INDEX one_corrective_per_turn",
        ["tests/test_overlay_schema_v6.py::test_at_most_one_corrective_delivery_per_turn"],
    ),
    (
        "a carrier delivery overwrites one of the same id or cause",
        "src/onloop/overlay/projections.py",
        'conn.execute(f"INSERT INTO deliveries ({cols})',
        'conn.execute(f"INSERT OR REPLACE INTO deliveries ({cols})',
        [
            "tests/test_overlay_schema_v6.py::test_at_most_one_corrective_delivery_per_turn",
            "tests/test_overlay_schema_v6.py::test_one_delivery_per_cause",
        ],
    ),
    (
        "version 5's source check takes the carrier's sources",
        "src/onloop/overlay/schema.py",
        'CHECK ({_in("source", SOURCES_5)}),',
        'CHECK ({_in("source", SOURCES)}),',
        [
            "tests/test_overlay_schema_v6.py::test_a_version_5_ledger_migrates_to_version_6_and_keeps_every_event"
        ],
    ),
    (
        "a retried delivery keeps the failed attempt's times",
        "src/onloop/overlay/projections.py",
        '"delivered_at = CASE WHEN ? THEN NULL ELSE delivered_at END, "',
        '"delivered_at = delivered_at, "',
        [
            "tests/test_overlay_schema_v6.py::test_a_delivery_walks_its_states_and_a_retry_clears_the_failed_attempt"
        ],
    ),
    (
        "a carried role may name Bash",
        "src/onloop/carrier/config.py",
        'SHELL_TOOLS = ("bash", ',
        'SHELL_TOOLS = ("notbash", ',
        [
            "tests/test_carrier.py::test_a_role_that_names_a_shell_is_refused_with_a_pointer_to_a_future_decision"
        ],
    ),
    (
        "the agents listing keeps rows of unbound sessions",
        "src/onloop/carrier/listing.py",
        "if not isinstance(sid, str) or sid not in wanted:",
        "if not isinstance(sid, str):",
        [
            "tests/test_carrier.py::test_the_listing_filter_returns_nothing_about_unbound_sessions",
            "tests/test_carrier.py::test_reading_the_listing_keeps_no_trace_of_unbound_sessions",
        ],
    ),
    (
        "a delivery is acked only by a turn with a valid outcome",
        "src/onloop/carrier/deliveries.py",
        '"SELECT id FROM turns WHERE session_id = ? AND ended_at >= ? ORDER BY ended_at, last_seq LIMIT 1",',
        '"SELECT id FROM turns WHERE session_id = ? AND ended_at >= ? AND parse_error IS NULL ORDER BY ended_at, last_seq LIMIT 1",',
        ["tests/test_carrier.py::test_a_turn_without_an_outcome_line_still_acks_and_fails_nothing"],
    ),
    (
        "the kill switch does not end a live run",
        "src/onloop/carrier/runner.py",
        "why = hooks.stop_requested()",
        "why = None",
        [
            "tests/test_carrier.py::test_the_kill_switch_ends_a_live_run_within_five_seconds_and_releases_its_lease"
        ],
    ),
    (
        "overage does not pause the carrier",
        "src/onloop/carrier/stream.py",
        'overage=rl.get("isUsingOverage") is True,',
        "overage=False,",
        ["tests/test_carrier.py::test_overage_pauses_the_carrier_at_once"],
    ),
    (
        "the run inherits the CLAUDE and ANTHROPIC variables",
        "src/onloop/carrier/guards.py",
        "return {k: v for k, v in environ.items() if not k.upper().startswith(_PREFIXES)}",
        "return dict(environ)",
        ["tests/test_carrier.py::test_the_child_environment_holds_no_claude_or_anthropic_variable"],
    ),
    (
        "the carrier starts with a metered key",
        "src/onloop/carrier/guards.py",
        '    for key in METERED_KEYS:\n        if key in environ:\n            raise MeteredKeyRefused(\n                f"the carrier',
        '    for key in ():\n        if key in environ:\n            raise MeteredKeyRefused(\n                f"the carrier',
        ["tests/test_carrier.py::test_a_metered_key_stops_the_carrier_at_start"],
    ),
    (
        "a session held elsewhere is resumed",
        "src/onloop/carrier/loop.py",
        "if seen.held(role.session_id):",
        "if False:",
        ["tests/test_carrier.py::test_a_session_open_elsewhere_is_never_resumed"],
    ),
    (
        "a turn that answered a corrective gets another",
        "src/onloop/carrier/loop.py",
        "            if answered_corrective(led, r.name, turn):\n                continue",
        "            if False:\n                continue",
        ["tests/test_carrier.py::test_one_corrective_per_turn_then_the_coordinator_with_the_age"],
    ),
    (
        "a carrier never started acts",
        "src/onloop/carrier/loop.py",
        'if not st["enabled"]:',
        "if False:",
        [
            "tests/test_carrier.py::test_a_fresh_install_spawns_nothing_and_logs_what_it_would_deliver",
            "tests/test_carrier.py::test_opted_in_but_not_started_is_still_observe_only",
        ],
    ),
    (
        "a usage-limited run is acked from its result line",
        "src/onloop/carrier/loop.py",
        'answered = res.stream.result is not None and status not in ("usage_limited", "not_logged_in")',
        "answered = res.stream.result is not None",
        [
            "tests/test_carrier.py::test_the_usage_limit_pauses_until_its_reset_and_puts_the_delivery_back_uncounted"
        ],
    ),
    (
        "the role budget is ignored",
        "src/onloop/carrier/guards.py",
        "    if hour >= per_hour:",
        "    if False:",
        [
            "tests/test_carrier.py::test_the_role_budget_holds_deliveries",
            "tests/test_carrier.py::test_a_usage_warning_halves_the_rate",
        ],
    ),
    (
        "a message is stored unscrubbed",
        "src/onloop/carrier/messages.py",
        "text, n = scrub(check(body))",
        "text, n = check(body), 0",
        ["tests/test_carrier.py::test_a_message_is_scrubbed_kept_for_its_retention_then_only_its_hash"],
    ),
    (
        "a message never expires",
        "src/onloop/carrier/messages.py",
        "WHERE expired_at IS NULL AND expires_at <= ? ORDER BY id",
        "WHERE expired_at IS NULL AND expires_at <= '0' AND ? IS NOT NULL ORDER BY id",
        ["tests/test_carrier.py::test_a_message_is_scrubbed_kept_for_its_retention_then_only_its_hash"],
    ),
    (
        "a refused run counts as a success",
        "src/onloop/carrier/loop.py",
        '        if res.stream.denials():\n            return "refused"',
        '        if False:\n            return "refused"',
        ["tests/test_carrier.py::test_a_refused_tool_is_not_a_success_and_is_not_re_run"],
    ),
    (
        "a crash's unanswered delivery is not resent",
        "src/onloop/carrier/loop.py",
        'dlv.fail(\n                led,\n                [r["id"]],',
        'False and dlv.fail(\n                led,\n                [r["id"]],',
        [
            "tests/test_carrier.py::test_after_a_crash_a_delivery_with_a_stop_hook_turn_is_acked_and_one_without_is_resent"
        ],
    ),
    (
        "the compose box opens with no carrier configured",
        "src/onloop/report/views.py",
        '    if carrier_cfg is None:\n        return {"enabled": False, "why": MESSAGES_OFF, "command": None}',
        '    if carrier_cfg is None:\n        return {"enabled": True, "why": None, "command": None}',
        [
            "tests/test_carrier_ui_api.py::test_the_compose_box_is_open_only_when_a_message_can_go_somewhere",
            "tests/test_carrier_ui_api.py::test_without_a_carrier_the_conversation_says_messages_are_off_and_a_post_is_a_plain_404",
        ],
    ),
    (
        "the compose box opens for a role that opted out",
        "src/onloop/report/views.py",
        '    if "message" not in opted:',
        "    if False:",
        ["tests/test_carrier_ui_api.py::test_the_compose_box_is_open_only_when_a_message_can_go_somewhere"],
    ),
    (
        "a message with no carrier gets the internal route error",
        "src/onloop/daemon/routes.py",
        '        if path.startswith("/messages/"):',
        "        if False:",
        [
            "tests/test_carrier_ui_api.py::test_without_a_carrier_the_conversation_says_messages_are_off_and_a_post_is_a_plain_404"
        ],
    ),
]


def run_tests(selectors: list[str]) -> bool:
    """True when the tests pass."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *selectors],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return p.returncode == 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--only", help="run only mutations whose label contains this text")
    args = ap.parse_args(argv)
    chosen = [m for m in MUTATIONS if not args.only or args.only in m[0]]
    if args.list:
        for label, path, *_ in chosen:
            print(f"{path}: {label}")
        return 0
    bad = 0
    for label, path, old, new, selectors in chosen:
        f = ROOT / path
        original = f.read_bytes()
        text = original.decode("utf-8")
        if text.count(old) != 1:
            print(f"CANNOT APPLY  {label}: the old text occurs {text.count(old)} times in {path}")
            bad += 1
            continue
        if not run_tests(selectors):
            print(f"BASELINE FAILS {label}: the tests fail before the mutation")
            bad += 1
            continue
        try:
            f.write_bytes(text.replace(old, new).encode("utf-8"))
            survived = run_tests(selectors)
        finally:
            f.write_bytes(original)
        print(f"{'SURVIVED' if survived else 'caught  '}  {label}")
        bad += survived
    print(f"{len(chosen) - bad} of {len(chosen)} mutations caught")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
