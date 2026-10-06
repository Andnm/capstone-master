"""Ham THUAN cho gate tong the Phan 2 (khong DB): thoi diem quan sat, post-approval, lead/since, mat mau do ambiguous, churn, Accuracy@tol dung mau so.

Sua theo GPT review file 48 (P2-M1/M2/M3): moi thu dua tren **observed_at UTC** cua quan sat that (khong dung `crawl_runs.started_at`), so voi `approved_at` UTC
(cung kieu `po.observed_at >= a.approved_at` cua sample rule); chi doi sang gio VN (+7h) de tinh NGAY/lead/since. Accuracy@tol dung `training.metrics.regression_metrics`
(mau so = gia that y, giong luc huan luyen), khong tu dinh nghia lai. Khong thuoc `ml/dataset_builder` nen khong doi code identity cua builder.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

VN_OFFSET = pd.Timedelta(hours=7)
EXACT_ALIAS = ("exact", "alias")


def vn_day(ts: pd.Series) -> pd.Series:
    """Ngay lich VN (00:00) cua timestamp UTC."""
    return (pd.to_datetime(ts) + VN_OFFSET).dt.normalize()


def event_time(frame: pd.DataFrame) -> pd.Series:
    """Thoi diem quan sat UTC cua mot item: exact/alias = `observed_at` cua quan sat DA CHON (chinh la thu sample rule dung); unavailable/ambiguous khong co quan sat
    duoc chon => MIN(observed_at) cua cac quan sat cua item (can `obs_min`; kiem `obs_max` o `item_time_audit`). Khong dung ngay run/finished_at."""
    selected = frame["sel_observed_at"]
    return pd.to_datetime(selected.where(frame["status"].isin(EXACT_ALIAS) & selected.notna(), frame["obs_min"]))


def item_time_audit(frame: pd.DataFrame) -> dict[str, Any]:
    """Item co nhieu timestamp khac nhau? (neu co, dinh nghia thoi diem item phai noi ro, khong am tham)."""
    spread = (pd.to_datetime(frame["obs_max"]) - pd.to_datetime(frame["obs_min"])).dt.total_seconds()
    selected_gap = (pd.to_datetime(frame["sel_observed_at"]) - pd.to_datetime(frame["obs_min"])).dt.total_seconds()
    chosen = frame["status"].isin(EXACT_ALIAS)
    return {
        "items": int(len(frame)), "items_with_multiple_observed_at": int((spread > 0).sum()),
        "max_spread_seconds": None if not len(frame) else float(spread.max()),
        "selected_observed_at_differs_from_item_min": int((selected_gap[chosen] != 0).sum()),
        "items_without_any_observation": int(frame["obs_min"].isna().sum()),
    }


def add_time_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Them `event_utc`, `post_approval` (event_utc >= approved_at, UTC), `event_vn_day`, `approval_vn_day`, `since_days`, `lead`."""
    out = frame.copy()
    out["event_utc"] = event_time(out)
    approved = pd.to_datetime(out["approved_at"])
    out["post_approval"] = out["event_utc"] >= approved
    out["event_vn_day"] = vn_day(out["event_utc"])
    out["approval_vn_day"] = vn_day(approved)
    out["since_days"] = (out["event_vn_day"] - out["approval_vn_day"]).dt.days
    out["lead"] = (pd.to_datetime(out["checkin_date"]) - out["event_vn_day"]).dt.days
    return out


def ambiguous_missing_days(post: pd.DataFrame, sample_days: set[tuple[Any, Any]]) -> dict[str, Any]:
    """Hai phep do KHAC NGHIA (GPT P2-M2): (a) so ITEM ambiguous khong co daily sample cung (assignment, ngay VN quan sat);
    (b) so NGAY-SERIES DISTINCT tuong ung. KHONG tuyen bo 'mat chi vi ambiguous' (con the do gia/checkout/lead/scope/anomaly): goi la ambiguous-ASSOCIATED.
    Mau so rieng: so ngay-series ung vien (distinct (assignment, ngay) cua MOI item sau duyet) va so co sample."""
    days = list(zip(post["aid"], post["event_vn_day"].dt.date))
    has_sample = pd.Series([pair in sample_days for pair in days], index=post.index)
    ambiguous = post["status"] == "ambiguous"
    missing_items = ambiguous & ~has_sample
    pairs = pd.DataFrame({"aid": post["aid"], "day": post["event_vn_day"].dt.date, "has_sample": has_sample, "ambiguous": ambiguous})
    candidate_days = pairs.drop_duplicates(["aid", "day"])
    candidate_with_sample = pairs[pairs["has_sample"]].drop_duplicates(["aid", "day"])
    missing_days = pairs[missing_items.values].drop_duplicates(["aid", "day"])
    return {
        "ambiguous_items_post_approval": int(ambiguous.sum()),
        "ambiguous_items_without_daily_sample_same_assignment_day": int(missing_items.sum()),
        "distinct_ambiguous_associated_missing_series_days": int(len(missing_days)),
        "denominator_candidate_series_days_distinct": int(len(candidate_days)),
        "candidate_series_days_with_a_sample_distinct": int(len(candidate_with_sample)),
        "note": "ambiguous-associated, KHONG phai thiet hai chi vi ambiguous: chua loai cac gate khac (gia/checkout/lead/scope/anomaly)",
    }


