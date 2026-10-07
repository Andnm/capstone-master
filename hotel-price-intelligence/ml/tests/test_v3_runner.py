"""Test dieu phoi train-v3 voi du lieu tong hop (hop dong C1-C18): cong khoa TEST, champion A/B, persistence, routing, ngan sach, loi candidate, xgboost gia, null, bundle, bat bien v2."""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.pipeline import Pipeline

import training
from training import v3_runner
from training.config import load_config
from training.encoder import TreeEncoder
from training.v3_contract import PERSISTENCE, feature_list_sha256, load_config_v3
from training.v3_runner import TestGate, run_from_frames
from training.v3_runtime import FitLedger
from v3_fixtures import FEATURES, install_fake_xgboost, small_cfg, synthetic_frames


@pytest.fixture(autouse=True)
def xgboost_absent_by_default(monkeypatch):
    """Mac dinh gia lap MAY KHONG CO xgboost (ket qua khong phu thuoc moi truong); test xgboost goi `install_fake_xgboost` de ghi de."""
    monkeypatch.setitem(__import__("sys").modules, "xgboost", None)


def _run(cfg=None, frames=None, out_dir=None, **kwargs):
    cfg = cfg or small_cfg()
    frames = frames or synthetic_frames()
    kwargs.setdefault("device_request", "cpu")
    return run_from_frames(frames, FEATURES, 1, cfg, out_dir=out_dir, **kwargs)


def test_end_to_end_structure_ledger_and_test_list(tmp_path):
    report = _run(out_dir=tmp_path)
    assert report["status"] == "ok" and report["claim_level"] == "dev_research_exploratory" and report["training_version"] == "training-1.3.0"
    assert report["features"]["sha256"] == feature_list_sha256(FEATURES) and len(report["folds"]) == 2
    family = report["families"]["hgb_l1"]
    assert family["complete"] and len(family["candidates"]) == 3 and all(len(c["per_fold"]) == 2 and c["pooled"] for c in family["candidates"])
    for c in family["candidates"]:                       # cung mot lan fit cho CA HAI scorer
        assert {"mae_vnd", "mae_log", "lift_vnd", "lift_log"} <= set(c["per_fold"][0]) and {"lift_vnd", "lift_log", "mae_vnd", "mae_log"} <= set(c["pooled"])
    cv_fits = sum(e["fits"] for e in report["ledger"]["entries"] if e["kind"] in ("pilot", "cv"))
    assert cv_fits == 3 * 2 and report["ledger"]["nominal"]["cv_fits"] == 6 and report["ledger"]["total_fits"] >= cv_fits
    assert {e["kind"] for e in report["ledger"]["entries"]} >= {"pilot", "cv", "refit", "control", "ablation", "null", "smoke"}
    pre = report["test"]["prespecified_models"]
    assert pre[0] == PERSISTENCE and "hgb_l2" in pre and "rf_l2" in pre and "ridge" not in pre and "xgb_l2" not in pre            # Ridge chi khi la champion; xgb khong co
    assert sorted(report["test"]["predicted_models"]) == sorted(n for n in pre if n != PERSISTENCE and not n.startswith("routed:"))
    assert report["contract_complete"] is False and report["controls"]["xgb_l2"]["status"] == "skipped" and report["controls"]["xgb_l2"]["reason"] == "xgboost_not_installed"
    assert (tmp_path / "h1_report.json").is_file() and (tmp_path / "h1_predictions_v3.parquet").is_file()
    json.loads((tmp_path / "h1_report.json").read_text(encoding="utf-8"), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))   # khong NaN/Infinity
    pred = pd.read_parquet(tmp_path / "h1_predictions_v3.parquet")
    assert set(pred["split"]) == {"validation", "test"} and "pred_persistence" in pred.columns and "pred_hgb_l2" in pred.columns
    test_rows = pred[pred["split"] == "test"]
    assert test_rows["pred_hgb_l2"].notna().all() and ("pred_ridge" not in pred.columns or test_rows["pred_ridge"].isna().all())      # Ridge khong duoc predict TEST khi khong la champion


