"""N1 proxy (CHI DOC, warehouse hien hanh): hotel nao co breakfast_included=TRUE o (gan) MOI option - dau hieu bug phu dinh bua sang ('Khong bao gom bua sang' -> True).
KHONG phai bang chung: raw HTML khong con (artifact opt-in tat), mot hotel co the that su bao gom bua sang o moi rate. Dung de cho biet cac ung vien ngoai 5 hotel da quet tren artifact."""
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
KNOWN = json.loads((HERE / "n1_hotels_from_scan.json").read_text(encoding="utf-8"))
known = sorted(r["hotel_id"] for r in KNOWN)
pointer = json.loads(Path(r"D:\MSE\CAPSTONE\outputs\warehouse\warehouse_current.json").read_text(encoding="utf-8"))
conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD,
                               database=pointer["warehouse_database"], connection_timeout=10, autocommit=True)
cur = conn.cursor(dictionary=True)
cur.execute("SET SESSION max_execution_time=300000")
cur.execute("SELECT hotel_id, COUNT(*) AS n_options, SUM(breakfast_included = 1) AS n_true, COUNT(DISTINCT room_identity_key) AS n_rooms "
            "FROM price_observations WHERE is_sold_out = 0 AND breakfast_included IS NOT NULL GROUP BY hotel_id")
rows = cur.fetchall()
conn.close()
hotels = [{"hotel_id": r["hotel_id"], "n_options": int(r["n_options"]), "n_rooms": int(r["n_rooms"]), "share_true": round(float(r["n_true"]) / int(r["n_options"]), 4)} for r in rows]
overall = sum(r["n_true"] for r in rows) / sum(r["n_options"] for r in rows)
suspects = sorted((h for h in hotels if h["share_true"] >= 0.99 and h["n_options"] >= 100), key=lambda h: -h["n_options"])
summary = {
    "warehouse": pointer["warehouse_database"], "hotels_with_options": len(hotels), "overall_share_true": round(float(overall), 4),
    "known_from_artifact_scan": known,
    "known_share_true": {h["hotel_id"]: h["share_true"] for h in hotels if h["hotel_id"] in known},
    "proxy_rule": "share_true >= 0.99 and n_options >= 100",
    "suspects_n": len(suspects), "suspects": suspects,
    "known_not_flagged_by_proxy": [k for k in known if k not in {h["hotel_id"] for h in suspects}],
    "suspects_not_in_known": [h["hotel_id"] for h in suspects if h["hotel_id"] not in known],
    "share_true_distribution": {
        "eq_1.0": sum(h["share_true"] == 1.0 for h in hotels), "ge_0.99": sum(h["share_true"] >= 0.99 for h in hotels),
        "ge_0.9": sum(h["share_true"] >= 0.9 for h in hotels), "le_0.01": sum(h["share_true"] <= 0.01 for h in hotels)},
}
(HERE / "n1_proxy_all_true.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False, indent=1))
