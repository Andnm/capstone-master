"""Phân tích HTML mini-crawl: nhóm trùng canonical key khác nhau ở đâu? (Claude, 2026-09-24)

CHỈ ĐỌC: HTML.gz trong run/crawl_artifacts + DB mini `hotel_price_intel_minicrawl_20260924` (run_id = 1). Ghi 2 CSV vào thư mục này.
Chạy (cwd bất kỳ, dùng venv của backend vì cần bs4/lxml/pandas/mysql-connector):
    backend\\venv\\Scripts\\python.exe analysis\\analyze_minicrawl_html.py

Các bước: (1) tách từng dòng `tr.js-rt-block-row` của bảng phòng, áp dụng rowspan giống hệt scraper để gán header/tên phòng; (2) ghép 1-1 với option đã lưu trong DB
(theo tên phòng + giá; dòng `bbasic` không có data-hotel-rounded-price nên ghép theo số tiền trong ô giá); (3) đối chiếu: số dòng DOM = option đã lưu + duplicate tuyệt đối bị scraper bỏ;
(4) nhóm trùng = (item, room_identity_key, rate_plan_key) có ≥ 2 option; xem chiều nào khác nhau: định danh (data-block-id) và nội dung hiển thị (số khách/bữa ăn/hủy/thanh toán/tiện ích).
"""
import gzip
import itertools
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
RUN_DIR = HERE.parent / "run"
ART = RUN_DIR / "crawl_artifacts"
BACKEND = Path("D:/MSE/CAPSTONE/hotel-price-intelligence/backend")
MINI_DB = "hotel_price_intel_minicrawl_20260924"
RUN_ID = 1

os.chdir(BACKEND)                      # để pydantic-settings đọc backend/.env (chỉ lấy host/user/password)
sys.path.insert(0, str(BACKEND))
import mysql.connector  # noqa: E402

from app.core.config import settings  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200); pd.set_option("display.max_colwidth", 90)
norm = lambda s: re.sub(r"\s+", " ", s or "").strip()


# ------------------------------------------------------------------ 1. tách dòng bảng phòng từ HTML
def cell_text(tag):
    return norm(tag.get_text(" ", strip=True)) if tag is not None else ""


def parse_rows(soup):
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


conn = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=MINI_DB, autocommit=True)
cur = conn.cursor(dictionary=True, buffered=True)
cur.execute("SELECT id, hotel_id, checkin_date, status, duplicate_options_count FROM crawl_run_items WHERE crawl_run_id=%s AND status='success' ORDER BY id", (RUN_ID,))
items = cur.fetchall()

dom_all, db_all = [], []
for it in items:
    iid = it["id"]
    html = gzip.open(ART / str(RUN_ID) / str(iid) / "page.html.gz", "rb").read().decode("utf-8", errors="replace")
    rows = [r for r in parse_rows(BeautifulSoup(html, "lxml")) if r["room_name"]]
    cur.execute("SELECT room_option_index, room_type_raw, price_per_night, original_price, discount_percent, breakfast_included, free_cancellation, max_occupancy, "
                "LEFT(room_identity_key, 8) rik, LEFT(rate_plan_key, 8) rpk FROM price_observations WHERE crawl_run_item_id=%s ORDER BY room_option_index", (iid,))
    dbr = cur.fetchall()
    used, matched, kind = set(), {}, {}
    fmt = lambda n: f"{n:,}".replace(",", ".")
    for d in dbr:                                              # lượt 1: tên + data-hotel-rounded-price
        p = int(d["price_per_night"])
        for r in rows:
            if r["dom_idx"] not in used and r["price_attr"] == p and r["room_name"] == norm(d["room_type_raw"]):
                used.add(r["dom_idx"]); matched[d["room_option_index"]] = r["dom_idx"]; kind[d["room_option_index"]] = "price_attr"; break
    for d in dbr:                                              # lượt 2: dòng bbasic (không có attr) -> số tiền trong ô giá
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
dom, db = pd.DataFrame(dom_all), pd.DataFrame(db_all)
db["price"] = db["price_per_night"].astype(float)

