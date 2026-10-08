"""Replay tool v3 (hotel-price-intelligence/replay_tools/replay_bundles_v3.py; GPT file 26 contract): fail-closed TRUOC joblib.load, dung sai khoa, join strict, danh tinh bundle,
runtime exact vs --compat-probe, manifest cua helper/config. Run that duoc dung bang CLI smoke tren dataset tong hop (cung moi truong => exact)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from v3_fixtures import make_v3_dataset

ML = Path(__file__).resolve().parents[1]
REPO = ML.parent
TOOL_DIR = REPO / "replay_tools"
SCRIPT = ML / "scripts" / "train_models_v3.py"


def _load_tool(path: Path = TOOL_DIR / "replay_bundles_v3.py"):
    spec = importlib.util.spec_from_file_location(f"replay_tool_{abs(hash(str(path)))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("replay")
    ds = make_v3_dataset(root / "src", version="ds_replay")
    env = {**os.environ, "OMP_NUM_THREADS": "1", "LOKY_MAX_CPU_COUNT": "1", "PYTHONIOENCODING": "utf-8"}
    done = subprocess.run([sys.executable, str(SCRIPT), "--dataset-dir", str(ds), "--smoke", "--smoke-families", "hgb_l1", "--smoke-n-iter", "3", "--smoke-null-n", "2",
                           "--device", "cpu", "--run-id", "smoke_replay", "--allow-env-drift", "--output-root", str(root / "models")], capture_output=True, text=True, env=env, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    return {"root": root, "dataset": ds, "run": root / "models" / "ds_replay" / "smoke_replay"}


def _copy_run(world, tmp_path, name="smoke_copy"):
    """Ban sao run (ten thu muc = run_id trong manifest de verify_run_dir con hop le). Tra duong dan ban sao."""
    dst = tmp_path / "runs" / "smoke_replay"
    shutil.copytree(world["run"], dst)
    return dst


def _rehash(run_dir: Path):
    """Cap nhat checksum trong run_manifest sau khi sua file de run VAN hop le (de kiem tra cac duong sau verify_run_dir)."""
    from training.run_transaction import _checksums

    path = run_dir / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["outputs"] = _checksums(run_dir)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def _argv(world, run, out_root, *extra, replay_id="r1"):
    return ["--code-root", str(REPO), "--run-dir", str(run), "--dataset-dir", str(world["dataset"]), "--output-root", str(out_root), "--replay-id", replay_id, "--allow-smoke-run", *extra]


def _report(out_root, replay_id="r1"):
    return json.loads((Path(out_root) / replay_id / "replay_report.json").read_text(encoding="utf-8"))


class _LoadSpy:
    def __init__(self, monkeypatch):
        import joblib

        self.calls = 0
        real = joblib.load

        def spy(*a, **k):
            self.calls += 1
            return real(*a, **k)

        monkeypatch.setattr(joblib, "load", spy)


# --------------------------------------------------------------------------- happy path + pins
def test_happy_path_is_bounded_and_bitwise_exact_with_routed_and_train_in_sample(world, tmp_path, capsys):
    code = tool.main(_argv(world, world["run"], tmp_path / "out"))
    rep = _report(tmp_path / "out")
    assert code == 0 and rep["verdict"]["overall"] == "PASS_EXACT_RUNTIME" and rep["verdict"]["bitwise_exact"] is True
    assert rep["verdict"]["bounded_numeric_replay"] == "PASS" and rep["verdict"]["train_in_sample_recompute"] == "PASS" and rep["verdict"]["runtime_exact"] is True
    models = rep["horizons"]["1"]["splits"]["test"]["models"]
    assert any(k.startswith("routed:") for k in models) and all(v["failed_rows"] == 0 and v["invalid_rows"] == 0 for v in models.values())
    assert rep["horizons"]["1"]["saved_columns_not_replayed"]                                  # cot khong co bundle duoc LIET KE (khong im lang), vd cac model khong phai champion
    assert rep["completeness"]["splits"] == ["validation", "test"] and rep["completeness"]["bundles"]
    sidecar = (tmp_path / "out" / "r1" / "replay_report.json.sha256").read_text(encoding="utf-8").strip()
    assert sidecar == hashlib.sha256((tmp_path / "out" / "r1" / "replay_report.json").read_bytes()).hexdigest()
    assert "replay verdict: PASS_EXACT_RUNTIME" in capsys.readouterr().out


def test_numeric_tolerance_is_pinned_in_the_config_and_cannot_be_changed_from_the_cli():
    config = json.loads((TOOL_DIR / "replay_config.json").read_text(encoding="utf-8"))
    assert config["numeric"]["rtol"] == 1e-07 and config["numeric"]["atol_vnd"] == 0.01
    help_text = subprocess.run([sys.executable, str(TOOL_DIR / "replay_bundles_v3.py"), "--help"], capture_output=True, text=True).stdout.lower()
    assert "rtol" not in help_text and "atol" not in help_text and "tolerance" not in help_text and "--compat-probe" in help_text


def test_existing_output_directory_is_refused(world, tmp_path):
    (tmp_path / "out" / "r1").mkdir(parents=True)
    assert tool.main(_argv(world, world["run"], tmp_path / "out")) == 2


def test_smoke_run_needs_the_explicit_testing_flag(world, tmp_path):
    args = [a for a in _argv(world, world["run"], tmp_path / "out") if a != "--allow-smoke-run"]
    code = tool.main(args)
    assert code == 3 and "--smoke" in " ".join(_report(tmp_path / "out")["problems"])


# --------------------------------------------------------------------------- fail-closed truoc joblib.load
def test_tampered_prediction_or_bundle_or_extra_file_is_rejected_before_any_joblib_load(world, tmp_path, monkeypatch):
    spy = _LoadSpy(monkeypatch)
    for index, mutate in enumerate((lambda d: (d / "h1_predictions_v3.parquet").write_bytes((d / "h1_predictions_v3.parquet").read_bytes() + b"x"),
                                    lambda d: next(d.glob("h1_bundle_*.joblib")).write_bytes(next(d.glob("h1_bundle_*.joblib")).read_bytes() + b"x"),
                                    lambda d: (d / "h1_bundle_extra_model.joblib").write_bytes(b"not a bundle"))):
        run = tmp_path / f"case{index}" / "smoke_replay"
        shutil.copytree(world["run"], run)
        mutate(run)
        assert tool.main(_argv(world, run, tmp_path / f"out{index}")) == 3
        assert "run khong hop le" in " ".join(_report(tmp_path / f"out{index}")["problems"])
    assert spy.calls == 0


def test_bundle_set_must_equal_the_champions_in_the_report(world, tmp_path, monkeypatch):
    spy = _LoadSpy(monkeypatch)
    run = _copy_run(world, tmp_path)
    bundle = next(run.glob("h1_bundle_*.joblib"))
    bundle.rename(run / "h1_bundle_other_name.joblib")          # doi ten (hash theo ten file => cap nhat manifest de run con hop le)
    _rehash(run)
    assert tool.main(_argv(world, run, tmp_path / "out")) == 3
    assert "tap bundle" in " ".join(_report(tmp_path / "out")["problems"]) and spy.calls == 0


def test_wrong_dataset_or_code_is_rejected(world, tmp_path):
    other = make_v3_dataset(tmp_path / "other", version="ds_other_dataset", n_hotels=18)
    args = _argv(world, world["run"], tmp_path / "out")
    args[args.index("--dataset-dir") + 1] = str(other)
    assert tool.main(args) == 3


def test_runtime_mismatch_fails_before_load_unless_compat_probe_is_explicit(world, tmp_path, monkeypatch):
    import training.v3_runtime as rt

    real = rt.collect_versions
    monkeypatch.setattr(rt, "collect_versions", lambda: {**real(), "scikit-learn": "0.0.1"})
    spy = _LoadSpy(monkeypatch)
    assert tool.main(_argv(world, world["run"], tmp_path / "out1")) == 4
    rep = _report(tmp_path / "out1")
    assert "scikit-learn" in rep["runtime"]["mismatch"] and spy.calls == 0 and rep["verdict"]["overall"] == "FAIL"
    code = tool.main(_argv(world, world["run"], tmp_path / "out2", "--compat-probe"))                           # explicit: chay duoc nhung KHONG phai exact-runtime
    rep2 = _report(tmp_path / "out2")
    assert code == 0 and rep2["verdict"]["overall"] == "PASS_COMPAT_PROBE_ONLY" and rep2["verdict"]["runtime_exact"] is False and rep2["mode"] == "compat_probe_allowed"


def test_python_patch_difference_is_recorded_but_major_minor_difference_fails():
    config = json.loads((TOOL_DIR / "replay_config.json").read_text(encoding="utf-8"))
    base = {k: "1" for k in config["runtime"]["required_package_keys"]}
    assert tool.check_runtime({**base, "python": "3.13.15"}, {**base, "python": "3.13.9"}, config)["exact"] is True
    bad = tool.check_runtime({**base, "python": "3.13.15"}, {**base, "python": "3.12.1"}, config)
    assert bad["exact"] is False and "python(major.minor)" in bad["mismatch"]
    assert tool.check_runtime({**base, "python": "3.13.1"}, {**base, "numpy": "2", "python": "3.13.1"}, config)["mismatch"].keys() == {"numpy"}


# --------------------------------------------------------------------------- dung sai & hang
def test_numeric_boundary_row_by_row_with_no_outlier_hiding():
    saved = np.array([1_000_000.0, 100.0, 5_000_000.0, 2_000.0])
    atol, rtol = 0.01, 1e-07
    edge = saved + atol + rtol * saved                             # DUNG bien: van dat
    stats, bad = tool.compare_prices(edge * (1 - 1e-15) + 0.0, saved, rtol=rtol, atol=atol)
    assert stats["pass"] is True and not bad.any()
    over = edge.copy()
    over[1] += 1e-3                                                # CHI mot hang vuot => FAIL toan bo, khong bo outlier
    stats2, bad2 = tool.compare_prices(over, saved, rtol=rtol, atol=atol)
    assert stats2["pass"] is False and stats2["failed_rows"] == 1 and bad2.tolist() == [False, True, False, False] and stats2["max_abs_delta_vnd"] > 0.01
    stats3, _ = tool.compare_prices(saved.copy(), saved, rtol=rtol, atol=atol)
    assert stats3["bitwise_exact"] is True and stats3["exact_match_rows"] == 4 and stats3["max_abs_delta_vnd"] == 0.0


def test_invalid_values_always_fail_and_shapes_must_match():
    saved = np.array([100.0, 200.0, 300.0, 400.0])
    for bad_value in (np.nan, np.inf, 0.0, -5.0):
        replay = saved.copy()
        replay[2] = bad_value
        stats, bad = tool.compare_prices(replay, saved, rtol=1e-7, atol=0.01)
        assert stats["pass"] is False and stats["invalid_rows"] == 1 and bad[2]
    with pytest.raises(tool.ReplayError):
        tool.compare_prices(saved[:3], saved, rtol=1e-7, atol=0.01)


def test_numeric_failure_after_valid_manifest_is_reported_as_fail_with_examples(world, tmp_path):
    run = _copy_run(world, tmp_path)
    frame = pd.read_parquet(run / "h1_predictions_v3.parquet")
    winner = json.loads((run / "h1_report.json").read_text(encoding="utf-8"))["champions"]["A"]["winner"]       # chi cot cua model CO bundle moi duoc replay
    column = f"pred_{winner}"
    assert column in frame.columns
    row = frame.index[(frame["split"] == "test")][0]
    frame.loc[row, column] = float(frame.loc[row, column]) + 5.0                       # lech 5 VND tren DUNG mot hang (vuot dung sai 0.01 + 1e-7*|saved|)
    frame.to_parquet(run / "h1_predictions_v3.parquet", index=False)
    _rehash(run)                                                                        # run van hop le o tang manifest: bat buoc duong replay phai phat hien
    code = tool.main(_argv(world, run, tmp_path / "out"))
    rep = _report(tmp_path / "out")
    stats = rep["horizons"]["1"]["splits"]["test"]["models"][column[len("pred_"):]]
    assert code == 1 and rep["verdict"]["overall"] == "FAIL" and rep["verdict"]["bitwise_exact"] is False
    assert stats["failed_rows"] == 1 and stats["example_failed_keys"][0]["saved"] != stats["example_failed_keys"][0]["replay"] and stats["pass"] is False


# --------------------------------------------------------------------------- alignment & danh tinh bundle
def _frame(n=6):
    return pd.DataFrame({"hotel_id": [f"h{i}" for i in range(n)], "checkin_date": ["2026-10-01"] * n, "canonical_series_id": [f"s{i}" for i in range(n)], "vn_observation_date": ["2026-09-30"] * n,
                         "current_price": np.arange(n) + 100.0, "y_true": np.arange(n) + 110.0, "inference_mode": ["history_enriched"] * n, "city": ["Hà Nội"] * n, "lead_time_bucket": ["0-3"] * n})


def test_alignment_is_strict_one_to_one_without_dropping_or_nearest_matching():
    frame = _frame()
    a, b = tool.align_frames(frame, frame.sample(frac=1.0, random_state=1), "test")
    assert len(a) == len(b) == len(frame)
    dup = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(tool.ReplayError, match="trung"):
        tool.align_frames(frame, dup, "test")
    with pytest.raises(tool.ReplayError, match="trung"):
        tool.align_frames(dup, frame, "test")
    with pytest.raises(tool.ReplayError, match="tap khoa khac"):
        tool.align_frames(frame, frame.iloc[:-1], "test")
    shifted = frame.copy()
    shifted["vn_observation_date"] = "2026-10-01"                                       # khong merge ngay gan nhat
    with pytest.raises(tool.ReplayError, match="tap khoa khac"):
        tool.align_frames(frame, shifted, "test")
    changed = frame.copy()
    changed.loc[2, "current_price"] += 1.0
    with pytest.raises(tool.ReplayError, match="current_price"):
        tool.align_frames(frame, changed, "test")
    wrong_mode = frame.copy()
    wrong_mode.loc[1, "inference_mode"] = "cold_start"
    with pytest.raises(tool.ReplayError, match="inference_mode"):
        tool.align_frames(frame, wrong_mode, "test")
    with pytest.raises(tool.ReplayError, match="thieu cot khoa"):
        tool.align_frames(frame, frame.drop(columns=["canonical_series_id"]), "test")


def test_bundle_contract_recomputes_hashes_and_cross_checks_routing_and_identity(world):
    import joblib

    from training.v3_contract import TRAINING_VERSION_V3  # noqa: F401

    run = world["run"]
    report = json.loads((run / "h1_report.json").read_text(encoding="utf-8"))
    run_manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    path = next(run.glob("h1_bundle_*.joblib"))
    name = path.name[len("h1_bundle_"):-len(".joblib")]
    bundle = joblib.load(path)
    features = list(report["features"]["list"])
    ok = tool.check_bundle_contract(bundle, name=name, horizon=1, features=features, report=report, run_manifest=run_manifest)
    assert ok["feature_list_sha256"] == report["features"]["sha256"]
    for label, mutate, match in (("feature order", lambda b: {**b, "features": list(reversed(b["features"]))}, "feature"),
                                 ("forged raw hash", lambda b: {**b, "feature_list_sha256": "0" * 64}, "feature"),
                                 ("forged effective hash", lambda b: {**b, "effective_feature_list_sha256": "0" * 64}, "hieu dung"),
                                 ("horizon", lambda b: {**b, "horizon": 3}, "horizon"),
                                 ("config", lambda b: {**b, "config_sha256": "f" * 64}, "config_sha256"),
                                 ("n_features_in", lambda b: {**b, "n_features_in": b["n_features_in"] + 1}, "n_features_in"),
                                 ("routing flipped", lambda b: {**b, "routing_policy": {m: {**p, "use_model": not p["use_model"]} for m, p in (b["routing_policy"] or {"cold_start": {"use_model": False}}).items()}}, "routing"),
                                 ("name", lambda b: {**b, "name": "other"}, "ten bundle")):
        with pytest.raises(tool.ReplayError, match=match):
            tool.check_bundle_contract(mutate(bundle), name=name, horizon=1, features=features, report=report, run_manifest=run_manifest)


def test_unknown_inference_mode_replays_as_persistence_for_the_routed_column(world):
    import joblib

    from training.v3_bundle import predict_z, routed_z
    from training.v3_contract import price_from_z

    bundle = joblib.load(next(world["run"].glob("h1_bundle_*.joblib")))
    frame = _frame(n=1)
    frame = pd.read_parquet(world["dataset"] / "samples.parquet").head(5).assign(inference_mode="a_mode_never_seen")
    z = predict_z(bundle, frame.assign(y_true=1.0))
    price = price_from_z(frame["current_price"].to_numpy(float), routed_z(bundle, frame, z), label="unknown-mode")
    assert np.array_equal(price, frame["current_price"].to_numpy(float))


# --------------------------------------------------------------------------- manifest cua helper/config
def test_helper_manifest_blocks_a_modified_config_or_script(world, tmp_path):
    copy = tmp_path / "replay_tools"
    shutil.copytree(TOOL_DIR, copy, ignore=shutil.ignore_patterns("__pycache__", "REPLAY_MANIFEST.json"))
    files = {name: hashlib.sha256((copy / name).read_bytes()).hexdigest() for name in ("replay_bundles_v3.py", "replay_config.json")}
    (copy / "REPLAY_MANIFEST.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    env = {**os.environ, "OMP_NUM_THREADS": "1", "PYTHONIOENCODING": "utf-8"}

    def run(replay_id):
        cmd = [sys.executable, str(copy / "replay_bundles_v3.py"), *_argv(world, world["run"], tmp_path / "out", replay_id=replay_id)]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=900)

    good = run("m1")
    assert good.returncode == 0, good.stderr[-1500:]
    assert _report(tmp_path / "out", "m1")["tool"]["manifest"]["files"] == 2
    config = json.loads((copy / "replay_config.json").read_text(encoding="utf-8"))
    config["numeric"]["rtol"] = 1e-3                                                                  # nơi lỏng dung sai SAU khi nhin ket qua => phai bi chan
    (copy / "replay_config.json").write_text(json.dumps(config), encoding="utf-8")
    bad = run("m2")
    assert bad.returncode == 3 and "REPLAY_MANIFEST" in bad.stderr
    assert not (tmp_path / "out" / "m2" / "replay_report.json").read_text(encoding="utf-8").count('"overall": "PASS')


def test_one_bad_row_among_many_still_fails_the_whole_replay():
    rng = np.random.default_rng(0)
    saved = rng.uniform(1e5, 1e7, 5000)
    replay = saved.copy()
    replay[1234] += 3.0                                              # MOT hang lech vuot dung sai trong 5.000 => FAIL (khong 'dat 99,98%')
    stats, bad = tool.compare_prices(replay, saved, rtol=1e-7, atol=0.01)
    assert stats["pass"] is False and stats["failed_rows"] == 1 and bad.sum() == 1


def test_persistence_column_must_equal_current_price(world, tmp_path):
    run = _copy_run(world, tmp_path)
    frame = pd.read_parquet(run / "h1_predictions_v3.parquet")
    frame.loc[frame.index[0], "pred_persistence"] = float(frame.loc[frame.index[0], "pred_persistence"]) + 1.0
    frame.to_parquet(run / "h1_predictions_v3.parquet", index=False)
    _rehash(run)
    assert tool.main(_argv(world, run, tmp_path / "out")) == 1
    assert "pred_persistence" in " ".join(_report(tmp_path / "out")["problems"])


def test_unknown_xgb_device_during_replay_blocks_the_exact_runtime_label_and_device_match_is_informational():
    known = {"h1:xgb_abs-02": {"xgb_actual_device_during_replay": "cuda"}, "h1:hgb_l1-08": {"matrix": "tree"}}
    assert tool.device_verdict(known, "cuda") == {"xgb_bundles": {"h1:xgb_abs-02": "cuda"}, "unknown": [], "verified": True, "device_matches_training": True}
    cpu = tool.device_verdict({"h1:xgb_abs-02": {"xgb_actual_device_during_replay": "cpu"}}, "cuda")
    assert cpu["verified"] is True and cpu["device_matches_training"] is False                            # CPU hop le nhung KHAC thiet bi train: chi la thong tin
    unknown = tool.device_verdict({"h1:xgb_abs-02": {"xgb_actual_device_during_replay": None}}, "cuda")
    assert unknown["verified"] is False and unknown["unknown"] == ["h1:xgb_abs-02"] and unknown["device_matches_training"] is None
    only_hgb = tool.device_verdict({"h3:hgb_l1-00": {"matrix": "tree"}}, None)
    assert only_hgb["verified"] is True and only_hgb["xgb_bundles"] == {}


def test_replay_never_reads_the_label_when_predicting(world):
    import joblib

    from training.v3_bundle import predict_z

    bundle = joblib.load(next(world["run"].glob("h1_bundle_*.joblib")))
    frame = pd.read_parquet(world["dataset"] / "samples.parquet").head(200)
    frame = frame.assign(y_true=frame["y_price_h1"].fillna(1.0))
    z = predict_z(bundle, frame)
    scrambled = frame.assign(y_true=np.random.default_rng(1).uniform(1, 9e9, len(frame)), y_price_h1=np.random.default_rng(2).uniform(1, 9e9, len(frame)))
    assert np.array_equal(z, predict_z(bundle, scrambled))                                                # doi nhan khong doi du bao: bundle khong doc y


def test_overall_verdict_downgrades_when_the_xgb_device_cannot_be_verified(world, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "device_verdict", lambda bundles, trained=None: {"xgb_bundles": {"h1:x": None}, "unknown": ["h1:x"], "verified": False, "device_matches_training": None})
    code = tool.main(_argv(world, world["run"], tmp_path / "out"))
    rep = _report(tmp_path / "out")
    assert code == 0 and rep["verdict"]["overall"] == "PASS_BOUNDED_DEVICE_UNVERIFIED" and rep["verdict"]["device_verified"] is False and rep["verdict"]["bounded_numeric_replay"] == "PASS"
    assert rep["runtime"]["xgb_devices_during_replay"]["unknown"] == ["h1:x"]
