"""Validator artifact du doan (GPT 12 M2): chay TRUOC bat ky metric/CI/claim, dung chung cho analysis, CLI phan tich va dieu kien resume.

Khong tin file da luu "dung theo cach tao": kiem lai tu dau (khoa duy nhat, vai tro/model theo report + allowlist, lop va xac suat hop le, `pred_class == argmax(xac suat)` theo luat hoa
(chi so nho nhat), nhat quan nhan/ID giua cac model, co `is_primary` dung nghia, hang unseen chi cua champion, dem khop report, metadata khop snapshot neu co).
Loi => ArtifactError (khong co du doan nao duoc dung de cham tiep).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from . import metrics as M
from .labels import class_index

REQUIRED = ["warehouse_record_id", "horizon", "split", "model_id", "role", "true_class", "pred_class", "p_down", "p_stable", "p_up", "is_primary"]
PROBA = ["p_down", "p_stable", "p_up"]
SPLITS = ("validation", "test")
META_COLUMNS = ("hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date")


class ArtifactError(RuntimeError):
    pass


def _fail(msg: str) -> None:
    raise ArtifactError(msg)


def _date_str(series: pd.Series) -> np.ndarray:
    return pd.to_datetime(series).dt.strftime("%Y-%m-%d").to_numpy()


def validate_prediction_artifact(pred: pd.DataFrame, report: dict[str, Any], snap: pd.DataFrame | None = None, *, tolerance: float = 1e-5) -> dict[str, Any]:
    """Tra tom tat kiem (so hang theo split/model). `snap` (tuy chon) = baseline snapshot da load: kiem tap record primary va metadata cua champion."""
    if not isinstance(pred, pd.DataFrame) or pred.empty:
        _fail("predictions rong hoac khong phai DataFrame")
    missing = [c for c in REQUIRED if c not in pred.columns]
    if missing:
        _fail(f"thieu cot: {missing}")
    h = int(report["horizon"])
    champion = report.get("champion")
    if champion is None:
        _fail("report khong co champion nhung co predictions")
    models = report.get("models")
    if not isinstance(models, dict) or champion not in models:
        _fail("report.models thieu hoac khong chua champion")
    if (pred["horizon"] != h).any():
        _fail(f"co hang khac horizon {h}")
    if not pred["split"].isin(SPLITS).all():
        _fail(f"split ngoai {SPLITS}: {sorted(set(pred['split']) - set(SPLITS))}")
    if pred["is_primary"].dtype != bool:
        _fail("is_primary phai la bool")
    if pred.duplicated(["horizon", "split", "warehouse_record_id", "model_id"]).any():
        _fail("trung khoa (horizon, split, warehouse_record_id, model_id)")
    unknown = sorted(set(pred["model_id"]) - set(models))
    if unknown:
        _fail(f"model ngoai report.models: {unknown}")
    freeze = report.get("freeze") or {}
    allow = {a["model_id"]: a["role"] for a in freeze.get("test_allowlist", [])}
    if allow and not set(models) <= set(allow):
        _fail(f"report.models {sorted(set(models) - set(allow))} ngoai allowlist {sorted(allow)}")
    if allow and set(pred.loc[pred["split"] == "test", "model_id"]) - set(allow):
        _fail("du doan test cua model ngoai allowlist")
    for model_id, meta in models.items():
        roles = pred.loc[pred["model_id"] == model_id, "role"].unique().tolist()
        if roles and roles != [",".join(meta["roles"])]:
            _fail(f"model {model_id!r}: role {roles} != report {meta['roles']}")

    for col in ("true_class", "pred_class"):
        v = pred[col].to_numpy()
        if not np.issubdtype(v.dtype, np.integer) or not np.isin(v, (0, 1, 2)).all():
            _fail(f"{col} phai la so nguyen trong {{0,1,2}}")
    p = pred[PROBA].to_numpy(dtype="float64")
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        _fail("xac suat khong huu han hoac ngoai [0, 1]")
    if not np.allclose(p.sum(axis=1), 1.0, atol=tolerance, rtol=0):
        _fail(f"tong hang xac suat != 1 (toi da lech {float(np.abs(p.sum(axis=1) - 1).max()):.3g})")
    if not np.array_equal(M.argmax_class(p), pred["pred_class"].to_numpy().astype(np.int64)):
        n_bad = int((M.argmax_class(p) != pred["pred_class"].to_numpy()).sum())
        _fail(f"pred_class khong bang argmax(xac suat) o {n_bad} hang")

    summary: dict[str, Any] = {"rows": {}, "unseen_rows": {}}
    primary = pred[pred["is_primary"]]
    non_primary = pred[~pred["is_primary"]]
    if len(non_primary) and set(non_primary["model_id"]) != {champion}:
        _fail("hang is_primary=False (hotel unseen) chi duoc thuoc champion")
    for sp in SPLITS:
        base_ids = None
        base_true = None
        for model_id in models:
            rows = primary[(primary["split"] == sp) & (primary["model_id"] == model_id)].sort_values("warehouse_record_id", kind="mergesort")
            ids, true = rows["warehouse_record_id"].to_numpy(), rows["true_class"].to_numpy()
            if sp == "test" and allow and model_id not in allow:
                _fail("model ngoai allowlist")
            if base_ids is None:
                base_ids, base_true = ids, true
            elif not np.array_equal(ids, base_ids):
                _fail(f"{sp}: tap record primary cua {model_id!r} khac cac model khac")
            elif not np.array_equal(true, base_true):
                _fail(f"{sp}: true_class cua {model_id!r} khac cac model khac cho cung record")
            summary["rows"].setdefault(sp, {})[model_id] = int(len(rows))
        champ_all = pred[(pred["split"] == sp) & (pred["model_id"] == champion)]
        n_unseen = int((~champ_all["is_primary"]).sum())
        summary["unseen_rows"][sp] = n_unseen
        if n_unseen and set(champ_all.loc[~champ_all["is_primary"], "warehouse_record_id"]) & set(base_ids.tolist()):
            _fail(f"{sp}: record unseen trung record primary")
        expected = (report.get("test") or {}).get("unseen_hotel_rows_champion_only") or {}
        if sp in expected and int(expected[sp]) != n_unseen:
            _fail(f"{sp}: so hang unseen {n_unseen} != report {expected[sp]}")
    test_rows = (report.get("test") or {}).get("rows")
    if test_rows is not None and any(n != int(test_rows) for n in summary["rows"].get("test", {}).values()):
        _fail(f"so hang test primary {summary['rows'].get('test')} != report.test.rows {test_rows}")

    if snap is not None:
        for sp in SPLITS:
            sn = snap[snap["split"] == sp].sort_values("warehouse_record_id", kind="mergesort").reset_index(drop=True)
            for model_id in models:                                          # MOI model (champion, finalist, ablation) phai khop snapshot: CI cum hotel dung hang cua ablation/doi chung
                rows = primary[(primary["model_id"] == model_id) & (primary["split"] == sp)].sort_values("warehouse_record_id", kind="mergesort").reset_index(drop=True)
                if not np.array_equal(rows["warehouse_record_id"].to_numpy(), sn["warehouse_record_id"].to_numpy()):
                    _fail(f"{sp}: tap record du doan cua {model_id!r} != tap primary cua snapshot")
                if not np.array_equal(rows["true_class"].to_numpy(), class_index(sn["y_direction"])):
                    _fail(f"{sp}: nhan true_class cua {model_id!r} != y_direction cua snapshot")
                for col in META_COLUMNS:
                    if col in rows.columns and col in sn.columns:
                        a_col, b_col = rows[col], sn[col]
                        same = np.array_equal(_date_str(a_col), _date_str(b_col)) if col.endswith("_date") else np.array_equal(a_col.astype(str).to_numpy(), b_col.astype(str).to_numpy())
                        if not same:
                            _fail(f"{sp}: metadata {col} cua {model_id!r} khac snapshot cho cung record (se lam sai cum hotel CI)")
    return summary
