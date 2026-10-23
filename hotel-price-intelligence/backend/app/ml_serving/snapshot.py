"""Dựng SERVING SNAPSHOT hồi cứu (offline; inference-only). Đầu ra là JSON thuần để API chỉ-đọc phục vụ — API KHÔNG nạp pickle/sklearn/DB.

Đầu vào: hai dataset đã xuất (nf1 cho h1, nf3 cho h3; bất biến, sha256 `samples.parquet` kiểm với `output_checksums.json` TRƯỚC khi đọc), registry đã ghim hash, metadata hotel offline (có checksum).
Quy trình: nạp bundle fail-closed -> FULL REPLAY mỗi lần build -> lát as-of (ngày quan sát cuối) -> dự đoán h1/h3 theo cặp đủ điều kiện (`lead_time >= h`) -> history nhân quả (<= as-of)
-> catalog/history/hotels -> manifest (contentSha256 phần xác định) -> publish NGUYÊN TỬ, không ghi đè `snapshotId`.
Chỉ allowlist field được đưa ra (không `y_*`, `label_*`, `hotel_seen_*`, `split`).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from app.dashboard import rules
from app.dashboard.rules import SUPPORTED_HORIZONS, pct_change

from . import classify
from .errors import IntegrityError, SnapshotError
from .inference import nan_report, predict_prices, replay_against_stored
from .registry import COMPAT_STATUS, Registry, load_bundle, resolve_contained, sha256_file

BUILDER_VERSION = "serving-builder-1.1.0"
SNAPSHOT_SCHEMA = "serving-snapshot-2"
BACKEND_DIR = Path(__file__).resolve().parents[2]
CLAIM_LEVEL = "dev_research_exploratory"
KEY_META_COLUMNS = ["hotel_id", "checkin_date", "canonical_series_id", "vn_observation_date", "prediction_time", "warehouse_record_id", "inference_mode", "city", "lead_time", "current_price",
                    "max_occupancy", "room_area_m2", "breakfast_included", "free_cancellation", "history_observation_count", "history_span_days"]
SHARED_COLUMNS = list(KEY_META_COLUMNS)           # tối thiểu; builder mở rộng bằng UNION feature thật của hai bundle (shared_columns)
CODE_INVENTORY_FILES = ("app/ml_serving/__init__.py", "app/ml_serving/errors.py", "app/ml_serving/registry.py", "app/ml_serving/registry_tool.py", "app/ml_serving/inference.py",
                        "app/ml_serving/snapshot.py", "app/ml_serving/hotel_meta.py", "app/ml_serving/xgb_compat.py", "app/ml_serving/classify.py", "app/dashboard/rules.py",
                        "scripts/build_serving_snapshot.py", "scripts/build_registry.py")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _clean(value: Any) -> Any:
    """Đưa giá trị pandas/numpy về JSON thuần; NaN/NA -> None (không bao giờ 0 giả)."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return value


