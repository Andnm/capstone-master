"""Phân tích E6 (cặp phiên Chrome đồng thời): trạng thái trang là của PHIÊN hay của THỜI ĐIỂM? Claude, 2026-09-26.

Đọc run/e6/pairNN_{A,B}.json + HTML.gz của từng trang (Mercure = dò họ dòng 1 khách; Roma = dò họ dòng Basic `bbasic_*`), tách dòng bằng `minicrawl_common.parse_rows`.
Nhãn phiên: 1G = ON nếu Mercure có ≥5 dòng cảnh báo "Chỉ dành cho 1 khách", OFF nếu 0 (Mercure ON: 54 dòng/27 cảnh báo; OFF: 27 dòng); BASIC = B+ nếu Roma có ≥1 dòng `bbasic`, B0 nếu 0.
Giả thuyết: (H_phiên) mỗi phiên bốc trạng thái độc lập ⇒ hai phiên trong một cặp lệch nhau ở tỉ lệ ≈ 2p(1-p) (p = tỉ lệ trạng thái hiếm hơn trong 2n phiên);
(H_thời điểm) trạng thái do hệ thống/thời điểm quyết ⇒ hai phiên đồng thời luôn giống nhau. In: bảng từng cặp, số cặp lệch, xác suất nhị thức P(D<=d) và P(D>=d) với q = 2p̂(1-p̂) (xấp xỉ), độ giống block-id giữa hai phiên.
CHỈ ĐỌC. Chạy (venv backend, PYTHONIOENCODING=utf-8):  python outputs/fullscan-20260924/analysis/analyze_e6_pairs.py
"""
import gzip
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, "D:/MSE/CAPSTONE/outputs/mini-crawl-discriminator-20260924/analysis")
from bs4 import BeautifulSoup  # noqa: E402
from minicrawl_common import parse_rows  # noqa: E402

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 40); pd.set_option("display.max_rows", 200)
E6 = Path("D:/MSE/CAPSTONE/outputs/fullscan-20260924/run/e6")
M, R = "mercure-vung-tau-vietnam", "roma"

recs = []
for f in sorted(E6.glob("pair*_*.json")):
    r = json.loads(f.read_text(encoding="utf-8"))
    for pg in r.get("pages", []):
        rec = {"pair": r["pair"], "side": r["side"], "slug": pg["slug"], "ok": pg["ok"], "fail": pg["fail"], "t1": pg["t1"], "final_url": pg.get("final_url") or "",
               "sold_out": pg.get("is_sold_out"), "rows": None, "alerts": None, "occ": None, "basic": None, "ids": frozenset()}
        h = pg.get("html")
        if pg["ok"] and h and Path(h).exists():
            rr = [x for x in parse_rows(BeautifulSoup(gzip.open(h, "rb").read().decode("utf-8", errors="replace"), "lxml")) if x["room_name"]]
            rec.update(rows=len(rr), alerts=sum(1 for x in rr if "Chỉ dành cho 1" in (x["only_for"] or "")), occ=sum(1 for x in rr if x["has_occ_cell"]),
                       basic=sum(1 for x in rr if x["is_basic"]), ids=frozenset(x["block_id"] for x in rr))
        recs.append(rec)
d = pd.DataFrame(recs)
if d.empty:
    sys.exit("chưa có dữ liệu E6")
d["chal"] = d.final_url.str.contains("chal_t", regex=False)
d["t1"] = pd.to_datetime(d.t1)


def label(g):
    m, r = g[g.slug == M], g[g.slug == R]
    v1 = vb = "?"
    if len(m) and m.ok.iloc[0] and (m.rows.iloc[0] or 0) > 0:
        v1 = "ON" if m.alerts.iloc[0] >= 5 else ("OFF" if m.alerts.iloc[0] == 0 else "?")
    if len(r) and r.ok.iloc[0] and (r.rows.iloc[0] or 0) > 0:
        vb = "B+" if r.basic.iloc[0] >= 1 else "B0"
    return pd.Series({"v1G": v1, "vBasic": vb, "mer_rows": (m.rows.iloc[0] if len(m) else None), "mer_alert": (m.alerts.iloc[0] if len(m) else None),
                      "roma_rows": (r.rows.iloc[0] if len(r) else None), "roma_basic": (r.basic.iloc[0] if len(r) else None),
                      "chal_first": bool(g.sort_values("t1").chal.iloc[0]) if len(g) else None})


s = d.groupby(["pair", "side"]).apply(label, include_groups=False).reset_index()
A, B = s[s.side == "A"].set_index("pair"), s[s.side == "B"].set_index("pair")
pairs = A.join(B, lsuffix="_A", rsuffix="_B", how="inner")
# độ giống block-id giữa hai phiên của cặp, theo trang
jac = {}
for slug in (M, R):
    for p, g in d[d.slug == slug].groupby("pair"):
        a, b = g[g.side == "A"], g[g.side == "B"]
        if len(a) and len(b) and a.rows.iloc[0] and b.rows.iloc[0]:
            u = a.ids.iloc[0] | b.ids.iloc[0]
            jac[(slug, p)] = len(a.ids.iloc[0] & b.ids.iloc[0]) / len(u) if u else 1.0
pairs["jac_mer"] = [jac.get((M, p)) for p in pairs.index]
pairs["jac_roma"] = [jac.get((R, p)) for p in pairs.index]
dt = d.groupby(["pair", "slug"]).t1.agg(lambda x: abs((x.max() - x.min()).total_seconds())).unstack()
pairs["lệch_giờ_mer_s"] = dt[M].reindex(pairs.index).round(1)

