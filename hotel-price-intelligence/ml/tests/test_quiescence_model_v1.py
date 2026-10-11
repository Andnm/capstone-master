"""audit_tools/quiescence_model_v1.py — mo hinh THUAN cua kiem tra quiescence (thiet ke, chua noi vao DB). Test gom hai phan-vi-du GPT 66 §5 (DB=NULL; ket noi khong nam trong lich su poll), bay control GPT 68 §4
(protected DB + pid, BUSY->CLEAR, dong ho NaN/Inf) va cac nhanh fail-closed."""
from __future__ import annotations

import importlib.util
import math
import random
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
spec = importlib.util.spec_from_file_location("quiescence_model_v1", TOOLS / "quiescence_model_v1.py")
qm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qm)

APP, BASELINE, PROBE_ID, UP0 = "hotel_app", 1000, 5000, 500.0
SCOPE, PIDS, PROTECTED = ("warehouse_dsdev_src", "warehouse_dsdev_20261009_3src_nf"), (4242, 4243), ("hotel_price_intel", "hotel_price_intel_fullscan", "warehouse_20261004_3src")
GOOD_SELFTEST = {k: True for k in qm.SELFTEST_KEYS}


def row(session_id, user=APP, db=None, pid=None):
    return {"PROCESSLIST_ID": session_id, "PROCESSLIST_USER": user, "PROCESSLIST_DB": db, "pid": pid}


def probe(rows, at, *, status="ok", truncated=False, rolling=(), uptime=None):
    return {"status": status, "rows": list(rows), "truncated": truncated, "rolling_back": None if rolling is None else list(rolling), "observed_at": at, "uptime_s": UP0 + at if uptime is None else uptime}


def verdict(probes, *, selftest=GOOD_SELFTEST, exit_at=100.0, **over):
    kwargs = dict(selftest=selftest, probes=probes, child_exit_monotonic=exit_at, baseline_thread_id=BASELINE, baseline_uptime_s=UP0, app_user=APP, db_scope=SCOPE, child_pids=PIDS,
                  probe_session_id=PROBE_ID, protected_dbs=PROTECTED)
    kwargs.update(over)
    return qm.evaluate_quiescence(**kwargs)


def clear_pair(extra_rows=()):
    return [probe(extra_rows, 105.0), probe(extra_rows, 120.0)]


def test_two_empty_probes_after_the_child_exit_with_a_good_selftest_is_verified_and_assumptions_are_carried():
    result = verdict(clear_pair())
    assert result["status"] == qm.VERIFIED and result["reasons"] == [] and result["kill_candidates"] == [] and result["assumptions"] == list(qm.ASSUMPTIONS)


# --- hai vi du GPT 66 §5
def test_gpt_counterexample_1_a_session_with_no_default_database_blocks_quiescence_but_is_not_a_kill_candidate_without_pid_evidence():
    result = verdict(clear_pair([row(1500, db=None)]))
    assert result["status"] == qm.NOT_QUIESCENT and result["kill_candidates"] == []                      # F(a) la false alarm duoc: chan, khong cap quyen
    owned = verdict(clear_pair([row(1500, db=None, pid=4242)]))
    assert owned["status"] == qm.NOT_QUIESCENT and owned["kill_candidates"] == [1500]                    # user ung dung + pid trong cay + DB NULL => so huu duong


def test_gpt_counterexample_2_a_connection_that_was_never_registered_or_polled_is_still_found():
    result = verdict(clear_pair([row(1777, db=SCOPE[1], pid=4242)]))
    assert result["status"] == qm.NOT_QUIESCENT and result["kill_candidates"] == [1777]


def test_a_reconnect_gets_a_new_larger_id_and_is_found_in_any_scoped_database():
    for db in (SCOPE[0], SCOPE[1], None):
        assert verdict(clear_pair([row(BASELINE + 777, db=db)]))["status"] == qm.NOT_QUIESCENT


def test_rule_b_catches_a_session_that_switched_database_or_user_by_its_client_pid_but_never_nominates_it():
    for switched in (row(1600, db="some_other_db", pid=4243), row(1601, user="someone_else", db="x", pid=4242)):
        result = verdict(clear_pair([switched]))
        assert result["status"] == qm.NOT_QUIESCENT and result["kill_candidates"] == []                  # khop pid KHONG tu cap quyen KILL


