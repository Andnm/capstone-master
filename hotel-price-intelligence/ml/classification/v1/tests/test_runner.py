"""E2E TREN DU LIEU TONG HOP (khong fit du lieu that): luong CV -> finalist -> champion -> khoa -> ablation -> mo test; thu tu truy cap test; no_valid_model; ngan sach; giao dich/resume; bundle."""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd
import pytest

from cls_training import candidates as C
from cls_training import metrics as M
from cls_training import runner as R
from cls_training import selection as S
from cls_training import transaction as T
from cls_training.features import prepare_frame
from cls_training.upstream.ratio import RATIO_BLOCK_COLUMNS, stable_sort

from synth import SYNTH_COLUMNS, make_loaded, runner_config
from test_candidates_selection import fake_xgb_factory


@pytest.fixture(autouse=True)
def _fake_xgb(monkeypatch):
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)


class Spy:
    """Boc khung test: moi truy cap thuoc tinh/phuong thuc ghi vao `order` (de chung minh khong ai doc test truoc khi mo)."""
    def __init__(self, frame, order):
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "_order", order)

    def __getattr__(self, name):
        self._order.append("ACCESS")
        return getattr(self._frame, name)


def run_with_order(cfg, loaded, monkeypatch):
    order: list[str] = []
    loaded.frames["test"] = Spy(loaded.frames["test"], order)
    real_freeze, real_open = S.freeze_record, R.TestGate.open
    monkeypatch.setattr(R, "freeze_record", lambda *a, **k: (order.append("FREEZE") or real_freeze(*a, **k)))
    monkeypatch.setattr(R.TestGate, "open", lambda self, freeze: (order.append("OPEN") or real_open(self, freeze)))
    real_predict = R.predict_frame
    monkeypatch.setattr(R, "predict_frame", lambda ft, frame, cfg_: (order.append("PREDICT") or real_predict(ft, frame, cfg_)))
    return R.run_horizon(cfg, loaded), order


@pytest.fixture(scope="module")
def happy():
    """Mot lan chay day du (module-scope de tiet kiem thoi gian); dung pytest.MonkeyPatch thu cong."""
    mp = pytest.MonkeyPatch()
    mp.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)
    cfg, loaded = runner_config(), make_loaded()
    result, order = run_with_order(cfg, loaded, mp)
    yield cfg, loaded, result, order
    mp.undo()


def test_happy_path_is_complete_with_one_champion_and_locked_allowlist(happy):
    cfg, loaded, result, order = happy
    rep = result.report
    assert rep["status"] == "complete" and rep["contract_complete"] is True and rep["incomplete_reasons"] == []
    fam = rep["selection"]["families"]
    assert {f: i["status"] for f, i in fam.items()} == {"lr": "complete", "hgb": "complete", "xgb": "complete"} and len(rep["selection"]["finalists"]) == 3
    champ = rep["champion"]
    assert champ in rep["selection"]["finalists"]
    allow = [a["model_id"] for a in rep["freeze"]["test_allowlist"]]
    assert allow == [*rep["selection"]["finalists"], f"{champ}+a1_ratio", f"{champ}+a2_no_history"] or sorted(allow) == sorted([*rep["selection"]["finalists"], f"{champ}+a1_ratio", f"{champ}+a2_no_history"])
    assert len(allow) == 5 == len(set(allow))
    assert sum(a["role"] == "champion" for a in rep["freeze"]["test_allowlist"]) == 1
    assert set(rep["models"]) == set(allow) and rep["test"]["opened_after_freeze"] is True
    assert rep["models"][champ]["roles"] == ["champion", "finalist"] and rep["probability_status"] == "uncalibrated"


def test_test_frame_is_untouched_until_after_the_freeze_record_exists(happy):
    cfg, loaded, result, order = happy
    assert order.index("FREEZE") < order.index("OPEN") < order.index("ACCESS")
    assert "ACCESS" not in order[: order.index("OPEN")]
    # moi lan predict tren test (sau OPEN) chi xay ra sau khi mo; truoc FREEZE chi predict tren validation (3 finalist)
    assert order[: order.index("FREEZE")].count("PREDICT") == 3
    ev = [e["event"] for e in result.events]
    assert ev.index("freeze") < ev.index("test_open") < ev.index("test_scored") < ev.index("test_open_unseen")
    assert [e for e in result.events if e["event"] == "test_open"][0]["freeze_sha256"] == result.report["freeze"]["freeze_sha256"]


