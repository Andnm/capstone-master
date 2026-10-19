"""Metric 3 lop CO DINH (EVAL_PROTOCOL §6), tinh tu ma tran nham lan; khong phu thuoc sklearn (test doi chung voi sklearn).

Quy uoc: ma tran `cm[i, j]` = so mau co lop that `i` va lop du doan `j`; lop `0=down, 1=stable, 2=up`.
`zero_division = 0` ghi ro: F1 cua lop = 0 khi precision + recall = 0 (khong dinh nghia). Chinh sach null cho SUBGROUP: neu mot lop co support that = 0
thi macro-F1 va balanced accuracy cua subgroup la `None` (ly do `missing_class_support`); confusion/n/support/ti le du doan "doi" luon duoc bao.
"""
from __future__ import annotations

from typing import Any

import numpy as np

N_CLASSES = 3
CLASS_NAMES = ("down", "stable", "up")


def _class_ints(values: Any, name: str) -> np.ndarray:
    """Chi so lop hop le: so nguyen (hoac float la so nguyen huu han), trong [0, 2], 1-D. Khong cast truoc khi kiem (0.9 khong bi cat thanh 0, -1 khong thanh chi so am cua NumPy)."""
    arr = np.asarray(values)
    if arr.ndim != 1:
        raise ValueError(f"{name} phai 1-D")
    if arr.dtype.kind == "b":
        raise ValueError(f"{name} la bool, can chi so lop nguyen")
    if arr.dtype.kind == "f":
        if not np.isfinite(arr).all() or not np.array_equal(arr, np.floor(arr)):
            raise ValueError(f"{name} co gia tri khong nguyen / khong huu han")
    elif arr.dtype.kind not in "iu":
        raise ValueError(f"{name} co kieu {arr.dtype} khong phai so nguyen")
    out = arr.astype(np.int64)
    if out.size and (out.min() < 0 or out.max() >= N_CLASSES):
        raise ValueError(f"{name} co chi so lop ngoai [0, {N_CLASSES - 1}]")
    return out


def confusion(y_true: Any, y_pred: Any) -> np.ndarray:
    t, p = _class_ints(y_true, "y_true"), _class_ints(y_pred, "y_pred")
    if t.shape != p.shape:
        raise ValueError("y_true/y_pred phai cung do dai")
    cm = np.zeros((N_CLASSES, N_CLASSES), dtype=np.float64)
    np.add.at(cm, (t, p), 1.0)
    return cm


def weighted_confusion(y_true: Any, y_pred: Any, weights: Any) -> np.ndarray:
    t, p = _class_ints(y_true, "y_true"), _class_ints(y_pred, "y_pred")
    w = np.asarray(weights)
    if w.dtype.kind not in "iuf" or w.shape != t.shape or t.shape != p.shape:
        raise ValueError("weights phai la so, cung do dai voi y_true/y_pred")
    w = w.astype(np.float64)
    if not np.isfinite(w).all() or (w < 0).any():
        raise ValueError("weights phai huu han va khong am")
    cm = np.zeros((N_CLASSES, N_CLASSES), dtype=np.float64)
    np.add.at(cm, (t, p), w)
    return cm


def _div(num: float, den: float) -> float:
    return float(num / den) if den > 0 else 0.0


def per_class(cm: np.ndarray) -> list[dict[str, float | None]]:
    out = []
    for k in range(N_CLASSES):
        tp, support, predicted = cm[k, k], cm[k, :].sum(), cm[:, k].sum()
        precision = _div(tp, predicted) if predicted > 0 else None
        recall = _div(tp, support) if support > 0 else None
        f1 = _div(2 * tp, support + predicted) if (support + predicted) > 0 else 0.0           # = 2PR/(P+R); zero_division = 0
        out.append({"precision": precision, "recall": recall, "f1": f1, "support": float(support), "predicted": float(predicted)})
    return out


