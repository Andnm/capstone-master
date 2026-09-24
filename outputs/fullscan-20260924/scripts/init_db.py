"""Tạo DATABASE MỚI cô lập cho quét toàn cohort từ setup.sql (Claude, 2026-09-24). Dùng: python init_db.py [TÊN_DB].
AN TOÀN: setup.sql gốc bắt đầu bằng `CREATE DATABASE IF NOT EXISTS hotel_price_intel; USE hotel_price_intel;` (= DB crawl hằng ngày) nên KHÔNG chạy file nguyên trạng.
Ở đây dùng `sanitize_setup_sql` (bỏ CREATE DATABASE/USE, raise nếu có DROP DATABASE) rồi chạy các câu lệnh còn lại trên connection có default DB = DB MỚI.
Từ chối nếu DB mới đã tồn tại hoặc trùng tên DB vận hành. Không in mật khẩu. Chỉ đọc information_schema của DB gốc để đối chiếu cấu trúc (không đụng dữ liệu)."""
import sys
from pathlib import Path

BACKEND = Path("D:/MSE/CAPSTONE/hotel-price-intelligence/backend")
sys.path.insert(0, str(BACKEND))

import mysql.connector  # noqa: E402

from app.core.config import settings  # noqa: E402  (đọc backend/.env - chỉ lấy host/port/user/password)
from app.warehouse.sql_script import sanitize_setup_sql  # noqa: E402

NEW_DB = sys.argv[1] if len(sys.argv) > 1 else "hotel_price_intel_fullscan_20260924"
MAIN_DB = settings.DB_NAME
assert NEW_DB != MAIN_DB, "DB mới trùng tên DB vận hành"
print("DB vận hành (KHÔNG đụng dữ liệu):", MAIN_DB, "| DB mới:", NEW_DB, "| host:", settings.DB_HOST, settings.DB_PORT, "| user:", settings.DB_USER)

conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, autocommit=True)
cur = conn.cursor(buffered=True)
cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (NEW_DB,))
if cur.fetchone():
    raise SystemExit(f"TỪ CHỐI: database {NEW_DB} đã tồn tại - không ghi đè")
cur.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME LIKE 'hotel%%' OR SCHEMA_NAME LIKE 'warehouse%%' ORDER BY 1")
print("Các DB liên quan trên server:", [r[0] for r in cur.fetchall()])

cur.execute(f"CREATE DATABASE `{NEW_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
cur.execute(f"USE `{NEW_DB}`")
cur.execute("SELECT DATABASE()")
assert cur.fetchone()[0] == NEW_DB, "default database không phải DB mới - dừng"

text = (BACKEND / "app/database/setup.sql").read_text(encoding="utf-8")
statements = sanitize_setup_sql(text)
print("số câu lệnh DDL sau khi bỏ CREATE DATABASE/USE:", len(statements))
for i, statement in enumerate(statements, start=1):
    try:
        cur.execute(statement)
        while cur.nextset():
            pass
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"LỖI ở câu lệnh #{i}: {exc}\n{statement[:300]}")

cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY 1", (NEW_DB,))
tables_new = [r[0] for r in cur.fetchall()]
print("bảng đã tạo (%d):" % len(tables_new), tables_new)

# đối chiếu cấu trúc cột với DB gốc (chỉ information_schema)
def columns(db: str):
    cur.execute("SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION", (db,))
    return {(t, c): (ty, n) for t, c, ty, n in cur.fetchall()}

new_cols, main_cols = columns(NEW_DB), columns(MAIN_DB)
only_new = sorted(set(new_cols) - set(main_cols)); only_main = sorted(set(main_cols) - set(new_cols))
diff_type = sorted(k for k in set(new_cols) & set(main_cols) if new_cols[k] != main_cols[k])
print(f"đối chiếu cấu trúc: cột chỉ có ở DB mới={len(only_new)}, chỉ có ở DB gốc={len(only_main)}, khác kiểu/nullable={len(diff_type)}")
if only_new[:5]:
    print("  chỉ ở DB mới:", only_new[:8])
if only_main[:5]:
    print("  chỉ ở DB gốc:", only_main[:8])
if diff_type[:5]:
    print("  khác kiểu:", [(k, new_cols[k], main_cols[k]) for k in diff_type[:5]])
cur.execute(f"SELECT COUNT(*) FROM `{NEW_DB}`.hotels")
print("hotels trong DB mới (phải = 0):", cur.fetchone()[0])
cur.close(); conn.close()
print("XONG")
