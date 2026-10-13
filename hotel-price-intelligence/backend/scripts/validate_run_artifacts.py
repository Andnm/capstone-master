"""Đối chiếu dữ liệu DB với DOM artifact được lưu đúng thời điểm crawl.

Fail-closed (N1, 13/10/2026, rev2 sau GPT file 04):
  * tập item mong đợi lấy ĐỘC LẬP (truy vấn riêng + `crawl_runs.total`), kiểm khớp cả tập lẫn số lần xuất hiện (thiếu/thừa/trùng ID => lỗi);
  * artifact thiếu/hỏng, dữ liệu sai cấu trúc, ngoại lệ, status lạ, item chưa terminal (queued/running) => không bao giờ "0 mismatch";
  * `breakfast_included` kiểm HAI CHIỀU trên phạm vi điều kiện của từng dòng (cùng bộ selector với scraper), bằng oracle scanner literal
    ĐỘC LẬP với `parser.py` nhưng theo ĐÚNG hợp đồng đã khóa của parser (cụm khẳng định chính xác, từ phủ định ngay trước, biên từ);
  * DB NULL chỉ hợp lệ khi điều kiện DOM thực sự rỗng (và không có `rooms_left`); khi đó đếm riêng, KHÔNG tính là đã xác nhận;
  * không có cơ chế "allow skipped": item không ánh xạ được dòng-theo-dòng => exit khác 0 (bằng chứng thay thế do bên gọi đánh giá riêng).
"""
import argparse
import gzip
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup

# Bản sao có kiểm soát của `booking_scraper._FACILITY_SELECTORS/_FACILITY_BROADER_SELECTORS` (test chống lệch đọc thẳng file nguồn dạng text).
FACILITY_SELECTORS = [
    '.hprt-table-cell-conditions .bui-list__item',
    '.hprt-conditions-bui .bui-list__item',
    '.bui-list--text .bui-list__item',
    '.hprt-conditions li',
    '.hprt-table-cell-conditions li',
    '.e2e-cancellation[data-testid="cancellation-subtitle"]',
    '.e2e-prepayment[data-testid="prepayment-subtitle"]',
    '.bui-list__description',
    '.hprt-conditions-bui li',
]
FACILITY_BROADER_SELECTORS = [
    '.bui-list__description strong',
    '.hprt-table-cell-conditions strong',
    '.policy-title',
    '[data-testid="policy-title"]',
]
_PRICE_ONLY = re.compile(r'^\s*\d+[\.,\d]*\s*(VND|USD|EUR)?\s*$')
KNOWN_STATUSES = frozenset({"queued", "running", "success", "partial", "sold_out", "not_bookable", "error"})

# ---- Oracle bữa sáng: scanner literal, KHÔNG dùng regex/parser của repo. Hợp đồng = parser b-boundary:
#      cụm khẳng định CHÍNH XÁC (khoảng trắng ASCII đơn, không phân biệt hoa/thường, sau NFC+strip); một lần khớp bị phủ định khi NGAY TRƯỚC nó
#      (chỉ cách nhau khoảng trắng Unicode) là một TỪ NGUYÊN VẸN thuộc {không, chưa, no, not, without}.
_AFFIRM_PHRASES = ("bao gồm bữa sáng", "bao bữa sáng", "breakfast included")
_NEGATORS = frozenset({"không", "chưa", "no", "not", "without"})


def _fold(text: str) -> str:
    """Hạ chữ thường GIỮ NGUYÊN độ dài (ký tự có lower() nhiều ký tự giữ nguyên) để chỉ số khớp với văn bản gốc."""
    return "".join(ch.lower() if len(ch.lower()) == 1 else ch for ch in text)


def _prefix_is_negated(prefix: str) -> bool:
    i = len(prefix)
    while i > 0 and prefix[i - 1].isspace():
        i -= 1
    j = i
    while j > 0 and (prefix[j - 1].isalnum() or prefix[j - 1] == "_"):
        j -= 1
    return j < i and _fold(prefix[j:i]) in _NEGATORS


def breakfast_occurrences(line: str) -> List[bool]:
    """Mỗi lần khớp cụm khẳng định -> True nếu bị phủ định ngay trước, False nếu là khẳng định hợp lệ."""
    text = unicodedata.normalize("NFC", line or "").strip()
    folded = _fold(text)
    found = []
    for phrase in _AFFIRM_PHRASES:
        start = 0
        while True:
            idx = folded.find(phrase, start)
            if idx < 0:
                break
            found.append((idx, _prefix_is_negated(text[:idx])))
            start = idx + 1
    return [negated for _, negated in sorted(found)]


