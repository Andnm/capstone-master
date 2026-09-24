"""E2 — paired-context 1 vs 2 người lớn ở TRANG KẾT QUẢ công khai (không đi vào phễu đặt phòng). Claude, 2026-09-24.

Bên 1 người lớn: DB `hotel_price_intel_minicrawl_20260924_adult1` (run #1, ~19:43–20:01), cùng workbook + 3 ngày check-in, chạy bằng process riêng ghi đè `_SCRAPE_QUERY`
(xem README_e2.md; `crawl_context` trong DB đó ghi "adults: 2" do hằng số cứng `data_contract.py:16`, URL thật là 1 người lớn).
Bên 2 người lớn có HAI trạng thái khác nhau của Booking cho CÙNG hotel/ngày (phát hiện ở E1): run #1 (17:25, có dòng "Chỉ dành cho 1 khách" + cột occupancy = chế độ BẬT)
và run #2 (19:24, KHÔNG có dòng 1 khách = chế độ TẮT). Nên script so trang 1 người lớn với CẢ HAI:
  * với run #1 (BẬT): dòng "chỉ 1 khách" có phải chính là giá/dòng mà người tìm 1 người lớn thấy? (P1 ở mức trang kết quả)
  * với run #2 (TẮT): cùng thời điểm gần nhau (~20 phút) nên phản ánh rõ nhất cách trang thay đổi theo số người tìm.
CHỈ ĐỌC (HTML.gz + DB). Chạy:  backend\\venv\\Scripts\\python.exe outputs\\mini-crawl-discriminator-20260924\\analysis\\analyze_e2_paired_context.py   (cần PYTHONIOENCODING=utf-8)
"""
import collections
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from minicrawl_common import RUN_ROOT, load_run  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 300); pd.set_option("display.max_colwidth", 70)
HERE = Path(__file__).resolve().parent
DB2, ART2 = "hotel_price_intel_minicrawl_20260924", RUN_ROOT / "crawl_artifacts"
DB1, ART1, RUN1 = "hotel_price_intel_minicrawl_20260924_adult1", RUN_ROOT / "adult1" / "crawl_artifacts", 1
CONTROL = {"gardenplazasaigonparkroyal", "22land-residence-2", "diamond-sea-vung-tau", "taladalat-thanh-pho-da-lat123", "roma"}


def comp3(x):
    return "1" if x == "1" else ("0" if x == "0" else ("N>=2" if isinstance(x, str) and x.isdigit() else "?"))


def prep(dom, o):
    o["c3"] = o["dom_bid_occ"].map(comp3)
    dom["c3"] = dom["bid_occ"].map(comp3)
    dom["alert"] = dom["only_for"].notna()
    return dom, o


def fam(bid):
    p = bid.split("_")
    return (p[0], p[1], p[3] if len(p) > 3 else "", p[4] if len(p) > 4 else "", "_".join(p[5:]) if len(p) > 5 else "") if len(p) >= 5 else (bid,)


dom1, o1, it1 = load_run(DB1, ART1, RUN1)      # 1 người lớn
dom1, o1 = prep(dom1, o1)
o1["fam"] = o1.dom_block_id.map(fam)
runs = {}
for rid, label in ((1, "run #1 (2 NL, 17:25, chế độ BẬT)"), (2, "run #2 (2 NL, 19:24, chế độ TẮT)")):
    d, o, it = load_run(DB2, ART2, rid)
    d, o = prep(d, o)
    o["fam"] = o.dom_block_id.map(fam)
    runs[rid] = (label, d, o, it)

