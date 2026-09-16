"""Merge `hotels` tu N nguon theo business key `hotel_id` (WAREHOUSE_EDA_ML_SPEC.md muc 6) - pure.

Merge theo NHOM TRUONG, khong chon nguyen 1 dong thang (chot voi GPT, file 02 D5):
- Nhom attribute (`ATTRIBUTE_FIELDS` + `attributes_updated_at`): thang = `attributes_updated_at` moi
  hon (NULL = cu nhat), roi so field non-NULL TRONG NHOM, roi `source_priority` nho hon.
- Nhom booking status (`STATUS_FIELDS` + `booking_status_checked_at`): tuong tu, voi timestamp va
  completeness cua RIENG nhom status.
- `created_at` = MIN tren moi nguon. `city` CHI tu cohort manifest (hotel ngoai cohort -> NULL).
Completeness cua nhom nay khong duoc anh huong nhom kia - 1 dong warehouse co the lay attribute tu
nguon A va booking status tu nguon B. Tie-break cuoi la `source_priority`, KHONG so sanh chuoi
`source_code` (registry mo, ten nguon khong mang thu tu uu tien ngam dinh).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .errors import WarehouseError

ATTRIBUTE_FIELDS = ("name", "name_normalized", "hotel_link", "address", "review_score", "review_count", "amenities")
STATUS_FIELDS = ("booking_status", "booking_status_reason")
# Toan bo cot cua `hotels` ma merge nay xu ly. Importer doi chieu voi information_schema: schema co
# them cot moi ma chua quyet dinh nhom -> FAIL, khong am tham bo cot do.
HOTEL_COLUMNS = frozenset({
    "hotel_id", "city", "created_at", "attributes_updated_at", "booking_status_checked_at",
    *ATTRIBUTE_FIELDS, *STATUS_FIELDS,
})
_OLDEST = dt.datetime.min


@dataclass(frozen=True)
class HotelSourceRow:
    source_code: str
    source_priority: int
    row: Mapping[str, Any]


@dataclass(frozen=True)
class MergedHotel:
    row: dict[str, Any]
    attribute_source: str
    status_source: str
    source_codes: tuple[str, ...]


def _completeness(row: Mapping[str, Any], fields: Sequence[str]) -> int:
    return sum(1 for field in fields if row.get(field) is not None)


def _winner(rows: Sequence[HotelSourceRow], timestamp: str, fields: Sequence[str]) -> HotelSourceRow:
    def key(item: HotelSourceRow):
        stamp = item.row.get(timestamp)
        return (stamp is not None, stamp or _OLDEST, _completeness(item.row, fields), -item.source_priority)
    return max(rows, key=key)


def merge_hotel(hotel_id: str, rows: Sequence[HotelSourceRow],
                city_of: Callable[[str], str | None]) -> MergedHotel:
    if not rows:
        raise WarehouseError(f"merge_hotel({hotel_id!r}) khong co dong nguon nao.")
    priorities = [item.source_priority for item in rows]
    if len(set(priorities)) != len(priorities):
        raise WarehouseError(f"hotel {hotel_id!r}: 2 dong nguon trung source_priority {priorities} - tie-break khong xac dinh.")
    for item in rows:
        if item.row.get("hotel_id") != hotel_id:
            raise WarehouseError(f"dong cua nguon {item.source_code!r} co hotel_id {item.row.get('hotel_id')!r} != {hotel_id!r}")
    attribute = _winner(rows, "attributes_updated_at", ATTRIBUTE_FIELDS)
    status = _winner(rows, "booking_status_checked_at", STATUS_FIELDS)
    created = [item.row["created_at"] for item in rows if item.row.get("created_at") is not None]
    if not created:
        raise WarehouseError(f"hotel {hotel_id!r}: moi nguon deu thieu created_at (cot NOT NULL).")
    merged: dict[str, Any] = {"hotel_id": hotel_id, "city": city_of(hotel_id), "created_at": min(created)}
    for field in (*ATTRIBUTE_FIELDS, "attributes_updated_at"):
        merged[field] = attribute.row.get(field)
    for field in (*STATUS_FIELDS, "booking_status_checked_at"):
        merged[field] = status.row.get(field)
    return MergedHotel(
        row=merged,
        attribute_source=attribute.source_code,
        status_source=status.source_code,
        source_codes=tuple(sorted(item.source_code for item in rows)),
    )
