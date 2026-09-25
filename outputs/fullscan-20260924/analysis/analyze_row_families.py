"""Hai họ "dòng phụ" của trang Booking (dòng "Chỉ dành cho 1 khách" và dòng Basic `bbasic_*`) qua hai lần quét toàn cohort và các mini-crawl. Claude, 2026-09-25.

Câu hỏi: (R-1) mỗi họ dòng phụ xuất hiện thế nào ở từng lần cào; (R-2) chúng chiếm bao nhiêu nhóm trùng canonical key ở lần quét 2 và phần còn lại có bằng đường cơ sở lần quét 1 không;
(R-3) trang TẮT có là tập con của trang BẬT không, phần dôi ra ở lần BẬT được hai họ này giải thích đến đâu (đối chứng cùng trạng thái); (R-4) hai họ có ổn định trong lúc quét 2 không;
(R-5) từng hotel mini qua 3 lần cào; (R-6) đặc điểm dòng bbasic; (R-7) sold_out ↔ not_bookable giữa hai lần quét.
CHỈ ĐỌC (DB cô lập + artifact). Gộp các đoạn thử ad-hoc của 25/09 để GPT tái lập được.
Chạy:  backend\\venv\\Scripts\\python.exe outputs\\fullscan-20260924\\analysis\\analyze_row_families.py [RUN_QUÉT_1=1] [RUN_QUÉT_2=3]   (PYTHONIOENCODING=utf-8; ~4 phút)
Định nghĩa: dòng 1 khách = cảnh báo "Chỉ dành cho 1 khách" hoặc thành phần 3 của block-id = 1; dòng bbasic = block-id chứa "bbasic" (Booking Basic: 100% có "• Thanh toán trước").
"""
import collections
import gzip
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
from minicrawl_common import RUN_ROOT as MINI_RUN, connect, load_run  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
DB, ART = "hotel_price_intel_fullscan_20260924", Path("D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/crawl_artifacts")
MDB, MART = "hotel_price_intel_minicrawl_20260924", MINI_RUN / "crawl_artifacts"
R1 = int(sys.argv[1]) if len(sys.argv) > 1 else 1
R2 = int(sys.argv[2]) if len(sys.argv) > 2 else 3

runs = {"quét 1 (TẮT 24/09 20:23)": load_run(DB, ART, R1)[1], f"quét 2 (BẬT 25/09 21:11)": load_run(DB, ART, R2)[1],
        "mini run1 (24/09 17:25)": load_run(MDB, MART, 1)[1], "mini run2 (24/09 19:24)": load_run(MDB, MART, 2)[1], "mini run3 (24/09 20:03, 3 hotel)": load_run(MDB, MART, 3)[1]}
for o in runs.values():
    o["basic"] = o.dom_is_basic == True
o1, o3 = runs["quét 1 (TẮT 24/09 20:23)"], runs["quét 2 (BẬT 25/09 21:11)"]

print("=" * 110)
print("R-1. HAI HỌ DÒNG PHỤ THEO LẦN CÀO (2 người lớn mặc định ở mọi lần)")
for name, o in runs.items():
    b, sg = o[o.basic], o[o.single_guest]
    print(f"  {name:34} option {len(o):5} | bbasic {len(b):4} ({len(b) / len(o):5.1%}) ở {b.hotel_id.nunique():3}/{o.hotel_id.nunique():3} hotel | dòng 1 khách {len(sg):4} ({len(sg) / len(o):5.1%}) ở {sg.hotel_id.nunique():3} hotel")