print("=" * 100)
print("1. ĐỐI CHIẾU DOM ↔ DB")
n_dropped = int((~dom["matched"]).sum())
print(f"  item thành công: {len(items)} | dòng DOM (có header): {len(dom)} | option đã lưu trong DB: {len(db)} | ghép 1-1 được: {int(db['dom_dom_idx'].notna().sum())} ({db['dom_dom_idx'].notna().mean():.1%})")
print(f"  dòng DOM không khớp DB (= duplicate tuyệt đối scraper bỏ): {n_dropped} | tổng duplicate_options_count trong DB: {sum(i['duplicate_options_count'] or 0 for i in items)}"
      f" | dòng bbasic: {int(dom['is_basic'].sum())} | hotel có dòng bị bỏ: {sorted(set(dom.loc[~dom['matched'], 'hotel_id']))}")
assert db["dom_dom_idx"].notna().all(), "còn option DB chưa ghép được DOM"
assert len(dom) == len(db) + n_dropped, "DOM ≠ DB + dropped"


# ------------------------------------------------------------------ 2. đặc trưng nội dung hiển thị
MEAL = re.compile(r"bữa sáng|bữa trưa|bữa tối|ăn sáng|breakfast", re.I)
CANCEL = re.compile(r"Hủy|Không hoàn tiền|Linh động|hoàn tiền|đổi ngày", re.I)
PAY = re.compile(r"thanh toán|thẻ tín dụng", re.I)


def split_conditions(conds):
    meal = [c for c in conds if MEAL.search(c) and not CANCEL.search(c)]
    cancel = [c for c in conds if CANCEL.search(c)]
    pay = [c for c in conds if PAY.search(c) and not CANCEL.search(c) and not MEAL.search(c)]
    used_ = set(meal) | set(cancel) | set(pay)
    perks = [c for c in conds if c not in used_]
    j = lambda xs: " | ".join(sorted(xs))
    return j(meal), j(cancel), j(pay), j(perks)


parts = db["dom_conditions"].apply(split_conditions)
db["meal"], db["cancel"], db["pay"], db["perks"] = [parts.str[i] for i in range(4)]
db["single_guest"] = db["dom_only_for"].fillna("").str.contains("Chỉ dành cho 1", regex=False) | (db["dom_bid_occ"].fillna("") == "1")
db["room_id"] = db["dom_room_id_hdr"].fillna("").astype(str)
db["badges_text"] = db["dom_badges"].apply(lambda b: " | ".join(sorted(b)) if isinstance(b, (list, tuple)) else "")

db["g0"] = db.groupby(["item_id", "rik", "rpk"]).ngroup()
db["g0_size"] = db.groupby("g0")["price"].transform("size")
dups = db[db["g0_size"] >= 2].copy()
G = dups["g0"].nunique()
print("\n" + "=" * 100)
print("2. NHÓM TRÙNG CANONICAL KEY (item × room_identity_key × rate_plan_key, ≥ 2 option)")
print(f"  {G} nhóm, {len(dups)} option = {len(dups) / len(db):.1%} option đã lưu | kích thước nhóm: {dups.groupby('g0').size().value_counts().sort_index().to_dict()}")

print("\n  -- Định danh trong data-block-id (roomId_rateId_occupancy_meal_flag[_packageId]) khác nhau trong nhóm:")
agg = dups.groupby("g0").agg(hotel=("hotel_id", "first"), checkin=("checkin", "first"), item_id=("item_id", "first"), n=("price", "size"), pmin=("price", "min"), pmax=("price", "max"),
                             n_room=("room_id", "nunique"), n_rate=("dom_bid_rate", "nunique"), n_occ=("dom_bid_occ", "nunique"), n_meal=("dom_bid_meal", "nunique"),
                             n_pkg=("dom_bid_pkg", lambda s: s.fillna("").nunique()), n_bid=("dom_block_id", "nunique")).reset_index()
agg["ratio"] = agg["pmax"] / agg["pmin"]
for label, col in (("room id (căn/loại phòng)", "n_room"), ("rate id", "n_rate"), ("số khách của dòng giá (thành phần 3)", "n_occ"), ("mã bữa ăn (thành phần 4)", "n_meal"), ("gói/extra id (hậu tố)", "n_pkg")):
    print(f"    {label:40} khác trong {int((agg[col] > 1).sum()):>4} nhóm ({(agg[col] > 1).mean():5.1%})")