print("=" * 100)
print("E2-0. TỔNG QUAN CÁC NGỮ CẢNH")
for name, items, dom, o in [(v[0], v[3], v[1], v[2]) for v in runs.values()] + [("1 NL (adult1, ~19:43)", it1, dom1, o1)]:
    st = collections.Counter(i["status"] for i in items)
    ok_req = sum(1 for i in items if "group_adults=1" in (i.get("requested_hotel_link") or "")); ok_fin = sum(1 for i in items if "group_adults=1" in (i.get("hotel_link") or ""))
    print(f"  {name}: item {dict(st)} | dòng DOM {len(dom)} | option lưu {len(o)} | ô-occupancy {int(dom.has_occ_cell.sum())} | cảnh báo 'Chỉ dành cho…' {int(dom.alert.sum())} | comp3 {dom.c3.value_counts().to_dict()}"
          f" | URL group_adults=1: yêu cầu {ok_req}/{len(items)}, cuối {ok_fin}/{len(items)}")
print("  cảnh báo trong trang 1 người lớn:", dom1.loc[dom1.alert, "only_for"].value_counts().head(5).to_dict() or "KHÔNG có")


def pair(o2, o1):
    keys = sorted(set(zip(o2.hotel_id, o2.checkin)) & set(zip(o1.hotel_id, o1.checkin)))
    rows = []
    for h, c in keys:
        a = o2[(o2.hotel_id == h) & (o2.checkin == c)].set_index("dom_block_id")
        b = o1[(o1.hotel_id == h) & (o1.checkin == c)].set_index("dom_block_id")
        for bid, r in a.iterrows():
            rows.append({"hotel": h, "checkin": c, "bid": bid, "fam": r.fam, "c3": r.c3, "sg": bool(r.single_guest), "in_other": bid in b.index, "p2": float(r.price), "p1": float(b.loc[bid, "price"]) if bid in b.index else None, "only1": False})
        for bid, r in b.iterrows():
            if bid not in a.index:
                rows.append({"hotel": h, "checkin": c, "bid": bid, "fam": r.fam, "c3": r.c3, "sg": bool(r.single_guest), "in_other": False, "p2": None, "p1": float(r.price), "only1": True})
    return keys, pd.DataFrame(rows)


