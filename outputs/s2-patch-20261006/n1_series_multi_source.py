"""N1/S2 (CHI DOC, warehouse hien hanh): bao nhieu chuoi (hotel_id, checkin_date) duoc nhieu hon mot nguon thu thap (local_primary/vps/local_aux) quan sat?
Neu parser vá chi duoc rollout tren MOT nguon, cung mot chuoi se bi parse theo hai che do khac nhau tuy nguon => key breakfast_included khac nhau trong cung chuoi."""
import json
import sys
from pathlib import Path

BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
import mysql.connector
from app.core.config import settings

HERE = Path(r"D:\MSE\CAPSTONE\outputs\s2-patch-20261006")
pointer = json.loads(Path(r"D:\MSE\CAPSTONE\outputs\warehouse\warehouse_current.json").read_text(encoding="utf-8"))
conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD,
                               database=pointer["warehouse_database"], connection_timeout=10, autocommit=True)
cur = conn.cursor(dictionary=True)
cur.execute("SET SESSION max_execution_time=300000")
cur.execute("""
SELECT cri.hotel_id, cri.checkin_date, COUNT(DISTINCT im.source_code) AS n_sources, COUNT(*) AS n_items
FROM crawl_run_items cri
JOIN etl_item_map im ON im.warehouse_item_id = cri.id AND im.import_batch_id = %s AND im.include_reference = TRUE
WHERE cri.status = 'success'
GROUP BY cri.hotel_id, cri.checkin_date
""", (pointer["batch_id"],))
rows = cur.fetchall()
conn.close()
total = len(rows)
by = {}
for r in rows:
    by[int(r["n_sources"])] = by.get(int(r["n_sources"]), 0) + 1
items_by = {}
for r in rows:
    items_by[int(r["n_sources"])] = items_by.get(int(r["n_sources"]), 0) + int(r["n_items"])
summary = {"warehouse": pointer["warehouse_database"], "series_hotel_checkin": total, "by_n_sources": by,
           "share_multi_source": round(sum(v for k, v in by.items() if k >= 2) / total, 4) if total else None,
           "items_by_n_sources": items_by,
           "items_share_in_multi_source_series": round(sum(v for k, v in items_by.items() if k >= 2) / sum(items_by.values()), 4) if items_by else None}
(HERE / "n1_series_multi_source.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=1))