def line_affirms_breakfast(line: str) -> bool:
    return any(not negated for negated in breakfast_occurrences(line))


def line_negates_breakfast(line: str) -> bool:
    return any(breakfast_occurrences(line))


def line_mentions_breakfast_other(line: str) -> bool:
    """Có nhắc bữa sáng nhưng không phải khẳng định/phủ định theo hợp đồng (vd. bữa sáng trả thêm)."""
    folded = _fold(unicodedata.normalize("NFC", line or ""))
    return ("bữa sáng" in folded or "breakfast" in folded) and not breakfast_occurrences(line)


def breakfast_oracle(lines: Iterable[str]) -> bool:
    return any(line_affirms_breakfast(line) for line in lines)


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


def facility_lines_from_row(row) -> List[str]:
    """Port bs4 của `booking_scraper._get_facility_lines` (cùng thứ tự selector, cùng bộ lọc dòng/tiền)."""
    lines: List[str] = []
    for selector in FACILITY_SELECTORS:
        elems = row.select(selector)
        if elems:
            for el in elems:
                text = re.sub(r'^[•\-–—]\s*', '', el.get_text().strip())
                if text and len(text) > 3:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line and len(line) > 3 and not _PRICE_ONLY.search(line):
                            lines.append(line)
            break
    if not lines:
        for selector in FACILITY_BROADER_SELECTORS:
            for el in row.select(selector):
                text = el.get_text().strip()
                if text and len(text) > 3 and text not in lines:
                    lines.append(text)
    return lines


def _dates_of_form(form) -> Dict[str, Any]:
    """Ngày lưu trú do MỘT <form> khai báo qua input ẩn: đúng 1 `checkin`; checkout = `checkout` tường minh và/hoặc checkin + `interval` (số nguyên dương);
    mọi nguồn có mặt phải đồng thuận; thiếu/mơ hồ/không hợp lệ => ok=False kèm lý do."""
    def values(name):
        return [inp.get("value") for inp in form.find_all("input", attrs={"name": name})]

    checkin_values, checkout_values, interval_values = values("checkin"), values("checkout"), values("interval")
    if len(checkin_values) != 1:
        return {"ok": False, "reason": f"checkin_inputs={len(checkin_values)}"}
    if len(checkout_values) > 1 or len(interval_values) > 1:
        return {"ok": False, "reason": f"checkout_inputs={len(checkout_values)},interval_inputs={len(interval_values)}"}
    try:
        checkin = date.fromisoformat(checkin_values[0] or "")
        candidates = []
        if checkout_values:
            candidates.append(date.fromisoformat(checkout_values[0] or ""))
        if interval_values:
            raw = interval_values[0] or ""
            if not (raw.isascii() and raw.isdigit() and int(raw) > 0):
                return {"ok": False, "reason": f"interval_not_positive_integer={raw!r}"}
            candidates.append(checkin + timedelta(days=int(raw)))
    except (ValueError, OverflowError):
        return {"ok": False, "reason": "malformed_date_value"}
    if not candidates:
        return {"ok": False, "reason": "no_checkout_source"}
    if len(set(candidates)) != 1:
        return {"ok": False, "reason": "checkout_sources_conflict"}
    return {"ok": True, "checkin": checkin.isoformat(), "checkout": candidates[0].isoformat()}


def form_dates(soup) -> Dict[str, Any]:
    """Bằng chứng ngày CHỈ lấy từ `form#hprt-form` (đúng 1) của bảng phòng; nếu `form#top-book` tồn tại thì phải hợp lệ và trùng cặp ngày. Comment/review/script/form khác không phải chứng cứ."""
    forms = soup.select("form#hprt-form")
    if len(forms) != 1:
        return {"ok": False, "reason": f"hprt_form_count={len(forms)}"}
    main = _dates_of_form(forms[0])
    if not main["ok"]:
        return {"ok": False, "reason": "hprt_form:" + main["reason"]}
    tops = soup.select("form#top-book")
    if len(tops) > 1:
        return {"ok": False, "reason": f"top_book_count={len(tops)}"}
    if tops:
        top = _dates_of_form(tops[0])
        if not top["ok"]:
            return {"ok": False, "reason": "top_book:" + top["reason"]}
        if (top["checkin"], top["checkout"]) != (main["checkin"], main["checkout"]):
            return {"ok": False, "reason": "forms_disagree"}
    return main