def test_predictions_have_one_row_per_record_and_model_and_valid_probabilities(happy):
    cfg, loaded, result, order = happy
    pred = result.predictions
    n_val, n_test = len(loaded.frames["validation"]), len(loaded.frames["test"]._frame)
    n_unseen = sum(len(f) for f in loaded.unseen_frames.values())
    assert len(pred) == 5 * (n_val + n_test) + n_unseen and not pred.duplicated(["warehouse_record_id", "model_id", "split"]).any()
    extra = pred[~pred["is_primary"]]
    assert len(extra) == n_unseen and extra["model_id"].nunique() == 1 and set(extra["role"]) == {"champion,finalist"} and set(extra["hotel_id"]) == {"hotel_unseen"}
    pred = pred[pred["is_primary"]].reset_index(drop=True)                                 # cac kiem tra con lai tren mau so primary
    assert set(pred["split"]) == {"validation", "test"} and pred["model_id"].nunique() == 5
    p = pred[["p_down", "p_stable", "p_up"]].to_numpy()
    assert np.isfinite(p).all() and np.allclose(p.sum(axis=1), 1, atol=1e-5) and ((p >= 0) & (p <= 1)).all()
    assert (pred["pred_class"].to_numpy() == p.argmax(axis=1)).all() and set(pred["true_class"].unique()) == {0, 1, 2}
    champ_rows = pred[pred["role"] == "champion,finalist"]
    assert champ_rows["model_id"].nunique() == 1                                          # champion khong bi nhan doi du la finalist
    for col in ("hotel_id", "vn_observation_date", "inference_mode", "lead_time_bucket", "city"):
        assert col in pred.columns


def test_report_metrics_equal_metrics_recomputed_from_saved_predictions(happy):
    cfg, loaded, result, order = happy
    pred = result.predictions[result.predictions["is_primary"]]                              # bao cao tinh tren mau so primary
    for model_id, summary in result.report["test"]["models"].items():
        rows = pred[(pred["model_id"] == model_id) & (pred["split"] == "test")]
        s = M.summarize(rows["true_class"].to_numpy(), rows["pred_class"].to_numpy())
        assert np.isclose(s["macro_f1"], summary["macro_f1"]) and np.isclose(s["mcc"], summary["mcc"])
        assert np.isclose(M.log_loss(rows["true_class"], rows[["p_down", "p_stable", "p_up"]]), summary["log_loss"])
    for fin in result.report["finalists"]:
        rows = pred[(pred["model_id"] == fin["id"]) & (pred["split"] == "validation")]
        assert np.isclose(M.summarize(rows["true_class"], rows["pred_class"])["macro_f1"], fin["validation"]["macro_f1"])


def test_champion_is_the_best_validation_finalist_and_ablations_reuse_its_hyperparameters(happy):
    cfg, loaded, result, order = happy
    rep = result.report
    best = max(rep["finalists"], key=lambda f: (f["validation"]["macro_f1"], f["validation"]["mcc"]))
    assert rep["champion"] == best["id"] or abs(best["validation"]["macro_f1"] - next(f for f in rep["finalists"] if f["id"] == rep["champion"])["validation"]["macro_f1"]) <= 1e-9
    champ, a1, a2 = rep["models"][rep["champion"]], rep["models"][f"{rep['champion']}+a1_ratio"], rep["models"][f"{rep['champion']}+a2_no_history"]
    assert a1["params"] == champ["params"] == a2["params"] and a1["family"] == champ["family"] == a2["family"] and a1["candidate_id"] == champ["candidate_id"]
    assert (champ["n_features"], a1["n_features"], a2["n_features"]) == (15, 21, 6)
    assert a1["feature_set"] == "a1_ratio" and a2["feature_set"] == "a2_no_history" and a1["fitted_on"] == a2["fitted_on"] == "train_only"
    assert rep["ablation"]["a1"]["status"] == rep["ablation"]["a2"]["status"] == "ok"


