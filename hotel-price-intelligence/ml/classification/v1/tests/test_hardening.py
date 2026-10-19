"""Phan vi du cua GPT 12 (M1-M6) + hop dong bao cao: han chot dung chung, ledger, TestGate/freeze gia mao, run_id/duong dan, self-test thu vien.

Toan bo tren du lieu TONG HOP; khong fit du lieu that; moi ham xoa de quy bi gan bay (trap) o noi lien quan.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import types

import numpy as np
import pandas as pd
import pytest

from cls_training import candidates as C
from cls_training import drive as D
from cls_training import runner as R
from cls_training import selection as S
from cls_training import selftest as ST
from cls_training import transaction as T
from cls_training.data import dataset_sufficiency, with_test_dates

from synth import SYNTH_COLUMNS, make_frame, make_loaded, runner_config, small_config
from test_candidates_selection import fake_xgb_factory
from test_runner import Spy


@pytest.fixture(autouse=True)
def _fake_xgb(monkeypatch):
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)


@pytest.fixture()
def rmtree_trap(monkeypatch):
    called = []
    monkeypatch.setattr(T.shutil, "rmtree", lambda *a, **k: called.append(a))
    monkeypatch.setattr(D.shutil, "rmtree", lambda *a, **k: called.append(a))
    return called


# ----------------------------------------------------------------- M4: han chot dung chung + ledger

def test_finalist_refits_do_not_start_after_the_shared_deadline_is_passed_by_cv(monkeypatch):
    now = {"t": 0.0}
    real_cv = R.run_cv

    def cv_that_burns_the_budget(*a, **k):
        out = real_cv(*a, **k)
        now["t"] = 2.0                                           # CV da dung het ngan sach (cap 1 giay): moi fit moi sau do la vi pham
        return out

    fits = []
    monkeypatch.setattr(R, "run_cv", cv_that_burns_the_budget)
    monkeypatch.setattr(R, "fit_on_train", lambda *a, **k: fits.append(1) or (_ for _ in ()).throw(AssertionError("khong duoc fit sau han")))
    cfg = runner_config()
    cfg["run"]["wall_clock_soft_cap_seconds"] = 1
    result = R.run_horizon(cfg, make_loaded(), clock=lambda: now["t"])
    rep = result.report
    assert fits == [] and rep["status"] == "no_valid_model" and rep["contract_complete"] is False
    assert all(why.startswith("not_run: wall_clock_soft_cap_exceeded") for why in rep["finalist_failures"].values()) and len(rep["finalist_failures"]) == 3
    assert rep["ledger"]["finalist_refits"] == {"scheduled": 3, "completed": 0, "failed": 0, "skipped": 3, "seconds": {}} and result.predictions is None


def test_overrun_by_the_last_fit_makes_the_horizon_incomplete_even_if_everything_ran(monkeypatch):
    now = {"t": 0.0}
    real_fit = R.fit_on_train
    calls = {"n": 0}

    def fit(*a, **k):
        calls["n"] += 1
        now["t"] += 500.0 if calls["n"] <= 3 else 1000.0         # 3 finalist x500, A1 +1000 (=2500), A2 bat dau 2500 < cap 3000 va ket thuc 3500 > cap
        return real_fit(*a, **k)

    monkeypatch.setattr(R, "fit_on_train", fit)
    cfg = runner_config()
    cfg["run"]["wall_clock_soft_cap_seconds"] = 3000
    rep = R.run_horizon(cfg, make_loaded(), clock=lambda: now["t"]).report
    assert rep["ablation"]["a1"]["status"] == rep["ablation"]["a2"]["status"] == "ok"            # fit dang chay duoc phep vuot
    assert rep["contract_complete"] is False and rep["status"] == "incomplete"
    assert any(r.startswith("wall_clock_soft_cap_exceeded") for r in rep["incomplete_reasons"])
    assert rep["ledger"]["elapsed_seconds"] == 3500.0 and rep["ledger"]["soft_cap_seconds"] == 3000


def test_ledger_counts_actual_attempts_devices_and_timings():
    result = R.run_horizon(runner_config(), make_loaded())
    ledger = result.report["ledger"]
    n_folds = result.report["cv"]["n_folds"]
    assert ledger["cv"] == {"scheduled": 22 * n_folds, "completed": 22 * n_folds, "failed": 0, "skipped": 0}
    assert ledger["finalist_refits"]["scheduled"] == ledger["finalist_refits"]["completed"] == 3 and set(ledger["finalist_refits"]["seconds"]) == set(result.report["selection"]["finalists"])
    assert ledger["ablation_fits"]["scheduled"] == ledger["ablation_fits"]["completed"] == 2 and len(ledger["ablation_fits"]["seconds"]) == 2
    assert ledger["devices"] == ["cpu"] and ledger["elapsed_seconds"] >= 0 and ledger["probe"] is None


def test_failed_candidate_keeps_the_per_fold_evidence_already_produced(monkeypatch):
    cfg = small_config("cpu")
    frame, y = make_frame(700, seed=2)
    from cls_training.data import make_folds
    folds = make_folds(frame, 1, {"min_train_days": 8, "n_splits": {"1": 3}})
    real = C.fit_estimator
    state = {"n": 0}

    def flaky(cand, cfg_, x, yy):
        if cand.id == "lr-00":
            state["n"] += 1
            if state["n"] == 3:
                raise RuntimeError("dut o fold cuoi")
        return real(cand, cfg_, x, yy)

    monkeypatch.setattr(S, "fit_estimator", flaky)
    cv = S.run_cv([c for c in C.sample_candidates(cfg) if c.id in ("lr-00", "lr-01")], frame, y, folds, SYNTH_COLUMNS, cfg)
    bad = next(r for r in cv["candidates"] if r["id"] == "lr-00")
    assert bad["status"] == "failed" and bad["failed_fold"] == 2 and [f["fold"] for f in bad["per_fold"]] == [0, 1] and bad["pooled"] is None
    assert cv["attempts"] == {"scheduled": 6, "completed": 5, "failed": 1, "skipped": 0}


# ----------------------------------------------------------------- M5: TestGate / freeze

def _freeze_fixture(rep):
    return copy.deepcopy(rep["freeze"])


def _rehash(freeze):
    body = {k: v for k, v in freeze.items() if k != "freeze_sha256"}
    return {**body, "freeze_sha256": S.canonical_sha256(body)}


@pytest.fixture(scope="module")
def good_run():
    mp = pytest.MonkeyPatch()
    mp.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)
    cfg, loaded = runner_config(), make_loaded()
    result = R.run_horizon(cfg, loaded)
    yield cfg, loaded, result
    mp.undo()


def test_valid_freeze_passes_and_returns_roles(good_run):
    cfg, loaded, result = good_run
    roles = S.verify_freeze(result.report["freeze"], cfg)
    assert sorted(roles.values()).count("champion") == 1 and len(roles) == 5


@pytest.mark.parametrize("mutate,match", [
    (lambda f: f.__setitem__("champion", "lr-00"), "freeze_sha256"),                                   # sua than, giu hash cu
    (lambda f: f.__setitem__("freeze_sha256", "x" * 64), "freeze_sha256"),                               # hash gia
    (lambda f: f.__setitem__("config_sha256", "0" * 64), "freeze_sha256"),
])
def test_tampered_freeze_body_or_hash_is_rejected(good_run, mutate, match):
    cfg, loaded, result = good_run
    f = _freeze_fixture(result.report)
    mutate(f)
    with pytest.raises(S.FreezeError, match=match):
        S.verify_freeze(f, cfg)


def test_structurally_invalid_but_correctly_hashed_freezes_are_rejected(good_run):
    cfg, loaded, result = good_run
    base = _freeze_fixture(result.report)

    def variant(fn):
        f = copy.deepcopy(base)
        fn(f)
        return _rehash(f)

    champ = base["champion"]
    cases = {
        "config khac": variant(lambda f: f.__setitem__("config_sha256", "0" * 64)),
        "champion doi": variant(lambda f: f.__setitem__("champion", next(i for i in f["finalists"] if i != champ))),
        "vai tro la": variant(lambda f: f["test_allowlist"][0].__setitem__("role", "hacker")),
        "model_id trung": variant(lambda f: f["test_allowlist"].append(dict(f["test_allowlist"][0]))),
        "allowlist rong": variant(lambda f: f.__setitem__("test_allowlist", [])),
        "ablation khong cua champion": variant(lambda f: f["test_allowlist"][-1].__setitem__("model_id", "xgb-99+a2_no_history")),
        "finalist ngoai allowlist": variant(lambda f: f["finalists"].append("lr-99")),
        "hai champion": variant(lambda f: f["test_allowlist"][1].__setitem__("role", "champion")),
        "qua 5 muc": variant(lambda f: f["test_allowlist"].extend({"model_id": f"extra-{i}", "role": "finalist"} for i in range(2))),
    }
    for name, forged in cases.items():
        with pytest.raises(S.FreezeError):
            S.verify_freeze(forged, cfg)
    with pytest.raises(S.FreezeError, match="mapping"):
        S.verify_freeze("not a dict", cfg)


def test_forged_freeze_never_touches_the_test_frame_and_gate_opens_once(good_run, monkeypatch):
    cfg, loaded, result = good_run
    order: list[str] = []
    frame = Spy(make_loaded().frames["test"], order)
    events: list[dict] = []
    gate = R.TestGate(frame, events, lambda: 0.0, 0.0, cfg)
    forged = _freeze_fixture(result.report)
    forged["champion"] = "lr-00"
    with pytest.raises(S.FreezeError):
        gate.open(forged)
    assert order == [] and events == []                                                       # khong mo, khong cham khung test
    with pytest.raises(R.RunnerError, match="chua mo"):
        gate.authorize("lr-00", "champion")
    valid = _freeze_fixture(result.report)
    gate.open(valid)
    with pytest.raises(R.RunnerError, match="da duoc mo"):
        gate.open(valid)                                                                       # khong mo lai
    champ = valid["champion"]
    gate.authorize(champ, "champion")                                                          # dung vai tro
    with pytest.raises(R.RunnerError, match="khong thuoc allowlist"):
        gate.authorize(champ, "finalist")                                                      # sai vai tro
    with pytest.raises(R.RunnerError, match="khong thuoc allowlist"):
        gate.authorize("stray-99", "finalist")                                                 # model la
    with pytest.raises(R.RunnerError, match="CHINH ban ghi"):
        gate.open_unseen({**valid, "freeze_sha256": "0" * 64})                                 # unseen phai dung CHINH ban ghi khoa


def test_unseen_cannot_open_before_primary_test_and_only_once(good_run):
    cfg, loaded, result = good_run
    ld = make_loaded()
    gate = R.TestGate(ld.frames["test"], [], lambda: 0.0, 0.0, cfg, ld.unseen_frames["test"])
    freeze = _freeze_fixture(result.report)
    with pytest.raises(R.RunnerError, match="chi mo sau"):
        gate.open_unseen(freeze)
    gate.open(freeze)
    gate.open_unseen(freeze)
    with pytest.raises(R.RunnerError, match="da duoc mo"):
        gate.open_unseen(freeze)


def test_runner_refuses_to_open_test_when_a_fitted_model_is_outside_the_frozen_allowlist(monkeypatch):
    order: list[str] = []
    ld = make_loaded()
    ld.frames["test"] = Spy(ld.frames["test"], order)
    real = S.freeze_record

    def narrow(cv, selection, scores, champion, cfg, allowlist):
        # allowlist hop le cau truc nhung thieu 1 finalist (va finalists tuong ung): 'model da fit nam ngoai allowlist'
        drop = next(a["model_id"] for a in allowlist if a["role"] == "finalist")
        keep = [a for a in allowlist if a["model_id"] != drop]
        sel = {**selection, "finalists": [m for m in selection["finalists"] if m != drop]}
        return real(cv, sel, [r for r in scores if r["id"] != drop], champion, cfg, keep)

    monkeypatch.setattr(R, "freeze_record", narrow)
    with pytest.raises(R.RunnerError, match="ngoai allowlist"):
        R.run_horizon(runner_config(), ld)
    assert order == []                                                                         # loi TRUOC khi cham khung test


# ----------------------------------------------------------------- M6: duong dan / xoa / run_id

@pytest.mark.parametrize("bad", ["r/../../victim", "..", "a/b", "a\\b", "C:evil", "", " x", "x" * 65, ".hidden", "a..b", "run/../x", "\\\\server\\share"])
def test_unsafe_run_ids_are_rejected_before_any_filesystem_effect(tmp_path, bad, rmtree_trap):
    root = tmp_path / "root"
    with pytest.raises(T.TransactionError, match="run_id"):
        T.RunTransaction(root, bad)
    assert not root.exists() or list(root.iterdir()) == []
    assert not (tmp_path / "victim").exists() and rmtree_trap == []


def test_safe_names_and_manifest_traversal_entries(tmp_path):
    assert T.safe_file_name("h1_bundle_xgb-03+a1_ratio.joblib") == "h1_bundle_xgb-03+a1_ratio.joblib"
    for bad in ("../x", "/abs", "a/b", "..", "a\\b", "C:x", ""):
        with pytest.raises(T.TransactionError):
            T.safe_file_name(bad)
    tx = T.RunTransaction(tmp_path, "ok")
    tx.write_text("a.txt", "x")
    with pytest.raises(T.TransactionError):
        tx.write_text("../escape.txt", "x")
    run = tx.commit({"k": 1})
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["../escape.txt"] = manifest["files"]["a.txt"]
    (run / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(T.TransactionError):
        T.verify_run_dir(run)                                                                  # sha COMPLETE khac manifest sua => khong hop le (khong doc file ngoai root)
    assert not (tmp_path.parent / "escape.txt").exists()


def test_preexisting_copying_dir_is_an_error_and_never_removed(tmp_path, rmtree_trap):
    src_tx = T.RunTransaction(tmp_path / "src", "x")
    src_tx.write_text("a.txt", "hello")
    src = src_tx.commit({"k": 1}, extra={"status": "complete"})
    (tmp_path / "drive").mkdir()
    (tmp_path / "drive" / ".copying_run_x").mkdir()
    (tmp_path / "drive" / ".copying_run_x" / "keep.txt").write_text("other process", encoding="utf-8")
    with pytest.raises(T.TransactionError, match="da ton tai"):
        D.copy_run_verified(src, tmp_path / "drive")
    assert (tmp_path / "drive" / ".copying_run_x" / "keep.txt").is_file() and rmtree_trap == []


def test_junction_root_is_rejected_on_windows(tmp_path):
    if sys.platform != "win32":
        pytest.skip("junction chi co tren Windows")
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"khong tao duoc junction: {r.stderr.strip()}")
    with pytest.raises(T.TransactionError, match="reparse"):
        T.RunTransaction(link, "x")
    assert list(target.iterdir()) == []


def test_failure_after_staging_removes_only_own_staging(tmp_path, monkeypatch):
    removed = []
    real = T.shutil.rmtree
    monkeypatch.setattr(T.shutil, "rmtree", lambda p, *a, **k: (removed.append(str(p)), real(p, *a, **k)))
    other = tmp_path / ".staging_other"
    other.mkdir()
    tx = T.RunTransaction(tmp_path, "mine")
    tx.abort()
    assert removed == [str(tx.staging)] and other.is_dir()


# ----------------------------------------------------------------- M1 + self-test thu vien

def _fake_xgboost_module(monkeypatch, device="cpu"):
    mod = types.SimpleNamespace(__version__="9.9-fake", build_info=lambda: {"USE_CUDA": device == "cuda"})
    monkeypatch.setitem(sys.modules, "xgboost", mod)
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda params, fixed, *, seed, device, n_jobs: fake_xgb_factory(params, fixed, seed=seed, device=device, n_jobs=n_jobs))


def test_selftest_runs_two_xgb_candidates_with_both_weights_and_reports_device(monkeypatch):
    _fake_xgboost_module(monkeypatch)
    out = ST.run_selftest(small_config("cpu"))
    assert out["status"] == "ok" and out["xgboost_version"] == "9.9-fake" and {f["weight"] for f in out["fits"]} == {"none", "balanced"}
    assert all(f["device"]["normalised"] == "cpu" and f["device"]["source"] == "booster.save_config" for f in out["fits"])


def test_selftest_fails_when_booster_device_differs_or_xgboost_missing(monkeypatch):
    _fake_xgboost_module(monkeypatch, "cuda")
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda params, fixed, *, seed, device, n_jobs: fake_xgb_factory(params, fixed, seed=seed, device="cpu", n_jobs=n_jobs))
    with pytest.raises(ST.SelfTestError, match="device that"):
        ST.run_selftest(small_config("cuda"))                                                  # yeu cau cuda, Booster bao cpu
    monkeypatch.setitem(sys.modules, "xgboost", None)
    with pytest.raises(ST.SelfTestError, match="khong import duoc"):
        ST.run_selftest(small_config("cpu"))


@pytest.mark.parametrize("config_json,ok", [
    ('{"learner": {"generic_param": {"device": "cuda:0"}}}', True),
    ('{"learner": {"generic_param": {"device": "cuda"}}}', True),
    ('{"learner": {"generic_param": {"device": "cpu"}}}', False),
    ('{"learner": {}}', False),
    ("not json", False),
    ('{"learner": {"generic_param": {"device": ""}}}', False),
])
def test_booster_device_is_read_from_save_config_and_normalised(config_json, ok):
    est = types.SimpleNamespace(get_booster=lambda: types.SimpleNamespace(save_config=lambda: config_json), get_params=lambda: {"device": "cuda"})
    cand = C.Candidate("xgb-00", "xgb", 0, {})
    if ok:
        info = C.verify_device(cand, est, "cuda")
        assert info["normalised"] == "cuda" and info["requested"] == "cuda"
    else:
        with pytest.raises(C.DeviceError):
            C.verify_device(cand, est, "cuda")


def test_wrapper_requests_cuda_but_booster_says_cpu_is_rejected():
    est = types.SimpleNamespace(get_booster=lambda: types.SimpleNamespace(save_config=lambda: '{"learner":{"generic_param":{"device":"cpu"}}}'), get_params=lambda: {"device": "cuda"})
    with pytest.raises(C.DeviceError, match="khac yeu cau"):
        C.verify_device(C.Candidate("xgb-00", "xgb", 0, {}), est, "cuda")


# ----------------------------------------------------------------- bao cao: sufficiency / identity / strata

def test_report_carries_sufficiency_short_window_and_test_dates_only_after_open(good_run):
    cfg, loaded, result = good_run
    suff = result.report["data_sufficiency"]
    assert suff["report_present"] is False and suff["eligible_prediction_dates"]["test"] == int(loaded.frames["test"]["vn_observation_date"].nunique())
    assert suff["short_window"] is (suff["eligible_prediction_dates"]["validation"] < 7 or suff["eligible_prediction_dates"]["test"] < 7)
    pre = dataset_sufficiency(loaded, {"train": loaded.frames["train"], "validation": loaded.frames["validation"]})
    assert "test" not in pre["eligible_prediction_dates"] and pre["short_window"] is None
    assert with_test_dates(pre, 3)["short_window"] is True


def test_synthetic_identity_is_explicit_and_real_pin_mismatch_is_rejected():
    cfg, loaded = runner_config(), make_loaded()
    ident = R.run_identity(cfg, loaded)
    assert ident["synthetic"] is True and ident["output_checksums_sha256"] is None and ident["dataset_version"] == "ds_synthetic"
    real = make_loaded()
    real.pin = {**cfg["dataset_pins"][0], "output_checksums_sha256": "0" * 64}
    real.horizon = 1
    with pytest.raises(R.RunnerError, match="khac cau hinh"):
        R.run_identity(cfg, real)


def test_predictions_include_canonical_series_id_and_strata(good_run):
    cfg, loaded, result = good_run
    for col in ("canonical_series_id", "hotel_id", "checkin_date", "vn_observation_date", "inference_mode", "price_velocity"):
        assert col in result.predictions.columns


# ----------------------------------------------------------------- moi truong ghim + van tay tap ung vien

def test_shipped_config_candidate_fingerprint_matches_and_detects_drift():
    from cls_training.config import load_config
    cfg = load_config()
    assert C.verify_candidate_set(cfg) == cfg["tuning"]["candidate_set_sha256"]
    drift = copy.deepcopy(cfg)
    drift["tuning"]["sampler_seed"] += 1                                                    # ParameterSampler/phien ban doi => tap khac => dung som
    with pytest.raises(C.CandidateError, match="van tay"):
        C.verify_candidate_set(drift)
    small = runner_config()
    small["tuning"]["sampler_seed"] += 1                                                    # van tay trong config giu nguyen => khong khop
    with pytest.raises(C.CandidateError, match="van tay"):
        R.run_horizon(small, make_loaded())                                                  # runner cung chan truoc moi fit


def test_selftest_rejects_a_candidate_set_that_differs_from_the_pinned_fingerprint(monkeypatch):
    _fake_xgboost_module(monkeypatch)
    cfg = small_config("cpu")
    cfg["tuning"]["candidate_set_sha256"] = "0" * 64
    with pytest.raises(ST.SelfTestError, match="van tay"):
        ST.run_selftest(cfg)


def test_environment_check_is_exact_and_reports_pip_line(monkeypatch):
    from cls_training import envcheck as E
    from cls_training.config import load_config
    cfg = load_config()
    pins = cfg["environment"]["expected_versions"]
    monkeypatch.setattr(E.md, "version", lambda pkg: pins.get(pkg, "0.0"))
    ok = E.check_environment(cfg)
    assert ok["status"] == "ok" and ok["mismatch"] == {} and set(ok["expected"]) == {"scikit-learn", "xgboost", "numpy", "pandas"} and E.require_environment(cfg)["status"] == "ok"
    monkeypatch.setattr(E.md, "version", lambda pkg: "9.9.9" if pkg == "pandas" else pins.get(pkg, "0.0"))
    bad = E.check_environment(cfg)
    assert bad["status"] == "mismatch" and set(bad["mismatch"]) == {"pandas"} and f"pandas=={pins['pandas']}" in bad["pip_install"]
    with pytest.raises(E.EnvironmentMismatch, match="pip install"):
        E.require_environment(cfg)

    def missing(pkg):
        raise E.md.PackageNotFoundError(pkg)

    monkeypatch.setattr(E.md, "version", missing)
    assert E.check_environment(cfg)["mismatch"]["xgboost"]["actual"] is None


def test_config_validation_requires_environment_pins_and_fingerprint():
    from cls_training.config import ConfigError, load_config, validate_config
    cfg = load_config()
    for mutate in (lambda c: c.pop("environment"), lambda c: c["environment"]["expected_versions"].pop("xgboost"), lambda c: c["tuning"].pop("candidate_set_sha256"),
                   lambda c: c["tuning"].__setitem__("candidate_set_sha256", "xyz")):
        bad = copy.deepcopy(cfg)
        mutate(bad)
        with pytest.raises(ConfigError):
            validate_config(bad)


# ----------------------------------------------------------------- M1 (GPT 14): refit finalist loi/bo qua khong lam freeze no

def _count_fits(monkeypatch, behaviour):
    state = {"calls": 0}
    real = R.fit_on_train

    def fit(cand, *a, **k):
        state["calls"] += 1
        return behaviour(state, real, cand, *a, **k)

    monkeypatch.setattr(R, "fit_on_train", fit)
    return state


def test_one_finalist_refit_failure_still_freezes_the_evaluated_finalists_and_reports_incomplete(monkeypatch):
    def lr_breaks(state, real, cand, *a, **k):
        if cand.family == "lr":
            raise RuntimeError("refit LR hong")
        return real(cand, *a, **k)

    _count_fits(monkeypatch, lr_breaks)
    order: list[str] = []
    ld = make_loaded()
    ld.frames["test"] = Spy(ld.frames["test"], order)
    cfg = runner_config()
    result = R.run_horizon(cfg, ld)
    rep = result.report
    assert rep["status"] == "incomplete" and rep["contract_complete"] is False and rep["champion"] is not None and not rep["champion"].startswith("lr")
    assert list(rep["finalist_failures"]) == [next(f for f in rep["selection"]["finalists"] if f.startswith("lr"))] and "RuntimeError" in next(iter(rep["finalist_failures"].values()))
    assert any(r.startswith("finalist lr") for r in rep["incomplete_reasons"]) and rep["ledger"]["finalist_refits"]["failed"] == 1 and rep["ledger"]["finalist_refits"]["completed"] == 2
    freeze = rep["freeze"]
    assert len(freeze["cv_selected_finalists"]) == 3 and len(freeze["finalists"]) == 2 and set(freeze["finalists"]) < set(freeze["cv_selected_finalists"])
    assert not any(a["model_id"].startswith("lr") for a in freeze["test_allowlist"]) and len(freeze["test_allowlist"]) == 4         # 2 finalist + A1 + A2
    assert S.verify_freeze(freeze, cfg) and order and rep["test"]["opened_after_freeze"] is True
    assert set(rep["test"]["models"]) == set(rep["models"]) and len(rep["models"]) == 4 and result.predictions["model_id"].nunique() == 4


@pytest.mark.parametrize("cap,n_refits", [(500, 1), (1500, 2)])
def test_budget_exhausted_after_some_refits_keeps_evaluated_finalists_and_audit(monkeypatch, cap, n_refits):
    now = {"t": 0.0}

    def slow(state, real, cand, *a, **k):
        now["t"] += 1000.0
        return real(cand, *a, **k)

    _count_fits(monkeypatch, slow)
    cfg = runner_config()
    cfg["run"]["wall_clock_soft_cap_seconds"] = cap
    rep = R.run_horizon(cfg, make_loaded(), clock=lambda: now["t"]).report
    assert rep["status"] == "incomplete" and rep["contract_complete"] is False
    skipped = [cid for cid, why in rep["finalist_failures"].items() if why.startswith("not_run")]
    assert len(skipped) == 3 - n_refits and len(rep["freeze"]["finalists"]) == n_refits and len(rep["freeze"]["cv_selected_finalists"]) == 3
    assert rep["ledger"]["finalist_refits"]["skipped"] == 3 - n_refits and rep["ablation"]["a1"]["status"] == rep["ablation"]["a2"]["status"] == "not_run"
    assert rep["test"]["opened_after_freeze"] is True and len(rep["models"]) == n_refits


def test_all_finalist_refits_failing_is_no_valid_model_and_never_opens_test(monkeypatch):
    def always(state, real, cand, *a, **k):
        raise RuntimeError("refit hong")

    _count_fits(monkeypatch, always)
    order: list[str] = []
    ld = make_loaded()
    ld.frames["test"] = Spy(ld.frames["test"], order)
    result = R.run_horizon(runner_config(), ld)
    assert result.report["status"] == "no_valid_model" and result.report["freeze"] is None and order == [] and result.predictions is None
    assert result.report["ledger"]["finalist_refits"]["failed"] == 3 and len(result.report["finalist_failures"]) == 3


def test_freeze_finalists_must_be_a_subset_of_cv_selected(good_run):
    cfg, loaded, result = good_run
    f = copy.deepcopy(result.report["freeze"])
    f["cv_selected_finalists"] = f["cv_selected_finalists"][:1]
    body = {k: v for k, v in f.items() if k != "freeze_sha256"}
    forged = {**body, "freeze_sha256": S.canonical_sha256(body)}
    with pytest.raises(S.FreezeError, match="cv_selected_finalists"):
        S.verify_freeze(forged, cfg)
    del body["cv_selected_finalists"]
    with pytest.raises(S.FreezeError, match="cv_selected_finalists"):
        S.verify_freeze({**body, "freeze_sha256": S.canonical_sha256(body)}, cfg)


# ----------------------------------------------------------------- smoke tong hop (GPT 14 §5: moi truong sach, truoc khi cham dataset that)

def test_smoke_runs_the_whole_training_path_on_the_synthetic_fixture(monkeypatch):
    from cls_training.config import load_config, override_device
    _fake_xgboost_module(monkeypatch)
    out = ST.run_smoke(override_device(load_config(), "cpu"))
    assert out["status"] == "ok" and out["synthetic"] is True and out["n_models"] == 5 and out["devices"] == ["cpu"] and out["champion"]
    assert {"h1_report.json", "h1_predictions.parquet", "CODE_MANIFEST.json"} <= set(out["files"]) and any(f.startswith("h1_bundle_") for f in out["files"])


def test_smoke_fails_closed_when_a_family_cannot_run(monkeypatch):
    from cls_training.config import load_config, override_device
    _fake_xgboost_module(monkeypatch)
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("xgb hong")))
    with pytest.raises(ST.SelfTestError, match="khong hoan tat dung hop dong"):
        ST.run_smoke(override_device(load_config(), "cpu"))


def test_smoke_config_keeps_device_quota_and_recomputes_identity():
    from cls_training.config import canonical_sha256, load_config
    from cls_training.synthetic import smoke_config
    base = load_config()
    small = smoke_config(base)
    assert small["run"] == base["run"] and small["tuning"]["families"]["xgb"]["n_candidates"] == 8 and small["ablation"]["a2_n_features"] == 6
    assert small["config_sha256"] == canonical_sha256({k: v for k, v in small.items() if k != "config_sha256"}) and small["config_sha256"] != base["config_sha256"]
    assert C.verify_candidate_set(small) == small["tuning"]["candidate_set_sha256"] != base["tuning"]["candidate_set_sha256"]