def _item_error(item: Any, code: str, detail: str = "") -> dict:
    item_id = item.get("id") if isinstance(item, dict) else None
    return {"item_id": item_id, "status": "error", "error": code, "detail": detail[:300], "mismatch_count": 0, "mismatches": []}


def validate_item(item: dict) -> dict:
    try:
        return _validate_item(item)
    except Exception as exc:  # bất kỳ lỗi nào (kể cả dữ liệu đầu vào sai cấu trúc) -> báo cáo error, không bao giờ "0 mismatch"
        return _item_error(item, "validator_exception", f"{type(exc).__name__}: {exc}")


def _validate_item(item: dict) -> dict:
    status = item.get("status")
    if status not in KNOWN_STATUSES:
        return _item_error(item, "unknown_status", repr(status))
    rooms = sorted(item.get("rooms") or [], key=lambda room: room["room_option_index"])
    priced = [room for room in rooms if room.get("price_per_night") is not None]
    if status in ("queued", "running"):
        return {"item_id": item["id"], "status": "not_ready", "item_status": status, "mismatch_count": 0, "mismatches": []}
    if status in ("sold_out", "not_bookable", "error"):
        if priced:
            return _item_error(item, "priced_rooms_on_non_priced_status", f"status={status} priced={len(priced)}")
        return {"item_id": item["id"], "status": "not_applicable", "item_status": status, "mismatch_count": 0, "mismatches": []}
    # success / partial: bắt buộc có hàng giá + artifact
    if not priced:
        return _item_error(item, "priced_status_without_priced_rooms", f"status={status}")
    artifact_path = Path(item.get("artifact_html_path") or "")
    if not item.get("artifact_html_path") or not artifact_path.is_file():
        return _item_error(item, "missing_artifact")
    try:
        with gzip.open(artifact_path, "rt", encoding="utf-8") as source:
            html = source.read()
    except Exception as exc:  # artifact hỏng/không giải nén được
        return _item_error(item, "corrupt_artifact", f"{type(exc).__name__}: {exc}")
    return _validate_loaded(item, html, rooms)


