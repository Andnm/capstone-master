"""Test tĩnh (không MySQL) cho rebuild_lib - chạy: cd hotel-price-intelligence/backend && venv/Scripts/python.exe -m pytest ../../outputs/warehouse-rebuild-20261004/tests -q"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rebuild_lib import compare_run_sets, compare_snapshots, diff_batch_provenance, sweep_overlaps, validate_cutoff  # noqa: E402

T = dt.datetime
D = dt.date


def run(source, rid, start, end):
    return {"source_code": source, "source_run_id": rid, "started_at": start, "finished_at": end}


def test_sweep_overlaps_catches_long_run_hidden_behind_a_short_middle_run():
    # A dài (1..20), B ngắn nằm trong A (2..3), C bắt đầu 10 < A.finished: cặp kề (B,C) KHÔNG chồng nhưng (A,C) có
    runs = [run("aux", 1, T(2026, 9, 1), T(2026, 9, 20)), run("aux", 2, T(2026, 9, 2), T(2026, 9, 3)), run("aux", 3, T(2026, 9, 10), T(2026, 9, 11))]
    found = sweep_overlaps(runs)
    assert {(o["run"], o["overlaps_run"]) for o in found} == {(2, 1), (3, 1)}


def test_sweep_overlaps_is_per_source_and_ignores_open_ended_runs():
    runs = [run("a", 1, T(2026, 9, 1), T(2026, 9, 5)), run("b", 1, T(2026, 9, 2), T(2026, 9, 3)), run("a", 2, T(2026, 9, 6), None),
            run("a", 3, T(2026, 9, 7), T(2026, 9, 8))]
    assert sweep_overlaps(runs) == []


def test_validate_cutoff_requires_full_source_coverage_and_matching_observed_date():
    cutoff = {"cutoff_vn_crawl_date": {"local_primary": "2026-10-04", "vps": "2026-10-04", "local_aux": "2026-10-04"}}
    observed = {"local_primary": D(2026, 10, 4), "vps": D(2026, 10, 4), "local_aux": D(2026, 10, 4)}
    assert validate_cutoff(cutoff, list(observed), observed) == []
    assert any("phủ" in p for p in validate_cutoff(cutoff, ["local_primary", "vps"], observed))          # thừa nguồn
    assert any("phủ" in p for p in validate_cutoff({"cutoff_vn_crawl_date": {"vps": "2026-10-04"}}, list(observed), observed))  # thiếu nguồn
    assert any("!=" in p for p in validate_cutoff(cutoff, list(observed), {**observed, "vps": D(2026, 10, 3)}))
    assert any("ISO" in p for p in validate_cutoff({"cutoff_vn_crawl_date": {"vps": "04/10"}}, ["vps"], {"vps": D(2026, 10, 4)}))
    assert validate_cutoff({}, ["vps"], {}) != []


def snapshot(runs=10, items=100, obs=1000, queued=0, latest=("completed", 61)):
    def t(n):
        return {"count": n, "max_id": n + 5}
    return {"operational_database": "hotel_price_intel", "crawl_runs": t(runs), "crawl_run_items": t(items), "price_observations": t(obs),
            "runs_queued_or_running": queued, "items_queued_or_running": 0, "latest_run": {"id": latest[1], "status": latest[0]}}


def test_strict_snapshot_compare_requires_equality_and_zero_active():
    assert compare_snapshots(snapshot(), snapshot(), strict=True) == []
    grew = compare_snapshots(snapshot(), snapshot(obs=1001), strict=True)
    assert any("price_observations.count" in p for p in grew) and any("strict" in p for p in grew)
    assert any("active" in p for p in compare_snapshots(snapshot(queued=1), snapshot(), strict=True))
    assert any("latest run" in p for p in compare_snapshots(snapshot(), snapshot(latest=("completed", 62)), strict=True))


def test_monotonic_mode_allows_growth_but_not_shrink():
    assert compare_snapshots(snapshot(), snapshot(obs=1500), strict=False) == []
    assert any("GIẢM" in p for p in compare_snapshots(snapshot(), snapshot(obs=900), strict=False))


def batch(**over):
    base = {"status": "pass", "setup_sql_sha256": "s", "source_manifest_sha256": "m", "cohort_manifest_sha256": "c", "ownership_manifest_sha256": "o",
            "etl_config_sha256": "e", "canonicalization_version": "v1", "canonicalization_git_commit": "abc", "canonicalization_config_sha256": "cc",
            "batch_id": "x", "warehouse_database": "wh_x", "started_at": 1, "finished_at": 2, "fail_reason": None, "notes": "n"}
    return {**base, **over}


def src(code, **over):
    base = {"source_code": code, "source_priority": 0, "dump_sha256": "d", "schema_sha256": "h", "dump_taken_at": T(2026, 10, 4, 14, 11, 19),
            "source_version_json": json.dumps({"v": 1})}
    return {**base, **over}


def test_provenance_diff_ignores_per_build_fields_only():
    a = {"batch": batch(), "sources": [src("local_primary"), src("vps", source_priority=1)]}
    b = {"batch": batch(batch_id="y", warehouse_database="wh_y", started_at=9, finished_at=10, notes="khac"), "sources": [src("vps", source_priority=1, source_version_json={"v": 1}), src("local_primary")]}
    assert diff_batch_provenance(a, b) == []
    for field, value in (("canonicalization_git_commit", "zzz"), ("ownership_manifest_sha256", "o2"), ("status", "fail"), ("setup_sql_sha256", "s2")):
        assert any(field in p for p in diff_batch_provenance(a, {"batch": batch(**{field: value}), "sources": a["sources"]})), field
    changed = {"batch": batch(), "sources": [src("local_primary", dump_sha256="d2"), src("vps", source_priority=1)]}
    assert any("dump_sha256" in p for p in diff_batch_provenance(a, changed))
    fewer = {"batch": batch(), "sources": [src("local_primary")]}
    assert any("tập source" in p for p in diff_batch_provenance(a, fewer))


def test_run_set_parity_for_frozen_history():
    old = [{"source_code": "local_primary", "source_run_id": 1, "status": "completed", "items": 10, "observations": 100},
           {"source_code": "vps", "source_run_id": 1, "status": "completed", "items": 5, "observations": 50}]
    same = old + [{"source_code": "local_aux", "source_run_id": 2, "status": "completed", "items": 7, "observations": 70}]
    ok = compare_run_sets(old, same)
    assert ok["parity"] is True and ok["runs_only_in_new"] == 1 and ok["old_runs"] == 2 and ok["new_runs"] == 3
    drift = compare_run_sets(old, [dict(old[0], observations=99), old[1]])
    assert drift["parity"] is False and drift["old_runs_changed_in_new"][0]["diff"] == {"observations": (100, 99)}
    gone = compare_run_sets(old, [old[0]])
    assert gone["parity"] is False and gone["old_runs_missing_in_new"] == [{"source_code": "vps", "source_run_id": 1}]


SETUP_SQL = Path(__file__).resolve().parents[3] / "hotel-price-intelligence" / "backend" / "app" / "database" / "setup.sql"


def _table_columns(table: str) -> set[str]:
    text = SETUP_SQL.read_text(encoding="utf-8")
    block = text[text.index(f"CREATE TABLE {table} ("):]
    block = block[: block.index("\n)")]
    cols = set()
    for line in block.splitlines()[1:]:
        m = re.match(r"^\s{2}([a-z_0-9]+)\s+[A-Za-z]", line)
        if m and m.group(1).upper() not in ("PRIMARY", "KEY", "UNIQUE", "CONSTRAINT", "INDEX", "CHECK", "FOREIGN"):
            cols.add(m.group(1))
    return cols


def test_parity_contract_covers_every_column_of_the_three_core_tables():
    """Moi cot cua bang core phai nam trong COMPARE hoac EXCLUDED (co ly do): them cot vao schema ma quen contract => test do (GPT 11 MAJOR 2)."""
    from rebuild_lib import ITEM_COMPARE, ITEM_EXCLUDED, OBSERVATION_COMPARE_GROUPS, OBSERVATION_EXCLUDED, RUN_COMPARE, RUN_EXCLUDED
    obs_compared = [c for group in OBSERVATION_COMPARE_GROUPS.values() for c, _k in group]
    for table, compared, excluded in (("price_observations", obs_compared, OBSERVATION_EXCLUDED),
                                      ("crawl_run_items", [c for c, _k in ITEM_COMPARE], ITEM_EXCLUDED),
                                      ("crawl_runs", [c for c, _k in RUN_COMPARE], RUN_EXCLUDED)):
        columns = _table_columns(table)
        assert len(compared) == len(set(compared)), f"{table}: cot so sanh trung"
        assert not set(compared) & set(excluded), f"{table}: cot vua so sanh vua loai"
        assert set(compared) | set(excluded) == columns, (table, sorted(columns ^ (set(compared) | set(excluded))))
        assert all(reason for reason in excluded.values())


def test_parity_sqls_are_field_level_null_safe_and_binary_for_text():
    from rebuild_lib import ITEM_COMPARE, OBSERVATION_COMPARE_GROUPS, RUN_COMPARE, build_parity_sqls
    sqls = build_parity_sqls("warehouse_20260916_2src", "warehouse_20261004_3src")
    assert set(sqls) == {"observation", "observation_core", "observation_text", "observation_room_flags", "item", "run"}
    assert "source_record_sha256 <> omo.source_record_sha256" in sqls["observation"]["count"]
    for kind, fields in OBSERVATION_COMPARE_GROUPS.items():
        for name, _k in fields:
            assert f"diff_{name}" in sqls[kind]["count"], (kind, name)
    for kind, fields in (("item", ITEM_COMPARE), ("run", RUN_COMPARE)):
        for name, _k in fields:
            assert f"diff_{name}" in sqls[kind]["count"], (kind, name)
    assert "diff_source_run_id" in sqls["item"]["count"] and "diff_planned_crawl_date" in sqls["run"]["count"]
    assert "BINARY o.room_type_raw" in sqls["observation_text"]["count"]               # doi chu hoa/dau khong bi che boi collation
    assert "CAST(o.crawl_context AS CHAR)" in sqls["run"]["count"] and "CAST(o.dead_link_confirmation AS CHAR)" in sqls["item"]["count"]
    for kind, pair in sqls.items():
        assert "LIMIT 20" in pair["sample"] and "<=>" in pair["count"] or kind == "observation"
    joined = " ".join(p["count"] for p in sqls.values())
    assert "imported_at" not in joined and "is_reference_room" not in joined and "reference_match_status" not in joined
    for bad in ("hotel_price_intel", "warehouse_x; DROP DATABASE y", "warehouse_`a"):
        with pytest.raises(ValueError):
            build_parity_sqls(bad, "warehouse_20261004_3src")


def test_snapshot_identity_is_fail_closed():
    from rebuild_lib import validate_snapshot_identity
    batch = {"batch_id": "b", "status": "pass", "source_manifest_sha256": "m", "cohort_manifest_sha256": "c", "ownership_manifest_sha256": "o",
             "canonicalization_version": "v", "canonicalization_git_commit": "abc"}
    sources = [{"source_code": "vps"}, {"source_code": "local_primary"}]
    problems, identity = validate_snapshot_identity("old", batch, sources, ["local_primary", "vps"])
    assert problems == [] and identity["sources"] == ["local_primary", "vps"] and identity["source_manifest_sha256"] == "m"
    assert any("không tồn tại" in p for p in validate_snapshot_identity("old", None, [], ["vps"])[0])
    assert any("status" in p for p in validate_snapshot_identity("old", {**batch, "status": "fail"}, sources, ["local_primary", "vps"])[0])
    assert any("tập source" in p for p in validate_snapshot_identity("new", batch, sources, ["local_primary", "vps", "local_aux"])[0])
    assert any("tập source" in p for p in validate_snapshot_identity("old", batch, sources + [{"source_code": "local_aux"}], ["local_primary", "vps"])[0])


def test_parity_verdict_requires_no_missing_and_no_field_difference():
    from rebuild_lib import parity_verdict
    clean = {"observation": {"total_old": 10, "missing_in_new": 0, "fingerprint_diff": 0},
             "item": {"total_old": 5, "missing_in_new": 0, "diff_status": 0, "diff_hotel_id": None}}
    assert parity_verdict(clean)["parity"] is True
    drift = {**clean, "observation": {"total_old": 10, "missing_in_new": 0, "fingerprint_diff": 3}}
    v = parity_verdict(drift)
    assert v["parity"] is False and v["observation"]["differences"] == {"fingerprint_diff": 3}
    missing = {**clean, "item": {"total_old": 5, "missing_in_new": 1, "diff_status": 0}}
    assert parity_verdict(missing)["parity"] is False
    assert parity_verdict({"observation": {"total_old": 0, "missing_in_new": 0, "fingerprint_diff": 0}})["parity"] is False   # rong khong duoc PASS
    assert parity_verdict({})["parity"] is False


def test_preflight_doc_and_code_state_gate_post_build():
    from rebuild_lib import check_code_state, evaluate_preflight_doc
    ok = {"checks": [{"name": "a", "ok": True, "detail": ""}], "snapshot": {}}
    assert evaluate_preflight_doc(ok) == []
    failed = {"checks": [{"name": "a", "ok": True}, {"name": "GUARDED_PATHS sach", "ok": False, "detail": "dirty"}]}
    assert any("GUARDED_PATHS sach" in p for p in evaluate_preflight_doc(failed))
    assert evaluate_preflight_doc({}) != []                       # thieu danh sach checks -> khong coi la PASS
    assert check_code_state("abc", "abc", []) == []
    assert any("HEAD" in p for p in check_code_state("abc", "def", []))
    assert any("HEAD" in p for p in check_code_state(None, None, []))
    assert any("dirty" in p for p in check_code_state("abc", "abc", ["hotel-price-intelligence/backend/app/warehouse/batch.py"]))


def test_sample_with_bytes_serializes_and_display_columns_are_not_binary_expressions():
    """GPT 13 MAJOR 1: BINARY cua connector tra bytes -> json.dumps phai thanh cong; cot hien thi khong dung BINARY."""
    from rebuild_lib import build_parity_sqls, json_default
    report = {"sample": [{"old_room_type_raw": b"Deluxe \xc4\x90\xc3\xb4i", "new_room_type_raw": b"\xff\xfe", "d": D(2026, 10, 4),
                          "x": __import__("decimal").Decimal("1.5"), "t": T(2026, 10, 4, 1, 2, 3)}]}
    dumped = json.loads(json.dumps(report, ensure_ascii=False, default=json_default))
    assert dumped["sample"][0]["old_room_type_raw"] == "Deluxe Đôi" and dumped["sample"][0]["new_room_type_raw"] == "hex:fffe"
    assert dumped["sample"][0]["d"] == "2026-10-04" and dumped["sample"][0]["x"] == 1.5
    with pytest.raises(TypeError):
        json.dumps({"s": {1, 2}}, default=json_default)
    sqls = build_parity_sqls("warehouse_20260916_2src", "warehouse_20261004_3src")
    text_sample = sqls["observation_text"]["sample"]
    head = text_sample.split(" FROM ")[0]
    assert "BINARY" not in head and "BINARY" in text_sample              # BINARY chi o dieu kien so sanh, khong o cot SELECT hien thi
    assert "old_room_type_raw" in head


def test_parity_connection_sets_execution_time_cap_before_any_query(monkeypatch):
    """GPT 13 MAJOR 2: moi truy van parity chay duoi `max_execution_time`; test bang ket noi gia (khong MySQL)."""
    import contextlib
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import compare_wave_a
    executed: list[str] = []

    class FakeCursor:
        def __init__(self):
            self.rows = None

        def execute(self, sql, params=()):
            executed.append(sql.strip().split("\n")[0][:60])
            self.rows = [{"total_old": 3, "missing_in_new": 0, "fingerprint_diff": 0, "diff_x": 0}] if sql.startswith("SELECT COUNT") else []

        def fetchone(self):
            return self.rows[0]

        def fetchall(self):
            return []

    class FakeConn:
        def cursor(self, dictionary=True):
            return FakeCursor()

    @contextlib.contextmanager
    def fake_connection(database):
        yield FakeConn()

    monkeypatch.setattr(compare_wave_a, "warehouse_connection", fake_connection)
    result = compare_wave_a.row_parity("warehouse_20260916_2src:b_old", "warehouse_20261004_3src:b_new")
    assert executed[0] == "SET SESSION TRANSACTION READ ONLY"
    assert executed[1] == f"SET SESSION max_execution_time = {compare_wave_a.MAX_MS}"
    assert result["max_execution_time_ms"] == compare_wave_a.MAX_MS and result["parity"] is True
    from rebuild_lib import parity_session_sql
    assert parity_session_sql(900000) == "SET SESSION max_execution_time = 900000"
    for bad in (0, -1, "9"):
        with pytest.raises(ValueError):
            parity_session_sql(bad)


def test_profile_verification_binds_preflight_to_the_exact_paths_and_hashes_used_by_build():
    from rebuild_lib import compare_input_snapshots, verify_profile
    doc = {"profiles": {
        "aux": {"source_manifest": {"path": "d/src_aux.json", "identity_sha256": "s1"}, "ownership_manifest": {"path": "d/own_aux.json", "sha256": "o1"},
                "cohort_manifest": {"path": "d/cohort.json", "sha256": "c"}},
        "full": {"source_manifest": {"path": "d/src.json", "identity_sha256": "s2"}, "ownership_manifest": {"path": "d/own.json", "sha256": "o2"},
                 "cohort_manifest": {"path": "d/cohort.json", "sha256": "c"}, "cutoff_file": {"path": "d/cut.json", "sha256": "k"}}}}
    aux = {"source_manifest": {"path": "d/src_aux.json", "sha256": "s1"}, "ownership_manifest": {"path": "d/own_aux.json", "sha256": "o1"},
           "cohort_manifest": {"path": "d/cohort.json", "sha256": "c"}}
    assert verify_profile(doc, "aux", aux) == []                                     # aux khong can cutoff
    full = {"source_manifest": {"path": "d/src.json", "sha256": "s2"}, "ownership_manifest": {"path": "d/own.json", "sha256": "o2"},
            "cohort_manifest": {"path": "d/cohort.json", "sha256": "c"}, "cutoff_file": {"path": "d/cut.json", "sha256": "k"}}
    assert verify_profile(doc, "full", full) == []
    # "preflight file A, build file B": dung hash dung nhung truyen duong dan khac -> FAIL
    wrong_path = {**aux, "ownership_manifest": {"path": "d/own.json", "sha256": "o1"}}
    assert any(".path" in p for p in verify_profile(doc, "aux", wrong_path))
    # aux manifest bi sua -> hash lech (khong con duoc che boi viec preflight kiem cap file full)
    tampered = {**aux, "source_manifest": {"path": "d/src_aux.json", "sha256": "XX"}}
    assert any("identity_sha256" in p for p in verify_profile(doc, "aux", tampered))
    assert any("thiếu giá trị thực tế" in p for p in verify_profile(doc, "full", aux))            # full thieu cutoff
    assert any("không hợp lệ" in p for p in verify_profile(doc, "nope", aux))
    assert any("thiếu profiles" in p for p in verify_profile({}, "aux", aux))
    # post-compare: path/hash khong duoc doi trong stage
    assert compare_input_snapshots(aux, aux) == []
    assert any("source_manifest" in p for p in compare_input_snapshots(aux, tampered))
    assert compare_input_snapshots(None, aux) != [] and compare_input_snapshots({}, aux) != []