def test_test_matrix_is_encoded_and_predicted_only_after_the_lock(monkeypatch):
    gate = TestGate()
    frames = synthetic_frames(n_hotels=30, train_days=34, val_days=6, test_days=5)
    n_test, n_val, n_train = (len(frames[k]) for k in ("test", "validation", "train"))
    assert len({n_test, n_val, n_train}) == 3
    calls: list[tuple[str, int, bool]] = []
    for cls in (HistGradientBoostingRegressor, RandomForestRegressor, Pipeline):
        original = cls.predict
        monkeypatch.setattr(cls, "predict", (lambda orig, name: lambda self, X, *a, **k: (calls.append((name, len(X), gate.locked)), orig(self, X, *a, **k))[1])(original, cls.__name__))
    original_transform = TreeEncoder.transform
    monkeypatch.setattr(TreeEncoder, "transform", lambda self, frame: (calls.append(("transform", len(frame), gate.locked)), original_transform(self, frame))[1])
    report = _run(frames=frames, gate=gate)
    test_calls = [c for c in calls if c[1] == n_test]
    assert test_calls and all(locked for _, _, locked in test_calls), test_calls                  # moi transform/predict tren TEST deu SAU khoa
    assert not any(c[1] == n_test and not c[2] for c in calls)
    assert gate.locked and set(gate.predicted) <= set(report["test"]["prespecified_models"])
    names = [c[0] for c in test_calls]
    assert names.count("transform") >= 1 and ("HistGradientBoostingRegressor" in names)


def test_gate_refuses_a_test_predict_for_a_model_outside_the_prespecified_list():
    gate = TestGate()
    gate.lock(["a"])
    with pytest.raises(v3_runner.TestAccessError):
        gate.predict("hgb_l1-07", lambda: np.zeros(1))


def test_champion_is_persistence_when_nothing_changes_and_no_null_or_routing(tmp_path):
    frames = synthetic_frames(all_unchanged=True)
    report = _run(frames=frames, out_dir=tmp_path)
    assert report["champions"]["A"]["winner"] == PERSISTENCE and report["champions"]["B"]["winner"] == PERSISTENCE
    assert report["null_test"]["status"] == "not_applicable" and report["routing"]["policy_by_model"] == {}
    assert not any(n.startswith("routed:") for n in report["test"]["prespecified_models"])
    assert report["claims"]["ordinary_warning"]["A"]["model"] == PERSISTENCE and not list(tmp_path.glob("h1_bundle_*"))        # khong bundle cho persistence
    assert report["persistence"]["validation"]["lift_vnd"] is None                                                              # mau so 0 => null + ly do, khong 0


def test_signal_gives_a_learned_champion_with_routing_null_bundle_and_claims(tmp_path):
    cfg = small_cfg(null_n=3)
    cfg["routing"].update(min_rows=50, min_hotels=5, min_dates=2)
    frames = synthetic_frames(seed=5)
    report = _run(cfg=cfg, frames=frames, out_dir=tmp_path)
    champ = report["champions"]["A"]["winner"]
    assert champ != PERSISTENCE and champ.startswith("hgb_l1-")
    assert report["validation_table"][champ]["validation"]["lift_vnd"] > 0
    assert report["null_test"]["status"] == "ok" and len(report["null_test"]["nulls"]) == 3 and report["null_test"]["lift_vnd"]["max_null"] <= 0.02
    assert [n["perm_seed"] for n in report["null_test"]["nulls"]] == [20261008, 20261009, 20261010]
    assert sum(e["kind"] == "null" for e in report["ledger"]["entries"]) == 3
    assert f"routed:{champ}" in report["test"]["prespecified_models"] and report["test"]["table"][champ]["lift_vnd"] is not None
    assert report["claims"]["claim_ready"] is False and "dev test da lo" in report["claims"]["why_not_claim"]
    assert report["claims"]["ordinary_warning"][f"A:{champ}"]["ordinary_lift_vnd_test"] is not None
    bundle = joblib.load(tmp_path / f"h1_bundle_{champ}.joblib")
    assert bundle["feature_list_sha256"] == report["features"]["sha256"] and bundle["config_sha256"] == cfg["config_sha256"] and "champion_A" in bundle["roles"]
    assert bundle["target_transform"] == "log_ratio" and bundle["routing_policy"] == report["routing"]["policy_by_model"][champ] and bundle["horizon"] == 1
    assert "KHONG phai package da phuc vu production" in bundle["note"] and bundle["model"].get_params()["loss"] == "absolute_error"
    assert report["finalists"][champ]["train_in_sample"]["lift_vnd"] is not None                                              # metric TRAIN co mat trong report


