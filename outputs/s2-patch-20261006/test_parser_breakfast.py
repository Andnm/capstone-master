"""Test patch N1 (S2/P1) cho `parse_room_conditions`: so parser GOC (frozen 503db7d = ban live luc viet) voi `parser_patched.py` tren cung mot corpus dong.

Khong dong vao code live; chay: `python -m pytest outputs/s2-patch-20261006/test_parser_breakfast.py -q` (bat ky moi truong Python co pytest).
Khi GPT PASS patch, ap vao `backend/app/scraper/parser.py` bang `parser_n1.patch` va chuyen file test nay vao `backend/tests/`.
"""
from __future__ import annotations

import importlib.util
import unicodedata
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LIVE = HERE / "parser_original_503db7d.py"   # ban parser HIEN HANH tai commit 503db7d, dong bang de test on dinh sau khi ap patch


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = _load(LIVE, "parser_live")
new = _load(HERE / "parser_patched.py", "parser_patched")

# (dong, breakfast_included mong doi SAU patch, co phai ca phai doi so voi parser cu)
CASES = [
    ("Bao gồm bữa sáng ngon", True, False),
    ("Giá bao gồm bữa sáng", True, False),
    ("Breakfast included", True, False),
    ("Bữa sáng Tuyệt vời - VND 544.320", False, False),      # bua sang tra them
    ("Phí hủy: Toàn bộ tiền phòng", False, False),
    ("Không cần thanh toán trước - thanh toán tại chỗ nghỉ", False, False),
    ("Chúng tôi còn 2 căn", False, False),
    ("Không bao gồm bữa sáng", False, True),                   # N1: parser cu ghi True
    ("không bao gồm bữa sáng", False, True),
    ("KHÔNG BAO GỒM BỮA SÁNG", False, True),
    ("Không có bữa sáng", False, False),                        # parser cu da False (khong khop 'bao gom')
    ("Không kèm bữa sáng", False, False),
    ("Chưa bao gồm bữa sáng", False, True),
    ("No breakfast", False, False),
    ("Breakfast not included", False, False),
]


@pytest.mark.parametrize("line, expected, changes", CASES)
def test_single_line_behaviour(line, expected, changes):
    got_old = old.parse_room_conditions([line])["breakfast_included"]
    got_new = new.parse_room_conditions([line])["breakfast_included"]
    assert got_new is expected, (line, got_new)
    assert (got_old != got_new) is changes, (line, got_old, got_new)


def test_decomposed_unicode_is_normalised_before_matching():
    decomposed = unicodedata.normalize("NFD", "Không bao gồm bữa sáng")
    assert decomposed != "Không bao gồm bữa sáng"
    assert new.parse_room_conditions([decomposed])["breakfast_included"] is False
    assert old.parse_room_conditions([decomposed])["breakfast_included"] is True   # bug cu cung xuat hien o dang NFD


def test_mixed_block_keeps_affirmative_line_wins():
    """Mot khoi co CA dong khang dinh va dong phu dinh (nhu hotel in ca hai kieu) van True vi co dong khang dinh hop le."""
    lines = ["Không bao gồm bữa sáng", "Bao gồm bữa sáng ngon", "Không hoàn tiền"]
    assert new.parse_room_conditions(lines)["breakfast_included"] is True
    assert old.parse_room_conditions(lines)["breakfast_included"] is True


def test_only_breakfast_changes_on_realistic_blocks():
    """Parity: tren cac khoi dieu kien thuc te (docstring parser.py + khoi Starview), CHI `breakfast_included` duoc phep doi, va chi khi khoi
    khong co dong khang dinh hop le."""
    blocks = [
        ["Bao gồm bữa sáng ngon", "Phí hủy: Toàn bộ tiền phòng", "Không cần thanh toán trước - thanh toán tại chỗ nghỉ",
         "Không cần thẻ tín dụng", "Có thể có giảm giá"],
        ["Bao gồm bữa sáng ngon", "Không hoàn tiền", "Thanh toán cho chỗ nghỉ trước khi đến", "Có thể có giảm giá", "Chúng tôi còn 2 căn"],
        ["Không bao gồm bữa sáng", "Miễn phí hủy trước 25 tháng 9, 2026", "Không cần thanh toán trước"],             # Starview-like
        ["Không bao gồm bữa sáng", "Không hoàn tiền", "Chúng tôi còn 1 căn"],
        ["Bữa sáng Tuyệt vời - VND 544.320", "Miễn phí hủy", "Chúng tôi còn 3 phòng"],
        [], [""],
    ]
    changed = []
    for block in blocks:
        a, b = old.parse_room_conditions(block), new.parse_room_conditions(block)
        diff = {k for k in a if a[k] != b[k]}
        assert diff <= {"breakfast_included"}, (block, diff)
        if diff:
            changed.append(block)
            assert a["breakfast_included"] is True and b["breakfast_included"] is False
    assert changed == [blocks[2], blocks[3]]


def test_normalize_room_type_bucket_follows_corrected_flag():
    """Hệ quả đã biết: room_type_norm (bucket bf/nobf) và rate_plan_key đổi cho hotel bị N1 - đây là ranh giới regime, không phải lỗi patch."""
    line = ["Không bao gồm bữa sáng"]
    assert old.normalize_room_type("Phòng Deluxe", 2, old.parse_room_conditions(line)["breakfast_included"]).endswith("_bf")
    assert new.normalize_room_type("Phòng Deluxe", 2, new.parse_room_conditions(line)["breakfast_included"]).endswith("_nobf")
