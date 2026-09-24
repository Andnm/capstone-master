"""Theo dõi tiến độ mini-crawl (Claude, 2026-09-24): chỉ ĐỌC qua API mini (cổng 8010) + đếm file artifact. Không ghi gì."""
import datetime as dt
import json
import os
import statistics
import sys
import urllib.request
from collections import Counter
from pathlib import Path

RUN_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
BASE = os.environ.get("MINI_BASE", "http://127.0.0.1:8012")
ART = Path(os.environ.get("MINI_ART", "D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/crawl_artifacts"))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.load(r)


def all_items():
    """Đọc MỌI trang của danh sách item (API giới hạn 200/trang; run quét toàn cohort có 354 mục)."""
    out, off = [], 0
    while True:
        page = get(f"/api/scraper/runs/{RUN_ID}/items?limit=200&offset={off}")
        out += page["items"]
        off += 200
        if not page["items"] or off >= page.get("total", len(out)):
            return out


def parse(ts):
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc) if ts else None


def vn(ts):
    return (ts + dt.timedelta(hours=7)).strftime("%H:%M:%S") if ts else "-"


run = get(f"/api/scraper/runs/{RUN_ID}")
items = all_items()
health = get("/api/scraper/worker/health")
now = dt.datetime.now(dt.timezone.utc)
counts = Counter(i["status"] for i in items)
terminal = [i for i in items if i["status"] not in ("queued", "running")]
finished = sorted((parse(i["finished_at"]) for i in terminal if i["finished_at"]))
started = parse(run["started_at"])
print(f"[{vn(now)} VN] run #{run['id']} = {run['status']} | {run['processed']}/{run['total']} xong | {dict(counts)} | lỗi={run['error_count']}, sold_out={run['sold_out_count']}, not_bookable={run['not_bookable_count']}, partial={run['partial_count']}")
print(f"worker: online={health['online']} status={health['status']} heartbeat_age={health['heartbeat_age_seconds']}s current_item={health['current_item_id']} waiting_for_network={health['waiting_for_network']}")
if started:
    print(f"bắt đầu {vn(started)} VN | đã chạy {(now - started).total_seconds() / 60:.1f} phút")
done_n = len(terminal)
if done_n >= 2 and finished:
    elapsed_first_last = (finished[-1] - started).total_seconds() if started else 0
    avg_all = elapsed_first_last / done_n
    gaps = [(b - a).total_seconds() for a, b in zip(finished, finished[1:])]
    recent = gaps[-10:] if gaps else []
    avg_recent = statistics.mean(recent) if recent else avg_all
    remaining = run["total"] - done_n
    print(f"tốc độ: trung bình toàn run {avg_all:.1f}s/item | 10 item gần nhất {avg_recent:.1f}s/item (median {statistics.median(recent):.1f}s)" if recent else f"tốc độ: {avg_all:.1f}s/item")
    for label, per in (("theo trung bình toàn run", avg_all), ("theo 10 item gần nhất", avg_recent)):
        eta = now + dt.timedelta(seconds=remaining * per)
        print(f"  ETA {label}: còn {remaining} item × {per:.1f}s = {remaining * per / 60:.1f} phút → xong khoảng {vn(eta)} VN")
if terminal:
    ms = [i["item_total_ms"] for i in terminal if i.get("item_total_ms")]
    if ms:
        print(f"item_total_ms (chỉ phần xử lý): median {statistics.median(ms) / 1000:.1f}s, max {max(ms) / 1000:.1f}s")
    opts = [i["saved_options_count"] for i in terminal if i.get("saved_options_count") is not None]
    if opts:
        print(f"option đã lưu/item: tổng {sum(opts)}, trung bình {statistics.mean(opts):.1f}")
    bad = [(i["hotel_id"] or i["hotel_name_hint"], i["checkin_date"], i["status"], i["last_error_code"], (i["error_message"] or "")[:80]) for i in terminal if i["status"] in ("error", "partial")]
    if bad:
        print("item lỗi/partial:", bad[:8])
    retried = [(i["hotel_id"], i["checkin_date"], i["attempt_count"]) for i in items if (i["attempt_count"] or 0) > 1]
    if retried:
        print("item phải thử lại:", retried[:8])
    cap = [i for i in terminal if i.get("last_error_code") and any(k in str(i["last_error_code"]).lower() for k in ("captcha", "block"))]
    if cap:
        print("!!! CAPTCHA/BLOCK:", [(i["hotel_id"], i["checkin_date"], i["last_error_code"]) for i in cap])
n_html = sum(1 for _ in ART.glob("*/*/page.html.gz")); n_png = sum(1 for _ in ART.glob("*/*/page.png"))
size_mb = sum(p.stat().st_size for p in ART.rglob("*") if p.is_file()) / 1e6
print(f"artifact: {n_html} html.gz + {n_png} png, tổng {size_mb:.1f} MB")
running = [i for i in items if i["status"] == "running"]
for i in running:
    print(f"đang chạy: item {i['id']} {i['hotel_name_hint']} {i['checkin_date']} (claim {vn(parse(i['claimed_at']))})")
