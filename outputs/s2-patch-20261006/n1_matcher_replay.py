"""N1 buoc (b) v2 (GPT file 58): replay MATCHER THAT truoc/sau patch tren moi item bi anh huong cua artifact quet 24-25/09. CHI DOC; ghi `n1_matcher_replay.json` (output MOI,
khong ghi de `n1_map_series.json` - ban cu chi la association theo rate key va da bi thay the). Logic o `ml/analysis/n1_replay.py` (co test)."""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ML = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\ml")
sys.path.insert(0, str(ML))
sys.path.insert(0, str(ML.parent / "backend"))

from analysis.n1_replay import KEY_FIELDS, replay_item  # noqa: E402
from analysis.utc_connection import connect_utc_readonly  # noqa: E402

import measure_facility_lines as m  # noqa: E402

HERE = Path(__file__).resolve().parent
SCAN_DB, DEV_DB, DEV_DATASET = "hotel_price_intel_fullscan_20260924", "warehouse_dsdev_20261004_3src", "ds_20261006_rh11"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


connect = connect_utc_readonly            # session UTC DA XAC MINH + READ ONLY (GPT file 60 C59-M2)


old, new = load(HERE / "parser_original_503db7d.py", "old"), load(HERE / "parser_patched.py", "new")
scan, scan_conn = connect(SCAN_DB)
dev, dev_conn = connect(DEV_DB)
items_out, pages = [], sorted(m.ART.rglob("page.html.gz"))
for page in pages:
    run_id, item_id = page.parent.parent.name, page.parent.name
    soup = BeautifulSoup(gzip.open(page, "rt", encoding="utf-8", errors="replace").read(), "html.parser")
    html_rows = []
    for ordinal, row in enumerate(soup.select("tr.js-rt-block-row")):
        lines = m.facility_lines(row)
        html_rows.append({"ordinal": ordinal, "old": old.parse_room_conditions(lines), "new": new.parse_room_conditions(lines)})
    if not any(r["old"]["breakfast_included"] != r["new"]["breakfast_included"] for r in html_rows):
        continue
    scan.execute("SELECT hotel_id, checkin_date FROM crawl_run_items WHERE id=%s AND crawl_run_id=%s", (int(item_id), int(run_id)))
    item = scan.fetchone()
    scan.execute(f"SELECT {', '.join(KEY_FIELDS)}, room_option_index FROM price_observations WHERE crawl_run_item_id=%s ORDER BY room_option_index", (int(item_id),))
    observations = scan.fetchall()
    dev.execute("SELECT id, canonical_room_key, canonical_rate_key, canonical_series_id, room_type_anchor_raw, max_occupancy, room_area FROM ml_reference_assignments "
                "WHERE dataset_version=%s AND hotel_id=%s AND checkin_date=%s", (DEV_DATASET, item["hotel_id"], item["checkin_date"]))
    assignment = dev.fetchone()
    result = replay_item(observations=observations, html_rows=html_rows, assignment=assignment)
    result.update(scan_run_id=int(run_id), scan_item_id=int(item_id), hotel_id=item["hotel_id"], checkin_date=str(item["checkin_date"]),
                  changed_html_rows=sum(r["old"]["breakfast_included"] != r["new"]["breakfast_included"] for r in html_rows))
    if assignment is not None:
        result["rehearsal_causal_assignment"] = {"database": DEV_DB, "dataset_version": DEV_DATASET, "assignment_id": assignment["id"], "canonical_series_id": assignment["canonical_series_id"],
                                                 "canonical_room_key": assignment["canonical_room_key"], "canonical_rate_key": assignment["canonical_rate_key"]}
    items_out.append(result)
scan_conn.close(), dev_conn.close()

classes = {}
for r in items_out:
    classes[r["class"]] = classes.get(r["class"], 0) + 1
evaluated = [r for r in items_out if r["class"] == "evaluated"]
by_series = {}
for r in evaluated:
    by_series.setdefault((r["hotel_id"], r["checkin_date"]), []).append(r["old_to_patched_transition"])
summary = {
    "scope": "snapshot quet 24-25/09: MOT ngay check-in (2026-10-11); assignment la REHEARSAL causal (khong phai production series); khong ngoai suy toan lich su",
    "pages_scanned": len(pages), "items_with_changed_rows": len(items_out), "item_classes": classes,
    "unmapped_items": [{"item": f"{r['scan_run_id']}/{r['scan_item_id']}", "hotel_id": r["hotel_id"], "reason": r["reason"]} for r in items_out if r["class"] == "unmapped"],
    "no_assignment_items": [f"{r['scan_run_id']}/{r['scan_item_id']} {r['hotel_id']}" for r in items_out if r["class"] == "no_assignment"],
    "evaluated_item_transitions": {f"{r['scan_run_id']}/{r['scan_item_id']} {r['hotel_id']}": r["old_to_patched_transition"] for r in evaluated},
    "evaluated_series_transitions": {f"{h} {c}": t for (h, c), t in by_series.items()},
    "association_only_note": "affected_option_rate_equals_reference_rate la association (rate key khong dinh danh phong), KHONG chung minh reference bi anh huong",
    "dataset": {"database": DEV_DB, "dataset_version": DEV_DATASET},
}
(HERE / "n1_matcher_replay.json").write_text(json.dumps({"summary": summary, "items": items_out}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
