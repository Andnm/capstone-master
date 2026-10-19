"""Panel + snapshot tren du lieu TONG HOP (khong can dataset that): 'NaN = chua duoc cham' doi chieu authority cua report, gia > 0, bitwise, pin bat buoc, run_manifest."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from cls_training import panel, snapshot
from cls_training.labels import LabelContractError, direction_from_pct, pct_change
from cls_training.snapshot import KEY, SnapshotError, bitwise_equal, export_snapshot, expected_scored_splits, load_snapshot, scored_splits
from cls_training.upstream.provenance import file_sha256

H = 1
SAMPLES_SHA = "c" * 64
PRESPEC = ["persistence", "good", "alias"]                     # duoc cham tren test; 'unscored' thi khong


def make_loaded(n_val=60, n_test=60, seed=0, n_hotels=9):
    rng = np.random.default_rng(seed)
    n = n_val + n_test
    cur = rng.uniform(2e5, 3e6, n).round(0)
    factor = rng.choice([1.0, 1.0, 1.0, 1.0, 1.04, 0.95, 1.5, 0.7, 1.02, 0.98], size=n)
    y = (cur * factor).round(0)
    split = ["validation"] * n_val + ["test"] * n_test
    frame = pd.DataFrame({"warehouse_record_id": np.arange(1000, 1000 + n, dtype="int64"), "hotel_id": [f"h{i % n_hotels}" for i in range(n)],
                          "checkin_date": pd.to_datetime("2026-10-10") + pd.to_timedelta(np.arange(n) % 7, unit="D"),
                          "canonical_series_id": [f"s{i}" for i in range(n)],
                          "vn_observation_date": pd.to_datetime("2026-09-20") + pd.to_timedelta(np.arange(n) % 5, unit="D"),
                          "split": split, "current_price": cur, f"y_price_h{H}": y, "price_velocity": np.where(rng.random(n) < 0.1, np.nan, rng.normal(0, 0.05, n))})
    frame[f"y_direction_h{H}"] = direction_from_pct(pct_change(y, cur))
    frames = {"train": frame.iloc[:0], "validation": frame[frame["split"] == "validation"], "test": frame[frame["split"] == "test"]}
    pin = {"dataset_version": "ds_fake", "output_checksums_sha256": "a" * 64, "dataset_contract_sha256": "b" * 64, "samples_file_sha256": SAMPLES_SHA}
    return SimpleNamespace(horizon=H, frames=frames, pin=pin, meta={"dataset_name": "ds_fake", "samples_file_sha256": SAMPLES_SHA}), frame


def write_run(run, pr, report):
    """Ghi file du doan + report + run_manifest nhat quan (hash that)."""
    pr_path, rp_path = run / f"h{H}_predictions_v3.parquet", run / f"h{H}_report.json"
    pr.to_parquet(pr_path, index=False)
    rp_path.write_text(json.dumps(report), encoding="utf-8")
    rm = {"state": "pass", "expected_horizons": [H], "official": False, "run_id": "fake", "dataset": {"samples_file_sha256": SAMPLES_SHA},
          "outputs": {pr_path.name: file_sha256(pr_path), rp_path.name: file_sha256(rp_path)}}
    (run / "run_manifest.json").write_text(json.dumps(rm), encoding="utf-8")


def base_report(pr_cols, prespec=PRESPEC, locked=True):
    return {"horizon": H, "config_sha256": "d" * 64, "training_version": "fake", "dataset": {"samples_file_sha256": SAMPLES_SHA},
            "champions": {"A": {"winner": "good"}, "B": {"winner": "good"}}, "test": {"locked": locked, "prespecified_models": list(prespec)},
            "validation_table": {c[len("pred_"):]: {} for c in pr_cols}}


def make_regression_run(tmp_path, frame, seed=1):
    """'good' va 'alias' (= ban sao) va 'persistence' co ca val+test; 'unscored' chi co validation (NaN toan bo test)."""
    rng = np.random.default_rng(seed)
    pr = frame[KEY + ["current_price"]].copy()
    pr["checkin_date"] = pr["checkin_date"].dt.date
    pr["vn_observation_date"] = pr["vn_observation_date"].dt.date
    pr["y_true"] = frame[f"y_price_h{H}"].to_numpy()
    pr["pred_persistence"] = frame["current_price"].to_numpy()
    good = frame["current_price"].to_numpy() * rng.choice([1.0, 1.05, 0.95, 1.5], size=len(frame))
    pr["pred_good"] = good
    pr["pred_alias"] = good
    unscored = frame["current_price"].to_numpy() * rng.choice([1.0, 1.1, 0.9], size=len(frame))
    pr["pred_unscored"] = np.where(frame["split"] == "test", np.nan, unscored)
    run = tmp_path / "run_fake"
    run.mkdir()
    pred_cols = [c for c in pr.columns if c.startswith("pred_")]
    write_run(run, pr, base_report(pred_cols))
    return run


@pytest.fixture()
def exported(tmp_path):
    loaded, frame = make_loaded()
    run = make_regression_run(tmp_path, frame)
    out = tmp_path / "snap"
    manifest = export_snapshot(loaded, run, out, export_code_files={"snapshot.py": Path(snapshot.__file__)})
    return loaded, frame, run, out, manifest


def pin_for(out):
    pq, mf = snapshot.snapshot_paths(out, H)
    return {"horizon": H, "snapshot_file_sha256": file_sha256(pq), "manifest_sha256": file_sha256(mf)}


def test_export_and_load_roundtrip_with_pin(exported):
    loaded, frame, run, out, manifest = exported
    assert manifest["rows"] == 120 and manifest["prediction_scored_splits"]["pred_unscored"] == ["validation"]
    assert manifest["prediction_scored_splits"]["pred_good"] == ["validation", "test"] and manifest["champions"] == {"A": "good", "B": "good"}
    assert manifest["source"]["run_manifest"]["state"] == "pass" and manifest["expected_scoredness_source"]["prespecified_models"] == PRESPEC
    snap, m = load_snapshot(out, loaded, pin=pin_for(out))
    assert len(snap) == 120 and m["_validated"]["pin_checked"] is True and "price_velocity" in snap.columns


def test_unpinned_load_is_refused_unless_explicitly_allowed_for_tests(exported):
    loaded, frame, run, out, manifest = exported
    with pytest.raises(SnapshotError, match="thieu pin"):
        load_snapshot(out, loaded, pin=None)
    snap, m = load_snapshot(out, loaded, pin=None, allow_unpinned=True)
    assert m["_validated"]["pin_checked"] is False


def test_nan_means_not_scored_never_stable():
    snap = pd.DataFrame({"split": ["validation"] * 3 + ["test"] * 3, "current_price": [100.0] * 6, "pred_x": [120.0, 100.0, 80.0, np.nan, np.nan, np.nan]})
    assert scored_splits(snap, ["pred_x"]) == {"pred_x": ["validation"]}
    bad = snap.copy()
    bad.loc[4, "pred_x"] = 101.0
    with pytest.raises(SnapshotError, match="NaN mot phan"):
        scored_splits(bad, ["pred_x"])


@pytest.mark.parametrize("bad_value,match", [(np.inf, "vo cuc"), (-np.inf, "vo cuc"), (0.0, "> 0"), (-5.0, "> 0")])
def test_scored_predictions_must_be_finite_positive_prices(bad_value, match):
    snap = pd.DataFrame({"split": ["validation"] * 3 + ["test"] * 3, "pred_x": [120.0, 100.0, bad_value, 80.0, 90.0, 95.0]})
    with pytest.raises(SnapshotError, match=match):
        scored_splits(snap, ["pred_x"])


def test_bitwise_equal_is_stricter_than_numeric_equality():
    assert bitwise_equal(np.array([1.5, 2.0]), np.array([1.5, 2.0]))
    assert not bitwise_equal(np.array([0.0]), np.array([-0.0]))                  # == la True, bit khac nhau
    assert not bitwise_equal(np.array([1.0]), np.array([1.0 + 2 ** -52]))
    with pytest.raises(SnapshotError, match="float64"):
        bitwise_equal(np.array([1.0], dtype=np.float32), np.array([1.0], dtype=np.float32))
    with pytest.raises(SnapshotError, match="float64"):
        bitwise_equal(np.array([1]), np.array([1.0]))


def test_panel_marks_unscored_member_and_excludes_it_from_test_stats(exported):
    loaded, frame, run, out, manifest = exported
    snap, m = load_snapshot(out, loaded, pin=pin_for(out))
    result = panel.evaluate_panel(snap, m)
    rows = {r["id"]: r for r in result["table"]}
    assert rows["C2:pred_unscored"]["test"] is None and rows["C2:pred_unscored"]["validation"] is not None
    assert "C2:pred_unscored" in result["members_not_scored_on_test"]
    assert result["aliases"]["C2:pred_good"] == "C2:pred_alias" and result["aliases"]["C2:pred_persistence"] == "C0:always_stable"
    assert "C2:pred_good" not in rows and "C2:pred_persistence" not in rows and "C2:pred_alias" in rows
    assert result["c3_roles"]["C3-A"] == "pred_good" and result["c3_role_to_member"]["C3-A"] == "C2:pred_alias"


def test_substitute_comparator_when_validation_best_is_not_scored_on_test(tmp_path):
    loaded, frame = make_loaded(80, 80)
    run = make_regression_run(tmp_path, frame)
    pr = pd.read_parquet(run / f"h{H}_predictions_v3.parquet")
    val = (pr["split"] == "validation").to_numpy()
    pr["pred_unscored"] = np.where(val, pr["y_true"].to_numpy(), np.nan)               # hoan hao tren validation => thang gate validation, khong co test
    cols = [c for c in pr.columns if c.startswith("pred_")]
    write_run(run, pr, base_report(cols))
    out = tmp_path / "snap"
    export_snapshot(loaded, run, out, export_code_files={"snapshot.py": Path(snapshot.__file__)})
    snap, m = load_snapshot(out, loaded, pin=None, allow_unpinned=True)
    r = panel.evaluate_panel(snap, m)
    assert r["validation_comparator"] == "C2:pred_unscored" and r["validation_comparator_scored_on_test"] is False
    assert r["is_substitute"] is True and r["test_available_comparator"] != r["validation_comparator"] and r["test_available_comparator_test_macro_f1"] is not None


def test_same_comparator_status_semantics():
    assert panel.same_comparator_test_status(is_substitute=True, test_delta_vs_validation_comparator=None) == "not_evaluable"
    with pytest.raises(panel.PanelError, match="phai la None"):
        panel.same_comparator_test_status(is_substitute=True, test_delta_vs_validation_comparator=0.05)       # khong dien so thay the vao truong nay
    assert panel.same_comparator_test_status(is_substitute=False, test_delta_vs_validation_comparator=0.01) == "positive_on_exposed_test"
    assert panel.same_comparator_test_status(is_substitute=False, test_delta_vs_validation_comparator=0.0) == "not_reproduced_on_exposed_test"
    assert panel.same_comparator_test_status(is_substitute=False, test_delta_vs_validation_comparator=-0.02) == "not_reproduced_on_exposed_test"
    with pytest.raises(panel.PanelError):
        panel.same_comparator_test_status(is_substitute=False, test_delta_vs_validation_comparator=None)


def test_gate_tie_breaks_by_mcc_then_panel_order():
    n = 6
    snap = pd.DataFrame({"split": ["validation"] * n + ["test"] * n, "y_direction": ["down", "stable", "up", "stable", "stable", "stable"] * 2,
                         "current_price": [100.0] * (2 * n), "price_velocity": [np.nan] * (2 * n), "pred_a": [100.0] * (2 * n), "pred_b": [100.0] * (2 * n)})
    manifest = {"prediction_columns": ["pred_a", "pred_b"], "prediction_scored_splits": {"pred_a": ["validation", "test"], "pred_b": ["validation", "test"]},
                "champions": {"A": "a", "B": "b"}, "routed_columns": []}
    r = panel.evaluate_panel(snap, manifest)
    assert r["validation_comparator"] == "C0:always_stable" and set(r["aliases"]) == {"C1:momentum", "C2:pred_a", "C2:pred_b"}


def test_momentum_threshold_nan_stable_but_infinity_is_an_error():
    d = panel.momentum_direction(np.array([0.03, -0.03, 0.02, -0.02, 0.0, np.nan]))
    assert d.tolist() == ["up", "down", "stable", "stable", "stable", "stable"]
    for bad in ([np.inf], [-np.inf], [0.1, np.inf, np.nan]):
        with pytest.raises(panel.PanelError, match="vo cuc"):
            panel.momentum_direction(np.array(bad))


def test_champion_column_missing_is_error():
    with pytest.raises(panel.PanelError, match="khong co cot"):
        panel.champion_columns({"champions": {"A": "ghost", "B": "good"}}, ["pred_good"])
    with pytest.raises(panel.PanelError, match="khong co champion"):
        panel.champion_columns({"champions": {"A": None, "B": "good"}}, ["pred_good"])


# ---------------------------------------------------------------- negative tests: authority cua report / run_manifest (export)

def _fresh(tmp_path):
    loaded, frame = make_loaded()
    run = make_regression_run(tmp_path, frame)
    pr = pd.read_parquet(run / f"h{H}_predictions_v3.parquet")
    return loaded, frame, run, pr


def _export(tmp_path, loaded, run, tag="o"):
    return export_snapshot(loaded, run, tmp_path / tag, export_code_files={})


def test_export_rejects_all_nan_for_a_column_the_report_says_was_scored(tmp_path):
    loaded, frame, run, pr = _fresh(tmp_path)
    pr.loc[pr["split"] == "test", "pred_good"] = np.nan                    # report: 'good' prespecified (duoc cham) nhung gia tri mat toan bo test
    write_run(run, pr, base_report([c for c in pr.columns if c.startswith("pred_")]))
    with pytest.raises(SnapshotError, match="authority cua report"):
        _export(tmp_path, loaded, run)


def test_export_rejects_finite_values_for_a_column_the_report_says_was_not_scored(tmp_path):
    loaded, frame, run, pr = _fresh(tmp_path)
    pr["pred_unscored"] = frame["current_price"].to_numpy() * 1.1         # report: 'unscored' khong prespecified nhung co gia tri that tren test
    write_run(run, pr, base_report([c for c in pr.columns if c.startswith("pred_")]))
    with pytest.raises(SnapshotError, match="authority cua report"):
        _export(tmp_path, loaded, run)


@pytest.mark.parametrize("bad", [0.0, -1.0, np.inf])
def test_export_rejects_nonpositive_or_infinite_scored_price(tmp_path, bad):
    loaded, frame, run, pr = _fresh(tmp_path)
    pr.loc[pr.index[3], "pred_good"] = bad
    write_run(run, pr, base_report([c for c in pr.columns if c.startswith("pred_")]))
    with pytest.raises(SnapshotError, match="> 0|vo cuc"):
        _export(tmp_path, loaded, run)


def test_export_rejects_bad_report_scoredness_metadata(tmp_path):
    loaded, frame, run, pr = _fresh(tmp_path)
    cols = [c for c in pr.columns if c.startswith("pred_")]
    rep = base_report(cols, locked=False)
    write_run(run, pr, rep)
    with pytest.raises(SnapshotError, match="test.locked"):
        _export(tmp_path, loaded, run, "o1")
    rep = base_report(cols, prespec=["persistence", "good", "good"])
    write_run(run, pr, rep)
    with pytest.raises(SnapshotError, match="thieu/trung"):
        _export(tmp_path, loaded, run, "o2")
    rep = base_report(cols, prespec=["persistence", "good", "alias", "ghost"])
    write_run(run, pr, rep)
    with pytest.raises(SnapshotError, match="khong co cot du doan"):
        _export(tmp_path, loaded, run, "o3")
    rep = base_report(cols)
    del rep["validation_table"]["unscored"]
    write_run(run, pr, rep)
    with pytest.raises(SnapshotError, match="validation_table"):
        _export(tmp_path, loaded, run, "o4")
    rep = base_report(cols)
    del rep["test"]
    write_run(run, pr, rep)
    with pytest.raises(SnapshotError, match="thieu `test`"):
        _export(tmp_path, loaded, run, "o5")


def test_export_rejects_run_manifest_problems(tmp_path):
    loaded, frame, run, pr = _fresh(tmp_path)
    rm_path = run / "run_manifest.json"
    original = rm_path.read_text(encoding="utf-8")
    for mutate, match in ((lambda r: r.update(state="fail"), "state"), (lambda r: r.update(expected_horizons=[3]), "expected_horizons"),
                          (lambda r: r["outputs"].update({f"h{H}_predictions_v3.parquet": "0" * 64}), "sha256 that khac"),
                          (lambda r: r["dataset"].update(samples_file_sha256="e" * 64), "samples_file_sha256")):
        bad = json.loads(original)
        mutate(bad)
        rm_path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(SnapshotError, match=match):
            _export(tmp_path, loaded, run, "o")
    rm_path.unlink()
    with pytest.raises(SnapshotError, match="thieu"):
        _export(tmp_path, loaded, run, "o")


def test_source_file_changed_after_run_is_rejected(tmp_path):
    loaded, frame, run, pr = _fresh(tmp_path)
    pr_path = run / f"h{H}_predictions_v3.parquet"
    pr.assign(pred_good=pr["pred_good"] * 1.0001).to_parquet(pr_path, index=False)           # doi sau khi run_manifest da ghi hash
    with pytest.raises(SnapshotError, match="sha256 that khac"):
        _export(tmp_path, loaded, run)


# ---------------------------------------------------------------- negative tests: load_snapshot / export_snapshot (du lieu)

def test_load_rejects_wrong_pin(exported):
    loaded, frame, run, out, manifest = exported
    pin = pin_for(out)
    pin["snapshot_file_sha256"] = "0" * 64
    with pytest.raises(SnapshotError, match="khac pin|khac manifest"):
        load_snapshot(out, loaded, pin=pin)
    pin = pin_for(out)
    pin["manifest_sha256"] = "1" * 64
    with pytest.raises(SnapshotError, match="khac pin"):
        load_snapshot(out, loaded, pin=pin)


def test_load_rejects_corrupt_parquet_bytes(exported):
    loaded, frame, run, out, manifest = exported
    pq, _ = snapshot.snapshot_paths(out, H)
    raw = bytearray(pq.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    pq.write_bytes(bytes(raw))
    with pytest.raises(SnapshotError, match="sha256 file snapshot"):
        load_snapshot(out, loaded, pin=None, allow_unpinned=True)


def _rewrite(out, mutate):
    pq, mf = snapshot.snapshot_paths(out, H)
    df = pd.read_parquet(pq)
    mutate(df)
    df.to_parquet(pq, index=False)
    m = json.loads(mf.read_text(encoding="utf-8"))                       # manifest hop le voi file moi => kiem tra phai bat o cap NOI DUNG, khong chi hash
    m["snapshot_file_sha256"] = file_sha256(pq)
    m["snapshot_content_sha256"] = snapshot.content_sha256(df)
    mf.write_text(json.dumps(m, sort_keys=True), encoding="utf-8")


@pytest.mark.parametrize("name,mutate,match", [
    ("missing_row", lambda d: d.drop(index=3, inplace=True), "tap record|tap primary"),
    ("duplicate_record_id", lambda d: d.__setitem__("warehouse_record_id", d["warehouse_record_id"].where(d.index != 5, d["warehouse_record_id"].iloc[4])), "trung"),
    ("changed_y_true", lambda d: d.__setitem__("y_true", d["y_true"].where(d.index != 7, d["y_true"] * 2)), "y_true"),
    ("changed_current_price", lambda d: d.__setitem__("current_price", d["current_price"].where(d.index != 7, d["current_price"] + 1)), "current_price"),
    ("changed_direction_label", lambda d: d.__setitem__("y_direction", d["y_direction"].where(d.index != 9, "up" if d["y_direction"].iloc[9] != "up" else "down")), "y_direction"),
    ("scored_value_made_nonpositive", lambda d: d.__setitem__("pred_good", d["pred_good"].where(d.index != 2, -3.0)), "> 0"),
])
def test_load_rejects_tampered_content_even_with_consistent_manifest(exported, name, mutate, match):
    loaded, frame, run, out, manifest = exported
    _rewrite(out, mutate)
    with pytest.raises((SnapshotError, LabelContractError), match=match):
        load_snapshot(out, loaded, pin=None, allow_unpinned=True)


def test_load_rejects_partial_nan_in_a_prediction_column(exported):
    loaded, frame, run, out, manifest = exported
    _rewrite(out, lambda d: d.__setitem__("pred_good", d["pred_good"].where(d.index != d.index[d["split"] == "test"][0], np.nan)))
    with pytest.raises(SnapshotError, match="NaN mot phan"):
        load_snapshot(out, loaded, pin=None, allow_unpinned=True)


def test_load_rejects_manifest_scoredness_or_authority_tampering(exported):
    loaded, frame, run, out, manifest = exported
    _, mf = snapshot.snapshot_paths(out, H)
    original = mf.read_text(encoding="utf-8")
    m = json.loads(original)
    m["prediction_scored_splits"]["pred_unscored"] = ["validation", "test"]
    mf.write_text(json.dumps(m, sort_keys=True), encoding="utf-8")
    with pytest.raises(SnapshotError, match="duoc cham"):
        load_snapshot(out, loaded, pin=None, allow_unpinned=True)
    m = json.loads(original)
    m["expected_scoredness_source"]["prespecified_models"] = ["persistence", "good"]       # report authority bi sua: 'alias' khong con duoc cham test
    mf.write_text(json.dumps(m, sort_keys=True), encoding="utf-8")
    with pytest.raises(SnapshotError, match="duoc cham"):
        load_snapshot(out, loaded, pin=None, allow_unpinned=True)


def test_expected_scored_splits_pure_function():
    src = {"test_locked": True, "prespecified_models": ["a"], "validation_table_names": ["a", "b"]}
    assert expected_scored_splits(src, ["pred_a", "pred_b"]) == {"pred_a": ["validation", "test"], "pred_b": ["validation"]}
    with pytest.raises(SnapshotError):
        expected_scored_splits({**src, "test_locked": False}, ["pred_a", "pred_b"])
    with pytest.raises(SnapshotError, match="khong khop"):
        expected_scored_splits(src, ["pred_a"])                                              # validation_table liet ke 'b' ma khong co cot


def test_load_rejects_dataset_pin_mismatch(exported):
    loaded, frame, run, out, manifest = exported
    loaded.pin = {**loaded.pin, "samples_file_sha256": "e" * 64}
    with pytest.raises(SnapshotError, match="dataset_pin"):
        load_snapshot(out, loaded, pin=None, allow_unpinned=True)


def test_export_rejects_report_pointing_to_other_dataset(tmp_path):
    loaded, frame, run, pr = _fresh(tmp_path)
    rep = base_report([c for c in pr.columns if c.startswith("pred_")])
    rep["dataset"]["samples_file_sha256"] = "f" * 64
    write_run(run, pr, rep)
    with pytest.raises(SnapshotError, match="khong tham chieu dung dataset"):
        _export(tmp_path, loaded, run)


def test_export_rejects_prediction_file_with_missing_extra_duplicate_rows_and_changed_targets(tmp_path):
    loaded, frame, run, full = _fresh(tmp_path)
    cols = [c for c in full.columns if c.startswith("pred_")]
    rep = base_report(cols)
    write_run(run, full.iloc[:-2], rep)                                                   # thieu 2 hang so voi primary
    with pytest.raises(SnapshotError, match="tap warehouse_record_id"):
        _export(tmp_path, loaded, run, "o1")
    extra = pd.concat([full, full.iloc[[0]].assign(vn_observation_date=dt.date(2030, 1, 1))], ignore_index=True)
    write_run(run, extra, rep)                                                            # hang du doan khong ton tai trong dataset
    with pytest.raises(SnapshotError, match="khong noi duoc"):
        _export(tmp_path, loaded, run, "o2")
    write_run(run, pd.concat([full, full.iloc[[0]]], ignore_index=True), rep)
    with pytest.raises(SnapshotError, match="khoa trung"):
        _export(tmp_path, loaded, run, "o3")
    changed = full.copy()
    changed.loc[0, "y_true"] = changed.loc[0, "y_true"] * 3
    write_run(run, changed, rep)
    with pytest.raises(SnapshotError, match="y_true"):
        _export(tmp_path, loaded, run, "o4")
    cp = full.copy()
    cp.loc[0, "current_price"] = cp.loc[0, "current_price"] + 1
    write_run(run, cp, rep)
    with pytest.raises(SnapshotError, match="current_price"):
        _export(tmp_path, loaded, run, "o5")
