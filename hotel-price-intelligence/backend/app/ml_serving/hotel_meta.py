"""Xuất metadata hotel tối thiểu (offline, một lần) thành JSON có checksum — phần THUẦN (không DB) để test; script `scripts/export_hotel_metadata.py` chỉ chạy một SELECT chỉ-đọc.

`review_score` luôn là số nguyên phần mười (8,3 -> 83); >1 chữ số thập phân hoặc ngoài [0,10] ⇒ dừng (không làm tròn). Metadata là của NGÀY XUẤT,
không phải thuộc tính quan sát của snapshot; chỉ dùng hiển thị/nhóm minh họa, không vào model/feature/lift.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from app.dashboard.rules import tenths_from_score

SELECT = "SELECT hotel_id, name, city, review_score FROM hotels ORDER BY hotel_id"
SCHEMA = "hotels-meta-1"


def rows_to_hotels(rows: Iterable[Sequence[Any]]) -> list[dict[str, Any]]:
    hotels, seen = [], set()
    for hotel_id, name, city, review in rows:
        if hotel_id in seen:
            raise ValueError(f"hotel_id trùng: {hotel_id}")
        seen.add(hotel_id)
        hotels.append({"hotelId": hotel_id, "name": name, "city": city, "reviewScoreTenths": tenths_from_score(review)})
    return hotels


def build_payload(rows: Iterable[Sequence[Any]], exported_at_utc: datetime) -> dict[str, Any]:
    hotels = rows_to_hotels(rows)
    body = json.dumps(hotels, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {"schema": SCHEMA, "exportedAtUtc": exported_at_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "select": SELECT, "rowCount": len(hotels),
            "hotelsSha256": hashlib.sha256(body).hexdigest(),
            "note": "metadata ngày xuất, KHÔNG phải thuộc tính quan sát của snapshot; chỉ để hiển thị/nhóm minh họa, không vào model/feature.", "hotels": hotels}


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"không ghi đè: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8", newline="\n")
