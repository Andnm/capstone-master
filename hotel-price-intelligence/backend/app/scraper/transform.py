"""Biến kết quả thô từ booking_scraper.scrape_booking_hotel() thành dữ liệu sẵn sàng ghi DB:
- 1 dict để upsert vào `hotels`
- list dict để insert vào `price_observations` (đã gồm room_type_norm, is_reference_room,
  breakfast_included, free_cancellation, cancellation_policy, rooms_left, availability_status)
"""
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.scraper.parser import (
    parse_amenity_count,
    parse_room_conditions,
    normalize_room_type,
    select_reference_room,
)
from app.scraper.url_utils import clean_hotel_link, extract_hotel_slug

_KNOWN_CITIES = {
    "Hồ Chí Minh": (
        "ho chi minh",
        "ho chi minh city",
        "hcm",
        "hcmc",
        "tp hcm",
        "tphcm",
        "sai gon",
        "saigon",
    ),
    "Hà Nội": ("ha noi", "hanoi"),
    "Đà Nẵng": ("da nang", "danang"),
    "Nha Trang": ("nha trang",),
    "Phú Quốc": ("phu quoc", "phu quoc island"),
}


def _strip_accents(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize('NFKD', text)
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return without_accents.replace("Đ", "D").replace("đ", "d")


def _normalize_city_text(text: str) -> str:
    normalized = _strip_accents(text).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", normalized).split())


def _canonical_city(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    normalized = f" {_normalize_city_text(text)} "
    for canonical, aliases in _KNOWN_CITIES.items():
        if any(f" {alias} " in normalized for alias in aliases):
            return canonical
    return None


def _guess_city(address: Optional[str], market_hint: Optional[str]) -> Optional[str]:
    return _canonical_city(market_hint) or _canonical_city(address)


def build_hotel_upsert(raw: Dict[str, Any], original_url: str, market_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    hotel_id = extract_hotel_slug(original_url)
    if not hotel_id:
        return None

    name = raw.get('hotel_name') or ''
    amenity_count = parse_amenity_count(raw.get('amenity_count_text') or '')

    return {
        'hotel_id': hotel_id,
        'name': name,
        'name_normalized': _strip_accents(name).lower().strip(),
        'hotel_link': clean_hotel_link(original_url),
        'address': raw.get('address'),
        'city': _guess_city(raw.get('address'), market_hint),
        'district': None,  # best-effort, chưa parse từ address ở bản đầu
        'latitude': raw.get('latitude'),
        'longitude': raw.get('longitude'),
        'review_score': raw.get('review_score'),
        'review_count': raw.get('review_count'),
        'amenities': raw.get('popular_facilities') or [],
        'amenity_count': amenity_count,
    }


def build_price_observations(
    raw: Dict[str, Any],
    hotel_id: str,
    crawl_run_id: int,
    crawl_trigger: str,
    observed_at: datetime,
    checkin_date: str,
    checkout_date: str,
) -> List[Dict[str, Any]]:
    """Trả về list record sẵn sàng insert vào price_observations cho 1 lần cào 1 khách sạn."""
    lead_time = (
        datetime.strptime(checkin_date, "%Y-%m-%d").date() - observed_at.date()
    ).days

    base = {
        'hotel_id': hotel_id,
        'crawl_run_id': crawl_run_id,
        'crawl_trigger': crawl_trigger,
        'observed_at': observed_at,
        'checkin_date': checkin_date,
        'checkout_date': checkout_date,
        'lead_time': lead_time,
    }

    if raw.get('is_sold_out'):
        return [{
            **base,
            'price_total': None,
            'price_per_night': None,
            'original_price': None,
            'discount_percent': None,
            'room_type_raw': None,
            'room_type_norm': None,
            'is_reference_room': True,
            'max_occupancy': None,
            'bed_config': None,
            'room_area': None,
            'breakfast_included': None,
            'free_cancellation': None,
            'cancellation_policy': None,
            'rooms_left': None,
            'is_sold_out': True,
            'availability_status': 'sold_out',
            'is_anomaly': False,
        }]

    rooms = raw.get('rooms') or []
    parsed_rooms = []
    for room in rooms:
        conditions = parse_room_conditions(room.get('facility_lines') or [])
        room_type_norm = normalize_room_type(
            room.get('room_type_raw'), room.get('max_occupancy'), conditions['breakfast_included']
        )
        parsed_rooms.append({
            **room,
            **conditions,
            'room_type_norm': room_type_norm,
        })

    reference_index = select_reference_room(parsed_rooms)

    records = []
    for i, room in enumerate(parsed_rooms):
        bed_config = room.get('bed_options') or None
        records.append({
            **base,
            'price_total': room.get('price_per_night'),   # luôn 1 đêm => total == per_night
            'price_per_night': room.get('price_per_night'),
            'original_price': room.get('original_price'),
            'discount_percent': room.get('discount_percent'),
            'room_type_raw': room.get('room_type_raw'),
            'room_type_norm': room.get('room_type_norm'),
            'is_reference_room': (i == reference_index),
            'max_occupancy': room.get('max_occupancy'),
            'bed_config': bed_config,
            'room_area': room.get('room_area'),
            'breakfast_included': room.get('breakfast_included'),
            'free_cancellation': room.get('free_cancellation'),
            'cancellation_policy': room.get('cancellation_policy'),
            'rooms_left': room.get('rooms_left'),
            'is_sold_out': False,
            'availability_status': 'available',
            'is_anomaly': False,
        })
    return records
