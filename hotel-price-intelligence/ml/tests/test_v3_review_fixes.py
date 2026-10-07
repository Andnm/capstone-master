"""Test cho cac phat hien cua GPT file 16 (M2 gia hop le, M3 null hong, M4 routed/fold-train, M5 thiet bi thuc, MIN1 horizon, MIN4 replay bundle).
Moi test tai hien dung phan vi du GPT neu va khang dinh hanh vi dung/sai mo ta o tung muc (khong chon metric theo hieu nang)."""
from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from training import v3_runner
from training.config import ConfigError
from training.v3_contract import InvalidPredictionError, PERSISTENCE, feature_list_sha256, stable_sort
from training.v3_runner import run_from_frames
from v3_fixtures import FEATURES, install_fake_xgboost, small_cfg, synthetic_frames


@pytest.fixture(autouse=True)
def xgboost_absent_by_default(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "xgboost", None)


def _run(cfg=None, frames=None, out_dir=None, **kwargs):
    kwargs.setdefault("device_request", "cpu")
    return run_from_frames(frames or synthetic_frames(seed=5), FEATURES, 1, cfg or small_cfg(), out_dir=out_dir, **kwargs)


def _null_cfg(n=3):
    cfg = small_cfg(null_n=n)
    cfg["routing"].update(min_rows=50, min_hotels=5, min_dates=2)
    return cfg


class _AlwaysHuge:
    """Estimator tra z huu han nhung vo ly (1000): gia = current*exp(1000) = inf."""
    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.full(len(X), 1000.0)


# --------------------------------------------------------------------------- M2
def test_invalid_candidate_predictions_fail_every_candidate_and_the_contract_is_not_complete(monkeypatch):
    monkeypatch.setattr(v3_runner, "build_estimator", lambda spec, params, *, seed, device="cpu": _AlwaysHuge())
    report = _run()
    cands = report["families"]["hgb_l1"]["candidates"]
    assert cands and all(c["status"] == "failed" and "gia du bao khong huu han" in c["reason"] and c["pooled"] is None for c in cands)
    assert report["families"]["hgb_l1"]["winner_A"]["candidate_id"] is None and not report["finalists"]
    assert report["contract_complete"] is False and report["status"] == "ok"
    assert all(np.isfinite(v["validation"]["mae_vnd"]) for v in report["validation_table"].values())                # khong co MAE inf nao duoc cong bo


def test_a_control_with_invalid_predictions_is_failed_and_excluded_from_validation_and_test(monkeypatch):
    original = v3_runner.build_control

    def bad_hgb_l2(name, cfg, horizon, *, seed, device, features):
        return (_AlwaysHuge(), "tree") if name == "hgb_l2" else original(name, cfg, horizon, seed=seed, device=device, features=features)

    monkeypatch.setattr(v3_runner, "build_control", bad_hgb_l2)
    report = _run()
    assert report["controls"]["hgb_l2"]["status"] == "failed" and "InvalidPredictionError" in report["controls"]["hgb_l2"]["reason"]
    assert "hgb_l2" not in report["validation_table"] and "hgb_l2" not in report["test"]["prespecified_models"] and report["contract_complete"] is False


def test_invalid_test_prediction_aborts_the_run_without_publishing_a_report(tmp_path, monkeypatch):
    frames = synthetic_frames(seed=5)
    n_test = len(frames["test"])
    original = HistGradientBoostingRegressor.predict
    monkeypatch.setattr(HistGradientBoostingRegressor, "predict", lambda self, X, *a, **k: np.full(len(X), 1000.0) if len(X) == n_test else original(self, X, *a, **k))
    with pytest.raises(InvalidPredictionError, match="TEST"):
        _run(frames=frames, out_dir=tmp_path)
    assert not (tmp_path / "h1_report.json").exists() and not (tmp_path / "h1_predictions_v3.parquet").exists()