def test_runs_are_deterministic(happy, monkeypatch):
    cfg, loaded, result, order = happy
    again = R.run_horizon(runner_config(), make_loaded())
    pd.testing.assert_frame_equal(result.predictions.reset_index(drop=True), again.predictions.reset_index(drop=True))
    assert again.report["freeze"]["freeze_sha256"] == result.report["freeze"]["freeze_sha256"]


def test_no_valid_model_never_opens_test_and_reports_clearly(monkeypatch):
    def broken(params, fixed, *, seed, device, n_jobs):
        raise RuntimeError("khong fit duoc")
    for fam in ("lr", "hgb", "xgb"):
        monkeypatch.setitem(C.ESTIMATOR_FACTORIES, fam, broken)
    cfg, loaded = runner_config(), make_loaded()
    order: list[str] = []
    loaded.frames["test"] = Spy(loaded.frames["test"], order)
    result = R.run_horizon(cfg, loaded)
    rep = result.report
    assert rep["status"] == "no_valid_model" and rep["champion"] is None and rep["contract_complete"] is False and rep["freeze"] is None and rep["ablation"] is None and rep["test"] is None
    assert result.predictions is None and result.bundle is None and order == []                      # khong ai cham den khung test
    assert {i["status"] for i in rep["selection"]["families"].values()} == {"no_valid_model"}


def test_one_family_failing_does_not_hide_incompleteness(monkeypatch):
    def broken(params, fixed, *, seed, device, n_jobs):
        raise RuntimeError("xgb hong")
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", broken)
    result = R.run_horizon(runner_config(), make_loaded())
    rep = result.report
    assert rep["status"] == "incomplete" and rep["contract_complete"] is False and any("xgb" in r for r in rep["incomplete_reasons"])
    assert rep["selection"]["families"]["xgb"]["status"] == "no_valid_model" and len(rep["selection"]["finalists"]) == 2 and rep["champion"] in rep["selection"]["finalists"]


def test_soft_budget_exhausted_after_finalists_marks_ablations_not_run(monkeypatch):
    now = {"t": 0.0}
    real_fit = R.fit_on_train

    def slow_fit(*a, **k):
        now["t"] += 1000.0                                                   # moi lan fit finalist/ablation "ton" 1000 giay
        return real_fit(*a, **k)

    monkeypatch.setattr(R, "fit_on_train", slow_fit)
    cfg = runner_config()
    cfg["run"]["wall_clock_soft_cap_seconds"] = 2500
    result = R.run_horizon(cfg, make_loaded(), clock=lambda: now["t"])
    rep = result.report
    assert rep["ablation"]["a1"]["status"] == "not_run" and rep["ablation"]["a2"]["status"] == "not_run"
    assert rep["status"] == "incomplete" and rep["contract_complete"] is False
    assert any("ablation a1" in r for r in rep["incomplete_reasons"]) and any("ablation a2" in r for r in rep["incomplete_reasons"])
    assert set(rep["models"]) == set(rep["selection"]["finalists"]) and rep["test"]["opened_after_freeze"] is True       # core van duoc cham, ablation khong gia vo da chay


# ----------------------------------------------------------------- giao dich / resume / bundle

def test_execute_horizon_commits_verifiable_run_and_bundle_reproduces_champion(tmp_path, monkeypatch):
    cfg, loaded = runner_config(), make_loaded()
    run_dir = R.execute_horizon(cfg, loaded, tmp_path, run_id="t1")
    manifest = T.verify_run_dir(run_dir)
    names = set(manifest["files"])
    assert {"h1_report.json", "h1_predictions.parquet", "CODE_MANIFEST.json", "environment_resolved.txt", "config_resolved.json"} <= names and any(n.startswith("h1_bundle_") for n in names)
    assert manifest["identity"]["horizon"] == 1 and manifest["identity"]["classes"] == ["down", "stable", "up"] and manifest["status"] == "complete"
    report = json.loads((run_dir / "h1_report.json").read_text(encoding="utf-8"))
    bundle = joblib.load(next(run_dir.glob("h1_bundle_*.joblib")))
    assert bundle["model_id"] == report["champion"] and bundle["probability_status"] == "uncalibrated" and bundle["fitted_on"] == "train_only"
    val = stable_sort(loaded.frames["validation"])
    proba = bundle["estimator"].predict_proba(bundle["matrix"].transform(val))
    pred = pd.read_parquet(run_dir / "h1_predictions.parquet")
    rows = pred[(pred["model_id"] == bundle["model_id"]) & (pred["split"] == "validation") & pred["is_primary"]].sort_values("warehouse_record_id")
    order = np.argsort(val["warehouse_record_id"].to_numpy(), kind="mergesort")
    assert np.allclose(proba[order], rows[["p_down", "p_stable", "p_up"]].to_numpy(), atol=0, rtol=0)