print("=" * 110)
print(f"E6. {len(pairs)} cặp phiên Chrome mới chạy đồng thời (A, B); trang Mercure (dò dòng 1 khách) và Roma (dò dòng bbasic), check-in 2026-10-11")
print(pairs[["v1G_A", "v1G_B", "mer_rows_A", "mer_rows_B", "vBasic_A", "vBasic_B", "roma_rows_A", "roma_rows_B", "roma_basic_A", "roma_basic_B", "jac_mer", "jac_roma", "lệch_giờ_mer_s"]].round(3).to_string())
bad = d[~d.ok]
if len(bad):
    print("\ntrang lỗi:", bad[["pair", "side", "slug", "fail"]].to_string(index=False))
print(f"\ntrang đầu tiên của mỗi phiên có tham số thử thách `chal_t` trong URL cuối: {int(s.chal_first.sum())}/{len(s)} phiên (mọi lượt cào vận hành cũng có đúng 1 mục/phiên như vậy)")


def binom_tail(n, q, k, upper):
    pm = lambda i: math.comb(n, i) * q ** i * (1 - q) ** (n - i)
    return sum(pm(i) for i in (range(k, n + 1) if upper else range(0, k + 1)))


def family(col, name):
    x = pairs[(pairs[f"{col}_A"] != "?") & (pairs[f"{col}_B"] != "?")]
    n = len(x)
    if n == 0:
        print(f"\n[{name}] không có cặp hợp lệ")
        return
    allv = pd.concat([x[f"{col}_A"], x[f"{col}_B"]])
    vc = allv.value_counts()
    minority = vc.idxmin() if len(vc) > 1 else vc.index[0]
    p = (allv == minority).mean() if len(vc) > 1 else 0.0
    disc = int((x[f"{col}_A"] != x[f"{col}_B"]).sum())
    q = 2 * p * (1 - p)
    print(f"\n[{name}] {n} cặp hợp lệ; trạng thái của {2 * n} phiên: {vc.to_dict()}; trạng thái hiếm '{minority}' p̂ = {p:.3f}")
    print(f"  cặp LỆCH: {disc}/{n}; kỳ vọng nếu mỗi phiên bốc độc lập: {n * q:.1f} (q = 2p̂(1-p̂) = {q:.3f})")
    if 0 < p < 1:
        print(f"  P(D<={disc}) = {binom_tail(n, q, disc, False):.4f} (nhỏ ⇒ ít lệch hơn mức độc lập ⇒ nghiêng về THỜI ĐIỂM/hệ thống); P(D>={disc}) = {binom_tail(n, q, disc, True):.4f}")
    else:
        print("  mọi phiên cùng một trạng thái ⇒ họ này KHÔNG phân biệt được hai giả thuyết trong lần chạy này")
    tab = pd.crosstab(x[f"{col}_A"], x[f"{col}_B"])
    print(tab.to_string())


family("v1G", "họ dòng 1 khách (Mercure)")
family("vBasic", "họ dòng bbasic (Roma)")
def fisher_two_sided(a, b, c, d_):
    """Fisher chính xác hai phía cho bảng [[a, b], [c, d_]]."""
    n1, n2, k = a + b, c + d_, a + c
    N = n1 + n2
    pm = lambda x: math.comb(n1, x) * math.comb(n2, k - x) / math.comb(N, k)
    p0 = pm(a)
    return sum(pm(x) for x in range(max(0, k - n2), min(n1, k) + 1) if pm(x) <= p0 + 1e-12)


sess = pd.concat([pairs[["v1G_A", "vBasic_A"]].set_axis(["v1G", "vBasic"], axis=1), pairs[["v1G_B", "vBasic_B"]].set_axis(["v1G", "vBasic"], axis=1)])
sess = sess[(sess.v1G != "?") & (sess.vBasic != "?")].reset_index(drop=True)
if len(sess):
    tb = pd.crosstab(sess.v1G, sess.vBasic)
    print(f"\n[liên hệ giữa hai họ TRONG cùng một phiên] {len(sess)} phiên; bảng (hàng: dòng 1 khách, cột: dòng bbasic):")
    print(tb.to_string())
    if tb.shape == (2, 2):
        print(f"  Fisher chính xác hai phía p = {fisher_two_sided(*tb.values.flatten()):.3f} (p nhỏ ⇒ hai họ không độc lập trong một phiên)")
pairs["biến_thể_A"] = pairs.v1G_A + "/" + pairs.vBasic_A
pairs["biến_thể_B"] = pairs.v1G_B + "/" + pairs.vBasic_B
ok = pairs[(~pairs.biến_thể_A.str.contains(r"\?")) & (~pairs.biến_thể_B.str.contains(r"\?"))]
print(f"\n[biến thể chung (1G/Basic)] {len(ok)} cặp hợp lệ; lệch ở ít nhất một họ: {int((ok.biến_thể_A != ok.biến_thể_B).sum())}")
print("  phân bố biến thể của các phiên:", pd.concat([ok.biến_thể_A, ok.biến_thể_B]).value_counts().to_dict())
print("\nĐọc kết quả: nếu số cặp lệch xấp xỉ kỳ vọng độc lập ⇒ trạng thái do PHIÊN (bốc lúc tạo phiên, tức lần tải đầu có `chal_t`); nếu ~0 cặp lệch trong khi kỳ vọng ≥ 3 ⇒ do THỜI ĐIỂM/hệ thống.")
