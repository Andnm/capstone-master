"""Canary phiên: từ HTML của run canary (Mercure, check-in 2026-10-11) xác định phiên Chrome đang BẬT hay TẮT. Claude, 2026-09-25.

BẬT = trang có dòng "Chỉ dành cho 1 khách" (Mercure 2026-10-11: phiên BẬT 54 dòng, 27 dòng cảnh báo, 54 ô số người — mini-crawl run #1 và #3;
phiên TẮT 27 dòng, 0, 0 — run #2 và fullscan run #1). Quy ước: cảnh báo >= 5 -> BẬT; 0 -> TẮT; 1..4 hoặc mục không success -> KHÔNG RÕ.

Chạy (venv backend, PYTHONIOENCODING=utf-8):  python scripts/canary_check.py <RUN_ID> [<lần thử>]
In một dòng STATE=ON|OFF|UNKNOWN, ghi thêm một dòng vào run/canary_log.md, thoát mã 0 (BẬT) / 10 (TẮT) / 20 (KHÔNG RÕ).
Chỉ ĐỌC DB + HTML; không ghi gì ngoài canary_log.md.
"""
import gzip
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
from minicrawl_common import connect, parse_rows  # noqa: E402  (chdir sang backend để đọc .env)
from bs4 import BeautifulSoup  # noqa: E402

DB = "hotel_price_intel_fullscan_20260924"
RUN_DIR = Path("D:/MSE/CAPSTONE/outputs/fullscan-20260924/run")
ART = RUN_DIR / "crawl_artifacts"
LOG = RUN_DIR / "canary_log.md"

run_id = int(sys.argv[1])
attempt = sys.argv[2] if len(sys.argv) > 2 else "?"
conn = connect(DB)
cur = conn.cursor(dictionary=True, buffered=True)
cur.execute("SET time_zone='+00:00'")
cur.execute("SELECT id, hotel_id, checkin_date, status, driver_start_ms FROM crawl_run_items WHERE crawl_run_id=%s ORDER BY id", (run_id,))
items = cur.fetchall()
if not items:
    print(f"run {run_id}: không có mục nào")
    sys.exit(20)
it = items[0]
n = alert = bid1 = occ = 0
state, why = "UNKNOWN", ""
if it["status"] != "success":
    why = f"mục {it['status']}"
else:
    html = gzip.open(ART / str(run_id) / str(it["id"]) / "page.html.gz", "rb").read().decode("utf-8", errors="replace")
    rows = [r for r in parse_rows(BeautifulSoup(html, "lxml")) if r["room_name"]]
    n = len(rows)
    alert = sum(1 for r in rows if "Chỉ dành cho 1" in (r["only_for"] or ""))
    bid1 = sum(1 for r in rows if r["bid_occ"] == "1")
    occ = sum(1 for r in rows if r["has_occ_cell"])
    if n == 0:
        why = "không có dòng bảng phòng"
    elif alert >= 5:
        state = "ON"
    elif alert == 0:
        state = "OFF"
        why = "0 dòng 1 khách, " + ("còn ô số người" if occ else "trang gọn (không ô số người)")
    else:
        why = f"chỉ {alert} dòng 1 khách"
vn = datetime.now(timezone(timedelta(hours=7)))
label = {"ON": "BẬT", "OFF": "TẮT", "UNKNOWN": "KHÔNG RÕ"}[state]
if not LOG.exists():
    LOG.write_text("# Nhật ký canary phiên (Mercure, check-in 2026-10-11, 2 người lớn mặc định)\n\n"
                   "| lần thử | giờ VN | run | mục | driver_start_ms | dòng bảng | dòng \"1 khách\" | block-id thành phần 3 = 1 | ô số người | kết luận |\n"
                   "|---|---|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
with LOG.open("a", encoding="utf-8") as f:
    f.write(f"| {attempt} | {vn:%Y-%m-%d %H:%M:%S} | {run_id} | {it['status']} | {it['driver_start_ms']} | {n} | {alert} | {bid1} | {occ} | **{label}** {why} |\n")
print(f"run {run_id} mục {it['id']} ({it['hotel_id']} {it['checkin_date']}) status={it['status']} driver_start_ms={it['driver_start_ms']} | "
      f"dòng bảng={n} dòng '1 khách'={alert} block-id comp3=1: {bid1} ô số người={occ} {why}")
print(f"STATE={state}")
sys.exit({"ON": 0, "OFF": 10, "UNKNOWN": 20}[state])
