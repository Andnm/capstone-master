"""Test thuần cho scripts/n1_rollout_monitor.py (G4): cửa sổ/ranh giới, múi giờ, mẫu số, ngưỡng biên, NOT_READY, hotel policy, whitelist chỉ-đọc. Không DB."""
import importlib.util
from datetime import datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "n1_rollout_monitor.py"
spec = importlib.util.spec_from_file_location("n1_monitor_under_test", SCRIPT)
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)
TH = M.THRESHOLDS


def run(i, day_utc, hour_utc, version, status="completed"):
    return {"id": i, "status": status, "started_at": datetime(2026, 10, day_utc, hour_utc, 0), "scraper_version": version}


def old_runs(first_day=1, n=10):                     # 1 run/ngày, 17:30Z => ngày VN kế tiếp
    return [run(i, first_day + i - 1, 17, "2.3.0") for i in range(1, n + 1)]


def test_version_tuple_is_numeric_not_lexical():
    assert M.version_tuple("2.3.10") > M.version_tuple("2.3.9") and M.version_tuple("2.3.1") > M.version_tuple("2.3.0") and M.version_tuple(None) == ()


def test_vn_day_uses_utc_plus_7():
    assert M.vn_day(datetime(2026, 10, 12, 17, 28)).isoformat() == "2026-10-13"          # run #70 thực tế
    assert M.vn_day(datetime(2026, 10, 12, 16, 59)).isoformat() == "2026-10-12"


def test_windows_before_is_last_7_crawl_days_not_7_runs_and_after_needs_3_days_and_3_runs():
    runs = old_runs() + [run(11, 11, 17, "2.3.1"), run(12, 12, 17, "2.3.1"), run(13, 13, 17, "2.3.1")]
    w = M.select_windows(runs, "2.3.1", TH["before_days"], TH["min_after_days"], TH["min_after_runs"])
    assert w["status"] == "READY" and w["boundary_run_id"] == 11
    assert w["before_run_ids"] == [4, 5, 6, 7, 8, 9, 10] and len(w["before_days"]) == 7 and w["after_run_ids"] == [11, 12, 13]
    two_runs_per_day = old_runs(n=4) + [run(5, 4, 5, "2.3.0")]                        # 2 run cùng ngày VN: 7 ngày ≠ 7 run
    w2 = M.select_windows(two_runs_per_day + [run(20, 10, 17, "2.3.1"), run(21, 11, 17, "2.3.1"), run(22, 12, 17, "2.3.1")], "2.3.1", 7, 3, 3)
    assert len(w2["before_run_ids"]) == 5 and len(w2["before_days"]) == 4


@pytest.mark.parametrize("new,reason", [
    ([], "no_new_code_run"),
    ([run(11, 11, 17, "2.3.1"), run(12, 12, 17, "2.3.1")], "after_days<3"),                           # 2 ngày
    ([run(11, 11, 17, "2.3.1"), run(12, 11, 18, "2.3.1"), run(13, 11, 19, "2.3.1")], "after_days<3"),   # 3 run nhưng 1 ngày VN
])
def test_not_ready_cases(new, reason):
    w = M.select_windows(old_runs() + new, "2.3.1", 7, 3, 3)
    assert w["status"] == "NOT_READY" and reason in w["reasons"]
    assert M.overall_status(w, {}, [])["status"] == "NOT_READY"


def test_not_ready_when_fewer_than_3_new_runs_even_with_3_days():
    w = M.select_windows(old_runs() + [run(11, 11, 17, "2.3.1"), run(12, 12, 17, "2.3.1")], "2.3.1", 7, 3, 3)
    assert w["status"] == "NOT_READY" and "after_runs<3" in w["reasons"]


def test_no_before_window_and_non_completed_and_post_boundary_old_version():
    w = M.select_windows([run(1, 11, 17, "2.3.1"), run(2, 12, 17, "2.3.1"), run(3, 13, 17, "2.3.1")], "2.3.1", 7, 3, 3)
    assert w["status"] == "NOT_READY" and "no_before_window_run" in w["reasons"]
    runs = old_runs() + [run(11, 11, 17, "2.3.1"), run(12, 12, 17, "2.3.1", status="failed"), run(13, 13, 17, "2.3.1"), run(14, 14, 17, "2.3.1"),
                         run(15, 15, 17, "2.3.0")]                                                    # rollback về 2.3.0 sau ranh giới
    w = M.select_windows(runs, "2.3.1", 7, 3, 3)
    assert 12 not in w["after_run_ids"] and 15 not in w["before_run_ids"] and 15 not in w["after_run_ids"] and w["post_boundary_old_version_runs"] == [15]


@pytest.mark.parametrize("b0,b1,flagged", [((10, 30), (16, 30), True), ((10, 30), (15, 30), False), ((16, 30), (10, 30), True),   # |delta| đúng 0,20 / giảm cũng gắn cờ
                                           ((10, 30), (15, 29), None), ((10, 29), (20, 30), None)])
