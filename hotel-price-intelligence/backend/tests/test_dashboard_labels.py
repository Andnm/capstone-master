"""Nhãn (LABEL_POLICY v3, A8): hash khóa từng mẫu, quét Lớp 1 trên TEMPLATE, negative control, không placeholder dữ liệu động, mọi message API đến từ hằng."""
import json
import re
import string
from pathlib import Path

import pytest

from app.dashboard import labels

LOCK = Path(labels.__file__).with_name("labels.lock.json")
ALLOWED_PLACEHOLDERS = {"data_as_of", "horizon", "n", "gap", "share_pct", "lift_pct", "lo_pct", "hi_pct", "pct", "threshold", "min_n", "max_peers", "exported_at"}


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def test_every_template_hash_matches_lock_file():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    assert lock["templates"] == labels.all_hashes()                                  # sửa chữ ⇒ hash đổi ⇒ phải review & cập nhật lock


def test_layer1_no_banned_affirmative_phrases_in_any_template_except_allowlist():
    offenders = {k: labels.banned_hits(v) for k, v in labels.TEMPLATES.items() if k not in labels.NEGATION_ALLOWED and labels.banned_hits(v)}
    assert offenders == {}


@pytest.mark.parametrize("bad", ["Nên đặt ngay", "Chờ thêm sẽ rẻ hơn", "Giá chính xác 95%", "Mô hình đảm bảo không rò rỉ", "Đã triển khai", "AI dự đoán giá",
                                 "Tin cậy cao", "Độ chính xác 95%", "Dữ liệu mẫu", "Đặt ngay hôm nay", "Giá tối ưu", "Mua ngay"])
def test_negative_control_scanner_blocks_forbidden_claims(bad):
    assert labels.banned_hits(bad), bad


def test_scanner_does_not_cross_word_boundaries_and_allows_reviewed_negations():
    assert labels.banned_hits("không phải dự đoán ổn định của mô hình") == []        # "phải dự đoán" không phải "ai dự đoán"
    assert labels.banned_hits(labels.TEMPLATES["not_booking_site"]) == [] and labels.banned_hits(labels.TEMPLATES["persistence_routed"]) == []


def test_disclaimers_are_present_with_reviewed_wording():
    assert "không phải giá hiện tại" in labels.TEMPLATES["snapshot_label"].lower()
    assert "không cam kết tiết kiệm" in labels.TEMPLATES["not_booking_site"].lower() and "không phải trang đặt phòng" in labels.TEMPLATES["not_booking_site"].lower()
    assert "đã lộ" in labels.TEMPLATES["test_exposed"]


def test_no_template_has_dynamic_data_placeholders():
    for key, template in labels.TEMPLATES.items():
        assert placeholders(template) <= ALLOWED_PLACEHOLDERS, (key, placeholders(template))
        assert not re.search(r"\{(name|hotel|hotel_name|city)\}", template)


def test_render_rejects_free_text_and_wrong_types():
    assert labels.render("persistence_routed", horizon=1).startswith("Với mốc 1 ngày")
    for bad in ({"horizon": "Grand Hotel & Spa"}, {"horizon": None}, {"horizon": True}, {"horizon": ["x"]}):
        with pytest.raises((TypeError, ValueError)):
            labels.render("persistence_routed", **bad)


def test_percent_format_is_display_only():
    assert labels.fmt_pct(10.0) == "+10,0" and labels.fmt_pct(-3.04) == "-3,0" and labels.fmt_pct(0.0) == "0,0" and labels.fmt_pct(-0.04) == "0,0"
    assert labels.fmt_plain(99.4) == "99,4"
