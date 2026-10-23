"""Nhánh PHÂN LOẠI up/down/stable của lớp serving — inference-only, CHỈ phát nhãn lớp (không xác suất), claim `none` (xác suất chưa hiệu chỉnh; không hơn rõ hướng suy ra từ hồi quy).

Bundle phân loại được huấn luyện trên Linux/GPU (Colab) rồi nạp trên Windows/CPU. Thứ tự nạp (mỗi bước fail ⇒ dừng, không bước nào bị bỏ qua):
  1. đường dẫn trong vùng cho phép; sha256 bundle == registry — TRƯỚC `joblib.load` (pickle = thực thi mã);
  2. sha256 mã `cls_training` (classificationCodeDependencies) và toàn bộ file wheel xgboost đóng gói sẵn (RECORD) — TRƯỚC khi thêm vào sys.path / import;
  3. runtime hiện tại == `validatedRuntime` (gồm `xgboost`); khác `trainedEnvironment` chỉ được khi có `compatibility` hợp lệ;
  4. nạp trong `xgb_compat.model_section_only_unpickle` (chỉ khối `Model` của Booster; lý do ở docstring `xgb_compat`);
  5. danh tính bundle khớp registry; ép CPU cho suy luận.
Độ đúng của đường nạp này KHÔNG dựa vào suy luận mà vào FULL REPLAY mỗi lần build: dự đoán lại toàn bộ hàng validation+test của mô hình đã lưu và so với xác suất lưu từ lúc huấn luyện.
"""
from __future__ import annotations

import hashlib
import sys
import warnings
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .errors import IdentityError, InputContractError, IntegrityError, RegistryError, ReplayError
from .inference import _numeric_problems
from .registry import COMPAT_STATUS, Registry, check_bundle_policy, check_runtime, default_loader, resolve_contained, sha256_file, verify_code_dependencies
from .xgb_compat import activate_vendored_lib, model_section_only_unpickle

LABELS = ("down", "stable", "up")
CLASS_MAPPING = {str(i): label for i, label in enumerate(LABELS)}
TOLERANCE_PROBABILITY = 1e-6
SUM_TOLERANCE = 1e-5
CLASSIFICATION_CODE_ROOT = "hotel-price-intelligence/ml/classification/v1"
BUNDLE_SCHEMA = "cls-bundle-1"
KEY_COLUMNS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date"]
PROBABILITY_COLUMNS = ("p_down", "p_stable", "p_up")


def activate_classification(registry: Registry) -> list[dict[str, Any]]:
    """Xác minh hash mã + thư viện đóng gói TRƯỚC rồi mới thêm vào sys.path (một lần). Trả tóm tắt các wheel đã xác minh (ghi manifest)."""
    verify_code_dependencies(registry, registry.classification_code_dependencies)
    libs = []
    for lib in registry.extra_libs:
        libs.append(activate_vendored_lib(resolve_contained(registry.workspace_root, lib["path"]), lib["distribution"], lib["version"],
                                          expected_record_sha256=lib["recordSha256"], expected_wheel_tag=lib["wheelTag"]))
    code_root = str(resolve_contained(registry.workspace_root, CLASSIFICATION_CODE_ROOT))
    if code_root not in sys.path:
        sys.path.insert(0, code_root)
    return libs


def _features() -> Any:
    from cls_training import features                           # noqa: PLC0415 - chỉ gọi SAU activate_classification (đã xác minh hash mã)

    return features


def verify_classification_identity(entry: Mapping[str, Any], bundle: Mapping[str, Any]) -> None:
    """Nội dung bundle == registry. Thiếu khóa/lệch ⇒ IdentityError."""
    expected = {"schema": BUNDLE_SCHEMA, "model_id": entry["modelName"], "horizon": entry["horizon"], "dataset_version": entry["datasetVersion"], "family": entry["family"],
                "feature_set": "base36", "classes": list(LABELS), "probability_status": "uncalibrated", "fitted_on": "train_only", "config_sha256": entry["configSha256"],
                "claim_level_hint": "dev_exploratory", "label_contract_threshold": 0.02}
    for key, value in expected.items():
        if bundle.get(key) != value:
            raise IdentityError(f"{entry['id']}: {key}={bundle.get(key)!r} != kỳ vọng {value!r}")
    columns = list(bundle.get("columns", []))
    if hashlib.sha256("\n".join(columns).encode("utf-8")).hexdigest() != entry["featureListSha256"] or len(columns) != entry["nFeaturesIn"]:
        raise IdentityError(f"{entry['id']}: danh sách feature khác registry")
    estimator = bundle.get("estimator")
    if type(estimator).__name__ != "XGBClassifier":
        raise IdentityError(f"{entry['id']}: estimator không phải XGBClassifier: {type(estimator).__name__}")
    if list(getattr(estimator, "classes_", [])) != [0, 1, 2] or int(estimator.get_booster().num_features()) != len(columns):
        raise IdentityError(f"{entry['id']}: classes_/num_features của booster không khớp")


