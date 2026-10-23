"""Phần thuần của xuất metadata hotel (không DB): số nguyên phần mười, checksum, không ghi đè, SELECT cố định chỉ-đọc."""
import json
from datetime import datetime
from decimal import Decimal

import pytest

from app.ml_serving import hotel_meta as M


def rows():
    return [("a-hotel", "A Hotel", "Hà Nội", Decimal("8.3")), ("b-hotel", "B", "Đà Lạt", Decimal("7.8")), ("c-hotel", "C", "Vũng Tàu", None)]


def test_payload_tenths_checksum_and_null_review():
    payload = M.build_payload(rows(), datetime(2026, 10, 22, 17, 51, 57))
    assert [h["reviewScoreTenths"] for h in payload["hotels"]] == [83, 78, None] and payload["rowCount"] == 3
    assert payload["exportedAtUtc"] == "2026-10-22T17:51:57Z" and len(payload["hotelsSha256"]) == 64
    assert abs(8.3 - 7.8) > 0.5 and abs(83 - 78) <= 5                               # lý do dùng số nguyên


def test_checksum_changes_with_content_and_is_order_insensitive_to_keys():
    a = M.build_payload(rows(), datetime(2026, 1, 1))
    b = M.build_payload(rows()[:2] + [("c-hotel", "C", "Vũng Tàu", Decimal("5.0"))], datetime(2026, 1, 1))
    assert a["hotelsSha256"] != b["hotelsSha256"]


@pytest.mark.parametrize("bad", [Decimal("8.35"), Decimal("10.1"), Decimal("-0.1")])
def test_precision_and_range_violations_stop(bad):
    with pytest.raises(ValueError):
        M.build_payload([("x", "X", "Hà Nội", bad)], datetime(2026, 1, 1))


def test_duplicate_hotel_id_stops():
    with pytest.raises(ValueError, match="trùng"):
        M.build_payload([("x", "X", "Hà Nội", None), ("x", "Y", "Hà Nội", None)], datetime(2026, 1, 1))


def test_write_refuses_overwrite(tmp_path):
    payload = M.build_payload(rows(), datetime(2026, 1, 1))
    path = tmp_path / "meta.json"
    M.write_payload(path, payload)
    assert json.loads(path.read_text(encoding="utf-8"))["hotelsSha256"] == payload["hotelsSha256"]
    with pytest.raises(FileExistsError):
        M.write_payload(path, payload)


def test_select_is_single_readonly_statement():
    assert M.SELECT.lower().startswith("select ") and ";" not in M.SELECT and "insert" not in M.SELECT.lower() and "update" not in M.SELECT.lower()
