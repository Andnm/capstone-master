"""Bootstrap GHEP CAP theo cum hotel + equal-hotel + leave-one-out (EVAL_PROTOCOL §6).

- Moi hotel mot cum: ma tran nham lan 3x3 theo hotel cho candidate va comparator tren CUNG record. Mot lan rut = chon hotel co hoan lai (da thuc `multinomial`), cong ma tran theo so lan chon.
- Thong ke = HIEU macro-F1 (candidate - comparator) tren cung record. Draw thieu lop that (support = 0) la `invalid_draw`: bi loai khoi phan vi, bao `valid_draws/n`; `valid_fraction < min_valid_fraction` => CI = None (khong dien so).
- Seed co dinh, thu tu hotel sap theo id => tai lap duoc doc lap voi thu tu hang.
- Equal-hotel = ma tran nham lan trong do moi hotel co tong trong so 1 (hang cua hotel chia deu), roi tinh macro-F1 3 lop tu ma tran co trong so (KHONG phai trung binh F1 theo hotel).
- Leave-one-out la chan doan KHONG cong duoc: macro-F1 phi tuyen nen khong tong hop thanh 'tong gain'.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from . import metrics as M


def _macro_f1_batch(cm: np.ndarray) -> np.ndarray:
    """cm (..., 3, 3) -> macro-F1 3 lop co dinh (zero_division = 0)."""
    tp = np.einsum("...ii->...i", cm)
    support, predicted = cm.sum(axis=-1), cm.sum(axis=-2)
    den = support + predicted
    f1 = np.where(den > 0, 2 * tp / np.where(den > 0, den, 1), 0.0)
    return f1.mean(axis=-1)


def per_hotel_confusions(hotel_ids: Any, y_true: Any, y_pred: Any) -> tuple[np.ndarray, np.ndarray]:
    ids = np.asarray(hotel_ids).astype(str)
    t, p = M._class_ints(y_true, "y_true"), M._class_ints(y_pred, "y_pred")
    if not (len(ids) == len(t) == len(p)):
        raise ValueError("hotel_ids/y_true/y_pred phai cung do dai")
    hotels, inverse = np.unique(ids, return_inverse=True)
    cms = np.zeros((len(hotels), 3, 3))
    np.add.at(cms, (inverse, t, p), 1.0)
    return hotels, cms


def paired_bootstrap_delta(hotel_ids: Any, y_true: Any, pred_candidate: Any, pred_comparator: Any, *, n: int, seed: int, min_valid_fraction: float) -> dict[str, Any]:
    hotels, cm_a = per_hotel_confusions(hotel_ids, y_true, pred_candidate)
    _, cm_b = per_hotel_confusions(hotel_ids, y_true, pred_comparator)
    h = len(hotels)
    point = float(_macro_f1_batch(cm_a.sum(axis=0)) - _macro_f1_batch(cm_b.sum(axis=0)))
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(h, np.full(h, 1.0 / h), size=n).astype(np.float64)               # (n, h): so lan moi hotel duoc chon
    draw_a = (counts @ cm_a.reshape(h, 9)).reshape(n, 3, 3)
    draw_b = (counts @ cm_b.reshape(h, 9)).reshape(n, 3, 3)
    support = draw_a.sum(axis=2)                                                              # support that cua tung lop trong draw (giong nhau cho A va B)
    valid = (support > 0).all(axis=1)
    delta = _macro_f1_batch(draw_a) - _macro_f1_batch(draw_b)
    out: dict[str, Any] = {"point": point, "n_draws": int(n), "valid_draws": int(valid.sum()), "valid_fraction": float(valid.mean()), "seed": int(seed), "n_hotels": int(h)}
    if valid.mean() < min_valid_fraction or valid.sum() == 0:
        out.update(ci95=None, ci_null_reason="valid_fraction_below_threshold")
    else:
        lo, hi = np.percentile(delta[valid], [2.5, 97.5])
        out.update(ci95=[float(lo), float(hi)], ci_null_reason=None)
    return out


def equal_hotel_summary(hotel_ids: Any, y_true: Any, y_pred: Any) -> dict[str, Any]:
    """Moi hotel tong trong so 1; macro-F1 tinh tu ma tran nham lan co trong so. Hotel co lop thieu trong tong the van dong gop theo ty le cua no."""
    _, cms = per_hotel_confusions(hotel_ids, y_true, y_pred)
    totals = cms.sum(axis=(1, 2))
    weighted = (cms / totals[:, None, None]).sum(axis=0)
    s = M.summarize_from_confusion(weighted)
    return {"n_hotels": int(len(cms)), "macro_f1": s["macro_f1"], "mcc": s["mcc"], "support": s["support"], "null_reason": s["null_reason"]}


def leave_one_out(hotel_ids: Any, y_true: Any, pred_candidate: Any, pred_comparator: Any, *, top_k: int = 5) -> dict[str, Any]:
    """Delta khi bo tung hotel (khong cong duoc). 'Top contributor' = hotel lam delta GIAM nhieu nhat khi bi bo; bao delta con lai khi bo top-1 va top-k (bo chung).
    Chinh sach null cua subgroup: phan con lai thieu support that cua BAT KY lop nao (hoac rong) => KHONG tra 0 nhu metric hop le, ma tra null + ly do; hotel bo ra khoi ung vien top
    chi xet trong cac tap con hop le."""
    hotels, cm_a = per_hotel_confusions(hotel_ids, y_true, pred_candidate)
    _, cm_b = per_hotel_confusions(hotel_ids, y_true, pred_comparator)
    tot_a, tot_b = cm_a.sum(axis=0), cm_b.sum(axis=0)
    support_all = tot_a.sum(axis=1)                                                           # support that theo lop (giong cho A va B)
    support_h = cm_a.sum(axis=2)                                                              # (h, 3)
    reasons: dict[str, str | None] = {"delta_all": None, "top1": None, "top_k": None}
    out: dict[str, Any] = {"k": 0, "additive": False, "delta_all": None, "top1_hotel": None, "delta_without_top1": None, "top_k_hotels": [], "delta_without_top_k": None,
                           "min_delta_leave_one_out": None, "max_delta_leave_one_out": None, "n_valid_leave_one_out": 0, "null_reasons": reasons}
    if not (support_all > 0).all():
        reasons["delta_all"] = reasons["top1"] = reasons["top_k"] = "missing_class_support"
        return out
    out["delta_all"] = float(_macro_f1_batch(tot_a) - _macro_f1_batch(tot_b))
    loo = _macro_f1_batch(tot_a - cm_a) - _macro_f1_batch(tot_b - cm_b)                      # (h,)
    valid = ((support_all[None, :] - support_h) > 0).all(axis=1)
    out["n_valid_leave_one_out"] = int(valid.sum())
    if not valid.any():
        reasons["top1"] = reasons["top_k"] = "missing_class_support_after_removal"
        return out
    order = np.array([i for i in np.argsort(np.where(valid, loo, np.inf), kind="mergesort") if valid[i]])      # tang dan trong cac tap con hop le
    k = min(top_k, len(order))
    top_idx = order[:k]
    out.update(top1_hotel=str(hotels[order[0]]), delta_without_top1=float(loo[order[0]]), top_k_hotels=[str(hotels[i]) for i in top_idx], k=int(k),
               min_delta_leave_one_out=float(loo[valid].min()), max_delta_leave_one_out=float(loo[valid].max()))
    rest_a, rest_b = tot_a - cm_a[top_idx].sum(axis=0), tot_b - cm_b[top_idx].sum(axis=0)
    if (rest_a.sum(axis=1) > 0).all():
        out["delta_without_top_k"] = float(_macro_f1_batch(rest_a) - _macro_f1_batch(rest_b))
    else:
        reasons["top_k"] = "missing_class_support_after_removal"
    return out