def stats(o, label):
    g = o.groupby(["item_id", "rik", "rpk"]).size().rename("n").reset_index()
    dup = g[g.n >= 2]
    hd = o.merge(dup[["item_id", "rik", "rpk"]], on=["item_id", "rik", "rpk"]).hotel_id.nunique()
    uniq = g[g.n == 1].merge(o[["item_id", "hotel_id"]].drop_duplicates(), on="item_id")
    print(f"  {label:46} option {len(o):5} | nhóm trùng {len(dup):4}/{len(g):4} ({len(dup) / len(g):5.1%}) | option trong nhóm trùng {int(dup.n.sum()):4} ({dup.n.sum() / len(o):5.1%}) | hotel có nhóm trùng {hd:3}/{o.hotel_id.nunique()} | hotel KHÔNG có khóa duy nhất {o.hotel_id.nunique() - uniq.hotel_id.nunique():2}")


print("\n" + "=" * 110)
print("R-2. NHÓM TRÙNG CANONICAL KEY: bỏ từng họ dòng phụ khỏi lần quét 2 (BẬT)")
stats(o1, "quét 1 (TẮT): tất cả")
stats(o3, "quét 2 (BẬT): tất cả")
stats(o3[~o3.single_guest], "quét 2 bỏ dòng 1 khách")
stats(o3[~o3.basic], "quét 2 bỏ dòng bbasic")
stats(o3[~o3.single_guest & ~o3.basic], "quét 2 bỏ CẢ HAI họ dòng phụ")
g = o3.groupby(["item_id", "rik", "rpk"]).agg(n=("price", "size"), has_b=("basic", "any"), has_sg=("single_guest", "any")).reset_index()
d = g[g.n >= 2]
print(f"  nhóm trùng quét 2 = {len(d)}: chứa dòng 1 khách {int(d.has_sg.sum())} ({d.has_sg.mean():.1%}) | chứa bbasic {int(d.has_b.sum())} ({d.has_b.mean():.1%}) | chứa cả hai {int((d.has_sg & d.has_b).sum())} | không chứa họ nào {int((~d.has_sg & ~d.has_b).sum())} ({(~d.has_sg & ~d.has_b).mean():.1%})")

print("\n" + "=" * 110)
print("R-3. TRANG TẮT (quét 1) SO VỚI TRANG BẬT (quét 2), cùng hotel, cùng check-in 2026-10-11")
common = sorted(set(o1.hotel_id) & set(o3.hotel_id))
a, b = o1[o1.hotel_id.isin(common)], o3[o3.hotel_id.isin(common)]
sa, sb = set(a.dom_block_id), set(b.dom_block_id)
print(f"  hotel chung {len(common)} | block-id quét 1 {len(sa)}, có ở quét 2: {len(sa & sb)} ({len(sa & sb) / len(sa):.1%}) ⇒ trang TẮT là tập con của trang BẬT ở mức đó")
new = b[~b.dom_block_id.isin(sa)]
n_sg, n_b = int(new.single_guest.sum()), int((new.basic & ~new.single_guest).sum())
oth = len(new) - n_sg - n_b
print(f"  dòng CHỈ có ở quét 2: {len(new)} = dòng 1 khách {n_sg} + bbasic {n_b} + còn lại {oth} ⇒ hai họ giải thích {(n_sg + n_b) / len(new):.1%}; phần còn lại {oth} dòng ≈ mức trôi thời gian")
old = a[~a.dom_block_id.isin(sb)]
print(f"  dòng CHỈ có ở quét 1: {len(old)} (trôi thời gian theo chiều ngược lại)")
m_b = b[b.dom_block_id.isin(sa)].set_index("dom_block_id").price
m_a = a[a.dom_block_id.isin(sb)].set_index("dom_block_id").price
same = [float(m_a[i]) == float(m_b[i]) for i in set(m_a.index) & set(m_b.index) if not isinstance(m_a[i], pd.Series) and not isinstance(m_b[i], pd.Series)]
print(f"  giá của block-id chung: {len(same)} | không đổi {sum(same) / len(same):.1%} (cách ~25 giờ, lead-time 17→16 ngày; đối chiếu E1 cách ~2 giờ: 94,8%)")
# hotel gọn ở cả hai lần: dòng dôi ra ở quét 2 là gì?
sg_h = set(o3[o3.single_guest].hotel_id)
cmpt = [h for h in common if h not in sg_h]
rows = []
for h in cmpt:
    x, y = o1[o1.hotel_id == h], o3[o3.hotel_id == h]
    yo = y[~y.dom_block_id.isin(set(x.dom_block_id))]
    pairs = set(zip(x.dom_bid_room, x.dom_bid_rate))
    rows.append({"hotel": h, "A_only": int((~x.dom_block_id.isin(set(y.dom_block_id))).sum()), "B_only": len(yo), "B_only_bbasic": int(yo.basic.sum()),
                 "B_only_cặp_room_rate_mới": sum(1 for r in yo.itertuples() if (r.dom_bid_room, r.dom_bid_rate) not in pairs), "B_only_1khách": int(yo.single_guest.sum())})
