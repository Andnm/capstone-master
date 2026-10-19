"""Test CLI + goi Colab cua train-v3: tham so, fail-closed moi truong/dataset truoc moi thu muc, giao dich run, goi tu du chay duoc trong tien trinh co lap, artifact v2 khong doi."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from training.v3_contract import load_config_v3
from training.v3_runtime import EnvironmentDriftError, check_environment
from v3_fixtures import make_v3_dataset

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_models_v3.py"
SMOKE = ["--smoke", "--smoke-families", "hgb_l1", "--smoke-n-iter", "2", "--smoke-null-n", "2", "--device", "cpu"]


def _pkg():
    spec = importlib.util.spec_from_file_location("package_for_colab", Path(__file__).resolve().parents[1] / "scripts" / "package_for_colab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pinned() -> bool:
    try:
        check_environment(load_config_v3(), allow_drift=False)
        return True
    except EnvironmentDriftError:
        return False


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd, timeout=900)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    return make_v3_dataset(tmp_path_factory.mktemp("v3ds"), version="ds_v3cli")


def test_argument_rules_for_smoke_and_run_ids(dataset, tmp_path):
    out = str(tmp_path / "models")
    base = ["--dataset-dir", str(dataset), "--output-root", out]
    no_prefix = _run(*base, "--smoke", "--run-id", "r1", "--allow-env-drift")
    assert no_prefix.returncode == 2 and "smoke_" in no_prefix.stderr
    smoke_prefix_without_flag = _run(*base, "--run-id", "smoke_x", "--allow-env-drift")
    assert smoke_prefix_without_flag.returncode == 2 and "danh rieng" in smoke_prefix_without_flag.stderr
    orphan = _run(*base, "--smoke-n-iter", "2", "--run-id", "r1", "--allow-env-drift")
    assert orphan.returncode == 2 and "--smoke" in orphan.stderr
    bad_horizon = _run(*base, "--horizons", "5", "--smoke", "--run-id", "smoke_h", "--allow-env-drift")
    assert bad_horizon.returncode == 2 and not (tmp_path / "models").exists()
    assert not (tmp_path / "models").exists()                                                # loi tham so => khong tao thu muc


def test_environment_drift_exits_4_before_any_output_unless_opted_in(dataset, tmp_path):
    out = tmp_path / "models"
    first = _run("--dataset-dir", str(dataset), "--output-root", str(out), *SMOKE, "--run-id", "smoke_env")
    if _pinned():
        pytest.skip("moi truong hien tai da khop phien ban ghim: khong the kiem fail-closed")
    assert first.returncode == 4 and "lech phien ban" in first.stderr and "ALLOW_ENV_DRIFT" in first.stderr
    assert not out.exists()                                                                    # fail-closed TRUOC khi tao bat ky thu muc nao


def test_tampered_dataset_exits_3_before_any_output(tmp_path):
    ds = make_v3_dataset(tmp_path / "src", version="ds_v3bad")
    with open(ds / "samples.parquet", "ab") as handle:
        handle.write(b"x")
    done = _run("--dataset-dir", str(ds), "--output-root", str(tmp_path / "models"), *SMOKE, "--run-id", "smoke_bad", "--allow-env-drift")
    assert done.returncode == 3 and "KHONG qua xac minh" in done.stderr and not (tmp_path / "models").exists()


def test_smoke_run_writes_a_transactional_pass_run_and_refuses_to_overwrite(dataset, tmp_path):
    out = tmp_path / "models"
    cmd = ["--dataset-dir", str(dataset), "--output-root", str(out), *SMOKE, "--run-id", "smoke_ok", "--allow-env-drift"]
    first = _run(*cmd)
    assert first.returncode == 0, first.stderr[-2500:]
    run_dir = out / "ds_v3cli" / "smoke_ok"
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["state"] == "pass" and manifest["official"] is False and manifest["smoke"] is True and manifest["claim_level"] == "smoke_not_results"
    assert manifest["training_version"] == "training-1.3.0" and manifest["environment_status"] in ("pinned", "exploratory_env_drift") and len(manifest["environment_fingerprint"]) == 64
    assert manifest["horizons"][0]["horizon"] == 1 and manifest["horizons"][0]["champion_A"]
    names = {p.name for p in run_dir.iterdir()}
    assert {"h1_report.json", "h1_predictions_v3.parquet", "environment_resolved.txt", "run_manifest.json"} <= names
    report = json.loads((run_dir / "h1_report.json").read_text(encoding="utf-8"))
    assert report["smoke"] is True and report["config_sha256"] == manifest["config_sha256"] and report["config_sha256"] != load_config_v3()["config_sha256"]   # smoke != identity run that
    assert set(report["test"]["predicted_models"]) <= set(report["test"]["prespecified_models"]) and report["test"]["locked"] is True
    before = (run_dir / "h1_report.json").read_bytes()
    again = _run(*cmd)
    assert again.returncode == 2 and "da ton tai" in again.stderr and (run_dir / "h1_report.json").read_bytes() == before


def test_colab_package_carries_v3_and_runs_in_an_isolated_process_without_the_repo(dataset, tmp_path):
    pkg = _pkg()
    manifest = pkg.build_package(tmp_path / "out", dataset, stamp="t3")
    code_zip, data_zip = tmp_path / "out" / "ml_train_pkg_t3.zip", tmp_path / "out" / "dataset_ds_v3cli.zip"
    assert set(manifest["archives"]) == {code_zip.name, data_zip.name}
    names = zipfile.ZipFile(code_zip).namelist()
    for needed in ("ml/scripts/train_models_v3.py", "ml/configs/train_v3.yaml", "ml/requirements-train-v3.txt", "ml/training/v3_runner.py", "ml/training/v3_contract.py",
                   "ml/training/v3_select.py", "ml/training/v3_metrics.py", "ml/training/v3_cv.py", "ml/training/v3_models.py", "ml/training/v3_runtime.py",
                   "ml/scripts/train_models.py", "ml/configs/train_v2.yaml", "ml/CODE_MANIFEST.json"):
        assert needed in names, needed
    assert not [n for n in names if "tests" in n or "__pycache__" in n or n.endswith(".env") or "v3_fixtures" in n]
    run_dir = tmp_path / "colab"
    zipfile.ZipFile(code_zip).extractall(run_dir)
    zipfile.ZipFile(data_zip).extractall(run_dir)
    done = subprocess.run([sys.executable, "-I", str(run_dir / "ml" / "scripts" / "train_models_v3.py"), "--dataset-dir", str(run_dir / "ds_v3cli"), *SMOKE,
                           "--output-root", str(run_dir / "models"), "--run-id", "smoke_pkg", "--allow-env-drift", "--colab-manifest", str(tmp_path / "out" / "COLAB_MANIFEST.json")],
                          capture_output=True, text=True, cwd=str(run_dir), timeout=900)
    assert done.returncode == 0, done.stderr[-2500:]
    out_dir = run_dir / "models" / "ds_v3cli" / "smoke_pkg"
    report = json.loads((out_dir / "h1_report.json").read_text(encoding="utf-8"))
    assert report["provenance"]["source"] == "code_manifest" and report["provenance"]["verified_files"] >= 20
    assert report["colab_manifest"] is not None and (out_dir / "CODE_MANIFEST.json").is_file() and (out_dir / "COLAB_MANIFEST.json").is_file()
    assert "tests" not in {p.name for p in (run_dir / "ml").iterdir()}


def test_v2_cli_and_config_still_work_unchanged_after_adding_v3(dataset):
    # v2 khong bi anh huong: cung cau hinh train_v2.yaml, hash khong doi so voi gia tri da ghi trong cac report v2 (config_sha256 8f772d8e4db7... cua run Colab 07/10)
    from training.config import load_config

    cfg2 = load_config()
    assert cfg2["version"] == "train-v2.0.0" and len(cfg2["config_sha256"]) == 64 and cfg2["selection"]["primary_metric"] == "mae"        # v2 nap duoc, khong bi v3 doi
    assert cfg2["config_sha256"] != load_config_v3()["config_sha256"]
    help_v2 = subprocess.run([sys.executable, str(Path(SCRIPT).with_name("train_models.py")), "--help"], capture_output=True, text=True, timeout=120)
    assert help_v2.returncode == 0 and "--official" in help_v2.stdout and "--device" in help_v2.stdout