# --------------------------------------------------------------------------- M3
def test_all_null_fits_failing_marks_the_null_failed_and_the_contract_incomplete(monkeypatch):
    install_fake_xgboost(monkeypatch)                          # moi dieu kien khac cua hop dong deu dat (xgb_l2 chay qua xgboost gia) => chi trang thai null quyet dinh contract_complete
    monkeypatch.setattr(v3_runner, "permute_within_dates", lambda z, dates, seed: np.full(len(z), np.nan))
    report = _run(cfg=_null_cfg(3))
    null = report["null_test"]
    assert report["champions"]["A"]["winner"] != PERSISTENCE
    assert null["status"] == "failed" and (null["requested"], null["attempted"], null["successful"], null["failed"]) == (3, 3, 0, 3) and null["complete"] is False
    assert null["lift_vnd"]["rank_p"] is None and null["lift_vnd"]["max_null"] is None and all("error" in n for n in null["nulls"])
    assert report["contract_complete"] is False and any("null hoan vi khong hoan tat" in w for w in report["warnings"])
    assert sum(e["kind"] == "null" and e["status"] == "failed" for e in report["ledger"]["entries"]) == 3
    assert report["test"]["locked"] is True                                          # negative control hong KHONG dung de doi model/route; TEST van chi theo danh sach da chot


def test_one_failed_null_is_incomplete_and_has_no_rank_from_the_remaining_fits(monkeypatch):
    real = v3_runner.permute_within_dates
    calls = [0]

    def flaky(z, dates, seed):
        calls[0] += 1
        return np.full(len(z), np.nan) if calls[0] == 2 else real(z, dates, seed)

    monkeypatch.setattr(v3_runner, "permute_within_dates", flaky)
    null = _run(cfg=_null_cfg(3))["null_test"]
    assert null["status"] == "incomplete" and (null["attempted"], null["successful"], null["failed"]) == (3, 2, 1)
    assert null["lift_vnd"]["rank_p"] is None and null["lift_vnd"]["partial_rank_denominator"] == 3 and null["lift_vnd"]["partial_rank_p_diagnostic"] > 0


def test_null_fits_with_absurd_predictions_count_as_failed(monkeypatch):
    monkeypatch.setattr(v3_runner, "permute_within_dates", lambda z, dates, seed: np.full(len(z), 1000.0))          # nhan vo ly => z~1000 => gia khong huu han
    report = _run(cfg=_null_cfg(2))
    assert report["null_test"]["status"] == "failed" and all("InvalidPredictionError" in n["error"] for n in report["null_test"]["nulls"]) and report["contract_complete"] is False


def test_fully_valid_null_is_complete_and_keeps_the_requested_rank_denominator(monkeypatch):
    install_fake_xgboost(monkeypatch)
    report = _run(cfg=_null_cfg(3))
    null = report["null_test"]
    assert report["contract_complete"] is True and not report["warnings"]                    # doi chung duong co so: null day du + moi dieu kien khac => hoan tat
    assert null["status"] == "ok" and null["complete"] is True and (null["requested"], null["successful"], null["failed"]) == (3, 3, 0)
    assert null["lift_vnd"]["rank_p"] in (1 / 4, 2 / 4, 3 / 4, 4 / 4)                                                 # (1 + #)/(3+1): mau so la so null YEU CAU


# --------------------------------------------------------------------------- M4
def test_routed_aggregate_on_validation_is_recomputable_from_the_locked_policy_and_predictions(tmp_path):
    report = _run(cfg=_null_cfg(2), out_dir=tmp_path)
    champ = report["champions"]["A"]["winner"]
    row = report["validation_table"][f"routed:{champ}"]
    assert row["routed_from"] == champ and row["validation"]["accuracy20"] is not None and row["validation"]["persistence_accuracy20"] is not None
    pred = pd.read_parquet(tmp_path / "h1_predictions_v3.parquet")
    val = pred[pred["split"] == "validation"]
    policy = report["routing"]["policy_by_model"][champ]
    use = val["inference_mode"].map(lambda m: policy.get(m, {"use_model": False})["use_model"]).to_numpy(bool)
    recomputed = np.where(use, val[f"pred_{champ}"].to_numpy(), val["current_price"].to_numpy())
    assert np.allclose(recomputed, val[f"pred_routed:{champ}"].to_numpy())
    y, cur = val["y_true"].to_numpy(), val["current_price"].to_numpy()
    assert row["validation"]["lift_vnd"] == pytest.approx(1 - np.abs(recomputed - y).sum() / np.abs(cur - y).sum(), abs=1e-12)
    assert row["validation"]["accuracy20"] == pytest.approx(float(np.mean(np.abs(recomputed - y) / y <= 0.2)))
    assert report["validation_table"][champ]["validation"]["lift_vnd"] is not None and PERSISTENCE in report["validation_table"]


