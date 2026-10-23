"""Snapshot (C5, C9–C12) trên DỮ LIỆU THẬT đã xuất (nf1/nf3) + bundle thật, chỉ đọc, không fit. Cần: anaconda env đã replay; bỏ qua nếu thiếu artifact cục bộ."""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.dashboard import rules
from app.dashboard.rules import direction_from_pct
from app.ml_serving import snapshot as S
from app.ml_serving.errors import IntegrityError, SnapshotError
from app.ml_serving.registry import WORKSPACE_ROOT, load_bundle, load_registry, sha256_file

REG = load_registry()
BUNDLE_IDS = {1: "reg-h1-hgb_l1-08", 3: "reg-h3-hgb_l1-ratio1"}
DATASETS = {h: WORKSPACE_ROOT / "outputs" / "datasets" / REG.bundle_entry(b)["datasetVersion"] for h, b in BUNDLE_IDS.items()}
META = WORKSPACE_ROOT / "discuss" / "model-serving-dashboard-20261023" / "artifacts" / "measurements" / "data" / "hotels_meta_20261022.json"
pytestmark = pytest.mark.skipif(not all((d / "samples.parquet").exists() for d in DATASETS.values()) or not META.exists(), reason="thiếu dataset/metadata cục bộ")
FORBIDDEN_KEYS = ("y_price", "y_delta", "y_pct", "y_direction", "has_label", "label_usable", "label_match", "hotel_seen", "split")


@pytest.fixture(scope="module")
def built():
    hotels, info = S.load_hotels_meta(META)
    payload = S.build_snapshot_payload(REG, hotels, dataset_dirs=DATASETS, bundle_ids=BUNDLE_IDS)
    return payload, info


def test_c5_full_replay_every_build_rows_finite_within_tolerance(built):
    payload, _ = built
    by_h = {b["horizon"]: b["replay"] for b in payload["manifest"]["bundles"]}
    assert by_h[1]["rows"] == 41_407 and by_h[3]["rows"] == 21_582
    for replay in by_h.values():
        for label in ("unrouted", "routed"):
            assert replay[label]["rowsOverTolerance"] == 0 and replay[label]["maxAbsDiffVnd"] <= 0.01
        assert replay["toleranceVnd"] == 0.01


def test_c9_lead_boundary_counts_and_supported_horizons(built):
    payload, _ = built
    counts = payload["manifest"]["counts"]
    assert (counts["pairs"], counts["pairsH1"], counts["pairsH3"], counts["lead0Excluded"]) == (3679, 3679, 3557, 30)
    for pair in payload["catalog"]["pairs"]:
        lead = pair["leadDays"]
        assert lead >= 1                                                         # lead0 không có trong catalog
        assert ("1" in pair["horizons"]) == (lead >= 1) and ("3" in pair["horizons"]) == (lead >= 3)
        assert set(pair["horizons"]) <= {"1", "3"}                               # không bao giờ h7/h14
        for h, item in pair["horizons"].items():
            assert pd.Timestamp(item["targetObservationDate"]) <= pd.Timestamp(pair["checkIn"])     # asOf+h <= checkIn
    leads = {p["leadDays"] for p in payload["catalog"]["pairs"]}
    assert 1 in leads and 2 in leads and 3 in leads


def test_c9_lead_by_lead_semantics(built):
    payload, _ = built
    by_lead = {}
    for p in payload["catalog"]["pairs"]:
        by_lead.setdefault(min(p["leadDays"], 3), set()).add(tuple(sorted(p["horizons"])))
    assert by_lead[1] == {("1",)} and by_lead[2] == {("1",)} and by_lead[3] == {("1", "3")}


def test_c9b_cold_start_with_null_span_is_kept_and_null(built):
    payload, _ = built
    nulls = [p for p in payload["catalog"]["pairs"] if p["historySpanDays"] is None]
    assert len(nulls) == 286 == payload["manifest"]["counts"]["historySpanNull"]
    assert all(p["historyObservationCount"] == 0 and p["inferenceMode"] == "cold_start" for p in nulls)
    for p in nulls:
        assert p["horizons"]["1"]["method"] == "persistence" and p["horizons"]["1"]["expectedPctChange"] == 0.0 and p["horizons"]["1"]["direction"] == "stable"
    text = json.dumps(payload["catalog"], allow_nan=False)                      # allow_nan=False ném lỗi nếu còn NaN/inf
    assert "NaN" not in text and "Infinity" not in text


