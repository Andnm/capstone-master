"""Test thuan cho train-v3 (hop dong C1-C18): cau hinh, khoi ty le, strata, tie-break, routing, ablation, null, metric, CV, pool, xgboost gia, hoan vi, moi truong, ledger."""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd
import pytest

from training.config import ConfigError
from training.cv import CVError
from training.v3_contract import (CONFIG_VERSION_V3, DEFAULT_CONFIG_V3, PERSISTENCE, RATIO_BLOCK_COLUMNS, STABLE_SORT, add_ratio_block, apply_smoke_overrides,
                                  feature_list_sha256, load_config_v3, stable_sort, strata_masks)
from training.v3_cv import cv_candidate, describe_folds, make_folds, pooled_lifts
from training.v3_metrics import EvalSet
from training.v3_models import build_estimator, resolve_xgb_device, sample_pool, xgb_smoke, xgboost_importable
from training.v3_runner import TestAccessError, TestGate, permute_within_dates
from training.v3_runtime import EnvironmentDriftError, FitLedger, check_environment
from training.v3_select import ablation_decision, champion, mode_policy, null_summary, pick_family_winner, pick_with_persistence, top_tie_group
from v3_fixtures import FakeXGBRegressor, install_fake_xgboost, small_cfg, synthetic_frames

TIE = 1e-12


# --------------------------------------------------------------------------- cau hinh
def test_default_config_loads_with_contract_pins():
    cfg = load_config_v3()
    assert cfg["version"] == CONFIG_VERSION_V3 and cfg["seed"] == 20261008 and cfg["tie_atol"] == 1e-12
    assert list(cfg["tuning"]["families"]) == ["hgb_l1", "xgb_abs", "xgb_ph"] and cfg["tuning"]["n_iter"] == 12
    assert cfg["cv"]["n_splits"]["1"] == 3 and cfg["cv"]["n_splits"]["3"] == 2 and cfg["cv"]["min_train_days"] == 14
    assert cfg["routing"] == {"modes": ["cold_start", "history_enriched"], "min_rows": 300, "min_hotels": 20, "min_dates": 3}
    assert cfg["claims"]["ordinary_warning_threshold"] == -0.02 and cfg["budget"]["seed_stability"] is False
    assert cfg["tuning"]["families"]["xgb_ph"]["space"]["huber_slope"] == [0.02, 0.05, 0.1]
    assert cfg["tuning"]["families"]["xgb_abs"]["objective"] == "reg:absoluteerror" and cfg["tuning"]["families"]["xgb_ph"]["objective"] == "reg:pseudohubererror"
    # khong early stopping o v3 (so vong co dinh) va HGB khong co lua chon ngau nhien ngoai random_state
    assert cfg["tuning"]["families"]["hgb_l1"]["fixed"]["early_stopping"] is False and "early_stopping" not in cfg["tuning"]["families"]["hgb_l1"]["space"]
    assert cfg["controls"]["rf_l2"]["params_by_horizon"]["3"]["min_samples_leaf"] == 3 and cfg["controls"]["rf_l2"]["params_by_horizon"]["1"]["min_samples_leaf"] == 1
    assert len(cfg["config_sha256"]) == 64 and load_config_v3()["config_sha256"] == cfg["config_sha256"]


def test_config_hash_changes_with_any_edit_and_smoke_overrides_never_collide(tmp_path):
    cfg = load_config_v3()
    smoke = apply_smoke_overrides(cfg, families=["hgb_l1"], n_iter=2, null_n=1)
    assert smoke["config_sha256"] != cfg["config_sha256"] and smoke["smoke"] is True and list(smoke["tuning"]["families"]) == ["hgb_l1"]
    text = DEFAULT_CONFIG_V3.read_text(encoding="utf-8").replace("tie_atol: 1.0e-12", "tie_atol: 2.0e-12")
    edited = tmp_path / "edited.yaml"
    edited.write_text(text, encoding="utf-8")
    assert load_config_v3(edited)["config_sha256"] != cfg["config_sha256"]
    with pytest.raises(ConfigError):
        apply_smoke_overrides(cfg, families=["nope"], n_iter=None, null_n=None)


