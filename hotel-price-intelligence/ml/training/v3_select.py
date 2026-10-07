"""Chon mo hinh v3 (C5'/C12/C17): persistence thang khi hoa; top-tie group quanh best hop le; metric undefined xuong sau; dinh tuyen mode tu VALIDATION.

Quy tac ghim (GPT file 10 MAJOR-M1/file 12 P12-M1): MOT hang so `tie_atol` tren thang lift (so voi persistence tren cung hang), dung o CV, validation, routing va champion.
Chon top group quanh BEST hop le (tranh comparator epsilon doi mot khong bac cau), roi ap thu tu dinh danh da pin. Persistence (lift = 0) thang khi nam trong top group.
Tuyet doi khong dung ordinary/null/log de chan khi chon (chi la canh bao pham vi tuyen bo).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .v3_contract import ARM_METRIC, PERSISTENCE


def _ok(value: Any) -> bool:
    return value is not None and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def top_tie_group(scores: Mapping[str, Any], tie_atol: float) -> tuple[float | None, list[str]]:
    """(best, ten nam trong [best - tie_atol, best]); khong co diem hop le => (None, [])."""
    valid = {name: float(value) for name, value in scores.items() if _ok(value)}
    if not valid:
        return None, []
    best = max(valid.values())
    return best, [name for name, value in valid.items() if value >= best - tie_atol]


def pick_with_persistence(scores: Mapping[str, Any], order: Sequence[str], tie_atol: float) -> dict[str, Any]:
    """Chon theo diem (cao hon tot hon). `scores` KHONG can chua persistence: persistence luon duoc them voi diem 0.0 va THANG KHI HOA.
    Hoa giua cac ung vien hoc duoc: thu tu `order`, ten khong co trong `order` xep sau theo ten (xac dinh). Diem undefined (None/NaN) xep sau moi diem hop le."""
    pool = dict(scores)
    pool[PERSISTENCE] = 0.0
    best, group = top_tie_group(pool, tie_atol)
    undefined = sorted(name for name, value in scores.items() if not _ok(value))
    if best is None:
        return {"winner": PERSISTENCE, "reason": "no_valid_scores", "best": None, "tie_group": [], "undefined": undefined}
    if PERSISTENCE in group:
        reason = "persistence_in_top_tie_group" if len(group) > 1 else "persistence_best"
        return {"winner": PERSISTENCE, "reason": reason, "best": best, "tie_group": sorted(group), "undefined": undefined}
    rank = {name: i for i, name in enumerate(order)}
    winner = sorted(group, key=lambda name: (rank.get(name, len(rank)), name))[0]
    return {"winner": winner, "reason": "learned_best" if len(group) == 1 else "learned_tie_group_ordered", "best": best, "tie_group": sorted(group),
            "undefined": undefined}


def pick_family_winner(scores: Mapping[str, Any], order: Sequence[str], tie_atol: float) -> dict[str, Any]:
    """Chon candidate tot nhat TRONG mot ho theo diem CV (khong co persistence trong tap): top group quanh best hop le, thu tu pool. Tat ca undefined => khong co winner."""
    best, group = top_tie_group(scores, tie_atol)
    undefined = sorted(name for name, value in scores.items() if not _ok(value))
    if best is None:
        return {"winner": None, "reason": "no_valid_scores", "best": None, "tie_group": [], "undefined": undefined}
    rank = {name: i for i, name in enumerate(order)}
    winner = sorted(group, key=lambda name: (rank.get(name, len(rank)), name))[0]
    return {"winner": winner, "reason": "best" if len(group) == 1 else "tie_group_ordered", "best": best, "tie_group": sorted(group), "undefined": undefined}


def champion(arm: str, candidates: Mapping[str, Mapping[str, Any]], order: Sequence[str], tie_atol: float) -> dict[str, Any]:
    """Champion cua mot arm tren VALIDATION: arm A theo lift_vnd, arm B theo lift_log. Moi arm CHI nhin metric cua minh (khong dung lift_vnd de chan B)."""
    metric = ARM_METRIC[arm]
    out = pick_with_persistence({name: row.get(metric) for name, row in candidates.items()}, order, tie_atol)
    out["arm"], out["metric"] = arm, metric
    return out


def mode_policy(stats: Mapping[str, Any], routing: Mapping[str, Any], tie_atol: float) -> dict[str, Any]:
    """Chinh sach theo mode (C12): dung model khi du support (rows/hotels/dates) VA lift_vnd > tie_atol VA can duoi CI hotel (quan the ca split) > 0; nguoc lai persistence + ly do.
    `stats` = ket qua EvalSet.mask_lift cua mode tren VALIDATION. Khong dung ordinary, khong dung TEST."""
    n, hotels, dates = int(stats.get("n", 0)), int(stats.get("n_hotels_active", 0)), int(stats.get("n_dates", 0))
    support = {"rows": n, "hotels": hotels, "dates": dates, "min_rows": int(routing["min_rows"]), "min_hotels": int(routing["min_hotels"]),
               "min_dates": int(routing["min_dates"])}
    lift, ci = stats.get("lift_vnd"), stats.get("ci95_hotel_full_split_population")
    if n < support["min_rows"] or hotels < support["min_hotels"] or dates < support["min_dates"]:
        return {"use_model": False, "reason": "insufficient_support", "support": support, "lift_vnd": lift, "ci95": ci}
    if not _ok(lift) or not ci or not all(_ok(c) for c in ci):
        return {"use_model": False, "reason": "undefined_lift_or_ci", "support": support, "lift_vnd": lift, "ci95": ci}
    if lift <= tie_atol:
        return {"use_model": False, "reason": "lift_not_positive", "support": support, "lift_vnd": lift, "ci95": ci}
    if ci[0] <= 0:
        return {"use_model": False, "reason": "ci_lower_not_positive", "support": support, "lift_vnd": lift, "ci95": ci}
    return {"use_model": True, "reason": "ok", "support": support, "lift_vnd": lift, "ci95": ci}


def ablation_decision(*, incr_lift: float | None, incr_ci_lower: float | None, per_fold_block_mae: Sequence[float], per_fold_raw_mae: Sequence[float],
                      cfg: Mapping[str, Any]) -> dict[str, Any]:
    """C10': chap nhan khoi khi (i) loi the gia tang tren validation > 0 voi can duoi CI hotel > 0 va (ii) MAE VND CV cua khoi THAP HON raw o TUNG fold.
    Quyet theo tung horizon, chi TRAIN/CV/VALIDATION cua chinh no."""
    rule = cfg["accept"]
    gate_i = bool(_ok(incr_lift) and incr_lift > 0 and _ok(incr_ci_lower) and incr_ci_lower > float(rule["validation_incr_lift_ci_lower_gt"]))
    folds_ok = (len(per_fold_block_mae) == len(per_fold_raw_mae) > 0 and all(b < r for b, r in zip(per_fold_block_mae, per_fold_raw_mae)))
    gate_ii = bool(folds_ok) if rule.get("cv_every_fold_mae_vnd_lower", True) else True
    return {"accepted": bool(gate_i and gate_ii), "validation_incr_lift_ci_lower_gt0": gate_i, "cv_every_fold_mae_lower": bool(folds_ok),
            "incr_lift_vnd": incr_lift, "incr_ci_lower": incr_ci_lower, "per_fold_block_mae_vnd": list(per_fold_block_mae), "per_fold_raw_mae_vnd": list(per_fold_raw_mae)}


def null_summary(real: Mapping[str, Any], nulls: Sequence[Mapping[str, Any]], *, requested: int | None = None) -> dict[str, Any]:
    """C11: so lift that voi null (heuristic, khong phai kiem dinh 5%). Ghi requested/attempted/successful/failed + `status`: 'ok' CHI khi du `requested` null hop le (lift VND va log huu han);
    'incomplete' neu thieu; 'failed' neu khong null nao hop le. rank_p = (1 + #{null >= real})/(requested+1) chi khi DU; thieu => `rank_p=None` va `partial_rank_p_diagnostic`
    voi mau so ghi ro (khong goi rank cua 9 null la rank cua phep 10 null). null > 2% => 'can dieu tra', khong ket luan ro ri (GPT file 16 M3)."""
    requested = len(nulls) if requested is None else int(requested)
    valid = [r for r in nulls if _ok(r.get("lift_vnd")) and _ok(r.get("lift_log"))]
    complete = requested > 0 and len(valid) == requested and len(nulls) == requested
    out: dict[str, Any] = {"requested": requested, "attempted": len(nulls), "successful": len(valid), "failed": len(nulls) - len(valid), "complete": complete,
                           "status": "ok" if complete else ("failed" if not valid else "incomplete")}
    for key in ("lift_vnd", "lift_log"):
        values = [r[key] for r in valid]
        r_real = real.get(key)
        defined = _ok(r_real) and bool(values)
        entry: dict[str, Any] = {"real": r_real, "max_null": max(values) if values else None,
                                 "real_gt_max_null": bool(defined and r_real > max(values)) if complete else None,
                                 "rank_p": ((1 + sum(v >= r_real for v in values)) / (requested + 1)) if (complete and defined) else None}
        if not complete and defined:
            entry["partial_rank_p_diagnostic"] = (1 + sum(v >= r_real for v in values)) / (len(values) + 1)
            entry["partial_rank_denominator"] = len(values) + 1
        out[key] = entry
    out["null_lift_gt_2pct_flag"] = bool(any(r[k] > 0.02 for r in valid for k in ("lift_vnd", "lift_log")))
    out["note"] = ("heuristic, khong phai kiem dinh 5%; null > 2% = can dieu tra day-prior/availability/artifact, khong ket luan ro ri; negative control, "
                   "khong thay audit feature-availability; null thieu/hong KHONG duoc coi la phep do hoan tat")
    return out
