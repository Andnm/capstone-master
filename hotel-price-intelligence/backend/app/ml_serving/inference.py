"""Wrapper inference cho bundle hồi quy đã khóa (inference-only: KHÔNG `.fit`, KHÔNG impute, KHÔNG chọn lại model/routing).

Tái dùng mã dựng ma trận/ratio-block/đổi log-ratio sang VND của `ml/regression/training` (nguồn duy nhất; hash từng file mã ghim trong registry, xác minh TRƯỚC khi import).
Wrapper bổ sung các kiểm tra fail-closed mà helper gốc KHÔNG làm (GPT 02/04):
  * `TreeEncoder.transform` đổi category lạ thành NaN không báo lỗi -> wrapper đối chiếu miền TRƯỚC khi encode;
  * `routed_z` rơi về persistence với `inference_mode` lạ -> wrapper từ chối mode lạ TRƯỚC khi route;
  * NaN: feature thuộc `nullableFeatures` (hợp đồng train) giữ nguyên (không impute); feature còn lại bắt buộc không-null;
  * KIỂU: giá trị non-null không phải số/bool (vd. chuỗi "BAD_NUMBER") KHÔNG được để `pd.to_numeric(errors="coerce")` âm thầm biến thành NaN; ±inf ở bất kỳ numeric feature nào bị chặn.
    Chuỗi số ("123") cũng bị từ chối: dataset đã gõ kiểu, serving không đoán.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from app.dashboard.rules import direction_from_pct, pct_change

from .errors import InputContractError, ReplayError, IntegrityError
from .registry import Registry, activate_code_paths, resolve_contained, sha256_file

TOLERANCE_VND = 0.01
KEY_COLUMNS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date"]


def _training() -> tuple[Any, Any]:
    from training import v3_bundle, v3_contract                   # noqa: PLC0415 - chỉ gọi SAU activate_code_paths (đã xác minh hash mã)

    return v3_bundle, v3_contract


def _numeric_problems(series: pd.Series) -> tuple[int, int]:
    """(số giá trị non-null KHÔNG phải số/bool, số giá trị ±inf). NaN/NA hợp lệ KHÔNG tính ở đây."""
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_numeric_dtype(dtype):
        values = series.astype("Float64").to_numpy(dtype="float64", na_value=np.nan)
        return 0, int(np.isinf(values).sum())
    non_numeric = 0
    infinite = 0
    for value in series.dropna():
        if isinstance(value, (bool, np.bool_, int, np.integer)):
            continue
        if isinstance(value, (float, np.floating)):
            infinite += int(math.isinf(float(value)))
            continue
        non_numeric += 1                                                       # chuỗi, Decimal, bytes... : không đoán, không ép
    return non_numeric, infinite


def validate_frame(frame: pd.DataFrame, bundle: Mapping[str, Any], nullable: frozenset[str]) -> dict[str, int]:
    """Kiểm hợp đồng đầu vào trước inference. Trả đếm NaN từng feature (để ghi manifest). Vi phạm ⇒ InputContractError (không sửa ngầm)."""
    if bundle.get("matrix") not in ("tree", "tree_block"):
        raise InputContractError(f"matrix không hỗ trợ ở serving: {bundle.get('matrix')!r}")
    features = list(bundle["features"])
    required = features + [c for c in ("current_price", "inference_mode") if c not in features]
    missing_cols = [c for c in required if c not in frame.columns]
    if missing_cols:
        raise InputContractError(f"thiếu cột bắt buộc: {missing_cols}")
    if len(frame) == 0:
        raise InputContractError("khung đầu vào rỗng")

    categories_in_bundle: Mapping[str, list[str]] = bundle["encoder_categories"]
    bad_type: dict[str, int] = {}
    infinite: dict[str, int] = {}
    for column in dict.fromkeys(features + ["current_price"]):
        if column in categories_in_bundle:
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
    price = pd.to_numeric(frame["current_price"], errors="coerce").to_numpy(float)
    if not (np.isfinite(price).all() and (price > 0).all()):
        raise InputContractError("current_price phải hữu hạn và > 0")

    policy = bundle["routing_policy"]
    modes = frame["inference_mode"].astype(str)
    unknown_modes = sorted(set(modes) - set(policy))
    if unknown_modes or frame["inference_mode"].isna().any():
        raise InputContractError(f"inference_mode lạ/thiếu (không route ngầm): {unknown_modes}")

    categories: Mapping[str, list[str]] = bundle["encoder_categories"]
    unknown_counts: dict[str, int] = {}
    for column, domain in categories.items():
        if column not in frame.columns:
            continue
        values = frame[column]
        bad = int((values.isna() | ~values.astype(str).isin(domain)).sum())
        if bad:
            unknown_counts[column] = bad
    if unknown_counts:
        raise InputContractError(f"category lạ/thiếu ngoài miền encoder (sẽ thành NaN ngầm): {unknown_counts}")

    nan_counts = {f: int(frame[f].isna().sum()) for f in features}
    violated = {f: n for f, n in nan_counts.items() if n and f not in nullable}
    if violated:
        raise InputContractError(f"NaN ở feature bắt buộc không-null (không impute): {violated}")
    return nan_counts


def predict_prices(bundle: Mapping[str, Any], frame: pd.DataFrame, registry: Registry) -> pd.DataFrame:
    """Dự đoán + route cho `frame` (đã qua `validate_frame`). Trả DataFrame cùng index: z_model, pred_unrouted, method, routing_reason, predicted_price, expected_delta, expected_pct_change, direction."""
    activate_code_paths(registry)
    v3_bundle, v3_contract = _training()
    nan_counts = validate_frame(frame, bundle, registry.nullable_features)                   # noqa: F841 - gọi để fail-closed
    current = pd.to_numeric(frame["current_price"], errors="coerce").to_numpy(float)
    z = v3_bundle.predict_z(bundle, frame)                                                   # đã kiểm finite + giá dương
    pred_unrouted = v3_contract.price_from_z(current, z, label=f"{bundle['name']} unrouted")
    policy = bundle["routing_policy"]
    modes = frame["inference_mode"].astype(str).to_numpy()
    use_model = np.array([bool(policy[m]["use_model"]) for m in modes])
    z_routed = np.where(use_model, z, 0.0)
    predicted = v3_contract.price_from_z(current, z_routed, label=f"{bundle['name']} routed")
    pct = np.array([pct_change(c, p) for c, p in zip(current, predicted)])
    out = pd.DataFrame({
        "z_model": z,
        "pred_unrouted": pred_unrouted,
        "method": np.where(use_model, "model", "persistence"),
        "routing_reason": [str(policy[m].get("reason", "")) for m in modes],
        "predicted_price": predicted,
        "expected_delta": predicted - current,
        "expected_pct_change": pct,
        "direction": [direction_from_pct(p) for p in pct],
    }, index=frame.index)
    if not np.isfinite(out[["pred_unrouted", "predicted_price", "expected_delta", "expected_pct_change"]].to_numpy(float)).all():
        raise InputContractError("đầu ra không hữu hạn")
    return out


def nan_report(frame: pd.DataFrame, bundle: Mapping[str, Any]) -> dict[str, int]:
    return {f: int(frame[f].isna().sum()) for f in bundle["features"]}


def _key_frame(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({c: frame[c].astype(str) for c in KEY_COLUMNS}, index=frame.index)


def replay_against_stored(bundle: Mapping[str, Any], entry: Mapping[str, Any], registry: Registry, samples: pd.DataFrame, *,
                          tolerance: float = TOLERANCE_VND) -> dict[str, Any]:
    """Full replay: dự đoán lại TOÀN BỘ hàng validation+test đã lưu (one-to-one theo khóa 4 cột) và so với cột đã lưu (chưa-route VÀ đã-route).
    Yêu cầu: đủ hàng, finite, dương, max|Δ| <= tolerance (VND). Ghi số đo thực của lần build (rows, max, số hàng khác bit, số hàng vượt) — không sao chép số cũ."""
    spec = entry["predictions"]
    stored_path = resolve_contained(registry.workspace_root, spec["path"])
    if sha256_file(stored_path) != spec["sha256"]:
        raise IntegrityError(f"sha256 dự đoán đã lưu không khớp registry: {spec['path']}")
    stored = pd.read_parquet(stored_path)
    cols = [spec["unroutedColumn"], spec["routedColumn"]]
    if any(c not in stored.columns for c in cols):
        raise ReplayError(f"thiếu cột đã lưu {cols}")
    skey = _key_frame(stored)
    if skey.duplicated().any():
        raise ReplayError("dự đoán đã lưu có khóa trùng")
    left = _key_frame(samples).assign(_pos=np.arange(len(samples)))
    right = skey.assign(**{c: stored[c].to_numpy(float) for c in cols})
    merged = left.merge(right, on=KEY_COLUMNS, how="inner", validate="one_to_one")
    if len(merged) != len(stored):
        raise ReplayError(f"không ghép đủ hàng: ghép {len(merged)} / lưu {len(stored)}")
    frame = samples.iloc[merged["_pos"].to_numpy()].reset_index(drop=True)
    result = predict_prices(bundle, frame, registry)
    report: dict[str, Any] = {"rows": int(len(merged)), "toleranceVnd": tolerance}
    for label, mine, ref_col in (("unrouted", result["pred_unrouted"].to_numpy(float), spec["unroutedColumn"]),
                                 ("routed", result["predicted_price"].to_numpy(float), spec["routedColumn"])):
        ref = merged[ref_col].to_numpy(float)
        if not (np.isfinite(ref).all() and np.isfinite(mine).all() and (mine > 0).all()):
            raise ReplayError(f"{label}: có giá không hữu hạn/không dương")
        diff = np.abs(mine - ref)
        report[label] = {"maxAbsDiffVnd": float(diff.max()), "rowsNotBitEqual": int((diff > 0).sum()), "rowsOverTolerance": int((diff > tolerance).sum())}
        if int((diff > tolerance).sum()):
            raise ReplayError(f"{label}: {int((diff > tolerance).sum())} hàng vượt tolerance {tolerance} VND (max {float(diff.max()):.6g})")
    return report
