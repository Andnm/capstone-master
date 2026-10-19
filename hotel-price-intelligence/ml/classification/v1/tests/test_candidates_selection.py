"""Ung vien + CV + finalist + champion + freeze tren du lieu tong hop. Fold-local, quota, ngan sach mem, loi ung vien, tie-break, thiet bi."""
from __future__ import annotations

import json
import sys
import types

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss as sk_log_loss
from sklearn.utils.class_weight import compute_sample_weight

from cls_training import candidates as C
from cls_training import metrics as M
from cls_training import selection as S
from cls_training.data import make_folds, describe_folds

from synth import SYNTH_COLUMNS, make_frame, small_config


class FakeXGBClassifier(HistGradientBoostingClassifier):
    """Gia lap XGBClassifier (may chinh khong cai xgboost): HGB bao `device` qua get_params nhu xgboost. Lop o cap module de pickle/joblib duoc."""

    def __init__(self, device="cpu", max_iter=100, max_depth=None, learning_rate=0.1, random_state=None, early_stopping=False):
        super().__init__(max_iter=max_iter, max_depth=max_depth, learning_rate=learning_rate, random_state=random_state, early_stopping=early_stopping)
        self.device = device

    def get_booster(self):
        """Giong Booster that: `save_config()` tra JSON co learner.generic_param.device (thiet bi THAT sau fit; gia lap lay tu `self.device`)."""
        device = self.device
        return types.SimpleNamespace(save_config=lambda: json.dumps({"learner": {"generic_param": {"device": device}}}))


def fake_xgb_factory(params, fixed, *, seed, device, n_jobs):
    return FakeXGBClassifier(device=device, max_iter=int(params["n_estimators"]), max_depth=int(params["max_depth"]), learning_rate=float(params["learning_rate"]), random_state=seed)


@pytest.fixture()
def cfg(monkeypatch):
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)
    return small_config("cpu")


@pytest.fixture()
def data():
    frame, y = make_frame(700, seed=2)
    folds = make_folds(frame, 1, {"min_train_days": 8, "n_splits": {"1": 3}})
    return frame, y, folds


# ----------------------------------------------------------------- lay mau ung vien

def test_sampling_is_deterministic_has_locked_quota_and_unique_candidates(cfg):
    a, b = C.sample_candidates(cfg), C.sample_candidates(cfg)
    assert [(c.id, c.params) for c in a] == [(c.id, c.params) for c in b]
    assert [sum(c.family == f for c in a) for f in ("lr", "hgb", "xgb")] == [6, 8, 8] and len(a) == 22
    assert [c.order for c in a] == list(range(22)) and len({c.id for c in a}) == 22
    assert all(c.params["weight"] in ("none", "balanced") for c in a)
    assert {c.params["weight"] for c in a if c.family == "lr"} == {"none", "balanced"}
    for fam in ("lr", "hgb", "xgb"):
        keys = [tuple(sorted(c.params.items())) for c in a if c.family == fam]
        assert len(set(keys)) == len(keys)
    other = small_config("cpu")
    other["tuning"]["sampler_seed"] = 1
    assert [c.params for c in C.sample_candidates(other)] != [c.params for c in a]


def test_balanced_weights_match_sklearn_and_none_is_ones():
    y = np.array([0] * 5 + [1] * 85 + [2] * 10)
    assert np.allclose(C.sample_weights(y, "balanced"), compute_sample_weight("balanced", y))
    assert (C.sample_weights(y, "none") == 1).all()
    with pytest.raises(C.CandidateError, match="du ca 3 lop"):
        C.sample_weights(np.array([0, 1, 1]), "balanced")
    with pytest.raises(C.CandidateError):
        C.sample_weights(y, "other")


# ----------------------------------------------------------------- hop dong xac suat

@pytest.mark.parametrize("bad", [np.array([[0.2, 0.3, 0.6]]), np.array([[0.5, 0.5, np.nan]]), np.array([[1.2, -0.2, 0.0]]), np.array([[0.5, 0.5]]), np.array([[np.inf, 0, 0]])])
def test_probability_contract_rejects_invalid(bad):
    with pytest.raises(C.ProbabilityError):
        C.validate_probabilities(bad, 1, tolerance=1e-5)


