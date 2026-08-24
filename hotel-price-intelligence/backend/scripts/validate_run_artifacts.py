"""Đối chiếu dữ liệu DB với DOM artifact được lưu đúng thời điểm crawl."""
import argparse
import gzip
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup

from app.database.repositories import CrawlRunItemRepository


def _text(value) -> str:
    return " ".join(unicodedata.normalize("NFC", str(value or "")).split())


def _price_text(value) -> str:
    return f"{int(round(float(value))):,}".replace(",", ".")


def _room_name(header) -> str:
    if not header:
        return ""
    for selector in (
        ".hprt-roomtype-link", "[data-room-name]", ".hprt-roomtype-icon-link",
        ".hprt-roomtype", "h3", "a",
    ):
        node = header.select_one(selector)
        if node and _text(node.get_text(" ", strip=True)):
            return _text(node.get_text(" ", strip=True))
    return _text(header.get_text(" ", strip=True))


def validate_item(item: dict) -> dict:
    mismatches = []
    artifact_path = Path(item.get("artifact_html_path") or "")
    if not artifact_path.is_file():
        return {"item_id": item["id"], "error": "missing_artifact", "mismatches": []}

    with gzip.open(artifact_path, "rt", encoding="utf-8") as source:
        html = source.read()
    soup = BeautifulSoup(html, "html.parser")
    page_text = _text(soup.get_text(" ", strip=True))
    dom_rows = soup.select("tr.js-rt-block-row")
    if not dom_rows:
        dom_rows = soup.select("table#hprt-table tbody tr, .hprt-table tbody tr")
    # Booking chèn một placeholder `.hprt-cheapest-block-row` rỗng sau khi hydrate.
    # Nó không phải rate candidate và Selenium parser cũng không nhìn thấy nó tại snapshot ổn định.
    dom_rows = [row for row in dom_rows if _text(row.get_text(" ", strip=True))]

    rooms = sorted(item.get("rooms") or [], key=lambda room: room["room_option_index"])
    if len(dom_rows) != item.get("dom_room_row_count"):
        mismatches.append({"field": "dom_room_row_count", "db": item.get("dom_room_row_count"), "dom": len(dom_rows)})
    if len(dom_rows) != len(rooms):
        mismatches.append({"field": "saved_room_count", "db": len(rooms), "dom": len(dom_rows)})

    query = parse_qs(urlparse(item.get("hotel_link") or "").query)
    for field, expected in (("checkin", str(item["checkin_date"])), ("checkout", str(item["checkout_date"]))):
        if query.get(field) != [expected]:
            mismatches.append({"field": f"url_{field}", "db": query.get(field), "expected": expected})

    for field in ("hotel_name", "hotel_address"):
        expected = _text(item.get(field))
        if expected and expected not in page_text:
            mismatches.append({"field": field, "db": expected, "dom": "not_found"})

    current_name = ""
    current_room_text = ""
    checked_fields = 0
    for index, (dom_row, room) in enumerate(zip(dom_rows, rooms)):
        header = dom_row.select_one("th.hprt-table-cell-roomtype")
        if header:
            current_name = _room_name(header)
            current_room_text = _text(header.get_text(" ", strip=True))
        row_text = _text(dom_row.get_text(" ", strip=True))

        checks = {
            "room_type_raw": current_name,
            "price_per_night": _price_text(room["price_per_night"]) if room.get("price_per_night") is not None else "",
            "original_price": _price_text(room["original_price"]) if room.get("original_price") is not None else "",
            "bed_config": _text(room.get("bed_config")),
            "room_area": _text(room.get("room_area")),
            "cancellation_policy": _text(room.get("cancellation_policy")),
        }
        for field, expected in checks.items():
            if expected:
                checked_fields += 1
                actual_scope = (
                    current_name if field == "room_type_raw"
                    else current_room_text if field in ("bed_config", "room_area")
                    else row_text
                )
                if expected not in actual_scope:
                    mismatches.append({
                        "option": index, "field": field, "db": expected,
                        "dom": actual_scope[:240],
                    })

        if room.get("discount_percent") is not None:
            checked_fields += 1
            expected = str(int(round(float(room["discount_percent"])))) + "%"
            if expected not in row_text:
                mismatches.append({"option": index, "field": "discount_percent", "db": expected, "dom": "not_found"})
        if room.get("price_includes_tax"):
            checked_fields += 1
            if "Đã bao gồm thuế và phí" not in row_text:
                mismatches.append({"option": index, "field": "price_includes_tax", "db": True, "dom": "not_found"})
        if room.get("breakfast_included"):
            checked_fields += 1
            if not re.search(r"bữa sáng", row_text, flags=re.IGNORECASE):
                mismatches.append({"option": index, "field": "breakfast_included", "db": True, "dom": "not_found"})
        if room.get("free_cancellation"):
            checked_fields += 1
            if "Hủy miễn phí" not in row_text:
                mismatches.append({"option": index, "field": "free_cancellation", "db": True, "dom": "not_found"})
        if room.get("rooms_left") is not None:
            checked_fields += 1
            expected = str(room["rooms_left"])
            if not re.search(rf"còn\s+{re.escape(expected)}\s+(?:căn|phòng)", row_text, flags=re.IGNORECASE):
                mismatches.append({"option": index, "field": "rooms_left", "db": room["rooms_left"], "dom": "not_found"})

    return {
        "item_id": item["id"],
        "hotel": item.get("hotel_name") or item.get("hotel_name_hint"),
        "checkin": str(item["checkin_date"]),
        "dom_rows": len(dom_rows),
        "db_rows": len(rooms),
        "checked_fields": checked_fields,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=int)
    args = parser.parse_args()
    reports = [validate_item(item) for item in CrawlRunItemRepository().list_by_run(args.run_id)]
    summary = {
        "run_id": args.run_id,
        "items": len(reports),
        "dom_rows": sum(report.get("dom_rows", 0) for report in reports),
        "db_rows": sum(report.get("db_rows", 0) for report in reports),
        "checked_fields": sum(report.get("checked_fields", 0) for report in reports),
        "mismatch_count": sum(report.get("mismatch_count", 0) for report in reports),
        "reports": reports,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(1 if summary["mismatch_count"] else 0)


if __name__ == "__main__":
    main()
