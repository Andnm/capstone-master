"""So sánh HAI lần quét toàn cohort (cùng DB, khác run) — độ ổn định theo thời gian ở quy mô cohort. Claude, 2026-09-24.

Dùng:  python compare_scans.py RUN_A RUN_B [DB] [ARTIFACT_DIR]     (mặc định DB = fullscan, artifact = outputs/fullscan-20260924/run/crawl_artifacts)
Để thử trên mini-crawl:  python compare_scans.py 1 3 hotel_price_intel_minicrawl_20260924 D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/run/crawl_artifacts
CHỈ ĐỌC. Trả lời: (1) mỗi hotel chuyển trạng thái BẬT/gọn/có-cột-0-dòng-1-khách thế nào giữa hai lần (đợt tắt có lặp không, có chung hotel không)?
(2) block-id, giá, dòng 1 khách, nhóm trùng, khoảng chênh ổn định ra sao? (3) N1 có cùng tập hotel không?
"""
import collections
import sys
from pathlib import Path

import pandas as pd

MINI = Path("D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
sys.path.insert(0, str(MINI))
from minicrawl_common import load_run  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300)
HERE = Path(__file__).resolve().parent
RA, RB = int(sys.argv[1]), int(sys.argv[2])
DB = sys.argv[3] if len(sys.argv) > 3 else "hotel_price_intel_fullscan_20260924"
ART = Path(sys.argv[4]) if len(sys.argv) > 4 else HERE.parent / "run" / "crawl_artifacts"


def fam(bid):
    p = str(bid).split("_")
    return (p[0], p[1], p[3] if len(p) > 3 else "", p[4] if len(p) > 4 else "", "_".join(p[5:]) if len(p) > 5 else "") if len(p) >= 5 else (bid,)


def per_hotel(dom, o):
    d = dom[dom.matched]
    pg = d.groupby(["hotel_id", "checkin"]).agg(rows=("dom_idx", "size"), occ_cells=("has_occ_cell", "sum"), alerts=("only_for", lambda s: int(s.notna().sum()))).reset_index()
    pg["state"] = pg.apply(lambda r: "BẬT" if r.alerts > 0 else ("có_cột_0_dòng1khách" if r.occ_cells > 0 else "gọn"), axis=1)
    return pg.set_index(["hotel_id", "checkin"])


domA, oA, itA = load_run(DB, ART, RA)
domB, oB, itB = load_run(DB, ART, RB)
for o in (oA, oB):
    o["fam"] = o.dom_block_id.map(fam)
pa, pb = per_hotel(domA, oA), per_hotel(domB, oB)

print("=" * 100)
print(f"C-1. TỔNG QUAN: run #{RA} ({len(oA)} option, {oA.hotel_id.nunique()} hotel) vs run #{RB} ({len(oB)} option, {oB.hotel_id.nunique()} hotel), DB {DB}")
j = pa[["state", "rows", "alerts"]].join(pb[["state", "rows", "alerts"]], how="inner", lsuffix="_A", rsuffix="_B")
print(f"  cặp (hotel, check-in) có ở cả hai: {len(j)}")
print("\nC-2. CHUYỂN TRẠNG THÁI (hàng = lần A, cột = lần B):")
print(pd.crosstab(j.state_A, j.state_B, margins=True).to_string())
flip = j[j.state_A != j.state_B]
print(f"\n  hotel ĐỔI trạng thái: {len(flip)}/{len(j)} ({len(flip) / max(len(j), 1):.1%})")
if len(flip):
    print(flip.to_string())
onA = j[j.state_A == "BẬT"]
print(f"  trong {len(onA)} hotel BẬT ở lần A: còn BẬT ở lần B {int((onA.state_B == 'BẬT').sum())} ({(onA.state_B == 'BẬT').mean():.1%})" if len(onA) else "  (không có hotel BẬT ở lần A)")

print("\n" + "=" * 100)
print("C-3. ỔN ĐỊNH block-id / giá / dòng 1 khách (cùng hotel, cùng check-in)")
rows, prices = [], []
for (h, c), a in oA.groupby(["hotel_id", "checkin"]):
    b = oB[(oB.hotel_id == h) & (oB.checkin == c)]
    if b.empty:
        continue
    sa, sb = set(a.dom_block_id), set(b.dom_block_id)
    ga, gb = set(a[a.single_guest].dom_block_id), set(b[b.single_guest].dom_block_id)
    rows.append({"hotel": h, "nA": len(a), "nB": len(b), "chung": len(sa & sb), "sgA": len(ga), "sgB": len(gb), "sg_chung": len(ga & gb), "nA_không_sg": len(sa - ga), "chung_không_sg": len((sa - ga) & sb)})
    ma, mb = a.set_index("dom_block_id").price, b.set_index("dom_block_id").price
    prices += [{"hotel": h, "same": float(ma[i]) == float(mb[i]), "rel": (float(mb[i]) - float(ma[i])) / float(ma[i])} for i in sa & sb]
