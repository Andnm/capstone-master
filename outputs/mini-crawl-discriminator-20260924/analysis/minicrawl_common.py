"""Thư viện dùng chung cho phân tích E1 (lặp cùng workbook) và E2 (paired-context 1 vs 2 người lớn). Claude, 2026-09-24.

CHỈ ĐỌC: HTML.gz trong artifact của từng run + DB cô lập tương ứng. Không ghi DB, không ghi artifact.
Logic tách dòng `tr.js-rt-block-row`, ghép DOM↔DB theo THỨ TỰ (tên phòng + giá) và các chiều nội dung là bản sao nguyên văn từ
`analyze_minicrawl_html.py` (đã được GPT tái lập ở thread canonical-key-duplicates, file 02) để hai lần phân tích so sánh được với nhau.

Dùng:  from minicrawl_common import load_run;  dom, opts = load_run(db_name, artifact_dir, run_id)
Chạy bằng venv của backend (cần bs4, lxml, pandas, mysql-connector); import này tự chdir sang backend để pydantic-settings đọc .env.
"""
import gzip
import math
import os
import re
import sys
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
RUN_ROOT = HERE.parent / "run"
BACKEND = Path("D:/MSE/CAPSTONE/hotel-price-intelligence/backend")

os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))
import mysql.connector  # noqa: E402

from app.core.config import settings  # noqa: E402

norm = lambda s: re.sub(r"\s+", " ", s or "").strip()
fmt = lambda n: f"{n:,}".replace(",", ".")

MEAL = re.compile(r"bữa sáng|bữa trưa|bữa tối|ăn sáng|breakfast", re.I)
CANCEL = re.compile(r"Hủy|Không hoàn tiền|Linh động|hoàn tiền|đổi ngày", re.I)
PAY = re.compile(r"thanh toán|thẻ tín dụng", re.I)


def cell_text(tag):
    return norm(tag.get_text(" ", strip=True)) if tag is not None else ""


def parse_rows(soup):
    """Mỗi dòng giá của bảng phòng; áp rowspan giống scraper để gán header/tên phòng."""
    trs = soup.select("tr.js-rt-block-row")
    out, header = [], None
    for idx, tr in enumerate(trs):
        th = tr.select_one("th.hprt-table-cell-roomtype")
        if th is not None:
            span = int(th.get("rowspan") or 1)
            link = th.select_one("a.hprt-roomtype-link")
            name = norm((th.select_one(".hprt-roomtype-icon-link") or link or th).get_text(" ", strip=True))
            header = {"name": name, "room_id_hdr": link.get("data-room-id") if link else None, "remaining": span, "header_row": idx}
        block_id = tr.get("data-block-id") or ""
        price_attr = tr.get("data-hotel-rounded-price")
        occ_td = tr.select_one("td.hprt-table-cell-occupancy")
        cond_td = tr.select_one("td.hprt-table-cell-conditions")
        price_td = tr.select_one("td.hprt-table-cell-price")
        conds = [norm(x.get_text(" ", strip=True)) for x in (cond_td.select(".bui-list__item") if cond_td else [])] or ([cell_text(cond_td)] if cond_td else [])
        occ_txt = cell_text(occ_td)
        m_occ = re.search(r"Số người tối đa:\s*(\d+)", occ_txt)
        rec = {"dom_idx": idx, "block_id": block_id, "price_attr": int(price_attr) if price_attr and price_attr.isdigit() else None, "is_basic": "bbasic" in block_id,
               "has_occ_cell": occ_td is not None, "occ_text": occ_txt,
               "occ_max": int(m_occ.group(1)) if m_occ else None, "only_for": (re.search(r"Chỉ dành cho[^\n]*", occ_txt).group(0) if "Chỉ dành cho" in occ_txt else None),
               "conditions": conds, "price_text": cell_text(price_td), "badges": [norm(x.get_text(" ", strip=True)) for x in (price_td.select(".bui-badge") if price_td else [])]}
        if header is not None and block_id:
            rec.update({"room_name": header["name"], "room_id_hdr": header["room_id_hdr"], "block_header_row": header["header_row"]})
            header["remaining"] -= 1
            if header["remaining"] <= 0:
                header = None
        else:
            rec.update({"room_name": None, "room_id_hdr": None, "block_header_row": None})
        parts = block_id.split("_") if block_id else []
        rec.update({"bid_room": parts[0] if parts else None, "bid_rate": parts[1] if len(parts) > 1 else None, "bid_occ": parts[2] if len(parts) > 2 else None,
                    "bid_meal": parts[3] if len(parts) > 3 else None, "bid_pkg": "_".join(parts[5:]) if len(parts) > 5 else None})
        out.append(rec)
    return out


