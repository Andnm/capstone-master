"""Mẻ thăm dò run #3 (2 người lớn, phiên Chrome mới, ~20:02–20:06): 3 hotel (Hilton, The Myst, Mercure) × 3 ngày check-in = 9 mục. Claude, 2026-09-24.

Mục đích: (1) sau đợt TẮT lúc 19:24 (run #2), chế độ có dòng "Chỉ dành cho 1 khách" có BẬT lại không? (2) khi BẬT: ổn định so với run #1 (2,5 giờ trước) và ghép GẦN ĐỒNG THỜI với trang 1 người lớn (E2, ~19:43–20:01);
(3) thử NGOÀI MẪU quy tắc "khoảng chênh cố định theo hotel" học từ run #1 trên nhãn HTML của run #3 (2 thời điểm cào thật sự khác nhau, có nhãn ở cả hai).
CHỈ ĐỌC. Chạy:  backend\\venv\\Scripts\\python.exe outputs\\mini-crawl-discriminator-20260924\\analysis\\analyze_probe.py   (cần PYTHONIOENCODING=utf-8)
"""
import collections
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minicrawl_common import RUN_ROOT, load_run, wilson_lower  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
DB = "hotel_price_intel_minicrawl_20260924"
DB1, ART1 = "hotel_price_intel_minicrawl_20260924_adult1", RUN_ROOT / "adult1" / "crawl_artifacts"
HOT = ["hilton-saigon", "the-myst-dong-khoi", "mercure-vung-tau-vietnam"]
ART = RUN_ROOT / "crawl_artifacts"

domP, oP, itP = load_run(DB, ART, 3)
dom1, o1, _ = load_run(DB, ART, 1)
dom2, o2, _ = load_run(DB, ART, 2)
domA, oA, _ = load_run(DB1, ART1, 1)
sub = lambda o: o[o.hotel_id.isin(HOT)]
o1, o2, oA = sub(o1), sub(o2), sub(oA)
dom1, dom2, domA = (d[d.hotel_id.isin(HOT)] for d in (dom1, dom2, domA))

print("=" * 100)
print("P-1. CHẾ ĐỘ (cột occupancy + dòng 'Chỉ dành cho 1 khách') theo thời điểm, 3 hotel × 3 ngày, 2 người lớn")
rows = []
for name, dom in (("run #1 17:25", dom1), ("run #2 19:24", dom2), ("run #3 20:03 (thăm dò)", domP)):
    d = dom[dom.matched]
    for h in HOT:
        x = d[d.hotel_id == h]
        rows.append({"lần cào": name, "hotel": h, "mục": x[["checkin"]].drop_duplicates().shape[0], "dòng": len(x), "ô_occupancy": int(x.has_occ_cell.sum()), "cảnh_báo_1_khách": int(x.only_for.notna().sum())})
print(pd.DataFrame(rows).to_string(index=False))

print("\n" + "=" * 100)
print("P-2. ỔN ĐỊNH block-id / dòng 1 khách giữa run #1 (17:25) và run #3 (20:03), cùng hotel/ngày, cùng 2 người lớn, cả hai đều BẬT")
rows, prices = [], []
for (h, c), b in oP.groupby(["hotel_id", "checkin"]):
    a = o1[(o1.hotel_id == h) & (o1.checkin == c)]
    if a.empty:
        continue
    s1, sP = set(a.dom_block_id), set(b.dom_block_id)
    sg1, sgP = set(a[a.single_guest].dom_block_id), set(b[b.single_guest].dom_block_id)
    rows.append({"hotel": h, "checkin": c, "n_run1": len(a), "n_run3": len(b), "id_chung": len(s1 & sP), "sg_run1": len(sg1), "sg_run3": len(sgP), "sg_chung": len(sg1 & sgP)})
    m, n = a.set_index("dom_block_id")["price"], b.set_index("dom_block_id")["price"]
    prices += [{"hotel": h, "same": float(m[i]) == float(n[i])} for i in s1 & sP]
