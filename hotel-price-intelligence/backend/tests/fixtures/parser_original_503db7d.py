"""Bóc tách các trường có cấu trúc từ text tự do mà Booking.com trả về.

Data mẫu thực tế (từ crawl_info.xlsx/crawl_price.xlsx của tool cũ) cho thấy các dòng
"điều kiện phòng" bị gộp chung thành 1 blob nhiều dòng, ví dụ:
    "Bao gồm bữa sáng ngon
     Phí hủy: Toàn bộ tiền phòng
     Không cần thanh toán trước - thanh toán tại chỗ nghỉ
     Không cần thẻ tín dụng
     Có thể có giảm giá"
hoặc:
    "Bao gồm bữa sáng ngon
     Không hoàn tiền
     Thanh toán cho chỗ nghỉ trước khi đến
     Có thể có giảm giá
     Chúng tôi còn 2 căn"

Hàm ở đây nhận list các dòng riêng lẻ (room_data['facilities'] từ booking_scraper) và
tách ra thành field có cấu trúc. Đây là parser dựa trên các mẫu đã quan sát được — cần
đối chiếu lại khi có thêm dữ liệu thực tế đa dạng hơn.
"""
import re
import unicodedata
from typing import List, Optional, Dict, Any


def _nfc(text: str) -> str:
    """Selenium/Chrome đôi khi trả text tiếng Việt không đồng nhất chuẩn Unicode (dấu bị tách
    thành ký tự tổ hợp riêng thay vì gộp sẵn) dù nhìn giống hệt NFC — khiến so khớp chuỗi/regex
    sai lệch dù 2 chuỗi hiển thị giống nhau. Luôn ép về NFC trước khi so khớp bất kỳ text tiếng
    Việt nào cào được. Xác nhận bằng thực nghiệm 2026-08-01.
    """
    return unicodedata.normalize('NFC', text or '')


_BREAKFAST_RE = re.compile(
    r'bao gồm bữa sáng|bao bữa sáng|breakfast included', re.IGNORECASE
)
_FREE_CANCEL_RE = re.compile(r'miễn phí hủy|hủy miễn phí|free cancellation', re.IGNORECASE)
_NON_FREE_CANCEL_RE = re.compile(
    r'không hoàn tiền|phí hủy[:\s]|non-?refundable', re.IGNORECASE
)
_CANCELLATION_KEYWORD_RE = re.compile(r'hủy|hoàn tiền|refund|cancellation', re.IGNORECASE)
_ROOMS_LEFT_RE = re.compile(r'còn\s+(\d+)\s*(?:căn|phòng)', re.IGNORECASE)


def parse_room_conditions(facility_lines: List[str]) -> Dict[str, Any]:
    """Tách breakfast_included / free_cancellation / cancellation_policy / rooms_left
    từ list dòng text tự do cào được cho 1 room option.
    """
    result: Dict[str, Any] = {
        "breakfast_included": None,
        "free_cancellation": None,
        "cancellation_policy": None,
        "rooms_left": None,
    }
    if not facility_lines:
        return result

    cancellation_lines = []
    for line in facility_lines:
        line = _nfc(line).strip()
        if not line:
            continue

        if _BREAKFAST_RE.search(line):
            result["breakfast_included"] = True

        if _FREE_CANCEL_RE.search(line):
            result["free_cancellation"] = True
        elif _NON_FREE_CANCEL_RE.search(line):
            result["free_cancellation"] = False

        if _CANCELLATION_KEYWORD_RE.search(line):
            cancellation_lines.append(line)

        rooms_left_match = _ROOMS_LEFT_RE.search(line)
        if rooms_left_match:
            result["rooms_left"] = int(rooms_left_match.group(1))

    if result["breakfast_included"] is None:
        # Không thấy dòng nào nhắc bữa sáng -> coi như không bao gồm (không phải "không biết")
        result["breakfast_included"] = False

    if cancellation_lines:
        result["cancellation_policy"] = " | ".join(cancellation_lines)

    return result


_TIER_KEYWORDS = [
    ("suite", ["suite"]),
    ("deluxe", ["deluxe"]),
    ("superior", ["superior"]),
    ("studio", ["studio"]),
    ("family", ["family", "gia đình", "gia dinh"]),
    ("standard", ["standard", "tiêu chuẩn", "tieu chuan"]),
]


def normalize_room_type(room_type_raw: Optional[str], max_occupancy: Optional[int],
                         breakfast_included: Optional[bool]) -> str:
    """Chuẩn hoá room_type_raw về 1 nhóm so sánh được xuyên suốt các lần cào.
    Heuristic đơn giản (hạng x sức chứa x breakfast) — tinh chỉnh lại ở Phase 3 khi có
    đủ dữ liệu thực tế để thấy hết các biến thể tên phòng.
    """
    text = _nfc((room_type_raw or "").lower())

    tier = "other"
    for tier_name, keywords in _TIER_KEYWORDS:
        if any(_nfc(kw) in text for kw in keywords):
            tier = tier_name
            break

    occ_bucket = "unknown"
    if max_occupancy is not None:
        occ_bucket = "1-2p" if max_occupancy <= 2 else "3p+"

    bf_bucket = "bf" if breakfast_included else "nobf"

    return f"{tier}_{occ_bucket}_{bf_bucket}"


def infer_max_occupancy(room_type_raw: Optional[str], scraped_occupancy: Optional[int]) -> Optional[int]:
    """Ưu tiên sức chứa ghi rõ trong tên phòng khi Booking chỉ hiển thị số khách đang tìm.

    Với query cố định 2 người, Booking có thể ghi ``Số người tối đa: 2`` ngay cả cho phòng 3/4
    người. Tên phòng là tín hiệu đáng tin cậy hơn cho các trường hợp ghi rõ ``3 Người``/``4
    Người`` hoặc ``Triple``/``Quadruple``.
    """
    text = _nfc((room_type_raw or "").lower())
    explicit = re.search(r'\b([1-9])\s*(?:người|nguoi|person|people)\b', text)
    if explicit:
        return max(int(explicit.group(1)), scraped_occupancy or 0)
    if re.search(r'\btriple\b', text):
        return max(3, scraped_occupancy or 0)
    if re.search(r'\bquad(?:ruple)?\b', text):
        return max(4, scraped_occupancy or 0)
    return scraped_occupancy


def select_reference_room(rooms: List[Dict[str, Any]]) -> Optional[int]:
    """Trong danh sách room đã cào cho 1 khách sạn/1 checkin_date, chọn ra INDEX của phòng
    "chuẩn" để dùng cho time series / feature engineering (theo CLAUDE.md 4.2.c):
    rẻ nhất trong số các phòng cho 2 người trở xuống, nếu không có thì rẻ nhất trong tất cả.
    Trả về None nếu danh sách rỗng.
    """
    if not rooms:
        return None

    def price_of(r):
        return r.get("price_per_night") if r.get("price_per_night") is not None else float("inf")

    two_person_rooms = [
        (i, r) for i, r in enumerate(rooms)
        if r.get("max_occupancy") is not None and r["max_occupancy"] <= 2
    ]
    candidates = two_person_rooms if two_person_rooms else list(enumerate(rooms))
    best_index, _ = min(candidates, key=lambda pair: price_of(pair[1]))
    return best_index
