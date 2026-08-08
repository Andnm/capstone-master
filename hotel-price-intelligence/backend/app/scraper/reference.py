"""Stable room identity, rate-plan signature và auto-calibration reference."""
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, Optional, Tuple


_ALIASES = {
    "giuong doi": "double",
    "double bed": "double",
    "giuong don": "single",
    "single bed": "single",
    "giuong co king": "king",
    "giuong king": "king",
    "king bed": "king",
    "ban cong": "balcony",
    "huong bien": "sea view",
    "nhin ra bien": "sea view",
    "huong thanh pho": "city view",
    "nhin ra thanh pho": "city view",
    "phong": "room",
}


def canonical_text(value: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    text = " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
    for source, target in _ALIASES.items():
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    return " ".join(text.split())


def normalize_area(value: Optional[str]) -> Optional[str]:
    match = re.search(r"(\d+)\s*(m|ft)", canonical_text(value))
    return f"{match.group(1)}{match.group(2)}" if match else None


def room_identity_payload(room: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": canonical_text(room.get("room_type_raw")),
        "occupancy": room.get("max_occupancy"),
        "bed": canonical_text(room.get("bed_config") or room.get("bed_options")),
        "area": normalize_area(room.get("room_area")),
    }


def room_identity_key(room: Dict[str, Any]) -> str:
    payload = json.dumps(room_identity_payload(room), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rate_plan_key(room: Dict[str, Any]) -> str:
    payload = {
        "breakfast": room.get("breakfast_included"),
        "free_cancellation": room.get("free_cancellation"),
        "cancellation_policy": canonical_text(room.get("cancellation_policy")),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def token_similarity(left: Optional[str], right: Optional[str]) -> float:
    a, b = set(canonical_text(left).split()), set(canonical_text(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_reference(room: Dict[str, Any], reference: Dict[str, Any]) -> Tuple[str, float]:
    if (
        room.get("room_identity_key") == reference.get("room_identity_key")
        and room.get("rate_plan_key") == reference.get("rate_plan_key")
    ):
        return "exact", 1.0

    same_rate = room.get("rate_plan_key") == reference.get("rate_plan_key")
    same_occ = room.get("max_occupancy") == reference.get("max_occupancy")
    same_area = normalize_area(room.get("room_area")) == normalize_area(reference.get("room_area"))
    name_score = token_similarity(room.get("room_type_raw"), reference.get("room_type_anchor_raw"))
    score = 0.65 * name_score + 0.15 * float(same_occ) + 0.10 * float(same_area) + 0.10 * float(same_rate)
    if same_rate and score >= 0.80:
        return "alias", round(score, 4)
    return "not_reference", round(score, 4)


def select_best_match(rooms: Iterable[Dict[str, Any]], reference: Dict[str, Any]):
    matches = []
    for index, room in enumerate(rooms):
        status, score = match_reference(room, reference)
        if status in ("exact", "alias"):
            matches.append((index, status, score))
    if not matches:
        return None, "unavailable", 0.0
    matches.sort(key=lambda value: value[2], reverse=True)
    if len(matches) > 1 and matches[0][2] == matches[1][2]:
        return None, "ambiguous", matches[0][2]
    return matches[0]
