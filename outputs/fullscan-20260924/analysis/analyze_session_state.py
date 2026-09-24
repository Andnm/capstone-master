"""Trạng thái BẬT/TẮT của dòng "Chỉ dành cho 1 khách" là thuộc tính của PHIÊN Chrome? — kiểm trên dữ liệu vận hành. Claude, 2026-09-24.

CHỈ ĐỌC DB vận hành (chỉ SELECT, pin time_zone='+00:00', MAX_EXECUTION_TIME). Không ghi gì.
Ý tưởng: worker dùng lại một phiên Chrome tối đa 500 mục rồi khởi động lại; mục nào phải khởi động driver có `crawl_run_items.driver_start_ms > 0` ⇒ đánh dấu ĐẦU MỖI PHIÊN.
Với 10 hotel hay có dòng 1 khách, một mục "BẬT" nếu có nhóm trùng canonical key (dòng 1 khách luôn nằm cạnh dòng 2 khách cùng khóa). Nếu trạng thái là của phiên thì mọi hotel trong CÙNG phiên phải cùng trạng thái.
Kết quả 2026-09-24 (22 lượt cào 03–24/09): 155 phiên; 20 phiên có ≥2 hotel → 20/20 cùng trạng thái, 0 phiên lẫn; phiên TẮT 6/155 (3,9%).
Chạy (cwd = hotel-price-intelligence/backend):  python ../../outputs/fullscan-20260924/analysis/analyze_session_state.py [RUN_MIN=30]   (PYTHONIOENCODING=utf-8)
"""
import sys
from pathlib import Path

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
ph = ",".join(["%s"] * len(HOT))
cur.execute(f"SELECT id, crawl_run_id, hotel_id, claimed_at FROM crawl_run_items WHERE hotel_id IN ({ph}) AND crawl_run_id>=%s AND status='success'", HOT + [RUN_MIN])
it = pd.DataFrame(cur.fetchall())
ids, out = it.id.tolist(), []
for k in range(0, len(ids), 500):
    ch = ids[k:k + 500]
    cur.execute(f"SELECT crawl_run_item_id iid, LEFT(room_identity_key,12) rik, LEFT(rate_plan_key,12) rpk FROM price_observations WHERE crawl_run_item_id IN ({','.join(['%s'] * len(ch))}) AND availability_status='available'", ch)
    out += cur.fetchall()
g = pd.DataFrame(out).groupby(["iid", "rik", "rpk"]).size().rename("n").reset_index()
per = g.groupby("iid").agg(opts=("n", "sum"), dup_groups=("n", lambda s: int((s >= 2).sum()))).reset_index()
m = it.merge(per, left_on="id", right_on="iid")
m["t"] = pd.to_datetime(m.claimed_at) + pd.Timedelta(hours=7)          # giờ VN
m["on"] = m.dup_groups > 0
m = m[m.opts >= 12].copy()                                              # bỏ mục tồn kho quá thấp
cur.execute("SELECT crawl_run_id, claimed_at FROM crawl_run_items WHERE crawl_run_id>=%s AND driver_start_ms>0", (RUN_MIN,))
st = pd.DataFrame(cur.fetchall()); st["t"] = pd.to_datetime(st.claimed_at) + pd.Timedelta(hours=7)
print(f"{len(m)} mục (≥12 option) của {m.hotel_id.nunique()} hotel; {len(st)} lần khởi động Chrome trong {st.crawl_run_id.nunique()} lượt cào (trung bình {len(st) / st.crawl_run_id.nunique():.1f} phiên/lượt)")
r = st[st.crawl_run_id == st.crawl_run_id.max()]
print("lượt cào mới nhất — các phiên bắt đầu lúc (VN):", [f"{x:%H:%M}" for x in r.t])

sess = []
for x in m.itertuples():
    s = st[(st.crawl_run_id == x.crawl_run_id) & (st.t <= x.t)]
    sess.append(s.t.max() if len(s) else pd.NaT)
m["sess_start"] = sess
m = m.dropna(subset=["sess_start"])
m["sess"] = m.crawl_run_id.astype(str) + "@" + m.sess_start.dt.strftime("%H:%M")
gs = m.groupby(["sess", "hotel_id"]).agg(items=("id", "size"), on=("on", "mean")).reset_index()
g5 = gs[gs["items"] >= 5]
multi = g5.groupby("sess").filter(lambda x: x.hotel_id.nunique() >= 2)
agree = multi.groupby("sess").apply(lambda x: bool((x.on >= 0.8).all() or (x.on <= 0.2).all()), include_groups=False)
print(f"\nphiên có ≥1 hotel (≥5 mục): {g5.sess.nunique()} | phiên có ≥2 hotel: {multi.sess.nunique()} | trong đó CÙNG trạng thái (mọi hotel BẬT ≥0,8 hoặc mọi hotel TẮT ≤0,2): {int(agree.sum())}/{len(agree)}")
ss = g5.groupby("sess").on.mean()
print("phân bố theo PHIÊN:", pd.cut(ss, [-0.01, 0.2, 0.8, 1.0], labels=["TẮT(≤0,2)", "lẫn", "BẬT(>0,8)"]).value_counts().to_dict(), f"| phiên TẮT {(ss <= 0.2).mean():.1%}")
off = m[~m.on]
print(f"mục TẮT: phút từ đầu phiên tới lúc claim — median {((off.t - off.sess_start).dt.total_seconds() / 60).median():.1f}, max {((off.t - off.sess_start).dt.total_seconds() / 60).max():.1f} (phiên dài ~110 phút)")
print("\nCác phiên TẮT (run@giờ khởi động, hotel trong phiên):")
for s in ss[ss <= 0.2].index:
    x = g5[g5.sess == s]
    print(f"  {s}: {x[['hotel_id', 'items']].values.tolist()}")