def test_probability_contract_accepts_valid_and_argmax_tie_is_lowest_index():
    p = C.validate_probabilities(np.array([[0.2, 0.5, 0.3], [1 / 3, 1 / 3, 1 / 3]]), 2, tolerance=1e-5)
    assert M.argmax_class(p).tolist() == [1, 0]


def test_classes_mismatch_is_an_error(cfg, data):
    frame, y, folds = data
    cand = C.sample_candidates(cfg)[0]
    x = np.random.default_rng(0).normal(size=(len(y), 3))
    with pytest.raises(C.ProbabilityError, match="classes_"):
        C.fit_estimator(cand, cfg, x[y != 2], y[y != 2])                      # chi 2 lop => classes_ = [0, 1]


def test_logistic_convergence_warning_makes_the_candidate_fail(cfg, data):
    frame, y, folds = data
    bad = small_config("cpu")
    bad["tuning"]["families"]["lr"]["fixed"]["max_iter"] = 1
    cand = C.sample_candidates(bad)[0]
    x = np.random.default_rng(1).normal(size=(len(y), 5)) * 50
    with pytest.raises(Exception, match="onverge"):
        C.fit_estimator(cand, bad, x, y)


# ----------------------------------------------------------------- thiet bi

def test_cuda_requested_without_gpu_fails_loudly(monkeypatch):
    cfg = small_config("cuda", fast=True)
    fake = types.SimpleNamespace(build_info=lambda: {"USE_CUDA": False}, XGBClassifier=lambda **kw: (_ for _ in ()).throw(AssertionError("khong duoc tao")))
    monkeypatch.setitem(sys.modules, "xgboost", fake)
    cand = next(c for c in C.sample_candidates(cfg) if c.family == "xgb")
    with pytest.raises(C.DeviceError, match="khong co CUDA"):
        C.build_estimator(cand, cfg)


def test_xgboost_parameters_are_mapped_from_config_and_device_is_passed(monkeypatch):
    cfg = small_config("cpu")
    seen = {}

    class FakeXGB:
        def __init__(self, **kw):
            seen.update(kw)

        def get_params(self):
            return dict(seen)

        def get_booster(self):
            dev = seen["device"]
            return types.SimpleNamespace(save_config=lambda: json.dumps({"learner": {"generic_param": {"device": dev}}}))

    monkeypatch.setitem(sys.modules, "xgboost", types.SimpleNamespace(build_info=lambda: {"USE_CUDA": True}, XGBClassifier=FakeXGB))
    cand = next(c for c in C.sample_candidates(cfg) if c.family == "xgb")
    est = C.build_estimator(cand, cfg)
    assert seen["objective"] == "multi:softprob" and seen["tree_method"] == "hist" and seen["device"] == "cpu" and seen["early_stopping_rounds"] is None
    assert seen["max_depth"] == cand.params["max_depth"] and seen["n_estimators"] == cand.params["n_estimators"] and seen["random_state"] == cfg["seed"]
    assert C.booster_device(est) == "cpu"
    cuda_cfg = small_config("cuda")
    seen.clear()
    C.build_estimator(cand, cuda_cfg)
    assert seen["device"] == "cuda"


def test_actual_device_mismatch_for_xgb_is_an_error(monkeypatch, cfg, data):
    frame, y, folds = data
    cuda_cfg = small_config("cuda")
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda params, fixed, *, seed, device, n_jobs: fake_xgb_factory(params, fixed, seed=seed, device="cpu", n_jobs=n_jobs))
    cand = next(c for c in C.sample_candidates(cuda_cfg) if c.family == "xgb")
    x = np.random.default_rng(2).normal(size=(len(y), 4))
    with pytest.raises(C.DeviceError, match="device that"):
        C.fit_estimator(cand, cuda_cfg, x, y)


# ----------------------------------------------------------------- CV: fold-local, ledger, ket qua

