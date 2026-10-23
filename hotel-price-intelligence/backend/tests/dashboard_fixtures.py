"""Snapshot tổng hợp NHỎ (schema serving-snapshot-2) cho test API: không DB, không ML, không dữ liệu thật. Ghi bằng cùng định dạng/hash mà store xác minh."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

AS_OF = "2026-10-04"
SNAPSHOT_ID = "serve-v1-2026-10-04-abcdef12"


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def horizon(price: float, pct: float, method: str = "model", reason: str = "ok", h: int = 1) -> dict[str, Any]:
    from app.dashboard.rules import direction_from_pct

    predicted = price * (1 + pct / 100.0)
    return {"method": method, "routingReason": reason, "predictedPrice": predicted, "expectedDelta": predicted - price, "expectedPctChange": pct,
            "direction": direction_from_pct(pct), "targetObservationDate": f"2026-10-{4 + h:02d}"}


def pair(hotel_id: str, city: str, check_in: str, price: float, *, mode: str = "history_enriched", h1_pct: float = 0.5, h3_pct: float | None = None,
         lead: int = 8, span: float | None = 10.0, count: int = 5, change7: float | None = 1.5, record_id: int = 1000) -> dict[str, Any]:
    horizons = {"1": horizon(price, 0.0, "persistence", "ci_lower_not_positive", 1) if mode == "cold_start" else horizon(price, h1_pct, h=1)}
    if lead >= 3:
        horizons["3"] = horizon(price, 0.0 if h3_pct is None else h3_pct, "model", "ok", 3)
    return {"hotelId": hotel_id, "city": city, "checkIn": check_in, "canonicalSeriesId": f"series-{hotel_id}-{check_in}", "recordId": record_id,
            "leadDays": lead, "currentPrice": float(price), "currentObservedAt": "2026-10-04T00:08:26Z", "inferenceMode": mode, "historyObservationCount": count,
            "historySpanDays": span, "roomSummary": {"maxOccupancy": 2, "areaM2": None, "breakfastIncluded": True, "freeCancellation": None}, "change7DaysPct": change7, "horizons": horizons}


def hotel(hotel_id: str, city: str, tenths: int | None, name: str | None = None, metadata: bool = True) -> dict[str, Any]:
    return {"hotelId": hotel_id, "name": name or hotel_id.replace("-", " ").title(), "city": city, "reviewScoreTenths": tenths, "metadata": metadata}


def default_world() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hà Nội: anchor (review 8,0) + 8 peer trong dải (một peer 9,1 ngoài dải) trên 3 ngày; Đà Lạt: 'lonely' (3 peer), 'norev' (không review), lead0 không có trong catalog."""
    hotels = [hotel("anchor-hotel", "Hà Nội", 80), hotel("p-80", "Hà Nội", 80), hotel("p-82", "Hà Nội", 82), hotel("p-78", "Hà Nội", 78), hotel("p-85", "Hà Nội", 85),
              hotel("p-75", "Hà Nội", 75), hotel("p-83", "Hà Nội", 83), hotel("p-79", "Hà Nội", 79), hotel("p-86", "Hà Nội", 86), hotel("far-91", "Hà Nội", 91),
              hotel("lonely", "Đà Lạt", 70), hotel("dl-a", "Đà Lạt", 71), hotel("dl-b", "Đà Lạt", 72), hotel("dl-c", "Đà Lạt", 66), hotel("norev", "Đà Lạt", None),
              hotel("noname-slug", "Vũng Tàu", None, name="noname-slug", metadata=False)]
    prices = {"anchor-hotel": 1_000_000, "p-80": 900_000, "p-82": 1_100_000, "p-78": 1_000_000, "p-85": 1_200_000, "p-75": 800_000, "p-83": 1_000_000, "p-79": 950_000, "p-86": 1_050_000, "far-91": 9_000_000}
    pairs, rid = [], 1
    for check_in, lead in (("2026-10-05", 1), ("2026-10-07", 3), ("2026-10-12", 8)):
        for hid, price in prices.items():
            pairs.append(pair(hid, "Hà Nội", check_in, price, lead=lead, h1_pct=1.0 if hid != "p-75" else -3.0, h3_pct=2.5, record_id=rid))
            rid += 1
    for hid, price in (("lonely", 500_000), ("dl-a", 520_000), ("dl-b", 480_000), ("dl-c", 700_000), ("norev", 600_000)):
        pairs.append(pair(hid, "Đà Lạt", "2026-10-12", price, lead=8, record_id=rid))
        rid += 1
    pairs.append(pair("noname-slug", "Vũng Tàu", "2026-10-12", 400_000, mode="cold_start", span=None, count=0, change7=None, lead=8, record_id=rid))
    return pairs, hotels


