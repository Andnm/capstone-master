"""Gate muc 2 (tiep): dong thuan not_bookable GIUA CAC NGUON cung (hotel, ngay crawl VN). CHI DOC.

Y nghia: neu cung hotel-ngay ma nguon A thay not_bookable con nguon B (may khac, session khac) thay co gia thi day la page/session-state;
neu ca hai cung not_bookable thi hop ly la property-state. Luu y: circuit-break theo hotel-run khien 'mixed trong cung nguon' = 0 theo thiet ke,
nen chi so GIUA nguon moi co thong tin.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(r"D:\MSE\CAPSTONE\hotel-price-intelligence\backend")
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
from app.warehouse.connection import warehouse_connection  # noqa: E402

SQL = """
SELECT m.source_code, cri.hotel_id,
       DATE(CONVERT_TZ(cr.started_at, '+00:00', '+07:00')) AS d,
       SUM(cri.status='success') n_success, SUM(cri.status='not_bookable') n_nb, COUNT(*) n_items
FROM crawl_run_items cri
JOIN etl_item_map m ON m.warehouse_item_id = cri.id
JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
WHERE m.include_eda_main = 1 AND cri.hotel_id IS NOT NULL AND cr.status = 'completed'
GROUP BY m.source_code, cri.hotel_id, DATE(CONVERT_TZ(cr.started_at, '+00:00', '+07:00'))
"""
with warehouse_connection("warehouse_20261004_3src") as conn:
    cur = conn.cursor(dictionary=True)
    cur.execute("SET SESSION TRANSACTION READ ONLY")
    cur.execute("SET SESSION max_execution_time = 600000")
    cur.execute(SQL)
    df = pd.DataFrame(cur.fetchall())
    cur.close()
for c in ("n_success", "n_nb", "n_items"):
    df[c] = df[c].astype(int)
df["state"] = "other"
df.loc[df.n_nb == df.n_items, "state"] = "nb"
df.loc[(df.n_nb == 0) & (df.n_success > 0), "state"] = "ok"
piv = df.pivot_table(index=["hotel_id", "d"], columns="source_code", values="state", aggfunc="first")
print("hotel-ngay co >=2 nguon:", int((piv.notna().sum(axis=1) >= 2).sum()), "| co >=3 nguon:", int((piv.notna().sum(axis=1) >= 3).sum()))
rows = []
srcs = list(piv.columns)
for i, a in enumerate(srcs):
    for b in srcs[i + 1:]:
        both = piv[[a, b]].dropna()
        both = both[both[a].isin(["nb", "ok"]) & both[b].isin(["nb", "ok"])]
        ct = pd.crosstab(both[a], both[b])
        n_nb_a = int((both[a] == "nb").sum())
        n_nb_b = int((both[b] == "nb").sum())
        both_nb = int(((both[a] == "nb") & (both[b] == "nb")).sum())
        a_nb_b_ok = int(((both[a] == "nb") & (both[b] == "ok")).sum())
        a_ok_b_nb = int(((both[a] == "ok") & (both[b] == "nb")).sum())
        rows.append({"pair": f"{a} vs {b}", "hotel_days": len(both), "nb_in_A": n_nb_a, "nb_in_B": n_nb_b, "both_nb": both_nb,
                     "A_nb_B_ok": a_nb_b_ok, "A_ok_B_nb": a_ok_b_nb,
                     "P(B_nb|A_nb)": round(both_nb / n_nb_a, 3) if n_nb_a else None,
                     "P(A_nb|B_nb)": round(both_nb / n_nb_b, 3) if n_nb_b else None})
print(pd.DataFrame(rows).to_string(index=False))
# Chuyen 'nb' o nguon nay trong khi cac nguon con lai dong loat 'ok' - liet ke mau
disagree = piv[(piv.eq("nb").sum(axis=1) >= 1) & (piv.eq("ok").sum(axis=1) >= 1)]
print("\nhotel-ngay BAT DONG (>=1 nguon nb va >=1 nguon ok):", len(disagree), "tren tong hotel-ngay co nb o >=1 nguon:",
      int((piv.eq("nb").sum(axis=1) >= 1).sum()))
print(disagree.reset_index().head(15).to_string(index=False))
