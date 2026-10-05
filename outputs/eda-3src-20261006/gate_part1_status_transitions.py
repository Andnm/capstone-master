"""Gate tong the muc 2 (CHI DOC warehouse hien hanh): chuyen trang thai item theo (hotel, nguon, ngay crawl VN).

Muc dich: phan biet property-state (not_bookable ben vung ca ngay) voi page-state/parser-state (lan lon trong cung hotel-ngay-nguon),
va do tan suat dao chieu giua cac ngay lien tiep. KHONG ghi gi; max_execution_time 10 phut; chi mot GROUP BY tren crawl_run_items.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
from app.warehouse.connection import warehouse_connection  # noqa: E402

SQL = """
SELECT m.source_code,
       cri.hotel_id,
       DATE(CONVERT_TZ(cr.started_at, '+00:00', '+07:00')) AS crawl_date_vn,
       SUM(cri.status='success')      AS n_success,
       SUM(cri.status='sold_out')     AS n_sold_out,
       SUM(cri.status='not_bookable') AS n_not_bookable,
       SUM(cri.status='error')        AS n_error,
       COUNT(*)                       AS n_items
FROM crawl_run_items cri
JOIN etl_item_map m ON m.warehouse_item_id = cri.id
JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
WHERE m.include_eda_main = 1 AND cri.hotel_id IS NOT NULL AND cr.status = 'completed'
GROUP BY m.source_code, cri.hotel_id, DATE(CONVERT_TZ(cr.started_at, '+00:00', '+07:00'))
"""

with warehouse_connection("warehouse_20261004_3src") as conn:
    cur = conn.cursor(dictionary=True)
    cur.execute("SET SESSION TRANSACTION READ ONLY")
    cur.execute("SET SESSION max_execution_time = 600000")
    cur.execute(SQL)
    df = pd.DataFrame(cur.fetchall())
    cur.close()
for c in ("n_success", "n_sold_out", "n_not_bookable", "n_error", "n_items"):
    df[c] = df[c].astype(int)
print("hotel-source-day rows:", len(df), "| items:", int(df.n_items.sum()))
df["any_nb"] = df.n_not_bookable > 0
df["all_nb"] = df.n_not_bookable == df.n_items
df["mixed_nb"] = df.any_nb & ~df.all_nb
df["nb_with_success"] = df.any_nb & (df.n_success > 0)
print("\n== Muc property-vs-page: hotel-source-day co >=1 item not_bookable ==")
g = df.groupby("source_code").agg(rows=("n_items", "size"), any_nb=("any_nb", "sum"), all_nb=("all_nb", "sum"),
                                 mixed_nb=("mixed_nb", "sum"), nb_with_success=("nb_with_success", "sum"))
g["mixed_share_of_any_nb"] = (g.mixed_nb / g.any_nb).round(3)
g["nb_with_success_share_of_any_nb"] = (g.nb_with_success / g.any_nb).round(3)
print(g.to_string())
tot = g.sum()
print("TONG: any_nb", int(tot.any_nb), "all_nb", int(tot.all_nb), "mixed_nb", int(tot.mixed_nb),
      "nb_with_success", int(tot.nb_with_success), "mixed_share", round(tot.mixed_nb / tot.any_nb, 3))

# Dao chieu giua cac ngay crawl lien tiep (theo nguon, hotel): trang thai ngay = 'all_nb' / 'has_success' / 'other'
df = df.sort_values(["source_code", "hotel_id", "crawl_date_vn"])
df["day_state"] = "other"
df.loc[df.all_nb, "day_state"] = "all_not_bookable"
df.loc[~df.any_nb & (df.n_success > 0), "day_state"] = "has_success_no_nb"
df.loc[df.any_nb & (df.n_success > 0), "day_state"] = "mixed_nb_and_success"
df["prev_state"] = df.groupby(["source_code", "hotel_id"]).day_state.shift(1)
df["prev_date"] = df.groupby(["source_code", "hotel_id"]).crawl_date_vn.shift(1)
cons = df[df.prev_state.notna() & ((pd.to_datetime(df.crawl_date_vn) - pd.to_datetime(df.prev_date)).dt.days == 1)]
print("\n== Chuyen trang thai giua ngay lien tiep (theo nguon x hotel) ==")
print(pd.crosstab(cons.prev_state, cons.day_state, margins=True).to_string())
flip = ((cons.prev_state == "all_not_bookable") & (cons.day_state == "has_success_no_nb")).sum()
stay = ((cons.prev_state == "all_not_bookable") & (cons.day_state == "all_not_bookable")).sum()
print(f"all_not_bookable hom truoc -> hom nay: giu nguyen {stay}, dao thanh co-success {flip}")

# Hotel co chuoi all_not_bookable dai
streak = []
for (src, hid), sub in df.groupby(["source_code", "hotel_id"]):
    run = 0
    best = 0
    for st in sub.day_state:
        run = run + 1 if st == "all_not_bookable" else 0
        best = max(best, run)
    if best:
        streak.append((src, hid, best))
st = pd.DataFrame(streak, columns=["source_code", "hotel_id", "max_streak_all_nb_days"])
print("\n== Hotel co it nhat 1 ngay all_not_bookable: ", len(st), " (hotel-nguon) ==")
print(st.max_streak_all_nb_days.describe().round(2).to_string())
print("so hotel-nguon co streak >=3 ngay:", int((st.max_streak_all_nb_days >= 3).sum()), "| >=6:", int((st.max_streak_all_nb_days >= 6).sum()))
print(st.sort_values("max_streak_all_nb_days", ascending=False).head(12).to_string(index=False))
