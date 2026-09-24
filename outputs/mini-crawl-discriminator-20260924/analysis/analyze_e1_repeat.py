"""E1 — lặp cùng workbook ở thời điểm khác: run #1 (17:25–17:43) vs run #2 (~19:24–19:45), cùng DB cô lập, cùng 54 mục (hotel × check-in). Claude, 2026-09-24.

CHỈ ĐỌC (HTML.gz + DB mini). Trả lời: (1) mẫu trang có đổi giữa hai lần cào không (N2d)? (2) `data-block-id` và giá ổn định theo THỜI GIAN thế nào (khác với 0,70 đo giữa 3 ngày check-in cùng thời điểm)?
(3) nhóm trùng canonical và dòng 1 khách có lặp lại không? (4) quy tắc "chênh giá cố định theo hotel" fit trên run #1 có đúng ngoài mẫu (run #2) không? (5) câu phủ định bữa sáng (N1).
Chạy:  backend\\venv\\Scripts\\python.exe outputs\\mini-crawl-discriminator-20260924\\analysis\\analyze_e1_repeat.py   (cần PYTHONIOENCODING=utf-8)
"""
import collections
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minicrawl_common import RUN_ROOT, load_run, wilson_lower  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
MINI_DB = "hotel_price_intel_minicrawl_20260924"
ART = RUN_ROOT / "crawl_artifacts"
CONTROL = {"gardenplazasaigonparkroyal", "22land-residence-2", "diamond-sea-vung-tau", "taladalat-thanh-pho-da-lat123", "roma"}

dom1, o1, it1 = load_run(MINI_DB, ART, 1)
dom2, o2, it2 = load_run(MINI_DB, ART, 2)
HERE = Path(__file__).resolve().parent


def summary(name, dom, o, items):
    st = collections.Counter(i["status"] for i in items)
    d = o[o.g0_size >= 2]
    print(f"  {name}: item {dict(st)} | dòng DOM {len(dom)} | option lưu {len(o)} | nhóm trùng {d.g0.nunique()} ({len(d)} option = {len(d) / max(len(o), 1):.1%}) | dòng 1 khách {int(o.single_guest.sum())} ({o.single_guest.mean():.1%})")


print("=" * 100)
print("E1-1. TỔNG QUAN HAI LẦN CÀO")
summary("run #1 (17:25)", dom1, o1, it1)
summary("run #2 (19:24)", dom2, o2, it2)
bad = [(i["hotel_id"], str(i["checkin_date"]), i["status"]) for i in it2 if i["status"] not in ("success", "sold_out")]
print(f"  item run #2 không success/sold_out: {bad or 'không có'}")

# ------------------------------------------------------------------ 2. mẫu trang
print("\n" + "=" * 100)
print("E1-2. MẪU TRANG (dòng giá có ô occupancy hay không) — có đổi giữa hai lần cào không?")
occ = lambda dom: dom[dom.matched].groupby(["hotel_id", "checkin"])["has_occ_cell"].mean().rename("occ_cell_share")
t = pd.concat([occ(dom1).rename("run1"), occ(dom2).rename("run2")], axis=1)
chg = t[(t.run1.fillna(-1) != t.run2.fillna(-1))]
print(f"  cặp (hotel, check-in) so được: {int(t.notna().all(axis=1).sum())} | tỉ lệ ô-occupancy KHÁC giữa hai lần: {len(chg)}")
if len(chg):
    print(chg.to_string())
hot = t.groupby("hotel_id").agg(run1=("run1", "mean"), run2=("run2", "mean"))
print("  hotel không có ô occupancy ở CẢ hai lần:", sorted(hot.index[(hot.run1 == 0) & (hot.run2 == 0)]))
print("  hotel có ô occupancy một phần (mẫu không đồng nhất giữa các ngày/lần):", sorted(hot.index[((hot.run1 > 0) & (hot.run1 < 1)) | ((hot.run2 > 0) & (hot.run2 < 1))]))

# ------------------------------------------------------------------ 3. block-id + giá
print("\n" + "=" * 100)
print("E1-3. ỔN ĐỊNH `data-block-id` THEO THỜI GIAN (cùng hotel, cùng check-in, hai thời điểm cào)")
rows, price_rows = [], []
for (h, c), a in o1.groupby(["hotel_id", "checkin"]):
    b = o2[(o2.hotel_id == h) & (o2.checkin == c)]
    if b.empty:
        continue
    s1, s2 = set(a.dom_block_id), set(b.dom_block_id)
    inter = s1 & s2
    rows.append({"hotel": h, "checkin": c, "n1": len(a), "n2": len(b), "shared": len(inter), "jaccard": len(inter) / len(s1 | s2), "run1_kept": len(inter) / len(s1), "new_in_run2": len(s2 - s1)})
    m = a.set_index("dom_block_id")["price"]; n = b.set_index("dom_block_id")["price"]
    for bid in inter:
        price_rows.append({"hotel": h, "checkin": c, "bid": bid, "p1": float(m[bid]), "p2": float(n[bid])})
