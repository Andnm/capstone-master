"""Notebook Colab v3: nguon output-free, cell chay duoc cu phap, mac dinh fail-closed, cell moi truong khong cai im lang, cell tom tat doc dung run PASS that."""
from __future__ import annotations

import importlib.metadata as md
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from training.v3_contract import DEFAULT_CONFIG_V3
from v3_fixtures import make_v3_dataset

NOTEBOOK = Path(__file__).resolve().parents[1] / "notebooks" / "train_colab_v3.ipynb"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_models_v3.py"


def _cells():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def _code(cell) -> str:
    return "".join(cell["source"])


def _strip_magics(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("!") and "google.colab" not in line and not line.startswith("drive.mount"))


def test_source_is_output_free_and_every_code_cell_compiles():
    cells = [c for c in _cells() if c["cell_type"] == "code"]
    assert len(cells) == 6
    for index, cell in enumerate(cells):
        assert cell.get("execution_count") is None and cell.get("outputs") == [], index
        compile(_strip_magics(_code(cell)), f"cell{index}", "exec")
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4 and nb["nbformat_minor"] == 5 and all(c.get("id") for c in nb["cells"])


def test_extraction_cell_cleans_previous_code_and_dataset_before_unzipping():
    cell = _code(_cells()[2])
    assert cell.index('shutil.rmtree("/content/ml"') < cell.index("extractall") and "shutil.rmtree(f\"/content/datasets/{DATASET_VERSION}\"" in cell
    assert "SHA-256 lệch" in cell and 'startswith("ml_train_pkg")' in cell


def test_defaults_are_fail_closed_non_official_and_horizons_follow_the_dataset_whitelist():
    params = _code(_cells()[1])
    assert "ALLOW_ENV_DRIFT = False" in params and 'HORIZONS = ""' in params and 'DEVICE = "auto"' in params and "official" not in params.lower()
    run_cell = _code(_cells()[4])
    assert "train_models_v3.py" in run_cell and "--official" not in run_cell and "HORIZONS_FLAG" in run_cell and "--colab-manifest" in run_cell and "--run-id {RUN_ID}" in run_cell
    text = "\n".join("".join(c["source"]) for c in _cells())
    assert "Không trộn hai gói" in text and "FAIL-CLOSED" in text and "dev_research_exploratory" in text and "Restart" in text
    assert "1,3,7,14" not in params


def _run_setup_cell(monkeypatch, *, actual, allow, tmp_yaml=None):
    source = _strip_magics(_code(_cells()[3])).replace("/content/ml/configs/train_v3.yaml", Path(tmp_yaml or DEFAULT_CONFIG_V3).as_posix())
    calls = []
    monkeypatch.setattr(md, "version", lambda package: actual[package] if actual.get(package) is not None else (_ for _ in ()).throw(md.PackageNotFoundError(package)))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    namespace = {"ALLOW_ENV_DRIFT": allow}
    return namespace, calls, source


def test_environment_cell_never_installs_silently_and_demands_a_restart(monkeypatch):
    import yaml

    expected = yaml.safe_load(DEFAULT_CONFIG_V3.read_text(encoding="utf-8"))["environment"]["expected_versions"]
    namespace, calls, source = _run_setup_cell(monkeypatch, actual=expected, allow=False)
    exec(compile(source, "env_ok", "exec"), namespace)                                       # khop: khong cai, khong loi
    assert calls == [] and namespace["mismatch"] == {}
    drift = dict(expected, **{"scikit-learn": "9.9.9"})
    namespace, calls, source = _run_setup_cell(monkeypatch, actual=drift, allow=False)
    with pytest.raises(RuntimeError, match="Restart session"):
        exec(compile(source, "env_bad", "exec"), namespace)
    assert len(calls) == 1 and "scikit-learn==1.6.1" in calls[0] and "xgboost==3.4.1" in calls[0] and "install" in calls[0]       # chi cai DUNG phien ban ghim
    namespace, calls, source = _run_setup_cell(monkeypatch, actual=drift, allow=True)
    exec(compile(source, "env_allow", "exec"), namespace)                                    # opt-in tuong minh: khong cai, khong dung
    assert calls == [] and "scikit-learn" in namespace["mismatch"]
    namespace, calls, source = _run_setup_cell(monkeypatch, actual=dict(expected, xgboost=None), allow=False)
    with pytest.raises(RuntimeError):
        exec(compile(source, "env_missing", "exec"), namespace)                              # thieu thu vien cung la lech


def test_summary_cells_read_the_pass_run_and_report_both_scales(tmp_path):
    ds = make_v3_dataset(tmp_path / "src", version="ds_v3nb")
    out = tmp_path / "models"
    done = subprocess.run([sys.executable, str(SCRIPT), "--dataset-dir", str(ds), "--output-root", str(out), "--smoke", "--smoke-families", "hgb_l1", "--smoke-n-iter", "2",
                           "--smoke-null-n", "2", "--device", "cpu", "--run-id", "smoke_nb", "--allow-env-drift"], capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    namespace = {"OUT_ROOT": str(out), "DATASET_VERSION": "ds_v3nb", "RUN_ID": "smoke_nb", "pd": pd}
    exec(compile(_strip_magics(_code(_cells()[5])), "summary", "exec"), namespace)
    frame = namespace["summary_df"]
    assert {"horizon", "model", "validation_lift_vnd", "test_lift_vnd", "test_lift_log", "test_acc20", "persistence_acc20", "tie_share"} <= set(frame.columns)
    assert "persistence" in set(frame["model"]) and len(frame) >= 3 and frame["horizon"].eq(1).all()
    exec(compile(_strip_magics(_code(_cells()[6])), "details", "exec"), namespace)                                       # o chi tiet khong duoc loi
    bad = tmp_path / "models" / "ds_v3nb" / "smoke_missing"
    namespace_bad = {"OUT_ROOT": str(out), "DATASET_VERSION": "ds_v3nb", "RUN_ID": "smoke_missing", "pd": pd}
    with pytest.raises(AssertionError, match="PASS"):
        exec(compile(_strip_magics(_code(_cells()[5])), "summary_bad", "exec"), namespace_bad)
    assert not bad.exists()
