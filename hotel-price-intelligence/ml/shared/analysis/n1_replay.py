"""N1: replay MATCHER THAT tren toan bo offer cua mot item truoc/sau patch parser (GPT file 58 N1-M1).

So khop rate key giua option bi anh huong va reference KHONG chung minh reference bi mat (rate key khong dinh danh phong). Dung bang chung duy nhat co nghia: chay
`select_best_match()` (qua adapter cua `dataset_builder.matches`) tren CA danh sach option cua item voi key CU, roi voi key MOI (chi doi breakfast theo parser da va), va so
status/score/option duoc chon. Khong sua observation nao; khong doan khi khong can chinh duoc HTML voi DB (-> `unmapped`) hay khi khong co assignment (-> `no_assignment`).
"""
from __future__ import annotations

from typing import Any

from dataset_builder import env  # noqa: F401 - nap backend vao sys.path
from dataset_builder.matches import adapt_reference, adapt_rooms

from app.scraper.reference import select_best_match  # noqa: E402
from app.warehouse.canonicalize import compute_canonical_keys  # noqa: E402

KEY_FIELDS = ("record_id", "hotel_id", "checkin_date", "is_sold_out", "room_type_raw", "max_occupancy", "bed_config", "room_area", "breakfast_included",
              "free_cancellation", "cancellation_policy")


def _norm(value: Any) -> Any:
    return bool(value) if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1) else value


def is_option_row(parsed: dict[str, Any]) -> bool:
    """Dong HTML sinh ra mot option = co it nhat mot dieu kien parse duoc (dong tieu de/gop phong cho (None, None))."""
    old = parsed["old"]
    return not (old["breakfast_included"] is None and old["free_cancellation"] is None and not old.get("cancellation_policy"))


def align_options(html_rows: list[dict[str, Any]], observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Can chinh THEO THU TU: so dong option HTML phai bang so observation DB va TOAN BO tuple (breakfast, free_cancellation, policy) khop tung dong. Khong khop => (None, ly do)."""
    option_rows = [r for r in html_rows if is_option_row(r)]
    if len(option_rows) != len(observations):
        return None, f"so dong option HTML ({len(option_rows)}) != so observation DB ({len(observations)})"
    for html_row, observation in zip(option_rows, observations):
        old = html_row["old"]
        if not (_norm(observation["breakfast_included"]) == old["breakfast_included"] and _norm(observation["free_cancellation"]) == old["free_cancellation"]
                and (observation["cancellation_policy"] or None) == (old.get("cancellation_policy") or None)):
            return None, f"tuple dieu kien lech tai option {html_row['ordinal']} (HTML) so voi DB"
    return option_rows, None


def _records(observations: list[dict[str, Any]], breakfast_override: dict[int, Any]) -> list[dict[str, Any]]:
    records = []
    for index, observation in enumerate(observations):
        row = {k: observation[k] for k in KEY_FIELDS}
        if index in breakfast_override:
            row["breakfast_included"] = breakfast_override[index]
        keys = compute_canonical_keys(row)
        records.append({"record_id": observation["record_id"], "canonical_room_key": keys.canonical_room_key, "canonical_rate_key": keys.canonical_rate_key,
                        "max_occupancy": observation["max_occupancy"], "room_area": observation["room_area"], "room_type_raw": observation["room_type_raw"]})
    return records


def _match(records: list[dict[str, Any]], assignment: dict[str, Any]) -> dict[str, Any]:
    index, status, score = select_best_match(adapt_rooms(records), adapt_reference(assignment))
    return {"status": status, "score": round(float(score), 4), "selected_option_index": index,
            "selected_scan_record_id": records[index]["record_id"] if index is not None else None,
            "selected_room_key": records[index]["canonical_room_key"] if index is not None else None,
            "selected_rate_key": records[index]["canonical_rate_key"] if index is not None else None}


def replay_item(*, observations: list[dict[str, Any]], html_rows: list[dict[str, Any]], assignment: dict[str, Any] | None) -> dict[str, Any]:
    """Ket qua cho MOT item: class = unmapped | no_assignment | evaluated. Association (rate key trung) va matcher evidence (old->patched) la HAI truong rieng."""
    option_rows, reason = align_options(html_rows, observations)
    if option_rows is None:
        return {"class": "unmapped", "reason": reason}
    affected = {j: row["new"]["breakfast_included"] for j, row in enumerate(option_rows) if row["old"]["breakfast_included"] != row["new"]["breakfast_included"]}
    old_records, new_records = _records(observations, {}), _records(observations, affected)
    base = {"n_options": len(observations), "affected_option_indexes": sorted(affected),
            "options": [{"index": j, "scan_record_id": o["record_id"], "affected": j in affected, "old_room_key": old_records[j]["canonical_room_key"],
                         "old_rate_key": old_records[j]["canonical_rate_key"], "new_rate_key": new_records[j]["canonical_rate_key"]} for j, o in enumerate(observations)]}
    base["room_key_changes"] = sum(old_records[j]["canonical_room_key"] != new_records[j]["canonical_room_key"] for j in affected)
    base["rate_key_changes"] = sum(old_records[j]["canonical_rate_key"] != new_records[j]["canonical_rate_key"] for j in affected)
    if assignment is None:
        return {"class": "no_assignment", **base}
    reference_rate = assignment["canonical_rate_key"]
    old_match, new_match = _match(old_records, assignment), _match(new_records, assignment)
    selected_old = old_match["selected_option_index"]
    return {
        "class": "evaluated", **base,
        "affected_option_rate_equals_reference_rate": any(old_records[j]["canonical_rate_key"] == reference_rate for j in affected),      # CHI la association
        "old_match": old_match, "patched_match": new_match,
        "selected_reference_match_affected": selected_old in affected if selected_old is not None else False,
        "selected_record_changed": old_match["selected_scan_record_id"] != new_match["selected_scan_record_id"],
        "old_to_patched_transition": ("unchanged" if (old_match["status"], old_match["selected_scan_record_id"]) == (new_match["status"], new_match["selected_scan_record_id"])
                                      else f"{old_match['status']}->{new_match['status']}"),
    }