st = pd.DataFrame(rows)
print(st.to_string(index=False))
print(f"  block-id chung {st.id_chung.sum()}/{st.n_run1.sum()} của run #1 ({st.id_chung.sum() / st.n_run1.sum():.1%}) | dòng 1 khách chung {st.sg_chung.sum()}/{st.sg_run1.sum()} | giá không đổi ở {pd.DataFrame(prices).same.mean():.1%} block-id chung")

print("\n" + "=" * 100)
print("P-3. GHÉP GẦN ĐỒNG THỜI: dòng 1 khách ở trang 2 NL (run #3, 20:03) vs trang 1 NL (E2, ~19:43–20:01) — cùng block-id, cùng giá?")
rows = []
for (h, c), b in oP.groupby(["hotel_id", "checkin"]):
    a = oA[(oA.hotel_id == h) & (oA.checkin == c)]
    if a.empty:
        continue
    p1 = a.set_index("dom_block_id")["price"]
    for r in b.itertuples():
        rows.append({"hotel": h, "sg": bool(r.single_guest), "c3": r.c3 if hasattr(r, "c3") else None, "in_E2": r.dom_block_id in p1.index, "same_price": (r.dom_block_id in p1.index) and float(p1[r.dom_block_id]) == float(r.price)})
pp = pd.DataFrame(rows)
for flag, lab in ((True, "DÒNG 1 KHÁCH (có cảnh báo)"), (False, "dòng còn lại")):
    x = pp[pp.sg == flag]
    if len(x):
        print(f"  {lab}: {len(x)} | có ở trang 1 NL cùng block-id: {x.in_E2.mean():.1%} | giá y hệt trong số đó: {x[x.in_E2].same_price.mean():.1%}")
print(pp.groupby(["hotel", "sg"]).agg(n=("in_E2", "size"), in_E2=("in_E2", "mean"), same_price=("same_price", "mean")).round(3).to_string())


def gaps(gx):
    ps = sorted(gx["price"].astype(int).tolist())
    return sorted({b - a for a, b in itertools.combinations(ps, 2) if b > a})


print("\n" + "=" * 100)
print("P-4. QUY TẮC 'CHÊNH GIÁ CỐ ĐỊNH THEO HOTEL' — học trên run #1 (17:25), ĐÁNH GIÁ NGOÀI MẪU trên run #3 (20:03, nhãn = cảnh báo HTML)")
fit = {}
for h in HOT:
    s = o1[(o1.hotel_id == h) & (o1.g0_size >= 2)]
    cnt = collections.Counter()
    for _, gx in s.groupby("g0"):
        for gp in gaps(gx):
            cnt[gp] += 1
    gap, c = cnt.most_common(1)[0]
    fit[h] = (gap, c / s.g0.nunique())
d3 = oP[oP.g0_size >= 2]
rows, TP, PRED, SG = 0, 0, 0, int(oP.single_guest.sum())
out = []
for h, (gap, share) in fit.items():
    s = d3[d3.hotel_id == h]
    pred = s[s.apply(lambda r: any(p - int(r["price"]) == gap for p in s.loc[s.g0 == r["g0"], "price"].astype(int)), axis=1)] if len(s) else s
    tp = int(pred.single_guest.sum()); n_sg = int(s.single_guest.sum())
    TP += tp; PRED += len(pred)
    out.append({"hotel": h, "G_học_từ_run1": gap, "%nhóm_run1": round(share, 2), "sg_run3": n_sg, "dự_đoán": len(pred), "đúng": tp, "precision": round(tp / len(pred), 3) if len(pred) else None, "recall": round(tp / n_sg, 3) if n_sg else None})
print(pd.DataFrame(out).to_string(index=False))
print(f"  TỔNG: dự đoán {PRED}, đúng {TP} → precision {TP / PRED:.1%}; Wilson-95% cận dưới {wilson_lower(TP, PRED):.3f}; recall {TP / SG:.1%} (trên {SG} dòng 1 khách của run #3)")
print("  Gate 05 §2.1: cận dưới ≥ 0,99 cần ≥ 381 ca không lỗi và ≥3 thời điểm × ≥3 ngày check-in ⇒ chỉ MÔ TẢ, chưa phải gate (có 2 thời điểm có nhãn: run #1 và run #3).")
