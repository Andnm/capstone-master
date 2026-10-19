"""Panel doi chieu (EVAL_PROTOCOL §4): C0 luon-stable, C1 momentum, C2 huong suy ra tu regression (nguong CO DINH +-2%), C3 = champion A/B/routed doc tu manifest.

Khong fit gi. QUAN TRONG: regression chi CHAM test cho danh sach prespecify => cot du doan khac la NaN tren TOAN BO test (manifest.prediction_scored_splits). Do la 'khong duoc cham',
khong phai 'du doan khong doi gia'. Thanh vien chua duoc cham tren mot split thi KHONG co so lieu split do (khong dien stable). Chi dung dau-vao huu han, khong lam tron NaN.
Mot thanh vien la ban sao (alias) cua thanh vien truoc neu vector huong (tren cac hang co cham) giong het => giu thanh vien dau theo thu tu panel, ghi alias.
Validation comparator (gate) = macro-F1 validation cao nhat trong {C0, C1, C2} duoc cham tren validation; hoa (|diff| <= tie_atol) => MCC cao hon => thu tu panel.
Test-available comparator = chinh validation comparator neu no duoc cham tren test; neu khong => thanh vien duoc cham tren CA HAI split co macro-F1 validation cao nhat (`is_substitute=True`).
Khi la thanh vien thay the, so sanh test KHONG phai la xac nhan cung doi chung: `same_comparator_test_status` = 'not_evaluable' (xem `same_comparator_test_status`).
"Diagnostic oracle" = thanh vien tot nhat tren TEST trong cac thanh vien da cham test: chi mo ta, khong phai mo hinh co the trien khai (EVAL §4).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .labels import class_index, direction_from_pct, pct_change
from .metrics import summarize

SPLITS_EVAL = ("validation", "test")
NA = "NA"


class PanelError(ValueError):
    pass


def momentum_direction(price_velocity: Any, threshold: float = 0.02) -> np.ndarray:
    """C1: up neu velocity > +thr, down neu < -thr; con lai hoac NaN => stable (heuristic, KHONG scale theo horizon)."""
    v = np.asarray(price_velocity, dtype=float)
    if np.isinf(v).any():
        raise PanelError("price_velocity co vo cuc: chi NaN moi duoc coi la stable (protocol), vo cuc la loi du lieu")
    v = np.where(np.isnan(v), 0.0, v)
    return np.where(v > threshold, "up", np.where(v < -threshold, "down", "stable")).astype(object)


def champion_columns(manifest: dict[str, Any], columns: list[str]) -> dict[str, str]:
    """Vai tro C3 -> ten cot; doc tu report da ghim (khong hardcode). Thieu cot => loi."""
    roles: dict[str, str] = {}
    for arm in ("A", "B"):
        name = (manifest.get("champions") or {}).get(arm)
        if not name:
            raise PanelError(f"manifest khong co champion {arm}")
        col = f"pred_{name}"
        if col not in columns:
            raise PanelError(f"champion {arm}={name!r} khong co cot {col} trong snapshot")
        roles[f"C3-{arm}"] = col
    for col in manifest.get("routed_columns", []):
        if col not in columns:
            raise PanelError(f"routed column {col} thieu")
        roles[f"C3-routed[{col[len('pred_'):]}]"] = col
    return roles


def candidate_directions(snap: pd.DataFrame, manifest: dict[str, Any], *, momentum_threshold: float = 0.02) -> list[dict[str, Any]]:
    """Thanh vien theo THU TU panel: C0, C1, roi C2 (cot pred_* sap theo ten). `direction` = mang str theo hang snapshot; hang chua duoc cham = 'NA'."""
    n = len(snap)
    members = [{"id": "C0:always_stable", "family": "C0", "direction": np.full(n, "stable", dtype=object)},
               {"id": "C1:momentum", "family": "C1", "direction": momentum_direction(snap["price_velocity"], momentum_threshold)}]
    cur = snap["current_price"].to_numpy(float)
    scored = manifest["prediction_scored_splits"]
    for col in sorted(manifest["prediction_columns"]):
        direction = np.full(n, NA, dtype=object)
        for sp in scored[col]:
            mask = (snap["split"] == sp).to_numpy()
            direction[mask] = direction_from_pct(pct_change(snap.loc[mask, col].to_numpy(float), cur[mask]))     # raise neu NaN: da loai o cap split
        members.append({"id": f"C2:{col}", "family": "C2", "column": col, "direction": direction, "scored": list(scored[col])})
    for order, m in enumerate(members):
        m["order"] = order
        m.setdefault("scored", list(SPLITS_EVAL))
    return members


def dedup(members: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    seen: dict[bytes, str] = {}
    kept, aliases = [], {}
    for m in members:
        key = np.asarray(m["direction"], dtype=object).astype("U8").tobytes()
        if key in seen:
            aliases[m["id"]] = seen[key]
        else:
            seen[key] = m["id"]
            kept.append(m)
    return kept, aliases


def _pick(pool: list[dict[str, Any]], split: str, tie_atol: float) -> dict[str, Any]:
    top = max(r[split]["macro_f1"] for r in pool)
    tied = [r for r in pool if top - r[split]["macro_f1"] <= tie_atol]
    tied.sort(key=lambda r: (-r[split]["mcc"], r["order"]))
    return tied[0]


def evaluate_panel(snap: pd.DataFrame, manifest: dict[str, Any], *, tie_atol: float = 1e-9, momentum_threshold: float = 0.02) -> dict[str, Any]:
    truth = {sp: class_index(snap.loc[snap["split"] == sp, "y_direction"]) for sp in SPLITS_EVAL}
    members = candidate_directions(snap, manifest, momentum_threshold=momentum_threshold)
    kept, aliases = dedup(members)
    roles = champion_columns(manifest, list(snap.columns))
    table: list[dict[str, Any]] = []
    for m in kept:
        row: dict[str, Any] = {"id": m["id"], "order": m["order"], "family": m["family"], "scored_splits": m["scored"]}
        for sp in SPLITS_EVAL:
            if sp not in m["scored"]:
                row[sp] = None                                                 # chua duoc cham: KHONG co so lieu (khong phai 'khong doi')
                continue
            mask = (snap["split"] == sp).to_numpy()
            s = summarize(truth[sp], class_index(m["direction"][mask]))
            row[sp] = {k: s[k] for k in ("n", "macro_f1", "mcc", "balanced_accuracy", "predicted_change_rate", "support", "confusion")}
        table.append(row)
    pool_val = [r for r in table if r["validation"] is not None]
    pool_both = [r for r in table if r["validation"] is not None and r["test"] is not None]
    gate = _pick(pool_val, "validation", tie_atol)
    if gate["test"] is not None:
        test_comp, substitute = gate, False
    else:
        test_comp, substitute = _pick(pool_both, "validation", tie_atol), True
    oracle = _pick(pool_both, "test", tie_atol)
    role_to_member = {role: aliases.get(f"C2:{col}", f"C2:{col}") for role, col in roles.items()}
    return {"table": table, "aliases": aliases,
            "validation_comparator": gate["id"], "validation_comparator_validation_macro_f1": gate["validation"]["macro_f1"], "validation_comparator_scored_on_test": gate["test"] is not None,
            "test_available_comparator": test_comp["id"], "is_substitute": substitute,
            "test_available_comparator_validation_macro_f1": test_comp["validation"]["macro_f1"], "test_available_comparator_test_macro_f1": test_comp["test"]["macro_f1"],
            "diagnostic_oracle_on_test": oracle["id"], "oracle_test_macro_f1": oracle["test"]["macro_f1"],
            "members_not_scored_on_test": [r["id"] for r in table if r["test"] is None], "c3_roles": roles, "c3_role_to_member": role_to_member, "tie_atol": tie_atol}


def same_comparator_test_status(*, is_substitute: bool, test_delta_vs_validation_comparator: float | None) -> str:
    """Trang thai kiem lai tren test CUNG doi chung (EVAL §7). Thay the => 'not_evaluable' (delta voi doi chung khac KHONG phai xac nhan/bac bo). Cung doi chung: delta > 0 => 'positive_on_exposed_test';
    delta <= 0 => 'not_reproduced_on_exposed_test' (khong bao gio la 'independent confirmation': test da lo)."""
    if is_substitute:
        if test_delta_vs_validation_comparator is not None:
            raise PanelError("comparator thay the: test_delta_vs_validation_comparator phai la None (khong dien so thay the vao truong nay)")
        return "not_evaluable"
    if test_delta_vs_validation_comparator is None or not np.isfinite(test_delta_vs_validation_comparator):
        raise PanelError("cung doi chung: can test_delta huu han")
    return "positive_on_exposed_test" if test_delta_vs_validation_comparator > 0 else "not_reproduced_on_exposed_test"
