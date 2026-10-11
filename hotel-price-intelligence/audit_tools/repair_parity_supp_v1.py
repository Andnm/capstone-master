"""THUAN (khong DB, khong ghi artifact cu): evaluator BO SUNG `label-timing-exception-v1` cho cong `repair_parity_v1.validate_repair` (GPT file 146 §5a, Claude xac nhan truoc khi do o file 147).

KHONG sua `repair_parity_v1.py` (SHA ghim `1022c5ee…`, la dependency pin cua producer ledger). Module nay:
  1. nap `repair_parity_v1.py` theo CHINH byte da bam va TU CHOI neu SHA khac ban ghim;
  2. goi NGUYEN `validate_repair` de TAI TINH toan bo ket qua goc (khong tin text receipt);
  3. ap policy len danh sach `violations` cua ket qua do: DUNG MOT ngoai le — file `reports/samples_labels.json`, root pointer `/label_build_seconds`, ca old/new ton tai, kieu int/float (khong bool/str/null/list/dict),
     huu han, >= 0; khong rang buoc bang nhau/nguong toc do; kiem kieu/presence LUON chay ke ca khi old == new. Chuoi vi pham cho phep phai BANG dung chuoi dung lai tu gia tri co kieu doc truc tiep tu hai file JSON.
     Bat ky vi pham nao khac => FAIL. Verdict v1 khong bao gio bi ghi de (`original_v1_verdict` luon duoc ghi).
Khong mo rong whitelist: them truong khac = policy version moi."""
from __future__ import annotations

import hashlib
import json
import math
import types
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
PINNED_V1_SHA256 = "1022c5ee9f0227536413636d5bcbb46fc18a0d59849df54d53eb83e75cd83510"
POLICY_ID = "label-timing-exception-v1"
POLICY_AUTHOR = "Claude (implementer); GPT file 146 §5a la contract, chua duoc GPT ky"
EXC_REL = "reports/samples_labels.json"
EXC_FIELD = "label_build_seconds"
EXC_POINTER = "/" + EXC_FIELD
V1_PASS = "PASS_REPAIR_PARITY"
PASS_WITH_EXCEPTION = "PASS_WITH_LABEL_TIMING_EXCEPTION"
NOT_NEEDED = "NOT_NEEDED_ORIGINAL_PASS"
FAIL = "FAIL"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_v1(path: Path | None = None, expected_sha256: str = PINNED_V1_SHA256):
    """Nap `repair_parity_v1` tu CHINH byte da bam; SHA != ghim => ValueError truoc khi exec."""
    path = Path(path) if path is not None else HERE / "repair_parity_v1.py"
    data = path.read_bytes()
    digest = sha256_bytes(data)
    if digest != expected_sha256:
        raise ValueError(f"repair_parity_v1_sha_not_pinned: {digest} != {expected_sha256}")
    module = types.ModuleType("repair_parity_v1_for_supp")
    module.__file__ = str(path)
    exec(compile(data, str(path), "exec"), module.__dict__)                                           # noqa: S102
    module.__source_sha256__ = digest
    return module


