"""Biến kết quả thô từ booking_scraper.scrape_booking_hotel() thành dữ liệu sẵn sàng ghi DB:
- 1 dict để upsert vào `hotels`
- list dict để insert vào `price_observations` (đã gồm room_type_norm, is_reference_room,
  breakfast_included, free_cancellation, cancellation_policy, rooms_left, availability_status)
"""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.scraper.parser import (
    infer_max_occupancy,
    parse_room_conditions,
    normalize_room_type,
)
from app.scraper.url_utils import clean_hotel_link, extract_hotel_slug
from app.scraper.reference import rate_plan_key, room_identity_key

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
    "Vũng Tàu": ("vung tau", "vungtau", "ba ria vung tau"),
    "Đà Lạt": ("da lat", "dalat"),
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
    return {
        'hotel_id': hotel_id,
        'name': name,
        'name_normalized': _strip_accents(name).lower().strip(),
        'hotel_link': clean_hotel_link(original_url),
        'address': raw.get('address'),
        'city': _guess_city(raw.get('address'), market_hint),
        'review_score': raw.get('review_score'),
        'review_count': raw.get('review_count'),
        'amenities': raw.get('popular_facilities') or [],
    }


def _room_option_key(room: Dict[str, Any]) -> str:
    """Fingerprint audit-friendly cho một rate option, không dùng làm nhóm phòng chuẩn hoá."""
    payload = {
        'room_type_raw': room.get('room_type_raw'),
        'price_per_night': room.get('price_per_night'),
        'original_price': room.get('original_price'),
        'bed_options': room.get('bed_options'),
        'room_area': room.get('room_area'),
        'facility_lines': room.get('facility_lines') or [],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def build_price_observations(
    raw: Dict[str, Any],
    hotel_id: str,
    crawl_run_id: int,
    crawl_trigger: str,
    observed_at: datetime,
    checkin_date: str,
    checkout_date: str,
    crawl_run_item_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Trả về list record sẵn sàng insert vào price_observations cho 1 lần cào 1 khách sạn."""
    observed_utc = observed_at.replace(tzinfo=timezone.utc) if observed_at.tzinfo is None else observed_at.astimezone(timezone.utc)
    observed_local_date = observed_utc.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    lead_time = (datetime.strptime(checkin_date, "%Y-%m-%d").date() - observed_local_date).days

    base = {
        'hotel_id': hotel_id,
        'crawl_run_id': crawl_run_id,
        'crawl_run_item_id': crawl_run_item_id,
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
            'taxes_fees': None,
            'price_includes_tax': None,
            'room_type_raw': None,
            'room_type_norm': None,
            'room_option_index': 0,
            'room_option_key': 'sold_out',
            'is_reference_room': False,
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
        max_occupancy = infer_max_occupancy(
            room.get('room_type_raw'), room.get('max_occupancy')
        )
        room_type_norm = normalize_room_type(
            room.get('room_type_raw'), max_occupancy, conditions['breakfast_included']
        )
        parsed_rooms.append({
            **room,
            **conditions,
            'max_occupancy': max_occupancy,
            'room_type_norm': room_type_norm,
            'room_option_key': _room_option_key(room),
        })

    for room in parsed_rooms:
        room['room_identity_key'] = room_identity_key(room)
        room['rate_plan_key'] = rate_plan_key(room)

    records = []
    for i, room in enumerate(parsed_rooms):
        bed_config = room.get('bed_options') or None
        records.append({
            **base,
            'price_total': room.get('price_per_night'),   # luôn 1 đêm => total == per_night
            'price_per_night': room.get('price_per_night'),
            'original_price': room.get('original_price'),
            'discount_percent': room.get('discount_percent'),
            'taxes_fees': room.get('taxes_fees'),
            'price_includes_tax': room.get('price_includes_tax'),
            'room_type_raw': room.get('room_type_raw'),
            'room_type_norm': room.get('room_type_norm'),
            'room_option_index': i,
            'room_option_key': room.get('room_option_key'),
            'room_identity_key': room.get('room_identity_key'),
            'rate_plan_key': room.get('rate_plan_key'),
            'is_reference_room': False,
            'reference_definition_id': None,
            'reference_match_status': 'calibrating',
            'reference_match_score': None,
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
