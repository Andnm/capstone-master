"""Bootstrap ghep cap theo cum hotel, equal-hotel, leave-one-out: doi chung voi cai dat vet can (index-draw) va sklearn; chinh sach draw khong hop le."""
from __future__ import annotations

import numpy as np
import pytest
from sklearn.metrics import f1_score

from cls_training import bootstrap as B
from cls_training import metrics as M


def make(n_hotels=40, per_hotel=60, seed=0, skill=0.25):
    rng = np.random.default_rng(seed)
    hotels = np.repeat([f"h{i:03d}" for i in range(n_hotels)], per_hotel)
    y = rng.choice(3, size=len(hotels), p=[0.1, 0.8, 0.1])
    base = np.where(rng.random(len(y)) < 0.5, 1, y)                       # comparator yeu
    better = np.where(rng.random(len(y)) < skill, y, base)                # candidate dung nhieu hon
    return hotels, y, better, base


def macro(y, p):
    return f1_score(y, p, average="macro", labels=[0, 1, 2], zero_division=0)


def test_point_estimate_matches_sklearn_difference():
    hotels, y, a, b = make()
    out = B.paired_bootstrap_delta(hotels, y, a, b, n=200, seed=1, min_valid_fraction=0.99)
    assert np.isclose(out["point"], macro(y, a) - macro(y, b)) and out["n_hotels"] == 40 and out["valid_draws"] == 200


def test_ci_agrees_with_a_brute_force_cluster_resample():
    hotels, y, a, b = make(n_hotels=30, per_hotel=50, seed=3)
    fast = B.paired_bootstrap_delta(hotels, y, a, b, n=4000, seed=2, min_valid_fraction=0.99)
    rng = np.random.default_rng(99)
    ids = np.unique(hotels)
    rows_of = {h: np.flatnonzero(hotels == h) for h in ids}
    deltas = []
    for _ in range(800):
        pick = rng.choice(ids, size=len(ids), replace=True)
        idx = np.concatenate([rows_of[h] for h in pick])
        deltas.append(macro(y[idx], a[idx]) - macro(y[idx], b[idx]))
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    assert abs(fast["ci95"][0] - lo) < 0.012 and abs(fast["ci95"][1] - hi) < 0.012


def test_reproducible_with_seed_and_independent_of_row_order():
    hotels, y, a, b = make()
    one = B.paired_bootstrap_delta(hotels, y, a, b, n=500, seed=7, min_valid_fraction=0.99)
    two = B.paired_bootstrap_delta(hotels, y, a, b, n=500, seed=7, min_valid_fraction=0.99)
    perm = np.random.default_rng(0).permutation(len(y))
    three = B.paired_bootstrap_delta(hotels[perm], y[perm], a[perm], b[perm], n=500, seed=7, min_valid_fraction=0.99)
    assert one == two == three
    assert B.paired_bootstrap_delta(hotels, y, a, b, n=500, seed=8, min_valid_fraction=0.99)["ci95"] != one["ci95"]


def test_identical_predictions_give_exactly_zero_delta_and_ci():
    hotels, y, a, _ = make()
    out = B.paired_bootstrap_delta(hotels, y, a, a.copy(), n=300, seed=1, min_valid_fraction=0.99)
    assert out["point"] == 0.0 and out["ci95"] == [0.0, 0.0]


def test_draws_missing_a_true_class_are_invalid_and_ci_is_null_below_threshold():
    n_h = 20
    hotels = np.repeat([f"h{i:02d}" for i in range(n_h)], 10)
    y = np.ones(len(hotels), dtype=int)
    y[:3] = 0
    y[10:13] = 2                                                          # lop 0 chi o hotel h00, lop 2 chi o hotel h01
    a = y.copy()
    b = np.ones_like(y)
    out = B.paired_bootstrap_delta(hotels, y, a, b, n=1000, seed=5, min_valid_fraction=0.99)
    assert out["valid_draws"] < 1000 and out["valid_fraction"] < 0.99 and out["ci95"] is None and out["ci_null_reason"] == "valid_fraction_below_threshold"
    loose = B.paired_bootstrap_delta(hotels, y, a, b, n=1000, seed=5, min_valid_fraction=0.3)
    assert loose["ci95"] is not None and loose["valid_draws"] == out["valid_draws"]


