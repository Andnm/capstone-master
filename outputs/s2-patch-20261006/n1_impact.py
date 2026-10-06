"""N1: map item->hotel (DB quet toan cohort 24/09, chi SELECT) ghi danh sach hotel; tac dong len dataset rehearsal do o n1_impact_part2.py. CHI DOC."""
import json
import sys
from pathlib import Path

BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")
import mysql.connector
import pandas as pd
from app.core.config import settings

HERE = Path(r"D:\MSE\CAPSTONE\outputs\s2-patch-20261006")
affected = json.loads((HERE / "n1_affected_items.json").read_text(encoding="utf-8"))["affected"]
conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD,
                               database="hotel_price_intel_fullscan_20260924", connection_timeout=10, autocommit=True)
cur = conn.cursor(dictionary=True)
cur.execute("SET SESSION max_execution_time=30000")
rows = []
for key, v in affected.items():
    run_id, item_id = key.split("/")
    cur.execute("SELECT hotel_id, checkin_date FROM crawl_run_items WHERE id=%s AND crawl_run_id=%s", (int(item_id), int(run_id)))
    r = cur.fetchone()
    rows.append({"item": key, "hotel_id": r["hotel_id"] if r else None, "checkin": str(r["checkin_date"]) if r else None, **v})
conn.close()
df = pd.DataFrame(rows)
hotels = df.groupby("hotel_id").agg(items=("item", "size"), options_changed=("changed_rows", "sum"), options_negation=("negation_rows", "sum"),
                                    options_in_items=("options", "sum"), affirmative=("affirmative_rows", "sum")).reset_index()
print(hotels.to_string())
hotels.to_json(HERE / "n1_hotels_from_scan.json", orient="records", indent=1)
print("hotels ->", sorted(hotels["hotel_id"].dropna()), "(buoc 2: n1_impact_part2.py, eda venv vi can pyarrow)")