def test_c9_h1_cold_start_persistence_h3_uses_model(built):
    payload, _ = built
    for p in payload["catalog"]["pairs"]:
        if p["inferenceMode"] == "cold_start":
            assert p["horizons"]["1"]["method"] == "persistence" and p["horizons"]["1"]["routingReason"] == "ci_lower_not_positive"
            if "3" in p["horizons"]:
                assert p["horizons"]["3"]["method"] == "model"                   # routing h3 cold_start use_model=True (theo bundle)
        else:
            assert p["horizons"]["1"]["method"] == "model"


def test_pct_direction_and_delta_are_consistent_everywhere(built):
    payload, _ = built
    for p in payload["catalog"]["pairs"]:
        for item in p["horizons"].values():
            assert item["predictedPrice"] > 0 and item["expectedDelta"] == pytest.approx(item["predictedPrice"] - p["currentPrice"])
            assert item["expectedPctChange"] == pytest.approx(100 * (item["predictedPrice"] - p["currentPrice"]) / p["currentPrice"])
            assert item["direction"] == direction_from_pct(item["expectedPctChange"])


def test_c10_causal_history_and_no_label_leak(built):
    payload, _ = built
    as_of = payload["manifest"]["dataAsOf"]
    assert as_of == "2026-10-04"
    for series, points in payload["history"].items():
        dates = [d for d, _ in points]
        assert dates == sorted(dates) and len(dates) == len(set(dates)) and max(dates) <= as_of
    text = json.dumps(payload["catalog"]) + json.dumps(payload["history"]) + json.dumps(payload["hotels"])
    for key in FORBIDDEN_KEYS:
        assert key not in text, key
    first = payload["catalog"]["pairs"][0]
    assert set(first) == {"hotelId", "city", "checkIn", "canonicalSeriesId", "recordId", "leadDays", "currentPrice", "currentObservedAt", "inferenceMode", "historyObservationCount",
                          "historySpanDays", "roomSummary", "change7DaysPct", "horizons"}


def test_c10_change7_is_exact_date_only(built):
    payload, _ = built
    for p in payload["catalog"]["pairs"]:
        points = dict((d, price) for d, price in payload["history"][p["canonicalSeriesId"]])
        past = points.get("2026-09-27")
        if past is None:
            assert p["change7DaysPct"] is None
        else:
            assert p["change7DaysPct"] == pytest.approx(100 * (p["currentPrice"] - past) / past)


def test_current_observed_at_is_utc_z_and_matches_vn_date(built):
    payload, _ = built
    for p in payload["catalog"]["pairs"][:300]:
        ts = pd.Timestamp(p["currentObservedAt"])
        assert p["currentObservedAt"].endswith("Z") and (ts + pd.Timedelta(hours=7)).strftime("%Y-%m-%d") == "2026-10-04"


def test_nan_counts_recorded_and_no_violation(built):
    payload, _ = built
    nan = payload["manifest"]["nanCounts"]["h1"]
    assert (nan["price_lag_1"], nan["price_lag_14"], nan["price_rolling_std_7"], nan["room_area_m2"]) == (358, 2321, 481, 60)
    assert all(nan[f] == 0 for f in nan if f not in REG.nullable_features)


def test_hotels_catalog_includes_all_pair_hotels_even_without_metadata(built):
    payload, info = built
    ids = {p["hotelId"] for p in payload["catalog"]["pairs"]}
    assert ids == {h["hotelId"] for h in payload["hotels"]["hotels"]} and len(ids) == 314
    assert info["rowCount"] == 358 and info["hotelsSha256"].startswith("5843a654")


