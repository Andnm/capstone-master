"""Analysis tren du lieu tong hop: cong validation, trang thai cung-doi-chung, thay the, phan ra, canh bao khop record."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cls_training import analysis as A
from cls_training import artifact as AE
from cls_training import snapshot
from cls_training.snapshot import export_snapshot, load_snapshot

from synth import small_config
from test_panel_snapshot import base_report, make_loaded, make_regression_run, write_run

H = 1


@pytest.fixture()
def world(tmp_path):
    loaded, frame = make_loaded(300, 300, seed=4, n_hotels=40)
    run = make_regression_run(tmp_path, frame)
    out = tmp_path / "snap"
    export_snapshot(loaded, run, out, export_code_files={"snapshot.py": Path(snapshot.__file__)})
    snap, manifest = load_snapshot(out, loaded, pin=None, allow_unpinned=True)
    return loaded, frame, run, snap, manifest


def champion_rows(snap, pred_fn, model_id="m1", role="champion,finalist"):
    rows = []
    rng = np.random.default_rng(0)
    for sp in ("validation", "test"):
        sn = snap[snap["split"] == sp].sort_values("warehouse_record_id", kind="mergesort").reset_index(drop=True)
        true = pd.Series(sn["y_direction"]).map({"down": 0, "stable": 1, "up": 2}).to_numpy()
        pred = pred_fn(true)
        proba = np.full((len(sn), 3), 0.05)
        proba[np.arange(len(sn)), pred] = 0.9
        rows.append(pd.DataFrame({"warehouse_record_id": sn["warehouse_record_id"].to_numpy(), "horizon": H, "split": sp, "model_id": model_id, "role": role,
                                  "true_class": true.astype("int8"), "pred_class": pred.astype("int8"), "p_down": proba[:, 0], "p_stable": proba[:, 1], "p_up": proba[:, 2], "is_primary": True,
                                  "hotel_id": sn["hotel_id"].to_numpy(), "vn_observation_date": sn["vn_observation_date"].to_numpy(),
                                  "inference_mode": np.where(np.arange(len(sn)) % 3 == 0, "cold_start", "history_enriched"), "city": rng.choice(["A", "B"], len(sn)),
                                  "lead_time_bucket": rng.choice(["lt3", "3-7"], len(sn)), f"y_pct_change_h{H}": (sn["y_true"] / sn["current_price"] - 1).to_numpy()}))
    return pd.concat(rows, ignore_index=True)


def report_for(model_id="m1", ablation=None):
    models = {model_id: {"roles": ["champion", "finalist"]}}
    for abl in (ablation or {}).values():
        if abl.get("status") == "ok":
            models[abl["model_id"]] = {"roles": [f"ablation_{abl['feature_set'].split('_')[0]}"]}
    return {"horizon": H, "champion": model_id, "status": "complete", "contract_complete": True, "ablation": ablation or {}, "models": models}


def test_perfect_champion_passes_the_validation_gate_and_reports_same_comparator_status(world):
    loaded, frame, run, snap, manifest = world
    cfg = small_config("cpu")
    out = A.analyze_horizon(champion_rows(snap, lambda t: t), report_for(), snap, manifest, cfg)
    c = out["claim"]
    assert c["validation_gate_passed"] is True and out["claim_level"] == "dev_exploratory_validation_gain" and c["validation_delta"] >= cfg["panel"]["practical_gain_macro_f1"]
    assert c["validation_delta_ci95"][0] > 0 and all(r == 1.0 for r in c["recall_validation"].values())
    assert c["is_substitute"] is False and c["same_comparator_test_status"] == "positive_on_exposed_test" and c["test_delta_vs_validation_comparator"] > 0
    assert c["test_available_comparator_id"] == c["validation_comparator_id"] and "dev/exploratory" in " ".join(c["caveats"])


def test_all_stable_champion_does_not_pass_gate(world):
    loaded, frame, run, snap, manifest = world
    out = A.analyze_horizon(champion_rows(snap, lambda t: np.ones_like(t)), report_for(), snap, manifest, small_config("cpu"))
    assert out["claim"]["validation_gate_passed"] is False and out["claim_level"] == "none" and out["claim"]["validation_delta"] < 0


def test_gate_requires_every_class_to_have_positive_recall(world):
    loaded, frame, run, snap, manifest = world
    never_down = lambda t: np.where(t == 0, 1, t)                                            # hoan hao tru viec khong bao gio du doan 'down'
    out = A.analyze_horizon(champion_rows(snap, never_down), report_for(), snap, manifest, small_config("cpu"))
    assert out["claim"]["recall_validation"]["down"] == 0.0 and out["claim"]["validation_gate_passed"] is False and out["claim_level"] == "none"


def test_gain_below_practical_threshold_fails_even_with_positive_delta(world):
    loaded, frame, run, snap, manifest = world
    rng = np.random.default_rng(1)
    cfg = small_config("cpu")
    cfg["panel"]["practical_gain_macro_f1"] = 0.99                                            # chi de kiem tra nhanh quy tac nguong
    out = A.analyze_horizon(champion_rows(snap, lambda t: np.where(rng.random(len(t)) < 0.97, t, 1)), report_for(), snap, manifest, cfg)
    assert out["claim"]["validation_delta"] > 0 and out["claim"]["validation_gate_passed"] is False


def test_substitute_comparator_leaves_same_comparator_delta_null_and_status_not_evaluable(tmp_path):
    loaded, frame = make_loaded(300, 300, seed=4, n_hotels=40)
    run = make_regression_run(tmp_path, frame)
    pr = pd.read_parquet(run / f"h{H}_predictions_v3.parquet")
    val = (pr["split"] == "validation").to_numpy()
    pr["pred_unscored"] = np.where(val, pr["y_true"].to_numpy(), np.nan)                      # hoan hao tren validation, khong duoc cham test
    write_run(run, pr, base_report([c for c in pr.columns if c.startswith("pred_")]))
    out_dir = tmp_path / "snap"
    export_snapshot(loaded, run, out_dir, export_code_files={})
    snap, manifest = load_snapshot(out_dir, loaded, pin=None, allow_unpinned=True)
    out = A.analyze_horizon(champion_rows(snap, lambda t: t), report_for(), snap, manifest, small_config("cpu"))
    c = out["claim"]
    assert c["validation_comparator_id"] == "C2:pred_unscored" and c["is_substitute"] is True
    assert c["test_delta_vs_validation_comparator"] is None and c["test_delta_vs_validation_comparator_status"] == "not_scored_on_test"
    assert c["same_comparator_test_status"] == "not_evaluable"
    assert c["test_available_comparator_id"] != c["validation_comparator_id"] and c["test_delta_vs_available_comparator"] is not None
    assert out["champion_vs_panel"]["C2:pred_unscored"]["test"] is None                      # khong co so lieu test cho thanh vien chua duoc cham


def test_breakdowns_equal_hotel_leave_one_out_tails_and_probability_diagnostics_are_present(world):
    loaded, frame, run, snap, manifest = world
    out = A.analyze_horizon(champion_rows(snap, lambda t: t), report_for(), snap, manifest, small_config("cpu"))
    for sp in ("validation", "test"):
        b = out["breakdowns"][sp]
        for key in ("inference_mode", "lead_time_bucket", "city", "vn_observation_date", "tails", "equal_hotel", "leave_one_out", "probability"):
            assert key in b
        assert {g["group"] for g in b["inference_mode"]} == {"cold_start", "history_enriched"}
        assert b["leave_one_out"]["additive"] is False and b["equal_hotel"]["candidate"]["n_hotels"] == 40
        assert b["probability"]["status"] == "uncalibrated" and len(b["probability"]["reliability"]) == 2 and b["probability"]["log_loss"] >= 0
        assert set(b["tails"]) == {"abs_pct_gt_20", "abs_pct_gt_50"}
        for g in b["vn_observation_date"]:
            assert g["null_reason"] is None or g["null_reason"].startswith("missing_class_support")      # nhom thieu lop => null co ly do
            if g["null_reason"]:
                assert g["macro_f1"] is None


def test_alignment_errors_are_detected(world):
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: t)
    with pytest.raises(AE.ArtifactError, match="tap record"):
        A.analyze_horizon(pred.iloc[:-3], report_for(), snap, manifest, small_config("cpu"))
    bad = pred.copy()
    bad.loc[bad.index[0], "true_class"] = (bad.loc[bad.index[0], "true_class"] + 1) % 3
    with pytest.raises(AE.ArtifactError, match="true_class"):
        A.analyze_horizon(bad, report_for(), snap, manifest, small_config("cpu"))


def test_no_champion_yields_claim_none_without_touching_snapshot(world):
    loaded, frame, run, snap, manifest = world
    out = A.analyze_horizon(pd.DataFrame(), {"horizon": H, "champion": None, "status": "no_valid_model"}, snap, manifest, small_config("cpu"))
    assert out["claim_level"] == "none" and out["validation_gate_passed"] is False and out["reason"] == "no_valid_model"


def test_ablation_delta_is_ablation_minus_champion_with_ci(world):
    loaded, frame, run, snap, manifest = world
    champ = champion_rows(snap, lambda t: t)
    weaker = champion_rows(snap, lambda t: np.ones_like(t), model_id="m1+a2_no_history", role="ablation_a2")
    pred = pd.concat([champ, weaker], ignore_index=True)
    rep = report_for(ablation={"a2": {"status": "ok", "model_id": "m1+a2_no_history", "feature_set": "a2_no_history"}, "a1": {"status": "not_run", "reason": "budget"}})
    out = A.analyze_horizon(pred, rep, snap, manifest, small_config("cpu"))
    a2 = out["ablations_vs_champion"]["a2"]
    assert a2["validation"]["point"] < 0 and a2["test"]["point"] < 0 and "ablation" in a2["validation"]["definition"]
    assert out["ablations_vs_champion"]["a1"] == {"status": "not_run", "reason": "budget"}


def test_all_hotels_denominator_adds_unseen_rows_and_compares_with_c0_and_c1(world):
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: t)
    rng = np.random.default_rng(3)
    pred["is_primary"] = True
    pred["price_velocity"] = rng.normal(0, 0.05, len(pred))
    extra = pred.groupby("split").head(5).copy()
    extra["is_primary"] = False
    extra["hotel_id"] = "hotel_unseen"
    extra["warehouse_record_id"] = extra["warehouse_record_id"] + 900_000
    full = pd.concat([pred, extra], ignore_index=True)
    out = A.analyze_horizon(full, report_for(), snap, manifest, small_config("cpu"))
    for sp in ("validation", "test"):
        a = out["all_hotels"][sp]
        assert a["n_unseen"] == 5 and a["n_all"] == a["n_primary"] + 5 == out["denominators"]["primary"][sp] + 5
        assert a["champion"]["macro_f1"] == 1.0 and set(a) >= {"C0:always_stable", "C1:momentum"} and a["C0:always_stable"]["delta"]["point"] > 0
    # hang unseen khong lam doi phan tich primary
    base = A.analyze_horizon(pred, report_for(), snap, manifest, small_config("cpu"))
    assert out["claim"] == base["claim"]


# ----------------------------------------------------------------- validator artifact (GPT 12 M2)

def _cfg():
    return small_config("cpu")


def test_pred_class_must_equal_argmax_of_saved_probabilities_before_any_claim(world):
    """Phan vi du GPT: xac suat LUON stable nhung pred_class bi sua thanh nhan that => truoc day 'validation_gate_passed=True'."""
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: np.ones_like(t))                                         # xac suat toan 'stable'
    true = pred["true_class"].to_numpy()
    forged = pred.copy()
    forged["pred_class"] = true.astype("int8")                                                     # F1 hoan hao gia
    with pytest.raises(AE.ArtifactError, match="argmax"):
        A.analyze_horizon(forged, report_for(), snap, manifest, _cfg())


def test_hotel_id_edit_with_same_ids_and_labels_is_rejected_before_bootstrap(world):
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: t)
    pred.loc[pred.index[:5], "hotel_id"] = "forged_hotel"
    with pytest.raises(AE.ArtifactError, match="metadata hotel_id"):
        A.analyze_horizon(pred, report_for(), snap, manifest, _cfg())


def test_duplicate_rows_other_horizon_and_unknown_model_are_rejected(world):
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: t)
    with pytest.raises(AE.ArtifactError, match="trung khoa"):
        A.analyze_horizon(pd.concat([pred, pred.head(2)], ignore_index=True), report_for(), snap, manifest, _cfg())
    other = pred.copy()
    other.loc[other.index[0], "horizon"] = 3
    with pytest.raises(AE.ArtifactError, match="khac horizon"):
        A.analyze_horizon(other, report_for(), snap, manifest, _cfg())
    stray = pd.concat([pred, champion_rows(snap, lambda t: t, model_id="stray", role="finalist")], ignore_index=True)
    with pytest.raises(AE.ArtifactError, match="ngoai report.models"):
        A.analyze_horizon(stray, report_for(), snap, manifest, _cfg())


def test_invalid_probabilities_classes_and_flags_are_rejected(world):
    loaded, frame, run, snap, manifest = world
    base = champion_rows(snap, lambda t: t)
    for mutate, match in ((lambda d: d.__setitem__("p_down", d["p_down"] * 0.5), "tong hang"), (lambda d: d.loc.__setitem__((d.index[0], "p_down"), np.nan), "huu han"),
                          (lambda d: d.loc.__setitem__((d.index[0], "true_class"), 3), "so nguyen"), (lambda d: d.__setitem__("is_primary", d["is_primary"].astype(object).map({True: 1})), "bool")):
        bad = base.copy()
        mutate(bad)
        with pytest.raises(AE.ArtifactError, match=match):
            A.analyze_horizon(bad, report_for(), snap, manifest, _cfg())


def test_role_mismatch_unseen_rows_by_non_champion_and_count_contract(world):
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: t)
    rep = report_for()
    bad_role = pred.copy()
    bad_role["role"] = "finalist"
    with pytest.raises(AE.ArtifactError, match="role"):
        A.analyze_horizon(bad_role, rep, snap, manifest, _cfg())
    extra = pred.groupby("split").head(2).copy()
    extra["is_primary"], extra["warehouse_record_id"], extra["model_id"] = False, extra["warehouse_record_id"] + 900_000, "other"
    rep2 = {**rep, "models": {**rep["models"], "other": {"roles": ["finalist"]}}}
    with pytest.raises(AE.ArtifactError, match="unseen"):
        A.analyze_horizon(pd.concat([pred, extra.assign(role="finalist")], ignore_index=True), rep2, snap, manifest, _cfg())
    ok_extra = pred.groupby("split").head(2).copy()
    ok_extra["is_primary"], ok_extra["warehouse_record_id"] = False, ok_extra["warehouse_record_id"] + 900_000
    rep3 = {**rep, "test": {"unseen_hotel_rows_champion_only": {"validation": 2, "test": 3}}}
    with pytest.raises(AE.ArtifactError, match="unseen"):
        A.analyze_horizon(pd.concat([pred, ok_extra], ignore_index=True), rep3, snap, manifest, _cfg())


def test_breakdowns_carry_confusion_and_per_class_for_candidate_and_comparator(world):
    loaded, frame, run, snap, manifest = world
    out = A.analyze_horizon(champion_rows(snap, lambda t: t), report_for(), snap, manifest, small_config("cpu"))
    for g in out["breakdowns"]["validation"]["inference_mode"]:
        assert np.asarray(g["confusion"]).shape == (3, 3) and np.asarray(g["comparator_confusion"]).shape == (3, 3)
        assert {"precision", "recall", "f1"} <= set(g["per_class"]["down"]) and set(g["comparator_per_class"]) == {"down", "stable", "up"}
    assert out["artifact_validation"]["rows"]["test"]["m1"] > 0


# ----------------------------------------------------------------- M3 (GPT 14): run incomplete khong co claim cuoi duong

@pytest.mark.parametrize("status,complete", [("incomplete", False), ("complete", False), ("incomplete", True)])
def test_incomplete_training_never_gets_a_positive_final_claim_but_keeps_diagnostic_numbers(world, status, complete):
    loaded, frame, run, snap, manifest = world
    pred = champion_rows(snap, lambda t: t)                                                    # classifier hoan hao
    rep = {**report_for(), "status": status, "contract_complete": complete, "incomplete_reasons": ["family xgb: incomplete"]}
    out = A.analyze_horizon(pred, rep, snap, manifest, small_config("cpu"))
    c = out["claim"]
    assert out["claim_level"] == "none" and c["claim_level"] == "none" and c["validation_gate_passed"] is False
    assert c["diagnostic_validation_gate_passed"] is True and c["claim_block_reasons"] == ["training_contract_incomplete"]
    assert c["validation_delta"] > 0 and c["validation_delta_ci95"][0] > 0                      # so do van duoc giu de chan doan
    done = A.analyze_horizon(pred, report_for(), snap, manifest, small_config("cpu"))             # run hoan tat dung hop dong: semantics gate khong doi
    assert done["claim_level"] == "dev_exploratory_validation_gain" and done["claim"]["claim_block_reasons"] == [] and done["claim"]["diagnostic_validation_gate_passed"] is True


def test_every_model_must_match_the_snapshot_metadata_not_only_the_champion(world):
    loaded, frame, run, snap, manifest = world
    champ = champion_rows(snap, lambda t: t)
    abl = champion_rows(snap, lambda t: t, model_id="m1+a2_no_history", role="ablation_a2")
    abl.loc[abl.index[:4], "hotel_id"] = "forged_hotel"                                           # chi hang cua ablation (champion nguyen ven)
    rep = report_for(ablation={"a2": {"status": "ok", "model_id": "m1+a2_no_history", "feature_set": "a2_no_history"}})
    with pytest.raises(AE.ArtifactError, match="m1\\+a2_no_history.*hotel_id|hotel_id.*m1\\+a2_no_history"):
        A.analyze_horizon(pd.concat([champ, abl], ignore_index=True), rep, snap, manifest, small_config("cpu"))
