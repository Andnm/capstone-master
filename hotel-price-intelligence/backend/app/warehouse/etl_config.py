"""Config da PIN cua 1 batch (`etl_config` + `canonicalization_config`) va hash cua chung.

Tach khoi `batch.py` de MOI lenh downstream co the re-tinh va so khop voi hash da pin trong
`etl_import_batches` TRUOC khi ghi bat ky thu gi. GPT review 12 MAJOR 1: doi
`REFERENCE_MIN_COVERAGE` trong .env roi chay rebuild reference roi se lam mot batch PASS lech khoi
chinh config no da pin, ma khong integrity query nao phat hien duoc.

KHONG import nguoc lai `reference_builder` -> `etl_config` (se thanh vong import); chieu dung la
`etl_config` doc `REFERENCE_ALGORITHM_VERSION` cua reference builder.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings

from .canonicalize import _BOOLEAN_COLUMNS, CANONICAL_SOURCE_COLUMNS, CANONICALIZATION_VERSION
from .hashing import canonical_json, sha256_hex
from .reference_builder import REFERENCE_ALGORITHM_VERSION


def etl_config() -> dict[str, Any]:
    return {
        "reference_algorithm_version": REFERENCE_ALGORITHM_VERSION,
        "reference_min_runs": settings.REFERENCE_MIN_RUNS,
        "reference_min_coverage": settings.REFERENCE_MIN_COVERAGE,
        "crawl_date_timezone": "UTC+07:00 (Asia/Ho_Chi_Minh, khong DST tu 1975)",
        "id_assignment": "explicit, ascending (source_priority, source_pk)",
    }


def canonicalization_config() -> dict[str, Any]:
    return {"version": CANONICALIZATION_VERSION, "source_columns": list(CANONICAL_SOURCE_COLUMNS),
            "boolean_columns": list(_BOOLEAN_COLUMNS), "sold_out_policy": "P-A"}


def etl_config_sha256() -> str:
    return sha256_hex(canonical_json(etl_config()))


def canonicalization_config_sha256() -> str:
    return sha256_hex(canonical_json(canonicalization_config()))
