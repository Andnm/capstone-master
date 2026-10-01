"""Canonical JSON + SHA-256 cho warehouse.

TAI DUNG `app/scraper/anomaly_registry_lib.py` (canonical_json/sha256_hex) thay vi viet lai - de
warehouse va tang operational khong bao gio lech convention serialize. Do la cung convention da
dung o `app/scraper/reference.py::room_identity_key()`:
`json.dumps(payload, sort_keys=True, separators=(",", ":"))`.

Rieng `source_manifest_sha256` co cong thuc CO DINH o muc 3a buoc 9 / muc 4: SHA-256 cua canonical
JSON danh sach nguon SAP XEP theo source_code, moi phan tu CHI gom 6 field
(source_code/source_priority/dump_sha256/dump_taken_at/schema_sha256/source_version_json) -
KHONG gom dump_path (phu thuoc may) hay bat ky timestamp materialization nao.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.scraper.anomaly_registry_lib import canonical_json, parse_iso_utc, sha256_hex

from .errors import ManifestError

__all__ = [
    "canonical_json",
    "sha256_hex",
    "file_sha256",
    "iso_utc",
    "source_manifest_payload",
    "source_manifest_sha256",
    "SOURCE_IDENTITY_FIELDS",
]

# Dung 6 field nay va khong hon - them/bot lam doi hash va pha kha nang re-verify (muc 16).
SOURCE_IDENTITY_FIELDS = (
    "source_code",
    "source_priority",
    "dump_sha256",
    "dump_taken_at",
    "schema_sha256",
    "source_version_json",
)

_FILE_CHUNK = 1024 * 1024


def file_sha256(path: str | Path) -> str:
    """SHA-256 cua file, doc theo chunk - dump co the 700+ MB, khong load het vao RAM."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_FILE_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def iso_utc(value: datetime | date | str) -> str:
    """Chuan hoa timestamp ve DUNG 1 dang chuoi 'YYYY-MM-DDTHH:MM:SSZ' truoc khi hash.

    `dump_taken_at` co the toi tu JSON manifest (chuoi, offset co the la 'Z' hoac '+07:00') hoac tu
    DB (datetime naive UTC). Chuoi luon duoc PARSE lai roi phat lai theo dang chuan, khong chi vá
    hau to - neu khong, cung mot thoi diem viet 2 kieu se cho 2 hash khac nhau va pha re-verify
    (muc 16).
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ManifestError("timestamp rong khi chuan hoa iso_utc().")
        try:
            parsed = parse_iso_utc(text)
        except (ValueError, TypeError) as exc:
            raise ManifestError(f"timestamp {text!r} khong parse duoc thanh ISO-8601 UTC: {exc}") from exc
        return parsed.replace(microsecond=0).isoformat() + "Z"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            raise ManifestError(
                f"iso_utc() chi nhan datetime naive-UTC (dung convention DB time_zone='+00:00'); "
                f"nhan duoc tz-aware: {value!r}"
            )
        return value.replace(microsecond=0).isoformat() + "Z"
    if isinstance(value, date):
        return value.isoformat()
    raise ManifestError(f"kieu khong hop le cho timestamp: {type(value)!r}")


def source_manifest_payload(sources: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Trich dung 6 field identity, sort theo source_code. Raise neu thieu field hoac trung source_code."""
    payload: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in sources:
        missing = [f for f in SOURCE_IDENTITY_FIELDS if f not in entry]
        if missing:
            raise ManifestError(
                f"nguon {entry.get('source_code')!r} thieu field bat buoc cho source_manifest_sha256: "
                f"{missing} (muc 3a buoc 9)."
            )
        code = entry["source_code"]
        if code in seen:
            raise ManifestError(f"source_code trung trong manifest: {code!r}")
        seen.add(code)
        payload.append({
            "source_code": code,
            "source_priority": int(entry["source_priority"]),
            "dump_sha256": entry["dump_sha256"],
            "dump_taken_at": iso_utc(entry["dump_taken_at"]),
            "schema_sha256": entry["schema_sha256"],
            "source_version_json": entry["source_version_json"],
        })
    if not payload:
        raise ManifestError("source manifest rong - muc 3a buoc 1 doi toi thieu 1 nguon.")
    payload.sort(key=lambda item: item["source_code"])
    return payload


def source_manifest_sha256(sources: Iterable[Mapping[str, Any]]) -> str:
    """Cong thuc muc 3a buoc 9 - dung y het khi tinh lan dau va khi re-verify (muc 16)."""
    return sha256_hex(canonical_json(source_manifest_payload(sources)))
