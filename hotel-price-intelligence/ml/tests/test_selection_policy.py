"""Lat 2 (GPT file 50 muc 5): chon mo hinh theo validation MAE (min, theo CHIEU metric), tie sMAPE roi ten, lift/fallback/chan doan, NaN->null. Thuan."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_training import make_dataset, write_checksums, write_contract  # noqa: E402
from training import TRAINING_VERSION  # noqa: E402
from training import config as tconfig  # noqa: E402
from dataset_builder.feature_spec import CATEGORY_DOMAINS  # noqa: E402
from training.encoder import TreeEncoder  # noqa: E402
from training.runner import changed_subset_diagnostic, lift_mae, run_horizon, sanitize, selection_ranking  # noqa: E402

V1 = Path(__file__).resolve().parents[1] / "configs" / "train_v1.yaml"


@pytest.fixture()
def cfg():
    c = tconfig.load_config()
    c["min_rows"] = {"train": 200, "validation": 50, "test": 50}
    c["cv"] = {"n_splits": 3, "min_train_days": 15}
    c["models"]["rf"]["random_search"] = {"n_iter": 2, "space": {"n_estimators": [20, 40], "max_depth": [4, 8, None], "min_samples_leaf": [1, 5]}}
    return tconfig._with_hash(c)


def _results(**models):
    return {name: {"kind": "model", "validation": dict(metrics)} for name, metrics in models.items()}


# ----------------------------------------------------------------- cau hinh / chieu metric
def test_training_version_and_default_config_are_v2_with_explicit_directions():
    assert TRAINING_VERSION >= "training-1.2.0" and TRAINING_VERSION.startswith("training-")
    c = tconfig.load_config()
    assert c["version"] == "train-v2.0.0" and c["selection"]["primary_metric"] == "mae" and c["selection"]["tie_break"] == "smape"
    assert tconfig.metric_direction(c, "mae") == "min" and tconfig.metric_direction(c, "accuracy_at_tol") == "max"
    assert c["selection"]["final_tie"] == "model_name_asc"


def test_v1_config_stays_reproducible_with_accuracy_as_max():
    c1 = tconfig.load_config(V1)
    assert c1["version"] == "train-v1.0.0" and c1["selection"]["primary_metric"] == "accuracy_at_tol"
    assert tconfig.metric_direction(c1, "accuracy_at_tol") == "max" and tconfig.metric_direction(c1, "mae") == "min"      # mac dinh khi khong khai bao


@pytest.mark.parametrize("selection, message", [
    ({"primary_metric": "mae", "tie_break": "smape", "directions": {"mae": "lowest"}}, "min|max"),
    ({"primary_metric": "bogus", "tie_break": "smape"}, "chieu tot"),
    ({"primary_metric": "mae"}, "tie_break"),
    ({"primary_metric": "mae", "tie_break": "smape", "final_tie": "random"}, "final_tie"),
])
def test_invalid_selection_config_is_rejected(tmp_path, selection, message):
    import yaml

    raw = yaml.safe_load(tconfig.DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    raw["selection"] = selection
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(tconfig.ConfigError, match=message):
        tconfig.load_config(path)


# ----------------------------------------------------------------- xep hang theo chieu (bat DUNG duong moi: MAE thap nhung Accuracy thap hon)
def test_lower_mae_wins_even_when_accuracy_is_lower():
    results = _results(rf={"mae": 100.0, "smape": 0.05, "accuracy_at_tol": 0.90}, ridge={"mae": 120.0, "smape": 0.04, "accuracy_at_tol": 0.97})
    v2 = tconfig.load_config()
    assert selection_ranking(results, ["ridge", "rf"], v2) == ["rf", "ridge"]                     # MAE thap hon thang du accuracy thap hon
    v1 = tconfig.load_config(V1)
    assert selection_ranking(results, ["ridge", "rf"], v1) == ["ridge", "rf"]                     # duong cu (accuracy max) cho ket qua nguoc => test bat duoc duong moi


def test_ties_break_on_smape_then_model_name_deterministically_regardless_of_input_order():
    v2 = tconfig.load_config()
    same_mae = _results(xgb={"mae": 100.0, "smape": 0.03}, rf={"mae": 100.0, "smape": 0.05}, ridge={"mae": 100.0, "smape": 0.04})
    assert selection_ranking(same_mae, ["rf", "ridge", "xgb"], v2) == ["xgb", "ridge", "rf"]
    identical = _results(xgb={"mae": 100.0, "smape": 0.03}, rf={"mae": 100.0, "smape": 0.03}, ridge={"mae": 100.0, "smape": 0.03})
    for order in (["xgb", "rf", "ridge"], ["ridge", "xgb", "rf"], ["rf", "ridge", "xgb"]):
        assert selection_ranking(identical, order, v2) == ["rf", "ridge", "xgb"]                  # hoa het => ten tang dan


def test_max_direction_still_works_for_accuracy_and_undefined_metrics_rank_last():
    v1 = tconfig.load_config(V1)
    results = _results(a={"accuracy_at_tol": 0.91, "mae": 5.0}, b={"accuracy_at_tol": 0.95, "mae": 9.0}, c={"accuracy_at_tol": float("nan"), "mae": 1.0})
    assert selection_ranking(results, ["a", "b", "c"], v1) == ["b", "a", "c"]                     # NaN khong bao gio thang ham ho
    v2 = tconfig.load_config()
    results2 = _results(a={"mae": float("nan"), "smape": 0.0}, b={"mae": 50.0, "smape": 0.1}, c={"mae": None, "smape": 0.0})
    assert selection_ranking(results2, ["a", "b", "c"], v2)[0] == "b"


# ----------------------------------------------------------------- lift / NaN / chan doan
def test_lift_mae_handles_zero_and_undefined_baselines_explicitly():
    assert lift_mae(80.0, 100.0) == {"value": pytest.approx(0.2), "reason": None}
    assert lift_mae(120.0, 100.0)["value"] == pytest.approx(-0.2)                                  # tham hon persistence => lift am, khong bi cat
    zero = lift_mae(5.0, 0.0)
    assert zero["value"] is None and "persistence" in zero["reason"]
    assert lift_mae(None, 100.0)["value"] is None and lift_mae(5.0, float("nan"))["value"] is None


def test_sanitize_turns_undefined_numbers_into_null_and_allows_strict_json():
    payload = {"p": float("nan"), "q": [1.0, float("inf"), np.float64("nan"), np.int64(3)], "ok": 0.0, "nested": {"x": float("-inf")}}
    clean = sanitize(payload)
    assert clean == {"p": None, "q": [1.0, None, None, 3], "ok": 0.0, "nested": {"x": None}}
    json.dumps(clean, allow_nan=False)                                                             # khong nem loi
    with pytest.raises(ValueError):
        json.dumps(payload, allow_nan=False)


def test_changed_subset_diagnostic_reports_denominators_and_baseline_on_the_same_rows(cfg):
    frame = pd.DataFrame({"current_price": [100.0, 100.0, 100.0, 100.0], "y_true": [100.0, 101.0, 150.0, 60.0]})   # 2 on dinh (<=2%), 2 doi (+50%, -40%)
    pred = np.array([100.0, 100.0, 140.0, 80.0])
    diag = changed_subset_diagnostic(frame, pred, np.full(4, 100.0), cfg)
    assert diag["n_all"] == 4 and diag["n_changed"] == 2 and diag["changed_share"] == 0.5 and diag["stable_share"] == 0.5
    assert diag["model"]["n"] == 2 and diag["persistence"]["n"] == 2
    assert diag["model"]["mae"] == pytest.approx(15.0) and diag["persistence"]["mae"] == pytest.approx(45.0)
    assert diag["lift_mae_vs_persistence"]["value"] == pytest.approx(1 - 15.0 / 45.0)
    none_changed = changed_subset_diagnostic(frame.iloc[:2], pred[:2], np.full(2, 100.0), cfg)
    assert none_changed["n_changed"] == 0 and "model" not in none_changed and none_changed["n_all"] == 2                # n=0 van hien, khong bo


# ----------------------------------------------------------------- encoder: mapping CO DINH tu domain protocol (GPT file 52 muc 4)
FEATURES = ["current_price", "city", "lead_time_bucket", "max_occupancy", "inference_mode"]


def test_tree_encoder_mapping_is_the_protocol_domain_and_independent_of_fit_data():
    a = pd.DataFrame({"current_price": [1.0, 2.0], "city": ["Vũng Tàu", "Vũng Tàu"], "lead_time_bucket": ["lt3", "lt3"], "max_occupancy": [2.0, 4.0], "inference_mode": ["cold_start"] * 2})
    b = pd.DataFrame({"current_price": [1e9], "city": ["Hà Nội"], "lead_time_bucket": ["gt60"], "max_occupancy": [9.0], "inference_mode": ["history_enriched"]})
    fresh, fit_a, fit_b = TreeEncoder(FEATURES), TreeEncoder(FEATURES).fit(a), TreeEncoder(FEATURES).fit(b)
    expected = {c: list(CATEGORY_DOMAINS[c]) for c in ("city", "lead_time_bucket", "inference_mode")}
    assert fresh.categories == fit_a.categories == fit_b.categories == expected
    assert set(vars(fresh)) == {"features", "categories"}                                           # khong giu thong ke nao hoc tu du lieu
    assert list(CATEGORY_DOMAINS["city"]) == ["Hồ Chí Minh", "Hà Nội", "Vũng Tàu", "Đà Lạt", "Phú Quốc"]
    assert list(CATEGORY_DOMAINS["lead_time_bucket"]) == ["lt3", "3-7", "7-14", "14-30", "30-60", "gt60"]


def test_cv_fold_where_validation_has_a_category_absent_from_fold_train_keeps_the_same_encoding():
    """Fixture cua GPT: fold train chi co city=[Vung Tau], validation chi co [Ha Noi]. Hoc category tu fold => validation -> NaN, train -> 0;
    hoc tu toan TRAIN => ma doi. Mapping co dinh: ma cua tung city giong het du fit tren gi, khong co category tuong lai nao 'lot' vao encoding."""
    train = pd.DataFrame({"city": ["Vũng Tàu"], "current_price": [1.0]})
    validation = pd.DataFrame({"city": ["Hà Nội"], "current_price": [2.0]})
    fold_fit, whole_fit = TreeEncoder(["city", "current_price"]).fit(train), TreeEncoder(["city", "current_price"]).fit(pd.concat([train, validation]))
    for encoder in (fold_fit, whole_fit):
        assert encoder.transform(train)["city"].tolist() == [2.0] and encoder.transform(validation)["city"].tolist() == [1.0]
    assert fold_fit.categories == whole_fit.categories                                              # known-but-absent khong lam doi codebook
    assert np.array_equal(fold_fit.transform(train).to_numpy(), whole_fit.transform(train).to_numpy())


def test_unknown_values_become_nan_and_are_counted_not_learned():
    enc = TreeEncoder(FEATURES)
    frame = pd.DataFrame({"city": ["Đà Lạt", "Nha Trang", None], "lead_time_bucket": ["7-14", "bogus", "lt3"], "inference_mode": ["cold_start", "x", None],
                          "current_price": [1.0, 2.0, 3.0], "max_occupancy": [2.0, 2.0, 2.0]})
    out = enc.transform(frame)
    assert out["city"].tolist()[0] == 3.0 and np.isnan(out["city"].tolist()[1]) and np.isnan(out["city"].tolist()[2])
    assert enc.unknown_counts(frame) == {"city": 1, "lead_time_bucket": 1, "inference_mode": 1}
    assert enc.categories["city"] == list(CATEGORY_DOMAINS["city"])                                  # gap Nha Trang khong them vao domain


# ----------------------------------------------------------------- end-to-end tren dataset gia
def test_run_horizon_reports_selection_lift_fallback_and_diagnostics(tmp_path, cfg):
    ds = make_dataset(tmp_path, n_days=110, n_series=24)
    report = run_horizon(ds, 7, cfg, tmp_path / "out", ["ridge", "rf"])
    text = (tmp_path / "out" / "h7_report.json").read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"NaN/inf trong bao cao: {c}")))      # strict JSON
    selection = report["selection"]
    assert selection["basis"] == "validation" and selection["primary_metric"] == "mae" and selection["directions"]["mae"] == "min"
    ranking = [(r["model"], r["mae"]) for r in selection["ranking"]]
    assert ranking == sorted(ranking, key=lambda x: (x[1], x[0])) and report["selected_model"] == ranking[0][0]
    for name in ("ridge", "rf"):
        assert report["results"][name]["validation"]["mae"] >= report["results"][report["selected_model"]]["validation"]["mae"]
    assert report["results"]["ridge"]["tuning"]["method"] == "fixed_alpha"
    assert set(report["lift_mae_vs_persistence"]) == {"validation", "test"}
    fallback = report["deployment_fallback"]
    assert fallback["decided_on"] == "validation" and fallback["recommended"] in ("persistence", report["selected_model"])
    val_lift = report["lift_mae_vs_persistence"]["validation"]["value"]
    assert (fallback["recommended"] == report["selected_model"]) == (val_lift is not None and val_lift > 0)       # quyet dinh chi tu validation
    diag = report["diagnostics"]["changed_price_subset"]
    assert diag["test"]["n_all"] == report["rows"]["test"] and diag["validation"]["n_all"] == report["rows"]["validation"]
    assert diag["test"]["n_changed"] <= diag["test"]["n_all"]


def test_fallback_recommends_persistence_when_there_is_nothing_to_learn(tmp_path, cfg):
    ds = make_dataset(tmp_path, n_days=110, n_series=24, version="ds_flat")
    frame = pd.read_parquet(ds / "samples.parquet")
    frame["y_price_h7"] = frame["current_price"]                                                   # gia khong doi => persistence MAE = 0
    frame["y_delta_h7"], frame["y_pct_change_h7"], frame["y_direction_h7"] = 0.0, 0.0, "stable"
    frame.to_parquet(ds / "samples.parquet", index=False)
    write_contract(ds, version="ds_flat")
    write_checksums(ds, rows=len(frame))
    report = run_horizon(ds, 7, cfg, tmp_path / "out", ["ridge"])
    assert report["results"]["persistence"]["validation"]["mae"] == 0.0
    assert report["lift_mae_vs_persistence"]["validation"] == {"value": None, "reason": "MAE persistence = 0: lift khong xac dinh"}
    assert report["deployment_fallback"]["recommended"] == "persistence"
    assert report["diagnostics"]["changed_price_subset"]["test"]["n_changed"] == 0


# ----------------------------------------------------------------- pham vi ket luan: official_run (CLI) + contract official + whitelist + primary_eligible (GPT file 54 C53-M1)
def _quiet_provenance(monkeypatch):
    import training.runner as runner_module

    monkeypatch.setattr(runner_module, "require_known_provenance", lambda *a, **k: None)
    monkeypatch.setattr(runner_module, "require_lineage", lambda *a, **k: None)


@pytest.mark.parametrize("index, purpose, status, official_run, expected", [
    (0, "rehearsal", "primary_eligible", False, "exploratory_not_official"),        # ca fixture GPT: rehearsal + primary_eligible goi nhu non-official
    (1, "rehearsal", "primary_eligible", True, "exploratory_not_official"),         # co official_run nhung dataset rehearsal
    (2, "dev", "primary_eligible", True, "exploratory_not_official"),
    (3, "official", "primary_eligible", False, "exploratory_not_official"),         # dataset official nhung run KHONG phai --official
    (4, "official", "exploratory", True, "exploratory_not_official"),
    (5, "official", "primary_eligible", True, "official"),
])
def test_target_assessment_scope_matrix(tmp_path, cfg, monkeypatch, index, purpose, status, official_run, expected):
    from training.runner import build_context

    _quiet_provenance(monkeypatch)
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version=f"ds_scope{index}", evaluation_horizons=(7,), purpose=purpose, status=status)
    out = tmp_path / "out"
    context = build_context(ds, out, official_run=official_run)
    assert context["official_run"] is official_run
    report = run_horizon(ds, 7, cfg, out, ["ridge"], context=context)
    assert report["target_assessment"] == expected and report["official_run"] is official_run
    if expected == "official":
        assert report["target_assessment_reasons"] == [] and report["meets_project_target_accuracy_at_20pct"] in (True, False)
    else:
        assert report["meets_project_target_accuracy_at_20pct"] is None and report["target_assessment_reasons"]
    import joblib

    bundle = joblib.load(next(out.glob("h7_model_*.joblib")))
    assert bundle["target_assessment"] == expected and bundle["official_run"] is official_run                    # report va bundle nhat quan


def test_direct_library_call_defaults_to_exploratory_even_for_an_official_eligible_dataset(tmp_path, cfg):
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_scope_lib", evaluation_horizons=(7,), purpose="official", status="primary_eligible")
    report = run_horizon(ds, 7, cfg, tmp_path / "out", ["ridge"])
    assert report["official_run"] is False and report["target_assessment"] == "exploratory_not_official" and report["meets_project_target_accuracy_at_20pct"] is None
    assert "training run khong phai --official" in report["target_assessment_reasons"]


def test_official_run_cannot_be_claimed_without_known_code_provenance(tmp_path, monkeypatch):
    import training.runner as runner_module
    from training.provenance import ProvenanceError

    monkeypatch.setattr(runner_module, "code_provenance", lambda: {"source": "unknown", "error": "khong co git"})
    ds = make_dataset(tmp_path / "src", n_days=30, n_series=4, version="ds_scope_prov", evaluation_horizons=(7,), purpose="official", status="primary_eligible")
    with pytest.raises(ProvenanceError, match="official"):
        runner_module.build_context(ds, tmp_path / "out", official_run=True)
    assert runner_module.build_context(ds, tmp_path / "out2", official_run=False)["official_run"] is False


def test_manifest_horizon_entries_carry_the_assessment_scope(tmp_path, monkeypatch):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("train_models", Path(__file__).resolve().parents[1] / "scripts" / "train_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _quiet_provenance(monkeypatch)
    ds = make_dataset(tmp_path / "src", n_days=110, n_series=24, version="ds_scope_man", evaluation_horizons=(7,), purpose="official", status="primary_eligible")
    monkeypatch.setattr(sys, "argv", ["train_models.py", "--dataset-dir", str(ds), "--models", "ridge", "--horizons", "7", "--output-root", str(tmp_path / "models"), "--run-id", "r1"])
    assert module.main() == 0                                                                                    # official dataset nhung KHONG --official
    manifest = json.loads((tmp_path / "models" / "ds_scope_man" / "r1" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["official"] is False and manifest["horizons"][0]["target_assessment"] == "exploratory_not_official" and manifest["horizons"][0]["official_run"] is False