def test_unrelated_sessions_are_ignored_the_crawler_other_users_and_sessions_older_than_the_baseline():
    rows = [row(2000, db="hotel_price_intel"), row(2001, db="hotel_price_intel_fullscan"), row(2002, user="other", db=None, pid=1), row(BASELINE, db=SCOPE[1]), row(5, db=None)]
    assert verdict(clear_pair(rows))["status"] == qm.VERIFIED


def test_the_probe_own_session_is_excluded_by_its_exact_id_only():
    assert verdict(clear_pair([row(PROBE_ID, db=None)]))["status"] == qm.VERIFIED
    assert verdict(clear_pair([row(PROBE_ID + 1, db=None)]))["status"] == qm.NOT_QUIESCENT


def test_a_missing_target_database_changes_nothing_because_the_filter_never_looks_it_up():
    assert verdict(clear_pair(), db_scope=("warehouse_dsdev_not_created_yet",))["status"] == qm.VERIFIED
    assert verdict(clear_pair([row(1900, db=None)]), db_scope=("warehouse_dsdev_not_created_yet",))["status"] == qm.NOT_QUIESCENT


# --- GPT 68 §4: bay control doc lap
def test_gpt68_control_protected_database_with_a_matching_pid_is_a_scope_violation_and_never_a_kill_candidate():
    for db in ("hotel_price_intel", "warehouse_20261004_3src"):
        result = verdict(clear_pair([row(1500, db=db, pid=4242)]))
        assert result["status"] == qm.NOT_QUIESCENT and result["kill_candidates"] == [] and "scope_violation_protected_database" in result["reasons"], db
    mixed = verdict(clear_pair([row(1500, db="hotel_price_intel", pid=4242), row(1501, db=None, pid=4242)]))
    assert mixed["status"] == qm.NOT_QUIESCENT and mixed["kill_candidates"] == []                        # co vi pham => khong de cu nao


def test_gpt68_control_busy_then_clear_has_no_candidates_from_the_earlier_probe():
    busy = probe([row(1500, db=None, pid=4242)], 105.0)
    result = verdict([busy, probe([], 120.0)])
    assert result["status"] == qm.NOT_QUIESCENT and result["kill_candidates"] == []
    assert verdict([busy, probe([], 120.0), probe([], 135.0)])["status"] == qm.VERIFIED


@pytest.mark.parametrize("bad_clock", [math.nan, math.inf, -math.inf, True, "120", None])
def test_gpt68_controls_non_finite_or_non_numeric_clocks_are_unknown_never_verified(bad_clock):
    for index in (0, 1):
        pair = clear_pair()
        pair[index] = dict(pair[index], observed_at=bad_clock)
        result = verdict(pair)
        assert result["status"] == qm.UNKNOWN and any("bad_clock" in r for r in result["reasons"]), (index, bad_clock)
    assert verdict(clear_pair(), exit_at=bad_clock)["status"] == qm.UNKNOWN


def test_candidates_come_only_from_a_valid_last_probe_not_from_truncated_or_noisy_observations():
    owned = [row(1500, db=None, pid=4242)]
    truncated_last = [probe([], 105.0), probe(owned, 120.0, truncated=True)]
    result = verdict(truncated_last)
    assert result["status"] == qm.NOT_QUIESCENT and result["kill_candidates"] == []
    rolling_last = [probe([], 105.0), probe(owned, 120.0, rolling=[{"trx": 9}])]
    assert verdict(rolling_last)["kill_candidates"] == []
    malformed_row = [probe([], 105.0), probe(owned + [{"PROCESSLIST_ID": "x"}], 120.0)]
    assert verdict(malformed_row)["kill_candidates"] == []
    assert verdict([probe([], 105.0), probe(owned, 120.0)])["kill_candidates"] == [1500]


def test_the_last_probe_nominates_only_what_it_still_sees_even_if_the_previous_one_saw_more():
    first = probe([row(1500, db=None, pid=4242), row(1501, db=None, pid=4242)], 105.0)
    second = probe([row(1501, db=None, pid=4242)], 120.0)
    assert verdict([first, second])["kill_candidates"] == [1501]


