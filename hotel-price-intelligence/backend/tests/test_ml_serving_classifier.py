"""C14/C14a — nhánh phân loại (champion xgb-06, h1/h3) trên bundle THẬT: nạp fail-closed, full replay (cùng record_id, max|Δp| <= 1e-6, nhãn trùng 100%), hợp đồng đầu vào, đột biến.
Bỏ qua nếu thiếu artifact cục bộ (bundle/dataset/xgboost đóng gói). Không fit, không DB."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.ml_serving import classify as C
from app.ml_serving import registry as R
from app.ml_serving.errors import IntegrityError, InputContractError, RegistryError, ReplayError, RuntimeMismatchError
from app.ml_serving.snapshot import read_samples

REG = R.load_registry()
IDS = {1: "cls-h1-xgb-06", 3: "cls-h3-xgb-06"}
ENTRIES = {h: REG.bundle_entry(i) for h, i in IDS.items()}
DATASETS = {h: R.WORKSPACE_ROOT / "outputs" / "datasets" / e["datasetVersion"] for h, e in ENTRIES.items()}
LIB = R.WORKSPACE_ROOT / "hotel-price-intelligence" / "data" / "serving" / "libs" / "xgboost-3.4.1"
needed = [R.WORKSPACE_ROOT / e["path"] for e in ENTRIES.values()] + [d / "samples.parquet" for d in DATASETS.values()] + [LIB / "xgboost-3.4.1.dist-info" / "RECORD"]
pytestmark = pytest.mark.skipif(not all(p.exists() for p in needed), reason="thiếu bundle/dataset/xgboost đóng gói cục bộ")


@pytest.fixture(scope="module")
def loaded():
    out = {}
    for h, bundle_id in IDS.items():
        bundle, info = C.load_classification_bundle(REG, bundle_id)
        samples, _ = read_samples(DATASETS[h])
        out[h] = (bundle, info, samples)
    return out


@pytest.fixture(scope="module")
def replays(loaded):
    return {h: C.replay_classifier(bundle, ENTRIES[h], REG, samples) for h, (bundle, _, samples) in loaded.items()}


def test_c14a_champion_replay_same_record_ids_probability_and_label_parity(replays):
    assert replays[1]["rows"] == 41_479 and replays[3]["rows"] == 21_653
    for report in replays.values():
        assert report["maxAbsProbabilityDiff"] <= C.TOLERANCE_PROBABILITY and report["rowsOverTolerance"] == 0 and report["labelMismatchCount"] == 0
        assert report["toleranceProbability"] == 1e-6


def test_load_records_cross_platform_mode_libs_and_declared_drift(loaded):
    for _, info, _ in loaded.values():
        assert info["loadMode"] == "booster_model_section_only" and info["loadWarnings"] == []
        assert info["declaredDrift"] == ["joblib 1.6.0 -> 1.5.2"]                                  # lệch môi trường được khai báo, có evidence replay
        lib = info["vendoredLibs"][0]
        assert lib["distribution"] == "xgboost" and lib["version"] == "3.4.1" and lib["filesVerified"] > 10 and lib["wheelTag"] == "py3-none-win_amd64"
        assert info["runtime"]["xgboost"] == "3.4.1"


def test_booster_setstate_is_restored_after_load(loaded):
    from xgboost import core

    assert "patched" not in core.Booster.__setstate__.__qualname__ and core.Booster.__setstate__.__qualname__ == "Booster.__setstate__"
    for bundle, _, _ in loaded.values():
        assert bundle["estimator"].get_params()["device"] == "cpu" and bundle["estimator"].get_booster().num_features() == len(bundle["columns"]) == 36


def test_labels_are_only_down_stable_up_and_match_stored_classes(loaded):
    for h, (bundle, _, samples) in loaded.items():
        stored = pd.read_parquet(R.WORKSPACE_ROOT / ENTRIES[h]["predictions"]["path"])
        stored = stored[stored["model_id"] == "xgb-06"].head(2000)
        frame = samples.set_index("warehouse_record_id").loc[stored["warehouse_record_id"]].reset_index()
        labels = C.classify_labels(bundle, frame)
        assert set(labels) <= set(C.LABELS)
        assert list(labels) == [C.LABELS[i] for i in stored["pred_class"]]


def test_loader_refuses_wrong_role_both_ways():
    with pytest.raises(RegistryError):
        C.load_classification_bundle(REG, "reg-h1-hgb_l1-08")
    with pytest.raises(RegistryError):
        R.load_bundle(REG, IDS[1])


def _registry_with(**changes) -> R.Registry:
    raw = copy.deepcopy(REG.raw)
    for key, value in changes.items():
        raw[key] = value
    return R.Registry(raw=raw, workspace_root=REG.workspace_root)


def _guard_loader(monkeypatch):
    def boom():
        raise AssertionError("joblib.load không được chạy khi kiểm tra trước đó đã thất bại")
    monkeypatch.setattr(C, "default_loader", boom)


def test_bundle_hash_mismatch_stops_before_deserialization(monkeypatch):
    _guard_loader(monkeypatch)
    raw = copy.deepcopy(REG.raw)
    next(e for e in raw["bundles"] if e["id"] == IDS[1])["sha256"] = "0" * 64
    with pytest.raises(IntegrityError, match="sha256 bundle"):
        C.load_classification_bundle(R.Registry(raw=raw, workspace_root=REG.workspace_root), IDS[1])


def test_classification_code_hash_mismatch_stops_before_import(monkeypatch):
    _guard_loader(monkeypatch)
    deps = copy.deepcopy(REG.raw["classificationCodeDependencies"])
    deps[1]["sha256"] = "f" * 64
    with pytest.raises(IntegrityError, match="mã phụ thuộc đổi"):
        C.load_classification_bundle(_registry_with(classificationCodeDependencies=deps), IDS[1])


def test_vendored_wheel_version_or_missing_record_fails(monkeypatch):
    _guard_loader(monkeypatch)
    with pytest.raises(IntegrityError, match="thiếu RECORD"):
        C.load_classification_bundle(_registry_with(extraLibs=[{**REG.raw["extraLibs"][0], "version": "9.9.9"}]), IDS[1])


def test_runtime_outside_validated_runtime_is_refused(monkeypatch):
    _guard_loader(monkeypatch)
    raw = copy.deepcopy(REG.raw)
    next(e for e in raw["bundles"] if e["id"] == IDS[1])["validatedRuntime"]["xgboost"] = "9.9.9"
    with pytest.raises(RuntimeMismatchError, match="xgboost"):
        C.load_classification_bundle(R.Registry(raw=raw, workspace_root=REG.workspace_root), IDS[1])


def test_undeclared_environment_drift_requires_evidence(monkeypatch):
    _guard_loader(monkeypatch)
    raw = copy.deepcopy(REG.raw)
    next(e for e in raw["bundles"] if e["id"] == IDS[1])["compatibility"] = {"status": "dev_compatibility_empirically_checked", "evidence": ""}
    with pytest.raises(RuntimeMismatchError, match="compatibility"):
        C.load_classification_bundle(R.Registry(raw=raw, workspace_root=REG.workspace_root), IDS[1])


def test_identity_mismatch_is_refused(loaded):
    bundle = loaded[1][0]
    entry = copy.deepcopy(ENTRIES[1])
    entry["featureListSha256"] = "0" * 64
    with pytest.raises(R.IdentityError):
        C.verify_classification_identity(entry, bundle)
    entry = copy.deepcopy(ENTRIES[1])
    entry["datasetVersion"] = "ds_other"
    with pytest.raises(R.IdentityError):
        C.verify_classification_identity(entry, bundle)


def _frame(loaded, n=50):
    return loaded[1][2].head(n).copy()


def test_input_contract_unknown_category_missing_column_string_and_inf(loaded):
    bundle = loaded[1][0]
    frame = _frame(loaded)
    frame["city"] = frame["city"].astype(object)
    frame.loc[frame.index[0], "city"] = "Atlantis"
    with pytest.raises(InputContractError, match="ngoai domain"):
        C.predict_proba(bundle, frame)
    with pytest.raises(InputContractError, match="thiếu cột"):
        C.predict_proba(bundle, _frame(loaded).drop(columns=["current_price"]))
    bad = _frame(loaded).astype({"price_lag_1": object})
    bad.loc[bad.index[0], "price_lag_1"] = "BAD_NUMBER"
    with pytest.raises(InputContractError, match="không phải số"):
        C.predict_proba(bundle, bad)
    inf = _frame(loaded)
    inf.loc[inf.index[0], "current_price"] = np.inf
    with pytest.raises(InputContractError, match="inf"):
        C.predict_proba(bundle, inf)
    with pytest.raises(InputContractError, match="rỗng"):
        C.predict_proba(bundle, _frame(loaded, 0))


def test_replay_detects_a_perturbed_dataset_and_a_tampered_stored_file(loaded):
    bundle, _, samples = loaded[1]
    shifted = samples.copy()
    shifted["current_price"] = shifted["current_price"] * 1.5
    shifted["price_lag_1"] = shifted["price_lag_1"] * 0.5
    with pytest.raises(ReplayError):
        C.replay_classifier(bundle, ENTRIES[1], REG, shifted)
    entry = copy.deepcopy(ENTRIES[1])
    entry["predictions"]["sha256"] = "0" * 64
    with pytest.raises(IntegrityError, match="dự đoán đã lưu"):
        C.replay_classifier(bundle, entry, REG, samples)
    with pytest.raises(ReplayError):                                                           # thiếu hàng: bỏ các hàng validation/test khỏi dataset
        C.replay_classifier(bundle, ENTRIES[1], REG, samples.iloc[: len(samples) // 2])


def test_model_info_child_requires_the_same_record_ids_as_regression(replays):
    child = C.build_classifier_model_info(ENTRIES[1], ENTRIES[1]["sha256"], replays[1], snapshot_records=3679, linked_to_regression=True)
    assert child["claimLevel"] == "none" and child["classMapping"] == {"0": "down", "1": "stable", "2": "up"} and child["coverage"] == {"snapshotRecords": 3679, "recordIdsLinkedToRegression": True}
    assert child["compatibility"]["labelMismatchCount"] == 0 and "limitations" not in child                      # câu chữ hạn chế do service dựng từ nhãn đã khóa
    with pytest.raises(ReplayError):
        C.build_classifier_model_info(ENTRIES[1], ENTRIES[1]["sha256"], replays[1], snapshot_records=3679, linked_to_regression=False)


def test_current_snapshot_capability_is_consistent_with_its_manifest():
    current = R.WORKSPACE_ROOT / "hotel-price-intelligence" / "data" / "serving" / "v1" / "CURRENT.json"
    if not current.exists():
        pytest.skip("chưa có snapshot cục bộ")
    directory = current.parent / json.loads(current.read_text(encoding="utf-8"))["snapshotId"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    catalog = json.loads((directory / "catalog.json").read_text(encoding="utf-8"))
    available = manifest["capabilities"]["classifier"]["available"]
    assert available == bool(manifest.get("classifierBundles"))
    if available:
        assert manifest["capabilities"]["classifier"] == {"available": True, "reason": None}
        for pair in catalog["pairs"]:
            assert set(pair["classifier"]) == set(pair["horizons"]) and set(pair["classifier"].values()) <= set(C.LABELS)
    else:
        assert manifest["capabilities"]["classifier"] == {"available": False, "reason": "dependency_missing"} and all("classifier" not in p for p in catalog["pairs"])


def test_registry_without_a_valid_pinned_record_digest_is_refused(tmp_path):
    for broken in ({"recordSha256": None}, {"recordSha256": "abc"}, {"recordSha256": "A" * 64}, {"wheelTag": ""}):
        raw = copy.deepcopy(REG.raw)
        lib = raw["extraLibs"][0]
        if broken.get("recordSha256", "x") is None:
            del lib["recordSha256"]
        else:
            lib.update(broken)
        path = tmp_path / "registry.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(RegistryError, match="recordSha256|thiếu khóa"):
            R.load_registry(path, REG.workspace_root)
    raw = copy.deepcopy(REG.raw)
    del raw["extraLibs"][0]["wheelTag"]
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RegistryError, match="thiếu khóa"):
        R.load_registry(path, REG.workspace_root)


def test_wrong_pinned_record_digest_or_wheel_tag_stops_before_deserialization(monkeypatch):
    _guard_loader(monkeypatch)
    lib = REG.raw["extraLibs"][0]
    assert lib["recordSha256"] == "d6591567b8005732bfa2d132bbc0dff34b43bf7d0cb25223b631f98f8a915ab5" and lib["wheelTag"] == "py3-none-win_amd64"
    with pytest.raises(IntegrityError, match="khác bản đã ghim"):
        C.load_classification_bundle(_registry_with(extraLibs=[{**lib, "recordSha256": "0" * 64}]), IDS[1])
    with pytest.raises(IntegrityError, match="thẻ wheel"):
        C.load_classification_bundle(_registry_with(extraLibs=[{**lib, "wheelTag": "py3-none-manylinux_2_28_x86_64"}]), IDS[1])


def test_real_library_has_no_file_outside_its_pinned_inventory_and_bytecode_is_ignored_by_policy():
    from app.ml_serving.xgb_compat import verify_vendored_wheel

    lib = REG.raw["extraLibs"][0]
    info = verify_vendored_wheel(R.resolve_contained(REG.workspace_root, lib["path"]), lib["distribution"], lib["version"], expected_record_sha256=lib["recordSha256"], expected_wheel_tag=lib["wheelTag"])
    assert info["filesVerified"] == 61 and info["recordSha256"] == lib["recordSha256"]
    import sys
    C.activate_classification(REG)
    assert sys.dont_write_bytecode is True and sys.pycache_prefix is not None             # chính sách bytecode đã được đặt khi kích hoạt (builder/loader)


def test_registry_tool_carries_pinned_libs_verbatim_and_never_creates_them():
    from app.ml_serving.registry_tool import build_candidate

    result = build_candidate(workspace_root=REG.workspace_root, existing=REG.raw, specs=[], code_dependencies=REG.raw["codeDependencies"], nullable_features=list(REG.raw["nullableFeatures"]),
                             datasets=REG.raw["datasets"], runtime=R.current_runtime())
    assert result.registry["extraLibs"] == REG.raw["extraLibs"] and result.registry["classificationCodeDependencies"] == REG.raw["classificationCodeDependencies"]
    fresh = build_candidate(workspace_root=REG.workspace_root, existing=None, specs=[], code_dependencies=REG.raw["codeDependencies"], nullable_features=list(REG.raw["nullableFeatures"]),
                            datasets=REG.raw["datasets"], runtime=R.current_runtime())
    assert "extraLibs" not in fresh.registry and "classificationCodeDependencies" not in fresh.registry         # không có registry đã duyệt ⇒ không tự tạo pin/mục phân loại