def test_content_hash_is_deterministic_and_excludes_time(built):
    payload, _ = built
    again = S.build_snapshot_payload(REG, S.load_hotels_meta(META)[0], dataset_dirs=DATASETS, bundle_ids=BUNDLE_IDS)
    assert again["manifest"]["contentSha256"] == payload["manifest"]["contentSha256"]
    assert again["manifest"]["snapshotId"] == payload["manifest"]["snapshotId"] == f"serve-v1-2026-10-04-{payload['manifest']['contentSha256'][:8]}"
    assert S.canonical_json(again["catalog"]) == S.canonical_json(payload["catalog"]) and S.canonical_json(again["history"]) == S.canonical_json(payload["history"])


def test_c11_publish_is_atomic_never_overwrites_and_pointer_changes_last(built, tmp_path):
    payload, info = built
    root = tmp_path / "serving"
    final = S.publish_snapshot(root, payload, hotels_info=info, registry_sha256="0" * 64)
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        assert sha256_file(final / name) == meta["sha256"]
    pointer = json.loads((root / "CURRENT.json").read_text(encoding="utf-8"))
    assert pointer["snapshotId"] == manifest["snapshotId"] and pointer["manifestSha256"] == sha256_file(final / "manifest.json")
    with pytest.raises(SnapshotError, match="không ghi đè"):
        S.publish_snapshot(root, payload, hotels_info=info, registry_sha256="0" * 64)
    assert sorted(p.name for p in root.iterdir()) == ["CURRENT.json", manifest["snapshotId"]]       # không còn thư mục tạm


def test_c11_failed_publish_leaves_no_temp_and_keeps_pointer(built, tmp_path):
    payload, info = built
    root = tmp_path / "serving"
    S.publish_snapshot(root, payload, hotels_info=info, registry_sha256="0" * 64)
    before = (root / "CURRENT.json").read_text(encoding="utf-8")
    broken = dict(payload)
    broken["manifest"] = {**payload["manifest"], "snapshotId": "serve-v1-broken"}
    broken["catalog"] = {"bad": float("nan")}                                    # canonical_json(allow_nan=False) ném lỗi giữa chừng
    with pytest.raises(ValueError):
        S.publish_snapshot(root, broken, hotels_info=info, registry_sha256="0" * 64)
    assert sorted(p.name for p in root.iterdir() if p.name != "CURRENT.json") == [payload["manifest"]["snapshotId"]]
    assert (root / "CURRENT.json").read_text(encoding="utf-8") == before


def test_c12_two_datasets_must_agree_on_asof_slice():
    a = pd.DataFrame({c: [1, 2] for c in S.SHARED_COLUMNS})
    b = a.copy()
    S.assert_same_slice(a, b)
    b.loc[1, "current_price"] = 99
    with pytest.raises(SnapshotError, match="current_price"):
        S.assert_same_slice(a, b)
    with pytest.raises(SnapshotError, match="số hàng"):
        S.assert_same_slice(a, a.iloc[:1])


