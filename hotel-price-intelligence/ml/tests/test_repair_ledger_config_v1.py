"""audit_tools/repair_parity_v1.py — phan A (ledger raw, SQL co `a.id AS joined_assignment_id`) va phan B (du doan config moi). THUAN: khong ket noi MySQL; SQL that duoc chay tren SQLite lam oracle ngu nghia."""
from __future__ import annotations

import ast
import copy
import datetime as dt
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "audit_tools"
BACKEND = Path(__file__).resolve().parents[2] / "backend"
spec = importlib.util.spec_from_file_location("repair_parity_v1", TOOLS / "repair_parity_v1.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

DS = "ds_new"


# =========================================================================== A. LEDGER SQL
def test_ledger_sql_is_one_bounded_read_only_left_join_that_exposes_the_joined_assignment_id():
    sql = rp.SQL_FLAG_LEDGER
    assert sql.startswith("SELECT ") and ";" not in sql and sql.count("%s") == 2
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|INTO|CALL|LOCK)\b", sql, re.IGNORECASE)
    assert "LEFT JOIN ml_reference_assignments a" in sql and "INNER JOIN" not in sql.upper() and "a.id AS joined_assignment_id" in sql           # GPT file 62 L1
    assert "a.dataset_version = s.dataset_version" in sql and "s.dataset_version = %s" in sql and "s.is_daily_snapshot_selected = 1" in sql
    assert "s.record_id > %s ORDER BY s.record_id LIMIT 1000" in sql
    for column in rp.LEDGER_COLUMNS:
        assert f" AS {column}" in sql, column


SAMPLES_DDL = "CREATE TABLE ml_samples (id INTEGER PRIMARY KEY, dataset_version TEXT, record_id INTEGER, ml_reference_assignment_id INTEGER, is_daily_snapshot_selected INTEGER)"
ASSIGN_DDL = ("CREATE TABLE ml_reference_assignments (id INTEGER, dataset_version TEXT, hotel_id TEXT, checkin_date TEXT, canonical_series_id TEXT, "
              "free_cancellation INTEGER, breakfast_included INTEGER, max_occupancy INTEGER)")


def run_sql(samples, assignments, dataset=DS, cursor=0):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(SAMPLES_DDL)
    conn.execute(ASSIGN_DDL)
    conn.executemany("INSERT INTO ml_samples (dataset_version, record_id, ml_reference_assignment_id, is_daily_snapshot_selected) VALUES (?,?,?,?)", samples)
    conn.executemany("INSERT INTO ml_reference_assignments VALUES (?,?,?,?,?,?,?,?)", assignments)
    return [dict(r) for r in conn.execute(rp.SQL_FLAG_LEDGER.replace("%s", "?"), (dataset, cursor)).fetchall()]


def identity_of(rows_by_id):
    return {rid: (a["hotel_id"], a["checkin_date"], a["canonical_series_id"]) for rid, a in rows_by_id.items()}


def test_gpt_counterexample_a_sample_pointing_to_a_missing_assignment_is_visible_through_the_joined_id():
    samples = [(DS, 1, 10, 1), (DS, 2, 11, 1)]                                             # 10 ton tai voi toan NULL; 11 KHONG ton tai (orphan)
    assignments = [(10, DS, "h", "2026-09-10", "S1", None, None, None)]
    rows = run_sql(samples, assignments)
    by_id = {r["record_id"]: r for r in rows}
    assert by_id[1]["s_assignment_id"] == 10 and by_id[1]["joined_assignment_id"] == 10 and by_id[1]["raw_free_cancellation"] is None            # NULL that
    assert by_id[2]["s_assignment_id"] == 11 and by_id[2]["joined_assignment_id"] is None and by_id[2]["raw_free_cancellation"] is None            # orphan: s-side id van co gia tri
    ident = {1: ("h", "2026-09-10", "S1"), 2: ("h", "2026-09-10", "S2")}
    assert rp.ledger_violations(by_id[1], dataset_version=DS, identity=ident[1]) == []
    assert rp.ledger_violations(by_id[2], dataset_version=DS, identity=ident[2]) == ["missing_assignment"]
    result = rp.evaluate_ledger(rows, dataset_version=DS, parquet_identity=ident, expected_rows=2)
    assert result["status"] == "FAIL" and result["violations"] == {"missing_assignment": 1}
    naive_orphan_check = [r["record_id"] for r in rows if r["s_assignment_id"] is None]       # kiem tra theo s-side id (the loi cu): KHONG thay orphan
    assert naive_orphan_check == []