def test_outer_train_rows_carry_accuracy_and_the_persistence_counterpart_and_fold_train_exists():
    report = _run()
    champ = report["champions"]["A"]["winner"]
    for row in (report["finalists"][champ]["train_in_sample"], report["persistence"]["train_in_sample"], report["controls"]["hgb_l2"]["train_in_sample"]):
        assert 0 <= row["accuracy20"] <= 1 and 0 <= row["persistence_accuracy20"] <= 1
    assert report["persistence"]["train_in_sample"]["accuracy20"] == report["persistence"]["train_in_sample"]["persistence_accuracy20"]
    fold = report["families"]["hgb_l1"]["candidates"][0]["per_fold"][0]
    assert fold["train"]["label"] == "in_sample_fold_train" and fold["train"]["n"] > fold["n_val"] and "lift_vnd" in fold["train"]            # fold-TRAIN co mat
    assert sum(e["fits"] for e in report["ledger"]["entries"] if e["kind"] in ("pilot", "cv")) == 3 * len(report["folds"])                    # va KHONG them fit


# --------------------------------------------------------------------------- M5
def test_xgb_that_silently_runs_on_cpu_after_the_cuda_smoke_is_flagged(monkeypatch):
    install_fake_xgboost(monkeypatch, silent_cpu_when=lambda kw: kw.get("n_jobs") == -1)         # smoke dung n_jobs=1 (cuda that), fit thuc dung n_jobs=-1 (am tham CPU)
    report = _run(cfg=small_cfg(families=("hgb_l1", "xgb_abs")), device_request="auto")
    assert report["device"]["device"] == "cuda" and report["device"]["device_verified"] is True
    rows = {**report["finalists"], **report["controls"]}
    assert any(r.get("actual_device") == "cpu" for r in rows.values())
    assert report["contract_complete"] is False and any("thiet bi XGBoost thuc te khac" in w for w in report["warnings"])


def test_xgb_actual_device_is_recorded_when_it_matches(monkeypatch):
    install_fake_xgboost(monkeypatch)
    report = _run(cfg=small_cfg(families=("hgb_l1", "xgb_abs")), device_request="auto")
    xgb_rows = {n: r for n, r in {**report["finalists"], **report["controls"]}.items() if r.get("family") in ("xgb_abs", "xgb_l2")}
    assert xgb_rows and all(r["actual_device"] == "cuda" for r in xgb_rows.values()) and not any("thiet bi XGBoost" in w for w in report["warnings"])


# --------------------------------------------------------------------------- MIN1
def test_horizon_outside_the_dataset_whitelist_is_refused_before_any_read_or_output(tmp_path):
    context = {"dataset_meta": {"contract": {"evaluation_horizons": [1]}}, "provenance": {}, "colab_manifest": None}
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="ngoai evaluation_horizons"):
        v3_runner.run_horizon_v3(tmp_path / "does_not_exist", 3, small_cfg(), out, context=context)
    assert not out.exists()


def test_unsupported_horizon_for_a_control_is_refused_by_the_runner():
    with pytest.raises(ConfigError, match="chua co tham so da chot"):
        run_from_frames(synthetic_frames(), FEATURES, 7, small_cfg(), device_request="cpu")


