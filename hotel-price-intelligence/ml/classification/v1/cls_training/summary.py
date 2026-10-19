"""Tom tat doc-duoc cua mot run da hoan tat (cho notebook Colab va CLI): KHONG tinh lai metric, chi doc `h{K}_report.json` + `h{K}_analysis.json` cua run da qua `verify_run_dir`.

Quy tac dien dat dung o day (EVAL_PROTOCOL §7): moi so la dev/exploratory; test da lo cho regression; `claim_level` khong phai cong trien khai; run `contract_complete=false` KHONG phai ket qua nghien cuu cuoi;
comparator thay the bi gan co; thieu phan test cua comparator hien la `None` (khong phai 'stable'). Tom tat khong dien so thay the vao truong nao.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .transaction import verify_run_dir


def _load(run_dir: Path, name: str) -> dict[str, Any]:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def _score(block: dict[str, Any] | None) -> dict[str, Any]:
    if not block:
        return {"macro_f1": None, "mcc": None, "n": None}
    return {"macro_f1": block.get("macro_f1"), "mcc": block.get("mcc"), "n": block.get("n")}


def summarize_horizon(run_dir: Path, horizon: int) -> dict[str, Any]:
    report = _load(run_dir, f"h{horizon}_report.json")
    warnings: list[str] = []
    if not report.get("contract_complete"):
        warnings.append(f"contract_complete=false ({report.get('status')}): {report.get('incomplete_reasons')}")
    suff = report.get("data_sufficiency") or {}
    if suff.get("short_window"):
        warnings.append(f"cua so ngan: ngay du doan hop le {suff.get('eligible_prediction_dates')} (<7 ngay validation/test)")
    if suff.get("status") not in (None, "official"):
        warnings.append(f"dataset sufficiency={suff.get('status')} failed_gates={suff.get('failed_gates')} (dev/exploratory)")
    out: dict[str, Any] = {"horizon": horizon, "status": report.get("status"), "contract_complete": bool(report.get("contract_complete")), "champion": report.get("champion"),
                           "incomplete_reasons": report.get("incomplete_reasons") or [], "data_sufficiency": suff, "device_ledger": (report.get("ledger") or {}).get("devices"),
                           "elapsed_seconds": (report.get("ledger") or {}).get("elapsed_seconds"), "claim_level": "none", "finalists": [], "panel": [], "claim": None, "ablations": {},
                           "warnings": warnings, "probability_status": report.get("probability_status")}
    if report.get("champion") is None:
        return out
    models = report.get("models", {})
    test_models = (report.get("test") or {}).get("models", {})
    cv_by_id = {c["id"]: c for c in (report.get("cv") or {}).get("candidates", [])}
    for fin in report.get("finalists", []):
        meta, cv = models.get(fin["id"], {}), cv_by_id.get(fin["id"], {})
        out["finalists"].append({"id": fin["id"], "family": fin["family"], "weight": (meta.get("params") or {}).get("weight"), "is_champion": fin["id"] == report["champion"],
                                 "cv_pooled_macro_f1": (cv.get("pooled") or {}).get("macro_f1"), "validation": _score(fin.get("validation")), "test": _score(test_models.get(fin["id"])),
                                 "device": meta.get("device")})
    analysis_path = run_dir / f"h{horizon}_analysis.json"
    if not analysis_path.is_file():
        warnings.append("khong co h{K}_analysis.json: chua co panel/claim")
        return out
    analysis = _load(run_dir, f"h{horizon}_analysis.json")
    out["claim_level"] = analysis.get("claim_level", "none")
    claim = analysis.get("claim")
    if claim:
        out["claim"] = {k: claim.get(k) for k in ("validation_comparator_id", "validation_delta", "validation_delta_ci95", "validation_gate_passed", "diagnostic_validation_gate_passed", "claim_block_reasons", "practical_gain_threshold", "recall_validation",
                                                  "is_substitute", "test_available_comparator_id", "test_delta_vs_validation_comparator", "test_delta_vs_available_comparator",
                                                  "test_delta_vs_available_comparator_ci95", "same_comparator_test_status")}
        if claim.get("is_substitute"):
            warnings.append(f"comparator validation {claim.get('validation_comparator_id')} khong duoc cham tren test; doi chung thay the {claim.get('test_available_comparator_id')} (test: not_evaluable)")
    for row in (analysis.get("panel") or {}).get("table", []):
        out["panel"].append({"id": row["id"], "family": row.get("family"), "validation": _score(row.get("validation")), "test": _score(row.get("test")), "scored_on_test": row.get("test") is not None})
    for key, abl in (analysis.get("ablations_vs_champion") or {}).items():
        out["ablations"][key] = abl if abl.get("status") != "ok" else {"status": "ok", "model_id": abl["model_id"], "validation_delta": abl["validation"]["point"], "validation_ci95": abl["validation"]["ci95"],
                                                                      "test_delta": abl["test"]["point"], "test_ci95": abl["test"]["ci95"]}
    loo = ((analysis.get("breakdowns") or {}).get("test") or {}).get("leave_one_out") or {}
    if loo.get("top1_hotel"):
        out["test_concentration"] = {"top1_hotel": loo["top1_hotel"], "delta_all": loo.get("delta_all"), "delta_without_top1": loo.get("delta_without_top1"),
                                     "delta_without_top_k": loo.get("delta_without_top_k"), "k": loo.get("k"), "additive": loo.get("additive")}
    warnings.append("dev/exploratory: test da lo cho regression; claim_level KHONG phai cong trien khai")
    return out


def summarize_run(run_dir: Path | str) -> dict[str, Any]:
    run_dir = Path(run_dir)
    manifest = verify_run_dir(run_dir)                      # hash tung file + COMPLETE; loi => TransactionError
    h = int(manifest["identity"]["horizon"])
    out = summarize_horizon(run_dir, h)
    out["run_id"] = manifest["run_id"]
    out["manifest_status"] = manifest.get("status")
    out["manifest_contract_complete"] = manifest.get("contract_complete")
    return out