def _validate_loaded(item: dict, html: str, rooms: List[dict]) -> dict:
    mismatches: List[dict] = []
    soup = BeautifulSoup(html, "html.parser")
    page_text = _text(soup.get_text(" ", strip=True))
    dom_rows = soup.select("tr.js-rt-block-row")
    if not dom_rows:
        dom_rows = soup.select("table#hprt-table tbody tr, .hprt-table tbody tr")
    # Booking chèn một placeholder `.hprt-cheapest-block-row` rỗng sau khi hydrate; không phải rate candidate.
    dom_rows = [row for row in dom_rows if _text(row.get_text(" ", strip=True))]

    duplicates = int(item.get("duplicate_options_count") or 0)
    rejected = int(item.get("rejected_options_count") or 0)
    if len(dom_rows) != item.get("dom_room_row_count"):
        mismatches.append({"field": "dom_room_row_count", "db": item.get("dom_room_row_count"), "dom": len(dom_rows)})
    mappable = len(dom_rows) == len(rooms) and duplicates == 0 and rejected == 0
    if not mappable:
        # DOM có hàng RAW trùng/bị loại hoặc số hàng lệch: zip theo thứ tự sẽ lệch -> không kiểm theo từng dòng; báo rõ, KHÔNG PASS giả.
        explained = (len(dom_rows) == len(rooms) + duplicates + rejected)
        return {
            "item_id": item["id"], "status": "skipped_unmappable", "explained_by_accounting": explained,
            "dom_rows": len(dom_rows), "db_rows": len(rooms), "duplicates": duplicates, "rejected": rejected,
            "mismatch_count": len(mismatches), "mismatches": mismatches,
        }

    # rev3 (N1, theo hợp đồng dữ liệu mục 4.6): ngày được ép trên `requested_hotel_link`; URL cuối `hotel_link` có thể mất tham số sau redirect
    # đổi slug (vd. 22land-residence-2). URL cuối mà CÒN tham số thì phải đúng ngày; nếu MẤT thì chỉ hợp lệ khi chính DOM đã lưu chứng minh đúng ngày
    # (input ẩn `checkin` == ngày check-in; ngày check-out xuất hiện trong trang). Không có `requested_hotel_link` (item cũ) => giữ hành vi cũ.
    expected_dates = {"checkin": str(item["checkin_date"]), "checkout": str(item["checkout_date"])}
    requested_link = item.get("requested_hotel_link")
    requested_query = parse_qs(urlparse(requested_link or "").query)
    final_query = parse_qs(urlparse(item.get("hotel_link") or "").query)
    dom_dates = None
    dropped_verified = []
    for field, expected in expected_dates.items():
        if requested_link and requested_query.get(field) != [expected]:
            mismatches.append({"field": f"requested_url_{field}", "db": requested_query.get(field), "expected": expected})
        final = final_query.get(field)
        if final is not None:
            if final != [expected]:
                mismatches.append({"field": f"url_{field}", "db": final, "expected": expected})
        elif not requested_link:
            mismatches.append({"field": f"url_{field}", "db": None, "expected": expected})
        else:
            if dom_dates is None:
                dom_dates = form_dates(soup)
            if dom_dates["ok"] and dom_dates[field] == expected:
                dropped_verified.append(field)
            else:
                mismatches.append({"field": f"url_{field}", "db": None, "expected": expected,
                                   "dom": dom_dates.get("reason") or f"form_{field}={dom_dates.get(field)}"})
    for field in ("hotel_name", "hotel_address"):
        expected = _text(item.get(field))
        if expected and expected not in page_text:
            mismatches.append({"field": field, "db": expected, "dom": "not_found"})

    current_name = ""
    current_room_text = ""
    checked_fields = 0
    breakfast = {"true_confirmed": 0, "false_confirmed": 0, "null_consistent_empty_conditions": 0}
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
                    mismatches.append({"option": index, "field": field, "db": expected, "dom": actual_scope[:240]})

        if room.get("discount_percent") is not None:
            checked_fields += 1
            expected = str(int(round(float(room["discount_percent"])))) + "%"
            if expected not in row_text:
                mismatches.append({"option": index, "field": "discount_percent", "db": expected, "dom": "not_found"})
        if room.get("price_includes_tax"):
            checked_fields += 1
            if "Đã bao gồm thuế và phí" not in row_text:
                mismatches.append({"option": index, "field": "price_includes_tax", "db": True, "dom": "not_found"})

        lines = facility_lines_from_row(dom_row)
        db_breakfast = room.get("breakfast_included")
        if db_breakfast is None:
            # parser chỉ trả None khi KHÔNG có dòng điều kiện nào; nếu DOM có điều kiện (hoặc có rooms_left) thì NULL là giá trị thiếu.
            if not lines and room.get("rooms_left") is None:
                breakfast["null_consistent_empty_conditions"] += 1
            else:
                mismatches.append({"option": index, "field": "breakfast_included", "db": None, "dom": "missing_value", "lines": lines[:6]})
        else:
            checked_fields += 1
            dom_breakfast = breakfast_oracle(lines)
            if bool(db_breakfast) != dom_breakfast:
                mismatches.append({"option": index, "field": "breakfast_included", "db": bool(db_breakfast), "dom": dom_breakfast, "lines": lines[:6]})
            else:
                breakfast["true_confirmed" if dom_breakfast else "false_confirmed"] += 1

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
        "status": "mismatch" if mismatches else "ok",
        "hotel": item.get("hotel_name") or item.get("hotel_name_hint"),
        "checkin": str(item["checkin_date"]),
        "dom_rows": len(dom_rows),
        "db_rows": len(rooms),
        "checked_fields": checked_fields,
        "breakfast": breakfast,
        "redirect_dropped_dates_verified_by_dom": dropped_verified,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