st = pd.DataFrame(rows)
tot_shared, tot1, tot2 = st.shared.sum(), st.n1.sum(), st.n2.sum()
print(f"  cặp so được: {len(st)} | option run #1: {tot1}, run #2: {tot2}, block-id chung: {tot_shared} → run #1 còn giữ {tot_shared / tot1:.1%}, run #2 có {tot_shared / tot2:.1%} là id đã thấy ở run #1")
print(f"  trung bình theo cặp: Jaccard {st.jaccard.mean():.2f} (min {st.jaccard.min():.2f}, max {st.jaccard.max():.2f}); (tham chiếu: giữa 3 ngày check-in cùng thời điểm, trung bình theo hotel 0,70)")
by_h = st.groupby("hotel").agg(n1=("n1", "sum"), n2=("n2", "sum"), shared=("shared", "sum")).assign(run1_kept=lambda d: (d.shared / d.n1).round(2))
print(by_h.sort_values("run1_kept").to_string())
pr = pd.DataFrame(price_rows)
if len(pr):
    same = (pr.p1 == pr.p2)
    rel = ((pr.p2 - pr.p1) / pr.p1)[~same]
    print(f"\n  giá của block-id chung: {len(pr)} | giá KHÔNG đổi: {same.mean():.1%} | khi đổi: median {rel.median():+.1%}, IQR [{rel.quantile(.25):+.1%}, {rel.quantile(.75):+.1%}], tăng {(rel > 0).mean():.0%}/giảm {(rel < 0).mean():.0%}")
    print("  tỉ lệ giá đổi theo hotel:", (1 - pr.assign(s=same).groupby("hotel")["s"].mean()).round(2).sort_values(ascending=False).head(8).to_dict())
st.to_csv(HERE / "e1_item_stability.csv", index=False, encoding="utf-8-sig")

# ------------------------------------------------------------------ 4. nhóm trùng
print("\n" + "=" * 100)
print("E1-4. NHÓM TRÙNG CANONICAL KEY CÓ LẶP LẠI KHÔNG? (nhóm nhận diện bằng hotel + check-in + room_identity_key + rate_plan_key)")
gk = lambda o: {(r.hotel_id, r.checkin, r.rik, r.rpk): r.g0_size for r in o.drop_duplicates("g0").itertuples()}
k1, k2 = gk(o1), gk(o2)
d1 = {k for k, v in k1.items() if v >= 2}; d2 = {k for k, v in k2.items() if v >= 2}
both = d1 & d2
print(f"  nhóm trùng run #1: {len(d1)} | run #2: {len(d2)} | trùng ở cả hai: {len(both)} → {len(both) / len(d1):.1%} nhóm trùng của run #1 vẫn trùng ở run #2; {len(both) / len(d2):.1%} nhóm trùng của run #2 đã trùng ở run #1")
sizes = lambda k, s: collections.Counter(k[x] for x in s)
print(f"  cỡ nhóm trùng: run #1 {dict(sorted(sizes(k1, d1).items()))} | run #2 {dict(sorted(sizes(k2, d2).items()))}")
print(f"  nhóm cỡ 1 ở run #1 nhưng ≥2 ở run #2: {sum(1 for k in k1 if k1[k] == 1 and k2.get(k, 0) >= 2)} | nhóm ≥2 ở run #1 nhưng cỡ 1 ở run #2: {sum(1 for k in k1 if k1[k] >= 2 and k2.get(k, 0) == 1)} | biến mất hẳn: {sum(1 for k in k1 if k1[k] >= 2 and k not in k2)}")
h_dup = lambda o: o[o.g0_size >= 2].groupby("hotel_id")["g0"].nunique()
cmp = pd.concat([h_dup(o1).rename("nhóm_trùng_run1"), h_dup(o2).rename("nhóm_trùng_run2")], axis=1).fillna(0).astype(int)
print(cmp.to_string())

