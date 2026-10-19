"""audit_tools/repair_parity_supp_v1.py — policy bo sung `label-timing-exception-v1` (GPT 146 §5a; control dang ky truoc o file 147, NHOM F = FIXTURE-ONLY).
Moi test dung hai thu muc JSON nho trong tmp_path + `original_result` TONG HOP (khong chay validate_repair, khong doc artifact that). Cac control NHOM R (chay validate_repair that voi tham so sai) nam o
`outputs/repair-run-20261009/run_parity_supp_nf.py`, KHONG o day. THUAN, khong DB."""
from __future__ import annotations

import importlib.util
import json
import math
import os
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[3] / "audit_tools"
MODULE_UNDER_TEST = Path(os.environ.get("SUPP_MODULE_UNDER_TEST") or TOOLS / "repair_parity_supp_v1.py")        # harness dot bien tro toi ban doi (cung thu muc phai co ban sao byte-identical cua repair_parity_v1.py)
spec = importlib.util.spec_from_file_location("repair_parity_supp_v1_t", MODULE_UNDER_TEST)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)
V1 = sp.load_v1()                                                                                                  # SHA ghim; neu v1 bi doi, MOI test o day loi => dung nhu mong doi
REL = sp.EXC_REL
FIELD = sp.EXC_FIELD
MISSING = object()


def write_json(directory: Path, rel: str, obj) -> None:
    path = directory / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, allow_nan=True), encoding="utf-8")


def make_pair(tmp_path: Path, old, new, *, extra_old=None, extra_new=None, rel: str = REL, pointer_obj_builder=None):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    for directory, value, extra in ((old_dir, old, extra_old), (new_dir, new, extra_new)):
        obj = {"rows": 1}
        if pointer_obj_builder is not None:
            obj = pointer_obj_builder(value)
        elif value is not MISSING:
            obj[FIELD] = value
        if extra:
            obj.update(extra)
        write_json(directory, rel, obj)
        if rel != REL:                                                                                              # file ngoai le luon ton tai nhung khong co field
            write_json(directory, REL, {"rows": 1})
    return old_dir, new_dir


def timing_violation(old_dir: Path, new_dir: Path, *, rel: str = REL, pointer: str = sp.EXC_POINTER) -> str:
    fo = V1.flatten(json.loads((old_dir / rel).read_text(encoding="utf-8")))
    fn = V1.flatten(json.loads((new_dir / rel).read_text(encoding="utf-8")))
    return f"artifacts: unallowlisted_diff: {rel}:{pointer}: {str(fo.get(pointer))[:70]} -> {str(fn.get(pointer))[:70]}"


def fake_result(violations, verdict=None):
    return {"verdict": verdict or ("FAIL" if violations else sp.V1_PASS), "violations": list(violations), "components": {}, "receipt": {}, "receipt_sha256": "0" * 64}


def evaluate(tmp_path, old, new, extra=(), **kw):
    old_dir, new_dir = make_pair(tmp_path, old, new, **kw)
    result = fake_result([timing_violation(old_dir, new_dir), *extra])
    return sp.apply_policy(result, old_dir, new_dir, V1)


# ============================================================================ POSITIVE
@pytest.mark.parametrize("old,new", [(26.1, 21.1), (0, 0.0), (0.0, 0), (5, 7), (1.5, 2.5), (0, 3.2), (100000, 0.25)])
def test_positive_exact_path_valid_numbers_pass_with_exception(tmp_path, old, new):
    out = evaluate(tmp_path, old, new)
    assert out["supplemental_verdict"] == sp.PASS_WITH_EXCEPTION, out
    assert out["original_v1_verdict"] == "FAIL" and out["refusals"] == [] and out["other_violations"] == []
    assert out["allowlisted"]["file"] == REL and out["allowlisted"]["pointer"] == "/label_build_seconds"


