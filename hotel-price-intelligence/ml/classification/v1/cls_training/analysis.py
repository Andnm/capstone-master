"""Phan tich ket qua mot horizon (EVAL_PROTOCOL §4, §6, §7) tu file du doan + snapshot baseline: panel, delta ghep cap co CI cum hotel, claim, phan ra, chan doan xac suat.

Chay giong het trong notebook Colab va cuc bo (cung ma): doc `predictions` (da luu) + `snapshot` (da ghim) + `report`; KHONG fit gi, khong doc dataset.
- Comparator: validation comparator (gate) / test-available comparator (co the la thanh vien thay the, `is_substitute`) / C0 / C1 / moi thanh vien panel duoc cham tren split.
- `validation_gate_passed` = delta validation >= practical_gain va can duoi CI > 0 va khong lop nao recall = 0 (tren validation) **VA** report `status=complete` + `contract_complete=true`;
  run incomplete van giu so do (`diagnostic_validation_gate_passed`) nhung `claim_level='none'` (claim_block_reasons=['training_contract_incomplete']). Test: truong rieng (xem panel.same_comparator_test_status); KHONG co truong nao gate trien khai.
- Claim `dev_exploratory_validation_gain` chi khi gate qua; CI la khoang sau chon loc, tham do; khong chung minh tong quat hoa.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import metrics as M
from .artifact import validate_prediction_artifact
from .bootstrap import equal_hotel_summary, leave_one_out, paired_bootstrap_delta
from .labels import class_index
from .panel import candidate_directions, dedup, evaluate_panel, momentum_direction, same_comparator_test_status

SPLITS = ("validation", "test")
PROBA_COLS = ["p_down", "p_stable", "p_up"]


class AnalysisError(RuntimeError):
    pass


def _rows(pred: pd.DataFrame, model_id: str, split: str) -> pd.DataFrame:
    rows = pred[(pred["model_id"] == model_id) & (pred["split"] == split)]
    if "is_primary" in rows.columns:
        rows = rows[rows["is_primary"]]
    return rows.sort_values("warehouse_record_id", kind="mergesort").reset_index(drop=True)


def _snap_split(snap: pd.DataFrame, split: str) -> pd.DataFrame:
    return snap[snap["split"] == split].sort_values("warehouse_record_id", kind="mergesort").reset_index(drop=True)


def _delta(hotels: Any, y: np.ndarray, cand: np.ndarray, comp: np.ndarray, cfg: dict[str, Any]) -> dict[str, Any]:
    b = cfg["bootstrap"]
    return paired_bootstrap_delta(hotels, y, cand, comp, n=int(b["n"]), seed=int(b["seed"]), min_valid_fraction=float(b["min_valid_fraction"]))


def _reliability(y: np.ndarray, proba: np.ndarray, k: int, bins: int = 10) -> dict[str, Any]:
    """Bang do tin cay mo ta (khong fit) cho P(lop k): cac bin ngang, n, trung binh du doan, tan suat quan sat."""
    p, obs = proba[:, k], (y == k).astype(float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, bins - 1)
    return {"class": M.CLASS_NAMES[k], "bins": [{"lo": float(edges[b]), "hi": float(edges[b + 1]), "n": int((idx == b).sum()),
                                                  "mean_predicted": float(p[idx == b].mean()) if (idx == b).any() else None,
                                                  "observed_rate": float(obs[idx == b].mean()) if (idx == b).any() else None} for b in range(bins)]}


def _group_metrics(df: pd.DataFrame, by: str, comparator_pred: np.ndarray | None) -> list[dict[str, Any]]:
    out = []
    for key, g in df.groupby(by, dropna=False, sort=True):
        s = M.summarize(g["true_class"].to_numpy(), g["pred_class"].to_numpy())
        row = {"group": None if pd.isna(key) else str(key), "n": s["n"], "support": s["support"], "macro_f1": s["macro_f1"], "mcc": s["mcc"], "null_reason": s["null_reason"],
               "predicted_change_rate": s["predicted_change_rate"], "confusion": s["confusion"], "per_class": s["per_class"]}
        if comparator_pred is not None:
            c = M.summarize(g["true_class"].to_numpy(), comparator_pred[g.index.to_numpy()])
            row["comparator_macro_f1"] = c["macro_f1"]
            row["comparator_confusion"], row["comparator_per_class"] = c["confusion"], c["per_class"]
            row["delta_macro_f1"] = None if s["macro_f1"] is None or c["macro_f1"] is None else s["macro_f1"] - c["macro_f1"]
        out.append(row)
    return out


def analyze_horizon(predictions: pd.DataFrame, report: dict[str, Any], snap: pd.DataFrame, snap_manifest: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    h = int(report["horizon"])
    if report.get("champion") is None:
        return {"horizon": h, "claim_level": "none", "reason": report.get("status"), "validation_gate_passed": False}
    champ_id = report["champion"]
    artifact = validate_prediction_artifact(predictions, report, snap, tolerance=float(cfg["run"]["probability_tolerance"]))        # TRUOC moi metric/CI/claim
    panel = evaluate_panel(snap, snap_manifest, tie_atol=float(cfg["panel"]["tie_atol"]), momentum_threshold=float(cfg["panel"]["momentum_threshold"]))
    kept, aliases = dedup(candidate_directions(snap, snap_manifest, momentum_threshold=float(cfg["panel"]["momentum_threshold"])))
    members = {m["id"]: m for m in kept}
    gain = float(cfg["panel"]["practical_gain_macro_f1"])

    per_split: dict[str, Any] = {}
    for sp in SPLITS:
        rows, sn = _rows(predictions, champ_id, sp), _snap_split(snap, sp)
        if not np.array_equal(rows["warehouse_record_id"].to_numpy(), sn["warehouse_record_id"].to_numpy()):
            raise AnalysisError(f"{sp}: tap record du doan champion != tap primary cua snapshot")
        if not np.array_equal(rows["true_class"].to_numpy(), class_index(sn["y_direction"])):
            raise AnalysisError(f"{sp}: nhan true_class cua du doan != y_direction cua snapshot")
        mask = (snap["split"] == sp).to_numpy()
        order = np.argsort(snap.loc[mask, "warehouse_record_id"].to_numpy(), kind="mergesort")
        comp_pred = {mid: (None if sp not in m["scored"] else class_index(np.asarray(m["direction"], dtype=object)[mask][order])) for mid, m in members.items()}
        per_split[sp] = {"rows": rows, "snap": sn, "comp": comp_pred}

    def table_row(mid: str) -> dict[str, Any]:
        return next(r for r in panel["table"] if r["id"] == mid)

    val_comp, test_comp, is_sub = panel["validation_comparator"], panel["test_available_comparator"], panel["is_substitute"]
    vs_panel: dict[str, Any] = {}
    for mid, m in members.items():
        entry: dict[str, Any] = {}
        for sp in SPLITS:
            cp, ps = per_split[sp]["comp"][mid], per_split[sp]
            if cp is None:
                entry[sp] = None
                continue
            rows = ps["rows"]
            entry[sp] = _delta(rows["hotel_id"].to_numpy(), rows["true_class"].to_numpy(), rows["pred_class"].to_numpy(), cp, cfg)
        vs_panel[mid] = entry

    v_rows = per_split["validation"]["rows"]
    v_summary = M.summarize(v_rows["true_class"].to_numpy(), v_rows["pred_class"].to_numpy())
    v_delta = vs_panel[val_comp]["validation"]
    recalls = [v_summary["per_class"][n]["recall"] for n in M.CLASS_NAMES]
    diagnostic_gate = bool(v_delta["ci95"] is not None and v_delta["point"] >= gain and v_delta["ci95"][0] > 0 and all(r is not None and r > 0 for r in recalls))
    contract_ok = report.get("status") == "complete" and report.get("contract_complete") is True
    claim_blocks = [] if contract_ok else ["training_contract_incomplete"]
    gate_passed = diagnostic_gate and contract_ok          # claim cuoi CHI cho run hoan tat dung hop dong; so do (delta/CI) van duoc giu de chan doan
    test_delta_same = None if is_sub else vs_panel[val_comp]["test"]["point"]
    status = same_comparator_test_status(is_substitute=is_sub, test_delta_vs_validation_comparator=test_delta_same)
    claim = {"validation_comparator_id": val_comp, "validation_delta": v_delta["point"], "validation_delta_ci95": v_delta["ci95"], "validation_gate_passed": gate_passed,
             "diagnostic_validation_gate_passed": diagnostic_gate, "claim_block_reasons": claim_blocks, "practical_gain_threshold": gain, "recall_validation": dict(zip(M.CLASS_NAMES, recalls)),
             "test_delta_vs_validation_comparator": test_delta_same, "test_delta_vs_validation_comparator_status": "not_scored_on_test" if is_sub else "scored",
             "test_available_comparator_id": test_comp, "is_substitute": is_sub, "test_delta_vs_available_comparator": vs_panel[test_comp]["test"]["point"],
             "test_delta_vs_available_comparator_ci95": vs_panel[test_comp]["test"]["ci95"], "same_comparator_test_status": status,
             "claim_level": "dev_exploratory_validation_gain" if gate_passed else "none",
             "caveats": ["dev/exploratory: test cua dataset da lo cho regression", "CI la khoang sau chon loc, tham do (khong xac nhan)",
                         "khong co truong nao la cong trien khai", "h3 chi co 4 ngay validation/test; CI cum hotel khong bao quat cu soc thi truong dung chung ngay"]}

    # ---- phan ra bat buoc, theo split, cho champion so voi comparator tuong ung
    breakdowns: dict[str, Any] = {}
    for sp in SPLITS:
        ps = per_split[sp]
        rows = ps["rows"]
        comp_id = val_comp if (sp == "validation" or not is_sub) else test_comp
        comp = ps["comp"][comp_id]
        info: dict[str, Any] = {"comparator_id": comp_id}
        for col in ("inference_mode", "lead_time_bucket", "city", "vn_observation_date"):
            if col in rows.columns:
                info[col] = _group_metrics(rows, col, comp)
        status_cols = [c for c in ("prediction_match_status", f"label_match_status_h{h}") if c in rows.columns]
        if len(status_cols) == 2:
            alias = ((rows[status_cols[0]] == "alias") | (rows[status_cols[1]] == "alias")).map({True: "any_alias", False: "exact_only"})
            info["match_exactness"] = _group_metrics(rows.assign(_m=alias), "_m", comp)
        pct_col = f"y_pct_change_h{h}"
        if pct_col in rows.columns:
            tails = {}
            for name, thr in (("abs_pct_gt_20", 0.20), ("abs_pct_gt_50", 0.50)):
                sel = rows[pct_col].abs() > thr
                s = M.summarize(rows.loc[sel, "true_class"].to_numpy(), rows.loc[sel, "pred_class"].to_numpy()) if sel.any() else None
                tails[name] = {"n": int(sel.sum()), "support": None if s is None else s["support"], "macro_f1": None if s is None else s["macro_f1"], "null_reason": None if s is None else s["null_reason"],
                               "confusion": None if s is None else s["confusion"], "per_class": None if s is None else s["per_class"]}
            info["tails"] = tails
        info["equal_hotel"] = {"candidate": equal_hotel_summary(rows["hotel_id"], rows["true_class"], rows["pred_class"]),
                               "comparator": equal_hotel_summary(rows["hotel_id"], rows["true_class"], comp)}
        info["leave_one_out"] = leave_one_out(rows["hotel_id"], rows["true_class"], rows["pred_class"], comp)
        info["probability"] = {"log_loss": M.log_loss(rows["true_class"], rows[PROBA_COLS]), "brier": M.brier_multiclass(rows["true_class"], rows[PROBA_COLS]), "status": "uncalibrated",
                               "reliability": [_reliability(rows["true_class"].to_numpy(), rows[PROBA_COLS].to_numpy(), k) for k in (0, 2)]}
        breakdowns[sp] = info

    # ---- ablation so voi champion (ghep cap)
    ablations: dict[str, Any] = {}
    for key, abl in (report.get("ablation") or {}).items():
        if abl.get("status") != "ok":
            ablations[key] = {"status": abl.get("status"), "reason": abl.get("reason")}
            continue
        entry = {"status": "ok", "model_id": abl["model_id"], "feature_set": abl["feature_set"]}
        for sp in SPLITS:
            a_rows, c_rows = _rows(predictions, abl["model_id"], sp), per_split[sp]["rows"]
            if not np.array_equal(a_rows["warehouse_record_id"].to_numpy(), c_rows["warehouse_record_id"].to_numpy()):
                raise AnalysisError(f"ablation {key} {sp}: tap record khac champion")
            entry[sp] = _delta(c_rows["hotel_id"].to_numpy(), c_rows["true_class"].to_numpy(), a_rows["pred_class"].to_numpy(), c_rows["pred_class"].to_numpy(), cfg)
            entry[sp]["definition"] = "delta = macro-F1(ablation) - macro-F1(champion); chan doan, khong doi champion, khong la claim"
        ablations[key] = entry
    # ---- mau so ALL-HOTELS (primary + hotel unseen): champion vs C0 (luon stable) va C1 (momentum); comparator regression khong co du doan cho hotel unseen
    all_hotels: dict[str, Any] = {}
    for sp in SPLITS:
        allrows = predictions[(predictions["model_id"] == champ_id) & (predictions["split"] == sp)].reset_index(drop=True)
        n_unseen = int((~allrows["is_primary"]).sum()) if "is_primary" in allrows.columns else 0
        entry = {"n_all": int(len(allrows)), "n_primary": int(len(allrows)) - n_unseen, "n_unseen": n_unseen}
        if "price_velocity" in allrows.columns:
            y_all, cand = allrows["true_class"].to_numpy(), allrows["pred_class"].to_numpy()
            comps = {"C0:always_stable": np.ones_like(y_all), "C1:momentum": class_index(momentum_direction(allrows["price_velocity"].to_numpy(float), float(cfg["panel"]["momentum_threshold"])))}
            entry["champion"] = {k: v for k, v in M.summarize(y_all, cand).items() if k in ("n", "macro_f1", "mcc", "support", "null_reason")}
            for cid, cp in comps.items():
                entry[cid] = {"summary": {k: v for k, v in M.summarize(y_all, cp).items() if k in ("n", "macro_f1", "mcc", "null_reason")},
                              "delta": _delta(allrows["hotel_id"].to_numpy(), y_all, cand, cp, cfg)}
        all_hotels[sp] = entry
    return {"horizon": h, "champion": champ_id, "artifact_validation": artifact, "all_hotels": all_hotels, "panel": panel, "champion_vs_panel": vs_panel, "claim": claim, "claim_level": claim["claim_level"], "breakdowns": breakdowns,
            "ablations_vs_champion": ablations, "aliases": aliases, "denominators": {"primary": {sp: int(len(per_split[sp]["rows"])) for sp in SPLITS}},
            "short_window_note": "h3: 4 ngay validation/test" if h == 3 else None, "table_for_validation_comparator": table_row(val_comp)["validation"]["macro_f1"]}