def test_routing_uses_persistence_for_modes_that_fail_the_validation_policy(tmp_path):
    frames = synthetic_frames(seed=5, n_hotels=40)
    rng = np.random.default_rng(11)
    for name in ("validation", "test"):                                       # cold_start: nhan nhieu vo quan voi feature => model that thu thiet
        f = frames[name]
        cold = (f["hotel_id"].str[-3:].astype(int) % 2 == 0)
        f["inference_mode"] = np.where(cold, "cold_start", "history_enriched")
        f.loc[cold, "y_true"] = f.loc[cold, "current_price"] * np.exp(rng.normal(0, 0.2, int(cold.sum())))
    cfg = small_cfg()
    cfg["routing"].update(min_rows=50, min_hotels=5, min_dates=2)
    cfg["bootstrap"]["n"] = 200
    report = _run(cfg=cfg, frames=frames, out_dir=tmp_path)
    champ = report["champions"]["A"]["winner"]
    assert champ != PERSISTENCE
    policy = report["routing"]["policy_by_model"][champ]
    assert policy["history_enriched"]["use_model"] is True and policy["cold_start"]["use_model"] is False, policy
    assert policy["cold_start"]["reason"] in ("lift_not_positive", "ci_lower_not_positive")
    pred = pd.read_parquet(tmp_path / "h1_predictions_v3.parquet")
    for split in ("validation", "test"):
        part = pred[pred["split"] == split]
        cold = part["inference_mode"] == "cold_start"
        assert np.allclose(part.loc[cold, f"pred_routed:{champ}"], part.loc[cold, "current_price"])                          # persistence cho mode bi loai
        assert np.allclose(part.loc[~cold, f"pred_routed:{champ}"], part.loc[~cold, f"pred_{champ}"])                        # model cho mode duoc dung
    routed = report["test"]["table"][f"routed:{champ}"]
    assert routed["lift_vnd"] != report["test"]["table"][champ]["lift_vnd"]


def test_time_budget_makes_a_family_incomplete_without_interim_best():
    t = [0.0]
    ledger = FitLedger(1.0, clock=lambda: t[0])
    original = v3_runner.cv_candidate

    def slow(*args, **kwargs):
        out = original(*args, **kwargs)
        t[0] += 5.0
        return out

    v3_runner.cv_candidate = slow
    try:
        report = _run(ledger=ledger)
    finally:
        v3_runner.cv_candidate = original
    family = report["families"]["hgb_l1"]
    assert family["complete"] is False and family["status"] == "incomplete" and family["reason"]
    assert family["winner_A"] is None and family["winner_B"] is None and not report["finalists"]
    assert report["champions"]["A"]["winner"] == PERSISTENCE or report["champions"]["A"]["winner"] in ("hgb_l2", "rf_l2", "ridge")      # khong co interim best cua ho
    assert not any(n.startswith("hgb_l1-") for n in report["test"]["prespecified_models"]) and report["contract_complete"] is False
    assert report["ablation"]["status"] == "skipped"


def test_a_failed_candidate_is_recorded_and_never_scored_as_zero():
    original = v3_runner.cv_candidate
    counter = [0]

    def sometimes_fail(build, X, z, cur, y, folds):
        counter[0] += 1
        if counter[0] == 2:
            return {"status": "failed", "reason": "RuntimeError: boom", "per_fold": [], "pooled": None, "seconds": 0.0}
        return original(build, X, z, cur, y, folds)

    v3_runner.cv_candidate = sometimes_fail
    try:
        report = _run()
    finally:
        v3_runner.cv_candidate = original
    cands = report["families"]["hgb_l1"]["candidates"]
    assert [c["status"] for c in cands] == ["ok", "failed", "ok"] and cands[1]["pooled"] is None and cands[1]["reason"] == "RuntimeError: boom"
    assert report["families"]["hgb_l1"]["complete"] is True and report["families"]["hgb_l1"]["winner_A"]["candidate_id"] != "hgb_l1-01"
    assert any(e["status"] == "failed" and e["kind"] == "cv" for e in report["ledger"]["entries"])