@pytest.mark.parametrize("edit,message", [
    ("n_iter: 12", "n_iter: 99999"),
    ("target_transform: log_ratio", "target_transform: price"),
    ("version: train-v3.0.0", "version: train-v3.9.9"),
    ("tie_atol: 1.0e-12", "tie_atol: 0"),
])
def test_invalid_config_is_rejected(tmp_path, edit, message):
    path = tmp_path / "bad.yaml"
    path.write_text(DEFAULT_CONFIG_V3.read_text(encoding="utf-8").replace(edit, message), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config_v3(path)


def test_feature_hash_uses_the_agreed_compact_serialization():
    features = ["a", "b", "Hà"]
    expected = hashlib.sha256(json.dumps(features, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert feature_list_sha256(features) == expected
    assert feature_list_sha256(features) != hashlib.sha256(json.dumps(features, ensure_ascii=False).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- khoi ty le + strata
def _ratio_frame(**overrides):
    base = {"current_price": [1000.0], "price_rolling_mean_14": [800.0], "price_max_trailing_14": [1250.0], "price_min_trailing_14": [500.0],
            "price_rolling_mean_7": [900.0], "price_rolling_std_7": [90.0], "price_velocity": [-0.05]}
    base.update({k: [v] for k, v in overrides.items()})
    return pd.DataFrame(base)


def test_ratio_block_formulas_and_columns():
    out = add_ratio_block(_ratio_frame())
    assert list(out.columns[-6:]) == list(RATIO_BLOCK_COLUMNS)
    row = out.iloc[0]
    assert row["r_mean14"] == pytest.approx(1000 / 800) and row["r_max14"] == pytest.approx(1000 / 1250) and row["r_min14"] == pytest.approx(2.0)
    assert row["log_current"] == pytest.approx(math.log(1000)) and row["cv7"] == pytest.approx(0.1) and row["abs_velocity"] == pytest.approx(0.05)


@pytest.mark.parametrize("column,bad,target", [("price_rolling_mean_14", 0.0, "r_mean14"), ("price_rolling_mean_14", -5.0, "r_mean14"), ("price_rolling_mean_14", np.nan, "r_mean14"),
                                               ("price_rolling_mean_14", np.inf, "r_mean14"), ("price_max_trailing_14", 0.0, "r_max14"), ("price_min_trailing_14", np.nan, "r_min14"),
                                               ("price_rolling_mean_7", 0.0, "cv7"), ("price_rolling_std_7", -1.0, "cv7"), ("price_velocity", np.nan, "abs_velocity"),
                                               ("price_velocity", np.inf, "abs_velocity"), ("current_price", 0.0, "log_current"), ("current_price", -3.0, "log_current")])
def test_ratio_block_guards_give_nan_never_inf_or_fake_zero(column, bad, target):
    out = add_ratio_block(_ratio_frame(**{column: bad}))
    assert math.isnan(out.iloc[0][target])
    assert not np.isinf(out[list(RATIO_BLOCK_COLUMNS)].to_numpy(float)).any()


def test_strata_boundaries_use_the_ratio_definition_at_plus_minus_2pct():
    cur = np.full(10, 1000.0)
    #                 0       1       2       3      4      5       6        7       8       9
    y = np.array([1000.0, 1019.0, 1021.0, 981.0, 979.0, 1020.0, 980.0, 500.0, 2000.0, 2000.1])
    m = strata_masks(y, cur)
    assert m["exact_unchanged"].tolist() == [True] + [False] * 9
    # BIEN SO HOC CHINH XAC (GPT file 16 MIN2): doi <=> |y - cur| > 2% * cur. Dung 2.0% (1000->1020, 1000->980) KHONG la doi; vuot 2% moi la doi.
    # (Dinh nghia float cu `abs(y/cur - 1) > 0.02` coi 1020/980 la DOI vi 1020/1000 - 1 = 0.020000000000000018: artifact so hoc, da bo.)
    assert (1020.0 / 1000.0 - 1) > 0.02 and abs(980.0 / 1000.0 - 1) > 0.02                      # chung minh artifact float cua dinh nghia cu
    got = m["changed_nonspike"].tolist()
    assert got[:7] == [False, False, True, False, True, False, False]                          # 1019 F, 1021 T, 981 F, 979 T, 1020 F (dung 2%), 980 F (dung 2%)
    assert got[7:] == [True, True, False]        # y/cur = 0.5 va 2.0 la ordinary (bien dong) va thuoc 'doi'; 2.0001 la extreme nen KHONG la changed_nonspike
    assert m["ordinary"].tolist() == [True] * 9 + [False] and (m["ordinary"] ^ m["extreme"]).all()


def test_strict_boundary_is_exact_integer_arithmetic_for_varied_magnitudes():
    from training.v3_contract import strict_changed

    for cur, y in [(1_143_000, 1_165_860), (1_143_000, 1_120_140), (50, 51), (50, 49), (7_000_000, 7_140_000), (1_257_150, 1_282_293)]:
        assert abs(y - cur) * 50 == cur                                                       # day la cac cap DUNG 2% (kiem bang so nguyen Python)
        assert not strict_changed(np.array([float(y)]), np.array([float(cur)]))[0], (cur, y)  # dung 2% KHONG la doi
    assert not strict_changed(np.array([1_165_860.0]), np.array([1_143_000.0]))[0] and strict_changed(np.array([1_165_861.0]), np.array([1_143_000.0]))[0]
    assert not strict_changed(np.array([1_120_140.0]), np.array([1_143_000.0]))[0] and strict_changed(np.array([1_120_139.0]), np.array([1_143_000.0]))[0]
    rng = np.random.default_rng(3)
    cur = rng.integers(1_000, 200_000_000, 5000)
    y = np.where(rng.random(5000) < 0.3, cur + (cur // 50) * rng.choice([-1, 1], 5000), rng.integers(1_000, 200_000_000, 5000))
    got = strict_changed(y.astype(float), cur.astype(float))
    want = np.array([abs(int(a) - int(b)) * 50 > int(b) for a, b in zip(y, cur)])                # tham chieu bang so nguyen Python (khong float)
    assert np.array_equal(got, want)
    on_boundary = np.array([abs(int(a) - int(b)) * 50 == int(b) for a, b in zip(y, cur)])
    assert on_boundary.sum() > 5 and not got[on_boundary].any()                                  # mau co nhieu dung-2% va KHONG cai nao bi tinh la doi


def test_stable_sort_is_deterministic_and_casts_dates():
    frame = pd.DataFrame({"hotel_id": ["b", "a", "a"], "checkin_date": [pd.Timestamp("2026-09-02"), pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01")],
                          "canonical_series_id": ["s", "s", "s"], "vn_observation_date": [pd.Timestamp("2026-08-30").date(), pd.Timestamp("2026-08-30").date(), pd.Timestamp("2026-08-29").date()],
                          "warehouse_record_id": [3, 2, 1]})
    out = stable_sort(frame)
    assert out["warehouse_record_id"].tolist() == [1, 2, 3] and out["vn_observation_date"].map(type).eq(str).all()
    assert STABLE_SORT[0] == "vn_observation_date" and STABLE_SORT[-1] == "warehouse_record_id"


# --------------------------------------------------------------------------- chon mo hinh (C5'/C17)
def test_top_tie_group_is_computed_around_the_best_not_pairwise():
    best, group = top_tie_group({"a": 1.0, "b": 1.0 - 0.6e-12, "c": 1.0 - 1.2e-12}, TIE)       # c cach b < tol nhung cach best > tol => khong vao nhom (bac cau bi chan)
    assert best == 1.0 and sorted(group) == ["a", "b"]


def test_persistence_wins_every_tie_and_learned_must_exceed_tolerance():
    order = ["m1", "m2"]
    assert pick_with_persistence({"m1": 5e-13}, order, TIE)["winner"] == PERSISTENCE            # lift trong tolerance == hoa voi persistence (0.0)
    assert pick_with_persistence({"m1": 0.0}, order, TIE)["winner"] == PERSISTENCE
    assert pick_with_persistence({"m1": -0.3, "m2": -0.1}, order, TIE)["winner"] == PERSISTENCE
    assert pick_with_persistence({"m1": 2e-12}, order, TIE)["winner"] == "m1"
    got = pick_with_persistence({"m1": 0.1, "m2": 0.1 - 5e-13}, order, TIE)                      # hoa giua hai model hoc duoc: thu tu dinh danh
    assert got["winner"] == "m1" and got["reason"] == "learned_tie_group_ordered" and got["tie_group"] == ["m1", "m2"]
    assert pick_with_persistence({"m2": 0.2, "m1": 0.2}, order, TIE)["winner"] == "m1"
    # ten khong co trong order xep sau, theo ten
    assert pick_with_persistence({"zzz": 0.2, "aaa": 0.2}, order, TIE)["winner"] == "aaa"


def test_undefined_scores_rank_after_valid_and_never_win():
    out = pick_with_persistence({"m1": None, "m2": float("nan"), "m3": 0.05}, ["m1", "m2", "m3"], TIE)
    assert out["winner"] == "m3" and out["undefined"] == ["m1", "m2"]
    none_valid = pick_with_persistence({"m1": None, "m2": float("nan")}, ["m1", "m2"], TIE)
    assert none_valid["winner"] == PERSISTENCE and none_valid["reason"] == "persistence_best"
    assert pick_with_persistence({}, [], TIE)["winner"] == PERSISTENCE


def test_family_winner_has_no_persistence_and_follows_pool_order_on_ties():
    got = pick_family_winner({"f-00": -0.2, "f-01": -0.2, "f-02": None}, ["f-00", "f-01", "f-02"], TIE)
    assert got["winner"] == "f-00" and got["undefined"] == ["f-02"]                               # tat ca am nhung van chon candidate tot nhat (champion se xet persistence sau)
    assert pick_family_winner({"f-00": None}, ["f-00"], TIE)["winner"] is None


def test_each_arm_ranks_by_its_own_metric_only():
    cands = {"m_vnd": {"lift_vnd": 0.2, "lift_log": -0.5}, "m_log": {"lift_vnd": -0.1, "lift_log": 0.3}}
    a = champion("A", cands, ["m_vnd", "m_log"], TIE)
    b = champion("B", cands, ["m_vnd", "m_log"], TIE)
    assert (a["winner"], a["metric"]) == ("m_vnd", "lift_vnd") and (b["winner"], b["metric"]) == ("m_log", "lift_log")      # khong dung VND de chan B


def test_champion_is_persistence_when_no_learned_model_beats_it():
    cands = {"m": {"lift_vnd": -0.05, "lift_log": 0.02}}
    assert champion("A", cands, ["m"], TIE)["winner"] == PERSISTENCE and champion("B", cands, ["m"], TIE)["winner"] == "m"


# --------------------------------------------------------------------------- routing / ablation / null
ROUTING = {"min_rows": 300, "min_hotels": 20, "min_dates": 3}


def _stats(n=300, hotels=20, dates=3, lift=0.05, ci=(0.01, 0.1)):
    return {"n": n, "n_hotels_active": hotels, "n_dates": dates, "lift_vnd": lift, "ci95_hotel_full_split_population": list(ci) if ci else None}


def test_mode_policy_requires_support_positive_lift_and_ci_lower_above_zero():
    assert mode_policy(_stats(), ROUTING, TIE)["use_model"] is True                                # dung bien support
    for kwargs, reason in [({"n": 299}, "insufficient_support"), ({"hotels": 19}, "insufficient_support"), ({"dates": 2}, "insufficient_support"),
                           ({"lift": None}, "undefined_lift_or_ci"), ({"ci": None}, "undefined_lift_or_ci"), ({"lift": 0.0}, "lift_not_positive"),
                           ({"lift": 1e-13}, "lift_not_positive"), ({"ci": (0.0, 0.1)}, "ci_lower_not_positive"), ({"ci": (-0.02, 0.1)}, "ci_lower_not_positive")]:
        out = mode_policy(_stats(**kwargs), ROUTING, TIE)
        assert out["use_model"] is False and out["reason"] == reason, (kwargs, out)


ABL = {"accept": {"validation_incr_lift_ci_lower_gt": 0.0, "cv_every_fold_mae_vnd_lower": True}}


def test_ablation_requires_ci_lower_gt0_and_every_fold_strictly_lower():
    ok = ablation_decision(incr_lift=0.01, incr_ci_lower=0.001, per_fold_block_mae=[9, 8, 7], per_fold_raw_mae=[10, 9, 8], cfg=ABL)
    assert ok["accepted"] is True
    for kwargs in [dict(incr_lift=0.01, incr_ci_lower=0.0, per_fold_block_mae=[9, 8], per_fold_raw_mae=[10, 9]),
                   dict(incr_lift=0.01, incr_ci_lower=0.001, per_fold_block_mae=[9, 10], per_fold_raw_mae=[10, 10]),                # mot fold bang => khong thang
                   dict(incr_lift=-0.01, incr_ci_lower=0.001, per_fold_block_mae=[9], per_fold_raw_mae=[10]),
                   dict(incr_lift=None, incr_ci_lower=None, per_fold_block_mae=[9], per_fold_raw_mae=[10]),
                   dict(incr_lift=0.01, incr_ci_lower=0.001, per_fold_block_mae=[9, 8], per_fold_raw_mae=[10])]:                   # lech so fold
        assert ablation_decision(cfg=ABL, **kwargs)["accepted"] is False


def test_null_summary_is_a_heuristic_with_rank_p_and_flags():
    out = null_summary({"lift_vnd": 0.14, "lift_log": 0.1}, [{"lift_vnd": 0.0, "lift_log": 0.0}] * 10, requested=10)
    assert out["status"] == "ok" and out["complete"] and (out["requested"], out["attempted"], out["successful"], out["failed"]) == (10, 10, 10, 0)
    assert out["lift_vnd"]["real_gt_max_null"] is True and out["lift_vnd"]["rank_p"] == pytest.approx(1 / 11) and out["null_lift_gt_2pct_flag"] is False
    assert "kiem dinh 5%" in out["note"]


def test_null_summary_never_calls_a_partial_or_failed_null_complete():
    nulls = [{"lift_vnd": 0.0, "lift_log": 0.0}] * 9 + [{"lift_vnd": None, "lift_log": None, "error": "boom"}]
    partial = null_summary({"lift_vnd": 0.14, "lift_log": 0.1}, nulls, requested=10)
    assert partial["status"] == "incomplete" and not partial["complete"] and (partial["attempted"], partial["successful"], partial["failed"]) == (10, 9, 1)
    assert partial["lift_vnd"]["rank_p"] is None and partial["lift_vnd"]["real_gt_max_null"] is None                  # khong co rank/ket luan tu 9 null
    assert partial["lift_vnd"]["partial_rank_p_diagnostic"] == pytest.approx(1 / 10) and partial["lift_vnd"]["partial_rank_denominator"] == 10
    failed = null_summary({"lift_vnd": 0.14, "lift_log": 0.1}, [{"lift_vnd": None, "lift_log": None}] * 10, requested=10)
    assert failed["status"] == "failed" and failed["successful"] == 0 and failed["lift_vnd"]["rank_p"] is None and failed["lift_vnd"]["max_null"] is None
    nonfinite = null_summary({"lift_vnd": 0.14, "lift_log": 0.1}, [{"lift_vnd": float("nan"), "lift_log": 0.0}, {"lift_vnd": float("inf"), "lift_log": 0.0}], requested=2)
    assert nonfinite["status"] == "failed"
    short = null_summary({"lift_vnd": 0.14, "lift_log": 0.1}, [{"lift_vnd": 0.0, "lift_log": 0.0}] * 3, requested=10)           # chua chay du so lan yeu cau
    assert short["status"] == "incomplete" and short["attempted"] == 3
    flagged = null_summary({"lift_vnd": 0.01, "lift_log": 0.0}, [{"lift_vnd": 0.03, "lift_log": 0.0}], requested=1)
    assert flagged["status"] == "ok" and flagged["null_lift_gt_2pct_flag"] is True and flagged["lift_vnd"]["real_gt_max_null"] is False


# --------------------------------------------------------------------------- metric
def _eval(n_hotels=12, rows_per=10, seed=0, unchanged_share=0.6):
    rng = np.random.default_rng(seed)
    rows = []
    for h in range(n_hotels):
        for i in range(rows_per):
            cur = 1_000_000 * float(np.exp(rng.normal(0, 0.3)))
            same = rng.random() < unchanged_share
            rows.append({"hotel_id": f"h{h}", "vn_observation_date": f"2026-09-{1 + i % 5:02d}", "current_price": cur, "y_true": cur if same else cur * float(np.exp(rng.normal(0, 0.1)))})
    return EvalSet(pd.DataFrame(rows), n_boot=100, seed=7)


def test_lifts_zero_for_persistence_and_none_when_denominator_is_zero():
    ev = _eval()
    out = ev.summary(np.zeros(len(ev.y)))
    assert out["lift_vnd"] == 0.0 and out["lift_log"] == 0.0 and out["identical_to_persistence"] is True and out["share_tie"] == 1.0
    flat = EvalSet(pd.DataFrame({"hotel_id": ["a", "b"], "vn_observation_date": ["d", "d"], "current_price": [1.0, 2.0], "y_true": [1.0, 2.0]}), n_boot=10)
    s = flat.summary(np.zeros(2))
    assert s["lift_vnd"] is None and s["lift_log"] is None and flat.strata(np.zeros(2))["all"]["lift_vnd"] is None
    assert flat.strata(np.zeros(2))["all"]["lift_vnd_undefined_reason"] == "persistence_abs_error_sum_is_zero"


def test_nonfinite_prediction_is_rejected_not_silently_zeroed():
    ev = _eval()
    bad = np.zeros(len(ev.y))
    bad[3] = np.nan
    with pytest.raises(ValueError, match="khong huu han"):
        ev.summary(bad)


def test_bootstrap_is_reproducible_paired_and_uses_the_whole_split_population():
    a, b = _eval(seed=1), _eval(seed=1)
    assert np.array_equal(a.idx, b.idx) and a.idx.shape == (100, 12)
    z = np.where(a.masks["changed_nonspike"], a.z * 0.5, 0.0)
    assert a.summary(z)["lift_vnd_ci95_hotel"] == b.summary(z)["lift_vnd_ci95_hotel"]
    mask = np.array([h in ("h0", "h1") for h in a.frame["hotel_id"]])                     # chi 2/12 hotel co hang trong mask; quan the van la 12
    ml = a.mask_lift(z, mask)
    assert ml["n_hotels_active"] == 2 and a.idx.shape[1] == a.nh == 12 and ml["undefined_bootstrap_denominators"] >= 0


def test_incr_lift_and_macro_diff_signs():
    ev = _eval(unchanged_share=0.0)
    good = ev.z.copy()                                      # oracle: du doan dung z
    bad = np.zeros(len(ev.z))
    assert ev.incr_lift(bad, good)["incr_lift_vnd"] > 0.9 and ev.macro_diff(bad, good)["diff"] > 0
    assert ev.incr_lift(good, bad)["incr_lift_vnd"] < 0


def test_breakdown_masks_cover_every_group_and_exact_unchanged_bias():
    ev = _eval()
    rows = ev.breakdown(np.zeros(len(ev.y)), "vn_observation_date")
    assert [r["vn_observation_date"] for r in rows] == sorted({f"2026-09-{1 + i % 5:02d}" for i in range(10)}) and sum(r["n"] for r in rows) == len(ev.y)
    assert ev.exact_unchanged_bias(np.zeros(len(ev.y))) == 0.0 and ev.exact_unchanged_bias(np.full(len(ev.y), 0.01)) == pytest.approx(math.exp(0.01) - 1)


# --------------------------------------------------------------------------- CV + pool + estimator
def test_cv_folds_have_the_agreed_sizes_and_purge_for_h1_and_h3():
    cfg = load_config_v3()
    rng_dates = pd.date_range("2026-08-21", periods=26, freq="D")
    train = pd.DataFrame({"vn_observation_date": np.repeat(rng_dates.astype(str), 3), "y_true": 1.0, "current_price": 1.0})
    folds = make_folds(train, 1, cfg)
    assert [len(f[1]) // 3 for f in folds] == [4, 4, 3]                                         # h1: 3 fold, 4/4/3 ngay
    info = describe_folds(train, folds)
    assert [f["val_days"] for f in info] == [4, 4, 3]
    for tr, va in folds:                                                                          # purge: moi ngay train + gap < ngay dau cua validation
        first_val = pd.Timestamp(train["vn_observation_date"].iloc[va].min())
        assert (pd.to_datetime(train["vn_observation_date"].iloc[tr]) + pd.Timedelta(days=1) < first_val).all()
    short = train[pd.to_datetime(train["vn_observation_date"]) <= "2026-09-11"]                 # 22 ngay (nhu h3 that)
    f3 = make_folds(short, 3, cfg)
    assert [len(f[1]) // 3 for f in f3] == [3, 2] and len(f3) == 2
    with pytest.raises(CVError):
        make_folds(short[pd.to_datetime(short["vn_observation_date"]) <= "2026-08-30"], 3, cfg)


class _Const:
    def __init__(self, value=0.0, fail=False, nan=False):
        self.value, self.fail, self.nan = value, fail, nan

    def fit(self, X, y):
        if self.fail:
            raise RuntimeError("boom")
        return self

    def predict(self, X):
        return np.full(len(X), np.nan if self.nan else self.value)


def test_cv_candidate_scores_both_scales_on_the_same_predictions_and_pools_correctly():
    rng = np.random.default_rng(0)
    n = 60
    cur = np.full(n, 100.0)
    z = rng.normal(0, 0.1, n)
    y = cur * np.exp(z)
    X = rng.normal(size=(n, 2))
    folds = [(np.arange(0, 30), np.arange(30, 45)), (np.arange(0, 45), np.arange(45, 60))]
    res = cv_candidate(lambda: _Const(0.0), X, z, cur, y, folds)
    assert res["status"] == "ok" and res["pooled"]["lift_vnd"] == pytest.approx(0.0) and res["pooled"]["lift_log"] == pytest.approx(0.0)
    assert [f["n_val"] for f in res["per_fold"]] == [15, 15] and res["pooled"]["n_val"] == 30
    hat = 0.02
    res2 = cv_candidate(lambda: _Const(hat), X, z, cur, y, folds)
    idx = np.r_[30:60]
    manual = pooled_lifts(y[idx], cur[idx], np.full(30, hat))
    assert res2["pooled"]["lift_vnd"] == pytest.approx(manual["lift_vnd"]) and res2["pooled"]["lift_log"] == pytest.approx(manual["lift_log"])
    assert res2["pooled"]["mae_vnd"] == pytest.approx(np.abs(cur[idx] * np.exp(hat) - y[idx]).mean())


def test_cv_candidate_records_failures_and_nonfinite_without_zero_scores():
    z = np.zeros(20)
    cur, y, X = np.full(20, 10.0), np.full(20, 10.0), np.zeros((20, 1))
    folds = [(np.arange(0, 10), np.arange(10, 15)), (np.arange(0, 15), np.arange(15, 20))]
    boom = cv_candidate(lambda: _Const(fail=True), X, z, cur, y, folds)
    assert boom["status"] == "failed" and "RuntimeError" in boom["reason"] and boom["pooled"] is None
    nan = cv_candidate(lambda: _Const(nan=True), X, z, cur, y, folds)
    assert nan["status"] == "failed" and "InvalidPredictionError" in nan["reason"] and "khong huu han" in nan["reason"] and nan["pooled"] is None
    huge = cv_candidate(lambda: _Const(1000.0), X, z, cur, y, folds)                              # z huu han nhung gia = cur*exp(1000) = inf (GPT file 16 M2)
    assert huge["status"] == "failed" and "gia du bao khong huu han" in huge["reason"] and huge["pooled"] is None
    tiny = cv_candidate(lambda: _Const(-1000.0), X, z, cur, y, folds)                             # exp(-1000) = 0 => gia 0 cung la loi
    assert tiny["status"] == "failed" and "khong duong" in tiny["reason"]


def test_pool_sampling_is_reproducible_unique_and_shares_ids_across_scorers():
    cfg = load_config_v3()
    spec = cfg["tuning"]["families"]["hgb_l1"]
    a = sample_pool("hgb_l1", spec, 12, 20261008)
    b = sample_pool("hgb_l1", spec, 12, 20261008)
    assert a == b and [c["candidate_id"] for c in a] == [f"hgb_l1-{i:02d}" for i in range(12)]
    assert len({tuple(sorted(c["params"].items())) for c in a}) == 12
    for c in a:
        for k, v in c["params"].items():
            assert v in spec["space"][k] and type(v) in (int, float)                          # JSON-safe, thuoc khong gian
    assert sample_pool("hgb_l1", spec, 12, 1) != a
    for family in ("xgb_abs", "xgb_ph"):
        assert len(sample_pool(family, cfg["tuning"]["families"][family], 12, 20261008)) == 12


def test_hgb_estimator_pins_loss_no_early_stopping_and_seed():
    cfg = load_config_v3()
    est = build_estimator(cfg["tuning"]["families"]["hgb_l1"], {"max_depth": 3, "learning_rate": 0.05, "max_iter": 150, "min_samples_leaf": 20}, seed=20261008)
    params = est.get_params()
    assert params["loss"] == "absolute_error" and params["early_stopping"] is False and params["random_state"] == 20261008 and params["categorical_features"] is None
    assert params["max_leaf_nodes"] == 31 and params["max_bins"] == 255 and params["l2_regularization"] == 0.0 and params["max_features"] == 1.0


def test_xgb_estimator_maps_objective_device_and_huber_slope_with_fake_module(monkeypatch):
    install_fake_xgboost(monkeypatch)
    cfg = load_config_v3()
    abs_est = build_estimator(cfg["tuning"]["families"]["xgb_abs"], {"max_depth": 3}, seed=1, device="cuda")
    ph_est = build_estimator(cfg["tuning"]["families"]["xgb_ph"], {"max_depth": 3, "huber_slope": 0.05}, seed=1, device="cpu")
    assert abs_est.kwargs["objective"] == "reg:absoluteerror" and abs_est.kwargs["device"] == "cuda" and abs_est.kwargs["tree_method"] == "hist"
    assert ph_est.kwargs["objective"] == "reg:pseudohubererror" and ph_est.kwargs["huber_slope"] == 0.05 and ph_est.kwargs["random_state"] == 1


# --------------------------------------------------------------------------- C8: smoke thiet bi
def test_xgb_device_resolution_cuda_ok_cpu_fallback_and_unavailable(monkeypatch):
    install_fake_xgboost(monkeypatch)
    ledger = FitLedger(100.0)
    ok = resolve_xgb_device("auto", 1, ledger)
    assert ok["device"] == "cuda" and ok["fallback_reason"] is None and len(ok["smokes"]) == 2
    install_fake_xgboost(monkeypatch, fail_devices={"cuda"})
    ledger2 = FitLedger(100.0)
    fb = resolve_xgb_device("cuda", 1, ledger2)
    assert fb["device"] == "cpu" and "CUDA" in fb["fallback_reason"] and sum(e["status"] == "failed" for e in ledger2.entries) == 2     # lan loi CUDA duoc dem
    assert ledger2.summary()["by_kind"]["smoke"]["attempts"] == 4
    install_fake_xgboost(monkeypatch, fail_devices={"cuda", "cpu"})
    none = resolve_xgb_device("auto", 1, FitLedger(100.0))
    assert none["device"] is None and none["fallback_reason"]
    only_cpu = resolve_xgb_device("cpu", 1, FitLedger(100.0))
    assert only_cpu["device"] is None


def test_xgboost_missing_gives_clear_reason(monkeypatch):
    monkeypatch.setattr("training.v3_models.xgboost_importable", lambda: False)
    ledger = FitLedger(100.0)
    out = resolve_xgb_device("auto", 1, ledger)
    assert out["device"] is None and out["fallback_reason"] == "xgboost_not_installed" and ledger.entries[0]["status"] == "failed"
    assert xgboost_importable() in (True, False)


def test_xgb_smoke_reports_exceptions_not_raises(monkeypatch):
    install_fake_xgboost(monkeypatch, fail_devices={"cuda"})
    bad = xgb_smoke("reg:absoluteerror", "cuda", 1)
    assert bad["ok"] is False and "RuntimeError" in bad["error"]


# --------------------------------------------------------------------------- hoan vi nhan (C11)
def _reference_permutation(z, dates, seed):                                  # cai dat doc lap theo mo ta o 07 (GPT cung cai dat nhu vay, 10/10 SHA khop)
    rng = np.random.default_rng(seed)
    out = z.copy()
    for d in sorted(set(dates)):
        idx = np.flatnonzero(dates == d)
        out[idx] = z[idx][rng.permutation(len(idx))]
    return out


def test_permutation_matches_the_pinned_algorithm_and_keeps_each_day_multiset():
    dates = np.array(["2026-09-01"] * 4 + ["2026-09-02"] * 3 + ["2026-09-03"] * 5)
    z = np.arange(12, dtype=float) * 0.01
    got = permute_within_dates(z, dates, 20261008)
    assert np.array_equal(got, _reference_permutation(z, dates, 20261008)) and not np.array_equal(got, z)
    for d in set(dates):
        assert sorted(got[dates == d]) == sorted(z[dates == d])
    assert np.array_equal(got, permute_within_dates(z, dates, 20261008)) and not np.array_equal(got, permute_within_dates(z, dates, 20261009))
    with pytest.raises(ValueError, match="sap theo ngay"):
        permute_within_dates(z, dates[::-1], 1)


# --------------------------------------------------------------------------- moi truong + ledger
def test_environment_fails_closed_unless_drift_is_explicitly_allowed():
    cfg = load_config_v3()
    pinned = dict(cfg["environment"]["expected_versions"], python="3.13.1", pyarrow="1")
    ok = check_environment(cfg, allow_drift=False, actual=pinned)
    assert ok["status"] == "pinned" and ok["mismatch"] == {} and ok["official_eligible"] is False and len(ok["fingerprint"]) == 64
    drift = dict(pinned, **{"scikit-learn": "1.7.2"})
    with pytest.raises(EnvironmentDriftError, match="scikit-learn"):
        check_environment(cfg, allow_drift=False, actual=drift)
    allowed = check_environment(cfg, allow_drift=True, actual=drift)
    assert allowed["status"] == "exploratory_env_drift" and allowed["mismatch"]["scikit-learn"] == {"expected": "1.6.1", "actual": "1.7.2"}
    assert allowed["fingerprint"] != ok["fingerprint"]                                       # identity moi truong thuc nam trong fingerprint, khong chi hau to
    with pytest.raises(EnvironmentDriftError):
        check_environment(cfg, allow_drift=False, actual={**pinned, "xgboost": None})        # thieu thu vien cung la lech


def test_ledger_counts_failures_and_budget_with_injected_clock():
    t = [0.0]
    ledger = FitLedger(10.0, clock=lambda: t[0])
    ledger.add("cv", "a", seconds=1.0, fits=3)
    ledger.add("cv", "b", seconds=1.0, status="failed", reason="boom", fits=1)
    ledger.add("smoke", "x", seconds=0.1, status="failed", reason="cuda")
    assert not ledger.over_budget() and ledger.remaining() == 10.0
    t[0] = 11.0
    assert ledger.over_budget() and ledger.remaining() == -1.0
    s = ledger.summary({"cv": 9})
    assert s["by_kind"]["cv"] == {"attempts": 2, "ok": 1, "failed": 1, "skipped": 0} and s["by_kind"]["smoke"]["failed"] == 1 and s["nominal"] == {"cv": 9}


def test_test_gate_blocks_before_lock_and_outside_the_prespecified_list():
    gate = TestGate()
    with pytest.raises(TestAccessError, match="chua khoa"):
        gate.predict("m", lambda: np.zeros(1))
    gate.lock(["m"])
    assert gate.predict("m", lambda: np.zeros(2)).tolist() == [0.0, 0.0] and gate.predicted == ["m"]
    with pytest.raises(TestAccessError, match="prespecify"):
        gate.predict("other", lambda: np.zeros(1))


# --------------------------------------------------------------------------- GPT file 16: M2 gia hop le, MIN3 khoi ty le tran so, MIN1 horizon, M4 fold-train, M5 thiet bi thuc te
def test_price_from_z_rejects_nonfinite_overflow_underflow_and_bad_shapes():
    from training.v3_contract import InvalidPredictionError, price_from_z

    cur = np.array([100.0, 200.0])
    assert price_from_z(cur, np.zeros(2)).tolist() == [100.0, 200.0]                                  # persistence hop le (z=0)
    assert price_from_z(cur, np.array([0.1, -0.1]))[0] == pytest.approx(100 * math.exp(0.1))
    for z, message in [(np.array([1000.0, 0.0]), "khong huu han"), (np.array([-1000.0, 0.0]), "khong duong"), (np.array([np.nan, 0.0]), "z khong huu han"),
                       (np.array([np.inf, 0.0]), "z khong huu han"), (np.array([-np.inf, 0.0]), "z khong huu han"), (np.zeros(3), "shape")]:
        with pytest.raises(InvalidPredictionError, match=message):
            price_from_z(cur, z)
    for bad_cur in (np.array([0.0, 1.0]), np.array([-1.0, 1.0]), np.array([np.nan, 1.0]), np.array([np.inf, 1.0])):
        with pytest.raises(InvalidPredictionError, match="current_price"):
            price_from_z(bad_cur, np.zeros(2))
    with pytest.raises(InvalidPredictionError, match="shape"):
        price_from_z(cur.reshape(1, 2), np.zeros((1, 2)))


def test_evalset_never_publishes_inf_or_nan_metrics_for_invalid_predictions_and_validates_inputs():
    from training.v3_contract import InvalidPredictionError

    ev = _eval()
    bad = np.zeros(len(ev.y))
    bad[0] = 1000.0
    for call in (ev.summary, ev.strata, ev.lifts):
        with pytest.raises(InvalidPredictionError):
            call(bad)
    with pytest.raises(InvalidPredictionError):
        ev.incr_lift(np.zeros(len(ev.y)), bad)
    for current, y in [(0.0, 1.0), (1.0, 0.0), (np.nan, 1.0), (1.0, np.inf)]:
        with pytest.raises(InvalidPredictionError):
            EvalSet(pd.DataFrame({"hotel_id": ["a"], "vn_observation_date": ["d"], "current_price": [current], "y_true": [y]}), n_boot=5)
    # denominator-zero (persistence hoan hao) la 'khong xac dinh', khong phai 'sai': van tra None + ly do (khac hoan toan voi gia sai)
    flat = EvalSet(pd.DataFrame({"hotel_id": ["a", "b"], "vn_observation_date": ["d", "d"], "current_price": [1.0, 2.0], "y_true": [1.0, 2.0]}), n_boot=5)
    assert flat.summary(np.zeros(2))["lift_vnd"] is None


def test_ratio_block_overflow_from_finite_inputs_becomes_nan_and_is_counted():
    tiny = np.nextafter(0.0, 1.0)
    frame = pd.DataFrame({"current_price": [1000.0, 1000.0, 1000.0], "price_rolling_mean_14": [tiny, 500.0, 500.0], "price_max_trailing_14": [tiny, tiny, 800.0],
                          "price_min_trailing_14": [500.0, 500.0, tiny], "price_rolling_mean_7": [tiny, 900.0, 900.0], "price_rolling_std_7": [1e308, 90.0, 90.0],
                          "price_velocity": [np.inf, 0.1, -0.1]})
    out = add_ratio_block(frame)
    block = out[list(RATIO_BLOCK_COLUMNS)].to_numpy(float)
    assert not np.isinf(block).any()                                                                # dau ra LUON huu han hoac NaN
    assert math.isnan(out.loc[0, "r_mean14"]) and out.loc[1, "r_mean14"] == pytest.approx(2.0)
    assert math.isnan(out.loc[0, "r_max14"]) and math.isnan(out.loc[1, "r_max14"]) and math.isnan(out.loc[2, "r_min14"])
    assert math.isnan(out.loc[0, "cv7"]) and math.isnan(out.loc[0, "abs_velocity"])                   # std 1e308 / mean tiny tran so; velocity inf
    counts = out.attrs["ratio_block_invalid"]
    assert (counts["r_mean14"], counts["r_max14"], counts["r_min14"], counts["cv7"], counts["abs_velocity"], counts["log_current"]) == (1, 2, 1, 1, 0, 0)    # velocity=inf la DAU VAO khong hop le (NaN), khong phai tran so
    assert set(counts) == set(RATIO_BLOCK_COLUMNS)


def test_horizon_support_is_validated_instead_of_copying_another_horizons_parameters():
    from training.v3_contract import validate_horizon_support

    cfg = load_config_v3()
    validate_horizon_support(cfg, [1])
    validate_horizon_support(cfg, [1, 3])
    for horizons in ([7], [14], [3, 7]):
        with pytest.raises(ConfigError, match="chua co tham so da chot"):
            validate_horizon_support(cfg, horizons)
    assert sorted(cfg["controls"]["rf_l2"]["params_by_horizon"]) == ["1", "3"]


def test_cv_fold_train_scores_are_in_sample_and_match_direct_prediction():
    rng = np.random.default_rng(1)
    n = 60
    cur = rng.uniform(100, 200, n)
    z = rng.normal(0, 0.1, n)
    y = cur * np.exp(z)
    X = rng.normal(size=(n, 2))
    folds = [(np.arange(0, 30), np.arange(30, 45)), (np.arange(0, 45), np.arange(45, 60))]
    hat = 0.03
    res = cv_candidate(lambda: _Const(hat), X, z, cur, y, folds)
    for k, (tr, va) in enumerate(folds):
        tr_scores = res["per_fold"][k]["train"]
        assert tr_scores["label"] == "in_sample_fold_train" and tr_scores["n"] == len(tr)
        assert tr_scores["mae_vnd"] == pytest.approx(np.abs(cur[tr] * np.exp(hat) - y[tr]).mean()) and tr_scores["mae_log"] == pytest.approx(np.abs(z[tr] - hat).mean())
        direct = pooled_lifts(y[tr], cur[tr], np.full(len(tr), hat))
        assert tr_scores["lift_vnd"] == pytest.approx(direct["lift_vnd"]) and tr_scores["lift_log"] == pytest.approx(direct["lift_log"])
        assert tr_scores["persistence_mae_vnd"] == pytest.approx(np.abs(cur[tr] - y[tr]).mean())
        assert res["per_fold"][k]["mae_vnd"] == pytest.approx(np.abs(cur[va] * np.exp(hat) - y[va]).mean())            # fold-VAL van la diem chinh (khong lan voi in-sample)


def test_xgb_warning_only_cpu_fallback_is_not_reported_as_cuda_success(monkeypatch):
    install_fake_xgboost(monkeypatch, warn_fallback_devices={"cuda"})
    bad = xgb_smoke("reg:absoluteerror", "cuda", 1)
    assert bad["ok"] is False and bad["actual_device"] == "cpu" and "device_fell_back_to_cpu" in bad["error"] and bad["warnings"] and "No visible GPU" in bad["warnings"][0]
    ledger = FitLedger(100.0)
    out = resolve_xgb_device("auto", 1, ledger)
    assert out["device"] == "cpu" and out["actual_device"] == "cpu" and out["device_verified"] is True and "device_fell_back_to_cpu" in out["fallback_reason"]
    assert [e["status"] for e in ledger.entries] == ["failed", "failed", "ok", "ok"] and [e["device"] for e in ledger.entries] == ["cuda", "cuda", "cpu", "cpu"]     # lan thu CPU duoc dem


def test_xgb_actual_cuda_and_explicit_cpu_are_verified(monkeypatch):
    install_fake_xgboost(monkeypatch)
    cuda = resolve_xgb_device("cuda", 1, FitLedger(100.0))
    assert cuda["device"] == "cuda" and cuda["actual_device"] == "cuda" and cuda["device_verified"] is True and cuda["fallback_reason"] is None
    cpu = resolve_xgb_device("cpu", 1, FitLedger(100.0))
    assert cpu["device"] == "cpu" and cpu["actual_device"] == "cpu" and cpu["device_verified"] is True and all(s["device"] == "cpu" for s in cpu["smokes"])


def test_xgb_unreadable_actual_device_is_flagged_unverified_not_assumed(monkeypatch):
    install_fake_xgboost(monkeypatch, unreadable_actual=True)
    smoke = xgb_smoke("reg:pseudohubererror", "cuda", 1)
    assert smoke["ok"] is True and smoke["actual_device"] is None and smoke["device_verified"] is False
    out = resolve_xgb_device("auto", 1, FitLedger(100.0))
    assert out["device"] == "cuda" and out["actual_device"] is None and out["device_verified"] is False                 # khong khang dinh GPU khi khong doc duoc thiet bi thuc
