"""Quét toàn cohort (354 hotel × 1 ngày check-in, 2 người lớn, có lưu HTML) — phân tích cấp cohort. Claude, 2026-09-24.

CHỈ ĐỌC (HTML.gz + DB cô lập `hotel_price_intel_fullscan_20260924`). Dùng lại thư viện tách dòng/ghép DOM↔DB của mini-crawl (`minicrawl_common.py`) để số liệu so sánh được.
Câu hỏi (thread canonical-key-duplicates, 05/07): tỉ lệ TOÀN COHORT của (1) hotel có dòng "Chỉ dành cho 1 khách" (chế độ BẬT), (2) hotel mẫu gọn, (3) hotel in câu phủ định bữa sáng
(lỗi parser N1) và hotel in CẢ hai kiểu trong cùng trang, (4) nhóm trùng canonical key và nguyên nhân, (5) khoảng chênh 1 người theo hotel, (6) chế độ có ổn định trong lúc quét không.
Chạy:  backend\\venv\\Scripts\\python.exe outputs\\fullscan-20260924\\analysis\\analyze_fullscan.py [RUN_ID=1]   (cần PYTHONIOENCODING=utf-8)
"""
import collections
import re
import sys
from pathlib import Path

import pandas as pd

MINI = Path("D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
sys.path.insert(0, str(MINI))
from minicrawl_common import RUN_ROOT as MINI_RUN, connect, load_run  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 400); pd.set_option("display.max_colwidth", 60)
HERE = Path(__file__).resolve().parent
DB = "hotel_price_intel_fullscan_20260924"
ART = HERE.parent / "run" / "crawl_artifacts"
RUN_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
MINI_DB = "hotel_price_intel_minicrawl_20260924"
KNOWN_SG = ["hilton-saigon", "the-myst-dong-khoi", "mercure-vung-tau-vietnam", "ibis-styles-vung-tau", "dusit-le-palais-tu-hoa-hanoi", "movenpick-residences-phu-quoc",
            "starview-villa", "pearl-wealth-da-lat", "vinhomes-ocean-park-bunnys-homes-nature-room", "b-amp-k-homestay-glocery-store"]
NEG = re.compile(r"không\s+(?:bao\s+gồm|kèm|có)\s+(?:bữa\s+)?(?:ăn\s+)?sáng|breakfast\s+not\s+included|no\s+breakfast", re.I)
POS = re.compile(r"(?<!không )(?<!không\s\s)bao\s+gồm\s+bữa\s+sáng|bao\s+bữa\s+sáng|giá\s+bao\s+gồm\s+bữa\s+sáng|breakfast\s+included", re.I)

dom, o, items = load_run(DB, ART, RUN_ID)
c = connect(DB); cur = c.cursor(dictionary=True, buffered=True)
cur.execute("SET time_zone='+00:00'")
cur.execute("SELECT id, hotel_id, hotel_name_hint, market_hint, status, claimed_at, finished_at, saved_options_count, duplicate_options_count, last_error_code FROM crawl_run_items WHERE crawl_run_id=%s ORDER BY id", (RUN_ID,))
meta = pd.DataFrame(cur.fetchall())
meta["t_vn"] = pd.to_datetime(meta.claimed_at) + pd.Timedelta(hours=7)
city = meta.set_index("id")["market_hint"]
o["city"] = o.item_id.map(city)
dom["city"] = dom.item_id.map(city)

print("=" * 100)
print(f"F-1. TỔNG QUAN run #{RUN_ID} ({DB})")
st = meta.status.value_counts().to_dict()
print(f"  item theo trạng thái: {st} | thời gian claim {meta.t_vn.min():%H:%M:%S} → {meta.t_vn.max():%H:%M:%S} VN")
print(f"  hotel có option: {o.hotel_id.nunique()} | option lưu: {len(o)} | dòng DOM: {len(dom)} | option/mục: median {o.groupby('item_id').size().median():.0f}, mean {o.groupby('item_id').size().mean():.1f}")
print("  theo thành phố (item / hotel có option / option):")
print(pd.concat([meta.groupby("market_hint").size().rename("item"), o.groupby("city").hotel_id.nunique().rename("hotel_co_option"), o.groupby("city").size().rename("option")], axis=1).fillna(0).astype(int).to_string())
bad = meta[~meta.status.isin(["success", "sold_out", "queued", "running"])]
if len(bad):
    print("  item KHÔNG success/sold_out:"); print(bad[["id", "hotel_name_hint", "status", "last_error_code"]].to_string(index=False))

