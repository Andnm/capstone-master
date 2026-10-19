"""audit_tools/repair_parity_v1.py — phan C (parity Parquet, hai co theo ledger, oracle content_sha256) va D (allowlist JSON/CSV theo duong dan cu the, mac dinh tu choi). THUAN.
Cac test "du lieu that" doc `outputs/datasets/ds_20261006_dev1b` (artifact cu, chi doc) va chi chay du khi runtime = runtime builder (pandas 3.0.5)."""
from __future__ import annotations

import copy
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

TOOLS = Path(__file__).resolve().parents[3] / "audit_tools"
ROOT = Path(__file__).resolve().parents[4]
OLD_REAL = ROOT / "outputs" / "datasets" / "ds_20261006_dev1b"
spec = importlib.util.spec_from_file_location("repair_parity_v1_c", TOOLS / "repair_parity_v1.py")
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)
OLD_V, NEW_V = "ds_old", "ds_new"


# =========================================================================== C. PARITY PARQUET (du lieu tong hop, mo phong cac dtype that)
def make_frames(n=60, seed=7, raw_null_old="true"):
    rng = np.random.default_rng(seed)
    ids = np.arange(1000, 1000 + n, dtype="int64")
    raw_fc = [None if i % 7 == 3 else int(i % 2) for i in range(n)]
    raw_bf = [None if i % 11 == 5 else int((i // 2) % 2) for i in range(n)]
    raw_occ = [None if i % 9 == 4 else int(2 + i % 3) for i in range(n)]
    base = pd.DataFrame({
        "warehouse_record_id": ids, "dataset_version": OLD_V, "hotel_id": [f"h{i % 5}" for i in range(n)], "checkin_date": [dt.date(2026, 9, 1) + dt.timedelta(days=int(i % 4)) for i in range(n)],
        "canonical_series_id": [f"s{i:04d}" for i in range(n)], "vn_observation_date": [dt.date(2026, 8, 21) + dt.timedelta(days=int(i % 30)) for i in range(n)],
        "prediction_time": pd.to_datetime("2026-08-21") + pd.to_timedelta(np.arange(n), unit="h"), "split": ["train" if i % 3 else None for i in range(n)],
        "city": ["Hà Nội" if i % 2 else "Đà Lạt" for i in range(n)], "max_occupancy": pd.array(raw_occ, dtype="Int64"), "room_area_m2": np.where(np.arange(n) % 8 == 0, np.nan, rng.random(n) * 40),
        "current_price": rng.random(n) * 1e6, "price_lag_1": np.where(np.arange(n) % 5 == 0, np.nan, rng.random(n) * 1e6), "is_weekend": rng.random(n) > 0.5, "lead_time": np.arange(n, dtype="int64") % 14,
        "y_direction_h1": pd.array([None if i % 6 == 0 else "up" for i in range(n)], dtype="string"),
    })
    old, new = base.copy(), base.copy()
    new["dataset_version"] = NEW_V
    old_flag = lambda raw: pd.array([True if (r is None and raw_null_old == "true") else (None if r is None else bool(r)) for r in raw], dtype="boolean")
    old["free_cancellation"], old["breakfast_included"] = old_flag(raw_fc), old_flag(raw_bf)
    new["free_cancellation"] = pd.array([None if r is None else bool(r) for r in raw_fc], dtype="boolean")
    new["breakfast_included"] = pd.array([None if r is None else bool(r) for r in raw_bf], dtype="boolean")
    cols = [c for c in base.columns if c not in ("max_occupancy",)]
    ordered = ["warehouse_record_id", "dataset_version"] + [c for c in cols if c not in ("warehouse_record_id", "dataset_version")] + ["max_occupancy", "free_cancellation", "breakfast_included"]
    old, new = old[ordered], new[ordered]

    def ledger(frame, offset):
        rows = []
        for i, rid in enumerate(frame["warehouse_record_id"].tolist()):
            rows.append({"record_id": int(rid), "s_assignment_id": offset + i, "joined_assignment_id": offset + i, "a_dataset_version": OLD_V if offset < 5000 else NEW_V, "a_hotel_id": frame["hotel_id"].iloc[i],
                         "a_checkin_date": frame["checkin_date"].iloc[i], "a_canonical_series_id": frame["canonical_series_id"].iloc[i], "raw_free_cancellation": raw_fc[i], "raw_breakfast_included": raw_bf[i],
                         "raw_max_occupancy": raw_occ[i]})
        return rows

    return old, new, ledger(old, 100), ledger(new, 9000)


def cmp(old, new, old_ledger, new_ledger, **kw):
    kw.setdefault("expected_rows", None)
    kw.setdefault("expected_columns", None)
    return rp.compare_samples(old, new, old_ledger=old_ledger, new_ledger=new_ledger, old_version=OLD_V, new_version=NEW_V, **kw)


def test_the_expected_repair_passes_and_counts_changed_rows_as_raw_null_and_old_known():
    old, new, lo, ln = make_frames()
    result = cmp(old, new, lo, ln)
    assert result["verdict"] == "PASS_PARITY", result["violations"]
    fc, bf = result["notes"]["flags"]["free_cancellation"], result["notes"]["flags"]["breakfast_included"]
    assert fc["changed_rows"] == fc["expected_changed_rows"] == sum(1 for r in lo if r["raw_free_cancellation"] is None) and bf["changed_rows"] == sum(1 for r in lo if r["raw_breakfast_included"] is None)
    assert set(fc["cells"]) == {"raw=NA|old=T|new=NA", "raw=T|old=T|new=T", "raw=F|old=F|new=F"} and result["notes"]["value_invariant_columns"] == len(old.columns) - 3
    assert result["notes"]["oracle_content_sha256"] == result["notes"]["new_content_sha256_recomputed"] != result["notes"]["old_content_sha256_recomputed"]


def test_raw_null_with_old_na_is_a_valid_unchanged_missing_and_reported_separately():
    old, new, lo, ln = make_frames(raw_null_old="na")
    result = cmp(old, new, lo, ln)
    assert result["verdict"] == "PASS_PARITY" and result["changed_rows_total"] == 0 and "raw=NA|old=NA|new=NA" in result["notes"]["flags"]["free_cancellation"]["cells"]
    assert result["notes"]["oracle_content_sha256"] == result["notes"]["new_content_sha256_recomputed"] == result["notes"]["old_content_sha256_recomputed"]          # khong doi gi => hash noi dung bang nhau


def test_row_order_difference_alone_is_not_a_failure_but_is_reported():
    old, new, lo, ln = make_frames()
    result = cmp(old, new.sample(frac=1.0, random_state=1).reset_index(drop=True), lo, ln)
    assert result["verdict"] == "PASS_PARITY" and result["notes"]["row_order_identical"] is False


def mutate_int64(frame, i, column, value):
    out = frame.copy()
    out.iat[i, out.columns.get_loc(column)] = value                                                            # giu nguyen dtype (Int64)
    return out


def mutate(frame, i, column, value):
    out = frame.copy()
    out[column] = out[column].astype(object) if out[column].dtype != "boolean" else out[column]
    out.iat[i, out.columns.get_loc(column)] = value
    return out


NEGATIVE = {
    "known_raw_flag_flipped_in_new": lambda o, n, lo, ln: (o, mutate(n, 1, "free_cancellation", not bool(n["free_cancellation"].iloc[1]))),
    "raw_null_but_new_true": lambda o, n, lo, ln: (o, mutate(n, 3, "free_cancellation", True)),
    "raw_null_but_old_false": lambda o, n, lo, ln: (mutate(o, 3, "free_cancellation", False), n),
    "known_raw_flag_unpreserved_in_old": lambda o, n, lo, ln: (mutate(o, 1, "free_cancellation", not bool(o["free_cancellation"].iloc[1])), n),
    "other_float_column_one_ulp": lambda o, n, lo, ln: (o, n.assign(current_price=np.nextafter(n["current_price"].to_numpy(), np.inf))),
    "float_nan_replaced_by_zero": lambda o, n, lo, ln: (o, n.assign(room_area_m2=n["room_area_m2"].fillna(0.0))),
    "int64_na_replaced_by_zero": lambda o, n, lo, ln: (o, n.assign(max_occupancy=n["max_occupancy"].fillna(0))),
    "string_na_changed": lambda o, n, lo, ln: (o, n.assign(y_direction_h1=n["y_direction_h1"].fillna("down"))),
    "split_changed_to_none": lambda o, n, lo, ln: (o, mutate(n, 1, "split", None)),
    "bool_column_flipped": lambda o, n, lo, ln: (o, n.assign(is_weekend=~n["is_weekend"])),
    "value_column_dtype_changed": lambda o, n, lo, ln: (o, n.assign(lead_time=n["lead_time"].astype("float64"))),
    "column_dropped_from_new": lambda o, n, lo, ln: (o, n.drop(columns=["lead_time"])),
    "columns_reordered": lambda o, n, lo, ln: (o, n[[*n.columns[::-1]]]),
    "row_missing_in_new": lambda o, n, lo, ln: (o, n.iloc[1:]),
    "row_extra_in_new": lambda o, n, lo, ln: (o, pd.concat([n, n.iloc[[0]].assign(warehouse_record_id=424242)], ignore_index=True)),
    "duplicate_key_in_new": lambda o, n, lo, ln: (o, pd.concat([n, n.iloc[[0]]], ignore_index=True)),
    "dataset_version_not_new_in_new": lambda o, n, lo, ln: (o, n.assign(dataset_version=OLD_V)),
    "dataset_version_mixed_in_old": lambda o, n, lo, ln: (mutate(o, 2, "dataset_version", "ds_other"), n),
    "ledger_new_raw_differs_from_old": lambda o, n, lo, ln: (o, n, lo, [dict(r, raw_free_cancellation=(1 if r["raw_free_cancellation"] is None else None)) if r["record_id"] == 1004 else r for r in ln]),
    "ledger_new_missing_record": lambda o, n, lo, ln: (o, n, lo, ln[1:]),
    "ledger_old_missing_record": lambda o, n, lo, ln: (o, n, lo[1:], ln),
    "ledger_identity_differs": lambda o, n, lo, ln: (o, n, lo, [dict(r, a_canonical_series_id="X") if r["record_id"] == 1005 else r for r in ln]),
    "max_occupancy_differs_from_raw": lambda o, n, lo, ln: (o, mutate(n, 2, "max_occupancy", 99)),
    # ---- co lap tung lop (moi lop chi co mot cach bat duoc) ----
    "duplicate_key_in_both_frames": lambda o, n, lo, ln: (pd.concat([o, o.iloc[[0]]], ignore_index=True), pd.concat([n, n.iloc[[0]]], ignore_index=True)),
    "occupancy_wrong_in_both_vs_ledger": lambda o, n, lo, ln: (mutate_int64(o, 2, "max_occupancy", 99), mutate_int64(n, 2, "max_occupancy", 99)),
    "flag_dtype_object_in_new": lambda o, n, lo, ln: (o, n.assign(free_cancellation=n["free_cancellation"].astype(object))),
    "both_frames_deviate_identically_from_known_raw": lambda o, n, lo, ln: (mutate(o, 1, "free_cancellation", not bool(o["free_cancellation"].iloc[1])),
                                                                              mutate(n, 1, "free_cancellation", not bool(n["free_cancellation"].iloc[1]))),
}


@pytest.mark.parametrize("name", list(NEGATIVE))
def test_every_negative_control_fails_the_parity(name):
    old, new, lo, ln = make_frames()
    out = NEGATIVE[name](old, new, lo, ln)
    old2, new2 = out[0], out[1]
    lo2, ln2 = (out[2], out[3]) if len(out) == 4 else (lo, ln)
    result = cmp(old2, new2, lo2, ln2)
    assert result["verdict"] == "FAIL" and result["violations"], name


def S(values, dtype):
    return pd.Series(values, dtype=dtype)


HELPER_CASES = [
    ("equal_int64", S([1, 2, 3], "int64"), S([1, 2, 3], "int64"), True),
    ("equal_float_with_nan", S([1.0, np.nan, 3.0], "float64"), S([1.0, np.nan, 3.0], "float64"), True),
    ("equal_Int64_with_na", S([1, None], "Int64"), S([1, None], "Int64"), True),
    ("equal_string_with_na", S(["a", None], "string"), S(["a", None], "string"), True),
    ("equal_boolean_with_na", S([True, None], "boolean"), S([True, None], "boolean"), True),
    ("one_ulp_float", S([1.0, 2.0], "float64"), S([float(np.nextafter(1.0, 2.0)), 2.0], "float64"), False),
    ("negative_zero_vs_zero", S([0.0], "float64"), S([-0.0], "float64"), False),
    ("nan_vs_zero_under_fill", S([np.nan, 1.0], "float64"), S([0.0, 1.0], "float64"), False),
    ("dtype_int64_vs_Int64", S([1, 2], "int64"), S([1, 2], "Int64"), False),
    ("dtype_float64_vs_Float64", S([1.0, 2.0], "float64"), S([1.0, 2.0], "Float64"), False),
    ("Int64_na_vs_value", S([1, None], "Int64"), S([1, 0], "Int64"), False),
    ("string_na_vs_text", S(["a", None], "string"), S(["a", "None"], "string"), False),
    ("boolean_flip", S([True, False], "boolean"), S([True, True], "boolean"), False),
    ("length_differs", S([1, 2], "int64"), S([1, 2, 3], "int64"), False),
]


@pytest.mark.parametrize("name,a,b,expected", HELPER_CASES, ids=[c[0] for c in HELPER_CASES])
def test_bitwise_series_helper_contract_on_its_own(name, a, b, expected):
    assert rp._series_equal_bitwise(a, b) is expected


@pytest.mark.parametrize("value,expected", [(None, "NA"), (pd.NA, "NA"), (float("nan"), "NA"), (True, "T"), (False, "F"), (np.bool_(True), "T"), (1, "BAD"), (0, "BAD"), ("x", "BAD"), (1.0, "BAD")])
def test_state_classification_of_parquet_flag_values(value, expected):
    assert rp._state(value) == expected


@pytest.mark.parametrize("value,expected", [(None, "NA"), (1, "T"), (0, "F"), (True, "BAD"), (2, "BAD"), (1.0, "BAD"), ("1", "BAD")])
def test_state_classification_of_raw_ledger_flag_values(value, expected):
    assert rp._raw_state(value) == expected


def test_the_content_hash_layer_alone_catches_a_value_change_when_the_bitwise_layer_is_disabled(monkeypatch):
    old, new, lo, ln = make_frames()
    new = new.assign(current_price=np.nextafter(new["current_price"].to_numpy(), np.inf))
    monkeypatch.setattr(rp, "_series_equal_bitwise", lambda a, b: True)
    result = cmp(old, new, lo, ln)
    assert result["verdict"] == "FAIL" and any("oracle_content_sha256" in v for v in result["violations"])


def test_the_bitwise_layer_alone_catches_a_value_change_when_the_content_hash_layer_is_disabled(monkeypatch):
    old, new, lo, ln = make_frames()
    new = new.assign(current_price=np.nextafter(new["current_price"].to_numpy(), np.inf))
    monkeypatch.setattr(rp, "content_sha256", lambda frame: "constant")
    result = cmp(old, new, lo, ln)
    assert result["verdict"] == "FAIL" and any("value_invariant_columns_differ" in v for v in result["violations"])


def test_the_flag_dtype_check_alone_catches_an_object_dtype_flag_when_the_content_hash_layer_is_disabled(monkeypatch):
    old, new, lo, ln = make_frames()
    monkeypatch.setattr(rp, "content_sha256", lambda frame: "constant")
    result = cmp(old, new.assign(free_cancellation=new["free_cancellation"].astype(object)), lo, ln)
    assert result["verdict"] == "FAIL" and any("flag_dtype_unexpected_free_cancellation" in v for v in result["violations"])


def test_expected_counts_are_enforced_when_requested():
    old, new, lo, ln = make_frames()
    assert cmp(old, new, lo, ln, expected_rows=61)["verdict"] == "FAIL" and cmp(old, new, lo, ln, expected_columns=99)["verdict"] == "FAIL"
    assert cmp(old, new, lo, ln, expected_rows=60, expected_columns=len(old.columns))["verdict"] == "PASS_PARITY"


def test_changed_row_count_is_a_measurement_not_a_precondition():
    old, new, lo, ln = make_frames()
    got = cmp(old, new, lo, ln)["changed_rows_total"]
    assert got == sum(1 for r in lo if r["raw_free_cancellation"] is None) + sum(1 for r in lo if r["raw_breakfast_included"] is None) and got != 776


# =========================================================================== C'. DU LIEU THAT (artifact cu dev1b, chi doc) — runtime builder
BUILDER_OK = pd.__version__ == rp.BUILDER_RUNTIME["pandas"]
needs_builder = pytest.mark.skipif(not (OLD_REAL / "samples.parquet").is_file() or not BUILDER_OK, reason="can artifact dev1b that + runtime builder (pandas 3.0.5)")


@needs_builder
def test_content_sha256_reimplementation_reproduces_the_value_the_builder_recorded_for_the_real_old_dataset():
    recorded = json.loads((OLD_REAL / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]["content_sha256"]
    assert rp.content_sha256(pd.read_parquet(OLD_REAL / "samples.parquet")) == recorded == "33b2f362daf29ca25d068cc3ab68745c6a7351a4c9f64f2982bab530be119a7b"


@pytest.fixture(scope="module")
def real_pair():
    old = pd.read_parquet(OLD_REAL / "samples.parquet")
    ids = old["warehouse_record_id"].to_numpy()
    true_rows = np.flatnonzero(old["free_cancellation"].to_numpy(dtype="bool"))
    chosen = set(true_rows[::true_rows.size // 776][:776].tolist())                 # 776 hang fc=True duoc coi la raw NULL (gia lap)
    raw_fc = [None if i in chosen else int(bool(v)) for i, v in enumerate(old["free_cancellation"].tolist())]
    raw_bf = [int(bool(v)) for v in old["breakfast_included"].tolist()]
    raw_occ = [None if pd.isna(v) else int(v) for v in old["max_occupancy"].tolist()]
    new = old.copy()
    new["dataset_version"] = "ds_20261009_nf1"
    new["free_cancellation"] = pd.array([None if r is None else bool(r) for r in raw_fc], dtype="boolean")

    def ledger(offset, version):
        return [{"record_id": int(ids[i]), "s_assignment_id": offset + i, "joined_assignment_id": offset + i, "a_dataset_version": version, "a_hotel_id": old["hotel_id"].iloc[i],
                 "a_checkin_date": old["checkin_date"].iloc[i], "a_canonical_series_id": old["canonical_series_id"].iloc[i], "raw_free_cancellation": raw_fc[i], "raw_breakfast_included": raw_bf[i],
                 "raw_max_occupancy": raw_occ[i]} for i in range(len(old))]

    return old, new, ledger(1, "ds_20261006_dev1b"), ledger(10 ** 6, "ds_20261009_nf1"), len(chosen)


@needs_builder
def test_real_sized_old_dataset_with_776_simulated_raw_null_rows_passes_all_74_value_invariant_columns(real_pair):
    old, new, lo, ln, n_chosen = real_pair
    result = rp.compare_samples(old, new, old_ledger=lo, new_ledger=ln, old_version="ds_20261006_dev1b", new_version="ds_20261009_nf1")
    assert result["verdict"] == "PASS_PARITY", result["violations"]
    assert result["notes"]["value_invariant_columns"] == 74 and result["changed_rows_total"] == n_chosen == 776
    assert result["notes"]["old_content_sha256_recomputed"] == "33b2f362daf29ca25d068cc3ab68745c6a7351a4c9f64f2982bab530be119a7b" != result["notes"]["new_content_sha256_recomputed"]
    assert result["notes"]["oracle_content_sha256"] == result["notes"]["new_content_sha256_recomputed"]


@needs_builder
@pytest.mark.parametrize("column", ["price_lag_7", "current_price", "max_occupancy", "split", "y_pct_change_h1", "rooms_left"])
def test_real_sized_one_cell_tampering_in_any_value_column_is_detected(real_pair, column):
    old, new, lo, ln, _ = real_pair
    tampered = new.copy()
    series = tampered[column]
    idx = int(np.flatnonzero(series.notna().to_numpy())[5])
    tampered[column] = series.astype(object)
    tampered.iat[idx, tampered.columns.get_loc(column)] = (series.iloc[idx] + 1) if not isinstance(series.iloc[idx], str) else "tampered"
    tampered[column] = tampered[column].astype(series.dtype) if column != "split" else tampered[column]
    assert rp.compare_samples(old, tampered, old_ledger=lo, new_ledger=ln, old_version="ds_20261006_dev1b", new_version="ds_20261009_nf1")["verdict"] == "FAIL"


# =========================================================================== D. ALLOWLIST JSON/CSV theo duong dan (cay artifact)
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def derive_new_tree(old_dir: Path, new_dir: Path, *, version="ds_20261009_nf1", code="c" * 64, config="d" * 64, content="e" * 64, samples_bytes=None):
    """MO PHONG cay artifact moi hop le theo hieu biet cua ke hoach 61/63 (day la DANG KY TRUOC cac truong duoc phep doi); builder that co the khac => validator se tu choi va ta dieu tra."""
    shutil.copytree(old_dir, new_dir)
    samples = new_dir / "samples.parquet"
    samples.write_bytes(samples.read_bytes() + b"-new" if samples_bytes is None else samples_bytes)
    rows = list(csv.reader((new_dir / "data_dictionary.csv").open(encoding="utf-8", newline="")))
    for row in rows[1:]:
        if row[0] in rp.FLAGS:
            row[2] = row[2] + "; NULL cua assignment = <NA> (chua biet)"
    with (new_dir / "data_dictionary.csv").open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
    for rel in ("reports/causal_references.json", "reports/item_matches.json", "reports/samples_labels.json", "reports/split.json"):
        data = json.loads((new_dir / rel).read_text(encoding="utf-8"))
        if "elapsed_s" in data:
            data["elapsed_s"] = round(data["elapsed_s"] + 0.3, 1)
        data["builder_code_sha256"] = code
        write_json(new_dir / rel, data)
    coverage = json.loads((new_dir / "coverage_report.json").read_text(encoding="utf-8"))
    coverage["split"]["builder_code_sha256"] = code                                                              # coverage_report.json nhung ban sao split report (khong co khoa top-level)
    write_json(new_dir / "coverage_report.json", coverage)
    contract = json.loads((new_dir / "dataset_contract.json").read_text(encoding="utf-8"))
    contract.update(dataset_version=version, build_config_sha256=config, builder_code_sha256=code)
    write_json(new_dir / "dataset_contract.json", contract)
    fl = json.loads((new_dir / "reports/features_labels.json").read_text(encoding="utf-8"))
    fl.update(builder_code_sha256=code, elapsed_s=fl["elapsed_s"] + 0.2, output_dir=str(new_dir), file_sha256=sha(samples), content_sha256=content)
    write_json(new_dir / "reports/features_labels.json", fl)
    actual = lambda rel: sha(new_dir / rel)
    val = json.loads((new_dir / "reports/validation.json").read_text(encoding="utf-8"))
    val["builder_code_sha256"], val["elapsed_s"] = code, val["elapsed_s"] + 0.4
    for check in val["checks"]:
        if check["name"] == "parquet_ton_tai":
            check["detail"] = str(samples)
        elif check["name"].startswith("hash_file_"):
            check["detail"] = actual(check["name"][len("hash_file_"):])[:16]
        elif check["name"] == "hash_noi_dung_khop":
            check["detail"] = content[:16]
    write_json(new_dir / "reports/validation.json", val)
    manifest = json.loads((new_dir / "reports/REPORTS_MANIFEST.json").read_text(encoding="utf-8"))
    manifest.update(dataset_version=version, build_config_sha256=config, builder_code_sha256=code)
    for name in manifest["reports"]:
        manifest["reports"][name]["file_sha256"] = actual(f"reports/{name}")
    write_json(new_dir / "reports/REPORTS_MANIFEST.json", manifest)
    checks = json.loads((new_dir / "output_checksums.json").read_text(encoding="utf-8"))
    for name, entry in checks.items():
        entry["file_sha256"] = actual(name)
    checks["samples.parquet"]["content_sha256"] = content
    write_json(new_dir / "output_checksums.json", checks)
    return {"new_version": version, "new_code_sha256": code, "new_config_sha256": config, "new_output_dir": str(new_dir), "new_samples_file_sha256": sha(samples), "new_samples_content_sha256": content,
            "new_samples_path": str(samples)}


needs_real_dir = pytest.mark.skipif(not OLD_REAL.is_dir(), reason="can artifact dev1b that")


@pytest.fixture()
def tree(tmp_path):
    new_dir = tmp_path / "ds_20261009_nf1"
    ctx = derive_new_tree(OLD_REAL, new_dir)
    return OLD_REAL, new_dir, ctx


@needs_real_dir
def test_a_simulated_legitimate_new_tree_has_zero_violations_and_only_the_registered_rules_fire(tree):
    old_dir, new_dir, ctx = tree
    result = rp.compare_json_trees(old_dir, new_dir, ctx)
    assert result["violations"] == [], result["violations"]
    assert set(result["allowed_observed"]) >= {"timing", "code_sha_new", "config_sha_new", "version_new", "output_dir_new", "samples_file_sha_new", "samples_content_sha_new", "self_file_sha",
                                               "validation_samples_path_new", "validation_hash_file_prefix_self", "validation_content_hash_prefix_new"}


def patch_json(directory, rel, fn):
    data = json.loads((directory / rel).read_text(encoding="utf-8"))
    fn(data)
    write_json(directory / rel, data)


def refresh_hashes(directory, skip=()):
    """Dong bo moi hash tu-tham-chieu theo thu tu phu thuoc (output_checksums -> validation hash_file_* -> REPORTS_MANIFEST) de loi da gieo la loi DUY NHAT. `skip` o {checksums, validation, manifest}
    giu nguyen gia tri da gieo loi."""
    if "checksums" not in skip:
        patch_json(directory, "output_checksums.json", lambda c: [c[n].update(file_sha256=sha(directory / n)) for n in c])
    if "validation" not in skip:
        patch_json(directory, "reports/validation.json", lambda d: [c.update(detail=sha(directory / c["name"][len("hash_file_"):])[:16]) for c in d["checks"] if c["name"].startswith("hash_file_")])
    if "manifest" not in skip:
        patch_json(directory, "reports/REPORTS_MANIFEST.json", lambda m: [m["reports"][n].update(file_sha256=sha(directory / f"reports/{n}")) for n in m["reports"]])


TREE_NEGATIVE = {                                                                   # ten: (file, ham gieo loi, doan thong bao CU THE phai xuat hien, tap hash KHONG dong bo lai)
    "split_plan_changed": ("reports/split.json", lambda d: d["plan"].update(test_end="2026-10-03"), "unallowlisted_diff: reports/split.json:/plan/test_end", ()),
    "coverage_rows_changed": ("coverage_report.json", lambda d: d.update(rows=d["rows"] + 1), "unallowlisted_diff: coverage_report.json:/rows", ()),
    "validation_check_flipped": ("reports/validation.json", lambda d: d["checks"][3].update(ok=False), "unallowlisted_diff: reports/validation.json:/checks[3]/ok", ()),
    "validation_failed_set_changed": ("reports/validation.json", lambda d: d.update(failed=["x"]), "unallowlisted_diff: reports/validation.json:/failed", ()),
    "validation_number_changed": ("reports/validation.json", lambda d: d["numbers"].update(parquet_rows=1), "unallowlisted_diff: reports/validation.json:/numbers/parquet_rows", ()),
    "unknown_key_added": ("reports/causal_references.json", lambda d: d.update(surprise=1), "unallowlisted_diff: reports/causal_references.json:/surprise", ()),
    "key_removed": ("reports/item_matches.json", lambda d: d.pop("step"), "unallowlisted_diff: reports/item_matches.json:/step", ()),
    "contract_split_plan_changed": ("dataset_contract.json", lambda d: d["split_plan"].update(train_end="2026-09-15"), "unallowlisted_diff: dataset_contract.json:/split_plan/train_end", ()),
    "contract_n1_changed": ("dataset_contract.json", lambda d: d["n1_policy"].update(policy_version="n1-policy-9"), "unallowlisted_diff: dataset_contract.json:/n1_policy/policy_version", ()),
    "contract_version_wrong": ("dataset_contract.json", lambda d: d.update(dataset_version="ds_wrong"), "allowlisted_rule_failed: dataset_contract.json:/dataset_version", ()),
    "contract_config_sha_wrong": ("dataset_contract.json", lambda d: d.update(build_config_sha256="0" * 64), "allowlisted_rule_failed: dataset_contract.json:/build_config_sha256", ()),
    "contract_code_sha_wrong": ("dataset_contract.json", lambda d: d.update(builder_code_sha256="0" * 64), "allowlisted_rule_failed: dataset_contract.json:/builder_code_sha256", ()),
    "code_sha_wrong_in_a_report": ("reports/split.json", lambda d: d.update(builder_code_sha256="0" * 64), "allowlisted_rule_failed: reports/split.json:/builder_code_sha256", ()),
    "elapsed_not_a_number": ("reports/split.json", lambda d: d.update(elapsed_s="fast"), "allowlisted_rule_failed: reports/split.json:/elapsed_s", ()),
    "elapsed_negative": ("reports/split.json", lambda d: d.update(elapsed_s=-1), "allowlisted_rule_failed: reports/split.json:/elapsed_s", ()),
    "features_output_dir_wrong": ("reports/features_labels.json", lambda d: d.update(output_dir="D:/elsewhere"), "allowlisted_rule_failed: reports/features_labels.json:/output_dir", ()),
    "features_samples_file_sha_wrong": ("reports/features_labels.json", lambda d: d.update(file_sha256="0" * 64), "allowlisted_rule_failed: reports/features_labels.json:/file_sha256", ()),
    "features_samples_content_sha_wrong": ("reports/features_labels.json", lambda d: d.update(content_sha256="0" * 64), "allowlisted_rule_failed: reports/features_labels.json:/content_sha256", ()),
    "features_rows_changed": ("reports/features_labels.json", lambda d: d.update(rows=1), "unallowlisted_diff: reports/features_labels.json:/rows", ()),
    "validation_hash_prefix_wrong": ("reports/validation.json", lambda d: [c.update(detail="0" * 16) for c in d["checks"] if c["name"] == "hash_file_coverage_report.json"], "validation_hash_file_prefix_self", ("validation",)),
    "validation_content_prefix_wrong": ("reports/validation.json", lambda d: [c.update(detail="0" * 16) for c in d["checks"] if c["name"] == "hash_noi_dung_khop"], "validation_content_hash_prefix_new", ("validation",)),
    "validation_parquet_path_wrong": ("reports/validation.json", lambda d: [c.update(detail="D:/elsewhere/samples.parquet") for c in d["checks"] if c["name"] == "parquet_ton_tai"], "validation_samples_path_new", ("validation",)),
    "validation_other_detail_changed": ("reports/validation.json", lambda d: d["checks"][25].update(detail={"test": 1}), "unallowlisted_diff: reports/validation.json:/checks[25]/detail", ()),
    "manifest_report_hash_stale": ("reports/REPORTS_MANIFEST.json", lambda d: d["reports"]["split.json"].update(file_sha256="0" * 64), "allowlisted_rule_failed: reports/REPORTS_MANIFEST.json:/reports/split.json/file_sha256", ("manifest",)),
    "manifest_version_wrong": ("reports/REPORTS_MANIFEST.json", lambda d: d.update(dataset_version="ds_wrong"), "allowlisted_rule_failed: reports/REPORTS_MANIFEST.json:/dataset_version", ()),
    "manifest_config_sha_wrong": ("reports/REPORTS_MANIFEST.json", lambda d: d.update(build_config_sha256="0" * 64), "allowlisted_rule_failed: reports/REPORTS_MANIFEST.json:/build_config_sha256", ()),
    "manifest_steps_changed": ("reports/REPORTS_MANIFEST.json", lambda d: d["steps"].pop(), "unallowlisted_diff: reports/REPORTS_MANIFEST.json:/steps", ()),
    "checksums_stale_for_contract": ("output_checksums.json", lambda d: d["dataset_contract.json"].update(file_sha256="0" * 64), "allowlisted_rule_failed: output_checksums.json:/dataset_contract.json/file_sha256", ("checksums",)),
    "checksums_samples_file_sha_wrong": ("output_checksums.json", lambda d: d["samples.parquet"].update(file_sha256="0" * 64), "allowlisted_rule_failed: output_checksums.json:/samples.parquet/file_sha256", ("checksums",)),
    "checksums_samples_content_sha_wrong": ("output_checksums.json", lambda d: d["samples.parquet"].update(content_sha256="0" * 64), "allowlisted_rule_failed: output_checksums.json:/samples.parquet/content_sha256", ()),
    "checksums_samples_rows_changed": ("output_checksums.json", lambda d: d["samples.parquet"].update(rows=1), "unallowlisted_diff: output_checksums.json:/samples.parquet/rows", ()),
    "checksums_samples_columns_changed": ("output_checksums.json", lambda d: d["samples.parquet"].update(columns=76), "unallowlisted_diff: output_checksums.json:/samples.parquet/columns", ()),
}


@needs_real_dir
@pytest.mark.parametrize("name", list(TREE_NEGATIVE))
def test_each_tampered_leaf_in_a_report_is_rejected_by_default_deny_or_a_failed_self_check(tree, name):
    old_dir, new_dir, ctx = tree
    rel, fn, fragment, skip = TREE_NEGATIVE[name]
    patch_json(new_dir, rel, fn)
    refresh_hashes(new_dir, skip=skip)                                                                          # dong bo hash de loi da gieo la loi DUY NHAT (tru tap `skip`)
    violations = rp.compare_json_trees(old_dir, new_dir, ctx)["violations"]
    assert any(fragment in v for v in violations), (name, violations)


@needs_real_dir
@pytest.mark.parametrize("name,action,rel", [
    ("input_file_byte_changed", lambda d: (d / "inputs/vn_holidays.csv").write_bytes((d / "inputs/vn_holidays.csv").read_bytes() + b"\n"), "inputs/vn_holidays.csv"),
    ("n1_policy_changed", lambda d: (d / "inputs/n1_policy/n1_policy_v1.json").write_bytes(b"{}"), "inputs/n1_policy/n1_policy_v1.json"),
    ("calendar_input_changed", lambda d: (d / "calendar_input.json").write_text("{}", encoding="utf-8"), "calendar_input.json"),
    ("sufficiency_changed", lambda d: (d / "sufficiency_report.json").write_text("{}", encoding="utf-8"), "sufficiency_report.json"),
])
def test_byte_identical_inputs_are_enforced_even_when_every_self_hash_is_made_consistent(tree, name, action, rel):
    old_dir, new_dir, ctx = tree
    action(new_dir)
    refresh_hashes(new_dir)                                                                                     # checksums/validation/manifest nhat quan => chi quy tac byte-identical con bat duoc
    violations = rp.compare_json_trees(old_dir, new_dir, ctx)["violations"]
    assert any(f"must_be_byte_identical_but_differs: {rel}" in v for v in violations), (name, violations)


@needs_real_dir
@pytest.mark.parametrize("name,action,fragment", [
    ("extra_file_added", lambda d: (d / "reports/extra.json").write_text("{}", encoding="utf-8"), "file_set_differs"),
    ("file_removed", lambda d: (d / "reports/split.json").unlink(), "file_set_differs"),
])
def test_the_file_set_must_be_identical(tree, name, action, fragment):
    old_dir, new_dir, ctx = tree
    action(new_dir)
    assert any(fragment in v for v in rp.compare_json_trees(old_dir, new_dir, ctx)["violations"]), name


@needs_real_dir
def test_must_differ_fields_that_stay_equal_are_violations(tree):
    old_dir, new_dir, ctx = tree
    patch_json(new_dir, "dataset_contract.json", lambda d: d.update(dataset_version=json.loads((old_dir / "dataset_contract.json").read_text(encoding="utf-8"))["dataset_version"]))
    violations = rp.compare_json_trees(old_dir, new_dir, {**ctx, "new_version": "ds_20261006_dev1b"})["violations"]
    assert any("must_differ_but_equal: dataset_contract.json:/dataset_version" in v for v in violations)


def edit_dictionary(directory, fn):
    path = directory / "data_dictionary.csv"
    rows = list(csv.reader(path.open(encoding="utf-8", newline="")))
    fn(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)


@needs_real_dir
@pytest.mark.parametrize("name,fn,fragment", [
    ("third_row_changed", lambda rows: rows[3].__setitem__(2, rows[3][2] + "x"), "dictionary_rows_differ_for"),
    ("flag_leakage_note_changed", lambda rows: [r.__setitem__(4, "x") for r in rows if r[0] == "free_cancellation"], "dictionary_non_description_column_differs: free_cancellation.leakage_note"),
    ("flag_group_changed", lambda rows: [r.__setitem__(1, "calendar") for r in rows if r[0] == "free_cancellation"], "dictionary_non_description_column_differs: free_cancellation.group"),
    ("flag_description_without_na_semantics", lambda rows: [r.__setitem__(2, "Huy mien phi (da sua)") for r in rows if r[0] == "free_cancellation"], "dictionary_description_lacks_NA_semantics: free_cancellation"),
    ("only_one_flag_changed", lambda rows: [r.__setitem__(2, "Gom bua sang (luu y N1: 5 hotel co co sai tu parser)") for r in rows if r[0] == "breakfast_included"], "dictionary_rows_differ_for"),
    ("row_dropped", lambda rows: rows.pop(), "dictionary_row_count_differs"),
    ("header_changed", lambda rows: rows[0].__setitem__(2, "desc"), "dictionary_header_not_pinned"),
    ("rows_swapped", lambda rows: rows.__setitem__(slice(5, 7), [rows[6], rows[5]]), "dictionary_row_order_changed"),
])
def test_dictionary_may_differ_in_exactly_the_description_of_the_two_flags(tree, name, fn, fragment):
    old_dir, new_dir, ctx = tree
    edit_dictionary(new_dir, fn)
    refresh_hashes(new_dir)
    assert any(fragment in v for v in rp.compare_json_trees(old_dir, new_dir, ctx)["violations"]), name


# =========================================================================== G1: ledger duoc kiem o ENTRY POINT CUOI (GPT file 64)
LEDGER_CONTROLS = {
    "orphan": (lambda r: r.update(joined_assignment_id=None), "missing_assignment"),
    "wrong_dataset": (lambda r: r.update(a_dataset_version="wrong"), "wrong_dataset"),
    "identity_vs_parquet": (lambda r: r.update(a_canonical_series_id="wrong"), "identity_mismatch"),
    "assignment_id_mismatch": (lambda r: r.update(s_assignment_id=-9), "assignment_id_mismatch"),
    "domain_invalid_flag": (lambda r: r.update(raw_free_cancellation=2), "domain_invalid_free_cancellation"),
}


@pytest.mark.parametrize("name", list(LEDGER_CONTROLS))
@pytest.mark.parametrize("which", ["old", "new", "both"])
def test_the_final_entry_point_rejects_a_ledger_that_is_wrong_against_its_own_parquet_even_when_old_and_new_ledgers_agree(name, which):
    old, new, lo, ln = make_frames()
    mutator, fragment = LEDGER_CONTROLS[name]
    lo2, ln2 = copy.deepcopy(lo), copy.deepcopy(ln)
    if which in ("old", "both"):
        mutator(lo2[0])
    if which in ("new", "both"):
        mutator(ln2[0])
    result = cmp(old, new, lo2, ln2)
    assert result["verdict"] == "FAIL" and any(fragment in v and v.startswith("ledger_") for v in result["violations"]), (name, which, result["violations"])


@pytest.mark.parametrize("name,make", [
    ("row_missing_field", lambda ledger: [{k: v for k, v in ledger[0].items() if k != "raw_max_occupancy"}, *ledger[1:]]),
    ("row_extra_field", lambda ledger: [dict(ledger[0], surprise=1), *ledger[1:]]),
    ("record_id_as_text", lambda ledger: [dict(ledger[0], record_id="1000"), *ledger[1:]]),
    ("rows_not_mappings", lambda ledger: [1, 2, 3]),
    ("empty_ledger", lambda ledger: []),
    ("none_ledger", lambda ledger: None),
    ("dict_instead_of_list", lambda ledger: {"record_id": 1}),
])
def test_malformed_ledger_payloads_are_a_fail_never_an_exception_and_never_a_pass(name, make):
    old, new, lo, ln = make_frames()
    result = cmp(old, new, lo, make(ln))
    assert result["verdict"] == "FAIL" and result["violations"], name
    result = cmp(old, new, make(lo), ln)
    assert result["verdict"] == "FAIL" and result["violations"], name


def test_any_unexpected_exception_inside_the_comparison_is_converted_to_fail_with_the_error_recorded():
    result = rp.compare_samples(None, None, old_ledger=[], new_ledger=[], old_version=OLD_V, new_version=NEW_V, expected_rows=None, expected_columns=None)
    assert result["verdict"] == "FAIL" and result["violations"][0].startswith("exception: ")


# =========================================================================== domain occupancy: schema ghim + gia tri chinh xac (GPT file 64)
def float_occupancy_frames(first_value):
    old, new, lo, ln = make_frames()
    first_valid = int(np.flatnonzero(old["max_occupancy"].notna().to_numpy())[0])
    raw = lo[first_valid]["raw_max_occupancy"]
    assert raw is not None
    old2, new2 = old.copy(), new.copy()
    for frame in (old2, new2):
        frame["max_occupancy"] = frame["max_occupancy"].astype("Float64")
        frame.iat[first_valid, frame.columns.get_loc("max_occupancy")] = first_value
    return old2, new2, lo, ln


def test_gpt_control_nullable_float64_occupancy_with_a_fraction_is_rejected_by_the_pinned_schema_guard():
    old, new, lo, ln = float_occupancy_frames(2.5)
    result = cmp(old, new, lo, ln)
    assert result["verdict"] == "FAIL" and any("max_occupancy_dtype_unexpected" in v for v in result["violations"])


@pytest.mark.parametrize("value", [2.5, 2.0, 3.0])
def test_the_exactness_check_alone_rejects_any_float_occupancy_even_an_integral_one_when_the_schema_guard_is_disabled(value):
    old, new, lo, ln = float_occupancy_frames(value)
    result = cmp(old, new, lo, ln, pinned_dtypes=None)
    assert result["verdict"] == "FAIL" and any("max_occupancy_not_exact_integer" in v for v in result["violations"])


def test_integer_occupancy_equal_to_the_raw_ledger_passes_with_or_without_the_schema_guard():
    old, new, lo, ln = make_frames()
    assert cmp(old, new, lo, ln)["verdict"] == "PASS_PARITY" and cmp(old, new, lo, ln, pinned_dtypes=None)["verdict"] == "PASS_PARITY"


@pytest.mark.parametrize("value,expected", [(None, (True, None)), (pd.NA, (True, None)), (float("nan"), (True, None)), (2, (True, 2)), (np.int64(3), (True, 3)), (True, (False, None)), (np.bool_(True), (False, None)),
                                            (2.0, (False, None)), (2.5, (False, None)), ("2", (False, None))])
def test_occupancy_value_helper_contract(value, expected):
    assert rp._occupancy_value(value) == expected


# =========================================================================== G2: JSON cau truc day du + co kieu; CSV co do rong
def test_flatten_emits_every_node_including_empty_containers_and_distinguishes_ambiguous_keys():
    f = rp.flatten
    assert f({"a": {}})[""] == ("dict", ("a",)) and f({"a": {}})["/a"] == ("dict", ()) and f({"a": []})["/a"] == ("list", 0)
    assert set(f({"a/b": 1})) != set(f({"a": {"b": 1}})) and set(f({"x[0]": 1})) != set(f({"x": [1]}))
    assert f({"k": True})["/k"] == ("bool", True) != f({"k": 1})["/k"] == ("int", 1) != f({"k": 1.0})["/k"] == ("float", 1.0)
    assert f({"a": None})["/a"] == ("null", None) and f([1, [2]])["[1]"] == ("list", 1)
    assert rp._unesc("inputs~1n1~2x~3~0") == "inputs/n1[x]~" and rp._esc("a/b[c]~") == "/a~1b~2c~3~0" and rp._esc(3) == "[3]"


def first_empty_container(directory):
    for path in sorted(directory.rglob("*.json")):
        flat = rp.flatten(json.loads(path.read_text(encoding="utf-8")))
        for node, entry in flat.items():
            if entry in (("dict", ()), ("list", 0)) and node:
                return path.relative_to(directory).as_posix(), node, entry
    return None


@needs_real_dir
def test_typed_and_structural_controls_that_the_leaf_only_comparison_missed(tree):
    old_dir, new_dir, ctx = tree
    cases = {
        "empty_object_added": ("coverage_report.json", lambda d: d.update(surprise={}), "unallowlisted_diff: coverage_report.json:"),
        "bool_replaced_by_int": ("reports/validation.json", lambda d: d["checks"][3].update(ok=1), "unallowlisted_diff: reports/validation.json:/checks[3]/ok"),
        "int_replaced_by_float": ("coverage_report.json", lambda d: d.update(rows=float(d["rows"])), "unallowlisted_diff: coverage_report.json:/rows"),
        "list_replaced_by_object": ("reports/validation.json", lambda d: d.update(failed={}), "unallowlisted_diff: reports/validation.json:/failed"),
        "empty_list_removed": ("reports/validation.json", lambda d: d.pop("failed"), "unallowlisted_diff: reports/validation.json:/failed"),
        "elapsed_as_bool": ("reports/split.json", lambda d: d.update(elapsed_s=True), "allowlisted_rule_failed: reports/split.json:/elapsed_s"),
        "elapsed_as_infinity_text": ("reports/split.json", lambda d: d.update(elapsed_s="inf"), "allowlisted_rule_failed: reports/split.json:/elapsed_s"),
    }
    for name, (rel, fn, fragment) in cases.items():
        scratch = new_dir.parent / f"scratch_{name}"
        shutil.copytree(new_dir, scratch)
        patch_json(scratch, rel, fn)
        refresh_hashes(scratch)
        violations = rp.compare_json_trees(old_dir, scratch, {**ctx, "new_output_dir": str(new_dir)})["violations"]
        assert any(fragment in v for v in violations), (name, violations)
        shutil.rmtree(scratch)


@needs_real_dir
def test_an_empty_container_present_in_the_old_artifact_cannot_vanish_or_change_type(tree):
    old_dir, new_dir, ctx = tree
    found = first_empty_container(old_dir)
    if found is None:
        pytest.skip("artifact cu khong co container rong")
    rel, node, entry = found
    keys = [k for k in node.split("/") if k]

    def mutate_json(d):
        cursor = d
        for k in keys[:-1]:
            cursor = cursor[int(k[1:-1]) if k.startswith("[") else k]
        cursor.pop(keys[-1]) if isinstance(cursor, dict) else None

    patch_json(new_dir, rel, mutate_json)
    refresh_hashes(new_dir)
    assert any(rel in v for v in rp.compare_json_trees(old_dir, new_dir, ctx)["violations"])


@needs_real_dir
@pytest.mark.parametrize("name,fn,fragment", [
    ("flag_row_missing_last_cell", lambda rows: [r.pop() for r in rows if r[0] == "free_cancellation"], "dictionary_row_width_new"),
    ("flag_row_extra_cell", lambda rows: [r.append("x") for r in rows if r[0] == "free_cancellation"], "dictionary_row_width_new"),
    ("other_row_missing_cell", lambda rows: rows[4].pop(), "dictionary_row_width_new"),
    ("duplicate_column_name", lambda rows: rows[5].__setitem__(0, rows[4][0]), "dictionary_"),
    ("empty_file", lambda rows: rows.clear(), "dictionary_empty"),
])
def test_dictionary_rows_must_have_exactly_the_pinned_width_and_unique_ordered_names(tree, name, fn, fragment):
    old_dir, new_dir, ctx = tree
    edit_dictionary(new_dir, fn)
    refresh_hashes(new_dir)
    violations = rp.compare_json_trees(old_dir, new_dir, ctx)["violations"]
    assert any(fragment in v for v in violations), (name, violations)


# =========================================================================== E: cong tong validate_repair tren du lieu that (runtime-agnostic nho enforce_builder_runtime=False)
NEW_V_REAL, OLD_V_REAL = "ds_20261009_nf1", "ds_20261006_dev1b"
CODE_SHA, CONFIG_SHA = "c" * 64, "d" * 64


def ledger_rows(old, raw_fc, raw_bf, raw_occ, *, version, offset):
    ids = old["warehouse_record_id"].tolist()
    return [{"record_id": int(ids[i]), "s_assignment_id": offset + i, "joined_assignment_id": offset + i, "a_dataset_version": version, "a_hotel_id": old["hotel_id"].iloc[i],
             "a_checkin_date": old["checkin_date"].iloc[i], "a_canonical_series_id": old["canonical_series_id"].iloc[i], "raw_free_cancellation": raw_fc[i], "raw_breakfast_included": raw_bf[i],
             "raw_max_occupancy": raw_occ[i]} for i in range(len(old))]


def write_ledger(path, rows):
    rp.write_ledger(rows, path)


@pytest.fixture(scope="module")
def total_case(tmp_path_factory):
    if not (OLD_REAL / "samples.parquet").is_file():
        pytest.skip("can artifact dev1b that")
    base = tmp_path_factory.mktemp("total_case")
    old = pd.read_parquet(OLD_REAL / "samples.parquet")
    true_rows = np.flatnonzero(old["free_cancellation"].to_numpy(dtype="bool"))
    chosen = set(true_rows[::true_rows.size // 776][:776].tolist())
    raw_fc = [None if i in chosen else int(bool(v)) for i, v in enumerate(old["free_cancellation"].tolist())]
    raw_bf = [int(bool(v)) for v in old["breakfast_included"].tolist()]
    raw_occ = [None if pd.isna(v) else int(v) for v in old["max_occupancy"].tolist()]
    new = old.copy()
    new["dataset_version"] = NEW_V_REAL
    new["free_cancellation"] = pd.array([None if r is None else bool(r) for r in raw_fc], dtype="boolean")
    staging = base / "staging"
    staging.mkdir()
    new.to_parquet(staging / "samples.parquet", index=False)
    measured = rp.content_sha256(pd.read_parquet(staging / "samples.parquet"))
    new_dir = base / NEW_V_REAL
    derive_new_tree(OLD_REAL, new_dir, version=NEW_V_REAL, code=CODE_SHA, config=CONFIG_SHA, content=measured, samples_bytes=(staging / "samples.parquet").read_bytes())
    write_ledger(base / "ledger_old.parquet", ledger_rows(old, raw_fc, raw_bf, raw_occ, version=OLD_V_REAL, offset=1))
    write_ledger(base / "ledger_new.parquet", ledger_rows(old, raw_fc, raw_bf, raw_occ, version=NEW_V_REAL, offset=10 ** 6))
    case = {"base": base, "new_dir": new_dir, "old": old, "raw": (raw_fc, raw_bf, raw_occ), "measured": measured, "changed": len(chosen)}
    case["prov"] = build_provenance(case)
    return case


# --- provenance (GPT 66 R): bao cao producer ledger cu/moi + bien lai du doan config + snapshot P4 cu; test dung dung hinh dang JSON ma producer that sinh ra
OLD_CONFIG_SHA, OTHER_OLD_V, OTHER_OLD_CONFIG_SHA, OLD_CODE_SHA = "e" * 64, "ds_20261006_dev3b", "9" * 64, "1" * 64
PARITY_SHA_REAL = rp._file_sha(TOOLS / "repair_parity_v1.py")
PROD_SCRIPT, PROD_SQL, BASE_CTRL = "a" * 64, "b" * 64, "f" * 64                                       # tuple producer DA DUYET (neo ngoai, chi dung trong fixture)
OLD_SAMPLES_SHA = rp._file_sha(OLD_REAL / "samples.parquet") if (OLD_REAL / "samples.parquet").is_file() else "0" * 64


def build_provenance(case, *, tag="ok", new_dir=None, mutate=None):
    """Tao 4 tep provenance nhat quan voi `case`; `mutate(docs)` sua dict truoc khi ghi (docs: ten -> dict; `snapshot_sha` cap nhat lai neu snapshot bi sua)."""
    base = case["base"]
    prov = base / f"prov_{tag}"
    prov.mkdir()
    new_dir = new_dir or case["new_dir"]
    docs = {"old_config_snapshot": {"run_id": "oldrun000001", "trusted": True, "versions": [OLD_V_REAL, OTHER_OLD_V],
                                    "evaluation": {"ok": True, "versions": {OLD_V_REAL: {"canonical_config_sha256": OLD_CONFIG_SHA, "build_config_sha256_in_db": OLD_CONFIG_SHA, "builder_code": {"code_sha256": OLD_CODE_SHA}},
                                                                OTHER_OLD_V: {"canonical_config_sha256": OTHER_OLD_CONFIG_SHA, "build_config_sha256_in_db": OTHER_OLD_CONFIG_SHA, "builder_code": {"code_sha256": OLD_CODE_SHA}}}}},
            "config_prediction": rp.prediction_receipt({"ok": True, "violations": [], "old_config_sha256": OLD_CONFIG_SHA, "predicted_config_sha256": CONFIG_SHA, "old_code_sha256": OLD_CODE_SHA,
                                                       "new_code_sha256": CODE_SHA, "changed_files": ["ml/dataset_builder/dictionary.py", "ml/dataset_builder/features.py"]})}

    def report(dataset, run_id, samples_path, ledger_path, pins, snapshot_entry=None):
        samples_sha = rp._file_sha(samples_path)
        rep = {"tool": "warehouse_flag_ledger_v1", "run_id": run_id, "mode": "run", "dataset": dataset, "expected_samples_sha256": samples_sha, "parquet": {"sha256": samples_sha, "rows": 146431},
               "ledger": {"status": "PASS", "file": {"sha256": rp._file_sha(ledger_path), "rows": 146431}}, "verdict": {"overall": "LEDGER_PASS", "exit_code": 0, "reasons": []}, "refusals": [],
               "step0": {"ok": True, "checks": {"status_pass": True, "build_config_sha256_pin": True}}, "expected_config_sha256s": pins, "script_sha256": PROD_SCRIPT, "sql_set_sha256": PROD_SQL,
               "base_controller_sha256": BASE_CTRL, "base_source_sha256": BASE_CTRL, "repair_parity_sha256": PARITY_SHA_REAL, "repair_parity_source_sha256": PARITY_SHA_REAL}
        if snapshot_entry is not None:
            rep["snapshot"] = snapshot_entry
        return rep

    docs["old_ledger_report"] = report(OLD_V_REAL, "oldrun000001", OLD_REAL / "samples.parquet", case["base"] / "ledger_old.parquet", {OLD_V_REAL: OLD_CONFIG_SHA, OTHER_OLD_V: OTHER_OLD_CONFIG_SHA},
                                       snapshot_entry={"status": "PASS", "ok": True})
    docs["new_ledger_report"] = report(NEW_V_REAL, "newrun000002", new_dir / "samples.parquet", case["base"] / "ledger_new.parquet", {NEW_V_REAL: CONFIG_SHA})
    if mutate:
        mutate(docs)
    paths = {}
    for name in ("old_config_snapshot", "config_prediction"):
        paths[name] = prov / f"{name}.json"
        write_json(paths[name], docs[name])
    if "snapshot" in docs["old_ledger_report"]:
        docs["old_ledger_report"]["snapshot"].setdefault("file", {"path": "x", "sha256": rp._file_sha(paths["old_config_snapshot"]), "trusted": True})
    for name in ("old_ledger_report", "new_ledger_report"):
        paths[name] = prov / f"{name}.json"
        write_json(paths[name], docs[name])
    return paths


def gate(case, *, new_dir=None, old_ledger=None, new_ledger=None, **overrides):
    kwargs = dict(old_dir=OLD_REAL, new_dir=new_dir or case["new_dir"], old_ledger_path=old_ledger or case["base"] / "ledger_old.parquet", new_ledger_path=new_ledger or case["base"] / "ledger_new.parquet",
                  old_version=OLD_V_REAL, new_version=NEW_V_REAL, expected_code_sha256=CODE_SHA, expected_config_sha256=CONFIG_SHA, expected_old_config_sha256=OLD_CONFIG_SHA, expected_old_code_sha256=OLD_CODE_SHA,
                  expected_old_samples_sha256=OLD_SAMPLES_SHA, expected_producer_script_sha256=PROD_SCRIPT, expected_producer_sql_set_sha256=PROD_SQL,
                  expected_base_controller_sha256=BASE_CTRL, provenance_paths=case["prov"], enforce_builder_runtime=False)
    kwargs.update(overrides)
    return rp.validate_repair(**kwargs)


def clone_case(case, name):
    target = case["base"] / name
    shutil.copytree(case["new_dir"], target / "ds")
    return target / "ds"


def test_total_gate_passes_the_consistent_real_sized_case_and_binds_a_receipt_to_the_source_hashes(total_case):
    result = gate(total_case)
    assert result["verdict"] == "PASS_REPAIR_PARITY" and result["violations"] == [], result["violations"][:5]
    assert result["components"]["samples"]["changed_rows_total"] == total_case["changed"] == 776 and result["components"]["artifacts"]["violations"] == []
    r = result["receipt"]
    assert r["new_content_sha256_measured"] == total_case["measured"] == r["oracle_content_sha256"] != r["old_content_sha256_measured"]
    assert r["new_samples_file_sha256"] == rp._file_sha(total_case["new_dir"] / "samples.parquet") and r["old_samples_file_sha256"] == rp._file_sha(OLD_REAL / "samples.parquet")
    assert r["old_ledger_file_sha256"] == rp._file_sha(total_case["base"] / "ledger_old.parquet") and r["tool_sha256"] == rp._file_sha(TOOLS / "repair_parity_v1.py")
    assert result["receipt_sha256"] == rp.sha256_hex(rp.canonical_json(r)) == gate(total_case)["receipt_sha256"]                    # tat dinh theo cung nguon
    assert set(r) >= {"runtime", "expected_code_sha256", "expected_config_sha256", "old_version", "new_version", "verdict", "violation_count"}


@needs_builder
def test_total_gate_enforces_the_builder_runtime_by_default_and_passes_in_it(total_case):
    assert gate(total_case, enforce_builder_runtime=True)["verdict"] == "PASS_REPAIR_PARITY"


@pytest.mark.skipif(BUILDER_OK, reason="chi co nghia o runtime KHAC runtime builder")
def test_total_gate_refuses_a_non_builder_runtime_when_enforced(total_case):
    result = gate(total_case, enforce_builder_runtime=True)
    assert result["verdict"] == "FAIL" and any("runtime_not_builder" in v for v in result["violations"])


def rewrite_ledger(case, which, name, fn):
    rows = rp.read_ledger(case["base"] / f"ledger_{which}.parquet")
    fn(rows)
    target = case["base"] / f"{name}_{which}.parquet"
    write_ledger(target, rows)
    return target


@pytest.mark.parametrize("control", ["orphan", "wrong_dataset", "identity_vs_parquet", "assignment_id_mismatch"])
@pytest.mark.parametrize("which", ["old", "new"])
def test_total_gate_rejects_the_four_gpt_ledger_controls_at_the_final_entry_point(total_case, control, which):
    mutator, fragment = LEDGER_CONTROLS[control]
    path = rewrite_ledger(total_case, which, f"ctl_{control}", lambda rows: mutator(rows[0]))
    result = gate(total_case, **{f"{which}_ledger": path})
    assert result["verdict"] == "FAIL" and any(f"ledger_{which}_invalid" in v and fragment in v for v in result["violations"]), result["violations"][:4]


def test_total_gate_rejects_a_ledger_with_missing_column_missing_file_and_extra_rows_without_raising(total_case):
    frame = pd.read_parquet(total_case["base"] / "ledger_new.parquet").drop(columns=["raw_max_occupancy"])
    bad = total_case["base"] / "ledger_bad_cols.parquet"
    frame.to_parquet(bad, index=False)
    for kw in ({"new_ledger": bad}, {"new_ledger": total_case["base"] / "does_not_exist.parquet"}):
        result = gate(total_case, **kw)
        assert result["verdict"] == "FAIL" and any(v.startswith("exception:") for v in result["violations"])
    rows = rp.read_ledger(total_case["base"] / "ledger_new.parquet")
    write_ledger(total_case["base"] / "ledger_extra.parquet", rows + [dict(rows[0], record_id=424242, s_assignment_id=9, joined_assignment_id=9)])
    assert gate(total_case, new_ledger=total_case["base"] / "ledger_extra.parquet")["verdict"] == "FAIL"


def test_total_gate_takes_expected_code_and_config_from_independent_sources_not_from_the_new_artifacts(total_case):
    wrong_code = gate(total_case, expected_code_sha256="0" * 64)
    assert wrong_code["verdict"] == "FAIL" and any("builder_code_sha256" in v and "allowlisted_rule_failed" in v for v in wrong_code["violations"])
    wrong_config = gate(total_case, expected_config_sha256="0" * 64)
    assert wrong_config["verdict"] == "FAIL" and any("build_config_sha256" in v and "allowlisted_rule_failed" in v for v in wrong_config["violations"])
    assert gate(total_case, new_version="ds_other")["verdict"] == "FAIL"


def test_total_gate_cross_checks_rows_and_columns_in_the_new_reports_against_the_measured_parquet(total_case):
    target = clone_case(total_case, "rows_cross_check")
    patch_json(target, "reports/features_labels.json", lambda d: d.update(rows=d["rows"] - 1))
    refresh_hashes(target)
    result = gate(total_case, new_dir=target)
    assert result["verdict"] == "FAIL" and any("rows_columns_mismatch" in v for v in result["violations"])


def test_total_gate_catches_a_tampered_value_in_the_real_new_parquet_even_when_the_tree_is_untouched(total_case):
    target = clone_case(total_case, "tampered_parquet")
    frame = pd.read_parquet(target / "samples.parquet")
    frame.loc[frame.index[7], "current_price"] = frame.loc[frame.index[7], "current_price"] + 1.0
    frame.to_parquet(target / "samples.parquet", index=False)
    result = gate(total_case, new_dir=target)
    assert result["verdict"] == "FAIL" and any("value_invariant_columns_differ" in v or "oracle" in v for v in result["violations"])


def test_total_gate_catches_a_typed_json_tamper_with_all_self_hashes_consistent(total_case):
    target = clone_case(total_case, "typed_tamper")
    patch_json(target, "reports/validation.json", lambda d: d["checks"][3].update(ok=1))
    refresh_hashes(target)
    result = gate(total_case, new_dir=target)
    assert result["verdict"] == "FAIL" and any("artifacts:" in v and "/checks[3]/ok" in v for v in result["violations"])


def test_the_cli_writes_a_unique_report_refuses_to_overwrite_and_exits_nonzero_on_fail(total_case, tmp_path, capsys):
    argv = ["--old-dir", str(OLD_REAL), "--new-dir", str(total_case["new_dir"]), "--old-ledger", str(total_case["base"] / "ledger_old.parquet"), "--new-ledger", str(total_case["base"] / "ledger_new.parquet"),
            "--old-version", OLD_V_REAL, "--new-version", NEW_V_REAL, "--expected-code-sha256", CODE_SHA, "--expected-config-sha256", CONFIG_SHA, "--expected-old-config-sha256", OLD_CONFIG_SHA, "--expected-old-code-sha256", OLD_CODE_SHA,
            "--expected-old-samples-sha256", OLD_SAMPLES_SHA, "--expected-producer-script-sha256", PROD_SCRIPT, "--expected-producer-sql-set-sha256", PROD_SQL,
            "--expected-base-controller-sha256", BASE_CTRL, "--out", str(tmp_path / "report.json")]
    for name, path in total_case["prov"].items():
        argv += ["--provenance", f"{name}={path}"]
    code = rp.main(argv)
    out = json.loads(capsys.readouterr().out)
    assert (code == 0) == (out["verdict"] == "PASS_REPAIR_PARITY")                                  # o runtime khac builder: FAIL do runtime; o runtime builder: PASS
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["receipt_sha256"] == out["receipt_sha256"]
    with pytest.raises(FileExistsError):
        rp.main(argv)


def test_the_cli_without_provenance_fails_closed(total_case, tmp_path, capsys):
    argv = ["--old-dir", str(OLD_REAL), "--new-dir", str(total_case["new_dir"]), "--old-ledger", str(total_case["base"] / "ledger_old.parquet"), "--new-ledger", str(total_case["base"] / "ledger_new.parquet"),
            "--old-version", OLD_V_REAL, "--new-version", NEW_V_REAL, "--expected-code-sha256", CODE_SHA, "--expected-config-sha256", CONFIG_SHA, "--expected-old-config-sha256", OLD_CONFIG_SHA, "--expected-old-code-sha256", OLD_CODE_SHA,
            "--expected-old-samples-sha256", OLD_SAMPLES_SHA, "--expected-producer-script-sha256", PROD_SCRIPT, "--expected-producer-sql-set-sha256", PROD_SQL,
            "--expected-base-controller-sha256", BASE_CTRL, "--out", str(tmp_path / "noprov.json")]
    assert rp.main(argv) == 1
    out = json.loads((tmp_path / "noprov.json").read_text(encoding="utf-8"))
    assert out["verdict"] == "FAIL" and any(v.startswith("provenance_missing") for v in out["violations"])


# =========================================================================== R: bien lai niem phong CAY artifact (moi byte, ke ca timing hop le) + provenance doc duoc
def test_the_receipt_changes_when_a_valid_timing_value_changes_even_though_the_gate_still_passes(total_case):
    baseline = gate(total_case)
    target = total_case["base"] / "timing_seal_parent" / NEW_V_REAL                                       # cay hop le MOI o duong dan khac (output_dir/samples_path la theo duong dan)
    derive_new_tree(OLD_REAL, target, version=NEW_V_REAL, code=CODE_SHA, config=CONFIG_SHA, content=total_case["measured"], samples_bytes=(total_case["new_dir"] / "samples.parquet").read_bytes())
    patch_json(target, "reports/split.json", lambda d: d.update(elapsed_s=d["elapsed_s"] + 1.0))
    refresh_hashes(target)
    prov = build_provenance(total_case, tag="timing_seal", new_dir=target)
    changed = gate(total_case, new_dir=target, provenance_paths=prov)
    assert baseline["verdict"] == changed["verdict"] == "PASS_REPAIR_PARITY", (baseline["violations"][:3], changed["violations"][:3])
    assert baseline["receipt"]["new_tree"]["files"]["reports/split.json"] != changed["receipt"]["new_tree"]["files"]["reports/split.json"]
    assert baseline["receipt"]["new_tree"]["aggregate_sha256"] != changed["receipt"]["new_tree"]["aggregate_sha256"] and baseline["receipt_sha256"] != changed["receipt_sha256"]


def test_the_receipt_seals_both_trees_every_file_and_the_options(total_case):
    r = gate(total_case)["receipt"]
    for side, root in (("old_tree", OLD_REAL), ("new_tree", total_case["new_dir"])):
        listed = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        assert sorted(r[side]["files"]) == listed and r[side]["files"]["samples.parquet"] == rp._file_sha(root / "samples.parquet")
        assert r[side]["aggregate_sha256"] == rp.sha256_hex(rp.canonical_json(r[side]["files"]))
    assert r["options"] == {"expected_rows": rp.EXPECTED_ROWS, "expected_columns": rp.EXPECTED_COLUMNS, "enforce_builder_runtime": False}
    assert set(r["provenance"]) == set(rp.PROVENANCE_KEYS) and all(len(v["sha256"]) == 64 for v in r["provenance"].values())


def test_tree_digest_covers_nested_files_and_is_sensitive_to_one_byte(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.json").write_text("{}", encoding="utf-8")
    (tmp_path / "y.txt").write_text("1", encoding="utf-8")
    first = rp.tree_digest(tmp_path)
    assert sorted(first["files"]) == ["a/b/x.json", "y.txt"]
    (tmp_path / "y.txt").write_text("2", encoding="utf-8")
    assert rp.tree_digest(tmp_path)["aggregate_sha256"] != first["aggregate_sha256"]


def tweak(name, fn):
    def mutate(docs):
        fn(docs[name])
    return mutate


OLD_FAM, NEW_FAM = "provenance_old_ledger_report_mismatch", "provenance_new_ledger_report_mismatch"
PROVENANCE_CONTROLS = {
    "old_report_not_ledger_pass": ("old_ledger_report", lambda d: d["verdict"].update(overall="FAIL"), OLD_FAM, "overall_ledger_pass"),
    "old_report_refused": ("old_ledger_report", lambda d: d.update(refusals=["x"]), OLD_FAM, "no_refusals"),
    "old_report_explain_mode": ("old_ledger_report", lambda d: d.update(mode="explain"), OLD_FAM, "mode_run"),
    "old_report_wrong_tool": ("old_ledger_report", lambda d: d.update(tool="other"), OLD_FAM, "tool"),
    "old_report_wrong_dataset": ("old_ledger_report", lambda d: d.update(dataset="ds_other"), OLD_FAM, "dataset"),
    "old_report_samples_sha": ("old_ledger_report", lambda d: d["parquet"].update(sha256="0" * 64), OLD_FAM, "parquet_sha"),
    "old_report_ledger_sha": ("old_ledger_report", lambda d: d["ledger"]["file"].update(sha256="0" * 64), OLD_FAM, "ledger_file_sha"),
    "old_report_expected_samples_sha": ("old_ledger_report", lambda d: d.update(expected_samples_sha256="0" * 64), OLD_FAM, "expected_samples_sha"),
    "old_report_ledger_status": ("old_ledger_report", lambda d: d["ledger"].update(status="UNVERIFIED"), OLD_FAM, "ledger_status_pass"),
    "new_report_ledger_status": ("new_ledger_report", lambda d: d["ledger"].update(status="FAIL"), NEW_FAM, "ledger_status_pass"),
    "old_report_step0_not_ok": ("old_ledger_report", lambda d: d["step0"].update(ok=False), OLD_FAM, "step0_ok_with_config_pin"),
    "old_report_step0_pin_not_checked": ("old_ledger_report", lambda d: d["step0"]["checks"].pop("build_config_sha256_pin"), OLD_FAM, "step0_ok_with_config_pin"),
    "old_report_config_pin_differs": ("old_ledger_report", lambda d: d["expected_config_sha256s"].update({OLD_V_REAL: "0" * 64}), OLD_FAM, "config_pin_matches_expected"),
    "old_report_no_config_pin": ("old_ledger_report", lambda d: d["expected_config_sha256s"].clear(), OLD_FAM, "config_pin_matches_expected"),
    "old_report_other_parity": ("old_ledger_report", lambda d: d.update(repair_parity_sha256="0" * 64), OLD_FAM, "repair_parity_sha_is_this_tool"),
    "old_report_other_parity_source": ("old_ledger_report", lambda d: d.update(repair_parity_source_sha256="0" * 64), OLD_FAM, "repair_parity_sha_is_this_tool"),
    "old_report_snapshot_failed": ("old_ledger_report", lambda d: d["snapshot"].update(status="FAIL"), OLD_FAM, "snapshot_status_pass"),
    "old_report_snapshot_untrusted": ("old_ledger_report", lambda d: d["snapshot"].update(file={"path": "x", "sha256": "0" * 64, "trusted": False}), OLD_FAM, "snapshot_status_pass"),
    "old_report_snapshot_other_file": ("old_ledger_report", lambda d: d["snapshot"].update(file={"path": "x", "sha256": "0" * 64, "trusted": True}), OLD_FAM, "snapshot_file_sha_is_provided"),
    "old_report_without_snapshot": ("old_ledger_report", lambda d: d.pop("snapshot"), OLD_FAM, "snapshot_status_pass"),
    "old_report_other_run_id": ("old_ledger_report", lambda d: d.update(run_id="someotherrun"), OLD_FAM, "snapshot_run_id_is_this_run"),
    "new_report_not_ledger_pass": ("new_ledger_report", lambda d: d["verdict"].update(overall="UNVERIFIED"), NEW_FAM, "overall_ledger_pass"),
    "new_report_config_pin_differs": ("new_ledger_report", lambda d: d["expected_config_sha256s"].update({NEW_V_REAL: "0" * 64}), NEW_FAM, "config_pin_matches_expected"),
    "new_report_step0_not_ok": ("new_ledger_report", lambda d: d["step0"].update(ok=False), NEW_FAM, "step0_ok_with_config_pin"),
    "new_report_other_parity": ("new_ledger_report", lambda d: d.update(repair_parity_sha256="0" * 64), NEW_FAM, "repair_parity_sha_is_this_tool"),
    "new_report_samples_sha": ("new_ledger_report", lambda d: d["parquet"].update(sha256="0" * 64), NEW_FAM, "parquet_sha"),
    "old_report_unapproved_script": ("old_ledger_report", lambda d: d.update(script_sha256="0" * 64), OLD_FAM, "producer_script_is_approved"),
    "new_report_unapproved_script": ("new_ledger_report", lambda d: d.update(script_sha256="0" * 64), NEW_FAM, "producer_script_is_approved"),
    "old_report_unapproved_sql_set": ("old_ledger_report", lambda d: d.update(sql_set_sha256="0" * 64), OLD_FAM, "producer_sql_set_is_approved"),
    "new_report_unapproved_sql_set": ("new_ledger_report", lambda d: d.update(sql_set_sha256="0" * 64), NEW_FAM, "producer_sql_set_is_approved"),
    "old_report_unapproved_base_controller": ("old_ledger_report", lambda d: d.update(base_controller_sha256="0" * 64), OLD_FAM, "base_controller_is_approved"),
    "new_report_unapproved_base_source": ("new_ledger_report", lambda d: d.update(base_source_sha256="0" * 64), NEW_FAM, "base_controller_is_approved"),
    "prediction_not_ok": ("config_prediction", lambda d: d.update(ok=False), "provenance_config_prediction_mismatch", "ok"),
    "prediction_other_config": ("config_prediction", lambda d: d.update(predicted_config_sha256="0" * 64), "provenance_config_prediction_mismatch", "predicted_config_sha"),
    "prediction_other_code": ("config_prediction", lambda d: d.update(new_code_sha256="0" * 64), "provenance_config_prediction_mismatch", "new_code_sha"),
    "prediction_wrong_kind": ("config_prediction", lambda d: d.update(kind="x"), "provenance_config_prediction_mismatch", "kind"),
    "snapshot_untrusted": ("old_config_snapshot", lambda d: d.update(trusted=False), "provenance_old_config_snapshot_mismatch", "trusted"),
    "snapshot_evaluation_failed": ("old_config_snapshot", lambda d: d["evaluation"].update(ok=False), "provenance_old_config_snapshot_mismatch", "evaluation_ok"),
    "snapshot_version_missing": ("old_config_snapshot", lambda d: d["evaluation"]["versions"].pop(OLD_V_REAL), "provenance_old_config_snapshot_mismatch", "old_version_present"),
    "snapshot_canonical_differs_from_expected_old": ("old_config_snapshot", lambda d: d["evaluation"]["versions"][OLD_V_REAL].update(canonical_config_sha256="0" * 64),
                                                     "provenance_old_config_snapshot_mismatch", "canonical_sha_matches_expected_old_config_sha"),
    "snapshot_db_sha_differs_from_expected_old": ("old_config_snapshot", lambda d: d["evaluation"]["versions"][OLD_V_REAL].update(build_config_sha256_in_db="0" * 64),
                                                  "provenance_old_config_snapshot_mismatch", "canonical_sha_matches_expected_old_config_sha"),
    "snapshot_old_code_differs": ("old_config_snapshot", lambda d: d["evaluation"]["versions"][OLD_V_REAL]["builder_code"].update(code_sha256="0" * 64),
                                  "provenance_old_config_snapshot_mismatch", "builder_code_sha_matches_expected_old_code"),
    "snapshot_old_code_missing": ("old_config_snapshot", lambda d: d["evaluation"]["versions"][OLD_V_REAL].pop("builder_code"), "provenance_old_config_snapshot_mismatch",
                                  "builder_code_sha_matches_expected_old_code"),
    "prediction_other_old_config": ("config_prediction", lambda d: d.update(old_config_sha256="0" * 64), "provenance_config_prediction_mismatch", "old_config_sha"),
    "prediction_other_old_code": ("config_prediction", lambda d: d.update(old_code_sha256="0" * 64), "provenance_config_prediction_mismatch", "old_code_sha"),
}


@pytest.mark.parametrize("control", list(PROVENANCE_CONTROLS))
def test_provenance_rejects_each_broken_link_with_the_specific_check_named(total_case, control):
    name, fn, family, check = PROVENANCE_CONTROLS[control]
    prov = build_provenance(total_case, tag=f"ctl_{control}", mutate=tweak(name, fn))
    result = gate(total_case, provenance_paths=prov)
    hits = [v for v in result["violations"] if v.startswith(family)]
    assert result["verdict"] == "FAIL" and hits and check in hits[0], result["violations"][:4]


def test_provenance_requires_all_four_documents_and_rejects_unknown_or_unreadable_ones(total_case, tmp_path):
    missing = dict(total_case["prov"])
    missing.pop("old_config_snapshot")
    assert any(v.startswith("provenance_missing") for v in gate(total_case, provenance_paths=missing)["violations"])
    assert any(v.startswith("provenance_missing") for v in gate(total_case, provenance_paths=None)["violations"])
    unknown = dict(total_case["prov"], extra=total_case["prov"]["old_config_snapshot"])
    assert any(v.startswith("provenance_unknown_keys") for v in gate(total_case, provenance_paths=unknown)["violations"])
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    result = gate(total_case, provenance_paths=dict(total_case["prov"], config_prediction=broken))
    assert result["verdict"] == "FAIL" and any(v.startswith("provenance_unreadable_config_prediction") for v in result["violations"])
    nonexistent = dict(total_case["prov"], new_ledger_report=tmp_path / "nope.json")
    assert any(v.startswith("provenance_unreadable_new_ledger_report") for v in gate(total_case, provenance_paths=nonexistent)["violations"])


def test_a_self_consistent_forged_old_config_is_caught_by_the_independent_old_anchor(total_case):
    forged = "7" * 64                                                                                    # DB gia + pin gia + bien lai gia + snapshot gia: moi thu NHAT QUAN voi nhau nhung KHAC hang so ghim ngoai DB

    def mutate(docs):
        docs["old_ledger_report"]["expected_config_sha256s"][OLD_V_REAL] = forged
        docs["config_prediction"]["old_config_sha256"] = forged
        entry = docs["old_config_snapshot"]["evaluation"]["versions"][OLD_V_REAL]
        entry["canonical_config_sha256"] = entry["build_config_sha256_in_db"] = forged

    prov = build_provenance(total_case, tag="forged_old", mutate=mutate)
    result = gate(total_case, provenance_paths=prov)
    text = " ".join(result["violations"])
    assert result["verdict"] == "FAIL" and "config_pin_matches_expected" in text and "old_config_sha" in text and "canonical_sha_matches_expected_old_config_sha" in text
    assert gate(total_case, provenance_paths=prov, expected_old_config_sha256=forged)["verdict"] == "PASS_REPAIR_PARITY"          # voi anchor GIA thi PASS: chinh anchor doc lap la thu chan
    assert gate(total_case)["receipt"]["expected_old_config_sha256"] == OLD_CONFIG_SHA and gate(total_case)["receipt"]["expected_old_code_sha256"] == OLD_CODE_SHA


def test_the_old_artifact_must_be_the_independently_pinned_samples_file(total_case):
    result = gate(total_case, expected_old_samples_sha256="0" * 64)
    assert result["verdict"] == "FAIL" and any(v.startswith("old_samples_sha_not_pinned") for v in result["violations"])
    assert gate(total_case)["receipt"]["expected_old_samples_sha256"] == OLD_SAMPLES_SHA


@pytest.mark.parametrize("anchor", ["expected_code_sha256", "expected_config_sha256", "expected_old_config_sha256", "expected_old_code_sha256", "expected_old_samples_sha256",
                                    "expected_producer_script_sha256", "expected_producer_sql_set_sha256", "expected_base_controller_sha256"])
@pytest.mark.parametrize("bad", [None, "", "abc", "G" * 64, "A" * 64, 7])
def test_every_independent_anchor_must_be_a_lowercase_sha256_hex_string(total_case, anchor, bad):
    result = gate(total_case, **{anchor: bad})
    assert result["verdict"] == "FAIL" and any(v.startswith("bad_anchor") and anchor in v for v in result["violations"]), result["violations"][:3]


def test_two_reports_that_agree_with_each_other_but_not_with_the_approved_producer_tuple_are_refused(total_case):
    def mutate(docs):                                                                                    # kich ban GPT 68 §3: ca hai bao cao cung doi ba hash thanh 0
        for name in ("old_ledger_report", "new_ledger_report"):
            for key in ("script_sha256", "sql_set_sha256", "base_controller_sha256", "base_source_sha256"):
                docs[name][key] = "0" * 64
    prov = build_provenance(total_case, tag="agree_but_unapproved", mutate=mutate)
    result = gate(total_case, provenance_paths=prov)
    text = " ".join(result["violations"])
    assert result["verdict"] == "FAIL" and all(token in text for token in ("producer_script_is_approved", "producer_sql_set_is_approved", "base_controller_is_approved"))
    zeros = "0" * 64
    ok = gate(total_case, provenance_paths=prov, expected_producer_script_sha256=zeros, expected_producer_sql_set_sha256=zeros, expected_base_controller_sha256=zeros)
    assert ok["verdict"] == "PASS_REPAIR_PARITY"                                                         # chinh tuple ngoai la thu chan; format hex 64 van hop le
    assert ok["receipt"]["approved_producer"] == {"script_sha256": zeros, "sql_set_sha256": zeros, "base_controller_sha256": zeros}


def test_producer_identity_fields_missing_from_the_reports_are_refused(total_case):
    def mutate(docs):
        for name in ("old_ledger_report", "new_ledger_report"):
            for key in ("script_sha256", "sql_set_sha256", "base_controller_sha256", "base_source_sha256"):
                docs[name].pop(key)
    prov = build_provenance(total_case, tag="no_identity", mutate=mutate)
    result = gate(total_case, provenance_paths=prov)
    assert result["verdict"] == "FAIL" and any("producer_script_is_approved" in v for v in result["violations"])


def test_the_receipt_carries_the_approved_producer_tuple(total_case):
    assert gate(total_case)["receipt"]["approved_producer"] == {"script_sha256": PROD_SCRIPT, "sql_set_sha256": PROD_SQL, "base_controller_sha256": BASE_CTRL}