def test_equal_valid_values_with_original_pass_is_not_needed(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, 3.0, 3.0)
    out = sp.apply_policy(fake_result([]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.NOT_NEEDED and out["original_v1_verdict"] == sp.V1_PASS


# ============================================================================ NEGATIVE: kieu/presence o MOI ben (ke ca khi bang nhau)
BAD_VALUES = [("true", True), ("false", False), ("str", "21.1"), ("null", None), ("neg", -1), ("neg_small", -0.0001), ("nan", float("nan")), ("pinf", float("inf")), ("ninf", float("-inf")), ("list", []), ("dict", {})]


@pytest.mark.parametrize("name,bad", BAD_VALUES, ids=[b[0] for b in BAD_VALUES])
@pytest.mark.parametrize("side", ["old", "new"])
def test_invalid_value_on_either_side_fails(tmp_path, name, bad, side):
    old, new = (bad, 21.1) if side == "old" else (26.1, bad)
    old_dir, new_dir = make_pair(tmp_path, old, new)
    # violation text KHONG the dung duoc neu gia tri khong hop le; van dua mot chuoi y het de chung minh policy khong tin text
    out = sp.apply_policy(fake_result([timing_violation(old_dir, new_dir)]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL, out
    assert any(r.startswith(f"{side}_field_invalid") for r in out["refusals"]), out["refusals"]


@pytest.mark.parametrize("side", ["old", "new"])
def test_missing_field_on_either_side_fails(tmp_path, side):
    old, new = (MISSING, 21.1) if side == "old" else (26.1, MISSING)
    old_dir, new_dir = make_pair(tmp_path, old, new)
    out = sp.apply_policy(fake_result(["artifacts: unallowlisted_diff: reports/samples_labels.json:/label_build_seconds: ('float', 26.1) -> ('float', 21.1)"]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and f"{side}_field_missing" in out["refusals"]


@pytest.mark.parametrize("name,bad", BAD_VALUES, ids=[b[0] for b in BAD_VALUES])
def test_equal_but_invalid_values_with_original_pass_still_fail(tmp_path, name, bad):
    """'Khi bang nhau cung phai kiem type/presence': old == new == gia tri hong, v1 khong thay diff => van FAIL boi policy."""
    old_dir, new_dir = make_pair(tmp_path, bad, bad)
    out = sp.apply_policy(fake_result([]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and out["refusals"], out


def test_equal_missing_with_original_pass_still_fails(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, MISSING, MISSING)
    out = sp.apply_policy(fake_result([]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and "old_field_missing" in out["refusals"] and "new_field_missing" in out["refusals"]


def test_malformed_root_or_unreadable_file_fails(tmp_path):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    write_json(old_dir, REL, [1, 2])
    write_json(new_dir, REL, {FIELD: 1.0})
    out = sp.apply_policy(fake_result(["x"]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and "old_root_not_object" in out["refusals"]
    (new_dir / REL).unlink()
    out = sp.apply_policy(fake_result(["x"]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and any(r.startswith("new_file_unreadable") for r in out["refusals"])


# ============================================================================ NEGATIVE: sai VI TRI (khong endswith/glob/nested)
def test_same_field_name_in_another_file_fails(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, 26.1, 21.1)                                                              # file ngoai le co truong hop le, KHONG co vi pham cua no
    write_json(old_dir, "coverage_report.json", {FIELD: 1.0})
    write_json(new_dir, "coverage_report.json", {FIELD: 2.0})
    out = sp.apply_policy(fake_result([timing_violation(old_dir, new_dir, rel="coverage_report.json")]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and out["other_violations"], out


@pytest.mark.parametrize("pointer", ["/timing/label_build_seconds", "/label_build_seconds/x", "/label_build_seconds_total", "/label_build_seconds[0]", "/Label_Build_Seconds"])
def test_nested_or_lookalike_pointer_in_exception_file_fails(tmp_path, pointer):
    old_dir, new_dir = make_pair(tmp_path, 26.1, 21.1)
    # vi pham cua pointer KHAC nam CUNG file; trong khi truong chinh khong doi (bang nhau) => khong co vi pham cho phep
    old_obj = {FIELD: 5.0, "timing": {FIELD: 1.0}, "label_build_seconds_total": 2.0, "Label_Build_Seconds": 3.0}
    new_obj = {FIELD: 5.0, "timing": {FIELD: 9.0}, "label_build_seconds_total": 8.0, "Label_Build_Seconds": 7.0}
    write_json(old_dir, REL, old_obj)
    write_json(new_dir, REL, new_obj)
    fo, fn = V1.flatten(old_obj), V1.flatten(new_obj)
    key = pointer if pointer in fo and pointer in fn else "/timing/label_build_seconds"
    text = f"artifacts: unallowlisted_diff: {REL}:{pointer}: {str(fo.get(key))[:70]} -> {str(fn.get(key))[:70]}"
    out = sp.apply_policy(fake_result([text]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and out["other_violations"] == [text], out


def test_exception_text_must_equal_rebuilt_text_not_claimed_values(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, 26.1, 21.1)
    fake = "artifacts: unallowlisted_diff: reports/samples_labels.json:/label_build_seconds: ('float', 1.0) -> ('float', 2.0)"          # text noi 1.0 -> 2.0 trong khi JSON that la 26.1 -> 21.1
    out = sp.apply_policy(fake_result([fake]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and out["other_violations"] == [fake]


def test_exception_violation_duplicated_fails(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, 26.1, 21.1)
    text = timing_violation(old_dir, new_dir)
    out = sp.apply_policy(fake_result([text, text]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and "exception_violation_duplicated" in out["refusals"]


# ============================================================================ NEGATIVE: timing hop le + MOT vi pham KHONG-timing thuoc tung nhom
NON_TIMING = [
    "samples: flags.free_cancellation: unexpected cells [('raw=F', 'old=T', 'new=T')]",
    "samples: ledger_new: violations {'identity_mismatch': 3}",
    "samples: column ['price_per_night'] differs bitwise",
    "artifacts: unallowlisted_diff: dataset_contract.json:/x: ('int', 1) -> ('int', 2)",
    "artifacts: unallowlisted_diff: reports/samples_labels.json:/rows: ('int', 1) -> ('int', 2)",
    "artifacts: allowlisted_rule_failed: dataset_contract.json:/build_config_sha256: config_sha_new",
    "artifacts: file_set_differs: only_old=['a.json'] only_new=[]",
    "artifacts: must_differ_but_equal: dataset_contract.json:/dataset_version",
    "artifacts: rows_columns_mismatch: reported=(1, 2, 3, 4) measured=(5, 6, 7, 8)",
    "provenance_old_ledger_report_mismatch: ['producer_script_is_approved']",
    "provenance_new_ledger_report_mismatch: ['no_refusals']",
    "provenance_config_prediction_mismatch: ['predicted_config_sha']",
    "provenance_old_config_snapshot_mismatch: ['trusted']",
    "provenance_missing: ['old_ledger_report']",
    "old_samples_sha_not_pinned: measured aaa != pinned bbb",
    "content_sha256_not_measured",
    "runtime_not_builder: {'pandas': '2.3.3'} != {'pandas': '3.0.5'}",
    "exception: ValueError: boom",
    "bad_anchor: ['expected_code_sha256']",
]


@pytest.mark.parametrize("other", NON_TIMING, ids=[f"n{i:02d}" for i in range(len(NON_TIMING))])
def test_valid_timing_plus_any_non_timing_violation_fails(tmp_path, other):
    out = evaluate(tmp_path, 26.1, 21.1, extra=[other])
    assert out["supplemental_verdict"] == sp.FAIL and out["other_violations"] == [other] and out["original_v1_verdict"] == "FAIL"


def test_only_non_timing_violations_without_timing_fail(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, 5.0, 5.0)
    out = sp.apply_policy(fake_result([NON_TIMING[0]]), old_dir, new_dir, V1)
    assert out["supplemental_verdict"] == sp.FAIL and out["other_violations"] == [NON_TIMING[0]]


def test_inconsistent_or_malformed_original_result_fails(tmp_path):
    old_dir, new_dir = make_pair(tmp_path, 26.1, 21.1)
    assert sp.apply_policy({"verdict": "FAIL"}, old_dir, new_dir, V1)["supplemental_verdict"] == sp.FAIL
    assert sp.apply_policy({"verdict": "PASS_REPAIR_PARITY", "violations": [timing_violation(old_dir, new_dir)]}, old_dir, new_dir, V1)["supplemental_verdict"] == sp.FAIL
    assert sp.apply_policy(None, old_dir, new_dir, V1)["supplemental_verdict"] == sp.FAIL
    assert sp.apply_policy({"verdict": "WEIRD", "violations": []}, old_dir, new_dir, V1)["supplemental_verdict"] == sp.FAIL
    weird = sp.apply_policy({"verdict": "WEIRD", "violations": [timing_violation(old_dir, new_dir)]}, old_dir, new_dir, V1)                 # verdict la KEM dung vi pham timing: khong duoc coi la FAIL-v1 hop le
    assert weird["supplemental_verdict"] == sp.FAIL and weird["refusals"] == ["original_result_malformed"]
    assert sp.apply_policy({"verdict": "FAIL", "violations": [timing_violation(old_dir, new_dir)], "extra": 1}, old_dir, new_dir, V1)["supplemental_verdict"] == sp.PASS_WITH_EXCEPTION   # khoa du thua khong lam hong ket qua


# ============================================================================ NEO NGUON
def test_v1_sha_pin_is_the_approved_value():
    assert sp.PINNED_V1_SHA256 == "1022c5ee9f0227536413636d5bcbb46fc18a0d59849df54d53eb83e75cd83510"
    assert V1.__source_sha256__ == sp.PINNED_V1_SHA256


def test_loading_a_different_v1_is_refused_before_exec(tmp_path):
    altered = tmp_path / "repair_parity_v1.py"
    altered.write_bytes((TOOLS / "repair_parity_v1.py").read_bytes() + b"\n# altered\n")
    with pytest.raises(ValueError, match="repair_parity_v1_sha_not_pinned"):
        sp.load_v1(altered)
    # file ngoai le khong ton tai o duong dan cho truoc cung khong the nap
    with pytest.raises(OSError):
        sp.load_v1(tmp_path / "missing.py")


@pytest.mark.parametrize("value,ok", [(0, True), (0.0, True), (1, True), (2.5, True), (-0.0, True), (True, False), (False, False), (None, False), ("1", False), (-1, False), (math.nan, False), (math.inf, False), (-math.inf, False), ([1], False), ({}, False)])
def test_valid_timing_matrix(value, ok):
    assert sp.valid_timing(value) is ok