def test_assignment_that_exists_only_in_another_dataset_is_an_orphan_for_this_dataset():
    rows = run_sql([(DS, 1, 10, 1)], [(10, "ds_other", "h", "2026-09-10", "S1", 1, 1, 2)])
    assert rows[0]["joined_assignment_id"] is None
    assert rp.ledger_violations(rows[0], dataset_version=DS, identity=("h", "2026-09-10", "S1")) == ["missing_assignment"]


def test_unselected_samples_other_datasets_and_the_keyset_cursor_are_respected():
    samples = [(DS, 1, 10, 1), (DS, 2, 10, 0), ("ds_other", 3, 10, 1), (DS, 4, 10, 1), (DS, 5, 10, 1)]
    assignments = [(10, DS, "h", "2026-09-10", "S", 0, 1, 2), (10, "ds_other", "h", "2026-09-10", "S", 0, 1, 2)]
    assert [r["record_id"] for r in run_sql(samples, assignments)] == [1, 4, 5]
    assert [r["record_id"] for r in run_sql(samples, assignments, cursor=1)] == [4, 5]


def good_rows(n=6):
    rows = []
    for i in range(1, n + 1):
        rows.append({"record_id": i, "s_assignment_id": 100 + i, "joined_assignment_id": 100 + i, "a_dataset_version": DS, "a_hotel_id": f"h{i % 2}", "a_checkin_date": dt.date(2026, 9, 10),
                     "a_canonical_series_id": f"S{i}", "raw_free_cancellation": [0, 1, None][i % 3], "raw_breakfast_included": [1, 0, 1][i % 3], "raw_max_occupancy": [None, 2, 4][i % 3]})
    return rows


def good_identity(rows):
    return {r["record_id"]: (r["a_hotel_id"], str(r["a_checkin_date"]), r["a_canonical_series_id"]) for r in rows}


def test_a_consistent_ledger_passes_and_reports_the_raw_distribution_and_a_stable_digest():
    rows = good_rows()
    result = rp.evaluate_ledger(rows, dataset_version=DS, parquet_identity=good_identity(rows), expected_rows=6)
    assert result["status"] == "PASS" and result["rows"] == result["unique_record_ids"] == 6 and result["raw_flag_distribution"]["free_cancellation"] == {"null": 2, "zero": 2, "one": 2}
    renumbered = [dict(r, s_assignment_id=r["s_assignment_id"] + 9000, joined_assignment_id=r["joined_assignment_id"] + 9000) for r in rows]
    assert rp.stable_ledger_digest(renumbered) == result["stable_digest"]                       # technical id khong anh huong hash on dinh
    flipped = copy.deepcopy(rows)
    flipped[0]["raw_free_cancellation"] = 1 if flipped[0]["raw_free_cancellation"] is None else None
    assert rp.stable_ledger_digest(flipped) != result["stable_digest"]


