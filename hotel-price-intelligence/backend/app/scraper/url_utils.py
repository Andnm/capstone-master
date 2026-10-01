"""Tiện ích xử lý URL Booking.com và chuẩn hoá cùng một ngữ cảnh tìm kiếm."""
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Optional


_SCRAPE_QUERY = {
    "group_adults": "2",
    "group_children": "0",
    "no_rooms": "1",
    "req_adults": "2",
    "req_children": "0",
    "room1": "A,A",
    "selected_currency": "VND",
    "lang": "vi",
}


def extract_hotel_slug(url: str) -> Optional[str]:
    """Lấy slug ổn định từ URL Booking, dùng làm hotel_id.
    vd: https://www.booking.com/hotel/vn/serenity-near-tan-son-nhat-airport.vi.html?...
        -> "serenity-near-tan-son-nhat-airport"
    Không dùng regex đoán locale (dễ sai) — lấy segment path cuối, cắt tại dấu '.' đầu tiên.
    """
    try:
        path = urlparse(str(url)).path
        parts = [p for p in path.split('/') if p]
        if len(parts) < 2 or parts[0].lower() != 'hotel':
            return None
        last_segment = parts[-1]  # vd "serenity-near-tan-son-nhat-airport.vi.html"
        slug = last_segment.split('.')[0].strip().lower()
        return slug or None
    except Exception:
        return None


def clean_hotel_link(url: str) -> str:
    """Bỏ toàn bộ query string (tracking params: label, sid, aid, srpvid...), giữ lại URL gốc."""
    try:
        p = urlparse(str(url))
        return urlunparse((p.scheme, p.netloc, p.path, '', '', ''))
    except Exception:
        return str(url)


def force_vnd_currency(url: str) -> str:
    try:
        parsed = urlparse(str(url))
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query['selected_currency'] = 'VND'
        query['lang'] = 'vi'
        new_query = urlencode(query)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def set_checkin_checkout(url: str, checkin: str, checkout: str) -> str:
    """Ghi đè ngày bằng parser URL, kể cả khi ``checkin`` là query param đầu tiên."""
    try:
        parsed = urlparse(str(url))
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query['checkin'] = checkin
        query['checkout'] = checkout
        return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        return url


def build_scrape_url(url: str, checkin: str, checkout: str) -> str:
    """Tạo URL Selenium thực sự mở với search context cố định cho toàn bộ dataset.

    Mọi khách sạn đều được hỏi cho 2 người lớn, 0 trẻ em, 1 phòng, 1 đêm, tiếng Việt và VND.
    Chỉ giữ canonical hotel path và các tham số thực sự mô tả crawl context. Tracking/session cũ
    (``label``, ``sid``, ``srpvid``, block id...) có thể làm Booking canonicalize về URL không query,
    khiến ngày checkin biến mất dù URL nguồn nhìn có vẻ hợp lệ.
    """
    dated_url = set_checkin_checkout(url, checkin, checkout)
    try:
        parsed = urlparse(clean_hotel_link(dated_url))
        query = dict(_SCRAPE_QUERY)
        query['checkin'] = checkin
        query['checkout'] = checkout
        return urlunparse(parsed._replace(query=urlencode(query)))
    except Exception:
        return force_vnd_currency(dated_url)