def test_resume_skips_only_a_valid_complete_run_with_identical_identity(tmp_path, monkeypatch):
    cfg = runner_config()
    first = R.execute_horizon(cfg, make_loaded(), tmp_path, run_id="a")
    monkeypatch.setattr(R, "run_horizon", lambda *a, **k: (_ for _ in ()).throw(AssertionError("khong duoc chay lai")))
    assert R.execute_horizon(cfg, make_loaded(), tmp_path, run_id="b") == first                       # resume nguyen horizon
    monkeypatch.undo()
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)
    cfg2 = runner_config()
    cfg2["seed"] = 123                                                                                 # khac config => khong resume
    cfg2["config_sha256"] = "z" * 64
    second = R.execute_horizon(cfg2, make_loaded(), tmp_path, run_id="c")
    assert second != first and second.name == "run_c"


def test_resume_rejects_tampered_or_incomplete_runs(tmp_path):
    cfg = runner_config()
    run = R.execute_horizon(cfg, make_loaded(), tmp_path, run_id="a")
    ident = T.verify_run_dir(run)["identity"]
    assert T.find_complete_run(tmp_path, ident) == run
    (run / "h1_report.json").write_bytes((run / "h1_report.json").read_bytes() + b" ")
    assert T.find_complete_run(tmp_path, ident) is None
    with pytest.raises(T.TransactionError, match="hash"):
        T.verify_run_dir(run)


def test_transaction_semantics(tmp_path):
    tx = T.RunTransaction(tmp_path, "x")
    tx.write_json("a.json", {"n": np.int64(3), "f": np.float64(0.5), "b": np.bool_(True), "arr": np.arange(3)})
    assert json.loads((tx.staging / "a.json").read_text(encoding="utf-8")) == {"arr": [0, 1, 2], "b": True, "f": 0.5, "n": 3}
    with pytest.raises(TypeError):
        tx.write_json("bad.json", {"x": object()})
    with pytest.raises(ValueError):
        tx.write_json("nan.json", {"x": float("nan")})
    with pytest.raises(T.TransactionError):
        tx.path("COMPLETE")
    (tx.staging / "bad.json").unlink(missing_ok=True)
    (tx.staging / "nan.json").unlink(missing_ok=True)
    final = tx.commit({"k": 1})
    assert final.name == "run_x" and not tx.staging.exists() and (final / "COMPLETE").is_file()
    with pytest.raises(T.TransactionError, match="da ton tai"):
        T.RunTransaction(tmp_path, "x")
    (final / "extra.txt").write_text("x", encoding="utf-8")
    with pytest.raises(T.TransactionError, match="ngoai manifest"):
        T.verify_run_dir(final)
    (final / "extra.txt").unlink()
    (final / "COMPLETE").unlink()
    with pytest.raises(T.TransactionError, match="chua hoan tat"):
        T.verify_run_dir(final)
    assert T.find_complete_run(tmp_path, {"k": 1}) is None
    tx2 = T.RunTransaction(tmp_path, "y")
    tx2.abort()
    assert not tx2.staging.exists()


def test_preexisting_staging_dir_is_an_error_and_is_never_deleted(tmp_path, monkeypatch):
    """Staging co san co the la cua tien trinh khac: bao loi ro, KHONG xoa (trap moi ham xoa de quy)."""
    deleted = []
    monkeypatch.setattr(T.shutil, "rmtree", lambda *a, **k: deleted.append(a))
    (tmp_path / ".staging_q").mkdir()
    (tmp_path / ".staging_q" / "junk.txt").write_text("x", encoding="utf-8")
    with pytest.raises(T.TransactionError, match="da ton tai"):
        T.RunTransaction(tmp_path, "q")
    assert (tmp_path / ".staging_q" / "junk.txt").is_file() and deleted == []
    assert T.find_complete_run(tmp_path, {"k": 1}) is None
