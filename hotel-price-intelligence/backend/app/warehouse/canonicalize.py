"""Canonical key cua warehouse (muc 9) - build `curated_observation_keys`, buoc 14.

Muc 9 chot: 1 batch chi co DUNG 1 `canonicalization_version`, vi `curated_observation_keys` chi co
1 dong/`record_id` nen khong the giu 2 phien ban key cho cung 1 observation. Doi thuat toan =
build warehouse moi, khong phai dataset moi.

===========================================================================================
BAY DA GAP THAT (2026-09-16, discuss file 07b) - DUNG BO `_normalize_row()`
===========================================================================================
`rate_plan_key()` hash 2 field boolean. MySQL tra `TINYINT(1)` ve Python la **int** (1/0), con
scraper luc ghi thi tinh tu **bool** (True/False):

    json.dumps(True) -> "true"      json.dumps(1) -> "1"      => SHA-256 KHAC NHAU

Hau qua do da do tren toan bo 1.049.253 dong cua local_primary:
  - khong chuan hoa kieu: `rate_plan_key` lech **100%** so voi gia tri operational
  - co chuan hoa kieu:    khop **100%** (1.038.002/1.038.002 dong co key)

`room_identity_key` khong dinh loi nay chi vi payload cua no tinh co khong co boolean nao (string +
occupancy int). Do la may man, khong phai thiet ke - nen `_normalize_row()` chuan hoa cho CA HAI de
khi ai do them field boolean vao payload thi khong tai dien.

Loi nay KHONG bi bat kip bang bat ky integrity query nao o muc 4/7/17: key van 64 hex, van
deterministic, van nhat quan trong noi bo warehouse. No chi lam `canonical_rate_key` cua warehouse
khac hoan toan `rate_plan_key` operational - pha parity test muc 10 va, neu chi MOT PHAN du lieu di
qua duong bool, se che doi `canonical_series_id` cua cung mot rate-plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.scraper.reference import rate_plan_key, room_identity_key

from .hashing import canonical_json, sha256_hex

# Phien ban thuat toan canonical hoa. TANG khi doi bat ky thu gi anh huong gia tri key -
# gom ca cach chuan hoa kieu o `_normalize_row()`, khong chi cong thuc hash.
CANONICALIZATION_VERSION = "warehouse-canon-1.0.0"

# Cac cot duoc luu TINYINT(1) trong MySQL nhung mang nghia boolean o tang Python.
_BOOLEAN_COLUMNS = ("breakfast_included", "free_cancellation", "price_includes_tax", "is_sold_out")

# Cac cot payload cua 2 ham key - liet ke tuong minh de query chi doc dung thu can.
CANONICAL_SOURCE_COLUMNS = (
    "record_id", "hotel_id", "checkin_date",
    "room_type_raw", "max_occupancy", "bed_config", "room_area",
    "breakfast_included", "free_cancellation", "cancellation_policy",
)


@dataclass(frozen=True)
class CanonicalKeys:
    record_id: int
    hotel_id: str
    checkin_date: Any
    canonical_room_key: str
    canonical_rate_key: str
    canonical_series_id: str


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Ep kieu tuong minh truoc khi hash - xem khoi canh bao o docstring module."""
    normalized = dict(row)
    for column in _BOOLEAN_COLUMNS:
        if column in normalized and normalized[column] is not None:
            normalized[column] = bool(normalized[column])
    return normalized


def canonical_series_id(hotel_id: str, checkin_date: Any, room_key: str, rate_key: str) -> str:
    """Muc 9: hash(hotel_id, checkin_date, canonical_room_key, canonical_rate_key).

    Dung canonical_json de `checkin_date` (date) duoc serialize xac dinh, khong phu thuoc `str()`
    cua tung driver.
    """
    return sha256_hex(canonical_json({
        "hotel_id": hotel_id,
        "checkin_date": checkin_date,
        "canonical_room_key": room_key,
        "canonical_rate_key": rate_key,
    }))


def compute_canonical_keys(row: Mapping[str, Any]) -> CanonicalKeys:
    """Tinh 3 key cho 1 observation. Pure - test duoc khong can MySQL."""
    normalized = _normalize_row(row)
    room_key = room_identity_key(normalized)
    rate_key = rate_plan_key(normalized)
    return CanonicalKeys(
        record_id=row["record_id"],
        hotel_id=row["hotel_id"],
        checkin_date=row["checkin_date"],
        canonical_room_key=room_key,
        canonical_rate_key=rate_key,
        canonical_series_id=canonical_series_id(
            row["hotel_id"], row["checkin_date"], room_key, rate_key
        ),
    )


def iter_canonical_keys(rows: Iterable[Mapping[str, Any]]) -> Iterable[CanonicalKeys]:
    for row in rows:
        yield compute_canonical_keys(row)


# Key cua mot observation KHONG co room identity (sentinel sold-out): payload rong -> hang so xac
# dinh. Tinh 1 lan de report co the in ra va nguoi doc khong nham no voi mot phong that.
EMPTY_ROOM_KEY = room_identity_key({})
EMPTY_RATE_KEY = rate_plan_key({})


def is_empty_room_identity(keys: CanonicalKeys) -> bool:
    return keys.canonical_room_key == EMPTY_ROOM_KEY and keys.canonical_rate_key == EMPTY_RATE_KEY
