"""Pipeline mot horizon (synthetic), chep Drive co kiem lai, va chot chan CLI khong fit that o may chinh."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from cls_training import candidates as C
from cls_training import drive as D
from cls_training import pipeline as P
from cls_training import snapshot
from cls_training import transaction as T
from cls_training.config import override_device, load_config, ConfigError
from cls_training.snapshot import export_snapshot, load_snapshot

from cls_testenv import V1_DIR
from synth import make_loaded as make_runner_loaded, runner_config
from test_candidates_selection import fake_xgb_factory
from test_panel_snapshot import make_regression_run


@pytest.fixture(autouse=True)
def _fake_xgb(monkeypatch):
    monkeypatch.setitem(C.ESTIMATOR_FACTORIES, "xgb", fake_xgb_factory)


def synthetic_snapshot(loaded, tmp_path):
    """Snapshot hop le cho CHINH khung val/test cua `loaded` (du doan regression gia lap; day la du lieu tong hop)."""
    import numpy as np
    from types import SimpleNamespace
    frames = []
    for name in ("validation", "test"):
        f = loaded.frames[name].copy()
        f["checkin_date"] = pd.to_datetime(f["checkin_date"])
        f["vn_observation_date"] = pd.to_datetime(f["vn_observation_date"])
        mult = f["y_direction_h1"].map({"down": 0.9, "stable": 1.0, "up": 1.1}).to_numpy()
        f["y_price_h1"] = (f["current_price"].to_numpy() * mult).round(0)
        f["y_price_h1"] = f["y_price_h1"].where(f["y_direction_h1"] != "stable", f["current_price"])
        frames.append(f)
    allf = pd.concat(frames, ignore_index=True)
    # nhan phai tai lap duoc tu (y-c)/c: stable giu nguyen gia, up/down +-10%
    from cls_training.labels import direction_from_pct, pct_change
    assert (direction_from_pct(pct_change(allf["y_price_h1"], allf["current_price"])) == allf["y_direction_h1"].to_numpy(dtype=object)).all()
    fake = SimpleNamespace(horizon=1, pin={"dataset_version": "ds_synthetic", "output_checksums_sha256": "a" * 64, "dataset_contract_sha256": "b" * 64, "samples_file_sha256": "c" * 64},
                           meta={"dataset_name": "ds_synthetic", "samples_file_sha256": "c" * 64},
                           frames={"train": allf.iloc[:0], "validation": allf[allf["split"] == "validation"], "test": allf[allf["split"] == "test"]})
    run = make_regression_run(tmp_path, allf.assign(**{}))
    out = tmp_path / "snap"
    export_snapshot(fake, run, out, export_code_files={})
    snap, manifest = load_snapshot(out, fake, pin=None, allow_unpinned=True)
    return fake, snap, manifest


def test_train_horizon_end_to_end_with_verified_drive_copy(tmp_path):
    cfg = runner_config()
    loaded = make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    drive_root = tmp_path / "drive"
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1", drive_root=drive_root)
    run, drive_run = Path(res["run_dir"]), Path(res["drive_run_dir"])
    assert res["status"] == "complete" and res["contract_complete"] is True and "h1_analysis.json" in res["files"]
    assert run.parent.name == "ds_synthetic" and drive_run.parent.name == "ds_synthetic" and drive_run.name == "run_r1"
    assert T.verify_run_dir(drive_run) == T.verify_run_dir(run)
    analysis = json.loads((run / "h1_analysis.json").read_text(encoding="utf-8"))
    assert analysis["claim_level"] in ("none", "dev_exploratory_validation_gain") and analysis["claim"]["validation_comparator_id"].startswith(("C0", "C1", "C2"))
    again = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r2", drive_root=drive_root)           # resume: khong tao run moi
    assert again["run_dir"] == res["run_dir"] and not (tmp_path / "out" / "ds_synthetic" / "run_r2").exists()


def _tiny_run(tmp_path, name="run_x"):
    tx = T.RunTransaction(tmp_path / "src", name[len("run_"):])
    tx.write_json("a.json", {"x": 1})
    tx.write_text("b.txt", "hello")
    return tx.commit({"k": 1}, extra={"status": "complete", "contract_complete": True})


def test_drive_copy_is_verified_and_idempotent(tmp_path):
    src = _tiny_run(tmp_path)
    dest = D.copy_run_verified(src, tmp_path / "drive")
    assert dest.name == "run_x" and T.verify_run_dir(dest)
    assert D.copy_run_verified(src, tmp_path / "drive") == dest                         # cung noi dung => tra ve, khong ghi de


def test_drive_copy_detects_corruption_and_leaves_no_complete_directory(tmp_path, monkeypatch):
    src = _tiny_run(tmp_path)
    real = shutil.copy2

    def corrupt(s, d, *a, **k):
        out = real(s, d, *a, **k)
        if Path(s).name == "a.json":
            Path(d).write_bytes(b"corrupted")
        return out

    monkeypatch.setattr(D.shutil, "copy2", corrupt)
    with pytest.raises(T.TransactionError, match="khac manifest"):
        D.copy_run_verified(src, tmp_path / "drive")
    assert not (tmp_path / "drive" / "run_x").exists() and not list((tmp_path / "drive").glob(".copying_*"))
    assert T.find_complete_run(tmp_path / "drive", {"k": 1}) is None


def test_drive_copy_interrupted_midway_is_not_a_result(tmp_path, monkeypatch):
    src = _tiny_run(tmp_path)
    calls = {"n": 0}
    real = shutil.copy2

    def flaky(s, d, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("mat ket noi Drive")
        return real(s, d, *a, **k)

    monkeypatch.setattr(D.shutil, "copy2", flaky)
    with pytest.raises(OSError):
        D.copy_run_verified(src, tmp_path / "drive")
    assert not (tmp_path / "drive" / "run_x").exists()
    assert T.find_complete_run(tmp_path / "drive", {"k": 1}) is None


def test_drive_refuses_to_overwrite_a_different_or_invalid_destination(tmp_path):
    src = _tiny_run(tmp_path)
    dest = D.copy_run_verified(src, tmp_path / "drive")
    (dest / "b.txt").write_text("tampered", encoding="utf-8")
    with pytest.raises(T.TransactionError, match="khong ghi de"):
        D.copy_run_verified(src, tmp_path / "drive")


def test_source_must_be_valid_before_copying(tmp_path):
    src = _tiny_run(tmp_path)
    (src / "a.json").write_text("{}", encoding="utf-8")
    with pytest.raises(T.TransactionError):
        D.copy_run_verified(src, tmp_path / "drive")


def test_override_device_recomputes_config_hash_and_validates():
    cfg = load_config()                                                   # cau hinh gui di: run.device = cuda (Colab GPU)
    assert cfg["run"]["device"] == "cuda"
    cpu = override_device(cfg, "cpu")
    assert cpu["run"]["device"] == "cpu" and cpu["config_sha256"] != cfg["config_sha256"]
    assert override_device(cpu, "cuda")["config_sha256"] == cfg["config_sha256"]          # doi lai => cung danh tinh
    assert override_device(cfg, "cuda")["config_sha256"] == cfg["config_sha256"]
    with pytest.raises(ConfigError):
        override_device(cfg, "tpu")


def _run_cli(*args, env_extra=None, script="train_cls.py"):
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("COLAB_")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-B", str(V1_DIR / "scripts" / script), *args], capture_output=True, text=True, timeout=300, env=env)


def _script(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"_cli_{name}", V1_DIR / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_refuses_real_fit_outside_colab_before_touching_data(tmp_path):
    r = _run_cli("--datasets-root", str(tmp_path / "none"), "--snapshot-dir", str(tmp_path / "none"), "--out-root", str(tmp_path / "out"))
    assert r.returncode == 2 and "TU CHOI" in r.stderr and not (tmp_path / "out").exists()
    r2 = _run_cli("--datasets-root", str(tmp_path), "--snapshot-dir", str(tmp_path), "--out-root", str(tmp_path / "o2"), "--allow-local-real-fit")
    assert r2.returncode == 2 and "unrecognized arguments" in r2.stderr                          # khong con cua hau cho fit that o may chinh


def test_cli_rejects_unsafe_run_id_and_unknown_horizon_before_selftest(tmp_path):
    env = {"COLAB_GPU": "1"}
    r = _run_cli("--datasets-root", str(tmp_path), "--snapshot-dir", str(tmp_path), "--out-root", str(tmp_path / "o"), "--run-id", "r/../../victim", env_extra=env)
    assert r.returncode == 2 and "run_id khong an toan" in r.stderr and not (tmp_path / "o").exists() and not (tmp_path.parent / "victim").exists()
    r = _run_cli("--datasets-root", str(tmp_path), "--snapshot-dir", str(tmp_path), "--out-root", str(tmp_path / "o"), "--horizons", "7", env_extra=env)
    assert r.returncode == 2 and "khong nam trong cau hinh" in r.stderr


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("xgboost") is not None, reason="may nay co xgboost that")
def test_cli_stops_at_the_pinned_environment_check_before_touching_data(tmp_path):
    r = _run_cli("--datasets-root", str(tmp_path / "none"), "--snapshot-dir", str(tmp_path / "none"), "--out-root", str(tmp_path / "out"), env_extra={"COLAB_GPU": "1"})
    assert r.returncode == 2 and "MOI TRUONG LECH" in r.stderr and "xgboost" in r.stderr and "pip install" in r.stderr and not (tmp_path / "out").exists()


def test_cli_environment_mismatch_is_fatal_and_selftest_failure_is_fatal(tmp_path, monkeypatch):
    mod = _script("train_cls")
    _fake_pipeline(monkeypatch, mod, True)
    bad = {"status": "mismatch", "mismatch": {"numpy": {"expected": "2.1.3", "actual": "9.9"}}, "pip_install": "numpy==2.1.3"}
    monkeypatch.setattr(mod, "check_environment", lambda cfg: bad)
    monkeypatch.setattr(mod, "load_dataset", lambda *a, **k: (_ for _ in ()).throw(AssertionError("khong duoc nap dataset")))
    args = ["--datasets-root", str(tmp_path), "--snapshot-dir", str(tmp_path), "--out-root", str(tmp_path / "o"), "--device", "cpu"]
    assert mod.main(args) == 2 and not (tmp_path / "o").exists()
    monkeypatch.setattr(mod, "check_environment", lambda cfg: {"status": "ok", "mismatch": {}, "pip_install": ""})
    monkeypatch.setattr(mod, "run_selftest", lambda cfg: (_ for _ in ()).throw(RuntimeError("selftest hong")))
    with pytest.raises(RuntimeError, match="selftest hong"):
        mod.main(args)


def _fake_pipeline(monkeypatch, mod, contract_complete):
    from types import SimpleNamespace
    monkeypatch.setattr(mod, "on_colab", lambda: True)
    monkeypatch.setattr(mod, "verify_packaged", lambda: [])
    monkeypatch.setattr(mod, "check_environment", lambda cfg: {"status": "ok", "mismatch": {}, "pip_install": ""})
    monkeypatch.setattr(mod, "run_selftest", lambda cfg: {"status": "ok", "xgboost_version": "fake", "fits": []})
    monkeypatch.setattr(mod, "run_smoke", lambda cfg: {"status": "ok", "champion": "hgb-00", "n_models": 5, "devices": ["cpu"], "elapsed_seconds": 1.0})
    monkeypatch.setattr(mod, "load_dataset", lambda *a, **k: SimpleNamespace(frames_info={"rows_primary": {}}))
    monkeypatch.setattr(mod, "load_snapshot", lambda *a, **k: ("snap", "manifest"))
    monkeypatch.setattr(mod, "train_horizon", lambda cfg, loaded, snap, out_root, **k: {"horizon": 1, "dataset_version": "ds", "status": "complete" if contract_complete else "incomplete",
                                                                                          "contract_complete": contract_complete, "resumed_from": None, "run_dir": "r", "drive_run_dir": None})


def test_cli_exit_codes_and_structured_status_for_complete_and_incomplete(tmp_path, monkeypatch, capsys):
    mod = _script("train_cls")
    for complete, code, outcome in ((True, 0, "complete"), (False, 3, "incomplete")):
        _fake_pipeline(monkeypatch, mod, complete)
        out = tmp_path / f"out_{outcome}"
        rc = mod.main(["--datasets-root", str(tmp_path), "--snapshot-dir", str(tmp_path), "--out-root", str(out), "--horizons", "1", "--run-id", "rid", "--device", "cpu"])
        assert rc == code
        status = json.loads((out / "train_status_rid.json").read_text(encoding="utf-8"))
        assert status["outcome"] == outcome and status["incomplete_horizons"] == ([] if complete else [1]) and status["device"] == "cpu"
        printed = capsys.readouterr().out.strip().splitlines()[-1]
        assert printed.startswith("TRAIN_STATUS ") and json.loads(printed[len("TRAIN_STATUS "):])["outcome"] == outcome


def test_cli_selftest_only_exits_zero_without_touching_data(tmp_path, monkeypatch):
    mod = _script("train_cls")
    _fake_pipeline(monkeypatch, mod, True)
    monkeypatch.setattr(mod, "load_dataset", lambda *a, **k: (_ for _ in ()).throw(AssertionError("khong duoc nap dataset")))
    assert mod.main(["--datasets-root", str(tmp_path), "--snapshot-dir", str(tmp_path), "--out-root", str(tmp_path / "o"), "--selftest-only", "--device", "cpu"]) == 0


# ----------------------------------------------------------------- resume ngu nghia (local + Drive)

def _boom(*a, **k):
    raise AssertionError("khong duoc fit/probe khi resume")


def test_fresh_local_root_restores_a_valid_drive_run_without_fitting(tmp_path, monkeypatch):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    drive = tmp_path / "drive"
    first = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out1", run_id="r1", drive_root=drive)
    monkeypatch.setattr(P, "execute_horizon", _boom)
    monkeypatch.setattr(P, "probe_device", _boom)
    monkeypatch.setattr("cls_training.runner.run_horizon", _boom)
    again = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out2_fresh", run_id="r2", drive_root=drive)          # local moi (runtime reset), Drive con nguyen
    assert again["resumed_from"] == "drive" and Path(again["run_dir"]).name == "run_r1" and "out2_fresh" in again["run_dir"]
    assert T.verify_run_dir(Path(again["run_dir"])) == T.verify_run_dir(Path(first["run_dir"]))


def test_incomplete_run_is_kept_for_audit_but_is_never_a_cache(tmp_path, monkeypatch):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    drive = tmp_path / "drive"
    with monkeypatch.context() as mp:
        mp.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("xgb hong")))
        bad = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1", drive_root=drive)
    assert bad["status"] == "incomplete" and bad["contract_complete"] is False and bad["resumed_from"] is None
    good = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r2", drive_root=drive)           # cung identity: run incomplete KHONG duoc dung lai
    assert good["status"] == "complete" and good["resumed_from"] is None and Path(good["run_dir"]).name == "run_r2"
    assert (tmp_path / "out" / "ds_synthetic" / "run_r1").is_dir() and (drive / "ds_synthetic" / "run_r1").is_dir()           # bang chung cu khong bi xoa
    with pytest.raises(T.TransactionError, match="da ton tai"):
        T.RunTransaction(tmp_path / "out" / "ds_synthetic", "r1")                                    # chay lai phai dung run_id MOI, khong ghi de


def test_tampered_drive_run_is_not_restored_and_is_not_deleted(tmp_path):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    drive = tmp_path / "drive"
    P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out1", run_id="r1", drive_root=drive)
    victim = drive / "ds_synthetic" / "run_r1" / "h1_report.json"
    victim.write_bytes(victim.read_bytes() + b" ")
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out_fresh", run_id="r2", drive_root=drive)
    assert res["resumed_from"] is None and Path(res["run_dir"]).name == "run_r2" and victim.is_file()


def test_semantic_validator_rejects_forged_but_byte_valid_run(tmp_path):
    """Run co hash dung nhung predictions bi sua (pred_class != argmax) khong phai 'thanh cong'."""
    from cls_training.runner import find_successful_run, run_identity
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    ident = run_identity(cfg, loaded)
    assert find_successful_run(tmp_path / "out" / "ds_synthetic", ident, require_analysis=True) == Path(res["run_dir"])
    run = Path(res["run_dir"])
    run_manifest = T.verify_run_dir(run)
    pred = pd.read_parquet(run / "h1_predictions.parquet")
    pred["pred_class"] = ((pred["pred_class"] + 1) % 3).astype("int8")
    forged = tmp_path / "forged" / "ds_synthetic"
    tx = T.RunTransaction(forged, "f1")
    for name in run_manifest["files"]:
        if name == "h1_predictions.parquet":
            pred.to_parquet(tx.path(name), index=False)
        else:
            tx.path(name).write_bytes((run / name).read_bytes())
    tx.commit(run_manifest["identity"], extra={"status": "complete", "contract_complete": True})
    assert T.find_complete_run(forged, ident) is not None                                         # hash dung, identity dung ...
    assert find_successful_run(forged, ident, require_analysis=True) is None                       # ... nhung ngu nghia sai => khong phai cache thanh cong


def test_standalone_snapshot_loader_matches_the_dataset_backed_loader(tmp_path):
    from cls_training.snapshot import load_snapshot_standalone, snapshot_paths
    from cls_training.upstream.provenance import file_sha256
    loaded = make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    pq, mf = snapshot_paths(tmp_path / "snap", 1)
    pin = {"horizon": 1, "snapshot_file_sha256": file_sha256(pq), "manifest_sha256": file_sha256(mf)}
    velocity = snap.set_index("warehouse_record_id")["price_velocity"]
    alone, m2 = load_snapshot_standalone(tmp_path / "snap", 1, pin=pin, dataset_pin=fake.pin, price_velocity_by_record=velocity)
    pd.testing.assert_frame_equal(alone.reset_index(drop=True), snap.reset_index(drop=True))
    assert m2["_validated"]["compared_with_dataset"] is False and m2["_validated"]["price_velocity_source"] == "run_predictions_artifact"
    with pytest.raises(Exception, match="pin"):
        load_snapshot_standalone(tmp_path / "snap", 1, pin={**pin, "snapshot_file_sha256": "0" * 64}, dataset_pin=fake.pin, price_velocity_by_record=velocity)
    with pytest.raises(Exception, match="price_velocity"):
        load_snapshot_standalone(tmp_path / "snap", 1, pin=pin, dataset_pin=fake.pin, price_velocity_by_record=velocity.iloc[:-3])


def test_analyze_results_cli_fails_closed_on_missing_or_incomplete_run(tmp_path):
    r = _run_cli("--run-dir", str(tmp_path / "run_none"), "--snapshot-dir", str(tmp_path), "--out-dir", str(tmp_path / "o"), script="analyze_results.py")
    assert r.returncode == 2 and "ANALYZE FAIL" in r.stderr and not (tmp_path / "o").exists()


def test_analyze_results_cli_reproduces_the_pipeline_analysis_from_a_downloaded_run(tmp_path, monkeypatch):
    """Duong phan tich lai that: run da tai ve + snapshot da ghim (khong dataset) => cung ket qua claim/panel voi analysis luc huan luyen; khong ghi de."""
    import copy
    from cls_training.config import canonical_sha256
    from cls_training.snapshot import snapshot_paths
    from cls_training.upstream.provenance import file_sha256
    mod = _script("analyze_results")
    monkeypatch.setattr(mod, "validate_config", lambda body: None)                      # cau hinh tong hop (cv/ablation nho) khong qua validator giao thuc that
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    pin = {"horizon": 1, "dataset_version": "ds_synthetic", "evaluation_horizons": [1], "output_checksums_sha256": "a" * 64, "dataset_contract_sha256": "b" * 64,
           "samples_file_sha256": "c" * 64, "feature_list_sha256": "d" * 64, "n_rows": 1, "n_entries": 14, "n_features": 15}
    pq, mf = snapshot_paths(tmp_path / "snap", 1)
    cfg = copy.deepcopy(cfg)
    cfg["dataset_pins"] = [pin if int(p["horizon"]) == 1 else p for p in cfg["dataset_pins"]]
    cfg["baseline_snapshot_pins"] = [{"horizon": 1, "snapshot_file_sha256": file_sha256(pq), "manifest_sha256": file_sha256(mf)} if int(p["horizon"]) == 1 else p for p in cfg["baseline_snapshot_pins"]]
    cfg["config_sha256"] = canonical_sha256({k: v for k, v in cfg.items() if k != "config_sha256"})
    loaded.pin = dict(pin)
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    run = Path(res["run_dir"])
    assert res["contract_complete"] is True
    target = mod.analyze_run(run, tmp_path / "snap", tmp_path / "re", None)
    again = json.loads(target.read_text(encoding="utf-8"))
    trained = json.loads((run / "h1_analysis.json").read_text(encoding="utf-8"))
    assert again["claim"] == trained["claim"] and again["denominators"] == trained["denominators"] and again["training_contract_complete"] is True
    assert again["source_run"]["snapshot_validation"]["compared_with_dataset"] is False
    with pytest.raises(RuntimeError, match="ghi de"):
        mod.analyze_run(run, tmp_path / "snap", tmp_path / "re", None)
    (run / "h1_report.json").write_bytes((run / "h1_report.json").read_bytes() + b" ")
    with pytest.raises(T.TransactionError):
        mod.analyze_run(run, tmp_path / "snap", tmp_path / "re2", None)               # run hong => khong phan tich


# ----------------------------------------------------------------- M2 (GPT 14): resume phai doi chieu snapshot da ghim

def _forged_copy(run, dest_root, run_id, mutate_pred=None, mutate_analysis=None):
    """Ban sao byte-valid (hash/COMPLETE dung, identity dung) cua mot run voi predictions/analysis bi sua: mo phong bug cua producer, khong phai tan cong lai hash."""
    manifest = T.verify_run_dir(run)
    tx = T.RunTransaction(dest_root, run_id)
    for name in manifest["files"]:
        src = run / name
        if name == "h1_predictions.parquet" and mutate_pred is not None:
            df = pd.read_parquet(src)
            mutate_pred(df).to_parquet(tx.path(name), index=False)
        elif name == "h1_analysis.json" and mutate_analysis is not None:
            obj = json.loads(src.read_text(encoding="utf-8"))
            mutate_analysis(obj)
            tx.write_json(name, obj)
        else:
            tx.path(name).write_bytes(src.read_bytes())
    return tx.commit(manifest["identity"], extra={k: manifest[k] for k in ("status", "contract_complete", "environment")})


def test_resume_requires_predictions_to_match_the_pinned_snapshot(tmp_path):
    from cls_training.runner import find_successful_run, run_identity
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    run, ident = Path(res["run_dir"]), run_identity(cfg, loaded)

    def wrong_hotel(df):
        df.loc[df.index[:7], "hotel_id"] = "999999"
        return df

    def wrong_true_everywhere(df):                                    # nhat quan giua cac model nhung sai so voi snapshot
        df["true_class"] = ((df["true_class"] + 1) % 3).astype("int8")
        return df

    def drop_primary_test_rows(df):                                   # thieu record primary o MOI model
        drop = df[(df["split"] == "test") & df["is_primary"]]["warehouse_record_id"].drop_duplicates().head(3)
        return df[~((df["split"] == "test") & df["is_primary"] & df["warehouse_record_id"].isin(drop))]

    forgeries = {"hotel": wrong_hotel, "true_label": wrong_true_everywhere, "membership": drop_primary_test_rows}
    for name, mutate in forgeries.items():
        root = tmp_path / f"forged_{name}"
        _forged_copy(run, root, "f1", mutate_pred=mutate)
        assert T.find_complete_run(root, ident) is not None, name                                          # byte-valid, identity dung
        assert find_successful_run(root, ident, require_analysis=True, snap=snap) is None, name             # co snapshot da ghim => khong resume
    stale = tmp_path / "forged_analysis"
    _forged_copy(run, stale, "f1", mutate_analysis=lambda a: a["artifact_validation"]["rows"]["test"].update({next(iter(a["artifact_validation"]["rows"]["test"])): 1}))
    assert find_successful_run(stale, ident, require_analysis=True, snap=snap) is None                       # analysis luu khong con khop artifact hien co
    assert find_successful_run(tmp_path / "out" / "ds_synthetic", ident, require_analysis=True, snap=snap) == run


def test_pipeline_does_not_restore_a_snapshot_inconsistent_drive_run_and_retrains(tmp_path):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    good = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out1", run_id="r1")
    drive = tmp_path / "drive" / "ds_synthetic"

    def wrong_hotel(df):
        df.loc[df.index[:7], "hotel_id"] = "999999"
        return df

    _forged_copy(Path(good["run_dir"]), drive, "bad", mutate_pred=wrong_hotel)
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out_fresh", run_id="r2", drive_root=tmp_path / "drive")
    assert res["resumed_from"] is None and Path(res["run_dir"]).name == "run_r2" and (drive / "run_bad").is_dir()            # train lai, khong xoa ban gia


def test_pipeline_does_not_resume_a_snapshot_inconsistent_local_run_and_retrains(tmp_path):
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    good = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "src", run_id="r1")
    local = tmp_path / "out" / "ds_synthetic"

    def wrong_hotel(df):
        df.loc[df.index[:7], "hotel_id"] = "999999"
        return df

    _forged_copy(Path(good["run_dir"]), local, "bad", mutate_pred=wrong_hotel)
    res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r2")
    assert res["resumed_from"] is None and Path(res["run_dir"]).name == "run_r2" and (local / "run_bad").is_dir()


def _pinned_world(tmp_path):
    """Cau hinh tong hop co pin dataset/snapshot khop fixture (de chay analyze_results nhu tren run that), tra (cfg, loaded, snap, manifest)."""
    import copy
    from cls_training.config import canonical_sha256
    from cls_training.snapshot import snapshot_paths
    from cls_training.upstream.provenance import file_sha256
    cfg, loaded = runner_config(), make_runner_loaded()
    fake, snap, manifest = synthetic_snapshot(loaded, tmp_path)
    pin = {"horizon": 1, "dataset_version": "ds_synthetic", "evaluation_horizons": [1], "output_checksums_sha256": "a" * 64, "dataset_contract_sha256": "b" * 64,
           "samples_file_sha256": "c" * 64, "feature_list_sha256": "d" * 64, "n_rows": 1, "n_entries": 14, "n_features": 15}
    pq, mf = snapshot_paths(tmp_path / "snap", 1)
    cfg = copy.deepcopy(cfg)
    cfg["dataset_pins"] = [pin if int(p["horizon"]) == 1 else p for p in cfg["dataset_pins"]]
    cfg["baseline_snapshot_pins"] = [{"horizon": 1, "snapshot_file_sha256": file_sha256(pq), "manifest_sha256": file_sha256(mf)} if int(p["horizon"]) == 1 else p for p in cfg["baseline_snapshot_pins"]]
    cfg["config_sha256"] = canonical_sha256({k: v for k, v in cfg.items() if k != "config_sha256"})
    loaded.pin = dict(pin)
    return cfg, loaded, snap, manifest


def test_analyze_results_handles_a_no_valid_model_run_without_snapshot_velocity_or_predictions(tmp_path, monkeypatch):
    """MINOR-2 (GPT 16): run diagnostic khong co champion/predictions van phan tich duoc (claim none + ly do), khong doi price_velocity; completed van dung duong snapshot doc lap."""
    mod = _script("analyze_results")
    monkeypatch.setattr(mod, "validate_config", lambda body: None)
    cfg, loaded, snap, manifest = _pinned_world(tmp_path)
    with monkeypatch.context() as mp:
        for fam in ("lr", "hgb", "xgb"):
            mp.setitem(C.ESTIMATOR_FACTORIES, fam, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("khong fit duoc")))
        res = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r1")
    run = Path(res["run_dir"])
    assert res["status"] == "no_valid_model" and not (run / "h1_predictions.parquet").exists()
    target = mod.analyze_run(run, tmp_path / "snap", tmp_path / "re", None)
    out = json.loads(target.read_text(encoding="utf-8"))
    assert out["claim_level"] == "none" and out["validation_gate_passed"] is False and out["reason"] == "no_valid_model" and out["claim_block_reasons"] == ["no_valid_model"]
    assert out["training_contract_complete"] is False and out["training_status"] == "no_valid_model" and out["source_run"]["snapshot_validation"] is None
    # run incomplete nhung co champion van dung duong snapshot doc lap (velocity tu artifact) va claim none
    with monkeypatch.context() as mp:
        mp.setitem(C.ESTIMATOR_FACTORIES, "xgb", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("xgb hong")))
        res2 = P.train_horizon(cfg, loaded, (snap, manifest), tmp_path / "out", run_id="r2")
    inc = json.loads(mod.analyze_run(Path(res2["run_dir"]), tmp_path / "snap", tmp_path / "re", None).read_text(encoding="utf-8"))
    assert inc["claim_level"] == "none" and inc["claim_block_reasons"] == ["training_contract_incomplete"] and inc["source_run"]["snapshot_validation"]["compared_with_dataset"] is False