allpairs = []
for rid, (label, dom2, o2, it2) in runs.items():
    print("\n" + "=" * 100)
    print(f"E2-{rid}. TRANG 1 NGƯỜI LỚN so với {label} — ghép theo `data-block-id` (cùng hotel, cùng check-in)")
    keys, pr = pair(o2, o1)
    pr["vs"] = f"run{rid}"
    allpairs.append(pr)
    print(f"  cặp (hotel, check-in) so được: {len(keys)}  (trang 1 NL mới xong {o1[['hotel_id','checkin']].drop_duplicates().shape[0]} cặp)")
    A = pr[pr.p2.notna()]
    print("  Dòng của trang 2 NL — theo thành phần 3 của block-id — còn có mặt ở trang 1 NL:")
    print(A.groupby("c3").agg(n=("bid", "size"), co_o_trang_1_nguoi=("in_other", "mean")).assign(co_o_trang_1_nguoi=lambda d: d.co_o_trang_1_nguoi.round(3)).to_string())
    B = pr[pr.only1]
    print(f"  Dòng CHỈ có ở trang 1 NL: {len(B)} — comp3: {B.c3.value_counts().to_dict()} | hotel: {B.hotel.value_counts().head(8).to_dict()}")
    both = A[A.in_other]
    same = (both.p1 == both.p2)
    print(f"  giá của block-id chung: {len(both)} | KHÔNG đổi: {same.mean():.1%}" + (f" | khi đổi: median p1/p2 = {(both.p1 / both.p2)[~same].median():.3f}" if (~same).any() else ""))
    sg = A[A.sg]
    if len(sg):
        print(f"  DÒNG 'CHỈ DÀNH CHO 1 KHÁCH' của trang 2 NL: {len(sg)} | có mặt (cùng block-id) ở trang 1 NL: {int(sg.in_other.sum())} ({sg.in_other.mean():.1%}) | giá y hệt: {int(((sg.p1 == sg.p2) & sg.in_other).sum())}")
        print(sg.groupby("hotel").agg(n=("bid", "size"), con_lai=("in_other", "sum")).assign(ti_le=lambda d: (d.con_lai / d.n).round(2)).to_string())
        item_of = {(i["hotel_id"], str(i["checkin_date"])): i["id"] for i in it1}
        alert_map = {(r.item_id, r.block_id): r.alert for r in dom1.itertuples()}
        n_alert = n_tot = 0
        for r in sg[sg.in_other].itertuples():
            iid = item_of.get((r.hotel, r.checkin))
            if iid is not None:
                n_tot += 1; n_alert += int(bool(alert_map.get((iid, r.bid), False)))
        print(f"  trong số đó, dòng CÒN cảnh báo ở trang 1 NL: {n_alert}/{n_tot}")
        ex = []
        for r in sg.head(3000).itertuples():
            f2 = pr[(pr.hotel == r.hotel) & (pr.checkin == r.checkin) & (pr.fam == r.fam) & (pr.p2.notna())]
            f1 = pr[(pr.hotel == r.hotel) & (pr.checkin == r.checkin) & (pr.fam == r.fam) & (pr.p1.notna())]
            ex.append({"hotel": r.hotel, "checkin": r.checkin, "họ": r.fam[:2], "2NL_c3": tuple(sorted(f2.c3)), "2NL_giá": tuple(int(x) for x in f2.sort_values("c3").p2), "1NL_c3": tuple(sorted(f1.c3)), "1NL_giá": tuple(int(x) for x in f1.sort_values("c3").p1)})
        ex = pd.DataFrame(ex)
        print("  mẫu (các thành phần 3 của cùng 'họ' room/rate/meal/flag/gói) — số họ theo (2 NL → 1 NL):")
        print(ex.groupby(["2NL_c3", "1NL_c3"]).size().rename("n").reset_index().sort_values("n", ascending=False).head(8).to_string(index=False))
        print("  ví dụ mỗi hotel:"); print(ex.drop_duplicates("hotel").to_string(index=False))
    occ_share = dom2[dom2.matched].groupby("hotel_id")["has_occ_cell"].mean()
    compact = sorted(occ_share.index[occ_share == 0])
    rows = []
    for h in compact:
        s = pr[pr.hotel == h]
        rows.append({"hotel": h, "tập": "CONTROL" if h in CONTROL else "DUP", "2NL": int(s.p2.notna().sum()), "1NL": int(s.p1.notna().sum()), "chung": int((s.p1.notna() & s.p2.notna()).sum()),
                     "chỉ_2NL": int((s.p2.notna() & s.p1.isna()).sum()), "chỉ_1NL": int(s.only1.sum()), "c3_chỉ_2NL": dict(s[s.p2.notna() & s.p1.isna()].c3.value_counts()), "c3_chỉ_1NL": dict(s[s.only1].c3.value_counts())})
    if rows:
        print(f"\n  Hotel mẫu gọn ở {label.split(' (')[0]} (T4): dòng xuất hiện/biến mất giữa 1 và 2 NL")
        print(pd.DataFrame(rows).to_string(index=False))

# trong trang 1 NL: các thành phần 3 nào có mặt, theo hotel
print("\n" + "=" * 100)
print("E2-3. TRONG TRANG 1 NGƯỜI LỚN: thành phần 3 của block-id theo hotel (kiểm 'comp3 = số người tìm?')")
print(o1.groupby(["hotel_id", "c3"]).size().unstack(fill_value=0).to_string())
# ------------------------------------------------------------------ khoảng chênh 2 NL − 1 NL đo trực tiếp
print("\n" + "=" * 100)
print("E2-4. KHOẢNG CHÊNH GIÁ 2 NL − 1 NL THEO HOTEL: (a) có nhãn trong run #1 (dòng 1 khách vs dòng 2 khách cùng 'họ' ở trang 2 NL, chế độ BẬT); (b) đo trực tiếp bằng trang 1 NL (E2) vs 2 NL run #2 (chế độ TẮT)")