t = pd.DataFrame(rows)
print(f"  hotel GỌN ở cả hai lần: {len(t)} | dòng chỉ ở quét 1 {t.A_only.sum()}, chỉ ở quét 2 {t.B_only.sum()} (bbasic {t.B_only_bbasic.sum()}, cặp room-rate mới hoàn toàn {t.B_only_cặp_room_rate_mới.sum()}, dòng 1 khách {t.B_only_1khách.sum()}); hotel có dòng dôi {int((t.B_only > 0).sum())}/{len(t)}")
# đối chứng cùng trạng thái
om1, om2 = runs["mini run1 (24/09 17:25)"], runs["mini run2 (24/09 19:24)"]
om1, om2 = om1[om1.checkin == "2026-10-11"], om2[om2.checkin == "2026-10-11"]
for lab, x, y in (("BẬT↔BẬT: mini run1 (24/09 17:25) → quét 2", om1, o3), ("TẮT↔TẮT: mini run2 (24/09 19:24) → quét 1", om2, o1)):
    hs = sorted(set(x.hotel_id) & set(y.hotel_id))
    x, y = x[x.hotel_id.isin(hs)], y[y.hotel_id.isin(hs)]
    sx, sy = set(x.dom_block_id), set(y.dom_block_id)
    print(f"  đối chứng {lab}: {len(hs)} hotel, dòng {len(x)} → {len(y)}; chỉ ở lần trước {len(sx - sy)}, chỉ ở lần sau {len(sy - sx)}")

print("\n" + "=" * 110)
print("R-4. HAI HỌ CÓ ỔN ĐỊNH TRONG LÚC QUÉT 2? (hotel có ≥5 option, theo khung 15 phút giờ claim VN, và theo thành phố)")
c = connect(DB); cur = c.cursor(dictionary=True, buffered=True); cur.execute("SET time_zone='+00:00'")
cur.execute("SELECT id, claimed_at, market_hint FROM crawl_run_items WHERE crawl_run_id=%s", (R2,))
m = pd.DataFrame(cur.fetchall()).rename(columns={"id": "item_id"}); m["t"] = pd.to_datetime(m.claimed_at) + pd.Timedelta(hours=7)
p = o3.groupby("item_id").agg(hotel=("hotel_id", "first"), n=("price", "size"), b=("basic", "sum"), sg=("single_guest", "sum")).reset_index().merge(m, on="item_id")
p = p[p.n >= 5].copy()
p["khung"] = p.t.dt.floor("15min").dt.strftime("%H:%M")
tt = p.groupby("khung").agg(hotel=("hotel", "size"), có_bbasic=("b", lambda s: int((s > 0).sum())), có_1khách=("sg", lambda s: int((s > 0).sum())))
tt["%bbasic"] = (tt.có_bbasic / tt.hotel * 100).round(0); tt["%1khách"] = (tt.có_1khách / tt.hotel * 100).round(0)
print(tt.T.to_string())
q = p.groupby("market_hint").agg(hotel=("hotel", "size"), có_bbasic=("b", lambda s: int((s > 0).sum())), có_1khách=("sg", lambda s: int((s > 0).sum())))
q["%bbasic"] = (q.có_bbasic / q.hotel * 100).round(0); q["%1khách"] = (q.có_1khách / q.hotel * 100).round(0)
print(q.to_string())
print(f"  hotel có CẢ hai họ {int(((p.b > 0) & (p.sg > 0)).sum())} | chỉ bbasic {int(((p.b > 0) & (p.sg == 0)).sum())} | chỉ 1 khách {int(((p.b == 0) & (p.sg > 0)).sum())} | không họ nào {int(((p.b == 0) & (p.sg == 0)).sum())} / {len(p)}")

