"""Unit test cau truc SQL cua `sql_builders.py` (thuan, khong MySQL). So hoc (quantile dung khop numpy) duoc kiem
chung rieng tren MySQL that o `test_sql_aggregates.py`."""
from __future__ import annotations

import datetime as dt

from sql_builders import BOX_QUANTILES, STANDARD_QUANTILES, grouped_quantile_sql, inline_calendar_derived_table, quantile_expr

FROM_WHERE = "FROM price_observations po WHERE po.is_sold_out=0 AND po.batch=%s"


def test_quantile_expr_dung_noi_suy_tuyen_tinh_khong_dung_percent_rank():
    sql = quantile_expr("0.50")
    assert "FLOOR(1+(g.cnt-1)*0.50)" in sql
    assert "LEAST(g.cnt" in sql  # chan chi so vuot n khi q=1
    assert "MAX(g.cnt)" in sql  # frac dung hang so theo nhom


def test_grouped_quantile_sql_tong_the_khong_partition_khong_group_by():
    sql = grouped_quantile_sql(value_expr="po.price_per_night", from_where=FROM_WHERE)
    assert "ROW_NUMBER() OVER (ORDER BY k.v)" in sql
    assert "COUNT(*) OVER ()" in sql
    assert "GROUP BY" not in sql
    for name in STANDARD_QUANTILES:
        assert f" {name}" in sql
    assert sql.count("%s") == 1  # chi placeholder cua from_where


def test_grouped_quantile_sql_co_nhom_partition_va_group_by_dung_alias():
    sql = grouped_quantile_sql(
        value_expr="po.price_per_night", from_where=FROM_WHERE,
        group_exprs={"city": "COALESCE(h.city,'(unknown)')", "lead_time_bucket": "b"},
    )
    assert "PARTITION BY k.city, k.lead_time_bucket" in sql
    assert "GROUP BY g.city, g.lead_time_bucket" in sql
    assert "ORDER BY g.city, g.lead_time_bucket" in sql


def test_grouped_quantile_sql_min_group_size_va_extra_aggregate():
    sql = grouped_quantile_sql(
        value_expr="po.price_per_night", from_where=FROM_WHERE, group_exprs={"hotel_id": "po.hotel_id"},
        quantiles={"p50": "0.50"}, extra_aggregates={"std_price": "STDDEV_SAMP(g.v)"}, min_group_size=5,
    )
    assert "HAVING COUNT(*) >= 5" in sql
    assert "STDDEV_SAMP(g.v) std_price" in sql


def test_grouped_quantile_sql_carry_exprs_mang_cot_phu_qua_3_tang_de_dem_ngay_phan_biet():
    """File 17 M4: so ngay check-in PHAN BIET moi nhom (weekday / co calendar) phai tinh duoc trong cung aggregate SQL, khong PARTITION theo cot mang theo."""
    sql = grouped_quantile_sql(
        value_expr="po.price_per_night", from_where=FROM_WHERE, group_exprs={"weekday": "DAYNAME(po.checkin_date)"},
        quantiles={"p50": "0.50"}, carry_exprs={"checkin_date": "po.checkin_date"},
        extra_aggregates={"n_distinct_checkin_dates": "COUNT(DISTINCT g.checkin_date)"},
    )
    assert "po.checkin_date checkin_date" in sql            # tang k
    assert "k.checkin_date" in sql                          # tang g (mang qua window)
    assert "COUNT(DISTINCT g.checkin_date) n_distinct_checkin_dates" in sql   # tang ngoai
    assert "PARTITION BY k.weekday " in sql and "PARTITION BY k.weekday, k.checkin_date" not in sql
    plain = grouped_quantile_sql(value_expr="po.price_per_night", from_where=FROM_WHERE)
    assert "checkin_date" not in plain  # khong dung carry => SQL cu khong doi


def test_grouped_quantile_sql_box_quantiles_co_p25():
    sql = grouped_quantile_sql(value_expr="po.price_per_night", from_where=FROM_WHERE, quantiles=BOX_QUANTILES)
    assert " p25" in sql and " p1" not in sql.replace("p1 ", "X")  # khong co p1/p99 trong box


def test_inline_calendar_rong_van_la_derived_table_hop_le():
    fragment, params = inline_calendar_derived_table([])
    assert "WHERE FALSE" in fragment and fragment.endswith(" cal") and params == []


def test_inline_calendar_bind_dung_thu_tu_va_chuyen_bool_thanh_int():
    rows = [
        {"checkin_date": dt.date(2026, 9, 2), "city": "Hà Nội", "is_public_holiday": True, "is_tet": False,
         "is_festival_period": False, "is_major_event": False},
        {"checkin_date": dt.date(2026, 9, 10), "city": "Đà Lạt", "is_public_holiday": False, "is_tet": False,
         "is_festival_period": True, "is_major_event": False},
    ]
    fragment, params = inline_calendar_derived_table(rows)
    assert fragment.count("%s") == len(params) == 12
    assert fragment.count("UNION ALL") == 1
    assert params[:6] == [dt.date(2026, 9, 2), "Hà Nội", 1, 0, 0, 0]
    assert params[6:] == [dt.date(2026, 9, 10), "Đà Lạt", 0, 0, 1, 0]
    assert "AS is_festival_period" in fragment  # alias chi o SELECT dau
