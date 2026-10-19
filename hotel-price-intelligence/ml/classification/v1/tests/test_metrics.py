"""Metric 3 lop co dinh: doi chung voi sklearn (chi trong test) + chinh sach null cua subgroup."""
from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import balanced_accuracy_score, f1_score, matthews_corrcoef, precision_recall_fscore_support

from cls_training import metrics as M


@pytest.mark.parametrize("seed", range(6))
def test_matches_sklearn_on_random_three_class_data(seed):
    rng = np.random.default_rng(seed)
    n = 500
    t = rng.choice(3, size=n, p=[0.1, 0.8, 0.1])
    p = np.where(rng.random(n) < 0.6, t, rng.choice(3, size=n, p=[0.15, 0.7, 0.15]))
    cm = M.confusion(t, p)
    assert np.isclose(M.macro_f1(cm), f1_score(t, p, average="macro", labels=[0, 1, 2], zero_division=0))
    assert np.isclose(M.mcc(cm), matthews_corrcoef(t, p))
    assert np.isclose(M.balanced_accuracy(cm), balanced_accuracy_score(t, p))
    pr, rc, f1, _ = precision_recall_fscore_support(t, p, labels=[0, 1, 2], zero_division=0)
    got = M.per_class(cm)
    assert np.allclose([g["f1"] for g in got], f1) and np.allclose([g["precision"] or 0 for g in got], pr) and np.allclose([g["recall"] or 0 for g in got], rc)


def test_always_stable_baseline_macro_f1_value():
    t = np.array([0] * 8 + [1] * 85 + [2] * 7)
    cm = M.confusion(t, np.ones_like(t))
    assert np.isclose(M.macro_f1(cm), (2 * 85 / (85 + 100)) / 3)           # F1(stable) / 3 ; hai lop kia = 0 (zero_division=0)
    assert M.mcc(cm) == 0.0 and np.isclose(M.balanced_accuracy(cm), 1 / 3)


def test_subgroup_missing_class_support_gives_null_macro_but_keeps_confusion():
    s = M.summarize(np.array([1, 1, 2, 2, 1]), np.array([1, 2, 2, 1, 1]))          # khong co lop down
    assert s["macro_f1"] is None and s["balanced_accuracy"] is None and s["null_reason"] == "missing_class_support:down"
    assert s["n"] == 5 and s["support"] == {"down": 0.0, "stable": 3.0, "up": 2.0} and len(s["confusion"]) == 3
    assert s["per_class"]["down"]["recall"] is None and s["predicted_change_rate"] is not None


def test_full_split_has_numbers_and_no_null_reason():
    t = np.array([0, 1, 2, 1, 1, 0, 2])
    s = M.summarize(t, t)
    assert s["macro_f1"] == 1.0 and s["null_reason"] is None and np.isclose(s["mcc"], 1.0)


def test_weighted_confusion_equals_duplicated_rows():
    t, p = np.array([0, 1, 2, 1]), np.array([0, 2, 2, 1])
    w = np.array([1, 3, 2, 1])
    dup_t, dup_p = np.repeat(t, w), np.repeat(p, w)
    assert np.array_equal(M.weighted_confusion(t, p, w), M.confusion(dup_t, dup_p))


def test_invalid_class_index_rejected():
    with pytest.raises(ValueError):
        M.confusion(np.array([0, 3]), np.array([0, 1]))
    with pytest.raises(ValueError):
        M.confusion(np.array([0, 1]), np.array([0]))


@pytest.mark.parametrize("bad_true,bad_pred", [
    (np.array([0.9, 1.0, 2.0]), np.array([0, 1, 2])),            # 0.9 khong bi cat thanh 0
    (np.array([-1, 1, 2]), np.array([0, 1, 2])),                 # -1 khong thanh chi so am cua NumPy
    (np.array([0, 1, 3]), np.array([0, 1, 2])),
    (np.array([0, 1, 2]), np.array([0, 1])),
    (np.array([[0, 1, 2]]), np.array([[0, 1, 2]])),
    (np.array([True, False, True]), np.array([0, 1, 2])),
    (np.array(["up", "down", "up"]), np.array([0, 1, 2])),
    (np.array([0.0, np.nan, 1.0]), np.array([0, 1, 2])),
])
def test_confusion_rejects_invalid_class_inputs_without_casting(bad_true, bad_pred):
    with pytest.raises(ValueError):
        M.confusion(bad_true, bad_pred)


@pytest.mark.parametrize("weights", [np.array([-1, 1, 1]), np.array([1, np.nan, 1]), np.array([1, np.inf, 1]), np.array([1, 1]), np.array(["a", "b", "c"])])
def test_weighted_confusion_rejects_bad_weights(weights):
    with pytest.raises(ValueError):
        M.weighted_confusion(np.array([0, 1, 2]), np.array([0, 1, 2]), weights)


def test_integer_valued_floats_are_accepted_as_class_indices():
    assert np.array_equal(M.confusion(np.array([0.0, 1.0, 2.0]), np.array([0, 1, 2])), np.eye(3))
