"""N1: metadata quet (run/scraper/selector/git/thoi gian) doc tu scan DB qua ket noi UTC DA XAC MINH, ghi `n1_scan_identity.json` (evidence cua policy). CHI DOC.
Gia tri `*_utc` la UTC that (khong copy literal tu session mac dinh). Them `*_vn` rieng + offset ro rang de de doi chieu voi nhat ky van hanh."""
import datetime as dt
import json
import sys
from pathlib import Path

ML = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\ml")
sys.path.insert(0, str(ML))

from analysis.utc_connection import connect_utc_readonly  # noqa: E402

HERE = Path(__file__).resolve().parent
SCAN_DB = "hotel_price_intel_fullscan_20260924"
RUN_IDS = (1, 3)

cursor, conn = connect_utc_readonly(SCAN_DB)
cursor.execute("SELECT @@session.time_zone AS tz")
session_tz = cursor.fetchone()["tz"]
cursor.execute("SELECT id, status, started_at, finished_at, scraper_version, selector_version, git_commit, total FROM crawl_runs WHERE id IN (%s, %s) ORDER BY id", RUN_IDS)
runs = cursor.fetchall()
cursor.execute("SELECT crawl_run_id, COUNT(*) AS items, COUNT(DISTINCT hotel_id) AS hotels, MIN(checkin_date) AS c0, MAX(checkin_date) AS c1 FROM crawl_run_items "
               "WHERE crawl_run_id IN (%s, %s) GROUP BY crawl_run_id ORDER BY crawl_run_id", RUN_IDS)
items = {r["crawl_run_id"]: r for r in cursor.fetchall()}
conn.close()
VN = dt.timedelta(hours=7)


def iso(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


identity = {"scan_database": SCAN_DB, "session_time_zone": session_tz, "timestamps": "started_at/finished_at doc qua session UTC (da xac minh); *_vn = UTC+07:00 chi de doi chieu",
            "runs": [{"run_id": r["id"], "status": r["status"], "started_at_utc": iso(r["started_at"]), "finished_at_utc": iso(r["finished_at"]),
                      "started_at_vn": iso(r["started_at"] + VN), "finished_at_vn": iso(r["finished_at"] + VN), "scraper_version": r["scraper_version"],
                      "selector_version": r["selector_version"], "git_commit": r["git_commit"], "planned_items": int(r["total"]), "items": int(items[r["id"]]["items"]),
                      "hotels": int(items[r["id"]]["hotels"]), "checkin_dates": sorted({str(items[r["id"]]["c0"]), str(items[r["id"]]["c1"])})} for r in runs]}
assert session_tz == "+00:00" and [r["run_id"] for r in identity["runs"]] == list(RUN_IDS)
(HERE / "n1_scan_identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(identity, ensure_ascii=False, indent=1))