def model_info(classifier_available: bool = False) -> dict[str, Any]:
    def horizon_info(h: int, name: str, cold_use: bool) -> dict[str, Any]:
        return {"horizon": h, "modelName": name, "family": "hgb_l1", "datasetVersion": f"ds_test_nf{h}", "claimLevel": "dev_research_exploratory", "bundleSha256": "a" * 64,
                "trainedEnvironment": {"python": "3.13.15", "scikit-learn": "1.6.1"}, "validatedRuntime": {"python": "3.13.9", "scikit-learn": "1.7.2"},
                "compatibility": {"status": "dev_compatibility_empirically_checked", "rows": 1000, "maxAbsDiffVnd": 7.45e-09, "rowsOverTolerance": 0, "toleranceVnd": 0.01},
                "routing": [{"mode": "cold_start", "useModel": cold_use, "reason": "ok" if cold_use else "ci_lower_not_positive", "validationLiftVnd": 0.04, "ci95": [-0.1, 0.15], "validationRows": 2659},
                            {"mode": "history_enriched", "useModel": True, "reason": "ok", "validationLiftVnd": 0.16, "ci95": [0.1, 0.2], "validationRows": 17755}],
                "evidence": {"validationLiftPct": [14.5, 9.0, 19.9], "testLiftPct": [20.5, 5.0, 32.6], "testRoutedLiftPct": [20.2, 4.7, 32.4], "testRows": 20993, "source": "ml/regression/models/x/report.json", "sourceSha256": "b" * 64,
                             "ordinary": {"testLiftPct": [4.9, -4.6, 12.4] if h == 1 else [-3.0, -19.2, 7.0], "shareOfRowsPct": 99.4, "belowThreshold": h == 3}}}
    return {"schema": "serving-modelinfo-1", "horizons": [horizon_info(1, "hgb_l1-08", False), horizon_info(3, "hgb_l1-ratio1", True)],
            "classifier": {"available": classifier_available, "horizons": [classifier_child(1), classifier_child(3)] if classifier_available else []}}


def classifier_child(h: int) -> dict[str, Any]:
    """Child classifier TỔNG HỢP (không fit/không bundle thật) — để kiểm nhánh available=true của schema/TS/API, không che gap bằng []."""
    return {"horizon": h, "task": "classification", "modelName": "xgb-test", "family": "xgb", "datasetVersion": f"ds_test_nf{h}", "claimLevel": "none", "bundleSha256": "9" * 64,
            "trainedEnvironment": {"python": "3.13.15", "xgboost": "3.4.1"}, "validatedRuntime": {"python": "3.13.9", "xgboost": "3.4.1"}, "classMapping": {"0": "down", "1": "stable", "2": "up"},
            "compatibility": {"status": "dev_compatibility_empirically_checked", "rows": 100, "maxAbsProbabilityDiff": 1e-7, "labelMismatchCount": 0, "toleranceProbability": 1e-6},
            "coverage": {"snapshotRecords": 3679, "recordIdsLinkedToRegression": True}}


def with_classifier(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gắn nhãn lớp tổng hợp CHỈ cho các horizon của chính cặp (lead 1–2 không có h3)."""
    labels = {"1": "down", "3": "stable"}
    return [{**p, "classifier": {h: labels[h] for h in p["horizons"]}} for p in pairs]


def write_snapshot(root: Path, *, pairs: list[dict[str, Any]] | None = None, hotels: list[dict[str, Any]] | None = None, classifier_available: bool = False,
                   schema: str = "serving-snapshot-2", snapshot_id: str = SNAPSHOT_ID, drop_files: tuple[str, ...] = (), semantic: dict | None = None) -> Path:
    if pairs is None or hotels is None:
        pairs, hotels = default_world()
    if classifier_available:
        pairs = with_classifier(pairs)
    history = {p["canonicalSeriesId"]: [["2026-09-27", p["currentPrice"] * 0.98], ["2026-10-02", p["currentPrice"] * 0.99], ["2026-10-04", p["currentPrice"]]] for p in pairs}
    root.mkdir(parents=True, exist_ok=True)
    directory = root / snapshot_id
    directory.mkdir()
    payloads = {"catalog.json": {"schema": "serving-catalog-1", "dataAsOf": AS_OF, "pairs": pairs}, "history.json": history, "hotels.json": {"schema": "serving-hotels-1", "hotels": hotels},
                "modelinfo.json": model_info(classifier_available)}
    files = {}
    for name, payload in payloads.items():
        data = canonical(payload)
        if name not in drop_files:
            (directory / name).write_bytes(data)
        files[name] = {"sha256": sha(data), "bytes": len(data)}
    manifest = {"schema": schema, "snapshotId": snapshot_id, "contentSha256": "c" * 64, "dataAsOf": AS_OF, "generatedAt": "2026-10-22T18:25:48Z", "claimLevel": "dev_research_exploratory",
                "datasetVersions": {"h1": "ds_test_nf1", "h3": "ds_test_nf3"}, "bundles": [{"id": "reg-h1-test", "horizon": 1, "sha256": "d" * 64}, {"id": "reg-h3-test", "horizon": 3, "sha256": "e" * 64}],
                "capabilities": {"horizons": [1, 3], "classifier": {"available": classifier_available, "reason": None if classifier_available else "dependency_missing"}},
                "hotelsMeta": {"exportedAtUtc": "2026-10-22T17:51:57Z", "hotelsSha256": "f" * 64, "rowCount": len(hotels)}, "files": files,
                "semanticConfig": semantic if semantic is not None else __import__("app.dashboard.rules", fromlist=["semantic_config"]).semantic_config(),
                "builder": {"version": "test", "codeSha256": "0" * 64}}
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8")
    (directory / "manifest.json").write_bytes(manifest_bytes)
    (root / "CURRENT.json").write_text(json.dumps({"snapshotId": snapshot_id, "manifestSha256": sha(manifest_bytes)}), encoding="utf-8")
    return directory


class SyncClient:
    """Client đồng bộ mỏng trên httpx.ASGITransport (starlette 0.35 TestClient hỏng với httpx >= 0.28). Mỗi request chạy trong event loop riêng; lỗi ứng dụng trả 500 như server thật."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, url: str, **kwargs):
        import httpx

        async def run():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, url, **kwargs)

        return asyncio.run(run())

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)