def test_cv_runs_all_candidates_and_pooled_score_is_the_sum_of_fold_confusions(cfg, data):
    frame, y, folds = data
    cands = C.sample_candidates(cfg)
    cv = S.run_cv(cands, frame, y, folds, SYNTH_COLUMNS, cfg)
    assert len(cv["candidates"]) == 22 and all(r["status"] == "ok" for r in cv["candidates"]) and not cv["budget_exhausted"] and cv["devices"] == ["cpu"]
    for r in cv["candidates"]:
        assert len(r["per_fold"]) == 3 and r["pooled"]["n_val"] == sum(f["n_val"] for f in r["per_fold"])
        cm = np.asarray(r["pooled"]["confusion"])
        assert np.isclose(M.macro_f1(cm), r["pooled"]["macro_f1"]) and cm.sum() == r["pooled"]["n_val"]
    assert describe_folds(frame, folds, 1)


def test_cv_preprocessing_and_weights_are_fit_on_train_fold_only(cfg, data, monkeypatch):
    frame, y, folds = data
    fit_rows, weight_lens = [], []
    real_make = S.make_matrix

    def spy_make(kind, columns):
        m = real_make(kind, columns)
        real_fit = m.fit
        m.fit = lambda fr: (fit_rows.append(set(fr.index.tolist())) or real_fit(fr))
        return m

    real_sw = C.sample_weights
    monkeypatch.setattr(S, "make_matrix", spy_make)
    monkeypatch.setattr(C, "sample_weights", lambda yy, mode: (weight_lens.append(len(yy)) or real_sw(yy, mode)))
    cands = [c for c in C.sample_candidates(cfg) if c.id in ("lr-00", "hgb-00")]
    S.run_cv(cands, frame, y, folds, SYNTH_COLUMNS, cfg)
    train_sets = [set(frame.index[tr].tolist()) for tr, _ in folds]
    val_sets = [set(frame.index[va].tolist()) for _, va in folds]
    assert len(fit_rows) == 2 * 3                                            # moi (kind, fold) mot lan (cache), khong fit tren toan bo
    for i, rows in enumerate(fit_rows):                                      # thu tu fit: (linear fold 0,1,2) roi (tree fold 0,1,2)
        k = i % 3
        assert rows == train_sets[k] and rows.isdisjoint(val_sets[k])
    assert sorted(set(weight_lens)) == sorted({len(tr) for tr, _ in folds})  # trong so chi tinh tren y cua fold-train


def test_failed_candidate_is_recorded_with_reason_and_others_continue(cfg, data, monkeypatch):
    frame, y, folds = data
    def broken(params, fixed, *, seed, device, n_jobs):
        raise RuntimeError("no fit")
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", broken)
    cv = S.run_cv(C.sample_candidates(cfg), frame, y, folds, SYNTH_COLUMNS, cfg)
    statuses = {fam: {r["status"] for r in cv["candidates"] if r["family"] == fam} for fam in ("lr", "hgb", "xgb")}
    assert statuses == {"lr": {"ok"}, "hgb": {"ok"}, "xgb": {"failed"}}
    assert all("RuntimeError: no fit" in r["reason"] and r["pooled"] is None and r["per_fold"] == [] for r in cv["candidates"] if r["family"] == "xgb")
    sel = S.select_finalists(cv, cfg)
    assert sel["families"]["xgb"]["status"] == "no_valid_model" and sel["families"]["xgb"]["finalist"] is None and len(sel["finalists"]) == 2


def test_probability_contract_violation_fails_the_candidate_not_the_run(cfg, data, monkeypatch):
    frame, y, folds = data
    class BadProba(HistGradientBoostingClassifier):
        def predict_proba(self, X):
            p = super().predict_proba(X)
            p[0, 0] = np.nan
            return p
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "hgb", lambda params, fixed, *, seed, device, n_jobs: BadProba(max_iter=10, early_stopping=False))
    cv = S.run_cv([c for c in C.sample_candidates(cfg) if c.family in ("lr", "hgb")][:8], frame, y, folds, SYNTH_COLUMNS, cfg)
    assert {r["status"] for r in cv["candidates"] if r["family"] == "hgb"} == {"failed"}
    assert any("ProbabilityError" in (r["reason"] or "") for r in cv["candidates"])
    assert {r["status"] for r in cv["candidates"] if r["family"] == "lr"} == {"ok"}