def fam_gaps(pr, two_mask, one_mask, p_two, p_one):
    two = pr[two_mask][["hotel", "checkin", "fam", p_two]].rename(columns={p_two: "p_two"}).groupby(["hotel", "checkin", "fam"]).p_two.agg(["min", "count"]).reset_index()
    one = pr[one_mask][["hotel", "checkin", "fam", p_one]].rename(columns={p_one: "p_one"}).groupby(["hotel", "checkin", "fam"]).p_one.agg(["min", "count"]).reset_index()
    j = two.merge(one, on=["hotel", "checkin", "fam"], suffixes=("_two", "_one"))
    j = j[(j.count_two == 1) & (j.count_one == 1)].copy()
    j["gap"] = (j.min_two - j.min_one).round().astype(int)
    return j


pr1, pr2 = allpairs[0], allpairs[1]
ga = fam_gaps(pr1, pr1.p2.notna() & (pr1.c3 != "1"), pr1.p2.notna() & (pr1.c3 == "1"), "p2", "p2")     # (a) có nhãn, cùng lúc, cùng trang
gb = fam_gaps(pr2, pr2.p2.notna() & (pr2.c3 != "1"), pr2.p1.notna() & (pr2.c3 == "1"), "p2", "p1")     # (b) trang 2 NL chế độ TẮT vs trang 1 NL


def modal(g):
    c = collections.Counter(g.gap)
    gap, n = c.most_common(1)[0]
    return gap, n / len(g)


rows = []
for h in sorted(set(ga.hotel) | set(gb.hotel)):
    a, b = ga[ga.hotel == h], gb[gb.hotel == h]
    ma = modal(a) if len(a) else (None, None)
    mb = modal(b) if len(b) else (None, None)
    rows.append({"hotel": h, "họ_a(nhãn,run1)": len(a), "chênh_phổ_biến_a": ma[0], "%_a": None if ma[1] is None else round(ma[1], 2), "họ_b(trực tiếp)": len(b), "chênh_phổ_biến_b": mb[0], "%_b": None if mb[1] is None else round(mb[1], 2),
                 "b_trùng_a": (mb[0] == ma[0]) if (ma[0] is not None and mb[0] is not None) else None,
                 "%họ_b_có_đúng_chênh_a": None if (ma[0] is None or not len(b)) else round(float((b.gap == ma[0]).mean()), 2)})
print(pd.DataFrame(rows).to_string(index=False))

# ------------------------------------------------------------------ dòng chỉ có ở trang 2 NL (vs run #2) ở hotel mẫu gọn
print("\n" + "=" * 100)
print("E2-5. HOTEL MẪU GỌN: dòng có ở trang 2 NL (run #2) nhưng KHÔNG có ở trang 1 NL — là dòng gì?")
_, o2b, _, _ = runs[2][1], runs[2][2], None, None
info = runs[2][2].set_index(["hotel_id", "checkin", "dom_block_id"])
comp_only2 = pr2[pr2.p2.notna() & pr2.p1.isna() & pr2.hotel.isin(["22land-residence-2", "gardenplazasaigonparkroyal", "intercontinental-westlake", "the-imperial-vung-tau", "sol-phu-quoc", "diamond-sea-vung-tau", "taladalat-thanh-pho-da-lat123", "roma"])]
if len(comp_only2):
    show = []
    for r in comp_only2.itertuples():
        try:
            x = info.loc[(r.hotel, r.checkin, r.bid)]
            show.append({"hotel": r.hotel, "checkin": r.checkin, "phòng": str(x.room_type_raw)[:32], "comp3": r.c3, "block-id": r.bid[-22:], "giá": int(r.p2), "max_occ_lưu": x.max_occupancy, "meal": str(x.meal)[:22], "hủy": str(x.cancel)[:24]})
        except KeyError:
            pass
    print(pd.DataFrame(show).head(30).to_string(index=False))
else:
    print("  (chưa có dòng nào — hoặc các hotel mẫu gọn chưa xong)")

pd.concat(allpairs).to_csv(HERE / "e2_pairs.csv", index=False, encoding="utf-8-sig")
print(f"\nĐã ghi {HERE / 'e2_pairs.csv'}")
