"""Tiện ích xử lý URL Booking.com: sinh hotel_id (slug), làm sạch link, set ngày/currency."""
import re
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from typing import Optional


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
        query = parse_qs(parsed.query)
        query['selected_currency'] = ['VND']
        query['lang'] = ['vi']
        new_query = urlencode(query, doseq=True)
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        return url


def set_checkin_checkout(url: str, checkin: str, checkout: str) -> str:
    """Ghi đè (hoặc thêm mới) checkin/checkout trên URL, luôn theo dạng YYYY-MM-DD."""
    url = re.sub(r'[&?]checkin=\d{4}-\d{2}-\d{2}', f'&checkin={checkin}', url)
    url = re.sub(r'[&?]checkout=\d{4}-\d{2}-\d{2}', f'&checkout={checkout}', url)

    if 'checkin=' not in url:
        separator = '&' if '?' in url else '?'
        url = f"{url}{separator}checkin={checkin}&checkout={checkout}"
    elif 'checkout=' not in url:
        url = f"{url}&checkout={checkout}"

    url = re.sub(r'&&+', '&', url)
    url = re.sub(r'\?&+', '?', url)
    return url
