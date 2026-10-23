"""Nạp & xác minh serving snapshot (JSON thuần). API KHÔNG nạp pickle/ML/DB. Mọi bất thường ⇒ `SnapshotUnavailable` (endpoint trả 503), không phục vụ dữ liệu chưa qua kiểm.

Kiểm khi khởi động: CURRENT.json -> thư mục snapshot (tên đơn giản, không dấu phân cách) -> sha256 manifest == CURRENT -> schema -> sha256 từng file == manifest.files -> parse -> chỉ mục.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import rules

SUPPORTED_SCHEMA = "serving-snapshot-2"
REQUIRED_FILES = ("catalog.json", "history.json", "hotels.json", "modelinfo.json")
SNAPSHOT_ID = re.compile(r"^serve-v1-\d{4}-\d{2}-\d{2}-[0-9a-f]{8}$")


class SnapshotUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _no_constant(name: str) -> None:
    raise ValueError(f"hằng số JSON không hợp lệ: {name}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)       # NaN/Infinity bị từ chối


@dataclass
class SnapshotStore:
    root: Path
    manifest: dict[str, Any]
    pairs: list[dict[str, Any]]
    history: dict[str, list[list[Any]]]
    hotels: dict[str, dict[str, Any]]
    model_info: dict[str, Any]
    by_key: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    by_hotel: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_city_checkin: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def snapshot_id(self) -> str:
        return self.manifest["snapshotId"]

    @property
    def data_as_of(self) -> str:
        return self.manifest["dataAsOf"]


def load_store(serving_root: Path) -> SnapshotStore:
    serving_root = Path(serving_root)
    current = serving_root / "CURRENT.json"
    try:
        pointer = _load_json(current)
    except (OSError, ValueError) as exc:
        raise SnapshotUnavailable(f"không đọc được CURRENT.json: {exc.__class__.__name__}") from exc
    snapshot_id = pointer.get("snapshotId") if isinstance(pointer, dict) else None
    if not isinstance(snapshot_id, str) or not SNAPSHOT_ID.match(snapshot_id):
        raise SnapshotUnavailable("snapshotId trong CURRENT.json không hợp lệ")
    directory = serving_root / snapshot_id
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise SnapshotUnavailable("thiếu manifest.json")
    if _sha256(manifest_path) != pointer.get("manifestSha256"):
        raise SnapshotUnavailable("manifest.json không khớp CURRENT.json")
    try:
        manifest = _load_json(manifest_path)
    except ValueError as exc:
        raise SnapshotUnavailable("manifest.json hỏng") from exc
    if manifest.get("schema") != SUPPORTED_SCHEMA:
        raise SnapshotUnavailable(f"schema snapshot không hỗ trợ: {manifest.get('schema')!r}")
    if manifest.get("snapshotId") != snapshot_id:
        raise SnapshotUnavailable("snapshotId trong manifest lệch")
    if manifest.get("semanticConfig") != rules.semantic_config():
        raise SnapshotUnavailable("semanticConfig của snapshot không khớp luật hiện hành (ngưỡng hướng/nhóm/alert đã đổi): dựng lại snapshot")
    files = manifest.get("files", {})
    for name in REQUIRED_FILES:
        meta = files.get(name)
        path = directory / name
        if meta is None or not path.is_file():
            raise SnapshotUnavailable(f"thiếu file {name}")
        if _sha256(path) != meta["sha256"]:
            raise SnapshotUnavailable(f"hash {name} không khớp manifest")
    try:
        catalog = _load_json(directory / "catalog.json")
        history = _load_json(directory / "history.json")
        hotels_payload = _load_json(directory / "hotels.json")
        model_info = _load_json(directory / "modelinfo.json")
    except ValueError as exc:
        raise SnapshotUnavailable(f"JSON snapshot hỏng: {exc}") from exc
    pairs = catalog.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise SnapshotUnavailable("catalog rỗng")
    hotels = {h["hotelId"]: h for h in hotels_payload.get("hotels", [])}
    store = SnapshotStore(root=directory, manifest=manifest, pairs=pairs, history=history, hotels=hotels, model_info=model_info)
    for pair in pairs:
        key = (pair["hotelId"], pair["checkIn"])
        if key in store.by_key:
            raise SnapshotUnavailable(f"cặp trùng: {key}")
        if pair["hotelId"] not in hotels:
            raise SnapshotUnavailable(f"hotel thiếu trong hotels.json: {pair['hotelId']}")
        if pair["canonicalSeriesId"] not in history:
            raise SnapshotUnavailable("thiếu history cho một series")
        for value in (pair["currentPrice"], *(h["predictedPrice"] for h in pair["horizons"].values())):
            if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
                raise SnapshotUnavailable("giá không hữu hạn/không dương trong catalog")
        store.by_key[key] = pair
        store.by_hotel.setdefault(pair["hotelId"], []).append(pair)
        store.by_city_checkin.setdefault((pair["city"], pair["checkIn"]), []).append(pair)
    for items in store.by_hotel.values():
        items.sort(key=lambda p: p["checkIn"])
    return store
