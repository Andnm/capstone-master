"""Spec fixture co GIA TRI BIET TRUOC, dung chung cho cac integration test (xem `warehouse_fixture.py`).

Moi spec ghi ro so lieu ky vong ngay canh du lieu de reviewer doi chieu bang mat khong can chay code.
"""
from __future__ import annotations

import datetime as dt

from warehouse_fixture import FxItem, FxObs, FxRun, FxSource, room

D, T = dt.date, dt.datetime

HOTELS_PRICE = {"h1": "Hà Nội", "h2": "Hà Nội", "h3": "Đà Lạt"}

HOLIDAYS_CSV_PRICE = (
    "holiday_date,event_code,name,event_type,scope,city,is_tet,status,source_url\n"
    "2026-09-10,fest1,Le hoi Ha Noi,festival,city,Hà Nội,0,confirmed,https://example.test\n"
    "2026-09-20,nat1,Ngay le,public_holiday,national,,0,confirmed,https://example.test\n"
)

# h1 co 10 option tren item 4: 9 gia 100k..180k (buoc 10k) + 1 gia 9.000.000 (outlier ro rang).
H1_ITEM4_PRICES = [100_000, 110_000, 120_000, 130_000, 140_000, 150_000, 160_000, 170_000, 180_000, 9_000_000]
H2_ITEM5_PRICES = [300_000, 310_000, 320_000]                                   # h2: 3 obs (< 5 -> khong xet dispersion/outlier)
H3_ITEM6_PRICES = [50_000, 60_000, 70_000, 80_000, 90_000, 100_000]             # h3 (Da Lat): 6 obs
H3_ITEM10_PRICES = [120_000, 130_000]                                           # h3 check-in 12/09 (thu Bay = weekend Fri/Sat)
SERIES_A_PRICE = 200_000                                                        # cung room "A", h1, check-in 20/09, quan sat 01/09, 02/09, 06/09

# Tong observation MAIN co gia = 3 (series A) + 10 + 3 + 6 + 2 = 24; sold-out sentinel (item 7) khong tinh.
ALL_PRICES_MAIN = [SERIES_A_PRICE] * 3 + H1_ITEM4_PRICES + H2_ITEM5_PRICES + H3_ITEM6_PRICES + H3_ITEM10_PRICES


def _run(run_id: int, day: int, *, selector: str | None = "sel-1") -> FxRun:
    return FxRun(run_id, T(2026, 9, day, 10, 0), T(2026, 9, day, 10, 30), selector_version=selector)


