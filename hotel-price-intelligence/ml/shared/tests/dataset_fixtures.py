"""Fixture warehouse cho dataset builder (dung `build_fixture_warehouse` -> `build_warehouse()` that).

Quy uoc: moi datetime NAIVE UTC; run ngay d: started 10:00, finished 10:30, quan sat 10:15 UTC (= 17:00 VN cung
ngay). approved_at = finished_at cua run duyet nen quan sat cung ngay (10:15) < approved_at (10:30): mau dau tien
cua moi chuoi la quan sat NGAY SAU ngay duyet (spec muc 12: observed_at >= approved_at).

Kich ban (check-in cua moi hotel co dinh sau ngay quan sat cuoi, ngay 1..12 cua thang 09/2026):
  h1 (Ha Noi, 20/09)  : phong A moi ngay (gia 100k + 10k*d), phong B ngay chan -> DUOC DUYET o ngay 3 (A duy nhat 3 run, coverage 1).
  h2 (Ha Noi, 20/09)  : A xuat hien 2 lan trong CUNG item -> non-unique -> KHONG BAO GIO duoc duyet.
  h3 (Da Lat, 20/09)  : chi 2 run -> khong du so run.
  h4 (Phu Quoc, 21/09): A ngay 1-5 (duoc duyet ngay 3), tu ngay 6 chi con phong C -> item sau 'unavailable'.
  h5 (Vung Tau, 21/09): A ngay 1-12 nhung ngay 7 sold-out sentinel (khong sample), ngay 8 co gia 0 khong hop le (khong co: tranh vi pham schema).
"""
from __future__ import annotations

import datetime as dt

from warehouse_fixture import FxItem, FxObs, FxRun, FxSource, room

D, T = dt.date, dt.datetime

HOTELS = {"h1": "Hà Nội", "h2": "Hà Nội", "h3": "Đà Lạt", "h4": "Phú Quốc", "h5": "Vũng Tàu"}
DAYS = range(1, 13)
CHECKIN_FAR = D(2026, 9, 20)
CHECKIN_FAR2 = D(2026, 9, 21)

HOLIDAYS_CSV = (
    "holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
    "2026-09-02,national_day,Quoc khanh,public_holiday,national,,0,confirmed,https://example.test\n"
    "2026-09-14,fest1,Le hoi Phu Quoc,festival,city,Phú Quốc,0,confirmed,https://example.test\n"
)


def price_a(day: int) -> int:
    return 100_000 + 10_000 * day


def finished(day: int) -> dt.datetime:
    return T(2026, 9, day, 10, 30)


def observed(day: int) -> dt.datetime:
    return T(2026, 9, day, 10, 15)


def dataset_fixture_spec() -> dict:
    runs = [FxRun(d, T(2026, 9, d, 10, 0), finished(d)) for d in DAYS]
    items: list[FxItem] = []
    obs: list[FxObs] = []
    ownership: list[tuple] = []
    rec = [0]

    def add_obs(item_id, day, price, spec=None, index=0):
        rec[0] += 1
        obs.append(FxObs(rec[0], item_id, observed(day), price, spec, index))

    for d in DAYS:
        # h1: A moi ngay; B ngay chan (de co >1 phong/item)
        items.append(FxItem(100 + d, d, "h1", CHECKIN_FAR, "success", finished(d)))
        add_obs(100 + d, d, price_a(d), room("A"), 0)
        if d % 2 == 0:
            add_obs(100 + d, d, price_a(d) + 5_000, room("B"), 1)
        # h2: A hai lan trong cung item (non-unique)
        items.append(FxItem(200 + d, d, "h2", CHECKIN_FAR, "success", finished(d)))
        add_obs(200 + d, d, price_a(d), room("A"), 0)
        add_obs(200 + d, d, price_a(d) + 1_000, room("A"), 1)
        # h3: chi ngay 1-2
        if d <= 2:
            items.append(FxItem(300 + d, d, "h3", CHECKIN_FAR, "success", finished(d)))
            add_obs(300 + d, d, price_a(d), room("A"), 0)
        # h4: A ngay 1-5, sau do chi C
        items.append(FxItem(400 + d, d, "h4", CHECKIN_FAR2, "success", finished(d)))
        add_obs(400 + d, d, price_a(d) * 2, room("A") if d <= 5 else room("C"), 0)
        # h5: A tat ca ngay, ngay 7 sold-out sentinel
        if d == 7:
            items.append(FxItem(500 + d, d, "h5", CHECKIN_FAR2, "sold_out", finished(d)))
            add_obs(500 + d, d, None, None, 0)
        else:
            items.append(FxItem(500 + d, d, "h5", CHECKIN_FAR2, "success", finished(d)))
            add_obs(500 + d, d, price_a(d) * 3, room("A"), 0)
        crawl = D(2026, 9, d)
        ownership.append(("local_primary", crawl, "N1", CHECKIN_FAR))
        ownership.append(("local_primary", crawl, "N2", CHECKIN_FAR2))
    return {
        "sources": [FxSource("local_primary", 0, runs, items, obs, dump_taken_at="2026-09-13T00:00:00Z")],
        "hotels": HOTELS, "ownership_rows": ownership, "holidays_csv": HOLIDAYS_CSV,
    }