# --------------------------------------------------------------------------- MIN4: replay bundle
def _replay(tmp_path, frames, name):
    from training.v3_bundle import predict_z, routed_z

    bundle = joblib.load(tmp_path / f"h1_bundle_{name.replace(':', '_')}.joblib")
    val = stable_sort(frames["validation"])
    pred = pd.read_parquet(tmp_path / "h1_predictions_v3.parquet")
    pv = pred[pred["split"] == "validation"].reset_index(drop=True)
    z = predict_z(bundle, val)
    price = val["current_price"].to_numpy(float) * np.exp(z)
    assert np.allclose(price, pv[f"pred_{name}"].to_numpy(), rtol=0, atol=1e-6)                                         # replay == du doan da xuat
    zr = routed_z(bundle, val, z)
    assert np.allclose(val["current_price"].to_numpy(float) * np.exp(zr), pv[f"pred_routed:{name}"].to_numpy(), rtol=0, atol=1e-6)
    assert np.all(routed_z(bundle, val.assign(inference_mode="chua_biet"), z) == 0.0)                                  # mode la => persistence
    assert bundle["effective_feature_list_sha256"] == feature_list_sha256(bundle["effective_features"]) and len(bundle["effective_features"]) >= len(bundle["features"])
    return bundle


def _force_champion(monkeypatch, winner):
    real = v3_runner.champion
    monkeypatch.setattr(v3_runner, "champion", lambda arm, cands, order, tie: {"winner": winner, "reason": "forced", "best": 0.1, "tie_group": [], "undefined": [], "arm": arm,
                                                                                "metric": "x"} if winner in cands else real(arm, cands, order, tie))


def test_bundle_replay_reproduces_raw_tree_predictions_and_routing(tmp_path):
    frames = synthetic_frames(seed=5)
    cfg = _null_cfg(2)
    cfg["ablation"]["enabled"] = False                                                   # khong ablation => champion la ung vien raw (tren du lieu nay khoi ty le tu duoc chap nhan)
    report = _run(cfg=cfg, frames=frames, out_dir=tmp_path)
    bundle = _replay(tmp_path, frames, report["champions"]["A"]["winner"])
    assert bundle["matrix"] == "tree" and bundle["effective_features"] == bundle["features"] and bundle["n_features_in"] == len(FEATURES)


def test_bundle_replay_refuses_a_bundle_whose_feature_contract_does_not_match(tmp_path):
    from training.v3_bundle import encode_for_bundle

    frames = synthetic_frames(seed=5)
    cfg = _null_cfg(2)
    cfg["ablation"]["enabled"] = False
    report = _run(cfg=cfg, frames=frames, out_dir=tmp_path)
    name = report["champions"]["A"]["winner"]
    bundle = joblib.load(tmp_path / f"h1_bundle_{name}.joblib")
    val = stable_sort(frames["validation"])
    encode_for_bundle(bundle, val)                                                                              # hop le
    with pytest.raises(ValueError, match="effective_features"):
        encode_for_bundle({**bundle, "effective_features": list(reversed(bundle["effective_features"]))}, val)
    with pytest.raises(ValueError, match="n_features_in"):
        encode_for_bundle({**bundle, "n_features_in": bundle["n_features_in"] + 1}, val)
    with pytest.raises(ValueError, match="domain danh muc"):
        encode_for_bundle({**bundle, "encoder_categories": {**bundle["encoder_categories"], "city": ["Mars"]}}, val)


def test_bundle_replay_reproduces_ratio_block_predictions(tmp_path, monkeypatch):
    monkeypatch.setattr(v3_runner, "ablation_decision", lambda **kw: {"accepted": True, "forced_for_test": True})
    _force_champion(monkeypatch, "hgb_l1-ratio1")
    frames = synthetic_frames(seed=5)
    _run(cfg=_null_cfg(2), frames=frames, out_dir=tmp_path)
    bundle = _replay(tmp_path, frames, "hgb_l1-ratio1")
    assert bundle["matrix"] == "tree_block" and bundle["effective_features"] == FEATURES + list(bundle["ratio_block"]["columns"]) and bundle["n_features_in"] == len(FEATURES) + 6
    assert bundle["feature_list_sha256"] != bundle["effective_feature_list_sha256"]                                       # raw va hieu dung khac nhau, deu duoc ghi