def test_too_few_rows_and_infeasible_cv_are_skipped_with_reasons():
    cfg = small_cfg()
    cfg["min_rows"] = {"train": 10_000, "validation": 10, "test": 10}
    skipped = _run(cfg=cfg)
    assert skipped["status"] == "skipped" and "khong du mau toi thieu" in skipped["reason"]
    short = _run(frames=synthetic_frames(train_days=12))
    assert short["status"] == "skipped" and "cv khong kha thi" in short["reason"]


# --------------------------------------------------------------------------- xgboost gia: 3 ho day du, fallback thiet bi, bo ho
def test_three_families_with_fake_xgboost_use_resolved_device_and_complete_contract(monkeypatch):
    fake = install_fake_xgboost(monkeypatch)
    cfg = small_cfg(families=("hgb_l1", "xgb_abs", "xgb_ph"))
    report = _run(cfg=cfg, device_request="auto")
    assert report["device"]["device"] == "cuda" and report["device"]["fallback_reason"] is None
    assert all(report["families"][f]["complete"] for f in ("hgb_l1", "xgb_abs", "xgb_ph")) and report["contract_complete"] is True
    objectives = {inst.kwargs["objective"] for inst in fake.instances}
    assert {"reg:absoluteerror", "reg:pseudohubererror", "reg:squarederror"} <= objectives
    assert fake.instances and all(inst.kwargs["device"] == "cuda" for inst in fake.instances)
    ph = [c for c in report["families"]["xgb_ph"]["candidates"]]
    assert all("huber_slope" in c["params"] for c in ph) and "xgb_l2" in report["test"]["prespecified_models"]
    order = [m for m in report["test"]["prespecified_models"]]
    assert order.index("hgb_l2") < order.index("xgb_l2") < order.index("rf_l2")


def test_cuda_failure_falls_back_to_cpu_and_the_failed_attempts_are_counted(monkeypatch):
    install_fake_xgboost(monkeypatch, fail_devices={"cuda"})
    report = _run(cfg=small_cfg(families=("hgb_l1", "xgb_abs")), device_request="auto")
    assert report["device"]["device"] == "cpu" and "CUDA" in report["device"]["fallback_reason"]
    smoke = [e for e in report["ledger"]["entries"] if e["kind"] == "smoke"]
    assert sum(e["status"] == "failed" for e in smoke) == 2 and sum(e["status"] == "ok" for e in smoke) == 2
    assert report["families"]["xgb_abs"]["complete"] is True


def test_unusable_xgboost_skips_xgb_families_and_marks_the_contract_incomplete(monkeypatch):
    install_fake_xgboost(monkeypatch, fail_devices={"cuda", "cpu"})
    report = _run(cfg=small_cfg(families=("hgb_l1", "xgb_abs")), device_request="auto")
    assert report["families"]["xgb_abs"]["status"] == "skipped" and report["families"]["xgb_abs"]["reason"] and report["contract_complete"] is False
    assert report["controls"]["xgb_l2"]["status"] == "skipped" and report["families"]["hgb_l1"]["complete"]


