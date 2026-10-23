"""M2 (GPT 10): công cụ dựng registry KHÔNG tự cấp 'validated'; hash sai ⇒ chưa deserialize; không ghi đè registry.json. Workspace giả, bundle giả, không joblib/fit/DB."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.ml_serving import registry as R
from app.ml_serving import registry_tool as T
from app.ml_serving.errors import IntegrityError

GOOD = {"python": "3.13.9", "scikit-learn": "1.7.2", "numpy": "2.1.3", "pandas": "2.3.3", "joblib": "1.5.2"}
TRAINED = {"python": "3.13.15", "scikit-learn": "1.6.1", "numpy": "2.1.3", "pandas": "2.2.3", "joblib": "1.6.0"}
BACKEND = Path(__file__).resolve().parents[1]


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Spy:
    def __init__(self):
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        return {"name": "m", "family": "hgb_l1", "horizon": 1, "matrix": "tree", "target_transform": "log_ratio", "claim_level": "dev_research_exploratory", "n_features_in": 2,
                "feature_list_sha256": "a" * 64, "effective_feature_list_sha256": "b" * 64, "environment_actual": dict(TRAINED), "features": ["f1", "f2"]}


def workspace(tmp_path, *, manifest_hash=None, bundle=b"BUNDLE"):
    run = tmp_path / "hotel-price-intelligence" / "ml" / "models" / "run1"
    run.mkdir(parents=True)
    (tmp_path / "outputs" / "datasets").mkdir(parents=True)
    (run / "b.joblib").write_bytes(bundle)
    (run / "p.parquet").write_bytes(b"P")
    (run / "r.json").write_bytes(b"{}")
    (run / "run_manifest.json").write_text(json.dumps({"outputs": {"b.joblib": manifest_hash or sha(bundle)}}), encoding="utf-8")
    spec = T.BundleSpec("b1", "regression_primary", 1, "ds", "hotel-price-intelligence/ml/models/run1", "b.joblib", "p.parquet", "r.json", "m")
    return spec


def approved_registry(spec, ws):
    cands = T.build_candidate(workspace_root=ws, existing=None, specs=[spec], code_dependencies=[], nullable_features=["f2"], datasets=[], runtime=GOOD, loader=Spy())
    reg = json.loads(json.dumps(cands.registry))
    entry = reg["bundles"][0]
    del entry["observedRuntime"]
    entry["validatedRuntime"] = dict(GOOD)
    entry["compatibility"] = {"status": R.COMPAT_STATUS, "evidence": "full replay (reviewed)", "toleranceVnd": 0.01}
    return reg


def test_new_bundle_is_only_a_candidate_never_checked(tmp_path):
    spec = workspace(tmp_path)
    res = T.build_candidate(workspace_root=tmp_path, existing=None, specs=[spec], code_dependencies=[], nullable_features=["f2"], datasets=[], runtime=GOOD, loader=Spy())
    entry = res.registry["bundles"][0]
    assert res.approved is False and entry["compatibility"]["status"] == T.CANDIDATE_STATUS and "validatedRuntime" not in entry and entry["observedRuntime"]["pandas"] == "2.3.3"
    assert R.COMPAT_STATUS not in json.dumps(res.registry)
    with pytest.raises(R.RegistryError):                                          # ứng viên không nạp nhầm được làm registry
        path = tmp_path / "candidate.json"
        path.write_text(json.dumps(res.registry), encoding="utf-8")
        R.load_registry(path, tmp_path)


def test_fake_runtime_outside_approval_cannot_produce_checked_status(tmp_path):
    spec = workspace(tmp_path)
    existing = approved_registry(spec, tmp_path)
    for fake in ({**GOOD, "pandas": "9.9.9"}, {**GOOD, "scikit-learn": "0.0.1"}, {**GOOD, "joblib": None}):
        res = T.build_candidate(workspace_root=tmp_path, existing=existing, specs=[spec], code_dependencies=[], nullable_features=["f2"], datasets=[], runtime=fake, loader=Spy())
        entry = res.registry["bundles"][0]
        assert res.approved is False and entry["compatibility"]["status"] == T.CANDIDATE_STATUS and "validatedRuntime" not in entry
        assert R.COMPAT_STATUS not in json.dumps(res.registry) and any("runtime hiện tại ngoài validatedRuntime" in f for f in res.findings)


def test_unchanged_everything_carries_over_approval_verbatim_and_is_idempotent(tmp_path):
    spec = workspace(tmp_path)
    existing = approved_registry(spec, tmp_path)
    res = T.build_candidate(workspace_root=tmp_path, existing=existing, specs=[spec], code_dependencies=[], nullable_features=["f2"], datasets=[], runtime=GOOD, loader=Spy())
    assert res.approved is True and res.findings == [] and res.registry["bundles"][0]["validatedRuntime"] == GOOD and res.registry == existing


def test_code_dependency_change_invalidates_approval(tmp_path):
    spec = workspace(tmp_path)
    existing = approved_registry(spec, tmp_path)
    res = T.build_candidate(workspace_root=tmp_path, existing=existing, specs=[spec], code_dependencies=[{"path": "hotel-price-intelligence/ml/x.py", "sha256": "0" * 64}],
                            nullable_features=["f2"], datasets=[], runtime=GOOD, loader=Spy())
    assert res.approved is False and res.registry["bundles"][0]["compatibility"]["status"] == T.CANDIDATE_STATUS and any("mã phụ thuộc" in f for f in res.findings)


def test_bundle_hash_not_matching_training_manifest_stops_before_deserialize(tmp_path):
    spec = workspace(tmp_path, manifest_hash="0" * 64)
    spy = Spy()
    with pytest.raises(IntegrityError, match="run_manifest"):
        T.build_candidate(workspace_root=tmp_path, existing=None, specs=[spec], code_dependencies=[], nullable_features=[], datasets=[], runtime=GOOD, loader=spy)
    assert spy.calls == []


def test_bundle_hash_not_matching_approved_registry_stops_before_deserialize(tmp_path):
    spec = workspace(tmp_path)
    existing = approved_registry(spec, tmp_path)
    (tmp_path / "hotel-price-intelligence/ml/models/run1/b.joblib").write_bytes(b"TAMPERED")
    (tmp_path / "hotel-price-intelligence/ml/models/run1/run_manifest.json").write_text(json.dumps({"outputs": {"b.joblib": sha(b"TAMPERED")}}), encoding="utf-8")   # manifest "khớp" file mới
    spy = Spy()
    with pytest.raises(IntegrityError, match="registry đã duyệt"):
        T.build_candidate(workspace_root=tmp_path, existing=existing, specs=[spec], code_dependencies=[], nullable_features=["f2"], datasets=[], runtime=GOOD, loader=spy)
    assert spy.calls == []


def test_missing_manifest_entry_or_path_outside_roots_is_rejected(tmp_path):
    spec = workspace(tmp_path)
    (tmp_path / "hotel-price-intelligence/ml/models/run1/run_manifest.json").write_text(json.dumps({"outputs": {}}), encoding="utf-8")
    with pytest.raises(IntegrityError):
        T.build_candidate(workspace_root=tmp_path, existing=None, specs=[spec], code_dependencies=[], nullable_features=[], datasets=[], runtime=GOOD, loader=Spy())
    bad = T.BundleSpec("b2", "regression_primary", 1, "ds", "somewhere/else", "b.joblib", "p.parquet", "r.json", "m")
    with pytest.raises(IntegrityError):
        T.build_candidate(workspace_root=tmp_path, existing=None, specs=[bad], code_dependencies=[], nullable_features=[], datasets=[], runtime=GOOD, loader=Spy())


def run_cli(*args):
    import os

    return subprocess.run([sys.executable, "-B", "scripts/build_registry.py", *args], cwd=BACKEND, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})


def test_cli_refuses_to_write_registry_json_and_real_registry_is_reproduced():
    refuse = run_cli("--candidate-out", str(R.REGISTRY_PATH))
    assert refuse.returncode == 2 and "không bao giờ ghi registry.json" in refuse.stdout
    verify = run_cli()
    assert verify.returncode == 0 and "KHÔNG ĐỔI" in verify.stdout, verify.stdout + verify.stderr
