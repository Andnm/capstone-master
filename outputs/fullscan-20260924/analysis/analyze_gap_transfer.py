"""Quy tắc "khoảng chênh cố định theo hotel" (T3) học ở MỘT thời điểm, đánh giá ở thời điểm KHÁC — 10 hotel hay có dòng 1 khách. Claude, 2026-09-25.

Nhãn = cảnh báo "Chỉ dành cho 1 khách" (hoặc thành phần 3 của block-id = 1) đọc từ HTML của trang 2 người lớn ở trạng thái BẬT; quy tắc CHỈ nhìn giá trong cùng nhóm canonical key
(G = khoảng chênh cặp giá phổ biến nhất giữa các option trong nhóm trùng của hotel; dự đoán dòng 1 khách = option có option khác cùng nhóm cao hơn đúng G). Giống P-4 của analyze_probe.py.
Các hướng:
  D1  học từ mini run #1 (24/09 17:25, 3 ngày check-in) → đánh giá trên lần quét 2 (25/09 ~21:1x, check-in 2026-10-11) — cách ~28 giờ, đúng thứ tự thời gian
  D3  học từ mini run #1 → đánh giá trên mini run #3 (24/09 20:03, 3 hotel) — con số 163/163 ở 07 (tham chiếu)
  D2  học từ lần quét 2 (chỉ ngày 10-11) → đánh giá trên mini run #1 (3 ngày) — KHÔNG đúng thứ tự thời gian; chỉ để xem khoảng chênh có ổn định không
Gate 05 §2.1 (precision Wilson-95% cận dưới ≥0,99; ≥381 ca dự đoán không lỗi; ≥3 thời điểm cào × ≥3 ngày check-in; áp cho từng hotel/template): báo rõ chỗ nào chưa đạt. CHỈ ĐỌC.
Chạy:  backend\\venv\\Scripts\\python.exe outputs\\fullscan-20260924\\analysis\\analyze_gap_transfer.py [RUN_QUÉT=3]   (PYTHONIOENCODING=utf-8)
"""
import collections
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
from minicrawl_common import RUN_ROOT as MINI_RUN, load_run, wilson_lower  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
RUN = int(sys.argv[1]) if len(sys.argv) > 1 else 3
FULL_DB, FULL_ART = "hotel_price_intel_fullscan_20260924", Path("D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/crawl_artifacts")
MINI_DB, MINI_ART = "hotel_price_intel_minicrawl_20260924", MINI_RUN / "crawl_artifacts"
HOT = ["hilton-saigon", "the-myst-dong-khoi", "mercure-vung-tau-vietnam", "ibis-styles-vung-tau", "dusit-le-palais-tu-hoa-hanoi", "movenpick-residences-phu-quoc",
       "starview-villa", "pearl-wealth-da-lat", "vinhomes-ocean-park-bunnys-homes-nature-room", "b-amp-k-homestay-glocery-store"]


def load(db, art, rid):
    _, o, _ = load_run(db, art, rid, hotels=HOT)
    return o[o.hotel_id.isin(HOT)]


def gaps(gx):
    ps = sorted(gx["price"].astype(int).tolist())
    return {b - a for a, b in itertools.combinations(ps, 2) if b > a}


def fit_gaps(o):
    """G theo hotel: khoảng chênh cặp phổ biến nhất (đếm theo nhóm trùng) + tỉ lệ nhóm trùng có khoảng chênh đó."""
    out = {}
    for h, s in o[o.g0_size >= 2].groupby("hotel_id"):
        cnt = collections.Counter()
        for _, gx in s.groupby("g0"):
            cnt.update(gaps(gx))
        if cnt:
            g, c = cnt.most_common(1)[0]
            out[h] = (g, c / s.g0.nunique(), s.g0.nunique())
    return out


def evaluate(fit, o, title):
    d = o[o.g0_size >= 2]
    rows, TP, PRED = [], 0, 0
    for h, (g, share, ng) in fit.items():
        s = d[d.hotel_id == h]
        if s.empty:
            continue
        prices = {gid: set(x.price.astype(int)) for gid, x in s.groupby("g0")}
        pred = s[s.apply(lambda r: (int(r["price"]) + g) in prices[r["g0"]], axis=1)]
        tp = int(pred.single_guest.sum()); n_sg = int(o[(o.hotel_id == h)].single_guest.sum())
        TP += tp; PRED += len(pred)
        rows.append({"hotel": h[:28], "G_đã_học": g, "%nhóm_lúc_học": round(share, 2), "sg_thật": n_sg, "dự_đoán": len(pred), "đúng": tp,
                     "precision": round(tp / len(pred), 3) if len(pred) else None, "recall": round(tp / n_sg, 3) if n_sg else None})
    SG = int(o.single_guest.sum())
    print(f"\n--- {title}")
    print(pd.DataFrame(rows).to_string(index=False))
    if PRED:
        print(f"  TỔNG: dự đoán {PRED}, đúng {TP} → precision {TP / PRED:.1%}; Wilson-95% cận dưới {wilson_lower(TP, PRED):.3f}; recall {TP / max(SG, 1):.1%} (trên {SG} dòng 1 khách thật)")
    else:
        print("  không có dự đoán nào")
    return PRED, TP


m1, m3, sc = load(MINI_DB, MINI_ART, 1), None, load(FULL_DB, FULL_ART, RUN)
try:
    _, m3, _ = load_run(MINI_DB, MINI_ART, 3)
except Exception as exc:  # noqa: BLE001
    print("(không nạp được mini run 3:", exc, ")")
print("=" * 100)
print(f"T3-transfer. mini run #1: {len(m1)} option, {int(m1.single_guest.sum())} dòng 1 khách, {m1.hotel_id.nunique()} hotel | quét 2 (run #{RUN}): {len(sc)} option, {int(sc.single_guest.sum())} dòng 1 khách, {sc.hotel_id.nunique()} hotel (ngày check-in {sorted(sc.checkin.unique())})")
fit1, fitS = fit_gaps(m1), fit_gaps(sc)
print("  G học từ mini run #1:", {h[:10]: g for h, (g, s, n) in fit1.items()})
print("  G học từ quét 2     :", {h[:10]: g for h, (g, s, n) in fitS.items()})
same = [h for h in fit1 if h in fitS and fit1[h][0] == fitS[h][0]]
print(f"  cùng G ở hai thời điểm: {len(same)}/{len(set(fit1) & set(fitS))} hotel: {[h[:12] for h in same]}")
p1, t1 = evaluate(fit1, sc, "D1  học mini run #1 (24/09 17:25) → đánh giá quét 2 (25/09, 10-11) — đúng thứ tự thời gian, cách ~28 giờ")
if m3 is not None:
    m3 = m3[m3.hotel_id.isin(HOT)]
    evaluate(fit1, m3, "D3  học mini run #1 → đánh giá mini run #3 (24/09 20:03) — tham chiếu 07")
evaluate(fitS, m1, "D2  học quét 2 (10-11) → đánh giá mini run #1 (3 ngày) — NGƯỢC thời gian, chỉ xem độ ổn định của khoảng chênh")
print("\nGate 05 §2.1 cho từng hotel/template: ≥3 thời điểm cào × ≥3 ngày check-in, cận dưới Wilson ≥0,99 (cần ≥381 ca dự đoán không lỗi). Ở đây quét 2 chỉ có MỘT ngày check-in ⇒ chưa đạt; "
      "số ca dự đoán theo hotel ở bảng trên cho thấy cả các hotel tốt nhất cũng xa 381 ⇒ kết quả chỉ là MÔ TẢ (sensitivity), quy tắc chưa được phép dùng.")