def price_fixture_spec() -> dict:
    """Nguon local_primary DUY NHAT, moi cap (crawl_date, checkin_date) deu do local_primary so huu -> moi item
    la owner (MAIN = RAW tru sold-out). Ky vong chinh (so voi ALL_PRICES_MAIN):
      * series A (h1, 20/09): quan sat 01/09, 02/09, 06/09 -> n_observed_days=3, max_gap=4, median_gap=2.5;
        horizon pairs: K=1 -> 1 cap, K=3/7/14 -> 0.
      * outlier robust (MAD): dung 1 observation (9.000.000 cua h1) - h2 (<5 obs) & h3 khong co outlier.
      * item status: success=7, sold_out=1, not_bookable=1, error=1 (item 9 hotel_id NULL)."""
    d1, d2, d6 = D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 6)
    runs = [_run(1, 1), _run(2, 2), _run(3, 6, selector=None)]   # run 3: selector_version NULL -> '(unknown)' + missingness metadata
    finished = {1: T(2026, 9, 1, 10, 20), 2: T(2026, 9, 2, 10, 20), 3: T(2026, 9, 6, 10, 20)}
    items = [
        FxItem(1, 1, "h1", D(2026, 9, 20), "success", finished[1]),
        FxItem(2, 2, "h1", D(2026, 9, 20), "success", finished[2]),
        FxItem(3, 3, "h1", D(2026, 9, 20), "success", finished[3]),
        FxItem(4, 1, "h1", D(2026, 9, 10), "success", finished[1]),
        FxItem(5, 1, "h2", D(2026, 9, 10), "success", finished[1]),
        FxItem(6, 1, "h3", D(2026, 9, 10), "success", finished[1]),
        FxItem(7, 2, "h3", D(2026, 9, 12), "sold_out", finished[2]),
        FxItem(8, 2, "h2", D(2026, 9, 12), "not_bookable", finished[2]),
        FxItem(9, 3, None, D(2026, 9, 12), "error", finished[3],
               source_hotel_link="https://www.booking.com/hotel/vn/h1.vi.html?checkin=2026-09-12"),
        FxItem(10, 3, "h3", D(2026, 9, 12), "success", finished[3]),
    ]
    at = {1: T(2026, 9, 1, 10, 15), 2: T(2026, 9, 2, 10, 15), 3: T(2026, 9, 6, 10, 15)}
    obs = [
        FxObs(1, 1, at[1], SERIES_A_PRICE, room("A")), FxObs(2, 2, at[2], SERIES_A_PRICE, room("A")),
        FxObs(3, 3, at[3], SERIES_A_PRICE, room("A")),
    ]
    obs += [FxObs(10 + i, 4, at[1], p, room(f"P{i + 1}"), option_index=i) for i, p in enumerate(H1_ITEM4_PRICES)]
    obs += [FxObs(20 + i, 5, at[1], p, room(f"S{i + 1}"), option_index=i) for i, p in enumerate(H2_ITEM5_PRICES)]
    obs += [FxObs(30 + i, 6, at[1], p, room(f"R{i + 1}"), option_index=i) for i, p in enumerate(H3_ITEM6_PRICES)]
    obs += [FxObs(40, 7, at[2], None)]                                            # sentinel sold-out cua item 7
    obs += [FxObs(50 + i, 10, at[3], p, room(f"Q{i + 1}"), option_index=i) for i, p in enumerate(H3_ITEM10_PRICES)]
    ownership_rows = [
        ("local_primary", d1, "N1", D(2026, 9, 20)), ("local_primary", d2, "N1", D(2026, 9, 20)),
        ("local_primary", d6, "N1", D(2026, 9, 20)), ("local_primary", d1, "N2", D(2026, 9, 10)),
        ("local_primary", d2, "N2", D(2026, 9, 12)), ("local_primary", d6, "N2", D(2026, 9, 12)),
    ]
    return {
        "sources": [FxSource("local_primary", 0, runs, items, obs)], "hotels": HOTELS_PRICE,
        "ownership_rows": ownership_rows, "holidays_csv": HOLIDAYS_CSV_PRICE,
    }


