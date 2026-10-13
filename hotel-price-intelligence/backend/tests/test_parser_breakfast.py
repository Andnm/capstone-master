"""N1 (bữa sáng bị phủ định): hành vi `parse_room_conditions` sau bản vá, so với parser CŨ đóng băng (git blob 503db7d).

Portable: không import `outputs\\`, không nạp Settings vận hành (chỉ `app.scraper.parser/reference/transform`, đều không import `app.core`).
Nhãn kỳ vọng là LITERAL (không suy từ parser). Xem discuss/parser-n1-fix-20261013 (file 03 §2).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.scraper import parser as new
from app.scraper.reference import rate_plan_key, room_identity_key
from app.scraper.transform import build_price_observations

FIXTURES = Path(__file__).resolve().parent / "fixtures"
OLD_PATH = FIXTURES / "parser_original_503db7d.py"
OLD_SHA256 = "9ce0163e477095fb4b35b1134ee6c0855dcb9f7e4b1f4953b353799f8a2dce1d"   # = git blob 503db7d của backend/app/scraper/parser.py


def _load_old():
    # chuẩn hoá EOL trước khi băm: `core.autocrlf=true` có thể đổi LF→CRLF khi checkout, hash theo nội dung LF của git blob
    assert hashlib.sha256(OLD_PATH.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == OLD_SHA256, "parser cũ đóng băng bị sửa"
    spec = importlib.util.spec_from_file_location("parser_old_frozen", OLD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = _load_old()
NFD = lambda s: unicodedata.normalize("NFD", s)  # noqa: E731


def bf(module, lines):
    return module.parse_room_conditions(lines)["breakfast_included"]


# (dòng, kỳ vọng SAU vá, parser cũ có khác không)
SINGLE_LINE_CASES = [
    ("Bao gồm bữa sáng ngon", True, False), ("Giá bao gồm bữa sáng", True, False), ("Breakfast included", True, False),
    ("Bữa sáng Tuyệt vời - VND 544.320", False, False), ("Phí hủy: Toàn bộ tiền phòng", False, False),
    ("Không cần thanh toán trước - thanh toán tại chỗ nghỉ", False, False), ("Chúng tôi còn 2 căn", False, False),
    ("Không bao gồm bữa sáng", False, True), ("không bao gồm bữa sáng", False, True), ("KHÔNG BAO GỒM BỮA SÁNG", False, True),
    ("Không có bữa sáng", False, False), ("Không kèm bữa sáng", False, False), ("Chưa bao gồm bữa sáng", False, True),
    ("No breakfast", False, False), ("Breakfast not included", False, False),
    ("Giá không bao gồm bữa sáng", False, True), ("Không bao bữa sáng", False, True),
    ("Bao gồm bữa sáng, không bao gồm bữa tối", True, False), ("Không bao gồm bữa tối, bao gồm bữa sáng", True, False),
    ("Không bao gồm bữa sáng; bao gồm bữa tối", False, True),
    ("Bao gồm bữa sáng (trẻ em: không bao gồm bữa sáng)", True, False),          # chỉ phân biệt (a) loại cả dòng vs (b) theo từng lần khớp
    ("No breakfast included", False, True), ("Not breakfast included", False, True), ("Without breakfast included", False, True),
    ("Casino breakfast included", True, False), ("Piano breakfast included", True, False),                    # đuôi 'no' không phải từ phủ định (\b)
    ("Bữa sáng VND 100.000 (không bắt buộc)", False, False),
]


@pytest.mark.parametrize("line, expected, changes", SINGLE_LINE_CASES)
def test_single_line_behaviour(line, expected, changes):
    got_new, got_old = bf(new, [line]), bf(old, [line])
    assert got_new is expected, (line, got_new)
    assert (got_old != got_new) is changes, (line, got_old, got_new)


def test_decomposed_unicode_is_normalised_before_matching():
    for text, want in (("Không bao gồm bữa sáng", False), ("Bao gồm bữa sáng ngon", True)):
        assert NFD(text) != text
        assert bf(new, [NFD(text)]) is want


def test_empty_inputs_keep_documented_semantics():
    assert bf(new, []) is None          # không có dòng nào → None (không đổi)
    assert bf(new, [""]) is False       # chỉ dòng rỗng → False (không đổi)
    assert bf(new, ["Bữa sáng VND 100.000"]) is False   # chỉ bữa sáng trả thêm


def test_mixed_block_affirmative_line_wins():
    lines = ["Không bao gồm bữa sáng", "Bao gồm bữa sáng ngon", "Không hoàn tiền"]
    assert bf(new, lines) is True and bf(old, lines) is True


def test_other_condition_fields_never_change_on_realistic_blocks():
    blocks = [
        ["Bao gồm bữa sáng ngon", "Phí hủy: Toàn bộ tiền phòng", "Không cần thanh toán trước - thanh toán tại chỗ nghỉ", "Không cần thẻ tín dụng"],
        ["Bao gồm bữa sáng ngon", "Không hoàn tiền", "Thanh toán cho chỗ nghỉ trước khi đến", "Chúng tôi còn 2 căn"],
        ["Không bao gồm bữa sáng", "Miễn phí hủy trước 25 tháng 9, 2026", "Không cần thanh toán trước"],
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


def _fixture():
    return json.loads((FIXTURES / "n1_breakfast_lines.json").read_text(encoding="utf-8"))


def test_real_breakfast_sentences_fixture():
    data = _fixture()
    assert data["n_distinct"] == len(data["lines"]) == 66 and data["kinds"] == {"affirmative": 16, "paid_addon": 49, "negation": 1}
    wrong = [r["line"] for r in data["lines"] if bf(new, [r["line"]]) is not r["expected_breakfast_included"]]
    assert wrong == []
    # parser cũ chỉ sai đúng ở câu phủ định
    old_wrong = [r["kind"] for r in data["lines"] if bf(old, [r["line"]]) is not r["expected_breakfast_included"]]
    assert old_wrong == ["negation"]


def test_every_other_field_identical_on_fixture_lines():
    for r in _fixture()["lines"]:
        a, b = old.parse_room_conditions([r["line"]]), new.parse_room_conditions([r["line"]])
        assert {k for k in a if a[k] != b[k]} <= {"breakfast_included"}


# ---- qua transform: khóa/nhãn đổi đúng, khóa audit/identity KHÔNG đổi, grain lưu/dedup không đổi ----
POLICY = "Hủy miễn phí trước 25 tháng 10, 2026"
RATE_KEY_NO_BREAKFAST = "9669cb7822cec8aef7f11ae6bd5691d085a74c4d638b51803331338bd392b688"   # rate_plan_key(bf=False, free_cancel=True, POLICY)
RATE_KEY_BREAKFAST = "5f1b4a369aec1506d5b5ab98c54f4a332dcb846a8ffa8412cf44f7f26a73fce4"       # rate_plan_key(bf=True,  ...)


def _raw(lines_per_room):
    rooms = [{"room_type_raw": "Phòng Deluxe", "max_occupancy": 2, "bed_options": "1 giường đôi", "room_area": "20 m²",
              "price_per_night": 1_000_000.0 + i, "original_price": None, "discount_percent": None, "taxes_fees": None,
              "price_includes_tax": None, "facility_lines": lines} for i, lines in enumerate(lines_per_room)]
    return {"is_sold_out": False, "rooms": rooms, "diagnostics": {}}


def _observations(raw):
    return build_price_observations(raw, "h-test", 1, "manual", datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc), "2026-10-15", "2026-10-16", 7)


def test_transform_negated_row_gets_concrete_keys_and_keeps_audit_keys():
    neg_lines, aff_lines = ["Không bao gồm bữa sáng", POLICY], ["Bao gồm bữa sáng ngon", POLICY]
    rows = _observations(_raw([neg_lines, aff_lines]))
    assert len(rows) == 2
    r_neg, r_aff = rows
    assert r_neg["breakfast_included"] is False and r_neg["room_type_norm"].endswith("_nobf")
    assert r_aff["breakfast_included"] is True and r_aff["room_type_norm"].endswith("_bf")
    assert r_neg["rate_plan_key"] == RATE_KEY_NO_BREAKFAST and r_aff["rate_plan_key"] == RATE_KEY_BREAKFAST
    # audit/identity KHÔNG phụ thuộc kết quả parse
    assert r_neg["room_identity_key"] == r_aff["room_identity_key"] == room_identity_key(
        {"room_type_raw": "Phòng Deluxe", "max_occupancy": 2, "bed_options": "1 giường đôi", "room_area": "20 m²"})
    assert r_neg["room_option_key"] != r_aff["room_option_key"]            # facility_lines thô khác nhau
    assert r_neg["free_cancellation"] is True and r_neg["cancellation_policy"] == POLICY


def test_transform_dedup_and_saved_grain_unchanged():
    raw = _raw([["Không bao gồm bữa sáng", POLICY], ["Không bao gồm bữa sáng", POLICY]])
    raw["rooms"][1] = dict(raw["rooms"][0])                                # RAW trùng hoàn toàn → bị bỏ, đếm duplicate
    rows = _observations(raw)
    assert len(rows) == 1 and raw["diagnostics"]["duplicate_options_count"] == 1 and raw["diagnostics"]["parsed_options_count"] == 2


def test_rate_plan_key_golden_values_are_stable():
    assert rate_plan_key({"breakfast_included": False, "free_cancellation": True, "cancellation_policy": POLICY}) == RATE_KEY_NO_BREAKFAST
    assert rate_plan_key({"breakfast_included": True, "free_cancellation": True, "cancellation_policy": POLICY}) == RATE_KEY_BREAKFAST