# --------------------------------------------------------------------------- ablation + champion la mo hinh khoi ty le
def test_accepted_ablation_enters_the_finalist_pool_and_can_be_a_champion_with_block_features(tmp_path, monkeypatch):
    monkeypatch.setattr(v3_runner, "ablation_decision", lambda **kw: {"accepted": True, "forced_for_test": True})
    real_champion = v3_runner.champion
    monkeypatch.setattr(v3_runner, "champion", lambda arm, cands, order, tie: {"winner": "hgb_l1-ratio1", "reason": "forced", "best": 0.1, "tie_group": [], "undefined": [], "arm": arm,
                                                                                "metric": "x"} if "hgb_l1-ratio1" in cands else real_champion(arm, cands, order, tie))
    report = _run(out_dir=tmp_path)
    assert "hgb_l1-ratio1" in report["finalists"] and report["finalists"]["hgb_l1-ratio1"]["matrix"] == "tree_block" and report["champions"]["A"]["winner"] == "hgb_l1-ratio1"
    assert "hgb_l1-ratio1" in report["test"]["predicted_models"]
    bundle = joblib.load(tmp_path / "h1_bundle_hgb_l1-ratio1.joblib")
    assert bundle["ratio_block"]["version"] == "ratio-1.0" and "r_mean14" in bundle["ratio_block"]["columns"] and bundle["matrix"] == "tree_block"
    assert set(bundle["encoder_categories"]) == {"city", "lead_time_bucket", "inference_mode"}


def test_ablation_can_be_disabled_or_skipped():
    cfg = small_cfg()
    cfg["ablation"]["enabled"] = False
    assert _run(cfg=cfg)["ablation"]["status"] == "not_run"


# --------------------------------------------------------------------------- bat bien v2 + khong ghi ngoai out_dir
def test_v2_identity_is_untouched():
    assert training.TRAINING_VERSION == "training-1.2.0"
    assert load_config()["version"] == "train-v2.0.0"
    assert load_config_v3()["version"] == "train-v3.0.0" and load_config_v3()["config_sha256"] != load_config()["config_sha256"]


def test_seed_stability_flag_is_refused_instead_of_being_a_silent_noop():
    cfg = small_cfg()
    cfg["budget"]["seed_stability"] = True
    with pytest.raises(NotImplementedError, match="seed_stability"):
        _run(cfg=cfg)


def test_winner_deduplication_refits_each_unique_candidate_once(tmp_path):
    report = _run(out_dir=tmp_path)
    fam = report["families"]["hgb_l1"]
    unique = {fam["winner_A"]["candidate_id"], fam["winner_B"]["candidate_id"]}
    refits = [e for e in report["ledger"]["entries"] if e["kind"] == "refit"]
    assert len(refits) == len(unique) and {e["name"] for e in refits} == unique and set(report["finalists"]) - {"hgb_l1-ratio1"} == unique


def test_arms_may_pick_different_champions_and_both_get_routed_bundles_and_test_rows(tmp_path, monkeypatch):
    real = v3_runner.champion

    def split(arm, cands, order, tie):
        out = real(arm, cands, order, tie)
        if arm == "B" and "rf_l2" in cands:
            out = {**out, "winner": "rf_l2", "reason": "forced_for_test"}
        return out

    monkeypatch.setattr(v3_runner, "champion", split)
    cfg = small_cfg()
    cfg["routing"].update(min_rows=50, min_hotels=5, min_dates=2)
    report = _run(cfg=cfg, frames=synthetic_frames(seed=5), out_dir=tmp_path)
    a, b = report["champions"]["A"]["winner"], report["champions"]["B"]["winner"]
    assert b == "rf_l2" and a != b and a != PERSISTENCE
    pre = report["test"]["prespecified_models"]
    assert {a, b, f"routed:{a}", f"routed:{b}"} <= set(pre) and report["claims"]["ordinary_warning"].keys() >= {f"A:{a}", f"B:{b}", f"B:routed:{b}"}
    assert (tmp_path / f"h1_bundle_{a}.joblib").is_file() and (tmp_path / "h1_bundle_rf_l2.joblib").is_file()
    assert report["routing"]["policy_by_model"].keys() == {a, b}


def test_run_only_writes_inside_its_output_directory(tmp_path):
    before = {p.name for p in tmp_path.iterdir()}
    out = tmp_path / "run"
    _run(out_dir=out)
    assert {p.name for p in tmp_path.iterdir()} == before | {"run"}                                  # khong tao gi ngoai thu muc dau ra
    assert sorted(p.name for p in out.iterdir()) and all(p.name.startswith("h1_") for p in out.iterdir())
