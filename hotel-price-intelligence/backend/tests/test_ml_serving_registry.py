"""Registry + nạp bundle FAIL-CLOSED (C1–C4). Dùng workspace giả trong tmp_path và bundle giả (dict) — KHÔNG joblib thật, KHÔNG fit, KHÔNG DB."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.ml_serving import registry as R
from app.ml_serving.errors import IdentityError, IntegrityError, RegistryError, RuntimeMismatchError

GOOD_RUNTIME = {"python": "3.13.9", "scikit-learn": "1.7.2", "numpy": "2.1.3", "pandas": "2.3.3", "joblib": "1.5.2"}
TRAINED = {"python": "3.13.15", "scikit-learn": "1.6.1", "numpy": "2.1.3", "pandas": "2.2.3", "joblib": "1.6.0"}
SENTINEL = object()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Spy:
    def __init__(self, result=None):
        self.calls = []
        self.result = result

    def __call__(self, path):
        self.calls.append(path)
        return self.result


def good_bundle():
    return {"name": "m", "horizon": 1, "matrix": "tree", "target_transform": "log_ratio", "claim_level": "dev_research_exploratory", "n_features_in": 2,
            "feature_list_sha256": "a" * 64, "effective_feature_list_sha256": "b" * 64, "model": SENTINEL, "features": ["f1", "f2"], "routing_policy": {}}


def make_workspace(tmp_path, *, compat=None, trained=None, validated=None, bundle_bytes=b"BUNDLE", dep_bytes=b"CODE"):
    ml = tmp_path / "hotel-price-intelligence" / "ml"
    (ml / "models").mkdir(parents=True)
    (ml / "code").mkdir()
    (tmp_path / "outputs" / "datasets").mkdir(parents=True)
    bundle_path = ml / "models" / "b.joblib"
    bundle_path.write_bytes(bundle_bytes)
    dep_path = ml / "code" / "dep.py"
    dep_path.write_bytes(dep_bytes)
    entry = {"id": "b1", "role": "regression_primary", "horizon": 1, "path": "hotel-price-intelligence/ml/models/b.joblib", "sha256": sha(b"BUNDLE"), "datasetVersion": "ds", "modelName": "m",
             "claimLevel": "dev_research_exploratory", "matrix": "tree", "targetTransform": "log_ratio", "nFeaturesIn": 2, "featureListSha256": "a" * 64, "effectiveFeatureListSha256": "b" * 64,
             "trainedEnvironment": trained or dict(TRAINED), "validatedRuntime": validated or dict(GOOD_RUNTIME),
             "compatibility": compat if compat is not None else {"status": R.COMPAT_STATUS, "evidence": "replay"}}
    raw = {"schema": R.SCHEMA, "codeDependencies": [{"path": "hotel-price-intelligence/ml/code/dep.py", "sha256": sha(b"CODE")}], "nullableFeatures": ["f2"], "bundles": [entry]}
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(raw), encoding="utf-8")
    return R.load_registry(reg_path, tmp_path), bundle_path, dep_path


def test_c1_hash_checked_before_load_and_loader_never_called_on_mismatch(tmp_path):
    reg, bundle_path, _ = make_workspace(tmp_path)
    bundle_path.write_bytes(b"TAMPERED")
    spy = Spy(good_bundle())
    with pytest.raises(IntegrityError):
        R.load_bundle(reg, "b1", loader=spy, runtime=GOOD_RUNTIME)
    assert spy.calls == []                                                          # pickle KHÔNG được deserialize khi hash sai


def test_c1_code_dependency_hash_checked_before_load(tmp_path):
    reg, _, dep_path = make_workspace(tmp_path)
    dep_path.write_bytes(b"EVIL")
    spy = Spy(good_bundle())
    with pytest.raises(IntegrityError):
        R.load_bundle(reg, "b1", loader=spy, runtime=GOOD_RUNTIME)
    assert spy.calls == []


def test_happy_path_loads_once_and_reports_declared_drift(tmp_path):
    reg, _, _ = make_workspace(tmp_path)
    spy = Spy(good_bundle())
    bundle, info = R.load_bundle(reg, "b1", loader=spy, runtime=GOOD_RUNTIME)
    assert len(spy.calls) == 1 and bundle["name"] == "m" and info["sha256"] == sha(b"BUNDLE")
    assert any("scikit-learn 1.6.1 -> 1.7.2" in d for d in info["declaredDrift"]) and any("pandas 2.2.3 -> 2.3.3" in d for d in info["declaredDrift"])


@pytest.mark.parametrize("bad", ["../escape.joblib", "/abs/path.joblib", "C:\\abs\\path.joblib", "hotel-price-intelligence/ml/../../outside.joblib"])
def test_c2_containment_rejects_traversal_and_absolute(tmp_path, bad):
    (tmp_path / "hotel-price-intelligence" / "ml").mkdir(parents=True)
    with pytest.raises(IntegrityError):
        R.resolve_contained(tmp_path, bad, must_exist=False)


def test_c2_containment_rejects_outside_allowed_roots(tmp_path):
    (tmp_path / "hotel-price-intelligence" / "ml").mkdir(parents=True)
    (tmp_path / "outputs" / "datasets").mkdir(parents=True)
    (tmp_path / "somewhere").mkdir()
    (tmp_path / "somewhere" / "x.joblib").write_bytes(b"x")
    with pytest.raises(IntegrityError):
        R.resolve_contained(tmp_path, "somewhere/x.joblib")
    ok = tmp_path / "hotel-price-intelligence" / "ml" / "y.joblib"
    ok.write_bytes(b"y")
    assert R.resolve_contained(tmp_path, "hotel-price-intelligence/ml/y.joblib") == ok.resolve()


def test_c2_symlink_escape_is_rejected(tmp_path):
    ml = tmp_path / "hotel-price-intelligence" / "ml"
    ml.mkdir(parents=True)
    (tmp_path / "outputs" / "datasets").mkdir(parents=True)
    outside = tmp_path / "outside.joblib"
    outside.write_bytes(b"x")
    link = ml / "link.joblib"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("không tạo được symlink (thiếu quyền trên Windows)")
    with pytest.raises(IntegrityError):
        R.resolve_contained(tmp_path, "hotel-price-intelligence/ml/link.joblib")


def test_c2_no_path_comes_from_environment(monkeypatch):
    monkeypatch.setenv("ML_CODE_ROOT", r"C:\evil")
    monkeypatch.setenv("DASHBOARD_MODEL_ROOT", r"C:\evil")
    assert str(R.WORKSPACE_ROOT).lower() != r"c:\evil" and R.WORKSPACE_ROOT == Path(R.__file__).resolve().parents[4]


@pytest.mark.parametrize("field, value", [("pandas", "2.2.3"), ("scikit-learn", "1.6.1"), ("numpy", "2.0.0"), ("joblib", "1.6.0")])
def test_c3_runtime_outside_validated_is_rejected_before_load(tmp_path, field, value):
    reg, _, _ = make_workspace(tmp_path)
    runtime = {**GOOD_RUNTIME, field: value}
    spy = Spy(good_bundle())
    with pytest.raises(RuntimeMismatchError):
        R.load_bundle(reg, "b1", loader=spy, runtime=runtime)
    assert spy.calls == []


def test_c3_python_minor_compared_not_patch(tmp_path):
    reg, _, _ = make_workspace(tmp_path)
    R.load_bundle(reg, "b1", loader=Spy(good_bundle()), runtime={**GOOD_RUNTIME, "python": "3.13.1"})               # khác patch: được
    with pytest.raises(RuntimeMismatchError):
        R.load_bundle(reg, "b1", loader=Spy(good_bundle()), runtime={**GOOD_RUNTIME, "python": "3.12.9"})          # khác minor: chặn


def test_c3_missing_package_in_runtime_is_mismatch(tmp_path):
    reg, _, _ = make_workspace(tmp_path)
    with pytest.raises(RuntimeMismatchError):
        R.load_bundle(reg, "b1", loader=Spy(good_bundle()), runtime={**GOOD_RUNTIME, "joblib": None})


def test_c3_trained_vs_validated_drift_requires_explicit_compatibility(tmp_path):
    reg, _, _ = make_workspace(tmp_path, compat={"status": "supported", "evidence": "x"})
    with pytest.raises(RuntimeMismatchError):
        R.load_bundle(reg, "b1", loader=Spy(good_bundle()), runtime=GOOD_RUNTIME)
    reg2, _, _ = make_workspace(tmp_path / "w2", compat={"status": R.COMPAT_STATUS})                 # thiếu evidence
    with pytest.raises(RuntimeMismatchError):
        R.load_bundle(reg2, "b1", loader=Spy(good_bundle()), runtime=GOOD_RUNTIME)
    reg3, _, _ = make_workspace(tmp_path / "w3", trained=dict(GOOD_RUNTIME), compat={"status": "none"})   # không drift -> không cần compat
    R.load_bundle(reg3, "b1", loader=Spy(good_bundle()), runtime=GOOD_RUNTIME)


@pytest.mark.parametrize("field, value", [("name", "other"), ("horizon", 3), ("matrix", "tree_block"), ("target_transform", "ratio"), ("claim_level", "official"),
                                          ("n_features_in", 36), ("feature_list_sha256", "c" * 64), ("effective_feature_list_sha256", "d" * 64)])
def test_c4_identity_mismatch_including_cross_horizon(tmp_path, field, value):
    reg, _, _ = make_workspace(tmp_path)
    bundle = good_bundle()
    bundle[field] = value
    with pytest.raises(IdentityError):
        R.load_bundle(reg, "b1", loader=Spy(bundle), runtime=GOOD_RUNTIME)


def test_c4_bundle_missing_required_parts(tmp_path):
    reg, _, _ = make_workspace(tmp_path)
    bundle = good_bundle()
    del bundle["routing_policy"]
    with pytest.raises(IdentityError):
        R.load_bundle(reg, "b1", loader=Spy(bundle), runtime=GOOD_RUNTIME)


def test_registry_schema_and_unknown_bundle(tmp_path):
    reg, _, _ = make_workspace(tmp_path)
    with pytest.raises(RegistryError):
        reg.bundle_entry("nope")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(RegistryError):
        R.load_registry(bad, tmp_path)


def test_real_registry_is_wellformed_and_pins_current_files():
    reg = R.load_registry()
    assert {e["id"] for e in reg.entries("regression_primary")} == {"reg-h1-hgb_l1-08", "reg-h3-hgb_l1-ratio1"}
    assert len(reg.nullable_features) == 18 and "current_price" not in reg.nullable_features and "history_span_days" in reg.nullable_features
    R.verify_code_dependencies(reg)                                                  # hash mã thật khớp registry
    for entry in reg.entries("regression_primary"):
        assert R.check_bundle_policy(entry)                                          # drift sklearn/pandas/joblib được KHAI BÁO tường minh
        assert entry["compatibility"]["status"] == R.COMPAT_STATUS and entry["claimLevel"] == "dev_research_exploratory"