def load_classification_bundle(registry: Registry, bundle_id: str, *, runtime: Mapping[str, str | None] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Nạp 1 bundle phân loại theo thứ tự fail-closed (xem docstring module). Trả (bundle, thông tin nạp)."""
    entry = registry.bundle_entry(bundle_id)
    if entry["role"] != "classification_reference":
        raise RegistryError(f"không phải bundle phân loại: {bundle_id!r}")
    path = resolve_contained(registry.workspace_root, entry["path"])
    actual_sha = sha256_file(path)
    if actual_sha != entry["sha256"]:
        raise IntegrityError(f"sha256 bundle {bundle_id} không khớp registry (file đổi?)")
    libs = activate_classification(registry)
    actual_runtime = check_runtime(entry["validatedRuntime"], runtime)
    drift = check_bundle_policy(entry)
    loader = default_loader()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with model_section_only_unpickle():
            bundle = loader(path)
    verify_classification_identity(entry, bundle)
    estimator = bundle["estimator"]
    estimator.set_params(device="cpu")                         # model-only booster mặc định CPU; đặt tường minh để không phụ thuộc cấu hình GPU lúc huấn luyện
    estimator.get_booster().set_param({"device": "cpu"})
    info = {"bundleId": bundle_id, "sha256": actual_sha, "runtime": actual_runtime, "declaredDrift": drift, "vendoredLibs": libs, "loadMode": "booster_model_section_only",
            "loadWarnings": sorted({f"{w.category.__name__}" for w in caught})}
    return bundle, info


def validate_classifier_frame(frame: pd.DataFrame, bundle: Mapping[str, Any]) -> None:
    """Hợp đồng đầu vào: đủ cột; numeric là số thật (chuỗi/±inf bị chặn, không ép NaN); category thuộc miền (kiểm lại bởi TreeMatrix). NaN giữ nguyên như lúc huấn luyện (XGBoost xử lý missing)."""
    columns = list(bundle["columns"])
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise InputContractError(f"thiếu cột bắt buộc: {missing}")
    if len(frame) == 0:
        raise InputContractError("khung đầu vào rỗng")
    from cls_training.upstream.encoder import categorical_in    # noqa: PLC0415 - sau activate_classification

    categorical = set(categorical_in(columns))
    bad_type: dict[str, int] = {}
    infinite: dict[str, int] = {}
    for column in columns:
        if column in categorical:
            continue
        non_numeric, inf_n = _numeric_problems(frame[column])
        if non_numeric:
            bad_type[column] = non_numeric
        if inf_n:
            infinite[column] = inf_n
    if bad_type:
        raise InputContractError(f"giá trị không phải số/bool ở numeric feature (không ép về NaN): {bad_type}")
    if infinite:
        raise InputContractError(f"±inf ở numeric feature: {infinite}")


def predict_proba(bundle: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """(n, 3) xác suất [down, stable, up] — chỉ dùng nội bộ (replay/gán nhãn), KHÔNG đưa ra API. Kiểm hữu hạn, trong [0,1], tổng hàng = 1."""
    features = _features()
    validate_classifier_frame(frame, bundle)
    try:
        matrix = bundle["matrix"].transform(features.prepare_frame(frame, bundle["feature_set"]))
    except features.FeatureError as exc:
        raise InputContractError(str(exc)) from exc
    proba = np.asarray(bundle["estimator"].predict_proba(matrix), dtype="float64")
    if proba.shape != (len(frame), 3) or not np.isfinite(proba).all() or (proba < 0).any() or (proba > 1).any() or np.abs(proba.sum(axis=1) - 1.0).max() > SUM_TOLERANCE:
        raise InputContractError("xác suất không hợp lệ (shape/hữu hạn/[0,1]/tổng=1)")
    return proba


def labels_from_proba(proba: np.ndarray) -> list[str]:
    return [LABELS[i] for i in proba.argmax(axis=1)]


def classify_labels(bundle: Mapping[str, Any], frame: pd.DataFrame) -> pd.Series:
    """Nhãn lớp theo từng hàng (cùng index với `frame`). Không xác suất."""
    return pd.Series(labels_from_proba(predict_proba(bundle, frame)), index=frame.index, name="label")


def _key_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({c: frame[c].astype(str) for c in KEY_COLUMNS}, index=frame.index)


def replay_classifier(bundle: Mapping[str, Any], entry: Mapping[str, Any], registry: Registry, samples: pd.DataFrame, *, tolerance: float = TOLERANCE_PROBABILITY) -> dict[str, Any]:
    """Full replay: dự đoán lại TOÀN BỘ hàng validation+test đã lưu của mô hình champion (khớp one-to-one theo `warehouse_record_id` + khóa 4 cột) và so với xác suất/lớp đã lưu.
    Yêu cầu: đủ hàng, max|Δp| <= tolerance, 0 hàng lệch lớp. Ghi số đo thực của lần build (không sao chép số cũ)."""
    spec = entry["predictions"]
    stored_path = resolve_contained(registry.workspace_root, spec["path"])
    if sha256_file(stored_path) != spec["sha256"]:
        raise IntegrityError(f"sha256 dự đoán đã lưu không khớp registry: {spec['path']}")
    stored = pd.read_parquet(stored_path)
    needed = {"warehouse_record_id", "model_id", "horizon", "pred_class", *PROBABILITY_COLUMNS, *KEY_COLUMNS}
    if not needed <= set(stored.columns):
        raise ReplayError(f"thiếu cột đã lưu: {sorted(needed - set(stored.columns))}")
    stored = stored[(stored["model_id"] == entry["modelName"]) & (stored["horizon"] == entry["horizon"])].reset_index(drop=True)
    if len(stored) == 0 or stored["warehouse_record_id"].duplicated().any():
        raise ReplayError("dự đoán đã lưu của mô hình rỗng hoặc trùng warehouse_record_id")
    left = samples.assign(_pos=np.arange(len(samples)))[["warehouse_record_id", "_pos", *KEY_COLUMNS]]
    right = stored[["warehouse_record_id", "pred_class", *PROBABILITY_COLUMNS, *KEY_COLUMNS]]
    merged = left.merge(right, on="warehouse_record_id", how="inner", validate="one_to_one", suffixes=("", "_stored"))
    if len(merged) != len(stored):
        raise ReplayError(f"không ghép đủ hàng: ghép {len(merged)} / lưu {len(stored)}")
    for column in KEY_COLUMNS:
        if (merged[column].astype(str) != merged[f"{column}_stored"].astype(str)).any():
            raise ReplayError(f"khóa {column} của hàng đã lưu khác dataset")
    frame = samples.iloc[merged["_pos"].to_numpy()].reset_index(drop=True)
    proba = predict_proba(bundle, frame)
    reference = merged[list(PROBABILITY_COLUMNS)].to_numpy(dtype="float64")
    diff = np.abs(proba - reference)
    mismatches = int((proba.argmax(axis=1) != merged["pred_class"].to_numpy(dtype="int64")).sum())
    report = {"rows": int(len(merged)), "maxAbsProbabilityDiff": float(diff.max()), "rowsOverTolerance": int((diff.max(axis=1) > tolerance).sum()), "labelMismatchCount": mismatches,
              "toleranceProbability": tolerance}
    if report["rowsOverTolerance"] or mismatches:
        raise ReplayError(f"replay phân loại lệch: {report['rowsOverTolerance']} hàng vượt {tolerance}, {mismatches} hàng lệch lớp (max {report['maxAbsProbabilityDiff']:.3g})")
    return report


def build_classifier_model_info(entry: Mapping[str, Any], bundle_sha: str, replay: Mapping[str, Any], *, snapshot_records: int, linked_to_regression: bool) -> dict[str, Any]:
    """Một phần tử `classifier.horizons[]` của modelinfo. Câu chữ hạn chế do SERVICE dựng từ nhãn đã khóa (`labels.py`), không do builder viết."""
    if not linked_to_regression:
        raise ReplayError("record phân loại không trùng đúng tập record hồi quy cùng mốc")
    return {"horizon": entry["horizon"], "task": "classification", "modelName": entry["modelName"], "family": entry["family"], "datasetVersion": entry["datasetVersion"], "claimLevel": "none",
            "bundleSha256": bundle_sha, "trainedEnvironment": entry["trainedEnvironment"], "validatedRuntime": entry["validatedRuntime"], "classMapping": dict(CLASS_MAPPING),
            "compatibility": {"status": COMPAT_STATUS, "rows": replay["rows"], "maxAbsProbabilityDiff": replay["maxAbsProbabilityDiff"], "labelMismatchCount": replay["labelMismatchCount"],
                              "toleranceProbability": replay["toleranceProbability"]},
            "coverage": {"snapshotRecords": int(snapshot_records), "recordIdsLinkedToRegression": True}}