print(f"    mọi option trong nhóm có data-block-id khác nhau: {int((agg['n_bid'] == agg['n']).sum())}/{G}")

dims = ["single_guest", "meal", "cancel", "pay", "perks"]
lab = {"single_guest": "1 khách", "meal": "bữa ăn", "cancel": "hủy", "pay": "thanh toán", "perks": "tiện ích/gói"}
print("\n  -- Nội dung hiển thị khác nhau trong nhóm:")
diffsets = {}
for gid, x in dups.groupby("g0"):
    diffsets[gid] = [d for d in dims if x[d].astype(str).nunique() > 1]
for d in dims:
    k = sum(1 for s in diffsets.values() if d in s)
    print(f"    {lab[d]:14} khác trong {k:>4} nhóm ({k / G:5.1%})")
agg["dims"] = agg["g0"].map(lambda g: " + ".join(lab[d] for d in diffsets[g]) or "(không khác gì)")
tab = agg.groupby("dims").agg(nhóm=("g0", "size"), median_ratio_giá=("ratio", "median")).sort_values("nhóm", ascending=False)
tab["tỉ_lệ"] = (tab["nhóm"] / G).round(3)
print("\n  -- Tổ hợp chiều khác nhau (mỗi nhóm đúng 1 dòng):")
print(tab.head(12).to_string())

print("\n" + "=" * 100)
print("3. DÒNG 'CHỈ DÀNH CHO 1 KHÁCH' (giá cho 1 khách trong tìm kiếm 2 người lớn)")
print(f"  {int(db['single_guest'].sum())}/{len(db)} option đã lưu = {db['single_guest'].mean():.1%}; max_occupancy đã lưu trong DB (lấy từ dòng đầu block) của các dòng này: {db.loc[db.single_guest, 'max_occupancy'].value_counts().to_dict()}")
print("  tỉ lệ theo hotel:", db.groupby("hotel_id")["single_guest"].mean().round(2).sort_values(ascending=False).to_dict())
mixed = dups.groupby("g0").filter(lambda x: x["single_guest"].any() and (~x["single_guest"]).any())
cheapest = mixed.loc[mixed.groupby("g0")["price"].idxmin()]["single_guest"]
print(f"  nhóm trùng chứa CẢ dòng 1 khách và dòng khác: {mixed['g0'].nunique()} ({mixed['g0'].nunique() / G:.1%}); option RẺ NHẤT là dòng 1 khách trong {cheapest.mean():.1%} các nhóm đó")

print("\n" + "=" * 100)
print("4. TINH CHỈNH KHÓA BẰNG NỘI DUNG HIỂN THỊ: còn bao nhiêu nhóm trùng?")
keys = ["item_id", "rik", "rpk"]
print(f"  khóa hiện tại (room key + rate key)   : {int((db.groupby(keys).size() >= 2).sum())} nhóm trùng")
dbs = db.assign(**{k: db[k].astype(str) for k in dims})
for d in dims:
    keys = keys + [d]
    sizes = dbs.groupby(keys).size()
    print(f"  + {'/'.join(lab[k] for k in keys[3:]):50}: {int((sizes >= 2).sum()):>4} nhóm trùng ({int(sizes[sizes >= 2].sum())} option)")

print("\n" + "=" * 100)
print("5. HIỆU ỨNG GIÁ CỦA TỪNG CHIỀU (cặp option trong nhóm chỉ khác đúng MỘT chiều; ratio = giá cao / giá thấp)")
prs = []
for gid, x in dups.groupby("g0"):
    for a, b in itertools.combinations(x.to_dict("records"), 2):
        diff = [lab[d] for d in dims if str(a[d]) != str(b[d])]
        lo, hi = sorted([a["price"], b["price"]])
        prs.append({"diff": " + ".join(diff) or "(không khác gì)", "n_diff": len(diff), "ratio": hi / lo})