def test_gain_concentrated_in_one_hotel_has_ci_lower_bound_zero_while_spread_gain_is_positive():
    """Cai thien chi o mot hotel: draw khong chon hotel do co delta == 0 chinh xac => can duoi CI == 0; cai thien trai deu => can duoi > 0."""
    rng = np.random.default_rng(11)
    n_h, per = 200, 40
    hotels = np.repeat([f"h{i:03d}" for i in range(n_h)], per)
    y = rng.choice(3, size=len(hotels), p=[0.12, 0.76, 0.12])
    base = np.ones_like(y)
    wrong = np.flatnonzero(y != 1)
    spread = base.copy()
    fix = rng.choice(wrong, size=len(wrong) // 3, replace=False)
    spread[fix] = y[fix]
    conc = base.copy()
    star = (hotels == "h000") & (y != 1)
    conc[star] = y[star]
    s = B.paired_bootstrap_delta(hotels, y, spread, base, n=2000, seed=1, min_valid_fraction=0.99)
    c = B.paired_bootstrap_delta(hotels, y, conc, base, n=2000, seed=1, min_valid_fraction=0.99)
    assert s["ci95"][0] > 0
    assert c["point"] > 0 and c["ci95"][0] == 0.0 and c["ci95"][1] > c["point"]


def test_equal_hotel_weights_each_hotel_by_one_not_by_rows():
    hotels = np.array(["a"] * 100 + ["b"] * 4)
    y = np.array([1] * 98 + [0, 2] + [0, 1, 2, 1])
    p = np.array([1] * 98 + [1, 1] + [0, 1, 2, 1])               # hotel a: bo lo 2 lop hiem; hotel b: hoan hao
    eq = B.equal_hotel_summary(hotels, y, p)
    cm = np.zeros((3, 3))
    for h in ("a", "b"):
        m = hotels == h
        c = M.confusion(y[m], p[m])
        cm += c / c.sum()
    expected = M.macro_f1(cm)
    assert np.isclose(eq["macro_f1"], expected) and eq["n_hotels"] == 2
    assert not np.isclose(eq["macro_f1"], M.macro_f1(M.confusion(y, p)))                 # khac gop theo hang
    per_hotel = np.mean([macro(y[hotels == h], p[hotels == h]) for h in ("a", "b")])
    assert not np.isclose(eq["macro_f1"], per_hotel)                                       # khong phai trung binh F1 cua tung hotel


def test_leave_one_out_identifies_top_contributor_and_is_flagged_non_additive():
    rng = np.random.default_rng(4)
    n_h, per = 25, 40
    hotels = np.repeat([f"h{i:02d}" for i in range(n_h)], per)
    y = rng.choice(3, size=len(hotels), p=[0.15, 0.7, 0.15])
    base = np.ones_like(y)
    cand = base.copy()
    star = (hotels == "h07") & (y != 1)
    cand[star] = y[star]                                                      # toan bo gain nam o h07
    out = B.leave_one_out(hotels, y, cand, base, top_k=3)
    assert out["top1_hotel"] == "h07" and out["additive"] is False
    assert out["delta_without_top1"] < out["delta_all"] and out["delta_without_top_k"] <= out["delta_without_top1"] + 1e-12
    assert np.isclose(out["delta_all"], macro(y, cand) - macro(y, base))
    # doi chieu thu cong
    keep = hotels != "h07"
    assert np.isclose(out["delta_without_top1"], macro(y[keep], cand[keep]) - macro(y[keep], base[keep]))


def test_input_validation():
    with pytest.raises(ValueError):
        B.per_hotel_confusions(["a", "b"], [0, 1, 2], [0, 1, 2])
    with pytest.raises(ValueError):
        B.per_hotel_confusions(["a", "b"], [0, 3], [0, 1])


def test_leave_one_out_returns_null_with_reason_when_removal_drops_a_class_or_everything():
    hotels = np.array(["a"] * 4 + ["b"] * 3 + ["c"] * 2)
    y = np.array([1, 1, 1, 0, 1, 1, 0, 2, 1])                                        # 'up' chi o hotel c
    cand, base = y.copy(), np.ones_like(y)
    out = B.leave_one_out(hotels, y, cand, base, top_k=2)
    assert out["delta_all"] is not None and out["n_valid_leave_one_out"] == 2                    # bo 'c' mat lop 'up' => khong hop le
    assert out["top1_hotel"] in ("a", "b") and out["delta_without_top1"] is not None
    assert out["delta_without_top_k"] is None and out["null_reasons"]["top_k"] == "missing_class_support_after_removal"
    single = B.leave_one_out(np.array(["a"] * 4), np.array([0, 1, 2, 1]), np.array([0, 1, 2, 1]), np.ones(4, dtype=int))
    assert single["n_valid_leave_one_out"] == 0 and single["top1_hotel"] is None and single["delta_without_top1"] is None                  # bo het => khong tra 0
    assert single["null_reasons"]["top1"] == "missing_class_support_after_removal"
    nodown = B.leave_one_out(np.array(["a", "b", "c"]), np.array([1, 2, 1]), np.array([1, 2, 1]), np.array([1, 1, 1]))
    assert nodown["delta_all"] is None and nodown["null_reasons"]["delta_all"] == "missing_class_support"