# ------------------------------------------------------------------ 2. trạng thái trang
print("\n" + "=" * 100)
print("F-2. TRẠNG THÁI TRANG: cột occupancy + cảnh báo 'Chỉ dành cho 1 khách' (chế độ BẬT/TẮT), theo hotel (mỗi hotel 1 mục)")
d = dom[dom.matched]
pg = d.groupby("item_id").agg(hotel_id=("hotel_id", "first"), city=("city", "first"), rows=("dom_idx", "size"), occ_cells=("has_occ_cell", "sum"), alerts=("only_for", lambda s: int(s.notna().sum())))
pg["template"] = pg.apply(lambda r: "có_cột_số_người" if r.occ_cells == r.rows else ("gọn(không_cột)" if r.occ_cells == 0 else "hỗn_hợp"), axis=1)
pg["sg_on"] = pg.alerts > 0
pg["state"] = pg.apply(lambda r: "BẬT(có dòng 1 khách)" if r.sg_on else ("có_cột_nhưng_0_dòng_1_khách" if r.template != "gọn(không_cột)" else "gọn"), axis=1)
print(pg.state.value_counts().to_string())
n = len(pg)
print(f"\n  Trên {n} hotel có option: BẬT {int(pg.sg_on.sum())} ({pg.sg_on.mean():.1%}) | có cột nhưng 0 dòng 1 khách {int((pg.state == 'có_cột_nhưng_0_dòng_1_khách').sum())} | gọn {int((pg.state == 'gọn').sum())}")
print(pd.crosstab(pg.city, pg.state).to_string())
sgrows = o[o.single_guest]
print(f"\n  dòng 1 khách: {len(sgrows)} = {len(sgrows) / len(o):.1%} option lưu; ở {sgrows.hotel_id.nunique()} hotel; median {sgrows.groupby('hotel_id').size().median():.0f} dòng/hotel")
print("  hotel BẬT theo tỉ lệ dòng 1 khách trong option của hotel đó (top 15):")
r = (sgrows.groupby("hotel_id").size() / o.groupby("hotel_id").size()).dropna().sort_values(ascending=False)
print(r.head(15).round(2).to_string())
kn = pg[pg.hotel_id.isin(KNOWN_SG)][["hotel_id", "rows", "alerts", "state"]]
kn = kn.merge(meta[["hotel_id", "t_vn"]].drop_duplicates("hotel_id"), on="hotel_id", how="left").sort_values("t_vn")
print("\n  10 hotel hay có dòng 1 khách (từ dữ liệu vận hành) — trạng thái trong lúc quét (nếu có mặt trong danh sách):")
print(kn.to_string(index=False))

# ------------------------------------------------------------------ 3. nhóm trùng
print("\n" + "=" * 100)
print("F-3. NHÓM TRÙNG CANONICAL KEY (mỗi mục × room_identity_key × rate_plan_key, ≥2 option)")
dup = o[o.g0_size >= 2]
G = dup.g0.nunique()
print(f"  nhóm: {o.g0.nunique()} | nhóm trùng {G} ({G / o.g0.nunique():.1%}) | option trong nhóm trùng {len(dup)} ({len(dup) / len(o):.1%}) | cỡ nhóm trùng: {dict(sorted(collections.Counter(dup.groupby('g0').size()).items()))}")
hd = dup.groupby("hotel_id").g0.nunique().sort_values(ascending=False)
print(f"  hotel có nhóm trùng: {len(hd)}/{o.hotel_id.nunique()} ({len(hd) / o.hotel_id.nunique():.1%}) | top 10% hotel ({max(1, round(0.1 * o.hotel_id.nunique()))}) chiếm {hd.head(max(1, round(0.1 * o.hotel_id.nunique()))).sum() / G:.1%} nhóm trùng")
print("  hotel có nhóm trùng theo trạng thái trang:", pd.crosstab(pg.set_index("hotel_id").state.reindex(hd.index).fillna("?"), pd.Series(1, index=hd.index)).iloc[:, 0].to_dict())
dims = ["single_guest", "meal", "cancel", "pay", "perks"]
lab = {"single_guest": "1 khách", "meal": "bữa ăn", "cancel": "hủy", "pay": "thanh toán", "perks": "tiện ích/gói"}
diffs = {}
for gid, x in dup.groupby("g0"):
    ds = [k for k in dims if x[k].astype(str).nunique() > 1]
    xo = x[~x.single_guest]
    if xo.dom_occ_max.dropna().nunique() > 1:
        ds.append("occupancy≠")
    diffs[gid] = ds