def collision_fixture_spec() -> dict:
    """2 nguon cung crawl ngay 01/09 dung (h1, 05/09) va (h1, 07/09) -> 2 collision item-pair. local_primary so huu
    ca 2 cap (vps = non_owner_duplicate, chi RAW). Ky vong:
      * pair 1 (05/09): success-success; option X gia local 500.000 vs vps 520.000 (lech 20.000, relative
        20.000/510.000), chenh observed_at 15 phut (bucket 6-15); Y (local) va Z (vps) khong shared;
        finished_at chenh 15 phut.
      * pair 2 (07/09): success (local) vs sold_out (vps) -> disagreement; finished_at chenh 12 phut.
      * vps item 3 (h1, check-in 06/09, crawl 01/09) KHONG co item local cung khoa -> KHONG phai collision (chung minh cap chon theo khoa, khong
        theo gia/status): ownership 'unassigned', chi RAW."""
    d1 = D(2026, 9, 1)
    local = FxSource(
        "local_primary", 0,
        [FxRun(1, T(2026, 9, 1, 10, 0), T(2026, 9, 1, 10, 30))],
        [FxItem(1, 1, "h1", D(2026, 9, 5), "success", T(2026, 9, 1, 10, 20)),
         FxItem(2, 1, "h1", D(2026, 9, 7), "success", T(2026, 9, 1, 10, 25))],
        [FxObs(1, 1, T(2026, 9, 1, 10, 15), 500_000, room("X"), 0),
         FxObs(2, 1, T(2026, 9, 1, 10, 16), 600_000, room("Y"), 1),
         FxObs(3, 2, T(2026, 9, 1, 10, 22), 800_000, room("X"), 0)],
    )
    vps = FxSource(
        "vps", 1,
        [FxRun(1, T(2026, 9, 1, 10, 5), T(2026, 9, 1, 10, 40))],
        [FxItem(1, 1, "h1", D(2026, 9, 5), "success", T(2026, 9, 1, 10, 35)),
         FxItem(2, 1, "h1", D(2026, 9, 7), "sold_out", T(2026, 9, 1, 10, 37)),
         FxItem(3, 1, "h1", D(2026, 9, 6), "success", T(2026, 9, 1, 10, 38))],
        [FxObs(1, 1, T(2026, 9, 1, 10, 30), 520_000, room("X"), 0),
         FxObs(2, 1, T(2026, 9, 1, 10, 31), 700_000, room("Z"), 1),
         FxObs(3, 2, T(2026, 9, 1, 10, 36), None),
         FxObs(4, 3, T(2026, 9, 1, 10, 39), 300_000, room("W"), 0)],
    )
    return {
        "sources": [local, vps], "hotels": {"h1": "Hà Nội"},
        "ownership_rows": [
            ("local_primary", d1, "N1", D(2026, 9, 5)), ("local_primary", d1, "N2", D(2026, 9, 7)),
            ("vps", d1, "V1", D(2026, 9, 8)),
        ],
    }


TURNOVER_CHECKIN = D(2026, 9, 30)
TURNOVER_CRAWL_DAYS = (1, 2, 4, 5, 8)      # ngay crawl (thang 9/2026) cua 1 series duy nhat -> khoang cach lien tiep 1, 2, 1, 3


def turnover_fixture_spec() -> dict:
    """1 canonical series DUY NHAT (h1, check-in 30/09, room T) quan sat vao 5 ngay VN 01, 02, 04, 05, 08/09 (moi ngay 1 run + 1 item + 1 option):
      * khoang cach lien tiep = 1, 2, 1, 3 -> max_gap 3, 2 khoang trong > 1 ngay (reappearance), median gap 1.5, span 8 ngay;
      * cap ngay cach DUNG K: K=1 -> (01,02) (04,05) = 2; K=3 -> (01,04) (02,05) (05,08) = 3; K=7 -> (01,08) = 1; K=14 -> 0.
    Kiem chung ham cua so (LAG + RANGE INTERVAL K DAY FOLLOWING) cua `canonical_series_facts_main` bang dem tay."""
    runs = [FxRun(i + 1, T(2026, 9, day, 10, 0), T(2026, 9, day, 10, 30)) for i, day in enumerate(TURNOVER_CRAWL_DAYS)]
    items = [FxItem(i + 1, i + 1, "h1", TURNOVER_CHECKIN, "success", T(2026, 9, day, 10, 20)) for i, day in enumerate(TURNOVER_CRAWL_DAYS)]
    obs = [FxObs(i + 1, i + 1, T(2026, 9, day, 10, 15), 400_000 + i * 1000, room("T")) for i, day in enumerate(TURNOVER_CRAWL_DAYS)]
    ownership_rows = [("local_primary", D(2026, 9, day), "N1", TURNOVER_CHECKIN) for day in TURNOVER_CRAWL_DAYS]
    return {"sources": [FxSource("local_primary", 0, runs, items, obs)], "hotels": {"h1": "Hà Nội"}, "ownership_rows": ownership_rows}
