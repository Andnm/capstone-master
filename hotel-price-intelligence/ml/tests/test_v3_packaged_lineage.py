"""M1 (GPT file 16): chay DONG GOI phai noi mat ma COLAB_MANIFEST voi code + dataset dang chay, doc lap official (v3 luon official=False), va tap file code phai bang CODE_MANIFEST.
MIN1: horizon ngoai whitelist bi tu choi truoc moi output. Moi truong hop sai phai FAIL (ma 3 / 2) TRUOC khi tao thu muc/fit."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from v3_fixtures import make_v3_dataset

ML = Path(__file__).resolve().parents[1]
FAST = ["--smoke", "--smoke-families", "hgb_l1", "--smoke-n-iter", "2", "--smoke-null-n", "2", "--device", "cpu", "--allow-env-drift"]


def _pkg_module():
    spec = importlib.util.spec_from_file_location("package_for_colab", ML / "scripts" / "package_for_colab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Hai dataset khac nhau, moi dataset mot goi (stamp rieng); goi A duoc giai nen mot lan (read-only cho test) va moi test copy khi can sua."""
    root = tmp_path_factory.mktemp("lineage")
    pkg = _pkg_module()
    ds_a, ds_b = make_v3_dataset(root / "src", version="ds_lin_a"), make_v3_dataset(root / "src", version="ds_lin_b", n_hotels=18)
    pkg.build_package(root / "out_a", ds_a, stamp="la1")
    pkg.build_package(root / "out_b", ds_b, stamp="lb1")
    return {"root": root, "out_a": root / "out_a", "out_b": root / "out_b", "ds_a": "ds_lin_a"}


def _extract(world, tmp_path, name="run"):
    run_dir = tmp_path / name
    zipfile.ZipFile(world["out_a"] / "ml_train_pkg_la1.zip").extractall(run_dir)
    zipfile.ZipFile(world["out_a"] / "dataset_ds_lin_a.zip").extractall(run_dir)
    return run_dir


def _cli(run_dir, *extra, manifest=True, run_id="smoke_lin", manifest_path=None, env=None):
    cmd = [sys.executable, "-I", str(run_dir / "ml" / "scripts" / "train_models_v3.py"), "--dataset-dir", str(run_dir / "ds_lin_a"), *FAST,
           "--output-root", str(run_dir / "models"), "--run-id", run_id, *extra]
    if manifest:
        cmd += ["--colab-manifest", str(manifest_path)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(run_dir), timeout=900, env=env)


def _no_output(run_dir):
    return not (run_dir / "models").exists()