tab = collections.Counter(" + ".join(lab.get(k, k) for k in v) or "(không khác gì)" for v in diffs.values())
print("  chiều khác nhau trong nhóm trùng (mỗi nhóm đúng 1 tổ hợp):")
for k, v in tab.most_common(10):
    print(f"    {k:55s} {v:5d} ({v / G:5.1%})")
only_sg = sum(1 for v in diffs.values() if v == ["single_guest"])
no_sg = o[~o.single_guest]
sz = no_sg.groupby("g0").size()
print(f"\n  nhóm CHỈ khác dòng 1 khách: {only_sg} ({only_sg / G:.1%}) | nếu bỏ mọi dòng 1 khách: nhóm trùng còn {int((sz >= 2).sum())} ({int((sz >= 2).sum()) / max(G, 1):.1%} của {G})")
uniq_hotels_before = set(o[o.g0_size == 1].hotel_id); uniq_hotels_after = set(no_sg.groupby('g0').filter(lambda x: len(x) == 1).hotel_id)
print(f"  hotel có ≥1 option mang khóa duy nhất: trước {len(uniq_hotels_before)} → sau khi bỏ dòng 1 khách {len(uniq_hotels_after)} / {o.hotel_id.nunique()}")
zero_uniq = sorted(set(o.hotel_id) - uniq_hotels_before)
print(f"  hotel KHÔNG có option nào mang khóa duy nhất (series bị chặn hoàn toàn ở ngày này): {len(zero_uniq)}")

# ------------------------------------------------------------------ 4. N1
print("\n" + "=" * 100)
print("F-4. N1 — câu phủ định bữa sáng bị ghi breakfast_included=True")
o["neg"] = o.dom_conditions.map(lambda cs: any(NEG.search(x) for x in cs) if isinstance(cs, (list, tuple)) else False)
o["pos"] = o.dom_conditions.map(lambda cs: any(POS.search(x) for x in cs) if isinstance(cs, (list, tuple)) else False)
neg = o[o.neg]
print(f"  option có dòng phủ định: {len(neg)} ({len(neg) / len(o):.1%}) ở {neg.hotel_id.nunique()} hotel | breakfast_included đã lưu của các option đó: {neg.breakfast_included.astype(int).value_counts().to_dict()}")
per = o.groupby("hotel_id").agg(n=("neg", "size"), neg=("neg", "sum"), pos=("pos", "sum"))
mixed = per[(per.neg > 0) & (per.pos > 0)]
allneg = per[(per.neg > 0) & (per.pos == 0)]
print(f"  hotel có phủ định: {int((per.neg > 0).sum())} | CHỈ phủ định (không dòng khẳng định nào): {len(allneg)} | in CẢ HAI kiểu trong cùng trang (nguy cơ gộp có/không bữa sáng): {len(mixed)}")
if len(mixed):
    print(mixed.assign(share_neg=lambda d: (d.neg / d.n).round(2)).to_string())
print("  mẫu câu phủ định gặp:", collections.Counter(x for cs in neg.dom_conditions for x in cs if NEG.search(x)).most_common(5))

# ------------------------------------------------------------------ 5. khoảng chênh 1 khách theo hotel
print("\n" + "=" * 100)
print("F-5. KHOẢNG CHÊNH GIÁ dòng 2 khách − dòng 1 khách theo hotel (ghép theo 'họ' room/rate/meal/cờ/gói, trong CÙNG trang 2 người lớn khi BẬT)")


def fam(bid):
    p = str(bid).split("_")
    return (p[0], p[1], p[3] if len(p) > 3 else "", p[4] if len(p) > 4 else "", "_".join(p[5:]) if len(p) > 5 else "") if len(p) >= 5 else (bid,)


