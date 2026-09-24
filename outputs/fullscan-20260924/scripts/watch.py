"""Vòng theo dõi mini-crawl cho Monitor (Claude, 2026-09-24): mỗi dòng stdout = 1 sự kiện. Chỉ đọc API mini (8010). Kết thúc khi run ở trạng thái cuối."""
import datetime as dt
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import Counter

RUN_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
INTERVAL = int(sys.argv[2]) if len(sys.argv) > 2 else 90
QUIET = len(sys.argv) > 3 and sys.argv[3] == "quiet"   # quiet: chi in moc 25/50/75% + ALERT + ket thuc
MILESTONES = {27, 41}
printed_milestones: set = set()
BASE = os.environ.get("MINI_BASE", "http://127.0.0.1:8012")
TERMINAL_RUN = {"completed", "failed", "cancelled", "canceled"}


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
    return (ts + dt.timedelta(hours=7)).strftime("%H:%M:%S")


seen_bad: set = set()
last_done = -1
stall_since = time.time()
while True:
    try:
        run = get(f"/api/scraper/runs/{RUN_ID}")
        items = all_items()
        health = get("/api/scraper/worker/health")
    except Exception as exc:  # noqa: BLE001
        print(f"ALERT không đọc được API mini: {exc}", flush=True)
        time.sleep(INTERVAL)
        continue
    now = dt.datetime.now(dt.timezone.utc)
    counts = Counter(i["status"] for i in items)
    terminal = [i for i in items if i["status"] not in ("queued", "running")]
    done_n = len(terminal)
    finished = sorted(parse(i["finished_at"]) for i in terminal if i["finished_at"])
    started = parse(run["started_at"])
    eta_txt = "?"
    if done_n >= 3 and started:
        gaps = [(b - a).total_seconds() for a, b in zip(finished, finished[1:])][-10:]
        per_recent = statistics.mean(gaps) if gaps else 0
        per_all = (finished[-1] - started).total_seconds() / done_n
        remaining = run["total"] - done_n
        eta = now + dt.timedelta(seconds=remaining * per_recent)
        eta_txt = f"{per_all:.0f}s/item (10 gần nhất {per_recent:.0f}s) → còn {remaining} item ≈ {remaining * per_recent / 60:.0f} phút, xong ~{vn(eta)} VN"
    hit = [m for m in MILESTONES if done_n >= m and m not in printed_milestones]
    printed_milestones.update(hit)
    if not QUIET or hit:
        print(f"[{vn(now)}] {done_n}/{run['total']} xong | {dict(counts)} | err={run['error_count']} sold_out={run['sold_out_count']} not_bookable={run['not_bookable_count']} partial={run['partial_count']} | {eta_txt} | worker={health['status']} hb={health['heartbeat_age_seconds']}s", flush=True)
    for i in terminal:
        key = (i["id"], i["status"], i["attempt_count"])
        if (i["status"] in ("error", "partial") or (i["attempt_count"] or 0) > 1) and key not in seen_bad:
            seen_bad.add(key)
            print(f"ALERT item {i['id']} {i['hotel_id'] or i['hotel_name_hint']} {i['checkin_date']}: status={i['status']} attempts={i['attempt_count']} code={i['last_error_code']} msg={(i['error_message'] or '')[:100]}", flush=True)
    if health["heartbeat_age_seconds"] is not None and health["heartbeat_age_seconds"] > 90:
        print(f"ALERT heartbeat worker cũ {health['heartbeat_age_seconds']}s (status={health['status']}, waiting_for_network={health['waiting_for_network']})", flush=True)
    if done_n != last_done:
        last_done, stall_since = done_n, time.time()
    elif time.time() - stall_since > 240 and run["status"] not in TERMINAL_RUN:
        print(f"ALERT không có item nào hoàn tất trong {int(time.time() - stall_since)}s (đang chạy: {[i['id'] for i in items if i['status'] == 'running']})", flush=True)
    if run["status"] in TERMINAL_RUN:
        print(f"KẾT THÚC run #{RUN_ID} = {run['status']} | success={run['success_count']} partial={run['partial_count']} sold_out={run['sold_out_count']} not_bookable={run['not_bookable_count']} error={run['error_count']} | finished_at={run['finished_at']}", flush=True)
        break
    time.sleep(INTERVAL)