def split_conditions(conds):
    meal = [c for c in conds if MEAL.search(c) and not CANCEL.search(c)]
    cancel = [c for c in conds if CANCEL.search(c)]
    pay = [c for c in conds if PAY.search(c) and not CANCEL.search(c) and not MEAL.search(c)]
    used_ = set(meal) | set(cancel) | set(pay)
    perks = [c for c in conds if c not in used_]
    j = lambda xs: " | ".join(sorted(xs))
    return j(meal), j(cancel), j(pay), j(perks)


def connect(db_name):
    return mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=db_name, autocommit=True)


def load_run(db_name, art_dir, run_id):
    """Trả (dom, opts): dom = mọi dòng DOM (cờ matched), opts = mọi option đã lưu kèm cột dom_* và các chiều nội dung."""
    art_dir = Path(art_dir)
    conn = connect(db_name)
    cur = conn.cursor(dictionary=True, buffered=True)
    cur.execute("SELECT id, hotel_id, checkin_date, status, duplicate_options_count, hotel_link, requested_hotel_link FROM crawl_run_items WHERE crawl_run_id=%s ORDER BY id", (run_id,))
    all_items = cur.fetchall()
    dom_all, db_all = [], []
    for it in (i for i in all_items if i["status"] == "success"):
        iid = it["id"]
        html = gzip.open(art_dir / str(run_id) / str(iid) / "page.html.gz", "rb").read().decode("utf-8", errors="replace")
        rows = [r for r in parse_rows(BeautifulSoup(html, "lxml")) if r["room_name"]]
        cur.execute("SELECT room_option_index, room_type_raw, price_per_night, original_price, discount_percent, rooms_left, breakfast_included, free_cancellation, cancellation_policy, max_occupancy, "
                    "LEFT(room_identity_key, 8) rik, LEFT(rate_plan_key, 8) rpk FROM price_observations WHERE crawl_run_item_id=%s ORDER BY room_option_index", (iid,))
        dbr = cur.fetchall()
        used, matched, kind = set(), {}, {}
        for d in dbr:                                              # lượt 1: tên + data-hotel-rounded-price
            p = int(d["price_per_night"])
            for r in rows:
                if r["dom_idx"] not in used and r["price_attr"] == p and r["room_name"] == norm(d["room_type_raw"]):
                    used.add(r["dom_idx"]); matched[d["room_option_index"]] = r["dom_idx"]; kind[d["room_option_index"]] = "price_attr"; break
        for d in dbr:                                              # lượt 2: dòng bbasic -> số tiền trong ô giá
            if d["room_option_index"] in matched:
                continue
            p = int(d["price_per_night"])
            for r in rows:
                if r["dom_idx"] not in used and r["room_name"] == norm(d["room_type_raw"]) and re.search(r"(?<![\d.])" + re.escape(fmt(p)) + r"(?![\d.])", r["price_text"] or ""):
                    used.add(r["dom_idx"]); matched[d["room_option_index"]] = r["dom_idx"]; kind[d["room_option_index"]] = "price_text"; break
        by_idx = {r["dom_idx"]: r for r in rows}
        for r in rows:
            dom_all.append({**r, "item_id": iid, "hotel_id": it["hotel_id"], "checkin": str(it["checkin_date"]), "matched": r["dom_idx"] in used})
        for d in dbr:
            rec = {**d, "item_id": iid, "hotel_id": it["hotel_id"], "checkin": str(it["checkin_date"]), "match_kind": kind.get(d["room_option_index"])}
            j = matched.get(d["room_option_index"])
            if j is not None:
                rec.update({"dom_" + k: v for k, v in by_idx[j].items()})
            db_all.append(rec)
    cur.close(); conn.close()
    dom, db = pd.DataFrame(dom_all), pd.DataFrame(db_all)
    db["price"] = db["price_per_night"].astype(float)
    parts = db["dom_conditions"].apply(lambda c: split_conditions(c) if isinstance(c, (list, tuple)) else ("", "", "", ""))
    db["meal"], db["cancel"], db["pay"], db["perks"] = [parts.str[i] for i in range(4)]
    db["single_guest"] = db["dom_only_for"].fillna("").str.contains("Chỉ dành cho 1", regex=False) | (db["dom_bid_occ"].fillna("") == "1")
    db["g0"] = db.groupby(["item_id", "rik", "rpk"]).ngroup()
    db["g0_size"] = db.groupby("g0")["price"].transform("size")
    return dom, db, all_items


def wilson_lower(k, n, z=1.96):
    if n == 0:
        return float("nan")
    p = k / n
    d = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d
