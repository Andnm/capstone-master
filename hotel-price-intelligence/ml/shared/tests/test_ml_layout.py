"""Layout migration contracts; no DB, training, or rewriting historical artifacts."""
from __future__ import annotations

import hashlib
import ast
import importlib.util
import json
from pathlib import Path

import pytest

ML = Path(__file__).resolve().parents[2]
PROJECT = ML.parent
WORKSPACE = PROJECT.parent


def script(name):
    spec = importlib.util.spec_from_file_location(f"layout_{name}", ML / "regression" / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_environment_resolves_the_project_and_keeps_dataset_exports_outside_task_artifacts():
    from dataset_builder import env

    assert env.ML_DIR == ML
    assert env.BACKEND_DIR == PROJECT / "backend"
    assert env.HOLIDAYS_CSV == PROJECT / "data" / "vn_holidays.csv"
    assert env.DATASET_OUTPUT_ROOT == WORKSPACE / "outputs" / "datasets"


def test_legacy_root_import_bridge_loads_only_the_real_shared_initializer_without_dotenv():
    import subprocess
    import sys

    code = (
        "import sys,pathlib;sys.path.insert(0,sys.argv[1]);import dataset_builder;"
        "assert pathlib.Path(dataset_builder.__file__).resolve()==pathlib.Path(sys.argv[1],'shared','dataset_builder','__init__.py').resolve();"
        "assert 'dotenv' not in sys.modules and 'app.core.config' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(ML)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_shared_builder_identity_pins_the_relocated_source_and_both_cli_entrypoints():
    from dataset_builder.code_identity import builder_code_manifest, diff_manifests

    manifest = builder_code_manifest()
    files = manifest["files"]
    assert "hotel-price-intelligence/ml/shared/dataset_builder/features.py" in files
    assert "hotel-price-intelligence/ml/shared/scripts/init_dataset_build.py" in files
    assert "hotel-price-intelligence/ml/shared/scripts/build_dataset.py" in files
    # A relocation is not silently normalized back into a historical code identity.
    old = {k.replace("/ml/shared/", "/ml/"): v for k, v in files.items()}
    delta = diff_manifests(old, files)
    assert delta["added"] and delta["removed"]


@pytest.mark.parametrize("name", ["train_models", "train_models_v3"])
def test_local_regression_cli_defaults_to_its_task_models_directory(name):
    assert script(name).DEFAULT_OUTPUT_ROOT == ML / "regression" / "models"


@pytest.mark.parametrize("name", ["train_models", "train_models_v3"])
def test_portable_package_default_does_not_write_models_into_its_verified_code_tree(name):
    source = (ML / "regression" / "scripts" / f"{name}.py").read_text(encoding="utf-8")
    assignments = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "DEFAULT_OUTPUT_ROOT" for t in n.targets)]
    ns = {"ML_DIR": Path("/content/ml")}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), "defaults", "exec"), ns)
    assert ns["DEFAULT_OUTPUT_ROOT"] == Path("/content/models")


def test_local_packager_defaults_to_task_colab_without_creating_a_package(monkeypatch):
    import sys

    pkg = script("package_for_colab")
    calls = []
    monkeypatch.setattr(pkg, "build_package", lambda out, dataset: calls.append((out, dataset)) or {"archives": {}})
    monkeypatch.setattr(sys, "argv", ["package_for_colab.py"])
    assert pkg.main() == 0
    assert calls == [(ML / "regression" / "colab", None)]


def test_new_portable_packages_keep_the_standalone_flat_contract_and_exclude_local_artifacts():
    pkg = script("package_for_colab")
    files = pkg.package_files()
    names = {arc for _, arc in files}
    assert "ml/training/v3_bundle.py" in names
    assert "ml/dataset_builder/feature_spec.py" in names
    assert "ml/scripts/train_models_v3.py" in names
    assert all(p.is_file() for p, _ in files)
    assert all(not any(part in {"models", "results", "colab", "tests"} for part in Path(arc).parts) for _, arc in files)


def test_relocated_historical_artifacts_are_byte_identical_when_available_locally():
    manifest = json.loads((ML / "LAYOUT_MIGRATION_MANIFEST.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 99
    assert sum(r["bytes"] for r in manifest["files"]) == 91_270_025
    present = 0
    for entry in manifest["files"]:
        p = WORKSPACE / entry["new_path"]
        if not p.exists():
            continue  # generated/ignored artifacts are not required in a fresh Git checkout
        present += 1
        assert p.stat().st_size == entry["bytes"]
        assert hashlib.sha256(p.read_bytes()).hexdigest() == entry["sha256"], entry["new_path"]
    if not present:
        pytest.skip("historical local model/Colab artifacts are intentionally gitignored")


def test_artifact_roots_are_ignored_but_source_packages_are_not():
    import subprocess

    for task in ("regression", "classification"):
        for directory in ("models", "results", "colab"):
            path = ML / task / directory / "layout-probe.json"
            result = subprocess.run(["git", "-C", str(WORKSPACE), "check-ignore", str(path)], capture_output=True)
            assert result.returncode == 0
    result = subprocess.run(["git", "-C", str(WORKSPACE), "check-ignore", str(ML / "regression" / "training" / "runner.py")], capture_output=True)
    assert result.returncode == 1