def test_c12_samples_checksum_is_verified_before_reading(tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    (ds / "samples.parquet").write_bytes(b"not a parquet")
    (ds / "output_checksums.json").write_text(json.dumps({"samples.parquet": {"file_sha256": "0" * 64, "rows": 1}}), encoding="utf-8")
    with pytest.raises(IntegrityError):
        S.read_samples(ds)


def test_hotels_meta_checksum_and_range_validated(tmp_path):
    good = json.loads(META.read_text(encoding="utf-8"))
    bad = dict(good)
    bad["hotels"] = [dict(good["hotels"][0], reviewScoreTenths=63)] + good["hotels"][1:]       # đổi nội dung -> hash lệch
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(IntegrityError):
        S.load_hotels_meta(path)


# ---------------------------------------------------------------- MIN1: C12 so toàn bộ feature contract (NaN-aware), không chỉ 1 witness
@pytest.fixture(scope="module")
def slices():
    out, bundles = {}, {}
    for h, bundle_id in BUNDLE_IDS.items():
        frame, _ = S.read_samples(DATASETS[h])
        out[h], _ = S.asof_slice(frame)
        bundles[h], _ = load_bundle(REG, bundle_id)
    return out, bundles


def test_min1_shared_columns_are_union_of_real_features_plus_keys(slices):
    _, bundles = slices
    columns = S.shared_columns(bundles)
    for bundle in bundles.values():
        assert set(bundle["features"]) <= set(columns)
    assert set(S.KEY_META_COLUMNS) <= set(columns) and len(columns) == len(set(columns)) >= 36


def mutate_value(original):
    import datetime as dt

    import numpy as np

    if original is None or original is pd.NA or (isinstance(original, float) and np.isnan(original)):
        return 0
    if isinstance(original, str):
        return "ZZZ_MUTATED"
    if isinstance(original, (bool, np.bool_)):
        return not bool(original)
    if isinstance(original, pd.Timestamp):
        return original + pd.Timedelta(days=1)
    if isinstance(original, dt.date):
        return original + dt.timedelta(days=1)
    return original + 1


def test_min1_real_slices_agree_and_every_feature_mutation_is_caught(slices):
    frames, bundles = slices
    columns = S.shared_columns(bundles)
    S.assert_same_slice(frames[1], frames[3], columns)                                  # thật sự trùng
    for column in columns:
        mutated = frames[3].copy()
        i = mutated.index[0]
        original = mutated.at[i, column]
        if column == "warehouse_record_id":
            continue                                                                    # là khóa sắp xếp; đổi nó là đổi cặp, kiểm riêng bên dưới
        mutated[column] = mutated[column].astype(object)
        mutated.at[i, column] = mutate_value(original)
        with pytest.raises(SnapshotError, match=column):
            S.assert_same_slice(frames[1], mutated, columns)


def test_min1_nan_vs_value_and_nan_vs_nan(slices):
    frames, bundles = slices
    columns = S.shared_columns(bundles)
    nan_rows = frames[3].index[frames[3]["price_lag_1"].isna()]
    assert len(nan_rows) > 0
    both = frames[3].copy()
    S.assert_same_slice(frames[1], both, columns)                                        # NaN == NaN được coi là trùng
    broken = frames[3].copy()
    broken.loc[nan_rows[0], "price_lag_1"] = 123.0                                       # NaN -> số: phải lệch
    with pytest.raises(SnapshotError, match="price_lag_1"):
        S.assert_same_slice(frames[1], broken, columns)


# ---------------------------------------------------------------- MIN2: danh tính mã + cấu hình trong content hash
def test_min2_manifest_has_independent_code_inventory_and_semantic_config(built):
    payload, _ = built
    manifest = payload["manifest"]
    inventory = manifest["builder"]["codeInventory"]
    backend = Path(S.__file__).resolve().parents[2]
    assert set(inventory) == set(S.CODE_INVENTORY_FILES) and manifest["builder"]["version"] == S.BUILDER_VERSION
    for rel, digest in inventory.items():
        assert hashlib.sha256((backend / rel).read_bytes()).hexdigest() == digest, rel         # tính SHA độc lập từ file
    assert manifest["builder"]["codeSha256"] == hashlib.sha256(S.canonical_json(inventory)).hexdigest()
    assert manifest["semanticConfig"] == rules.semantic_config() and manifest["semanticConfig"]["compset"]["bandTenths"] == 5 and manifest["semanticConfig"]["direction"]["thresholdPct"] == 2.0
    assert manifest["builder"]["config"]["nullableFeatures"] == sorted(REG.nullable_features)
    assert "training" not in json.dumps(manifest["builder"]["codeInventory"])               # không chép hash mã training vào field builder để lấp tên


def test_min2_generated_at_alone_does_not_change_content_hash_but_config_and_code_do(built, monkeypatch):
    payload, _ = built
    hotels = S.load_hotels_meta(META)[0]
    base = payload["manifest"]["contentSha256"]
    later = S.build_snapshot_payload(REG, hotels, dataset_dirs=DATASETS, bundle_ids=BUNDLE_IDS, generated_at=datetime(2031, 1, 1, tzinfo=timezone.utc))
    assert later["manifest"]["generatedAt"] != payload["manifest"]["generatedAt"] and later["manifest"]["contentSha256"] == base
    monkeypatch.setattr(rules, "MIN_GROUP_SIZE", 6)
    changed_cfg = S.build_snapshot_payload(REG, hotels, dataset_dirs=DATASETS, bundle_ids=BUNDLE_IDS)
    assert changed_cfg["manifest"]["contentSha256"] != base and changed_cfg["manifest"]["snapshotId"] != payload["manifest"]["snapshotId"]
    monkeypatch.undo()
    real_inventory = S.code_inventory()
    monkeypatch.setattr(S, "code_inventory", lambda *a, **k: {**real_inventory, "app/ml_serving/inference.py": "0" * 64})
    changed_code = S.build_snapshot_payload(REG, hotels, dataset_dirs=DATASETS, bundle_ids=BUNDLE_IDS)
    assert changed_code["manifest"]["contentSha256"] != base and changed_code["manifest"]["builder"]["codeSha256"] != payload["manifest"]["builder"]["codeSha256"]


# ---------------------------------------------------------------- model-info: sao chép từ report đã ghim, không tính lại
def test_modelinfo_matches_independent_report_read(built):
    payload, _ = built
    info = {h["horizon"]: h for h in payload["modelinfo"]["horizons"]}
    for h, bundle_id in BUNDLE_IDS.items():
        entry = REG.bundle_entry(bundle_id)
        report = json.loads((WORKSPACE_ROOT / entry["report"]["path"]).read_text(encoding="utf-8"))
        name = entry["modelName"]
        v = report["finalists"][name]["validation"]
        item = info[h]
        assert item["evidence"]["validationLiftPct"][0] == pytest.approx(100 * v["lift_vnd"], abs=1e-3) and item["evidence"]["validationLiftPct"][1] == pytest.approx(100 * v["lift_vnd_ci95_hotel"][0], abs=1e-3)
        t = report["test"]["table"][name]
        assert item["evidence"]["testLiftPct"][0] == pytest.approx(100 * t["lift_vnd"], abs=1e-3) and item["evidence"]["testRows"] == t["n"]
        assert item["evidence"]["sourceSha256"] == entry["report"]["sha256"] and item["bundleSha256"] == entry["sha256"]
        assert item["compatibility"]["rows"] in (41_407, 21_582) and item["compatibility"]["rowsOverTolerance"] == 0 and item["compatibility"]["toleranceVnd"] == 0.01
    assert info[1]["evidence"]["ordinary"]["belowThreshold"] is False and info[3]["evidence"]["ordinary"]["belowThreshold"] is True            # h3: lift gộp đi cùng tổn thất ở hàng thông thường
    assert {r["mode"]: r["useModel"] for r in info[1]["routing"]} == {"cold_start": False, "history_enriched": True}
    assert {r["mode"]: r["useModel"] for r in info[3]["routing"]} == {"cold_start": True, "history_enriched": True}
    assert payload["modelinfo"]["classifier"] == {"available": False, "reason": "dependency_missing", "horizons": []}


# ---------------------------------------------------------------- nhánh phân loại trong snapshot (nhãn lớp, claim none)
CLS_IDS = {1: "cls-h1-xgb-06", 3: "cls-h3-xgb-06"}
CLS_READY = all((WORKSPACE_ROOT / REG.bundle_entry(i)["path"]).exists() for i in CLS_IDS.values()) and (WORKSPACE_ROOT / "hotel-price-intelligence" / "data" / "serving" / "libs" / "xgboost-3.4.1").exists()
needs_classifier = pytest.mark.skipif(not CLS_READY, reason="thiếu bundle phân loại/xgboost đóng gói cục bộ")


@pytest.fixture(scope="module")
def built_cls():
    hotels, info = S.load_hotels_meta(META)
    return S.build_snapshot_payload(REG, hotels, dataset_dirs=DATASETS, bundle_ids=BUNDLE_IDS, classifier_bundle_ids=CLS_IDS), info


@needs_classifier
def test_cls_capability_replay_and_manifest(built_cls):
    payload, _ = built_cls
    manifest = payload["manifest"]
    assert manifest["capabilities"]["classifier"] == {"available": True, "reason": None}
    by_h = {b["horizon"]: b for b in manifest["classifierBundles"]}
    assert (by_h[1]["replay"]["rows"], by_h[3]["replay"]["rows"]) == (41_479, 21_653)
    for item in by_h.values():
        assert item["replay"]["maxAbsProbabilityDiff"] <= 1e-6 and item["replay"]["labelMismatchCount"] == 0 and item["replay"]["rowsOverTolerance"] == 0
        assert item["loadMode"] == "booster_model_section_only" and item["vendoredLibs"][0]["filesVerified"] > 10


@needs_classifier
def test_cls_labels_cover_exactly_the_regression_horizons_and_are_labels_only(built_cls, built):
    payload, _ = built_cls
    base_payload, _ = built
    pairs = payload["catalog"]["pairs"]
    assert (sum("1" in p["classifier"] for p in pairs), sum("3" in p["classifier"] for p in pairs)) == (3679, 3557) == (payload["manifest"]["counts"]["pairsH1"], payload["manifest"]["counts"]["pairsH3"])
    for p in pairs:
        assert set(p["classifier"]) == set(p["horizons"]) and set(p["classifier"].values()) <= {"down", "stable", "up"}
    text = json.dumps(payload["catalog"]).lower()
    assert "p_up" not in text and "proba" not in text                                             # không có xác suất theo từng record ở bất kỳ đâu trong catalog (modelinfo chỉ có số đo replay)
    stripped = [{k: v for k, v in p.items() if k != "classifier"} for p in pairs]
    assert stripped == base_payload["catalog"]["pairs"]                                           # nhánh phân loại KHÔNG đổi một byte phần hồi quy
    assert payload["history"] == base_payload["history"] and payload["hotels"] == base_payload["hotels"]


@needs_classifier
def test_cls_identity_changes_snapshot_id_and_modelinfo_children(built_cls, built):
    payload, _ = built_cls
    base_payload, _ = built
    assert payload["manifest"]["contentSha256"] != base_payload["manifest"]["contentSha256"] and payload["manifest"]["snapshotId"] != base_payload["manifest"]["snapshotId"]
    classifier = payload["modelinfo"]["classifier"]
    assert classifier["available"] is True and classifier["reason"] is None and [c["horizon"] for c in classifier["horizons"]] == [1, 3]
    counts = payload["manifest"]["counts"]
    for child in classifier["horizons"]:
        entry = REG.bundle_entry(CLS_IDS[child["horizon"]])
        assert child["claimLevel"] == "none" and child["bundleSha256"] == entry["sha256"] and child["modelName"] == "xgb-06" and "limitations" not in child
        assert child["coverage"] == {"snapshotRecords": counts["pairsH1"] if child["horizon"] == 1 else counts["pairsH3"], "recordIdsLinkedToRegression": True}
        assert child["trainedEnvironment"]["xgboost"] == "3.4.1" and child["validatedRuntime"]["xgboost"] == "3.4.1"
    assert payload["modelinfo"]["horizons"] == base_payload["modelinfo"]["horizons"]


@needs_classifier
def test_cls_snapshot_served_by_api_end_to_end(built_cls, tmp_path):
    import dashboard_fixtures as F
    from app.dashboard.app import create_app
    from app.dashboard.settings import Settings

    payload, info = built_cls
    root = tmp_path / "serving"
    S.publish_snapshot(root, payload, hotels_info=info, registry_sha256="0" * 64)
    client = F.SyncClient(create_app(Settings(serving_root=root, cors_origins=())))
    base = "/api/dashboard/v1"
    assert client.get(base + "/config").json()["classifier"] == {"available": True, "reason": None}
    pair = next(p for p in payload["catalog"]["pairs"] if "3" in p["horizons"])
    body = client.get(base + "/forecast", params={"hotelId": pair["hotelId"], "checkIn": pair["checkIn"]}).json()
    assert body["classifier"]["available"] is True and body["classifier"]["claim"] == "none"
    assert {(r["horizon"], r["label"]) for r in body["classifier"]["results"]} == {(int(h), v) for h, v in pair["classifier"].items()}
    assert all(r["recordId"] == pair["recordId"] for r in body["classifier"]["results"])
    model = client.get(base + "/model-info").json()["classifier"]
    assert model["available"] is True and model["claim"] == "none" and len(model["horizons"]) == 2
    for child in model["horizons"]:
        assert len(child["limitations"]) == 5 and any("recall" in x for x in child["limitations"]) and any("chưa được hiệu chỉnh" in x for x in child["limitations"])
