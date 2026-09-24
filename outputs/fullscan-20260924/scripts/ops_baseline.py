"""Chụp trạng thái DB vận hành + các DB cô lập (Claude, 2026-09-24). CHỈ ĐỌC. Chạy 2 lần (trước/sau) rồi so: dòng OPS phải giống hệt.
Dùng (cwd = hotel-price-intelligence/backend):  python ../../outputs/fullscan-20260924/scripts/ops_baseline.py [DB_CO_LAP ...]"""
import sys
from pathlib import Path

BACKEND = Path("D:/MSE/CAPSTONE/hotel-price-intelligence/backend")
sys.path.insert(0, str(BACKEND))
import mysql.connector  # noqa: E402

from app.core.config import settings  # noqa: E402


def snap(db):
    c = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=db, autocommit=True)
    cur = c.cursor(buffered=True)
    out = {}
    cur.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM crawl_runs"); out["crawl_runs(count,max_id)"] = cur.fetchone()
    cur.execute("SELECT COALESCE(MAX(id),0) FROM crawl_run_items"); out["crawl_run_items(max_id)"] = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(MAX(record_id),0) FROM price_observations"); out["price_observations(max_record_id)"] = cur.fetchone()[0]
    cur.execute("SELECT id, status, total, processed FROM crawl_runs ORDER BY id DESC LIMIT 3"); out["3 run gần nhất(id,status,total,processed)"] = cur.fetchall()
    cur.close(); c.close()
    return out


print("DB vận hành (settings.DB_NAME) =", settings.DB_NAME)
for k, v in snap(settings.DB_NAME).items():
    print("  OPS ", k, "=", v)
for db in sys.argv[1:]:
    print("DB cô lập =", db)
    for k, v in snap(db).items():
        print("  ISO ", k, "=", v)