def macro_f1(cm: np.ndarray) -> float:
    """Macro-F1 ba lop co dinh voi zero_division = 0 (== sklearn f1_score(average='macro', labels=[0,1,2], zero_division=0))."""
    return float(np.mean([c["f1"] for c in per_class(cm)]))


def mcc(cm: np.ndarray) -> float:
    """MCC da lop (Gorodkin); mau so = 0 => 0.0 (cung quy uoc sklearn)."""
    n = cm.sum()
    if n == 0:
        return 0.0
    c = np.trace(cm)
    t, p = cm.sum(axis=1), cm.sum(axis=0)
    den = np.sqrt(n * n - float((p * p).sum())) * np.sqrt(n * n - float((t * t).sum()))
    return float((c * n - float((t * p).sum())) / den) if den > 0 else 0.0


def balanced_accuracy(cm: np.ndarray) -> float:
    """Trung binh recall cua cac lop co support (== sklearn balanced_accuracy_score khi du ca 3 lop)."""
    recalls = [c["recall"] for c in per_class(cm) if c["recall"] is not None]
    return float(np.mean(recalls)) if recalls else 0.0


def summarize(y_true: Any, y_pred: Any, *, weights: Any = None) -> dict[str, Any]:
    t = np.asarray(y_true)
    cm = confusion(t, y_pred) if weights is None else weighted_confusion(t, y_pred, weights)
    return summarize_from_confusion(cm)


def summarize_from_confusion(cm: np.ndarray) -> dict[str, Any]:
    cm = np.asarray(cm, dtype=np.float64)
    if cm.shape != (N_CLASSES, N_CLASSES) or not np.isfinite(cm).all() or (cm < 0).any():
        raise ValueError("ma tran nham lan phai 3x3, huu han, khong am")
    support = cm.sum(axis=1)
    missing = [CLASS_NAMES[k] for k in range(N_CLASSES) if support[k] <= 0]
    pc = per_class(cm)
    n = float(cm.sum())
    result: dict[str, Any] = {"n": n, "support": {CLASS_NAMES[k]: float(support[k]) for k in range(N_CLASSES)},
                              "confusion": cm.tolist(), "per_class": {CLASS_NAMES[k]: pc[k] for k in range(N_CLASSES)},
                              "mcc": mcc(cm), "predicted_change_rate": _div(n - float(cm[:, 1].sum()), n) if n > 0 else None}
    if missing:
        result.update(macro_f1=None, balanced_accuracy=None, null_reason=f"missing_class_support:{','.join(missing)}")
    else:
        result.update(macro_f1=macro_f1(cm), balanced_accuracy=balanced_accuracy(cm), null_reason=None)
    return result


def _proba_and_classes(y_true: Any, proba: Any) -> tuple[np.ndarray, np.ndarray]:
    y = _class_ints(y_true, "y_true")
    p = np.asarray(proba, dtype=np.float64)
    if p.shape != (len(y), N_CLASSES) or not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError("proba phai co shape (n, 3), huu han, trong [0, 1]")
    return y, p


def log_loss(y_true: Any, proba: Any) -> float:
    """-mean log p[y]; p cat tai 1e-15 (khong bao gio -inf). Chan doan, xac suat chua hieu chinh."""
    y, p = _proba_and_classes(y_true, proba)
    if len(y) == 0:
        raise ValueError("rong")
    return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-15, 1.0))))


def brier_multiclass(y_true: Any, proba: Any) -> float:
    """mean_i sum_k (p_ik - 1[y_i = k])^2 (thang 0..2)."""
    y, p = _proba_and_classes(y_true, proba)
    if len(y) == 0:
        raise ValueError("rong")
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    return float(np.mean(((p - onehot) ** 2).sum(axis=1)))


def argmax_class(proba: Any) -> np.ndarray:
    """Lop co xac suat lon nhat; hoa chon chi so NHO nhat (np.argmax) - thu tu co dinh down < stable < up."""
    return np.argmax(np.asarray(proba, dtype=np.float64), axis=1).astype(np.int64)