def test_breakfast_threshold_and_denominator_boundaries(b0, b1, flagged):
    r = M.evaluate_hotel(b0, b1, (0, 25), (0, 25))
    if flagged is None:
        assert r["status"] == "INSUFFICIENT_SAMPLE" and r["breakfast"]["delta"] is None
    else:
        assert (r["status"] == "FLAG_BREAKFAST") is flagged


@pytest.mark.parametrize("u0,u1,flagged", [((5, 20), (8, 20), True), ((5, 20), (7, 20), False), ((8, 20), (5, 20), False),            # chỉ chiều TĂNG
                                           ((5, 20), (8, 19), None), ((5, 19), (20, 20), None)])
def test_unavailable_threshold_direction_and_denominator_boundaries(u0, u1, flagged):
    r = M.evaluate_hotel((15, 30), (15, 30), u0, u1)
    if flagged is None:
        assert r["status"] == "INSUFFICIENT_SAMPLE"
    else:
        assert (r["status"] == "FLAG_UNAVAILABLE") is flagged


def test_flag_both_missing_windows_and_insufficient_is_never_ok():
    assert M.evaluate_hotel((10, 30), (20, 30), (5, 20), (10, 20))["status"] == "FLAG_BOTH"
    assert M.evaluate_hotel(None, (20, 30), (5, 20), (5, 20))["status"] == "INSUFFICIENT_SAMPLE"
    assert M.evaluate_hotel((10, 30), (10, 30), (5, 20), (5, 20))["status"] == "OK"


def test_overall_separates_policy_hotels_and_states_insufficient_is_not_zero_impact():
    w = {"status": "READY", "reasons": []}
    hotels = {"starview-villa": M.evaluate_hotel((0, 40), (30, 40), (0, 25), (0, 25)),            # policy: exposure đã biết
              "new-hotel": M.evaluate_hotel((0, 40), (30, 40), (0, 25), (0, 25)),
              "quiet": M.evaluate_hotel((10, 40), (10, 40), (5, 25), (5, 25)), "small": M.evaluate_hotel((1, 5), (1, 5), (0, 3), (0, 3))}
    o = M.overall_status(w, hotels, ["starview-villa"])
    assert o["status"] == "READY_FLAGS_FOUND" and o["new_flags"] == {"new-hotel": "FLAG_BREAKFAST"}
    assert "starview-villa" in o["policy_hotels_reported_separately"] and o["hotels_evaluable"] == 3 and o["hotels_insufficient_sample"] == ["small"]
    assert "not evidence of zero impact" in o["note"]
    clean = {"quiet": hotels["quiet"], "small": hotels["small"]}
    assert M.overall_status(w, clean, [])["status"] == "READY_NO_NEW_FLAGS"


# ---------------- chỉ đọc ----------------
class FakeCursor:
    def __init__(self, rows_by_marker):
        self.rows_by_marker, self.last = rows_by_marker, []

    def execute(self, sql, params=()):
        self.last = next((rows for marker, rows in self.rows_by_marker.items() if marker in sql), [])

    def fetchall(self):
        return self.last


def test_whitelist_rejects_writes_file_io_locking_arbitrary_selects_and_tampered_templates():
    cursor = FakeCursor({})
    session = M.ReadOnlySession(cursor, 1000)
    n_before = len(session.issued)
    bad = ["INSERT INTO t VALUES (1)", "UPDATE t SET a=1", "DELETE FROM t", "DROP TABLE t", "SELECT 1; DROP TABLE t", "TRUNCATE t", "SET SESSION sql_mode=''",
           "SELECT 1", "SELECT * FROM crawl_runs", "SELECT 1 INTO OUTFILE 'not-executed'", "SELECT 1 INTO DUMPFILE 'not-executed'", "SELECT 1 INTO @v",
           M.SQL_RUNS + " FOR UPDATE", M.SQL_RUNS + " LOCK IN SHARE MODE", "SELECT SLEEP(10)", M.SQL_RUNS + " -- x", M.SQL_RUNS + " /* x */", M.SQL_RUNS + " # x",
           M.SQL_RUNS.replace("scraper_version", "scraper_version, git_commit"),                                        # thêm cột: không còn là template
           M.SQL_BREAKFAST.format(ph="%s") + " INTO OUTFILE 'x'", M.SQL_BREAKFAST.format(ph="%s").replace("GROUP BY hotel_id", "OR 1=1 GROUP BY hotel_id"),
           "SET SESSION MAX_EXECUTION_TIME=0", "SET SESSION MAX_EXECUTION_TIME=-5", "SET SESSION MAX_EXECUTION_TIME=1000000", "COMMIT; DROP TABLE t"]
    for sql in bad:
        with pytest.raises(RuntimeError):
            session.execute(sql)
    assert len(session.issued) == n_before and not cursor.last                                                         # không câu nào tới cursor
    with pytest.raises(RuntimeError):
        session.execute(M.SQL_BREAKFAST.format(ph="%s,%s"), ["a", "b"])                                                # tham số phải là int
    with pytest.raises(RuntimeError):
        session.execute(M.SQL_BREAKFAST.format(ph="%s,%s"), [1])                                                       # placeholder != tham số
    session.execute(M.SQL_RUNS)
    session.execute(M.SQL_BREAKFAST.format(ph="%s,%s"), [1, 2])
    session.execute(M.SQL_UNAVAILABLE.format(ph="%s"), [3])
    assert session.issued[0].startswith("SET SESSION MAX_EXECUTION_TIME=") and session.issued[1] == "START TRANSACTION READ ONLY"