def summarize(reports: List[dict], expected_item_ids: Iterable[Any]) -> Dict[str, Any]:
    """`expected_item_ids` BẮT BUỘC và phải lấy từ nguồn độc lập với `reports` (truy vấn riêng/plan). Khớp CẢ TẬP lẫn số lần xuất hiện."""
    expected_list = list(expected_item_ids)
    bad_expected = [x for x in expected_list if not isinstance(x, int) or isinstance(x, bool)]
    expected_counts = Counter(expected_list)
    got_ids = [r.get("item_id") for r in reports]
    bad_reported = [x for x in got_ids if not isinstance(x, int) or isinstance(x, bool)]
    got_counts = Counter(got_ids)
    missing = sorted(set(expected_counts) - set(got_counts), key=repr)
    extra = sorted(set(got_counts) - set(expected_counts), key=repr)
    duplicated_reported = sorted([i for i, n in got_counts.items() if n > 1], key=repr)
    duplicated_expected = sorted([i for i, n in expected_counts.items() if n > 1], key=repr)
    by_status = Counter(r.get("status") for r in reports)
    errors = [r.get("item_id") for r in reports if r.get("status") == "error"]
    not_ready = [r.get("item_id") for r in reports if r.get("status") == "not_ready"]
    skipped = [r.get("item_id") for r in reports if r.get("status") == "skipped_unmappable"]
    unknown_status = [r.get("item_id") for r in reports if r.get("status") not in ("ok", "mismatch", "error", "not_ready", "skipped_unmappable", "not_applicable")]
    bf = {k: sum(r.get("breakfast", {}).get(k, 0) for r in reports) for k in ("true_confirmed", "false_confirmed", "null_consistent_empty_conditions")}
    summary = {
        "items_expected": len(expected_list), "items_reported": len(reports),
        "missing_from_report": missing, "extra_in_report": extra, "duplicate_reported_ids": duplicated_reported,
        "duplicate_expected_ids": duplicated_expected, "bad_expected_ids": bad_expected, "bad_reported_ids": bad_reported,
        "by_status": dict(by_status), "error_items": errors, "not_ready_items": not_ready, "skipped_unmappable_items": skipped,
        "unknown_report_status": unknown_status,
        "dom_rows": sum(r.get("dom_rows", 0) for r in reports), "db_rows": sum(r.get("db_rows", 0) for r in reports),
        "checked_fields": sum(r.get("checked_fields", 0) for r in reports),
        "mismatch_count": sum(r.get("mismatch_count", 0) for r in reports),
        "breakfast": bf, "breakfast_options_validated": bf["true_confirmed"] + bf["false_confirmed"],
        "redirect_items_with_dropped_url_dates_verified_by_dom": [r["item_id"] for r in reports if r.get("redirect_dropped_dates_verified_by_dom")],
    }
    problems = [bool(expected_list) is False, bool(missing), bool(extra), bool(duplicated_reported), bool(duplicated_expected), bool(bad_expected),
                bool(bad_reported), bool(errors), bool(not_ready), bool(skipped), bool(unknown_status), summary["mismatch_count"] > 0]
    summary["exit_code"] = 1 if any(problems) else 0
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id", type=int)
    parser.add_argument("--expected-items", type=int, default=None, help="số item mong đợi theo plan (bắt buộc khớp nếu cho)")
    args = parser.parse_args()
    from app.core.database import get_db_connection  # import muộn: module thuần không chạm DB khi được test
    from app.database.repositories import CrawlRunItemRepository

    with get_db_connection() as conn:                    # nguồn ID ĐỘC LẬP với list_by_run
        cur = conn.cursor()
        cur.execute("SELECT total FROM crawl_runs WHERE id=%s", (args.run_id,))
        row = cur.fetchone()
        if row is None:
            print(json.dumps({"run_id": args.run_id, "error": "run_not_found", "exit_code": 2}))
            raise SystemExit(2)
        run_total = row[0]
        cur.execute("SELECT id FROM crawl_run_items WHERE crawl_run_id=%s ORDER BY id", (args.run_id,))
        expected_ids = [r[0] for r in cur.fetchall()]
        cur.close()
    items = CrawlRunItemRepository().list_by_run(args.run_id)
    reports = [validate_item(item) for item in items]
    summary = summarize(reports, expected_item_ids=expected_ids)
    summary["run_id"] = args.run_id
    summary["run_total"] = run_total
    if run_total != len(expected_ids) or (args.expected_items is not None and args.expected_items != len(expected_ids)):
        summary["exit_code"] = 1
        summary["count_problem"] = {"run_total": run_total, "item_rows": len(expected_ids), "plan": args.expected_items}
    summary["reports"] = reports
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(summary["exit_code"])


if __name__ == "__main__":
    main()