o["fam"] = o.dom_block_id.map(fam)
rows = []
for h, x in o[o.hotel_id.isin(sgrows.hotel_id.unique())].groupby("hotel_id"):
    one = x[x.single_guest].groupby("fam").price.min()
    two = x[~x.single_guest].groupby("fam").price.min()
    j = pd.concat([two.rename("p2"), one.rename("p1")], axis=1, join="inner")
    if not len(j):
        continue
    gap = (j.p2 - j.p1).round().astype(int)
    m, cnt = collections.Counter(gap).most_common(1)[0]
    rows.append({"hotel": h, "họ_ghép": len(j), "chênh_phổ_biến": m, "%họ": round(cnt / len(j), 2), "median_tỉ_lệ_p2/p1": round(float((j.p2 / j.p1).median()), 3)})
gt = pd.DataFrame(rows, columns=["hotel", "họ_ghép", "chênh_phổ_biến", "%họ", "median_tỉ_lệ_p2/p1"]).sort_values("họ_ghép", ascending=False)
print(f"  hotel có ≥1 họ ghép được: {len(gt)}")
if len(gt):
    print(gt.head(25).to_string(index=False))
    print(f"  hotel chênh CỐ ĐỊNH (≥60% họ cùng một khoảng chênh, ≥5 họ): {int(((gt['%họ'] >= 0.6) & (gt['họ_ghép'] >= 5)).sum())}/{len(gt)}")

# ------------------------------------------------------------------ 6. đối chiếu với mini-crawl (18 hotel, cùng ngày check-in 2026-10-11)
print("\n" + "=" * 100)
print("F-6. ĐỐI CHIẾU VỚI MINI-CRAWL (cùng hotel, cùng check-in 2026-10-11): block-id, dòng 1 khách, giá")
mini_art = MINI_RUN / "crawl_artifacts"
res = []
for rid, lab_ in ((1, "run1 17:25"), (2, "run2 19:24"), (3, "run3 20:03")):
    try:
        _, om, _ = load_run(MINI_DB, mini_art, rid)
    except Exception as exc:  # noqa: BLE001
        print(f"  (bỏ qua run {rid}: {exc})"); continue
    om = om[om.checkin == "2026-10-11"]
    for h, a in om.groupby("hotel_id"):
        b = o[o.hotel_id == h]
        if b.empty:
            continue
        s1, s2 = set(a.dom_block_id), set(b.dom_block_id)
        sa, sb = set(a[a.single_guest].dom_block_id), set(b[b.single_guest].dom_block_id)
        res.append({"mini": lab_, "hotel": h, "n_mini": len(a), "n_scan": len(b), "id_chung": len(s1 & s2), "sg_mini": len(sa), "sg_scan": len(sb), "sg_chung": len(sa & sb)})
rs = pd.DataFrame(res)
if len(rs):
    for lab_, x in rs.groupby("mini"):
        print(f"  {lab_}: {len(x)} hotel | block-id chung {x.id_chung.sum()}/{x.n_mini.sum()} ({x.id_chung.sum() / x.n_mini.sum():.1%}) | dòng 1 khách mini {x.sg_mini.sum()}, scan {x.sg_scan.sum()}, chung {x.sg_chung.sum()}")
    print(rs[rs.mini == "run3 20:03"].to_string(index=False))

# ------------------------------------------------------------------ 7. CSV
hot = pg.reset_index().merge(o.groupby("hotel_id").agg(options=("g0", "size"), sg_rows=("single_guest", "sum"), dup_groups=("g0", lambda s: int((o.loc[s.index, "g0_size"] >= 2).groupby(s).any().sum())), neg_rows=("neg", "sum"), pos_rows=("pos", "sum")).reset_index(), on="hotel_id", how="left")
hot = hot.merge(meta[["id", "hotel_name_hint", "t_vn"]].rename(columns={"id": "item_id"}), on="item_id", how="left")
out = HERE / f"fullscan_hotel_summary_run{RUN_ID}.csv"
hot.to_csv(out, index=False, encoding="utf-8-sig")
print(f"\nĐã ghi {out} ({len(hot)} hotel)")