# --- uptime / vong doi server
def test_a_missing_or_decreasing_uptime_means_the_connection_id_assumption_cannot_be_trusted():
    assert verdict([probe([], 105.0, uptime=None), dict(probe([], 120.0), uptime_s=None)])["status"] == qm.UNKNOWN
    restarted = [probe([], 105.0, uptime=10.0), probe([], 120.0, uptime=25.0)]                              # < baseline: server khoi dong lai
    assert verdict(restarted)["status"] == qm.UNKNOWN
    shrinking = [probe([], 105.0, uptime=700.0), probe([], 120.0, uptime=690.0)]
    assert verdict(shrinking)["status"] == qm.UNKNOWN
    assert verdict(clear_pair(), baseline_uptime_s=None)["status"] == qm.UNKNOWN
    assert verdict(clear_pair(), baseline_uptime_s=math.nan)["status"] == qm.UNKNOWN
    assert verdict([probe([], 105.0, uptime=math.inf), probe([], 120.0)])["status"] == qm.UNKNOWN


# --- fail-closed
@pytest.mark.parametrize("selftest", [None, {}, {**GOOD_SELFTEST, "visible_db_null_session": False}, {**GOOD_SELFTEST, "pid_attribute_visible": False}, {**GOOD_SELFTEST, "removed_after_close": None},
                                      {**GOOD_SELFTEST, "innodb_trx_readable": 1}, {k: v for k, v in GOOD_SELFTEST.items() if k != "visible_other_session"}])
def test_a_missing_or_incomplete_selftest_is_unknown(selftest):
    assert verdict(clear_pair(), selftest=selftest)["status"] == qm.UNKNOWN


def test_one_probe_probes_too_close_or_before_the_child_exit_are_unknown():
    assert verdict([probe([], 120.0)])["status"] == qm.UNKNOWN
    assert verdict([probe([], 105.0), probe([], 114.9)])["status"] == qm.UNKNOWN
    assert verdict([probe([], 105.0), probe([], 115.0)])["status"] == qm.VERIFIED
    assert verdict([probe([], 99.0), probe([], 120.0)])["status"] == qm.UNKNOWN
    assert verdict([probe([], 100.0), probe([], 120.0)])["status"] == qm.UNKNOWN                      # bang dung luc child chet cung chua du
    assert verdict(clear_pair(), exit_at=None)["status"] == qm.UNKNOWN


@pytest.mark.parametrize("bad", [probe([], 105.0, status="error"), probe([], 105.0, status="timeout"), probe([], 105.0, truncated=True), probe([], 105.0, truncated=None),
                                 probe([row(1, db=None)] * qm.PROBE_ROW_LIMIT, 105.0), probe([], 105.0, rolling=None), probe([], 105.0, rolling=[{"trx": 1}]),
                                 {"status": "ok", "rows": "oops", "truncated": False, "rolling_back": [], "observed_at": 105.0, "uptime_s": 605.0}])
def test_any_probe_error_truncation_or_unreadable_rollback_state_is_not_verified(bad):
    for pair in ([dict(bad, observed_at=105.0), probe([], 120.0)], [probe([], 105.0), dict(bad, observed_at=120.0, uptime_s=620.0)]):
        assert verdict(pair)["status"] != qm.VERIFIED


@pytest.mark.parametrize("bad_row", [{"PROCESSLIST_ID": 1500}, row(True), row(-3), row("1500"), row(1500, user=7), row(1500, db=3), row(1500, pid=True), row(1500, pid="4242"), "not a mapping"])
def test_a_malformed_row_makes_the_probe_unknown_never_clear(bad_row):
    result = verdict([probe([bad_row], 105.0), probe([], 120.0)])
    assert result["status"] == qm.UNKNOWN


@pytest.mark.parametrize("over", [{"baseline_thread_id": -1}, {"baseline_thread_id": True}, {"app_user": ""}, {"app_user": None}, {"probe_session_id": "5000"}, {"child_pids": ["4242"]},
                                  {"db_scope": [None]}, {"protected_dbs": [3]}, {"db_scope": ("hotel_price_intel",)}])
def test_bad_or_overlapping_scope_arguments_are_unknown(over):
    assert verdict(clear_pair(), **over)["status"] == qm.UNKNOWN


@pytest.mark.parametrize("probes,reason", [(None, "need_two_probes"), (7, "need_two_probes"), ("ab", "need_two_probes"), ([], "need_two_probes"), ([probe([], 105.0)], "need_two_probes"),
                                           ([None, None], "probe_not_a_mapping"), (["a", "b"], "probe_not_a_mapping"), ([probe([], 105.0), None], "probe_not_a_mapping")])
