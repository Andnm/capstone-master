"""Buoc 14 - `curated_observation_keys` cho MOI `record_id` (muc 9) + 3 bat bien P-A (GPT file 08).

Doc `price_observations` cua warehouse bang 1 connection rieng (streaming), ghi bang connection
chinh. Observation vi pham (`CanonicalizationError`) KHONG duoc insert; gom lai roi FAIL ca buoc voi
count + mau - "bao count/sample va dung de ban, khong tu doi semantics" (GPT file 08).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Any, Callable

from .canonicalize import (
    CANONICAL_SOURCE_COLUMNS, CANONICALIZATION_VERSION, EMPTY_RATE_KEY, EMPTY_ROOM_KEY, compute_canonical_keys,
)
from .connection import warehouse_connection
from .errors import CanonicalizationError, ValidationError

_CHUNK = 5000


def build_curated_keys(wh_conn, *, created_at: dt.datetime, connect: Callable = warehouse_connection) -> dict[str, Any]:
    cursor = wh_conn.cursor()
    cursor.execute("SELECT DATABASE()")
    database = cursor.fetchone()[0]
    cursor.close()
    violations: Counter = Counter()
    samples: list[tuple[Any, str]] = []
    written = sold_out = 0
    columns = ", ".join(CANONICAL_SOURCE_COLUMNS)
    with connect(database) as reader:
        read = reader.cursor(dictionary=True)
        write = wh_conn.cursor()
        try:
            read.execute(f"SELECT {columns} FROM price_observations ORDER BY record_id")
            while True:
                rows = read.fetchmany(_CHUNK)
                if not rows:
                    break
                values = []
                for row in rows:
                    try:
                        keys = compute_canonical_keys(row)
                    except CanonicalizationError as exc:
                        violations[str(exc).split(":", 1)[-1].strip()[:80]] += 1
                        if len(samples) < 10:
                            samples.append((row["record_id"], str(exc)[:160]))
                        continue
                    sold_out += keys.is_sold_out
                    values.append((keys.record_id, keys.hotel_id, keys.checkin_date, keys.canonical_room_key,
                                   keys.canonical_rate_key, keys.canonical_series_id, created_at))
                if values:
                    write.executemany(
                        """INSERT INTO curated_observation_keys (record_id, hotel_id, checkin_date, canonical_room_key,
                             canonical_rate_key, canonical_series_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        values)
                    written += len(values)
                wh_conn.commit()
        except Exception:
            wh_conn.rollback()
            raise
        finally:
            read.close()
            write.close()
    if violations:
        raise ValidationError(
            f"canonical hoa: {sum(violations.values())} observation vi pham, KHONG insert - dung de ban. "
            f"Nhom: {dict(violations)}. Mau: {samples}"
        )
    return {"canonicalization_version": CANONICALIZATION_VERSION, "written": written, "sold_out": sold_out,
            **check_curated_invariants(wh_conn)}


def check_curated_invariants(wh_conn) -> dict[str, int]:
    """3 bat bien P-A (GPT file 08) - moi gia tri tra ve phai = 0 tru 2 count dem."""
    cursor = wh_conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT (SELECT COUNT(*) FROM price_observations) po, (SELECT COUNT(*) FROM curated_observation_keys) cok")
        counts = cursor.fetchone()
        cursor.execute("""SELECT
              SUM((room_identity_key IS NULL OR rate_plan_key IS NULL) AND is_sold_out = 0) raw_null_nhung_khong_sold_out,
              SUM(room_identity_key IS NOT NULL AND rate_plan_key IS NOT NULL AND is_sold_out = 1) sold_out_nhung_co_raw_key,
              SUM((room_identity_key IS NULL) <> (rate_plan_key IS NULL)) raw_chi_null_1_trong_2
            FROM price_observations""")
        raw = cursor.fetchone()
        cursor.execute(
            """SELECT COUNT(*) n FROM price_observations po JOIN curated_observation_keys cok ON cok.record_id = po.record_id
               WHERE (cok.canonical_room_key = %s AND cok.canonical_rate_key = %s) <> (po.is_sold_out = 1)""",
            (EMPTY_ROOM_KEY, EMPTY_RATE_KEY))
        canonical_mismatch = int(cursor.fetchone()["n"])
    finally:
        cursor.close()
    return {
        "price_observations": int(counts["po"]), "curated_observation_keys": int(counts["cok"]),
        "curated_thieu_hoac_thua": int(counts["po"]) - int(counts["cok"]),
        **{key: int(value or 0) for key, value in raw.items()},
        "canonical_rong_lech_sold_out": canonical_mismatch,
    }
