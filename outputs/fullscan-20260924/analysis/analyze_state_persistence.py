"""Độ bền của trạng thái BẬT/TẮT ("Chỉ dành cho 1 khách") theo thời gian và theo phiên Chrome — dữ liệu vận hành, CHỈ ĐỌC. Claude, 2026-09-25.

Vì sao có file này: analyze_session_state.py (24/09) kết luận "trạng thái là của cả phiên" từ 20/20 phiên nhiều-hotel nhất quán. Dữ liệu lượt cào #52 (25/09) có một ca
phiên 52@01:37: Hilton BẬT (01:40) rồi The Myst TẮT (02:24) trong CÙNG phiên, và một số chuỗi nhấp nháy trong vài phút (run 35 Dusit, run 41 Mövenpick). Script này xét:
  (1) từng "lượt hotel" (12 mục cào liền nhau của một hotel) là BẬT / TẮT / LẪN;
  (2) độ bền giữa hai lượt hotel liền nhau TRONG CÙNG phiên so với KHÁC phiên;
  (3) phiên = khoảng giữa hai lần khởi động Chrome (driver_start_ms>0) tính xuyên các lượt cào (phiên đầu của mỗi lượt bắt đầu ở lượt trước).
Proxy trạng thái mỗi mục = tỉ lệ (số nhóm trùng (room_identity_key, rate_plan_key)) / (số option) >= 0,10 (bản đầu dùng ngưỡng tuyệt đối theo hotel: nhiễu, xếp nhầm nhiều lượt Hilton/Mercure là LẪN).
Không có HTML ở DB vận hành, nên đây chỉ là proxy; lần quét toàn cohort (artifact) dùng dòng cảnh báo thật (scripts/hot_state.py).
Chạy (cwd hotel-price-intelligence/backend, PYTHONIOENCODING=utf-8):  python ../../outputs/fullscan-20260924/analysis/analyze_state_persistence.py [RUN_MIN=30] [driver|chal|either]
Mốc `chal`: mục có tham số `chal_t` trong URL cuối = lần tải đầu của một phiên Booking mới (26/09: khớp `driver_start_ms>0` gần như hoàn toàn; vài lượt cào 45-47 có thêm 1-3 mục chal_t giữa phiên).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path("D:/MSE/CAPSTONE/hotel-price-intelligence/backend")
sys.path.insert(0, str(BACKEND))
import mysql.connector  # noqa: E402

from app.core.config import settings  # noqa: E402

RUN_MIN = int(sys.argv[1]) if len(sys.argv) > 1 else 30
HOT = ["hilton-saigon", "the-myst-dong-khoi", "mercure-vung-tau-vietnam", "ibis-styles-vung-tau", "dusit-le-palais-tu-hoa-hanoi", "movenpick-residences-phu-quoc",
       "starview-villa", "pearl-wealth-da-lat", "vinhomes-ocean-park-bunnys-homes-nature-room", "b-amp-k-homestay-glocery-store"]
c = mysql.connector.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER, password=settings.DB_PASSWORD, database=settings.DB_NAME, autocommit=True)
cur = c.cursor(dictionary=True, buffered=True)
cur.execute("SET time_zone='+00:00'"); cur.execute("SET SESSION MAX_EXECUTION_TIME=180000")
print("DB:", settings.DB_NAME)
ph = ",".join(["%s"] * len(HOT))
cur.execute(f"SELECT id, crawl_run_id, hotel_id, checkin_date, claimed_at FROM crawl_run_items WHERE hotel_id IN ({ph}) AND crawl_run_id>=%s AND status='success'", HOT + [RUN_MIN])
it = pd.DataFrame(cur.fetchall())
ids, out = it.id.tolist(), []
for k in range(0, len(ids), 500):
    ch = ids[k:k + 500]
    cur.execute(f"SELECT crawl_run_item_id iid, LEFT(room_identity_key,12) rik, LEFT(rate_plan_key,12) rpk FROM price_observations WHERE crawl_run_item_id IN ({','.join(['%s'] * len(ch))}) AND availability_status='available'", ch)
    out += cur.fetchall()
g = pd.DataFrame(out).groupby(["iid", "rik", "rpk"]).size().rename("n").reset_index()
per = g.groupby("iid").agg(opts=("n", "sum"), dups=("n", lambda s: int((s >= 2).sum()))).reset_index()
m = it.merge(per, left_on="id", right_on="iid")
m["t"] = pd.to_datetime(m.claimed_at) + pd.Timedelta(hours=7)               # giờ VN
m = m[m.opts >= 12].copy()
m["ratio"] = m.dups / m.opts                    # tỉ lệ nhóm trùng/option: BẬT ~0,2-0,5 (dòng 1 khách nhân đôi rate), TẮT ~0-0,05 (chỉ gói/bữa ăn); ngưỡng tuyệt đối theo hotel bị nhiễu vì số nhóm đổi theo ngày check-in
m["on"] = m.ratio >= 0.10
m["thr"] = 0.10
MARK = sys.argv[2] if len(sys.argv) > 2 else "driver"          # "driver": driver_start_ms>0 (mặc định); "chal": tham số thử thách chal_t trong URL cuối (Booking tạo phiên mới); "either": cả hai
COND = {"driver": "driver_start_ms>0", "chal": "hotel_link LIKE '%chal_t%'", "either": "(driver_start_ms>0 OR hotel_link LIKE '%chal_t%')"}[MARK]
print("Mốc đầu phiên:", MARK, "->", COND)
cur.execute(f"SELECT crawl_run_id, claimed_at FROM crawl_run_items WHERE crawl_run_id>=%s AND {COND} ORDER BY claimed_at", (RUN_MIN,))
st = pd.DataFrame(cur.fetchall()); st["t"] = pd.to_datetime(st.claimed_at) + pd.Timedelta(hours=7)
starts = st.t.sort_values().reset_index(drop=True)
m = m.sort_values("t").reset_index(drop=True)
idx = np.searchsorted(starts.values, m.t.values, side="right") - 1          # phiên = lần khởi động Chrome gần nhất trước mục (xuyên các lượt cào)
m["sess"] = idx
m = m[m.sess >= 0].copy()
m["sess_start"] = starts.values[m.sess.values]
print(f"{len(m)} mục (>=12 option) của {m.hotel_id.nunique()} hotel, {m.sess.nunique()} phiên (xuyên lượt cào), {m.crawl_run_id.nunique()} lượt cào; ngưỡng BẬT theo hotel:",
      {h[:10]: round(t, 1) for h, t in m.groupby("hotel_id").thr.first().items()})

# ---- lượt hotel (burst) = (run, hotel)
b = m.groupby(["crawl_run_id", "hotel_id"]).agg(t0=("t", "min"), t1=("t", "max"), n=("on", "size"), frac=("on", "mean"), sess=("sess", "first"), sess_start=("sess_start", "first"),
                                                 nsess=("sess", "nunique"), seq=("on", lambda s: "".join("1" if v else "0" for v in s))).reset_index().sort_values("t0")
b = b[b.n >= 5].copy()
b["cls"] = np.where(b.frac >= 0.8, "ON", np.where(b.frac <= 0.2, "OFF", "LẪN"))
print(f"\n{len(b)} lượt hotel (>=5 mục): " + str(b.cls.value_counts().to_dict()) + f" | TẮT {int((b.cls == 'OFF').sum())}/{len(b)} = {(b.cls == 'OFF').mean():.1%}, LẪN (đổi trạng thái ngay trong ~3-4 phút của một lượt hotel) {int((b.cls == 'LẪN').sum())}/{len(b)} = {(b.cls == 'LẪN').mean():.1%}")
print("\nLượt hotel KHÔNG BẬT (giờ VN, phút từ đầu phiên, chuỗi 1=BẬT 0=TẮT theo thứ tự claim):")
for r in b[b.cls != "ON"].itertuples():
    print(f"  run {r.crawl_run_id:>2} {r.hotel_id[:18]:18} {r.t0:%m-%d %H:%M}  phiên từ {r.sess_start:%H:%M} (+{(r.t0 - r.sess_start).total_seconds() / 60:>5.1f}p)  {r.cls:3} {r.seq}")

# ---- TẮT theo thời điểm TRONG phiên (kỷ nguyên phiên dài, run >= 39: ~500 mục ≈ 110 phút/phiên; trước đó phiên chỉ 10-90 mục)
lg = b[b.crawl_run_id >= 39].copy()
lg["phút_từ_đầu_phiên"] = (lg.t0 - lg.sess_start).dt.total_seconds() / 60
lg["khoảng"] = pd.cut(lg["phút_từ_đầu_phiên"], [-1, 10, 30, 60, 130], labels=["0-10p", "10-30p", "30-60p", "60-130p"])
tb = lg.groupby("khoảng", observed=True).agg(lượt=("cls", "size"), TẮT=("cls", lambda s: int((s == "OFF").sum())), LẪN=("cls", lambda s: int((s == "LẪN").sum())))
tb["%TẮT"] = (tb["TẮT"] / tb["lượt"] * 100).round(1)
print("\nKỷ nguyên phiên dài (run >= 39): TẮT theo số phút kể từ đầu phiên Chrome (lượt hotel bắt đầu cào lúc đó):")
print(tb.to_string())
print("  (các hotel hay có dòng 1 khách nằm ở vị trí cố định trong hàng đợi nên tương quan với ranh giới phiên; đây chỉ là mô tả, không phải kiểm định)")
print("\nSố mục mỗi phiên theo lượt cào (kiểm định nghĩa 'phiên' theo thời gian):")
print(st.groupby("crawl_run_id").size().rename("lần khởi động Chrome").to_frame().T.to_string())

# ---- tần suất TẮT theo lượt cào (có đang tăng không?)
import math
pr = b.groupby("crawl_run_id").agg(ngày=("t0", lambda s: f"{s.min():%m-%d}"), lượt=("cls", "size"), TẮT=("cls", lambda s: int((s == "OFF").sum())), LẪN=("cls", lambda s: int((s == "LẪN").sum())))
print("\nTần suất TẮT theo lượt cào vận hành (mỗi lượt hotel = 12 mục liền của một hotel hay có dòng 1 khách):")
print(pr.T.to_string())
last_n = 5
late, early = b[b.crawl_run_id > b.crawl_run_id.max() - last_n], b[b.crawl_run_id <= b.crawl_run_id.max() - last_n]
a_, n1, c_, n2 = int((late.cls == "OFF").sum()), len(late), int((early.cls == "OFF").sum()), len(early)


def fisher_upper(k1, n1_, k2, n2_):                         # P(X >= k1) với X ~ Hypergeom (tổng TẮT cố định), một phía
    K, N = k1 + k2, n1_ + n2_
    return sum(math.comb(K, x) * math.comb(N - K, n1_ - x) for x in range(k1, min(K, n1_) + 1)) / math.comb(N, n1_)


print(f"{last_n} lượt cào gần nhất: TẮT {a_}/{n1} ({a_ / n1:.1%}) so với các lượt trước: {c_}/{n2} ({c_ / n2:.1%}) | Fisher một phía p = {fisher_upper(a_, n1, c_, n2):.3f}")

# ---- độ bền giữa hai lượt hotel liền nhau trong cùng phiên / khác phiên
b = b.sort_values("t0").reset_index(drop=True)
rows = []
for a, c2 in zip(b.itertuples(), b.iloc[1:].itertuples()):
    if a.cls == "LẪN" or c2.cls == "LẪN":
        continue
    rows.append({"same_sess": a.sess == c2.sess, "a": a.cls, "b": c2.cls, "gap_min": (c2.t0 - a.t1).total_seconds() / 60})
p = pd.DataFrame(rows)
print("\nHai lượt hotel liền nhau (bỏ LẪN):")
for ss, name in ((True, "CÙNG phiên"), (False, "KHÁC phiên")):
    x = p[p.same_sess == ss]
    off_a = x[x.a == "OFF"]
    on_a = x[x.a == "ON"]
    print(f"  {name}: {len(x)} cặp | trước=BẬT: {len(on_a)} cặp, sau=TẮT {int((on_a.b == 'OFF').sum())} | trước=TẮT: {len(off_a)} cặp, sau=TẮT {int((off_a.b == 'OFF').sum())}"
          f" | khoảng cách giữa hai lượt: median {x.gap_min.median():.0f} phút")
print("\nCác cặp CÙNG phiên mà trạng thái ĐỔI:")
for r in [(a, c2) for a, c2 in zip(b.itertuples(), b.iloc[1:].itertuples()) if a.sess == c2.sess and a.cls != "LẪN" and c2.cls != "LẪN" and a.cls != c2.cls]:
    a, c2 = r
    print(f"  run {a.crawl_run_id} phiên {a.sess_start:%H:%M}: {a.hotel_id[:14]} {a.t0:%H:%M} {a.cls} → {c2.hotel_id[:14]} {c2.t0:%H:%M} {c2.cls}  (cách {(c2.t0 - a.t1).total_seconds() / 60:.0f} phút)")