MUTATIONS = {
    "missing_assignment": lambda rows: rows[0].update(joined_assignment_id=None),
    "assignment_id_mismatch": lambda rows: rows[0].update(joined_assignment_id=999),
    "wrong_dataset": lambda rows: rows[0].update(a_dataset_version="ds_old"),
    "identity_mismatch": lambda rows: rows[0].update(a_canonical_series_id="OTHER"),
    "domain_invalid_free_cancellation": lambda rows: rows[0].update(raw_free_cancellation=2),
    "domain_invalid_breakfast_included": lambda rows: rows[0].update(raw_breakfast_included=True),
    "domain_invalid_max_occupancy": lambda rows: rows[0].update(raw_max_occupancy=-1),
    "bad_columns": lambda rows: rows[0].pop("raw_max_occupancy"),
}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_each_row_level_violation_is_detected_and_fails_the_ledger(name):
    rows = good_rows()
    MUTATIONS[name](rows)
    result = rp.evaluate_ledger(rows, dataset_version=DS, parquet_identity=good_identity(good_rows()), expected_rows=6)
    assert result["status"] == "FAIL" and name in result["violations"], result["violations"]


@pytest.mark.parametrize("value", [2, -1, 1.0, True, "1", 0.5])
def test_flag_domain_is_only_null_zero_one_as_exact_ints(value):
    rows = good_rows()
    rows[1]["raw_free_cancellation"] = value
    assert rp.evaluate_ledger(rows, dataset_version=DS, parquet_identity=good_identity(good_rows()), expected_rows=6)["status"] == "FAIL"


def test_set_level_violations_duplicate_missing_extra_and_wrong_count_are_failures():
    rows, ident = good_rows(), good_identity(good_rows())
    assert rp.evaluate_ledger(rows + [dict(rows[0])], dataset_version=DS, parquet_identity=ident, expected_rows=6)["duplicate_record_ids"] == 1
    assert rp.evaluate_ledger(rows[:-1], dataset_version=DS, parquet_identity=ident, expected_rows=6)["missing_vs_parquet"] == 1
    extra = rows + [dict(rows[0], record_id=77, s_assignment_id=177, joined_assignment_id=177)]
    result = rp.evaluate_ledger(extra, dataset_version=DS, parquet_identity=ident, expected_rows=6)
    assert result["status"] == "FAIL" and result["extra_vs_parquet"] == 1 and "record_not_in_parquet" in result["violations"]
    assert rp.evaluate_ledger(rows, dataset_version=DS, parquet_identity=ident, expected_rows=7)["status"] == "FAIL"
    assert rp.evaluate_ledger([], dataset_version=DS, parquet_identity=ident, expected_rows=0)["status"] == "FAIL"           # rong khong bao gio PASS vi parquet co khoa


def test_orphans_are_never_repaired_by_dropping_rows_or_inner_join():
    assert "INNER JOIN" not in rp.SQL_FLAG_LEDGER.upper()
    rows = good_rows()
    rows[2]["joined_assignment_id"] = None
    assert rp.evaluate_ledger(rows[:2] + rows[3:], dataset_version=DS, parquet_identity=good_identity(good_rows()), expected_rows=6)["status"] == "FAIL"        # bo hang orphan => thieu khoa => van FAIL


# =========================================================================== B. DU DOAN CONFIG
def source_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {"json": json, "Any": object}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), "exec"), namespace)    # noqa: S102
    return namespace[name]


@pytest.mark.parametrize("payload", [{"b": 1, "a": [1, 2, {"z": None, "y": True}]}, {"x": "Đà Lạt", "n": 0.02, "i": 10 ** 12}, {"k": {"k2": {"k3": [1.5, "a"]}}}, [], {}, {"é": "ü"}])
def test_canonical_json_equals_the_real_anomaly_registry_lib_function_on_json_native_payloads(payload):
    real_default = lambda value: (_ for _ in ()).throw(TypeError(f"{type(value)}"))               # config chi co kieu JSON thuan; `_default` that chi xu ly Decimal/date
    real = source_function(BACKEND / "app" / "scraper" / "anomaly_registry_lib.py", "canonical_json")
    real.__globals__["_default"] = real_default
    assert rp.canonical_json(payload) == real(payload)
    real_sha = source_function(BACKEND / "app" / "scraper" / "anomaly_registry_lib.py", "sha256_hex")
    real_sha.__globals__["hashlib"] = __import__("hashlib")
    assert rp.sha256_hex(rp.canonical_json(payload)) == real_sha(real(payload))