@pytest.mark.parametrize("bad_ms", [0, -1, 600001, True, False, "5000", None, 1.5])
def test_timeout_must_be_a_positive_bounded_integer_and_is_rejected_before_any_execute(bad_ms):
    cursor = FakeCursor({})
    executed = []
    cursor.execute = lambda *a, **k: executed.append(a)
    with pytest.raises(ValueError):
        M.ReadOnlySession(cursor, bad_ms)
    assert executed == []
    M.ReadOnlySession(FakeCursor({}), 1)
    M.ReadOnlySession(FakeCursor({}), M.MAX_MS_LIMIT)


def test_run_monitor_end_to_end_with_fake_cursor_issues_only_read_only_sql():
    runs = [(r["id"], r["status"], r["started_at"], r["scraper_version"]) for r in old_runs() + [run(11, 11, 17, "2.3.1"), run(12, 12, 17, "2.3.1"), run(13, 13, 17, "2.3.1")]]
    cursor = FakeCursor({"FROM crawl_runs": runs, "breakfast_included=1": [("h1", 10, 40), ("starview-villa", 0, 40)], "reference_match_status='unavailable'": [("h1", 5, 25)]})
    session = M.ReadOnlySession(cursor, 5000)
    out = M.run_monitor(session, boundary_version="2.3.1", policy_hotels=["starview-villa"])
    session.close()
    assert out["windows"]["status"] == "READY" and "h1" in out["hotels"] and out["overall"]["note"]
    assert all(M.sql_is_allowed(s) and ";" not in s for s in session.issued)
    assert not any(w in s.upper() for s in session.issued for w in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"))
    assert session.issued[-1] == "COMMIT"


def test_sql_text_encodes_the_locked_denominators():
    session = M.ReadOnlySession(FakeCursor({}), 1000)
    M.collect(session, [1, 2], "breakfast")
    M.collect(session, [1, 2], "unavailable")
    bf_sql, un_sql = session.issued[-2], session.issued[-1]
    for needle in ("price_per_night IS NOT NULL", "is_sold_out=0", "availability_status='available'", "breakfast_included IS NOT NULL"):
        assert needle in bf_sql
    assert "status='success'" in un_sql and all(f"'{s}'" in un_sql for s in ("exact", "alias", "unavailable", "ambiguous")) and "'calibrating'" not in un_sql


def test_policy_load_reports_status_and_never_silently_empty(tmp_path):
    hotels, status = M.load_policy_hotels(M.DEFAULT_POLICY)
    assert hotels == ["chez-mimosa-ho-chi-minh", "chez-van", "ks-huy-hoang-airport", "mila-homestay", "starview-villa"] and status["status"] == "ok" and status["count"] == 5
    assert M.load_policy_hotels(tmp_path / "nope.json")[1]["status"] == "missing_or_unreadable"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert M.load_policy_hotels(bad) == ([], {"status": "invalid_json", "path": str(bad), "error": "JSONDecodeError"})
    no_key = tmp_path / "nokey.json"
    no_key.write_text('{"policy_version": "x"}', encoding="utf-8")
    assert M.load_policy_hotels(no_key)[1]["status"] == "no_excluded_hotels_list"
    wrong_type = tmp_path / "wrong.json"
    wrong_type.write_text('{"excluded_hotels": [1, 2]}', encoding="utf-8")
    assert M.load_policy_hotels(wrong_type)[1]["status"] == "no_excluded_hotels_list"


def test_overall_warns_when_policy_could_not_be_loaded():
    ready = {"status": "READY", "reasons": []}
    hotels = {"h": M.evaluate_hotel((0, 40), (30, 40), (0, 25), (0, 25))}
    missing = {"status": "missing_or_unreadable", "path": "x", "error": "FileNotFoundError"}
    assert "policy_warning" in M.overall_status(ready, hotels, [], missing)
    assert "policy_warning" in M.overall_status({"status": "NOT_READY", "reasons": ["no_new_code_run"]}, {}, [], missing)
    assert "policy_warning" not in M.overall_status(ready, hotels, [], {"status": "ok"})
    assert "policy_warning" not in M.overall_status(ready, hotels, [])


def test_run_monitor_output_carries_policy_load_status():
    session = M.ReadOnlySession(FakeCursor({"FROM crawl_runs": []}), 1000)
    out = M.run_monitor(session, boundary_version="2.3.1", policy_hotels=[], policy_load={"status": "invalid_json", "path": "p", "error": "ValueError"})
    assert out["policy_load"]["status"] == "invalid_json" and "policy_warning" in out["overall"]