def test_correct_package_and_manifest_pass_and_stay_non_official(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    done = _cli(run_dir, manifest_path=world["out_a"] / "COLAB_MANIFEST.json")
    assert done.returncode == 0, done.stderr[-2000:]
    manifest = json.loads((run_dir / "models" / "ds_lin_a" / "smoke_lin" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["official"] is False and manifest["packaged_execution"]["packaged"] is True and manifest["packaged_execution"]["manifest_files"] >= 30


def test_manifest_of_another_dataset_is_rejected_before_any_output(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    done = _cli(run_dir, manifest_path=world["out_b"] / "COLAB_MANIFEST.json")
    assert done.returncode == 3 and "goi Colab khong noi duoc" in done.stderr and _no_output(run_dir)


@pytest.mark.parametrize("mutation", ["created_at", "code_manifest_sha256", "code_sha256", "drop_dataset_archive", "schema_version"])
def test_tampered_manifest_fields_are_rejected_before_any_output(world, tmp_path, mutation):
    run_dir = _extract(world, tmp_path)
    data = json.loads((world["out_a"] / "COLAB_MANIFEST.json").read_text(encoding="utf-8"))
    if mutation == "created_at":
        data["created_at"] = "20200101_000000"
    elif mutation == "code_manifest_sha256":
        data["code_manifest_sha256"] = "0" * 64
    elif mutation == "code_sha256":
        data["code_sha256"] = "f" * 64
    elif mutation == "drop_dataset_archive":
        data["archives"] = {k: v for k, v in data["archives"].items() if not k.startswith("dataset_")}
    else:
        data["schema_version"] = 1
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(data), encoding="utf-8")
    done = _cli(run_dir, manifest_path=forged)
    assert done.returncode == 3 and "goi Colab khong noi duoc" in done.stderr and _no_output(run_dir)


def test_missing_manifest_is_rejected_for_packaged_execution(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    done = _cli(run_dir, manifest=False)
    assert done.returncode == 3 and "thieu --colab-manifest" in done.stderr and _no_output(run_dir)


def test_stale_extra_code_file_left_in_the_extraction_directory_is_rejected(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    (run_dir / "ml" / "training" / "v3_stale.py").write_text("STALE = True\n", encoding="utf-8")                  # code v3 cu con sot, khong nam trong CODE_MANIFEST
    done = _cli(run_dir, manifest_path=world["out_a"] / "COLAB_MANIFEST.json")
    assert done.returncode == 3 and "v3_stale.py" in done.stderr and "khac CODE_MANIFEST" in done.stderr and _no_output(run_dir)


def test_incomplete_code_manifest_is_rejected_even_when_it_and_the_colab_manifest_are_made_self_consistent(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    manifest_path = run_dir / "ml" / "CODE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["files"]["ml/training/v3_cv.py"]                                                                    # manifest 'cu' khong phu mot module v3 dang thuc thi
    manifest["code_sha256"] = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    new_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    manifest_path.write_bytes(new_bytes)
    colab = json.loads((world["out_a"] / "COLAB_MANIFEST.json").read_text(encoding="utf-8"))
    colab["code_manifest_sha256"], colab["code_sha256"] = hashlib.sha256(new_bytes).hexdigest(), manifest["code_sha256"]
    forged = tmp_path / "forged_consistent.json"
    forged.write_text(json.dumps(colab), encoding="utf-8")
    done = _cli(run_dir, manifest_path=forged)
    assert done.returncode == 3 and "v3_cv.py" in done.stderr and _no_output(run_dir)                              # lineage hop le nhung tap file != manifest => chan


def test_tampered_code_file_is_rejected_by_the_existing_manifest_hash_check(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    path = run_dir / "ml" / "training" / "v3_select.py"
    path.write_text(path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    done = _cli(run_dir, manifest_path=world["out_a"] / "COLAB_MANIFEST.json")
    assert done.returncode == 3 and _no_output(run_dir)


def test_horizon_outside_the_whitelist_or_without_pinned_control_params_exits_2_before_output(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    outside = _cli(run_dir, "--horizons", "3", manifest_path=world["out_a"] / "COLAB_MANIFEST.json")
    assert outside.returncode == 2 and "ngoai evaluation_horizons" in outside.stderr and _no_output(run_dir)
    unsupported = _cli(run_dir, "--horizons", "7", manifest_path=world["out_a"] / "COLAB_MANIFEST.json")
    assert unsupported.returncode == 2 and "chua co tham so da chot" in unsupported.stderr and _no_output(run_dir)


def _no_git_env(root):
    """Moi truong khong cho git tim repo tu tien (vd %TEMP% nam duoi mot repo khac) - de ca 'khong co git' deu xac dinh."""
    import os
    return {**os.environ, "GIT_CEILING_DIRECTORIES": str(Path(root).resolve().parent), "GIT_DIR": str(Path(root) / "no-such-git-dir")}


def _drop_code_manifest(run_dir):
    (run_dir / "ml" / "CODE_MANIFEST.json").unlink()


def test_r2m1_extraction_without_code_manifest_and_without_git_is_rejected_even_with_no_colab_manifest(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    _drop_code_manifest(run_dir)
    done = _cli(run_dir, manifest=False, env=_no_git_env(run_dir))                                     # thieu CA HAI manifest, khong co git
    assert done.returncode == 3 and "provenance code khong xac dinh" in done.stderr and _no_output(run_dir)


def test_r2m1_supplied_colab_manifest_without_code_manifest_is_rejected_not_downgraded_to_git(world, tmp_path):
    run_dir = _extract(world, tmp_path)
    _drop_code_manifest(run_dir)
    done = _cli(run_dir, manifest_path=world["out_a"] / "COLAB_MANIFEST.json", env=_no_git_env(run_dir))
    assert done.returncode == 3 and "COLAB_MANIFEST" in done.stderr and "CODE_MANIFEST" in done.stderr and _no_output(run_dir)


@pytest.mark.parametrize("with_colab", [False, True])
def test_r2m1_extraction_under_an_unrelated_git_repo_is_not_taken_as_a_verified_git_tree(world, tmp_path, with_colab):
    import shutil
    if shutil.which("git") is None:
        pytest.skip("can git")
    repo = tmp_path / "unrelated_repo"
    repo.mkdir()
    for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    run_dir = _extract(world, repo)                                                                    # goi nam duoi repo khac (HEAD doc duoc, file code la untracked)
    _drop_code_manifest(run_dir)
    done = _cli(run_dir, manifest=with_colab, manifest_path=world["out_a"] / "COLAB_MANIFEST.json" if with_colab else None)
    assert done.returncode == 3 and _no_output(run_dir)
    assert ("COLAB_MANIFEST" in done.stderr) if with_colab else ("khong duoc git theo doi" in done.stderr)


def test_r2m1_the_tracked_repo_tree_still_runs_from_git_as_non_official(tmp_path):
    import shutil
    if shutil.which("git") is None or subprocess.run(["git", "-C", str(ML), "rev-parse", "HEAD"], capture_output=True).returncode != 0:
        pytest.skip("can cay git cua repo")
    ds = make_v3_dataset(tmp_path / "src", version="ds_lin_git")
    out = tmp_path / "models"
    done = subprocess.run([sys.executable, str(ML / "scripts" / "train_models_v3.py"), "--dataset-dir", str(ds), *FAST, "--output-root", str(out), "--run-id", "smoke_git"],
                          capture_output=True, text=True, timeout=900)
    assert done.returncode == 0, done.stderr[-2000:]
    manifest = json.loads((out / "ds_lin_git" / "smoke_git" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["official"] is False and manifest["packaged_execution"]["packaged"] is False and manifest["packaged_execution"]["execution_source"] == "git"


def test_r2m1_unit_gate_covers_every_provenance_source():
    from training.provenance import ProvenanceError
    from training.v3_runtime import verify_packaged_execution

    git = {"source": "git", "head": "abc123def456", "ml_dirty": False}
    got = verify_packaged_execution(git, None, "ds_x", tracked_check=lambda files, repo: [])
    assert got["packaged"] is False and got["execution_source"] == "git" and got["git_tracked_files"] >= 1
    with pytest.raises(ProvenanceError, match="khong duoc git theo doi"):
        verify_packaged_execution(git, None, "ds_x", tracked_check=lambda files, repo: [str(files[0])])
    with pytest.raises(ProvenanceError, match="khong xac dinh"):
        verify_packaged_execution({"source": "unknown", "error": "x"}, None, "ds_x", tracked_check=lambda files, repo: [])
    with pytest.raises(ProvenanceError, match="khong xac dinh"):
        verify_packaged_execution({"source": "git", "head": ""}, None, "ds_x", tracked_check=lambda files, repo: [])
    with pytest.raises(ProvenanceError, match="COLAB_MANIFEST"):
        verify_packaged_execution(git, {"content": {}}, "ds_x", tracked_check=lambda files, repo: [])         # co COLAB nhung code khong co CODE_MANIFEST