def read_samples(dataset_dir: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Đọc samples.parquet SAU khi sha256 khớp `output_checksums.json` của chính dataset."""
    checks = json.loads((dataset_dir / "output_checksums.json").read_text(encoding="utf-8"))["samples.parquet"]
    actual = sha256_file(dataset_dir / "samples.parquet")
    if actual != checks["file_sha256"]:
        raise IntegrityError(f"samples.parquet không khớp output_checksums.json: {dataset_dir.name}")
    frame = pd.read_parquet(dataset_dir / "samples.parquet")
    if len(frame) != int(checks["rows"]):
        raise IntegrityError(f"số hàng samples.parquet != checksums: {dataset_dir.name}")
    return frame, {"fileSha256": actual, "rows": int(checks["rows"]), "contentSha256": checks.get("content_sha256")}


def load_hotels_meta(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    body = canonical_json(payload["hotels"])
    if sha256_bytes(body) != payload["hotelsSha256"]:
        raise IntegrityError("hotelsSha256 của metadata không khớp nội dung")
    by_id: dict[str, dict[str, Any]] = {}
    for item in payload["hotels"]:
        if item["hotelId"] in by_id:
            raise IntegrityError(f"hotelId trùng trong metadata: {item['hotelId']}")
        tenths = item["reviewScoreTenths"]
        if tenths is not None and (not isinstance(tenths, int) or isinstance(tenths, bool) or not 0 <= tenths <= 100):
            raise IntegrityError(f"reviewScoreTenths sai: {item['hotelId']}={tenths!r}")
        by_id[item["hotelId"]] = item
    info = {"fileSha256": sha256_file(Path(path)), "hotelsSha256": payload["hotelsSha256"], "exportedAtUtc": payload["exportedAtUtc"], "rowCount": len(payload["hotels"]), "select": payload.get("select")}
    return by_id, info


def asof_slice(samples: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    dates = samples["vn_observation_date"].astype(str)
    as_of = dates.max()
    return samples[dates == as_of].reset_index(drop=True), as_of


def shared_columns(bundles: Mapping[int, Mapping[str, Any]]) -> list[str]:
    """Cột cần trùng giữa nf1 và nf3 ở lát as-of: khóa/metadata + UNION feature thật của các bundle (không rút gọn cho vừa witness)."""
    columns: list[str] = list(KEY_META_COLUMNS)
    for bundle in bundles.values():
        columns.extend(c for c in bundle["features"] if c not in columns)
    return columns


def _same(left: pd.Series, right: pd.Series) -> pd.Series:
    """So từng phần tử NaN-aware, độc lập dtype (kể cả boolean/Int64 có pd.NA hoặc cột object): thiếu==thiếu là trùng; thiếu vs giá trị là lệch."""
    a = left.astype(object).where(left.notna(), None).to_numpy(dtype=object)
    b = right.astype(object).where(right.notna(), None).to_numpy(dtype=object)
    return pd.Series(np.fromiter((x == y for x, y in zip(a, b)), dtype=bool, count=len(a)), index=left.index)


def assert_same_slice(a: pd.DataFrame, b: pd.DataFrame, columns: list[str] | None = None) -> None:
    """nf1 và nf3 phải trùng record và các cột dùng chung ở lát as-of, NaN-aware (khác có chủ đích: version dataset, cờ hotel-seen, nhãn, split)."""
    columns = columns or SHARED_COLUMNS
    if len(a) != len(b):
        raise SnapshotError(f"lát as-of lệch số hàng: {len(a)} != {len(b)}")
    a = a.sort_values("warehouse_record_id").reset_index(drop=True)
    b = b.sort_values("warehouse_record_id").reset_index(drop=True)
    for column in columns:
        if column not in a.columns or column not in b.columns:
            raise SnapshotError(f"thiếu cột so sánh ở lát as-of: {column}")
        same = _same(a[column], b[column])
        if not bool(same.all()):
            raise SnapshotError(f"lát as-of nf1 != nf3 ở cột {column} ({int((~same).sum())} hàng)")


def code_inventory(backend_dir: Path = BACKEND_DIR) -> dict[str, str]:
    """sha256 từng file mã của lớp serving (đường dẫn tương đối backend). Đi vào danh tính nội dung: đổi mã ⇒ snapshotId đổi."""
    return {rel: sha256_file(Path(backend_dir) / rel) for rel in CODE_INVENTORY_FILES}


def build_model_info(registry: Registry, bundle_ids: Mapping[int, str], bundles: Mapping[int, Mapping[str, Any]], replay: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """Metadata chứng cứ cho /model-info: chỉ SAO CHÉP từ report đã ghim hash (không tính lại) + routing của bundle + số đo replay của lần build."""
    horizons = []
    for h in SUPPORTED_HORIZONS:
        entry = registry.bundle_entry(bundle_ids[h])
        report_path = resolve_contained(registry.workspace_root, entry["report"]["path"])
        if sha256_file(report_path) != entry["report"]["sha256"]:
            raise IntegrityError(f"report không khớp registry: {entry['report']['path']}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        name = entry["modelName"]

        def triple(node: Mapping[str, Any], ci_key: str = "lift_vnd_ci95_hotel") -> list[float]:
            lo, hi = node[ci_key]
            return [round(100.0 * float(node["lift_vnd"]), 4), round(100.0 * float(lo), 4), round(100.0 * float(hi), 4)]

        validation = report["finalists"][name]["validation"]
        test_model = report["test"]["table"][name]
        test_routed = report["test"]["table"][f"routed:{name}"]
        ordinary = report["test"]["strata"][name]["ordinary"]
        threshold = float(report["claims"]["ordinary_warning_threshold"])
        policy = bundles[h]["routing_policy"]
        rep = replay[h]
        horizons.append({
            "horizon": h, "modelName": name, "family": entry["family"], "datasetVersion": entry["datasetVersion"], "claimLevel": entry["claimLevel"], "bundleSha256": entry["sha256"],
            "trainedEnvironment": entry["trainedEnvironment"], "validatedRuntime": entry["validatedRuntime"],
            "compatibility": {"status": COMPAT_STATUS, "rows": rep["rows"], "maxAbsDiffVnd": max(rep["unrouted"]["maxAbsDiffVnd"], rep["routed"]["maxAbsDiffVnd"]),
                              "rowsOverTolerance": rep["unrouted"]["rowsOverTolerance"] + rep["routed"]["rowsOverTolerance"], "toleranceVnd": rep["toleranceVnd"]},
            "routing": [{"mode": mode, "useModel": bool(p["use_model"]), "reason": str(p["reason"]), "validationLiftVnd": float(p["lift_vnd"]), "ci95": [float(x) for x in p["ci95"]],
                         "validationRows": int(p["support"]["rows"])} for mode, p in sorted(policy.items())],
            "evidence": {"validationLiftPct": triple(validation), "testLiftPct": triple(test_model), "testRoutedLiftPct": triple(test_routed), "testRows": int(test_model["n"]),
                         "ordinary": {"testLiftPct": triple(ordinary), "shareOfRowsPct": round(100.0 * float(ordinary["share_of_rows"]), 2), "belowThreshold": float(ordinary["lift_vnd"]) < threshold},
                         "source": entry["report"]["path"], "sourceSha256": entry["report"]["sha256"]},
        })
    return {"schema": "serving-modelinfo-1", "horizons": horizons, "classifier": {"available": False, "reason": "dependency_missing", "horizons": []}}


def _iso_utc(ts: Any) -> str:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")                    # prediction_time của dataset là UTC naive (= price_observations.observed_at)


def _history(samples: pd.DataFrame, series_ids: set[str], as_of: str) -> dict[str, list[list[Any]]]:
    sub = samples.loc[samples["canonical_series_id"].isin(series_ids), ["canonical_series_id", "vn_observation_date", "current_price"]].copy()
    sub["vn_observation_date"] = sub["vn_observation_date"].astype(str)
    sub = sub[sub["vn_observation_date"] <= as_of].sort_values(["canonical_series_id", "vn_observation_date"])
    out: dict[str, list[list[Any]]] = {}
    for series, group in sub.groupby("canonical_series_id", sort=True):
        dates = group["vn_observation_date"].tolist()
        if len(set(dates)) != len(dates):
            raise SnapshotError(f"history có ngày trùng trong series {series}")
        out[series] = [[d, float(p)] for d, p in zip(dates, group["current_price"].to_numpy(float))]
    return out


def build_snapshot_payload(registry: Registry, hotels_meta: Mapping[str, Mapping[str, Any]], *, dataset_dirs: Mapping[int, Path],
                           bundle_ids: Mapping[int, str], classifier_bundle_ids: Mapping[int, str] | None = None, generated_at: datetime | None = None) -> dict[str, Any]:
    """Tính toàn bộ nội dung snapshot (chưa ghi). Mọi lỗi hợp đồng ⇒ exception, không trả snapshot nửa vời.
    `classifier_bundle_ids` (tùy chọn, đủ cả hai mốc): bật nhánh phân loại — CHỈ nhãn lớp trên đúng các record hồi quy cùng mốc, kèm full replay riêng; thiếu ⇒ capability `dependency_missing`."""
    generated_at = generated_at or datetime.now(timezone.utc)
    bundles: dict[int, dict[str, Any]] = {}
    load_infos: dict[int, dict[str, Any]] = {}
    samples: dict[int, pd.DataFrame] = {}
    sample_infos: dict[int, dict[str, Any]] = {}
    for h in SUPPORTED_HORIZONS:
        bundles[h], load_infos[h] = load_bundle(registry, bundle_ids[h])
        samples[h], sample_infos[h] = read_samples(Path(dataset_dirs[h]))
        entry = registry.bundle_entry(bundle_ids[h])
        pinned = next((d for d in registry.raw.get("datasets", []) if d["id"] == entry["datasetVersion"]), None)
        if pinned is not None and pinned["samplesFileSha256"] != sample_infos[h]["fileSha256"]:
            raise IntegrityError(f"samples.parquet của {entry['datasetVersion']} khác registry")

    replay = {h: replay_against_stored(bundles[h], registry.bundle_entry(bundle_ids[h]), registry, samples[h]) for h in SUPPORTED_HORIZONS}

    slices, as_ofs = {}, {}
    for h in SUPPORTED_HORIZONS:
        slices[h], as_ofs[h] = asof_slice(samples[h])
    if len(set(as_ofs.values())) != 1:
        raise SnapshotError(f"as-of khác nhau giữa hai dataset: {as_ofs}")
    as_of = as_ofs[SUPPORTED_HORIZONS[0]]
    assert_same_slice(slices[1], slices[3], shared_columns(bundles))

    predictions: dict[int, pd.DataFrame] = {}
    nan_counts: dict[str, dict[str, int]] = {}
    for h in SUPPORTED_HORIZONS:
        eligible = slices[h][slices[h]["lead_time"] >= h].reset_index(drop=True)
        result = predict_prices(bundles[h], eligible, registry)
        result.insert(0, "warehouse_record_id", eligible["warehouse_record_id"].to_numpy())
        predictions[h] = result.set_index("warehouse_record_id")
        nan_counts[f"h{h}"] = nan_report(eligible, bundles[h])

    cls_labels: dict[int, pd.Series] = {}
    cls_children: list[dict[str, Any]] = []
    cls_manifest: list[dict[str, Any]] = []
    if classifier_bundle_ids is not None:
        if set(classifier_bundle_ids) != set(SUPPORTED_HORIZONS):
            raise SnapshotError(f"classifier_bundle_ids phải đủ các mốc {list(SUPPORTED_HORIZONS)}")
        for h in SUPPORTED_HORIZONS:
            cid = classifier_bundle_ids[h]
            centry = registry.bundle_entry(cid)
            if centry["horizon"] != h or centry["datasetVersion"] != registry.bundle_entry(bundle_ids[h])["datasetVersion"]:
                raise SnapshotError(f"{cid}: horizon/dataset khác bundle hồi quy cùng mốc")
            cbundle, cinfo = classify.load_classification_bundle(registry, cid)
            creplay = classify.replay_classifier(cbundle, centry, registry, samples[h])
            eligible = slices[h][slices[h]["lead_time"] >= h].reset_index(drop=True)
            labelled = classify.classify_labels(cbundle, eligible)
            labelled.index = eligible["warehouse_record_id"].to_numpy()
            linked = set(labelled.index) == set(predictions[h].index)
            cls_labels[h] = labelled
            cls_children.append(classify.build_classifier_model_info(centry, cinfo["sha256"], creplay, snapshot_records=len(labelled), linked_to_regression=linked))
            cls_manifest.append({"id": cid, "horizon": h, "sha256": cinfo["sha256"], "modelName": centry["modelName"], "runtime": cinfo["runtime"], "declaredDrift": cinfo["declaredDrift"],
                                 "loadMode": cinfo["loadMode"], "vendoredLibs": cinfo["vendoredLibs"], "loadWarnings": cinfo["loadWarnings"], "replay": creplay})

    base = slices[1]
    pair_rows = base[base["lead_time"] >= min(SUPPORTED_HORIZONS)].sort_values(["hotel_id", "checkin_date"]).reset_index(drop=True)
    history = _history(samples[1], set(pair_rows["canonical_series_id"]), as_of)
    week_ago = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    pairs: list[dict[str, Any]] = []
    for row in pair_rows.itertuples(index=False):
        record_id = int(row.warehouse_record_id)
        current = float(row.current_price)
        horizons: dict[str, Any] = {}
        for h in SUPPORTED_HORIZONS:
            if int(row.lead_time) >= h:
                p = predictions[h].loc[record_id]
                pct = float(p["expected_pct_change"])
                if abs(pct - pct_change(current, float(p["predicted_price"]))) > 1e-9:
                    raise SnapshotError("pct không khớp công thức")
                horizons[str(h)] = {"method": str(p["method"]), "routingReason": str(p["routing_reason"]), "predictedPrice": float(p["predicted_price"]),
                                    "expectedDelta": float(p["expected_delta"]), "expectedPctChange": pct, "direction": str(p["direction"]),
                                    "targetObservationDate": (datetime.strptime(as_of, "%Y-%m-%d") + timedelta(days=h)).strftime("%Y-%m-%d")}
        classifier_labels = {str(h): str(cls_labels[h].loc[record_id]) for h in SUPPORTED_HORIZONS if h in cls_labels and str(h) in horizons}
        series = history[row.canonical_series_id]
        past = [price for date, price in series if date == week_ago]
        # change7DaysPct = thay đổi (điểm %) của giá hiện tại so với giá ĐÚNG as-of−7 của cùng series: 100*(current−past)/past; thiếu ngày đó ⇒ None (không ffill)
        change7 = None if not past or past[0] <= 0 else pct_change(past[0], current)
        pairs.append({
            "hotelId": row.hotel_id, "city": row.city, "checkIn": str(row.checkin_date), "canonicalSeriesId": row.canonical_series_id, "recordId": record_id,
            "leadDays": int(row.lead_time), "currentPrice": current, "currentObservedAt": _iso_utc(row.prediction_time), "inferenceMode": row.inference_mode,
            "historyObservationCount": int(row.history_observation_count), "historySpanDays": _clean(row.history_span_days),
            "roomSummary": {"maxOccupancy": _clean(row.max_occupancy), "areaM2": _clean(row.room_area_m2), "breakfastIncluded": _clean(row.breakfast_included), "freeCancellation": _clean(row.free_cancellation)},
            "change7DaysPct": change7, "horizons": horizons,
            **({"classifier": classifier_labels} if cls_labels else {}),
        })

    hotel_ids = sorted({p["hotelId"] for p in pairs})
    city_of = {p["hotelId"]: p["city"] for p in pairs}
    hotels, missing_meta, city_mismatch = [], 0, 0
    for hid in hotel_ids:
        meta = hotels_meta.get(hid)
        if meta is None:
            missing_meta += 1
            hotels.append({"hotelId": hid, "name": hid, "city": city_of[hid], "reviewScoreTenths": None, "metadata": False})
            continue
        city_mismatch += int(meta["city"] != city_of[hid])
        hotels.append({"hotelId": hid, "name": meta["name"], "city": city_of[hid], "reviewScoreTenths": meta["reviewScoreTenths"], "metadata": True})

    catalog = {"schema": "serving-catalog-1", "dataAsOf": as_of, "pairs": pairs}
    inventory = code_inventory()
    builder = {"version": BUILDER_VERSION, "codeSha256": sha256_bytes(canonical_json(inventory)), "codeInventory": inventory,
               "config": {"historyPolicy": "vn_observation_date <= dataAsOf (causal, no fill)", "change7Policy": "price at exact asOf-7 of same series else null",
                          "replayToleranceVnd": 0.01, "nullableFeatures": sorted(registry.nullable_features), "sharedColumnsCompared": shared_columns(bundles)}}
    semantic = rules.semantic_config()
    content = {"catalog": catalog, "history": history, "hotels": hotels,
               "bundles": [{"id": bundle_ids[h], "sha256": load_infos[h]["sha256"]} for h in SUPPORTED_HORIZONS],
               "datasetVersions": {f"h{h}": registry.bundle_entry(bundle_ids[h])["datasetVersion"] for h in SUPPORTED_HORIZONS}, "schema": SNAPSHOT_SCHEMA, "claimLevel": CLAIM_LEVEL,
               "builder": builder, "semanticConfig": semantic}
    if cls_manifest:
        content["classifierBundles"] = [{"id": b["id"], "sha256": b["sha256"], "loadMode": b["loadMode"], "libsRecordSha256": [lib["recordSha256"] for lib in b["vendoredLibs"]]} for b in cls_manifest]
        content["classifierCode"] = registry.classification_code_dependencies
    content_sha = sha256_bytes(canonical_json(content))
    counts = {"pairs": len(pairs), "hotels": len(hotels), "pairsH1": sum("1" in p["horizons"] for p in pairs), "pairsH3": sum("3" in p["horizons"] for p in pairs),
              "lead0Excluded": int((base["lead_time"] < min(SUPPORTED_HORIZONS)).sum()), "historySeries": len(history),
              "historySpanNull": sum(p["historySpanDays"] is None for p in pairs), "hotelsWithoutMetadata": missing_meta, "hotelCityMismatchWithMetadata": city_mismatch}
    manifest = {
        "schema": SNAPSHOT_SCHEMA, "snapshotId": f"serve-v1-{as_of}-{content_sha[:8]}", "contentSha256": content_sha, "dataAsOf": as_of,
        "generatedAt": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "claimLevel": CLAIM_LEVEL, "builder": builder, "semanticConfig": semantic,
        "datasetVersions": content["datasetVersions"], "datasetInputs": {f"h{h}": sample_infos[h] for h in SUPPORTED_HORIZONS},
        "bundles": [{"id": bundle_ids[h], "horizon": h, "sha256": load_infos[h]["sha256"], "modelName": registry.bundle_entry(bundle_ids[h])["modelName"],
                     "runtime": load_infos[h]["runtime"], "declaredDrift": load_infos[h]["declaredDrift"], "loadWarnings": load_infos[h]["loadWarnings"], "replay": replay[h]} for h in SUPPORTED_HORIZONS],
        "counts": counts, "nanCounts": nan_counts,
        "capabilities": {"horizons": list(SUPPORTED_HORIZONS), "classifier": {"available": True, "reason": None} if cls_manifest else {"available": False, "reason": "dependency_missing"}},
    }
    if cls_manifest:
        manifest["classifierBundles"] = cls_manifest
    modelinfo = build_model_info(registry, bundle_ids, bundles, replay)
    if cls_manifest:
        modelinfo["classifier"] = {"available": True, "reason": None, "horizons": cls_children}
    return {"manifest": manifest, "catalog": catalog, "history": history, "hotels": {"schema": "serving-hotels-1", "hotels": hotels},
            "modelinfo": modelinfo}


def publish_snapshot(root: Path, payload: Mapping[str, Any], *, hotels_info: Mapping[str, Any], registry_sha256: str) -> Path:
    """Ghi vào thư mục tạm cùng volume rồi `os.replace` -> `<root>/<snapshotId>`; KHÔNG ghi đè; `CURRENT.json` đổi cuối cùng."""
    manifest = dict(payload["manifest"])
    snapshot_id = manifest["snapshotId"]
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    final = root / snapshot_id
    if final.exists():
        raise SnapshotError(f"snapshotId đã tồn tại, không ghi đè: {snapshot_id}")
    tmp = root / f".tmp-{snapshot_id}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    tmp.mkdir()
    try:
        files = {}
        for name, key in (("catalog.json", "catalog"), ("history.json", "history"), ("hotels.json", "hotels"), ("modelinfo.json", "modelinfo")):
            data = canonical_json(payload[key])
            (tmp / name).write_bytes(data)
            files[name] = {"sha256": sha256_bytes(data), "bytes": len(data)}
        manifest["files"] = files
        manifest["hotelsMeta"] = dict(hotels_info)
        manifest["registrySha256"] = registry_sha256
        (tmp / "manifest.json").write_bytes(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True, allow_nan=False).encode("utf-8"))
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    pointer = root / "CURRENT.json"
    pointer_tmp = root / f".CURRENT.{os.getpid()}.tmp"
    pointer_tmp.write_text(json.dumps({"snapshotId": snapshot_id, "manifestSha256": sha256_file(final / "manifest.json")}, indent=1), encoding="utf-8")
    os.replace(pointer_tmp, pointer)
    return final