def test_malformed_probe_lists_never_raise_and_are_unknown_with_the_specific_reason(probes, reason):
    result = verdict(probes)
    assert result["status"] == qm.UNKNOWN and result["reasons"] == [reason], result["reasons"]                      # ly do cu the, khong phai 'exception' chung


def test_ownership_is_false_for_a_protected_database_even_before_the_violation_gate_applies():
    result = qm.classify_probe(probe([row(1500, db="hotel_price_intel", pid=4242), row(1501, db=None, pid=4242)], 105.0), baseline_thread_id=BASELINE, app_user=APP, db_scope=SCOPE,
                               child_pids=PIDS, probe_session_id=PROBE_ID, protected_dbs=PROTECTED)
    owned = {m["id"]: m["owned"] for m in result["matches"]}
    assert owned == {1500: False, 1501: True} and result["violations"] == [1500]
    overlapping = qm.classify_probe(probe([row(1500, db="hotel_price_intel", pid=4242)], 105.0), baseline_thread_id=BASELINE, app_user=APP, db_scope=SCOPE + ("hotel_price_intel",),
                                    child_pids=PIDS, probe_session_id=PROBE_ID, protected_dbs=PROTECTED)                      # cau hinh sai (scope trung protected) van KHONG cap so huu: protected thang
    assert overlapping["matches"][0]["owned"] is False and overlapping["violations"] == [1500]


def test_unexpected_exceptions_are_unknown_never_optimistic():
    class Boom:
        def __iter__(self):
            raise RuntimeError("boom")
    result = verdict(clear_pair(), child_pids=Boom())
    assert result["status"] == qm.UNKNOWN and result["reasons"] == ["exception: RuntimeError"]


def test_property_no_scoped_session_after_the_baseline_can_ever_be_verified_and_candidates_are_always_owned_and_unprotected():
    rng = random.Random(20261009)
    for _ in range(4000):
        rows, blocking = [], False
        for _ in range(rng.randint(0, 6)):
            sid = rng.choice([rng.randint(1, BASELINE), rng.randint(BASELINE + 1, BASELINE + 4000)])
            user = rng.choice([APP, APP, "crawler_x"])
            db = rng.choice([None, SCOPE[0], SCOPE[1], PROTECTED[0], PROTECTED[2], "x"])
            pid = rng.choice([None, 4242, 99])
            if sid != PROBE_ID and sid > BASELINE:
                blocking = blocking or (user == APP and (db is None or db in SCOPE)) or pid in PIDS
            rows.append(row(sid, user=user, db=db, pid=pid))
        result = verdict(clear_pair(rows))
        if blocking:
            assert result["status"] != qm.VERIFIED, rows
        else:
            assert result["status"] == qm.VERIFIED, rows
        for candidate in result["kill_candidates"]:
            r = next(x for x in rows if x["PROCESSLIST_ID"] == candidate)
            assert r["PROCESSLIST_USER"] == APP and r["pid"] in PIDS and r["PROCESSLIST_DB"] not in PROTECTED and (r["PROCESSLIST_DB"] is None or r["PROCESSLIST_DB"] in SCOPE) and candidate > BASELINE


def test_property_random_clocks_and_uptimes_never_produce_an_optimistic_verdict():
    rng = random.Random(7)
    clocks = [105.0, 120.0, math.nan, math.inf, -math.inf, True, False, None, "x", 99.0, 114.0, 1e308]
    for _ in range(2000):
        first, second, exit_at = rng.choice(clocks), rng.choice(clocks), rng.choice(clocks + [100.0])
        ups = [rng.choice(clocks + [700.0]), rng.choice(clocks + [800.0])]
        probes = [dict(probe([], 0), observed_at=first, uptime_s=ups[0]), dict(probe([], 0), observed_at=second, uptime_s=ups[1])]
        result = verdict(probes, exit_at=exit_at)
        valid = (all(type(x) in (int, float) and math.isfinite(x) for x in (first, second, exit_at, *ups)) and first > exit_at and second > exit_at and second - first >= qm.MIN_SPACING_S
                 and ups[0] >= UP0 and ups[1] >= ups[0])
        assert (result["status"] == qm.VERIFIED) == valid, (first, second, exit_at, ups)