# ------------------------------------------------------------------ 5. dòng 1 khách
print("\n" + "=" * 100)
print("E1-5. DÒNG 'CHỈ DÀNH CHO 1 KHÁCH' theo thời gian")
s1, s2 = o1[o1.single_guest], o2[o2.single_guest]
tab = pd.concat([s1.groupby("hotel_id").size().rename("sg_run1"), s2.groupby("hotel_id").size().rename("sg_run2")], axis=1).fillna(0).astype(int)
ids1 = set(zip(s1.hotel_id, s1.checkin, s1.dom_block_id)); ids2 = set(zip(s2.hotel_id, s2.checkin, s2.dom_block_id))
print(f"  dòng 1 khách: run #1 {len(s1)}, run #2 {len(s2)} | block-id 1 khách chung: {len(ids1 & ids2)} → {len(ids1 & ids2) / len(ids1):.1%} của run #1 còn ở run #2")
print(tab.to_string())
print("  hotel có dòng 1 khách ở một lần nhưng không ở lần kia:", sorted(tab.index[(tab.sg_run1 == 0) != (tab.sg_run2 == 0)]) or "không có")

# ------------------------------------------------------------------ 6. quy tắc chênh giá cố định: fit run #1, kiểm run #2
print("\n" + "=" * 100)
print("E1-6. QUY TẮC 'CHÊNH GIÁ CỐ ĐỊNH THEO HOTEL' — fit trên run #1, ĐÁNH GIÁ NGOÀI MẪU trên run #2 (nhãn = HTML của run #2)")


def gaps(gx):
    ps = sorted(gx["price"].astype(int).tolist())
    return sorted({b - a for a, b in itertools.combinations(ps, 2) if b > a})


fit = {}
for h, sub in o1[o1.g0_size >= 2].groupby("hotel_id"):
    if not sub.single_guest.any():
        continue
    cnt = collections.Counter()
    for _, gx in sub.groupby("g0"):
        for gp in gaps(gx):
            cnt[gp] += 1
    gap, c = cnt.most_common(1)[0]
    fit[h] = (gap, c / sub.g0.nunique())
d2o = o2[o2.g0_size >= 2]
rows = []
for h, (gap, share) in sorted(fit.items()):
    sub = d2o[d2o.hotel_id == h]
    pred = sub[sub.apply(lambda r: any(p - int(r["price"]) == gap for p in sub.loc[sub.g0 == r["g0"], "price"].astype(int)), axis=1)] if len(sub) else sub
    tp = int(pred.single_guest.sum()); n_sg = int(sub.single_guest.sum())
    rows.append({"hotel": h, "G_fit_run1": gap, "share_run1": round(share, 2), "sg_run2": n_sg, "pred": len(pred), "TP": tp, "precision": round(tp / len(pred), 3) if len(pred) else float("nan"), "recall": round(tp / n_sg, 3) if n_sg else float("nan")})
r6 = pd.DataFrame(rows)
print(r6.to_string(index=False))
tp_all, pred_all, sg_all = r6.TP.sum(), r6.pred.sum(), int(o2.single_guest.sum())
print(f"  TỔNG (mọi hotel đã fit): dự đoán {pred_all}, đúng {tp_all} → precision {tp_all / pred_all:.1%} (Wilson-95% cận dưới {wilson_lower(tp_all, pred_all):.3f}), recall (trên mọi dòng 1 khách của run #2) {tp_all / sg_all:.1%}")
strong = r6[(r6.share_run1 >= 0.6) & (r6.G_fit_run1 > 1000)]
tp_s, pred_s = strong.TP.sum(), strong.pred.sum()
print(f"  CHỈ hotel chênh cố định rõ ở run #1 (share ≥ 0,6 và G > 1.000 VND: {sorted(strong.hotel)}): dự đoán {pred_s}, đúng {tp_s} → precision {tp_s / pred_s:.1%} (Wilson cận dưới {wilson_lower(tp_s, pred_s):.3f}), recall {tp_s / sg_all:.1%}")
print("  Gate đã đăng ký (05 §2.1): cận dưới ≥ 0,99 cần ≥ 381 ca dự đoán không lỗi và ≥3 thời điểm cào × ≥3 ngày check-in cho đúng hotel/template → chỉ có 2 thời điểm ⇒ kết quả này là MÔ TẢ, chưa thể là gate.")

# ------------------------------------------------------------------ 7. N1
print("\n" + "=" * 100)
print("E1-7. N1 — 'Không bao gồm bữa sáng' bị ghi breakfast_included=True")
for name, o in (("run #1", o1), ("run #2", o2)):
    neg = o[o.meal.str.contains("Không bao gồm bữa sáng", regex=False)]
    print(f"  {name}: {len(neg)} option, hotel {sorted(neg.hotel_id.unique())}, breakfast_included đã lưu {neg.breakfast_included.astype(int).value_counts().to_dict()}")
print(f"\nĐã ghi {HERE / 'e1_item_stability.csv'}")