def test_soft_budget_aborts_remaining_candidates_and_family_is_incomplete_without_finalist(cfg, data):
    frame, y, folds = data
    ticks = iter(range(0, 10_000))
    cfg2 = small_config("cpu")
    cfg2["run"]["wall_clock_soft_cap_seconds"] = 25
    # dong ho gia: moi lan goi +1 giay => het ngan sach giua chung
    clock = lambda: float(next(ticks))                                           # noqa: E731
    cv = S.run_cv(C.sample_candidates(cfg2), frame, y, folds, SYNTH_COLUMNS, cfg2, clock=clock)
    assert cv["budget_exhausted"] is True
    assert {r["status"] for r in cv["candidates"]} >= {"ok", "aborted"} and all(r["pooled"] is None for r in cv["candidates"] if r["status"] == "aborted")
    sel = S.select_finalists(cv, cfg2)
    incomplete = [f for f, i in sel["families"].items() if i["status"] == "incomplete"]
    assert incomplete and all(sel["families"][f]["finalist"] is None for f in incomplete)
    assert all(cid.split("-")[0] not in incomplete for cid in sel["finalists"])


def test_family_complete_with_partial_failures_still_picks_best_ok_candidate(cfg):
    cv = {"candidates": [
        {"id": "lr-00", "family": "lr", "order": 0, "status": "failed", "reason": "x", "pooled": None},
        {"id": "lr-01", "family": "lr", "order": 1, "status": "ok", "pooled": {"macro_f1": 0.40, "mcc": 0.2}},
        {"id": "lr-02", "family": "lr", "order": 2, "status": "ok", "pooled": {"macro_f1": 0.45, "mcc": 0.1}},
        {"id": "hgb-00", "family": "hgb", "order": 3, "status": "ok", "pooled": {"macro_f1": 0.5, "mcc": 0.3}},
        {"id": "xgb-00", "family": "xgb", "order": 4, "status": "aborted", "reason": "budget", "pooled": None}]}
    sel = S.select_finalists(cv, cfg)
    assert sel["families"]["lr"] == {"scheduled": 3, "ok": 2, "failed": 1, "aborted": 0, "finalist": "lr-02", "status": "complete", "cv_pooled_macro_f1": 0.45}
    assert sel["families"]["xgb"]["status"] == "incomplete" and sel["finalists"] == ["lr-02", "hgb-00"]


def test_tie_rules_macro_f1_then_mcc_then_order(cfg):
    base = lambda i, f1, mcc: {"id": f"lr-{i:02d}", "family": "lr", "order": i, "status": "ok", "pooled": {"macro_f1": f1, "mcc": mcc}}      # noqa: E731
    cv = {"candidates": [base(0, 0.5, 0.20), base(1, 0.5 + 5e-10, 0.30), base(2, 0.5 + 5e-10, 0.30), base(3, 0.49, 0.9)]}
    assert S.select_finalists(cv, cfg)["families"]["lr"]["finalist"] == "lr-01"          # hoa trong 1e-9 -> MCC cao -> order nho
    cv2 = {"candidates": [base(0, 0.5, 0.20), base(1, 0.51, 0.10)]}
    assert S.select_finalists(cv2, cfg)["families"]["lr"]["finalist"] == "lr-01"         # chenh vuot tie_atol => macro-F1 thang


def test_champion_by_validation_macro_f1_ties_prefer_simpler_family(cfg):
    mk = lambda i, fam, f1, mcc: {"id": f"{fam}-0{i}", "family": fam, "order": i, "validation": {"macro_f1": f1, "mcc": mcc}}               # noqa: E731
    assert S.choose_champion([mk(0, "lr", 0.4, 0.1), mk(1, "hgb", 0.5, 0.1), mk(2, "xgb", 0.45, 0.4)], cfg)["id"] == "hgb-01"
    assert S.choose_champion([mk(1, "hgb", 0.5, 0.2), mk(2, "xgb", 0.5, 0.2)], cfg)["id"] == "hgb-01"                  # hoa het => ho don gian (lr < hgb < xgb)
    assert S.choose_champion([mk(1, "hgb", 0.5, 0.2), mk(2, "xgb", 0.5, 0.3)], cfg)["id"] == "xgb-02"                  # hoa macro-F1 => MCC cao hon
    assert S.choose_champion([], cfg) is None


