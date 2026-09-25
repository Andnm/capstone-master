"""Theo dõi trạng thái BẬT/TẮT theo từng mục của 10 hotel hay có dòng "Chỉ dành cho 1 khách" NGAY TRONG một lần quét (đọc HTML thật, không dùng proxy nhóm trùng).
Claude, 2026-09-25. Chỉ ĐỌC (DB cô lập + artifact). Mỗi mục: giờ claim (VN), số dòng bảng, số dòng "1 khách", số dòng có ô số người.

Chạy (venv backend, PYTHONIOENCODING=utf-8):  python scripts/hot_state.py <RUN_ID>
Mục đích: xem trạng thái có đổi GIỮA LƯỢT QUÉT hay không (10 hotel này nằm rải rác trong hàng đợi: HCM đầu, Hà Nội, Vũng Tàu, Đà Lạt, Phú Quốc cuối).
"""
import gzip
import sys
from pathlib import Path

sys.path.insert(0, "D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
from minicrawl_common import connect, parse_rows  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

DB = "hotel_price_intel_fullscan_20260924"
ART = Path("D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/crawl_artifacts")
HOT = ["hilton-saigon", "the-myst-dong-khoi", "mercure-vung-tau-vietnam", "ibis-styles-vung-tau", "dusit-le-palais-tu-hoa-hanoi", "movenpick-residences-phu-quoc",
       "starview-villa", "pearl-wealth-da-lat", "vinhomes-ocean-park-bunnys-homes-nature-room", "b-amp-k-homestay-glocery-store"]
run_id = int(sys.argv[1])
conn = connect(DB)
cur = conn.cursor(dictionary=True, buffered=True)
cur.execute("SET time_zone='+00:00'")
ph = ",".join(["%s"] * len(HOT))
cur.execute(f"SELECT id, hotel_id, checkin_date, status, claimed_at, driver_start_ms FROM crawl_run_items WHERE crawl_run_id=%s AND hotel_id IN ({ph}) ORDER BY claimed_at, id", [run_id] + HOT)
its = cur.fetchall()
cur.execute("SELECT MIN(claimed_at) t0, SUM(driver_start_ms>0) starts, COUNT(*) n FROM crawl_run_items WHERE crawl_run_id=%s AND claimed_at IS NOT NULL", (run_id,))
meta = cur.fetchone()
print(f"run {run_id}: {meta['n']} mục đã claim, {int(meta['starts'] or 0)} lần khởi động Chrome trong run")
cur_hotel, line, tot = None, [], {}
def flush():
    if cur_hotel is not None and line:
        print(f"  {cur_hotel[:24]:24} " + " | ".join(line))
for x in its:
    t = (x["claimed_at"].replace(microsecond=0) if x["claimed_at"] else None)
    hhmm = f"{(t.hour + 7) % 24:02d}:{t.minute:02d}" if t else "--:--"
    if x["hotel_id"] != cur_hotel:
        flush(); cur_hotel, line = x["hotel_id"], []
    if x["status"] != "success":
        line.append(f"{hhmm} {x['status']}")
        continue
    p = ART / str(run_id) / str(x["id"]) / "page.html.gz"
    if not p.exists():
        line.append(f"{hhmm} (thiếu HTML)")
        continue
    rows = [r for r in parse_rows(BeautifulSoup(gzip.open(p, "rb").read().decode("utf-8", errors="replace"), "lxml")) if r["room_name"]]
    alert = sum(1 for r in rows if "Chỉ dành cho 1" in (r["only_for"] or ""))
    occ = sum(1 for r in rows if r["has_occ_cell"])
    line.append(f"{hhmm} {len(rows)}d/{alert}m/{occ}o")
    tot.setdefault(x["hotel_id"], []).append(alert)
flush()
print("(mỗi ô: giờ claim VN, số dòng bảng 'd', số dòng '1 khách' 'm', số dòng có ô số người 'o')")