pairs = pd.DataFrame(prs)
print(pairs[pairs.n_diff == 1].groupby("diff")["ratio"].describe(percentiles=[0.25, 0.5, 0.75])[["count", "25%", "50%", "75%", "max"]].round(3).to_string())
print(f"  cặp khác đúng 1 chiều: {int((pairs.n_diff == 1).sum())} | khác ≥ 2 chiều: {int((pairs.n_diff >= 2).sum())} | không khác chiều nào: {int((pairs.n_diff == 0).sum())} | tổng cặp: {len(pairs)}")

print("\n" + "=" * 100)
print("6. ỔN ĐỊNH ĐỊNH DANH GIỮA 3 NGÀY CHECK-IN (cùng thời điểm cào)")
res = []
for h, x in db.groupby("hotel_id"):
    sets = {d: set(x[x.checkin == d]["dom_block_id"]) for d in sorted(x["checkin"].unique())}
    if len(sets) < 3:
        continue
    allb = set.union(*sets.values())
    res.append({"hotel": h, "block-id đủ 3 ngày": round(len(set.intersection(*sets.values())) / len(allb), 2)})
r = pd.DataFrame(res)
print(f"  trung bình theo hotel: {r.iloc[:, 1].mean():.2f} | min {r.iloc[:, 1].min():.2f} | max {r.iloc[:, 1].max():.2f}")

# ------------------------------------------------------------------ 7. ghi CSV
cols = ["item_id", "hotel_id", "checkin", "room_option_index", "room_type_raw", "price_per_night", "original_price", "discount_percent", "breakfast_included", "free_cancellation", "max_occupancy",
        "rik", "rpk", "g0", "g0_size", "dom_dom_idx", "dom_block_id", "dom_bid_room", "dom_bid_rate", "dom_bid_occ", "dom_bid_meal", "dom_bid_pkg", "dom_occ_max", "dom_only_for", "single_guest",
        "meal", "cancel", "pay", "perks", "badges_text", "match_kind"]
db[cols].to_csv(HERE / "options_dom_matched.csv", index=False, encoding="utf-8-sig")
agg.to_csv(HERE / "dup_groups.csv", index=False, encoding="utf-8-sig")
print(f"\nĐã ghi {HERE / 'options_dom_matched.csv'} ({len(db)} dòng) và {HERE / 'dup_groups.csv'} ({len(agg)} nhóm)")

# ------------------------------------------------------------------ 8. thử heuristic CHỈ-DỰA-GIÁ nhận diện dòng 1 khách (để xem có sửa được lịch sử không)
print("\n" + "=" * 100)
print("8. HEURISTIC GIÁ-ONLY: 'option rẻ nhất trong nhóm trùng là dòng 1 khách'? (kiểm khả năng suy luận cho lịch sử khi không có cờ)")
rows8 = []
for gid, x in dups.groupby("g0"):
    cheapest = x.loc[x["price"].idxmin()]
    rows8.append({"size": len(x), "has_single": bool(x["single_guest"].any()), "n_single": int(x["single_guest"].sum()), "cheapest_is_single": bool(cheapest["single_guest"])})
h = pd.DataFrame(rows8)
for size, x in h.groupby("size"):
    print(f"  nhóm cỡ {size}: {len(x):>3} nhóm | có dòng 1 khách: {x['has_single'].mean():5.1%} | rẻ nhất là dòng 1 khách: {x['cheapest_is_single'].mean():5.1%} (trên TẤT CẢ nhóm cỡ này) | "
          f"khi nhóm có dòng 1 khách: {x.loc[x.has_single, 'cheapest_is_single'].mean() if x.has_single.any() else float('nan'):5.1%}")
tot = h
print(f"  tất cả nhóm trùng: rẻ nhất là dòng 1 khách trong {tot['cheapest_is_single'].mean():.1%} nhóm; nếu quy tắc 'bỏ option rẻ nhất' được áp cho MỌI nhóm trùng thì bỏ ĐÚNG dòng 1 khách {tot['cheapest_is_single'].mean():.1%}, "
      f"bỏ NHẦM một phương án hợp lệ {1 - tot['cheapest_is_single'].mean():.1%}")