st = pd.DataFrame(rows)
if len(st):
    print(f"  block-id chung {st.chung.sum()}/{st.nA.sum()} của lần A ({st.chung.sum() / st.nA.sum():.1%}) | bỏ dòng 1 khách của A: {st.chung_không_sg.sum()}/{st.nA_không_sg.sum()} ({st.chung_không_sg.sum() / max(st.nA_không_sg.sum(), 1):.1%})")
    print(f"  dòng 1 khách: A {st.sgA.sum()}, B {st.sgB.sum()}, chung {st.sg_chung.sum()}")
    pr = pd.DataFrame(prices)
    ch = pr[~pr.same]
    print(f"  giá của block-id chung: {len(pr)} | KHÔNG đổi {pr.same.mean():.1%} | khi đổi: median {ch.rel.median():+.1%}, IQR [{ch.rel.quantile(.25):+.1%}, {ch.rel.quantile(.75):+.1%}]" if len(ch) else f"  giá của block-id chung: {len(pr)} | KHÔNG đổi {pr.same.mean():.1%}")
    low = st.assign(giu=lambda d: d.chung / d.nA).sort_values("giu").head(12)
    print("  12 hotel giữ ít block-id nhất:"); print(low[["hotel", "nA", "nB", "chung", "sgA", "sgB"]].to_string(index=False))

print("\n" + "=" * 100)
print("C-4. NHÓM TRÙNG canonical key theo thời gian")
gk = lambda o: {(r.hotel_id, r.checkin, r.rik, r.rpk): r.g0_size for r in o.drop_duplicates("g0").itertuples()}
ka, kb = gk(oA), gk(oB)
da = {k for k, v in ka.items() if v >= 2}; db_ = {k for k, v in kb.items() if v >= 2}
both = da & db_
print(f"  nhóm trùng A {len(da)} | B {len(db_)} | cả hai {len(both)} → {len(both) / max(len(da), 1):.1%} nhóm trùng của A còn trùng ở B; {len(both) / max(len(db_), 1):.1%} nhóm trùng của B đã trùng ở A")

print("\n" + "=" * 100)
print("C-5. KHOẢNG CHÊNH dòng 2 khách − 1 khách theo hotel (họ ghép), A vs B")
def gaps(o):
    out = {}
    for h, x in o[o.single_guest | o.hotel_id.isin(o[o.single_guest].hotel_id)].groupby("hotel_id"):
        one = x[x.single_guest].groupby("fam").price.min(); two = x[~x.single_guest].groupby("fam").price.min()
        jj = pd.concat([two.rename("p2"), one.rename("p1")], axis=1, join="inner")
        if len(jj) >= 3:
            g = (jj.p2 - jj.p1).round().astype(int)
            m, c = collections.Counter(g).most_common(1)[0]
            out[h] = (m, round(c / len(jj), 2), len(jj))
    return out
gA, gB = gaps(oA), gaps(oB)
common = sorted(set(gA) & set(gB))
same = [h for h in common if gA[h][0] == gB[h][0]]
print(f"  hotel có khoảng chênh đo được ở cả hai lần: {len(common)} | cùng khoảng chênh phổ biến: {len(same)} ({len(same) / max(len(common), 1):.1%})")
diff = [(h, gA[h], gB[h]) for h in common if gA[h][0] != gB[h][0]]
for h, a, b in diff[:15]:
    print(f"    khác: {h}: A {a} vs B {b}")

print("\n" + "=" * 100)
print("C-6. N1 (câu phủ định bữa sáng)")
nA = set(oA[oA.meal.str.contains("Không bao gồm bữa sáng", regex=False)].hotel_id); nB = set(oB[oB.meal.str.contains("Không bao gồm bữa sáng", regex=False)].hotel_id)
print(f"  hotel có câu phủ định: A {len(nA)}, B {len(nB)}, chung {len(nA & nB)}; chỉ ở A {sorted(nA - nB)[:8]}; chỉ ở B {sorted(nB - nA)[:8]}")
