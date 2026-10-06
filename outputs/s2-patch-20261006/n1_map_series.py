"""N1 buoc (b) cua GPT file 54: ANH XA option bi anh huong (artifact HTML quet toan cohort 24/09) sang canonical key va sang chuoi production. CHI DOC (artifact + SELECT).

Voi moi dong HTML ma parser goc va parser da va cho `breakfast_included` KHAC nhau:
  1. tim observation DB cua dung item (fullscan DB) bang `room_option_index`; chi nhan anh xa khi cac truong parse lai (breakfast cu, free_cancellation, cancellation_policy)
     TRUNG voi DB (thu ca co so 0 va 1) - khong khop => `unmapped` kem ly do, khong doan;
  2. tinh canonical key CU (tu DB) va key MOI (breakfast = gia tri parser da va) bang `app.warehouse.canonicalize.compute_canonical_keys` that;
  3. tra warehouse hien hanh: chuoi (hotel_id, checkin_date) co ton tai khong, reference da dong bang (canonical_rate_key) co bang key CU cua option bi anh huong khong.
Ket qua la xac nhan o MUC SNAPSHOT SCAN (item da quet), KHONG ngoai suy sang toan lich su.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
import mysql.connector
from app.core.config import settings
from app.warehouse.canonicalize import compute_canonical_keys

import measure_facility_lines as m

HERE = Path(__file__).resolve().parent
SCAN_DB = "hotel_price_intel_fullscan_20260924"
DEV_DB, DEV_DATASET = "warehouse_dsdev_20261004_3src", "ds_20261006_rh11"      # ml_* cua warehouse hien hanh RONG: assignment causal nam o clone dev (dataset rehearsal ghi ro)


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


old = load(HERE / "parser_original_503db7d.py", "old")
new = load(HERE / "parser_patched.py", "new")
pointer = json.loads(Path(r"D:\MSE\CAPSTONE\outputs\warehouse\warehouse_current.json").read_text(encoding="utf-8"))


def connect(database: str):
    return mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=database,
                                   connection_timeout=10, autocommit=True)


def norm(value):
    return None if value is None else (bool(value) if isinstance(value, (bool, int)) and value in (0, 1) else value)


scan, wh, dev = connect(SCAN_DB), connect(pointer["warehouse_database"]), connect(DEV_DB)
for c in (scan, wh, dev):
    cur = c.cursor()
    cur.execute("SET SESSION max_execution_time=120000")
    cur.execute("SET SESSION TRANSACTION READ ONLY")
    cur.close()
scur, wcur, dcur = scan.cursor(dictionary=True), wh.cursor(dictionary=True), dev.cursor(dictionary=True)

affected_items: dict[tuple[str, str], list[dict]] = {}
all_rows_by_item: dict[tuple[str, str], list[dict]] = {}
pages = sorted(m.ART.rglob("page.html.gz"))
for page in pages:
    run_id, item_id = page.parent.parent.name, page.parent.name
    soup = BeautifulSoup(gzip.open(page, "rt", encoding="utf-8", errors="replace").read(), "html.parser")
    parsed_rows = []
    for ordinal, row in enumerate(soup.select("tr.js-rt-block-row")):
        lines = m.facility_lines(row)
        a, b = old.parse_room_conditions(lines), new.parse_room_conditions(lines)
        parsed_rows.append({"ordinal": ordinal, "old": a, "new": b, "lines": lines})
    if any(r["old"]["breakfast_included"] != r["new"]["breakfast_included"] for r in parsed_rows):
        all_rows_by_item[(run_id, item_id)] = parsed_rows
        affected_items[(run_id, item_id)] = [r for r in parsed_rows if r["old"]["breakfast_included"] != r["new"]["breakfast_included"]]


def is_option_row(parsed: dict) -> bool:
    """Dong HTML sinh ra mot option = co it nhat mot dieu kien parse duoc (dong tieu de/gop phong cho (None, None))."""
    old_parse = parsed["old"]
    return not (old_parse["breakfast_included"] is None and old_parse["free_cancellation"] is None and not old_parse.get("cancellation_policy"))

rows_out, summary = [], {"pages_scanned": len(pages), "items_with_changed_rows": len(affected_items), "changed_rows": sum(len(v) for v in affected_items.values())}
unmapped_reasons: dict[str, int] = {}
for (run_id, item_id), changed in sorted(affected_items.items()):
    scur.execute("SELECT hotel_id, checkin_date FROM crawl_run_items WHERE id=%s AND crawl_run_id=%s", (int(item_id), int(run_id)))
    item = scur.fetchone()
    scur.execute("SELECT * FROM price_observations WHERE crawl_run_item_id=%s ORDER BY room_option_index", (int(item_id),))
    observations = scur.fetchall()
    option_rows = [r for r in all_rows_by_item[(run_id, item_id)] if is_option_row(r)]
    alignment_problem = None
    if len(option_rows) != len(observations):
        alignment_problem = f"so dong option HTML ({len(option_rows)}) != so observation DB ({len(observations)})"
    else:                                                                    # cung so luong + cung thu tu: kiem TOAN BO tuple (bf, free_cancellation, policy), khong chi dong doi
        for html_row, observation in zip(option_rows, observations):
            if not (norm(observation["breakfast_included"]) == html_row["old"]["breakfast_included"] and norm(observation["free_cancellation"]) == html_row["old"]["free_cancellation"]
                    and (observation["cancellation_policy"] or None) == (html_row["old"].get("cancellation_policy") or None)):
                alignment_problem = f"tuple dieu kien lech tai option {html_row['ordinal']} (HTML) so voi DB"
                break
    ordinal_to_observation = {} if alignment_problem else {r["ordinal"]: o for r, o in zip(option_rows, observations)}
    base = None if alignment_problem else 0
    for change in changed:
        record = {"run_id": int(run_id), "item_id": int(item_id), "hotel_id": item["hotel_id"] if item else None, "checkin_date": str(item["checkin_date"]) if item else None,
                  "ordinal": change["ordinal"], "old_breakfast": change["old"]["breakfast_included"], "new_breakfast": change["new"]["breakfast_included"]}
        if item is None or alignment_problem:
            reason = "item khong co trong scan DB" if item is None else alignment_problem
            unmapped_reasons[reason] = unmapped_reasons.get(reason, 0) + 1
            rows_out.append({**record, "mapped": False, "reason": reason})
            continue
        obs = ordinal_to_observation[change["ordinal"]]
        base_row = {k: obs[k] for k in ("record_id", "hotel_id", "checkin_date", "is_sold_out", "room_type_raw", "max_occupancy", "bed_config", "room_area",
                                        "breakfast_included", "free_cancellation", "cancellation_policy")}
        old_keys = compute_canonical_keys(base_row)
        new_keys = compute_canonical_keys({**base_row, "breakfast_included": change["new"]["breakfast_included"]})
        dcur.execute("SELECT id, canonical_room_key, canonical_rate_key, canonical_series_id FROM ml_reference_assignments WHERE dataset_version=%s AND hotel_id=%s AND checkin_date=%s",
                     (DEV_DATASET, obs["hotel_id"], obs["checkin_date"]))
        assignment = dcur.fetchone()
        wcur.execute("SELECT room_identity_key, rate_plan_key, status FROM hotel_reference_rooms WHERE hotel_id=%s AND checkin_date=%s", (obs["hotel_id"], obs["checkin_date"]))
        full_history = wcur.fetchall()
        wcur.execute("SELECT COUNT(*) AS n FROM curated_observation_keys WHERE hotel_id=%s AND checkin_date=%s AND canonical_rate_key=%s", (obs["hotel_id"], obs["checkin_date"], old_keys.canonical_rate_key))
        old_key_seen = int(wcur.fetchone()["n"])
        wcur.execute("SELECT COUNT(*) AS n FROM curated_observation_keys WHERE hotel_id=%s AND checkin_date=%s", (obs["hotel_id"], obs["checkin_date"]))
        series_obs = int(wcur.fetchone()["n"])
        rows_out.append({**record, "mapped": True, "room_option_index_base": "can chinh theo thu tu option (khong theo ordinal DOM)", "room_key_changes": old_keys.canonical_room_key != new_keys.canonical_room_key,
                         "rate_key_changes": old_keys.canonical_rate_key != new_keys.canonical_rate_key, "old_rate_key": old_keys.canonical_rate_key[:16], "new_rate_key": new_keys.canonical_rate_key[:16],
                         "production_series_exists_for_hotel_checkin": assignment is not None, "full_history_reference_rows": len(full_history),
                         "full_history_reference_rate_key_equals_old_key": any(r["rate_plan_key"] == old_keys.canonical_rate_key for r in full_history),
                         "full_history_reference_room_key_equals": any(r["room_identity_key"] == old_keys.canonical_room_key for r in full_history),
                         "frozen_reference_rate_key_equals_old_key": bool(assignment and assignment["canonical_rate_key"] == old_keys.canonical_rate_key),
                         "frozen_reference_room_key_equals": bool(assignment and assignment["canonical_room_key"] == old_keys.canonical_room_key),
                         "warehouse_observations_for_hotel_checkin": series_obs, "warehouse_observations_with_old_rate_key": old_key_seen})
scan.close(), wh.close(), dev.close()
mapped = [r for r in rows_out if r["mapped"]]
summary.update(mapped_rows=len(mapped), unmapped_rows=len(rows_out) - len(mapped), unmapped_reasons=unmapped_reasons,
               room_key_changes=sum(r["room_key_changes"] for r in mapped), rate_key_changes=sum(r["rate_key_changes"] for r in mapped),
               distinct_hotels=sorted({r["hotel_id"] for r in rows_out}), distinct_hotel_checkin=len({(r["hotel_id"], r["checkin_date"]) for r in mapped}),
               hotel_checkin_with_production_series=len({(r["hotel_id"], r["checkin_date"]) for r in mapped if r["production_series_exists_for_hotel_checkin"]}),
               hotel_checkin_where_frozen_reference_is_an_affected_option=len({(r["hotel_id"], r["checkin_date"]) for r in mapped if r["frozen_reference_rate_key_equals_old_key"]}),
               hotel_checkin_with_full_history_reference=len({(r["hotel_id"], r["checkin_date"]) for r in mapped if r["full_history_reference_rows"]}),
               hotel_checkin_where_full_history_reference_equals_affected_option=len({(r["hotel_id"], r["checkin_date"]) for r in mapped if r["full_history_reference_rate_key_equals_old_key"] and r["full_history_reference_room_key_equals"]}),
               causal_assignments_from=f"{DEV_DB}/{DEV_DATASET} (rehearsal)",
               scope="snapshot scan 24/09 (item da quet), khong ngoai suy toan lich su", warehouse=pointer["warehouse_database"])
(HERE / "n1_map_series.json").write_text(json.dumps({"summary": summary, "rows": rows_out}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