def test_fit_on_train_then_predict_frames_use_train_statistics_and_valid_probabilities(cfg, data):
    frame, y, folds = data
    cand = next(c for c in C.sample_candidates(cfg) if c.id == "lr-00")
    train, val = frame.iloc[:450], frame.iloc[450:]
    fitted = S.fit_on_train(cand, train, y[:450], SYNTH_COLUMNS, cfg)
    p = S.predict_frame(fitted, val, cfg)
    assert p.shape == (250, 3) and np.allclose(p.sum(axis=1), 1) and fitted.device == "cpu"
    assert np.isclose(M.log_loss(y[450:], p), sk_log_loss(y[450:], p, labels=[0, 1, 2]))


def test_freeze_record_is_deterministic_and_sensitive(cfg, data):
    frame, y, folds = data
    cv = S.run_cv([c for c in C.sample_candidates(cfg) if c.id in ("lr-00", "hgb-00")], frame, y, folds, SYNTH_COLUMNS, cfg)
    sel = S.select_finalists(cv, cfg)
    scores = [{"id": "lr-00", "family": "lr", "order": 0, "validation": {"macro_f1": 0.4, "mcc": 0.1}}, {"id": "hgb-00", "family": "hgb", "order": 6, "validation": {"macro_f1": 0.5, "mcc": 0.2}}]
    champ = S.choose_champion(scores, cfg)
    allow = [{"model_id": "hgb-00", "role": "champion"}, {"model_id": "lr-00", "role": "finalist"}]
    a, b = S.freeze_record(cv, sel, scores, champ, cfg, allow), S.freeze_record(cv, sel, scores, champ, cfg, allow)
    assert a == b and len(a["freeze_sha256"]) == 64 and a["champion"] == "hgb-00"
    c = S.freeze_record(cv, sel, scores, scores[0], cfg, allow)
    assert c["freeze_sha256"] != a["freeze_sha256"]


# ----------------------------------------------------------------- probe thiet bi (tong hop)

def test_probe_device_skipped_for_cpu_and_fails_loudly_when_cuda_fit_fails(monkeypatch):
    assert C.probe_device(small_config("cpu")) == {"requested": "cpu", "probe": "skipped", "actual": "cpu"}
    cfg = small_config("cuda")

    class Boom:
        def __init__(self, **kw):
            pass

        def fit(self, x, y):
            raise RuntimeError("CUDA error: no CUDA-capable device is detected")

        def get_params(self):
            return {"device": "cuda"}

    monkeypatch.setitem(sys.modules, "xgboost", types.SimpleNamespace(build_info=lambda: {"USE_CUDA": True}, XGBClassifier=Boom))
    with pytest.raises(C.DeviceError, match="probe CUDA that bai"):
        C.probe_device(cfg)


def test_probe_device_succeeds_only_if_actual_device_is_cuda(monkeypatch):
    cfg = small_config("cuda")
    state = {"device": "cuda"}

    class Ok:
        def __init__(self, **kw):
            state["kw"] = kw

        def fit(self, x, y):
            assert x.shape == (60, 4) and set(y.tolist()) == {0, 1, 2}
            return self

        def get_params(self):
            return {"device": "cuda"}                                            # yeu cau cua wrapper (khong phai thiet bi that)

        def get_booster(self):
            return types.SimpleNamespace(save_config=lambda: json.dumps({"learner": {"generic_param": {"device": state["device"]}}}))

    monkeypatch.setitem(sys.modules, "xgboost", types.SimpleNamespace(build_info=lambda: {"USE_CUDA": True}, XGBClassifier=Ok))
    assert C.probe_device(cfg) == {"requested": "cuda", "probe": "synthetic_fit_ok", "actual": "cuda", "normalised": "cuda", "source": "booster.save_config"}
    assert state["kw"]["device"] == "cuda" and state["kw"]["n_estimators"] == 2
    state["device"] = "cpu"
    with pytest.raises(C.DeviceError, match="device that"):
        C.probe_device(cfg)