def test_bundle_replay_reproduces_ridge_raw_predictions(tmp_path, monkeypatch):
    _force_champion(monkeypatch, "ridge")
    frames = synthetic_frames(seed=5)
    report = _run(cfg=_null_cfg(2), frames=frames, out_dir=tmp_path)
    assert "ridge" in report["test"]["predicted_models"]                                  # Ridge chi duoc predict TEST vi la champion
    bundle = _replay(tmp_path, frames, "ridge")
    assert bundle["matrix"] == "raw" and bundle["effective_features"] == FEATURES and bundle["n_features_in"] == len(FEATURES)


# --------------------------------------------------------------------------- R2-M5 (GPT file 18): thiet bi thuc KHONG doc duoc khong duoc hoan tat C8 im lang
_XGB_CFG = dict(families=("hgb_l1", "xgb_abs"))


def _device_verdict(report):
    return report["contract_complete"], report["device"]["verification"], [w for w in report["warnings"] if "KHONG xac minh" in w]


def test_unreadable_actual_device_at_the_smoke_is_not_complete_and_is_warned(monkeypatch):
    install_fake_xgboost(monkeypatch, unreadable_actual=True)
    report = _run(cfg=small_cfg(**_XGB_CFG), device_request="auto")
    complete, verification, warned = _device_verdict(report)
    assert report["device"]["device"] == "cuda" and report["device"]["device_verified"] is False
    assert complete is False and verification["smoke_verified"] is False and verification["complete"] is False and warned          # khong 'warnings=[]'
    assert any(r.get("actual_device") is None for r in {**report["finalists"], **report["controls"]}.values())


def test_unreadable_actual_device_only_at_fit_time_after_a_verified_smoke_is_not_complete(monkeypatch):
    install_fake_xgboost(monkeypatch, unreadable_when=lambda kw: kw.get("n_jobs") == -1)               # smoke n_jobs=1 doc duoc; fit that n_jobs=-1 khong doc duoc
    report = _run(cfg=small_cfg(**_XGB_CFG), device_request="auto")
    complete, verification, warned = _device_verdict(report)
    assert report["device"]["device_verified"] is True and verification["smoke_verified"] is True
    assert verification["fits_unverified"] and complete is False and warned and verification["complete"] is False


def test_verified_cuda_verified_cpu_fallback_and_explicit_cpu_can_complete_the_device_contract(monkeypatch):
    install_fake_xgboost(monkeypatch)
    cuda = _run(cfg=small_cfg(**_XGB_CFG), device_request="auto")
    assert cuda["device"]["device"] == "cuda" and cuda["device"]["verification"]["complete"] is True and cuda["contract_complete"] is True and not cuda["warnings"]
    install_fake_xgboost(monkeypatch, warn_fallback_devices={"cuda"})                                                # warning-only fallback: CPU duoc xac minh
    fallback = _run(cfg=small_cfg(**_XGB_CFG), device_request="auto")
    assert fallback["device"]["device"] == "cpu" and fallback["device"]["fallback_reason"] and fallback["device"]["verification"]["complete"] is True
    assert fallback["contract_complete"] is True                                                                       # CPU hop le + da xac minh KHONG bi phat
    install_fake_xgboost(monkeypatch)
    explicit = _run(cfg=small_cfg(**_XGB_CFG), device_request="cpu")
    assert explicit["device"]["device"] == "cpu" and explicit["device"]["verification"]["complete"] is True and explicit["contract_complete"] is True


def test_unreadable_actual_device_at_the_smoke_only_is_still_not_complete(monkeypatch):
    install_fake_xgboost(monkeypatch, unreadable_when=lambda kw: kw.get("n_jobs") == 1)               # CHI smoke (n_jobs=1) khong doc duoc; moi fit that doc duoc
    report = _run(cfg=small_cfg(**_XGB_CFG), device_request="auto")
    verification = report["device"]["verification"]
    assert report["device"]["device_verified"] is False and verification["smoke_verified"] is False and verification["fits_unverified"] == []
    assert report["contract_complete"] is False and any("KHONG xac minh" in w for w in report["warnings"])