def churn_tail(post: pd.DataFrame, k: int = 3) -> dict[str, Any]:
    """'Khong tim thay reference o cac luot cuoi' (KHONG goi la series chet: chua biet nguyen nhan - turnover Booking, parser hay doi offer/rate).
    Thu tu xac dinh: (event_utc, source_code, run_id, item_id). Hai phien ban: k ITEM cuoi cung deu unavailable; k NGAY quan sat cuoi (distinct) ma moi item trong ngay deu unavailable."""
    ordered = post.sort_values(["aid", "event_utc", "source_code", "run_id", "item_id"], kind="mergesort")
    tail_items = ordered.groupby("aid")["status"].apply(lambda s: bool(len(s) >= k and (s.tail(k) == "unavailable").all()))
    day = ordered.groupby(["aid", "event_vn_day"])["status"].apply(lambda s: bool((s == "unavailable").all())).reset_index(name="day_unavailable")
    day = day.sort_values(["aid", "event_vn_day"], kind="mergesort")
    tail_days = day.groupby("aid")["day_unavailable"].apply(lambda s: bool(len(s) >= k and s.tail(k).all()))
    n = int(ordered["aid"].nunique())
    per_assign = ordered.assign(u=(ordered["status"] == "unavailable")).groupby("aid")["u"].mean()
    return {
        "assignments": n,
        f"assignments_last{k}_items_all_unavailable": int(tail_items.sum()), f"assignments_last{k}_items_all_unavailable_%": round(100.0 * tail_items.mean(), 2) if n else None,
        f"assignments_last{k}_distinct_days_all_unavailable": int(tail_days.sum()), f"assignments_last{k}_distinct_days_all_unavailable_%": round(100.0 * tail_days.mean(), 2) if n else None,
        "distinct_observation_days_per_assignment_median": float(day.groupby("aid").size().median()) if n else None,
        "assignments_with_any_unavailable_%": round(100.0 * float((per_assign > 0).mean()), 2) if n else None,
        "assignments_mostly_unavailable_gt50%_items": round(100.0 * float((per_assign > 0.5).mean()), 2) if n else None,
    }


def persistence_accuracy(frame: pd.DataFrame, horizon: int, *, tol: float = 0.20, stable: float = 0.02, primary_only: bool = False) -> dict[str, Any]:
    """Accuracy@tol cua baseline persistence (pred = current_price) bang `training.metrics.regression_metrics` (mau so = y that), theo split.
    `primary_only`: val/test chi hotel thuoc `hotel_seen_in_train_h{h}` (dung dinh nghia primary cua training); train khong loc."""
    import sys
    from pathlib import Path
    ml = str(Path(__file__).resolve().parents[1])
    if ml not in sys.path:
        sys.path.insert(0, ml)
    from training.metrics import regression_metrics

    usable = frame[frame[f"label_usable_h{horizon}"].fillna(False).astype(bool) & frame[f"y_price_h{horizon}"].notna()
                   & (frame["current_price"] > 0) & (frame[f"y_price_h{horizon}"] > 0)]
    out: dict[str, Any] = {}
    for name, part in [("all_splits", usable)] + [(s, g) for s, g in usable.groupby("split")]:
        if primary_only and name != "train":
            part = part[part[f"hotel_seen_in_train_h{horizon}"].astype(bool)] if name != "all_splits" else part[
                (part["split"] == "train") | part[f"hotel_seen_in_train_h{horizon}"].astype(bool)]
        if not len(part):
            continue
        m = regression_metrics(part[f"y_price_h{horizon}"].astype(float), part["current_price"].astype(float), part["current_price"].astype(float), tol=tol, stable=stable)
        out[name] = {"n": m["n"], "accuracy_at_tol": round(m["accuracy_at_tol"], 6), "mae": round(m["mae"], 1), "smape": round(m["smape"], 6),
                     "stable_within_2pct_share": round(float((part[f"y_pct_change_h{horizon}"].astype(float).abs() <= stable).mean()), 6)}
    return out


def class_metrics(y_true_cls: np.ndarray, y_pred_cls: np.ndarray, labels=("down", "stable", "up")) -> dict[str, Any]:
    """Confusion matrix + recall/precision/F1 tung lop, balanced accuracy, macro-F1 va baseline hang 'stable' (GPT muc 5: directional accuracy 3 lop)."""
    labels = list(labels)
    cm = pd.crosstab(pd.Categorical(y_true_cls, categories=labels), pd.Categorical(y_pred_cls, categories=labels), dropna=False).reindex(index=labels, columns=labels, fill_value=0)
    n = int(cm.values.sum())
    recall, precision, f1 = {}, {}, {}
    for c in labels:
        tp = int(cm.loc[c, c])
        rec = tp / cm.loc[c].sum() if cm.loc[c].sum() else float("nan")
        prec = tp / cm[c].sum() if cm[c].sum() else float("nan")
        recall[c], precision[c] = rec, prec
        f1[c] = (2 * prec * rec / (prec + rec)) if prec == prec and rec == rec and (prec + rec) > 0 else 0.0
    present = [c for c in labels if cm.loc[c].sum() > 0]
    prevalence = {c: float(cm.loc[c].sum() / n) if n else None for c in labels}
    return {"n": n, "confusion_matrix_rows_true_cols_pred": cm.astype(int).to_dict(orient="index"), "accuracy": float(np.trace(cm.values) / n) if n else None,
            "recall": recall, "precision": precision, "f1": f1, "balanced_accuracy": float(np.nanmean([recall[c] for c in present])) if present else None,
            "macro_f1": float(np.mean([f1[c] for c in labels])), "prevalence_true": prevalence,
            "constant_stable_baseline_accuracy": prevalence.get("stable")}


def classify_pct(pct: np.ndarray, stable: float = 0.02) -> np.ndarray:
    return np.where(pct > stable, "up", np.where(pct < -stable, "down", "stable"))
