"""E6 — CẶP PHIÊN CHROME ĐỒNG THỜI: trạng thái trang (dòng "Chỉ dành cho 1 khách", dòng Basic `bbasic_*`) là của PHIÊN hay của THỜI ĐIỂM? Claude, 2026-09-25/26.
Người dùng duyệt 25/09 ("ok, chạy 20 cặp phiên Chrome đi"). Thiết kế ở discuss/canonical-key-duplicates/08-claude-consolidated-evidence-report.md §5.

Mỗi CẶP = hai tiến trình Selenium riêng (mỗi tiến trình một Chrome headless MỚI, hồ sơ tạm mới ⇒ cookie mới) chạy ĐỒNG THỜI; mỗi phiên cào cùng các trang (mặc định Mercure Vũng Tàu và Roma, check-in 2026-10-11,
2 người lớn mặc định qua `build_scrape_url`) bằng đúng hàm của collector (`get_driver`, `scrape_booking_hotel`), lưu HTML.gz + ảnh. KHÔNG dùng DB, KHÔNG dùng hàng đợi, KHÔNG đụng DB vận hành / worker của người dùng.
Nếu trạng thái là của PHIÊN: hai phiên trong một cặp lệch nhau ở một phần các cặp; nếu là của THỜI ĐIỂM/hệ thống: luôn giống nhau (phân tích ở analyze_e6_pairs.py).

Chạy (venv backend):  python outputs/fullscan-20260924/scripts/e6_pairs.py --pairs 20 --deadline 00:20      (giờ máy = giờ VN; không mở cặp mới nếu còn <80 giây tới hạn)
Đầu ra: run/e6/pairNN_A.json, pairNN_B.json, e6_log.jsonl; artifact ở run/crawl_artifacts/<1000+cặp>/<item>/ (gitignore).
An toàn: dừng NGAY nếu gặp CAPTCHA/chặn; chỉ dừng tiến trình do chính script này tạo (theo PID); không bao giờ dừng theo tên chromedriver (worker của người dùng có thể đang chạy).
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("D:/MSE/CAPSTONE")
BACKEND = ROOT / "hotel-price-intelligence" / "backend"
RUN = ROOT / "outputs" / "fullscan-20260924" / "run"
ART = RUN / "crawl_artifacts"
OUT = RUN / "e6"
PAGES = [("mercure-vung-tau-vietnam", "1G"), ("roma", "basic")]
CHECKIN, CHECKOUT = "2026-10-11", "2026-10-12"
RUN_BASE = 1000


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def find_links():
    import openpyxl
    wb = openpyxl.load_workbook(ROOT / "link_hotel_data_expanded.xlsx", read_only=True)
    links = {}
    for ws in wb.worksheets:
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r and len(r) > 1 and r[1]:
                for slug, _ in PAGES:
                    if f"/hotel/vn/{slug}." in str(r[1]):
                        links[slug] = str(r[1])
    missing = [s for s, _ in PAGES if s not in links]
    if missing:
        raise SystemExit(f"không tìm thấy link cho {missing}")
    return links


def session(pair, side, links_json):
    """Một phiên: Chrome mới, cào lần lượt các trang, lưu HTML, ghi JSON."""
    os.chdir(BACKEND)
    sys.path.insert(0, str(BACKEND))
    from app.scraper.driver import get_driver
    from app.scraper.booking_scraper import scrape_booking_hotel

    links = json.loads(Path(links_json).read_text(encoding="utf-8"))
    rec = {"pair": pair, "side": side, "pid": os.getpid(), "start_utc": utc_now(), "pages": []}
    out = OUT / f"pair{pair:02d}_{side}.json"
    t = time.perf_counter()
    try:
        driver = get_driver(is_headless=True)
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"driver: {exc}"[:300]
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return
    rec["driver_start_ms"] = round((time.perf_counter() - t) * 1000)
    try:
        for i, (slug, kind) in enumerate(PAGES):
            item_id = (1 if side == "A" else 11) + i
            t0 = utc_now()
            result, fail, meta = scrape_booking_hotel(links[slug], CHECKIN, CHECKOUT, driver=driver, save_artifact=True, artifact_root=str(ART),
                                                      run_id=RUN_BASE + pair, item_id=item_id)
            code = None
            if fail is not None:
                code = str(getattr(fail.code, "value", fail.code))
            rec["pages"].append({"slug": slug, "kind": kind, "item_id": item_id, "t0": t0, "t1": utc_now(), "ok": fail is None, "fail": code,
                                 "html": meta.get("artifact_html_path"), "final_url": meta.get("final_url"),
                                 "is_sold_out": bool(result and result.get("is_sold_out")), "is_not_bookable": bool(result and result.get("is_not_bookable")),
                                 "n_rooms": len(result["rooms"]) if result else None, "page_load_ms": meta.get("page_load_ms"), "wait_ms": meta.get("availability_wait_ms")})
            if fail is not None and code and any(k in code.lower() for k in ("captcha", "block")):
                break
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            try:
                driver.service.process.kill()
            except Exception:  # noqa: BLE001
                pass
        rec["end_utc"] = utc_now()
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=20)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--deadline", default="00:20", help="HH:MM giờ máy; không mở cặp mới nếu còn <80 giây")
    ap.add_argument("--session", nargs=3, metavar=("PAIR", "SIDE", "LINKS_JSON"))
    args = ap.parse_args()
    if args.session:
        session(int(args.session[0]), args.session[1], args.session[2])
        return
    OUT.mkdir(parents=True, exist_ok=True)
    links = find_links()
    links_json = OUT / "links.json"
    links_json.write_text(json.dumps(links, ensure_ascii=False), encoding="utf-8")
    hh, mm = (int(x) for x in args.deadline.split(":"))
    now = dt.datetime.now()
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline < now - dt.timedelta(hours=1):
        deadline += dt.timedelta(days=1)
    print(f"E6: {args.pairs} cặp từ #{args.start}, hạn {deadline:%H:%M} (giờ máy {now:%H:%M:%S})", flush=True)
    logf = (OUT / "e6_log.jsonl").open("a", encoding="utf-8")
    bad_streak, done = 0, 0
    for pair in range(args.start, args.start + args.pairs):
        if dt.datetime.now() + dt.timedelta(seconds=80) > deadline:
            print(f"dừng vì hạn {deadline:%H:%M}: đã xong {done} cặp", flush=True)
            break
        t_pair = time.time()
        procs = []
        for side in ("A", "B"):
            lf = (OUT / f"pair{pair:02d}_{side}.log").open("w", encoding="utf-8")
            p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--session", str(pair), side, str(links_json)], stdout=lf, stderr=lf)
            procs.append((side, p))
            time.sleep(1.0)
        for side, p in procs:
            try:
                p.wait(timeout=300)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)   # chỉ PID do script này tạo
        recs = []
        for side in ("A", "B"):
            f = OUT / f"pair{pair:02d}_{side}.json"
            recs.append(json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"error": "không có JSON"})
        summ = {"pair": pair, "secs": round(time.time() - t_pair, 1), "sides": []}
        blocked = False
        for r in recs:
            pg = r.get("pages", [])
            summ["sides"].append({"err": r.get("error"), "pages": [(x["slug"][:8], x["ok"], x["fail"], x["n_rooms"]) for x in pg]})
            blocked |= any(x.get("fail") and any(k in x["fail"].lower() for k in ("captcha", "block")) for x in pg)
        logf.write(json.dumps(summ, ensure_ascii=False) + "\n"); logf.flush()
        ok = all(not r.get("error") and r.get("pages") and all(x["ok"] for x in r["pages"]) for r in recs)
        bad_streak = 0 if ok else bad_streak + 1
        done += 1
        print(f"cặp {pair:02d} xong {summ['secs']}s ok={ok} {summ['sides']}", flush=True)
        if blocked:
            print("GẶP CAPTCHA/CHẶN — dừng toàn bộ", flush=True)
            break
        if bad_streak >= 3:
            print("3 cặp liên tiếp lỗi — dừng để kiểm tra", flush=True)
            break
    print(f"KẾT THÚC: {done} cặp", flush=True)


if __name__ == "__main__":
    main()
