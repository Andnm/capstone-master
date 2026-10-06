"""GPT file 60 C59-M1: goi Colab phai mang bang chung N1 cua CHINH artifact; training verifier kiem snapshot cho MOI dataset co n1_policy (khong chi --official), khong can repo/backend/MySQL."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("sklearn")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dataset_builder import n1_policy as n1  # noqa: E402
from test_training import make_dataset, write_checksums  # noqa: E402
from training.provenance import DatasetVerificationError, verify_dataset  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
N1_FILES = ["n1_policy_v1.json", "n1_policy_input.json", "n1_affected_items.json", "n1_hotels_from_scan.json", "n1_matcher_replay.json", "n1_scan_identity.json", "parity_on_artifacts.out"]


def _packager():
    spec = importlib.util.spec_from_file_location("package_for_colab", SCRIPTS / "package_for_colab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def packaged(tmp_path):
    dataset = make_dataset(tmp_path / "src", n_days=30, n_series=4, version="ds_n1pkg", evaluation_horizons=(7,), n1=True)       # rehearsal + N1 snapshot that
    out = tmp_path / "pkg"
    manifest = _packager().build_package(out, dataset, stamp="20261006_000000")
    extracted = tmp_path / "colab"
    for name in manifest["archives"]:
        with zipfile.ZipFile(out / name) as archive:
            archive.extractall(extracted)
    return dataset, out, manifest, extracted, extracted / "ds_n1pkg"


def _rewrite(dataset: Path):
    write_checksums(dataset, rows=len(pd.read_parquet(dataset / "samples.parquet")))


def test_dataset_zip_carries_every_n1_input_and_the_extracted_copy_verifies(packaged):
    dataset, out, manifest, extracted, copy = packaged
    with zipfile.ZipFile(out / "dataset_ds_n1pkg.zip") as archive:
        names = set(archive.namelist())
    assert {f"ds_n1pkg/inputs/n1_policy/{n}" for n in N1_FILES} <= names
    meta = verify_dataset(copy)
    assert meta["contract"]["n1_policy"]["policy_version"] == "n1-policy-1.2.0" and len(meta["contract"]["n1_policy"]["excluded_hotels"]) == 5
    assert "ml/dataset_builder/n1_policy.py" in json.loads((extracted / "ml" / "CODE_MANIFEST.json").read_text(encoding="utf-8"))["files"]


def test_extracted_code_zip_verifies_the_dataset_without_the_repo_or_backend(packaged, tmp_path):
    """Chay verifier trong tien trinh MOI chi voi `ml/` da giai nen (khong repo, khong backend): module phai nap tu goi, khong tu ban live."""
    dataset, out, manifest, extracted, copy = packaged
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]);\n"
            "from training.provenance import verify_dataset\nimport training.provenance as tp, dataset_builder.n1_policy as n1\n"
            "assert tp.__file__.startswith(sys.argv[1]) and n1.__file__.startswith(sys.argv[1]), (tp.__file__, n1.__file__)\n"
            "assert not any('backend' in m for m in sys.modules if m.startswith('app')), 'backend duoc nap'\n"
            "meta = verify_dataset(sys.argv[2]); print(json.dumps({'n1': meta['contract']['n1_policy']['policy_version']}))\n")
    done = subprocess.run([sys.executable, "-c", code, str(extracted / "ml"), str(copy)], capture_output=True, text=True, cwd=str(isolated), timeout=120,
                          env={"PATH": __import__("os").environ.get("PATH", ""), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    assert done.returncode == 0, done.stderr[-1200:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {"n1": "n1-policy-1.2.0"}


@pytest.mark.parametrize("name", ["n1_policy_v1.json", "n1_policy_input.json", "n1_affected_items.json", "n1_matcher_replay.json", "n1_scan_identity.json"])
def test_missing_n1_file_is_rejected_even_if_the_checksum_list_is_rewritten(packaged, name):
    dataset, out, manifest, extracted, copy = packaged
    (copy / "inputs" / "n1_policy" / name).unlink()
    _rewrite(copy)                                                                          # tan cong ghi lai checksum cho khop cac file con lai
    with pytest.raises(DatasetVerificationError, match="n1_policy: .*(thieu|khong doc)"):
        verify_dataset(copy)


def test_tampered_evidence_policy_or_manifest_is_rejected_after_rewriting_checksums(packaged):
    dataset, out, manifest, extracted, copy = packaged
    base = copy / "inputs" / "n1_policy"
    for name, message in (("n1_matcher_replay.json", "sha256 that khac"), ("n1_policy_v1.json", "policy_sha256"), ("n1_affected_items.json", "sha256 that khac")):
        original = (base / name).read_bytes()
        (base / name).write_bytes(original + b" ")
        _rewrite(copy)                                                                       # checksum khop file da bi sua => chi doi chieu policy/contract moi bat duoc
        with pytest.raises(DatasetVerificationError, match=message):
            verify_dataset(copy)
        (base / name).write_bytes(original)
        _rewrite(copy)
    verify_dataset(copy)                                                                     # khoi phuc nguyen ven => lai hop le
    manifest_path = base / "n1_policy_input.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["pinned_in_config"]["excluded_hotels"] = ["only-one-hotel"]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    _rewrite(copy)
    with pytest.raises(DatasetVerificationError, match="khong nhat quan|khac descriptor"):
        verify_dataset(copy)


def test_checksum_entry_removed_or_summary_mismatch_is_rejected(packaged):
    dataset, out, manifest, extracted, copy = packaged
    checks = json.loads((copy / "output_checksums.json").read_text(encoding="utf-8"))
    del checks["inputs/n1_policy/n1_matcher_replay.json"]
    (copy / "output_checksums.json").write_text(json.dumps(checks), encoding="utf-8")
    with pytest.raises(DatasetVerificationError, match="n1_matcher_replay.json khong co trong output_checksums"):
        verify_dataset(copy)
    _rewrite(copy)
    contract = json.loads((copy / "dataset_contract.json").read_text(encoding="utf-8"))
    for field, value in (("policy_sha256", "1" * 64), ("policy_version", "n1-policy-9.9.9"), ("excluded_hotels", ["a", "b"])):
        bad = {**contract, "n1_policy": {**contract["n1_policy"], field: value}}
        (copy / "dataset_contract.json").write_text(json.dumps(bad), encoding="utf-8")
        _rewrite(copy)
        with pytest.raises(DatasetVerificationError, match="n1_policy"):
            verify_dataset(copy)


def test_training_cli_creates_no_run_when_the_n1_snapshot_is_broken(packaged, tmp_path, monkeypatch):
    dataset, out, manifest, extracted, copy = packaged
    (copy / "inputs" / "n1_policy" / "n1_matcher_replay.json").unlink()
    _rewrite(copy)
    spec = importlib.util.spec_from_file_location("train_models", SCRIPTS / "train_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["train_models.py", "--dataset-dir", str(copy), "--models", "ridge", "--horizons", "7", "--output-root", str(tmp_path / "models"), "--run-id", "r1"])
    assert module.main() == 3
    assert not (tmp_path / "models").exists()


def test_packager_refuses_a_broken_snapshot_before_creating_any_archive(tmp_path):
    dataset = make_dataset(tmp_path / "src", n_days=30, n_series=4, version="ds_n1bad", evaluation_horizons=(7,), n1=True)
    (dataset / "inputs" / "n1_policy" / "n1_hotels_from_scan.json").write_bytes(b"[]")        # checksum DB cua artifact khong con khop
    out = tmp_path / "pkg"
    with pytest.raises(ValueError, match="snapshot N1 cua dataset khong hop le"):
        _packager().build_package(out, dataset, stamp="20261006_000001")
    assert not out.exists() or not list(out.iterdir())
    shutil.rmtree(dataset / "inputs" / "n1_policy")                                           # thieu han ca thu muc
    with pytest.raises(ValueError, match="thieu inputs/n1_policy/n1_policy_input.json"):
        _packager().build_package(out, dataset, stamp="20261006_000002")
    assert not out.exists() or not list(out.iterdir())


def test_legacy_dataset_without_n1_policy_still_packages_and_verifies(tmp_path):
    dataset = make_dataset(tmp_path / "src", n_days=30, n_series=4, version="ds_legacy", evaluation_horizons=(7,), n1=False)
    manifest = _packager().build_package(tmp_path / "pkg", dataset, stamp="20261006_000003")
    with zipfile.ZipFile(tmp_path / "pkg" / "dataset_ds_legacy.zip") as archive:
        assert not any("n1_policy" in n for n in archive.namelist())
    assert verify_dataset(dataset)["contract"]["n1_policy"] is None and "dataset_ds_legacy.zip" in manifest["archives"]
