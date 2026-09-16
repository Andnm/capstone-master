"""Canonical key cua warehouse (muc 9) - build `curated_observation_keys`, buoc 14.

Muc 9 chot: 1 batch chi co DUNG 1 `canonicalization_version`, vi `curated_observation_keys` chi co
1 dong/`record_id` nen khong the giu 2 phien ban key cho cung 1 observation. Doi thuat toan =
build warehouse moi, khong phai dataset moi.

===========================================================================================
BAY DA GAP THAT (2026-09-16, discuss file 07b) - DUNG BO `_normalize_row()`
===========================================================================================
`rate_plan_key()` hash 2 field boolean. MySQL tra `TINYINT(1)` ve Python la **int** (1/0), con
scraper luc ghi thi tinh tu **bool** (True/False): `json.dumps(True)="true"` != `json.dumps(1)="1"`.
Do tren 1.049.253 dong local_primary: khong chuan hoa -> `rate_plan_key` lech 100% so voi gia tri
operational; co chuan hoa -> khop 100%. Loi nay qua duoc MOI integrity query (key van 64 hex, van
deterministic) nen chi bat duoc bang cach so voi key da luu tren du lieu that.

Chuan hoa la FAIL-CLOSED (GPT review 08): chi nhan {0, 1, True, False, None}. `bool(2)` hay
`bool("0")` deu la True trong Python - am tham bien du lieu hong thanh "co breakfast".

===========================================================================================
SOLD-OUT - phuong an P-A (GPT chot o file 08, theo input cua user o file 07d)
===========================================================================================
Moi observation, ke ca sold-out, deu co 1 dong trong `curated_observation_keys` - de EDA/feature
phan biet duoc "het inventory" (tin hieu nhu cau) voi "khong cao duoc" (thieu du lieu). Quyet dinh
dua TUONG MINH vao `is_sold_out`, khong suy tu payload:
- `is_sold_out=TRUE`  -> ep `EMPTY_ROOM_KEY`/`EMPTY_RATE_KEY` bat ke payload con sot gi.
- `is_sold_out=FALSE` -> tinh key thuong; neu KHONG co room identity (payload phong rong) thi FAIL -
  nhieu kha nang loi parser, khong duoc de no trong giong sold-out.
Model hoi quy GIA van khong nhan sold-out lam sample/nhan (tang 2 eligibility, muc 12).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.scraper.reference import rate_plan_key, room_identity_key

from .errors import CanonicalizationError
from .hashing import canonical_json, sha256_hex

# TANG khi doi bat ky thu gi anh huong gia tri key - ke ca cach chuan hoa kieu, khong chi cong thuc.
CANONICALIZATION_VERSION = "warehouse-canon-1.1.0"

# Cot TINYINT(1) mang nghia boolean o tang Python.
_BOOLEAN_COLUMNS = ("breakfast_included", "free_cancellation", "price_includes_tax", "is_sold_out")

# Cot can doc cho canonical hoa - liet ke tuong minh, ke ca `is_sold_out` (quyet dinh sentinel).
CANONICAL_SOURCE_COLUMNS = (
    "record_id", "hotel_id", "checkin_date", "is_sold_out",
    "room_type_raw", "max_occupancy", "bed_config", "room_area",
    "breakfast_included", "free_cancellation", "cancellation_policy",
)

# Key cua observation khong co room identity: payload rong -> hang so xac dinh, in duoc ra report.
EMPTY_ROOM_KEY = room_identity_key({})
EMPTY_RATE_KEY = rate_plan_key({})


@dataclass(frozen=True)
class CanonicalKeys:
    record_id: int
    hotel_id: str
    checkin_date: Any
    canonical_room_key: str
    canonical_rate_key: str
    canonical_series_id: str
    is_sold_out: bool


def _strict_bool(value: Any, column: str, record_id: Any) -> bool | None:
    if value is None or value is True or value is False:
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise CanonicalizationError(
        f"record {record_id}: {column}={value!r} ({type(value).__name__}) khong thuoc "
        f"{{0, 1, True, False, None}} - FAIL closed thay vi bool() am tham."
    )


def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    record_id = row.get("record_id")
    for column in _BOOLEAN_COLUMNS:
        if column in normalized:
            normalized[column] = _strict_bool(normalized[column], column, record_id)
    return normalized


def canonical_series_id(hotel_id: str, checkin_date: Any, room_key: str, rate_key: str) -> str:
    """Muc 9: hash(hotel_id, checkin_date, canonical_room_key, canonical_rate_key)."""
    return sha256_hex(canonical_json({
        "hotel_id": hotel_id,
        "checkin_date": checkin_date,
        "canonical_room_key": room_key,
        "canonical_rate_key": rate_key,
    }))


def compute_canonical_keys(row: Mapping[str, Any]) -> CanonicalKeys:
    """Tinh 3 key cho 1 observation. Pure. Raise `CanonicalizationError` khi khong an toan."""
    normalized = _normalize_row(row)
    record_id = row.get("record_id")
    sold_out = normalized.get("is_sold_out")
    if sold_out is None:
        raise CanonicalizationError(
            f"record {record_id}: thieu is_sold_out (cot NOT NULL) - khong quyet dinh duoc sentinel."
        )
    if sold_out:
        room_key, rate_key = EMPTY_ROOM_KEY, EMPTY_RATE_KEY
    else:
        room_key = room_identity_key(normalized)
        rate_key = rate_plan_key(normalized)
        if room_key == EMPTY_ROOM_KEY:
            raise CanonicalizationError(
                f"record {record_id}: is_sold_out=FALSE nhung KHONG co room identity (ten/suc chua/"
                f"giuong/dien tich deu rong) - nghi loi parser, khong duoc de trong giong sold-out."
            )
    return CanonicalKeys(
        record_id=record_id,
        hotel_id=row["hotel_id"],
        checkin_date=row["checkin_date"],
        canonical_room_key=room_key,
        canonical_rate_key=rate_key,
        canonical_series_id=canonical_series_id(row["hotel_id"], row["checkin_date"], room_key, rate_key),
        is_sold_out=bool(sold_out),
    )


def iter_canonical_keys(rows: Iterable[Mapping[str, Any]]) -> Iterable[CanonicalKeys]:
    for row in rows:
        yield compute_canonical_keys(row)


def is_empty_room_identity(keys: CanonicalKeys) -> bool:
    return keys.canonical_room_key == EMPTY_ROOM_KEY and keys.canonical_rate_key == EMPTY_RATE_KEY
