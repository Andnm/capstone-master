"""MỌI chuỗi người dùng thấy do backend phát ra (LABEL_POLICY v3). Một module duy nhất; mỗi mẫu có ID ổn định, hash khóa trong `labels.lock.json`.

Nguyên tắc: mức diễn đạt YẾU NHẤT mà bằng chứng đỡ được. Không "chính xác", "tối ưu", "nên đặt/chờ", "đã triển khai", "không overfit/leak", "tin cậy cao/thấp".
Mẫu chỉ nội suy SỐ / NGÀY / ENUM cố định (qua `render`); tên khách sạn và dữ liệu động KHÔNG BAO GIỜ được nối vào mẫu hay bị sửa.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Mapping

TEMPLATES: dict[str, str] = {
    # --- snapshot / bằng chứng ---
    "snapshot_label": "Dữ liệu hồi cứu tới {data_as_of}. Mô hình thử nghiệm (dev). Không phải giá hiện tại.",
    "evidence_level": "Bằng chứng: mô hình thử nghiệm (dev). Chưa có hiệu chỉnh độ tin cậy cho từng dự báo.",
    "test_exposed": "Phần test đã lộ cho giai đoạn chọn mô hình; số liệu chỉ có giá trị minh họa, không phải bằng chứng độc lập.",
    "concentration": "Phần lớn cải thiện gộp tập trung ở một số khách sạn, không phân bố đều.",
    "ordinary_warning": "Trên hàng thông thường ({share_pct}% số hàng test) lift test của mô hình thuần là {lift_pct}% (CI95 theo khách sạn {lo_pct}% đến {hi_pct}%): không gọi là cải thiện ròng, phần lợi gộp đi cùng tổn thất ở hàng thông thường.",
    "ordinary_note": "Trên hàng thông thường ({share_pct}% số hàng test) lift test của mô hình thuần là {lift_pct}% (CI95 theo khách sạn {lo_pct}% đến {hi_pct}%).",
    "served_variant_note": "Bản phục vụ có routing: lift test (đã lộ) {lift_pct}% (CI95 theo khách sạn {lo_pct}% đến {hi_pct}%).",
    "cold_start_limit": "Trên tập kiểm định, với chuỗi chưa đủ lịch sử giá riêng, mức cải thiện so với giữ nguyên giá nhỏ hơn so với chuỗi có lịch sử.",
    "metadata_date_note": "Tên và điểm đánh giá khách sạn là metadata ngày xuất {exported_at}, không phải thuộc tính quan sát của dữ liệu; chỉ dùng để hiển thị và nhóm so sánh.",
    "not_booking_site": "Đây không phải trang đặt phòng và không cam kết tiết kiệm.",
    "no_soldout": "Chưa có dữ liệu hết phòng.",
    # --- cảnh báo theo dự báo ---
    "cold_start_preliminary": "Chưa đủ lịch sử giá riêng của chuỗi này — đây là đánh giá sơ bộ.",
    "persistence_routed": "Với mốc {horizon} ngày và chuỗi chưa đủ lịch sử, mô hình không được bật theo kiểm định trước; hệ thống dùng giá đã quan sát làm mốc giữ nguyên (không phải dự đoán ổn định của mô hình).",
    "metadata_missing": "Thiếu tên hoặc điểm đánh giá của khách sạn này trong metadata; hiển thị mã khách sạn.",
    # --- nhóm so sánh ---
    "group_insufficient": "Chưa đủ khách sạn tương đồng (cùng thành phố, điểm đánh giá lệch ≤ 0,5) để tổng hợp.",
    "anchor_review_missing": "Khách sạn này chưa có điểm đánh giá trong metadata nên không lập được nhóm so sánh.",
    "group_note": "Nhóm so sánh dựa trên cùng thành phố và điểm đánh giá (metadata ngày xuất, không phải thời điểm quan sát). Không bảo đảm cùng loại phòng hay điều kiện bán.",
    "group_small": "Nhóm so sánh nhỏ ({n} khách sạn); cần thận trọng khi đọc.",
    "review_gap_wide": "Mức tương đồng điểm đánh giá của nhóm thấp (chênh lớn nhất {gap}).",
    # --- classifier ---
    "classifier_note": "Phân loại thử nghiệm — chưa có claim cải thiện so với hướng suy ra từ hồi quy. Chỉ hiển thị lớp, không hiển thị xác suất.",
    "classifier_missing": "Mô hình phân loại chưa khả dụng trong môi trường này.",
    "classifier_limit_claim": "Chưa vượt rõ hướng giá suy ra từ mô hình hồi quy trên tập đánh giá; không xem đây là bằng chứng cải thiện.",
    "classifier_limit_up_recall": "Lớp tăng giá được nhận ra rất ít trên tập kiểm định (recall thấp).",
    "classifier_limit_concentration": "Lợi thế so với đường cơ sở tập trung ở một số ít khách sạn.",
    "classifier_limit_uncalibrated": "Xác suất chưa được hiệu chỉnh nên chỉ hiển thị lớp, không hiển thị xác suất.",
    # --- alert ---
    "alert_position_title": "Giá của bạn lệch so với trung vị nhóm",
    "alert_position_desc": "Giá niêm yết hiện tại lệch {pct}% so với trung vị {n} khách sạn tương đồng. Đây là luật so sánh giá đã quan sát, không phải khuyến nghị định giá.",
    "alert_market_title": "Mặt bằng giá nhóm có thể đổi",
    "alert_market_desc": "Trung vị thay đổi ước lượng của nhóm ở mốc {horizon} ngày là {pct}% (ngưỡng ±{threshold}%). Đây là ước lượng thăm dò từ mô hình thử nghiệm.",
    # --- quy tắc trong model-info ---
    "rule_direction": "Hướng giá: tăng nếu thay đổi ước lượng > +{threshold}%, giảm nếu < −{threshold}%, còn lại ít biến động (ngưỡng nghiêm ngặt, tính trên giá trị chưa làm tròn).",
    "rule_position_alert": "Cảnh báo vị thế khi |giá của bạn so với trung vị nhóm| ≥ {threshold}% (heuristic có tên, không kết luận định giá).",
    "rule_market_alert": "Cảnh báo thị trường khi |trung vị thay đổi ước lượng của nhóm| > {threshold}% ở mốc 1 hoặc 3 ngày với tối thiểu {min_n} khách sạn.",
    "rule_group": "Nhóm so sánh: cùng thành phố và cùng ngày nhận phòng, điểm đánh giá lệch ≤ 0,5 (số nguyên phần mười), tối đa {max_peers} khách sạn gần nhất, cần ≥ {min_n}; không nới dải.",
    # --- ghi chú model-info ---
    "note_meaning": "Dự báo là ước lượng thay đổi của giá niêm yết cho cùng ngày nhận phòng, quan sát sau 1 hoặc 3 ngày kể từ ngày dữ liệu; không phải giá đêm nhận phòng và không phải khuyến nghị giao dịch.",
    "note_unsupported_horizon": "Chưa có mô hình cho mốc 7 và 14 ngày.",
    "note_snapshot_scope": "Dữ liệu là bản chụp hồi cứu; không phản ánh giá sau ngày dữ liệu.",
    # --- lỗi ---
    "err_hotel_not_found": "Không tìm thấy khách sạn trong dữ liệu hiện có.",
    "err_check_in_not_supported": "Ngày nhận phòng này không có trong dữ liệu của khách sạn.",
    "err_invalid_parameter": "Tham số không hợp lệ.",
    "err_no_forecast_after_checkin": "Ngày nhận phòng không sau ngày dữ liệu nên không có dự báo.",
    "err_snapshot_unavailable": "Dữ liệu dự báo chưa sẵn sàng.",
    "err_internal": "Đã xảy ra lỗi nội bộ.",
}

# ID được phép chứa cụm phủ định của cụm bị cấm (Lớp 2). Hiện không có mẫu nào cần; thêm ID phải qua review.
NEGATION_ALLOWED: frozenset[str] = frozenset()

BANNED_PATTERNS = (
    r"chinh xac", r"do chinh xac", r"dam bao", r"toi uu", r"nen (dat|mua|cho)", r"dat ngay", r"cho them", r"mua ngay", r"nhat dinh", r"chac chan",
    r"da (trien khai|duyet|kiem chung)", r"production", r"khong (overfit|ro ri|leak)", r"tin cay (cao|thap|trung binh)", r"confidence", r"ai (du doan|biet)",
    r"du lieu mau", r"hu cau", r"mock",
)
_BANNED = re.compile(r"\b(?:" + "|".join(BANNED_PATTERNS) + r")\b")           # \b: tránh khớp xuyên ranh giới từ ("phải dự đoán" khác "ai dự đoán")


def fold(text: str) -> str:
    """Bỏ dấu + hạ chữ thường (đ -> d) để quét không phân biệt dấu/hoa thường."""
    text = text.replace("đ", "d").replace("Đ", "d")
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn").lower()


def banned_hits(text: str) -> list[str]:
    return sorted({m.group(0) for m in _BANNED.finditer(fold(text))})


def template_hash(label_id: str) -> str:
    return hashlib.sha256(TEMPLATES[label_id].encode("utf-8")).hexdigest()


def all_hashes() -> dict[str, str]:
    return {k: template_hash(k) for k in sorted(TEMPLATES)}


def render(label_id: str, **values: object) -> str:
    """Nội suy chỉ giá trị số/ngày/enum cố định. Từ chối chuỗi tự do (tên khách sạn...) để dữ liệu động không lọt vào mẫu."""
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError(f"{label_id}.{key}: kiểu không hợp lệ {type(value).__name__}")
        if isinstance(value, str) and not re.fullmatch(r"[0-9A-Za-z_.:+\-−, /%≤≥—]*", value):
            raise ValueError(f"{label_id}.{key}: chuỗi tự do không được nội suy: {value!r}")
    return TEMPLATES[label_id].format(**values)


def fmt_pct(value: float) -> str:
    """Điểm phần trăm hiển thị: 1 chữ số thập phân, dấu phẩy thập phân, dấu + khi dương (chỉ để hiển thị; hướng do API quyết định trước đó)."""
    text = f"{value:+.1f}".replace(".", ",")
    return text if text != "-0,0" and text != "+0,0" else "0,0"


def fmt_plain(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def messages_for_scan() -> Mapping[str, str]:
    return dict(TEMPLATES)