def canon(obj: Any) -> str:
    """JSON chuan (khoa sap, khong khoang trang, default=str cho kieu la): dung de bam toan bo ket qua goc/receipt bo sung."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def valid_timing(value: Any) -> bool:
    """int/float THUAN (type chinh xac: bool bi loai), huu han, >= 0."""
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _read_field(directory: Path, which: str) -> tuple[list[str], Any, Any]:
    """(refusals, gia_tri, co_ton_tai). Doc JSON truc tiep (Python chap nhan NaN/Infinity nen kiem bang `valid_timing`)."""
    path = Path(directory) / EXC_REL
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{which}_file_unreadable: {type(exc).__name__}"], None, False
    if not isinstance(obj, dict):
        return [f"{which}_root_not_object"], None, False
    if EXC_FIELD not in obj:
        return [f"{which}_field_missing"], None, False
    value = obj[EXC_FIELD]
    if not valid_timing(value):
        return [f"{which}_field_invalid: {type(value).__name__}"], value, True
    return [], value, True


def apply_policy(result: Mapping[str, Any], old_dir: Path, new_dir: Path, v1: Any) -> dict[str, Any]:
    """Ap policy len MOT ket qua `validate_repair`. Tra khoi `supplemental` (khong tu chay lai v1)."""
    refusals: list[str] = []
    if not isinstance(result, Mapping) or not isinstance(result.get("violations"), list) or result.get("verdict") not in (V1_PASS, FAIL):
        return {"policy_id": POLICY_ID, "original_v1_verdict": (result or {}).get("verdict") if isinstance(result, Mapping) else None, "supplemental_verdict": FAIL,
                "refusals": ["original_result_malformed"], "allowlisted": None, "other_violations": []}
    verdict_v1 = result["verdict"]
    violations = [str(v) for v in result["violations"]]
    if (verdict_v1 == V1_PASS) != (not violations):
        refusals.append("original_verdict_inconsistent_with_violations")
    old_refusals, old_value, _ = _read_field(Path(old_dir), "old")
    new_refusals, new_value, _ = _read_field(Path(new_dir), "new")
    refusals += old_refusals + new_refusals
    expected = None
    allowlisted: dict[str, Any] | None = None
    if not old_refusals and not new_refusals:
        try:
            old_entry = v1.flatten(json.loads((Path(old_dir) / EXC_REL).read_text(encoding="utf-8")))[EXC_POINTER]
            new_entry = v1.flatten(json.loads((Path(new_dir) / EXC_REL).read_text(encoding="utf-8")))[EXC_POINTER]
        except (OSError, ValueError, KeyError) as exc:
            refusals.append(f"flatten_failed: {type(exc).__name__}")
        else:
            expected = f"artifacts: unallowlisted_diff: {EXC_REL}:{EXC_POINTER}: {str(old_entry)[:70]} -> {str(new_entry)[:70]}"
            allowlisted = {"file": EXC_REL, "pointer": EXC_POINTER, "old": {"kind": old_entry[0], "value": old_value}, "new": {"kind": new_entry[0], "value": new_value}}
    matched = [v for v in violations if expected is not None and v == expected]
    others = [v for v in violations if v not in matched]
    if len(matched) > 1:
        refusals.append("exception_violation_duplicated")
    if verdict_v1 == V1_PASS:
        verdict = FAIL if refusals else NOT_NEEDED
    else:
        verdict = PASS_WITH_EXCEPTION if (not refusals and not others and len(matched) == 1) else FAIL
    if verdict == FAIL and verdict_v1 == FAIL and not matched and expected is None:
        refusals.append("exception_not_applicable")
    return {"policy_id": POLICY_ID, "original_v1_verdict": verdict_v1, "supplemental_verdict": verdict, "refusals": refusals, "allowlisted": allowlisted,
            "matched_violations": matched, "other_violations": others, "expected_violation_text": expected}


def run_supplemental(*, v1: Any | None = None, **kwargs: Any) -> dict[str, Any]:
    """Chay NGUYEN `validate_repair` (tham so `kwargs` y het v1) roi ap policy; tra receipt bo sung dung nguon."""
    v1 = v1 or load_v1()
    original = v1.validate_repair(**kwargs)
    policy = apply_policy(original, Path(kwargs["old_dir"]), Path(kwargs["new_dir"]), v1)
    original_receipt = original.get("receipt", {})
    receipt: dict[str, Any] = {
        "kind": "supplemental_parity_receipt", "policy_id": POLICY_ID, "author": POLICY_AUTHOR, "original_v1_verdict": original.get("verdict"),
        "supplemental_verdict": policy["supplemental_verdict"], "policy": policy,
        "sources": {"policy_module_sha256": sha256_bytes(Path(__file__).read_bytes()), "repair_parity_v1_sha256": v1.__source_sha256__, "pinned_v1_sha256": PINNED_V1_SHA256},
        "original_result_sha256": sha256_bytes(canon(original).encode("utf-8")),
        "original_receipt_sha256": original.get("receipt_sha256"), "original_violations": original.get("violations"),
        "inputs": {k: original_receipt.get(k) for k in ("old_version", "new_version", "old_samples_file_sha256", "new_samples_file_sha256", "old_ledger_file_sha256", "new_ledger_file_sha256", "new_content_sha256_measured",
                                                       "expected_code_sha256", "expected_config_sha256", "expected_old_config_sha256", "expected_old_code_sha256", "expected_old_samples_sha256", "approved_producer", "runtime")},
        "provenance": original_receipt.get("provenance"), "trees": {"old": (original_receipt.get("old_tree") or {}).get("aggregate_sha256"), "new": (original_receipt.get("new_tree") or {}).get("aggregate_sha256")},
    }
    receipt["supplemental_receipt_sha256"] = sha256_bytes(canon({k: v for k, v in receipt.items() if k != "supplemental_receipt_sha256"}).encode("utf-8"))
    return receipt