print("\n" + "=" * 110)
print("R-5. TỪNG HOTEL MINI QUA BA LẦN CÀO (số dòng bbasic / dòng 1 khách; 3 ngày check-in cộng lại)")
rows = []
for lab, key in (("run1 17:25", "mini run1 (24/09 17:25)"), ("run2 19:24", "mini run2 (24/09 19:24)"), ("run3 20:03", "mini run3 (24/09 20:03, 3 hotel)")):
    o = runs[key]
    for h, x in o.groupby("hotel_id"):
        rows.append({"lần": lab, "hotel": h[:26], "bbasic": int(x.basic.sum()), "1khách": int(x.single_guest.sum())})
print(pd.DataFrame(rows).pivot(index="hotel", columns="lần", values=["bbasic", "1khách"]).to_string())

print("\n" + "=" * 110)
print("R-6. ĐẶC ĐIỂM DÒNG bbasic Ở QUÉT 2")
bb = o3[o3.basic]
print(f"  {len(bb)} dòng ở {bb.hotel_id.nunique()} hotel; 'Thanh toán trước' trong ô điều kiện: {int(bb.pay.str.contains('Thanh toán trước').sum())}/{len(bb)}; hủy: {dict(bb.cancel.str.replace(r'\d+ tháng \d+, \d{4}', 'N tháng M, YYYY', regex=True).value_counts().head(4))}")
ratios = []
for (iid, room), x in o3.groupby(["item_id", "dom_bid_room"]):
    xb, xn = x[x.basic], x[~x.basic & ~x.single_guest]
    if len(xb) and len(xn):
        ratios.append(float(xb.price.min()) / float(xn.price.min()))
print(f"  giá bbasic / giá thấp nhất của các dòng thường cùng phòng: median {pd.Series(ratios).median():.3f} (n={len(ratios)} phòng)")

print("\n" + "=" * 110)
print("R-7. TRẠNG THÁI MỤC GIỮA HAI LẦN QUÉT (sold_out ↔ not_bookable)")
cur.execute("SELECT id, crawl_run_id r, hotel_name_hint name, status FROM crawl_run_items WHERE crawl_run_id IN (%s,%s)", (R1, R2))
s = pd.DataFrame(cur.fetchall())
j = pd.concat([s[s.r == R1].set_index("name").status.rename("quét1"), s[s.r == R2].set_index("name").status.rename("quét2")], axis=1)
print(pd.crosstab(j.quét1, j.quét2, margins=True).to_string())
chg = j[j.quét1 != j.quét2]
cur.execute("SELECT id, crawl_run_id r, hotel_name_hint name FROM crawl_run_items WHERE crawl_run_id IN (%s,%s) AND status IN ('sold_out','not_bookable')", (R1, R2))
NB = re.compile(r"non-bookable-container")
flag = {}
for x in cur.fetchall():
    pth = ART / str(x["r"]) / str(x["id"]) / "page.html.gz"
    if pth.exists():
        flag[(x["name"], x["r"])] = bool(NB.search(gzip.open(pth, "rb").read().decode("utf-8", errors="replace")))
print("  hotel đổi trạng thái (kèm có phần tử 'non-bookable-container' trong HTML: quét1/quét2):")
for n, r in chg.iterrows():
    print(f"    {n[:46]:46} {r.quét1:>12} → {r.quét2:<12} | non-bookable-container: {flag.get((n, R1), '-')}/{flag.get((n, R2), '-')}")