def test_the_code_hash_formula_is_the_one_used_by_the_real_builder_code_manifest():
    source = (Path(__file__).resolve().parents[1] / "dataset_builder" / "code_identity.py").read_text(encoding="utf-8")
    assert 'json.dumps({"files": files, "excluded_backend_prefixes": list(EXCLUDED_BACKEND_PREFIXES)}, sort_keys=True, separators=(",", ":"))' in source
    assert "hashlib.sha256(canonical.encode(\"utf-8\")).hexdigest()" in source
    files = {"a.py": "1" * 64, "b.py": "2" * 64}
    assert rp.code_sha256_of(files, ["app.core"]) == __import__("hashlib").sha256(json.dumps({"files": files, "excluded_backend_prefixes": ["app.core"]}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def manifest(files, prefixes=("app.core",)):
    files = dict(sorted(files.items()))
    return {"files": files, "excluded_backend_prefixes": list(prefixes), "code_sha256": rp.code_sha256_of(files, prefixes)}


OLD_FILES = {"hotel-price-intelligence/ml/dataset_builder/features.py": "a" * 64, "hotel-price-intelligence/ml/dataset_builder/dictionary.py": "b" * 64, "hotel-price-intelligence/ml/dataset_builder/config.py": "c" * 64,
             "hotel-price-intelligence/backend/app/x.py": "d" * 64}
NEW_FILES = {**OLD_FILES, "hotel-price-intelligence/ml/dataset_builder/features.py": "e" * 64, "hotel-price-intelligence/ml/dataset_builder/dictionary.py": "f" * 64}


def old_config():
    return {"purpose": "dev", "batch": "b20261004_3src", "builder_code": manifest(OLD_FILES), "label_config": {"threshold": 0.02}, "label_config_sha256": "1" * 64, "purge_gap_days": 1,
            "anomaly": {"mode": "evaluation_asof", "cutoff_at": "2026-10-05T00:00:00Z", "registry_file_sha256": "8" * 64}, "evaluation_horizons": [1], "random_seed": 20261005}


def test_prediction_replaces_only_builder_code_and_matches_an_independent_recomputation():
    cfg, new_code = old_config(), manifest(NEW_FILES)
    result = rp.predict_new_config(cfg, new_code, old_config_sha256=rp.config_sha256(cfg))
    assert result["ok"], result["violations"]
    expected = copy.deepcopy(cfg)
    expected["builder_code"] = new_code
    assert result["predicted_config_sha256"] == rp.sha256_hex(json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)) != rp.config_sha256(cfg)
    assert result["changed_files"] == sorted(rp.EXPECTED_CHANGED_FILES) and all(result["other_keys_identical"].values()) and set(result["other_keys_identical"]) == set(cfg) - {"builder_code"}
    assert cfg["builder_code"]["code_sha256"] == manifest(OLD_FILES)["code_sha256"]                    # khong sua config cu tai cho


def test_prediction_is_independent_of_dict_key_order_of_the_stored_json():
    cfg, new_code = old_config(), manifest(NEW_FILES)
    reordered = json.loads(json.dumps(cfg, sort_keys=True))
    shuffled = dict(reversed(list(cfg.items())))
    shas = {rp.predict_new_config(c, new_code)["predicted_config_sha256"] for c in (cfg, reordered, shuffled)}
    assert len(shas) == 1


@pytest.mark.parametrize("name,mutate_new,expected_fragment", [
    ("extra_file_changed", lambda f: f.update({"hotel-price-intelligence/ml/dataset_builder/config.py": "9" * 64}), "changed_files_mismatch"),
    ("expected_file_unchanged", lambda f: f.update({"hotel-price-intelligence/ml/dataset_builder/dictionary.py": OLD_FILES["hotel-price-intelligence/ml/dataset_builder/dictionary.py"]}), "changed_files_mismatch"),
    ("file_added", lambda f: f.update({"hotel-price-intelligence/ml/dataset_builder/new_mod.py": "7" * 64}), "files_added_or_removed"),
    ("file_removed", lambda f: f.pop("hotel-price-intelligence/backend/app/x.py"), "files_added_or_removed"),
])
def test_unexpected_code_changes_make_the_prediction_not_ok(name, mutate_new, expected_fragment):
    files = dict(NEW_FILES)
    mutate_new(files)
    result = rp.predict_new_config(old_config(), manifest(files))
    assert not result["ok"] and any(expected_fragment in v for v in result["violations"]), result["violations"]


def test_integrity_failures_of_inputs_are_reported_not_ignored():
    cfg, new_code = old_config(), manifest(NEW_FILES)
    assert "old_config_sha_mismatch" in rp.predict_new_config(cfg, new_code, old_config_sha256="0" * 64)["violations"]
    broken_old = copy.deepcopy(cfg)
    broken_old["builder_code"]["code_sha256"] = "0" * 64
    assert "old_builder_code_self_hash_mismatch" in rp.predict_new_config(broken_old, new_code)["violations"]
    broken_new = copy.deepcopy(new_code)
    broken_new["code_sha256"] = "0" * 64
    assert "new_builder_code_self_hash_mismatch" in rp.predict_new_config(cfg, broken_new)["violations"]
    assert "excluded_backend_prefixes_changed" in rp.predict_new_config(cfg, manifest(NEW_FILES, prefixes=("app.core", "app.other")))["violations"]
    assert rp.predict_new_config({"x": 1}, new_code)["violations"] == ["old_config_missing_builder_code"]


def test_non_json_native_values_in_a_config_are_a_loud_error_not_silently_serialized():
    cfg = old_config()
    cfg["when"] = dt.datetime(2026, 10, 5)
    with pytest.raises(TypeError):
        rp.config_sha256(cfg)


# =========================================================================== ledger IO co schema co dinh (khong de pandas ep NULL thanh float)
def io_rows():
    return good_rows() + [{"record_id": 99, "s_assignment_id": 5, "joined_assignment_id": None, "a_dataset_version": None, "a_hotel_id": None, "a_checkin_date": None, "a_canonical_series_id": None,
                           "raw_free_cancellation": None, "raw_breakfast_included": None, "raw_max_occupancy": None}]


def test_ledger_roundtrip_preserves_python_types_nulls_dates_and_the_exact_schema(tmp_path):
    rows = io_rows()
    path = tmp_path / "ledger.parquet"
    sha = rp.write_ledger(rows, path)
    assert sha == __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    back = rp.read_ledger(path)
    assert back == rows
    assert all(type(r["record_id"]) is int and (r["raw_free_cancellation"] is None or type(r["raw_free_cancellation"]) is int) for r in back)
    assert back[0]["a_checkin_date"] == dt.date(2026, 9, 10) and back[-1]["joined_assignment_id"] is None and back[-1]["raw_free_cancellation"] is None
    import pyarrow.parquet as pq
    assert [(f.name, str(f.type)) for f in pq.read_table(path).schema] == [(f.name, str(f.type)) for f in rp.ledger_arrow_schema()]


def test_ledger_writer_never_overwrites_and_rejects_bool_flags_and_wrong_types(tmp_path):
    path = tmp_path / "ledger.parquet"
    rp.write_ledger(good_rows(), path)
    with pytest.raises(FileExistsError):
        rp.write_ledger(good_rows(), path)
    for column in ("raw_free_cancellation", "raw_breakfast_included", "raw_max_occupancy"):
        bad = good_rows()
        bad[0][column] = True
        with pytest.raises(ValueError, match="bool"):
            rp.write_ledger(bad, tmp_path / f"bad_{column}.parquet")
    wrong = good_rows()
    wrong[0]["record_id"] = "not a number"
    with pytest.raises(ValueError, match="ledger_rows_invalid"):
        rp.write_ledger(wrong, tmp_path / "wrong.parquet")
    assert not (tmp_path / "wrong.parquet").exists()


# --- GPT 66 (I): Arrow tu ep 1.5->1, 2.5->2, 1000.5->1000, tu them NULL cho khoa thieu va bo khoa thua => writer phai kiem hinh dang/kieu CHINH XAC truoc khi chuyen Arrow, khong tao tep
def _set(column, value):
    def mutate(row):
        row[column] = value
    return mutate


def _drop(column):
    def mutate(row):
        del row[column]
    return mutate


def _add(column, value):
    def mutate(row):
        row[column] = value
    return mutate


WRITER_CONTROLS = {
    "free_cancellation_1_5": (_set("raw_free_cancellation", 1.5), "raw_free_cancellation: invalid_float"),
    "max_occupancy_2_5": (_set("raw_max_occupancy", 2.5), "raw_max_occupancy: invalid_float"),
    "record_id_1000_5": (_set("record_id", 1000.5), "record_id: invalid_float"),
    "missing_key": (_drop("raw_breakfast_included"), "missing=['raw_breakfast_included']"),
    "extra_key": (_add("unexpected_column", 1), "extra=['unexpected_column']"),
    "flag_2": (_set("raw_free_cancellation", 2), "raw_free_cancellation: invalid_int"),
    "negative_occupancy": (_set("raw_max_occupancy", -1), "raw_max_occupancy: invalid_int"),
    "record_id_null": (_set("record_id", None), "record_id: null_not_allowed"),
    "assignment_id_str": (_set("s_assignment_id", "7"), "s_assignment_id: invalid_str"),
    "date_as_datetime": (_set("a_checkin_date", dt.datetime(2026, 9, 10, 0, 0)), "a_checkin_date: invalid_datetime"),
    "date_as_string": (_set("a_checkin_date", "2026-09-10"), "a_checkin_date: invalid_str"),
    "hotel_as_int": (_set("a_hotel_id", 5), "a_hotel_id: invalid_int"),
    "occupancy_integral_float": (_set("raw_max_occupancy", 3.0), "raw_max_occupancy: invalid_float"),
}


@pytest.mark.parametrize("control", list(WRITER_CONTROLS))
def test_ledger_writer_rejects_values_arrow_would_coerce_and_creates_no_file(tmp_path, control):
    mutator, fragment = WRITER_CONTROLS[control]
    rows = good_rows()
    mutator(rows[0])
    path = tmp_path / f"{control}.parquet"
    with pytest.raises(ValueError) as caught:
        rp.write_ledger(rows, path)
    assert "ledger_rows_invalid" in str(caught.value) and fragment in str(caught.value), str(caught.value)
    assert not path.exists()


def test_ledger_writer_rejects_a_non_mapping_row_and_still_accepts_every_valid_null_pattern(tmp_path):
    with pytest.raises(ValueError, match="row_not_mapping"):
        rp.write_ledger([["not", "a", "mapping"]], tmp_path / "nm.parquet")
    assert rp.ledger_row_shape_errors(io_rows()[-1]) == []                                              # hang toan NULL (cho phep) van hop le
    for good in good_rows():
        assert rp.ledger_row_shape_errors(good) == []


def test_ledger_reader_rejects_a_file_whose_schema_is_not_exactly_the_pinned_one(tmp_path):
    import pandas as pd
    pandas_written = tmp_path / "pandas.parquet"
    pd.DataFrame(good_rows()).to_parquet(pandas_written, index=False)                                  # raw_* thanh float64/object => khac schema ghim
    with pytest.raises(ValueError, match="ledger_schema_unexpected"):
        rp.read_ledger(pandas_written)
    missing = tmp_path / "missing_col.parquet"
    pd.DataFrame(good_rows()).drop(columns=["raw_max_occupancy"]).to_parquet(missing, index=False)
    with pytest.raises(ValueError, match="ledger_schema_unexpected"):
        rp.read_ledger(missing)
