"""Test THUAN cho cac metric/registry them theo review file 17 cua GPT (M1 taxonomy NULL, M3 duplicate canonical key, M4 anchor check-in, MINOR 1 near-time
collision). Khong MySQL: moi ham chay tren DataFrame nho co gia tri biet truoc."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import metrics
import null_taxonomy as nt
import queries

D = dt.date


# ======================================================================== M1 taxonomy NULL
def _missingness_by_source() -> pd.DataFrame:
    """2 nguon x 16 field cua `queries._MISSINGNESS_FIELD_GROUPS`, so lieu biet truoc: free_cancellation NULL 6/1000 (local) + 2/500 (vps); git_commit NULL 500/500 o
    vps (ngoai le da khai bao) va 0 o local; taxes_fees NULL 840/1000 va 420/500; bed_config 280/1000 va 170/500; con lai 0."""
    n_null = {("local_primary", "free_cancellation"): 6, ("vps", "free_cancellation"): 2, ("vps", "git_commit"): 500,
              ("local_primary", "taxes_fees"): 840, ("vps", "taxes_fees"): 420, ("local_primary", "bed_config"): 280, ("vps", "bed_config"): 170}
    rows = []
    for source, total in (("local_primary", 1000), ("vps", 500)):
        for group, fields in queries._MISSINGNESS_FIELD_GROUPS.items():
            for field in fields:
                rows.append({"source_code": source, "field": field, "field_group": group, "n_null": n_null.get((source, field), 0), "n_total": total,
                             "null_rate": n_null.get((source, field), 0) / total})
    return pd.DataFrame(rows)


def test_registry_phu_dung_tap_field_cua_missingness_va_khong_lop_la():
    nt.validate_against_field_groups(queries._MISSINGNESS_FIELD_GROUPS)   # khong raise
    bad_groups = {"room_identity": ("room_type_raw",)}
    with pytest.raises(ValueError, match="lech missingness"):
        nt.validate_against_field_groups(bad_groups)
    assert set(nt.NULL_CLASSES) == {"required_contract", "optional_listing", "source_metadata_expected_gap"}
    assert {r.canonical_key_role for r in nt.FIELD_RULES.values()} <= {"room_identity_key", "rate_plan_key", "none"}


def test_registry_lop_cua_field_then_chot_khong_gop_structural_vao_unexpected():
    assert nt.classify("free_cancellation") == "required_contract" and nt.FIELD_RULES["free_cancellation"].canonical_key_role == "rate_plan_key"
    assert nt.classify("taxes_fees") == "optional_listing" and nt.classify("bed_config") == "optional_listing"
    # ngoai le THEO NGUON: git_commit bat buoc o local nhung la gap da biet o VPS
    assert nt.classify("git_commit", "local_primary") == "required_contract"
    assert nt.classify("git_commit", "vps") == "source_metadata_expected_gap"
    assert nt.class_without_source("git_commit") == "source_dependent" and nt.class_without_source("taxes_fees") == "optional_listing"
    assert "git_commit" in nt.required_fields() and "taxes_fees" not in nt.required_fields()
    with pytest.raises(KeyError):
        nt.classify("truong_khong_khai_bao")


def test_null_taxonomy_by_source_phan_hoach_day_du_va_tong_khop_bang_goc():
    table = metrics.null_taxonomy_by_source(_missingness_by_source())
    raw = _missingness_by_source()
    assert len(table) == len(raw) == 32 and list(table.columns) == list(metrics.NULL_TAXONOMY_COLUMNS)
    assert set(table["null_class"]) <= set(nt.NULL_CLASSES)
    # moi (source, field) dung 1 lop; tong n_null / n_total qua ba lop = tong bang goc (bang chung khong nuot/nhan doi cell)
    assert not table.duplicated(["source_code", "field"]).any()
    assert int(table["n_null"].sum()) == int(raw["n_null"].sum()) and int(table["n_total"].sum()) == int(raw["n_total"].sum())
    vps_git = table[(table["source_code"] == "vps") & (table["field"] == "git_commit")].iloc[0]
    assert vps_git["null_class"] == "source_metadata_expected_gap" and vps_git["n_null"] == vps_git["n_total"]
    local_git = table[(table["source_code"] == "local_primary") & (table["field"] == "git_commit")].iloc[0]
    assert local_git["null_class"] == "required_contract"


def test_null_class_summary_du_3_lop_mau_so_rieng_va_tong_khop():
    table = metrics.null_taxonomy_by_source(_missingness_by_source())
    summary = metrics.null_class_summary(table).set_index("null_class")
    assert list(summary.index) == list(nt.NULL_CLASSES)
    assert int(summary["n_null_cells"].sum()) == int(table["n_null"].sum()) and int(summary["n_total_cells"].sum()) == int(table["n_total"].sum())
    # required_contract: chi free_cancellation (6 + 2) - KHONG lan taxes_fees/bed_config/VPS git_commit
    assert int(summary.loc["required_contract", "n_null_cells"]) == 8
    assert int(summary.loc["source_metadata_expected_gap", "n_null_cells"]) == 500 and int(summary.loc["source_metadata_expected_gap", "n_total_cells"]) == 500
    assert int(summary.loc["optional_listing", "n_null_cells"]) == 840 + 420 + 280 + 170
    assert summary.loc["source_metadata_expected_gap", "null_rate"] == pytest.approx(1.0)
    # bang khong co dong nao cua 1 lop van tra dong 0 (khong mat lop)
    only_required = table[table["null_class"] == "required_contract"]
    assert len(metrics.null_class_summary(only_required)) == 3


def test_required_field_null_stats_free_cancellation_co_mau_so_rieng_khong_chim_trong_tong_cell():
    table = metrics.null_taxonomy_by_source(_missingness_by_source())
    stats = metrics.required_field_null_stats(table).set_index("field")
    assert set(stats.index) == set(nt.required_fields())
    fc = stats.loc["free_cancellation"]
    assert (fc["n_null"], fc["n_total"]) == (8, 1500) and fc["null_rate"] == pytest.approx(8 / 1500)
    assert fc["canonical_key_role"] == "rate_plan_key"
    # git_commit: VPS bi loai khoi mau so (ngoai le da khai bao) -> chi local: 0 / 1000
    git = stats.loc["git_commit"]
    assert (git["n_null"], git["n_total"]) == (0, 1000) and git["sources_exempt"] == "vps" and git["sources_counted"] == "local_primary"
    total_cells = int(table["n_total"].sum())
    assert fc["n_null"] / total_cells < 0.0005  # neu chim trong tong cell thi tinh ra ~0,04% - chinh la ly do can mau so rieng


def test_null_taxonomy_field_la_thi_fail_khong_doan_lop():
    frame = _missingness_by_source().assign(field=lambda f: f["field"].replace({"taxes_fees": "truong_la"}))
    with pytest.raises(KeyError):
        metrics.null_taxonomy_by_source(frame)


# ======================================================================== M3 duplicate canonical key
def _dup_groups() -> pd.DataFrame:
    """6 nhom trung key (RAW); 5 nhom thuoc MAIN. Gia: g1 cung gia; g2..g6 khac gia."""
    rows = [
        # source, city, is_main, item, hotel, checkin, room_key, rate_key, n, min, max
        ("local_primary", "Ha Noi", True, 1, "h1", D(2026, 9, 20), "R1" + "0" * 62, "K1" + "0" * 62, 2, 100.0, 100.0),      # cung gia
        ("local_primary", "Ha Noi", True, 2, "h1", D(2026, 9, 20), "R2" + "0" * 62, "K1" + "0" * 62, 3, 100.0, 300.0),      # spread 200, rel 1.0
        ("local_primary", "Da Lat", True, 3, "h2", D(2026, 9, 21), "R1" + "0" * 62, "K1" + "0" * 62, 4, 200.0, 220.0),      # spread 20, rel 20/210
        ("vps", "Ha Noi", True, 4, "h1", D(2026, 9, 20), "R3" + "0" * 62, "K2" + "0" * 62, 2, 50.0, 150.0),               # spread 100, rel 1.0
        ("vps", "Da Lat", True, 5, "h2", D(2026, 9, 21), "R1" + "0" * 62, "K2" + "0" * 62, 5, 400.0, 500.0),              # spread 100, rel 100/450
        ("vps", "Da Lat", False, 6, "h3", D(2026, 8, 10), "R9" + "0" * 62, "K9" + "0" * 62, 2, 10.0, 30.0),                # ngoai MAIN (pilot)
    ]
    return pd.DataFrame(rows, columns=list(queries.DUPLICATE_GROUP_COLUMNS))


def _dup_totals() -> pd.DataFrame:
    return pd.DataFrame([
        ("local_primary", "Da Lat", 20, 20), ("local_primary", "Ha Noi", 30, 30), ("vps", "Da Lat", 25, 20), ("vps", "Ha Noi", 10, 10)],
        columns=["source_code", "city", "n_observations_raw", "n_observations_main"])


def test_duplicate_series_summary_cong_thuc_va_mau_so_n_groups_bang_observation_tru_observation_du():
    summary = metrics.duplicate_series_summary(_dup_groups(), _dup_totals())
    assert list(summary.columns) == list(metrics.DUPLICATE_SUMMARY_COLUMNS)
    raw_total = summary[(summary["scope"] == "RAW") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")].iloc[0]
    # RAW: 85 observation; nhom trung = 6 voi n = 2,3,4,2,5,2 -> observation du = 1+2+3+1+4+1 = 12; n_groups = 85 - 12 = 73
    assert (raw_total["n_observations"], raw_total["duplicate_groups"], raw_total["extra_observations"], raw_total["n_groups"]) == (85, 6, 12, 73)
    assert (raw_total["same_price_groups"], raw_total["divergent_price_groups"], raw_total["max_group_size"]) == (1, 5, 5)
    assert raw_total["divergent_share"] == pytest.approx(5 / 6) and raw_total["duplicate_group_rate"] == pytest.approx(6 / 73)
    main_total = summary[(summary["scope"] == "MAIN") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")].iloc[0]
    assert (main_total["n_observations"], main_total["duplicate_groups"], main_total["extra_observations"], main_total["same_price_groups"]) == (80, 5, 11, 1)
    # dong chi tiet source x city + rollup: tong cac dong chi tiet = dong (all)
    detail = summary[(summary["scope"] == "RAW") & (summary["source_code"] != "(all)") & (summary["city"] != "(all)")]
    assert int(detail["duplicate_groups"].sum()) == 6 and int(detail["n_observations"].sum()) == 85
    by_source = summary[(summary["scope"] == "RAW") & (summary["city"] == "(all)") & (summary["source_code"] == "vps")].iloc[0]
    assert by_source["duplicate_groups"] == 3 and by_source["max_group_size"] == 5
    assert set(summary["scope"]) == {"RAW", "MAIN"}


def test_duplicate_series_summary_khong_co_nhom_trung_van_co_dong_voi_0():
    empty = _dup_groups().iloc[0:0]
    summary = metrics.duplicate_series_summary(empty, _dup_totals())
    grand = summary[(summary["scope"] == "RAW") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")].iloc[0]
    assert grand["duplicate_groups"] == 0 and grand["n_groups"] == 85 and grand["max_group_size"] == 0 and np.isnan(grand["divergent_share"])


def test_duplicate_series_price_spread_summary_tuyet_doi_va_doi_xung_tuong_doi():
    spread = metrics.duplicate_series_price_spread_summary(_dup_groups())
    assert list(spread.columns) == list(metrics.DUPLICATE_SPREAD_COLUMNS)
    absolute = spread[(spread["scope"] == "RAW") & (spread["source_code"] == "(all)") & (spread["spread_kind"] == "absolute_vnd")].iloc[0]
    # 5 nhom khac gia: spread 200, 20, 100, 100, 20 (g6 pilot: 30 - 10)
    assert absolute["n_divergent_groups"] == 5 and absolute["spread_min"] == 20 and absolute["spread_max"] == 200
    assert absolute["spread_mean"] == pytest.approx((200 + 20 + 100 + 100 + 20) / 5) and absolute["spread_q50"] == pytest.approx(100.0)
    relative = spread[(spread["scope"] == "RAW") & (spread["source_code"] == "(all)") & (spread["spread_kind"] == "relative_symmetric")].iloc[0]
    # doi xung = (max - min) / ((max + min) / 2): g2 200/200 = 1, g3 20/210, g4 100/100 = 1, g5 100/450, g6 20/20 = 1
    assert relative["spread_max"] == pytest.approx(1.0) and relative["spread_min"] == pytest.approx(20 / 210) and relative["spread_q50"] == pytest.approx(1.0)
    assert relative["spread_mean"] == pytest.approx((1.0 + 20 / 210 + 1.0 + 100 / 450 + 1.0) / 5)
    vps = spread[(spread["scope"] == "RAW") & (spread["source_code"] == "vps") & (spread["spread_kind"] == "absolute_vnd")].iloc[0]
    assert vps["n_divergent_groups"] == 3
    main = spread[(spread["scope"] == "MAIN") & (spread["source_code"] == "(all)") & (spread["spread_kind"] == "absolute_vnd")].iloc[0]
    assert main["n_divergent_groups"] == 4  # loai nhom pilot ngoai MAIN
    assert not spread[["spread_q25", "spread_q50", "spread_q75", "spread_q90", "spread_q95", "spread_q99"]].isna().all().any()


def test_duplicate_series_audit_selection_xac_dinh_khong_phu_thuoc_thu_tu_dong_va_chi_nhom_khac_gia():
    groups = _dup_groups()
    first = metrics.duplicate_series_audit_selection(groups, per_criterion_source=2)
    shuffled = metrics.duplicate_series_audit_selection(groups.sample(frac=1.0, random_state=7).reset_index(drop=True), per_criterion_source=2)
    pd.testing.assert_frame_equal(first, shuffled)
    assert (first["min_price"] < first["max_price"]).all() and 1 not in set(first["item_id"])   # g1 cung gia khong duoc chon
    assert first["group_id"].is_unique and set(first["source_code"]) == {"local_primary", "vps"}
    # moi (nguon x tieu chi) toi da 2 -> moi nguon toi da 2 * 3 = 6 lan chon; nhom trung nhieu tieu chi chi 1 dong voi audit_reasons gop
    assert len(first) <= 12 and first["audit_reasons"].str.contains("largest_group_size#1").any()
    top_size_local = first[(first["source_code"] == "local_primary") & first["audit_reasons"].str.contains("largest_group_size#1")].iloc[0]
    assert top_size_local["item_id"] == 3 and top_size_local["n_observations"] == 4
    assert first.iloc[0]["group_id"].startswith(f"{first.iloc[0]['item_id']}:")


def test_duplicate_series_audit_selection_moi_hotel_toi_da_1_nhom_moi_nguon_x_tieu_chi():
    """Thuc te nhom lon nhat cua 1 nguon thuong la CUNG 1 villa qua nhieu ngay crawl -> chi lay nhom dau cua tung hotel de mau audit phu nhieu hotel."""
    groups = _dup_groups()
    same_hotel = groups.iloc[[1]].assign(item_id=99, n_observations=9, room_key="R7" + "0" * 62)         # local_primary / h1, nhom LON HON item 2 (n = 3)
    selection = metrics.duplicate_series_audit_selection(pd.concat([groups, same_hotel], ignore_index=True), per_criterion_source=5)
    local = selection[selection["source_code"] == "local_primary"]
    assert local["hotel_id"].is_unique and set(local["hotel_id"]) == {"h1", "h2"}
    h1 = local[local["hotel_id"] == "h1"].iloc[0]
    assert h1["item_id"] == 99 and h1["n_observations"] == 9      # nhom dau cua hotel = nhom lon nhat (khong phai nhom item 2 nho hon)
    assert 2 not in set(local["item_id"])                         # nhom nho hon cua cung hotel bi bo (tieu chi group_size)


def _audit_details(selection: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for record_id, sel in enumerate(selection.itertuples(index=False), start=1):
        for index in range(int(sel.n_observations)):
            price = float(sel.min_price) if index == 0 else float(sel.max_price) if index == 1 else float(sel.min_price) + index
            rows.append({"record_id": record_id * 100 + index, "item_id": sel.item_id, "source_code": sel.source_code, "city": sel.city, "hotel_id": sel.hotel_id,
                         "checkin_date": sel.checkin_date, "observed_at": pd.Timestamp("2026-09-01 03:00"), "vn_observation_date": D(2026, 9, 1),
                         "room_key": sel.room_key, "rate_key": sel.rate_key, "room_option_index": 10 - index, "price_per_night": price,
                         "original_price": None, "discount_percent": None, "taxes_fees": None, "price_includes_tax": None, "rooms_left": None,
                         "room_type_raw": "Deluxe", "max_occupancy": 2, "bed_config": None, "room_area": None, "breakfast_included": 1,
                         "free_cancellation": 0, "cancellation_policy": "x"})
    return pd.DataFrame(rows)


def test_duplicate_series_audit_sample_du_dong_moi_nhom_gia_rank_va_raise_khi_lech():
    groups = _dup_groups()
    selection = metrics.duplicate_series_audit_selection(groups, per_criterion_source=1)
    details = _audit_details(selection)
    sample = metrics.duplicate_series_audit_sample(details, selection)
    assert list(sample.columns) == list(metrics.DUPLICATE_AUDIT_SAMPLE_COLUMNS)
    assert len(sample) == int(selection["n_observations"].sum())
    per_group = sample.groupby("group_id").size()
    assert (per_group.reindex(selection["group_id"]).to_numpy() == selection["n_observations"].to_numpy()).all()
    for _, group in sample.groupby("group_id"):
        assert sorted(group["price_rank_in_group"]) == list(range(1, len(group) + 1))
        assert group["price_per_night"].min() == group["group_min_price"].iloc[0] and group["price_per_night"].max() == group["group_max_price"].iloc[0]
    assert list(sample["group_id"].drop_duplicates()) == list(selection["group_id"])     # thu tu nhom = thu tu chon (xac dinh)
    with pytest.raises(ValueError, match="KHAC so option"):
        metrics.duplicate_series_audit_sample(details.iloc[:-1], selection)


def test_duplicate_series_ham_thieu_cot_thi_fail():
    with pytest.raises(ValueError, match="thieu cot"):
        metrics.duplicate_series_summary(pd.DataFrame({"source_code": ["a"]}), _dup_totals())
    with pytest.raises(ValueError, match="thieu cot"):
        metrics.duplicate_series_audit_selection(pd.DataFrame({"source_code": ["a"]}))


# ======================================================================== MINOR 1 near-time concentration
def _near_time_fixture():
    detail = pd.DataFrame({
        "item_id_a": [1, 1, 1, 2, 2, 3], "item_id_b": [11, 11, 11, 12, 12, 13], "source_a": "local_primary", "source_b": "vps",
        "observed_at_diff_minutes": [1.0, 2.5, 5.9, 3.0, 4.0, 45.0], "price_abs_diff": [0.0, 500.0, 0.0, 1000.0, 200.0, 0.0],
    })
    pairs = pd.DataFrame({"item_id_a": [1, 2, 3], "item_id_b": [11, 12, 13], "vn_crawl_date": [D(2026, 8, 27), D(2026, 9, 14), D(2026, 9, 14)],
                          "hotel_id": ["h1", "h2", "h3"]})
    return detail, pairs


def test_collision_near_time_concentration_chi_bucket_0_5_va_cho_thay_tap_trung():
    detail, pairs = _near_time_fixture()
    out = metrics.collision_near_time_concentration(detail, pairs)
    assert list(out.columns) == list(metrics.COLLISION_NEAR_TIME_COLUMNS)
    assert int(out["n_option_pairs"].sum()) == 5            # 45 phut nam ngoai bucket 0-5; 5.9 phut van thuoc 0-5 (< 6)
    first = out.iloc[0]
    assert (first["vn_crawl_date"], first["hotel_id"], first["n_option_pairs"], first["n_exact_price_match"], first["n_non_exact"]) == (D(2026, 8, 27), "h1", 3, 2, 1)
    assert first["non_exact_rate"] == pytest.approx(1 / 3) and first["median_price_abs_diff_non_exact"] == 500.0 and first["max_price_abs_diff"] == 500.0
    assert out["share_of_near_time_pairs"].sum() == pytest.approx(1.0) and first["share_of_near_time_pairs"] == pytest.approx(3 / 5)
    assert first["min_observed_at_diff_minutes"] == 1.0 and first["max_observed_at_diff_minutes"] == pytest.approx(5.9)
    assert set(out["vn_crawl_date"]) == {D(2026, 8, 27), D(2026, 9, 14)}


def test_collision_near_time_concentration_rong_va_thieu_item_pair_thi_khong_am_tham():
    detail, pairs = _near_time_fixture()
    assert metrics.collision_near_time_concentration(detail.iloc[0:0], pairs).empty
    far_only = detail.assign(observed_at_diff_minutes=100.0)
    assert metrics.collision_near_time_concentration(far_only, pairs).empty
    with pytest.raises(ValueError, match="khong tim thay item-pair"):
        metrics.collision_near_time_concentration(detail, pairs.iloc[1:])


# ======================================================================== M4 anchor check-in
def _anchor_items() -> pd.DataFrame:
    """3 anchor: 2027-01-01 (Friday), 2026-09-19 (Saturday), 2026-09-20 (Sunday); Friday co nhieu item hon vi lap qua nhieu crawl day/hotel."""
    rows = []
    for checkin, n_items, crawl_days in ((D(2027, 1, 1), 40, 8), (D(2026, 9, 19), 6, 3), (D(2026, 9, 20), 5, 2)):
        for i in range(n_items):
            rows.append({"checkin_date": checkin, "crawl_date": D(2026, 9, 1) + dt.timedelta(days=i % crawl_days), "source_code": "local_primary" if i % 2 else "vps",
                         "lead_time": 10 + (i % crawl_days), "effective_city": "Ha Noi"})
    frame = pd.DataFrame(rows)
    stamp = pd.to_datetime(frame["checkin_date"])
    frame["weekday_number"] = stamp.dt.weekday
    frame["weekday"] = stamp.dt.day_name()
    frame["is_weekend_fri_sat"] = frame["weekday_number"].isin((4, 5))
    return frame


def test_item_coverage_with_anchors_friday_1_anchor_va_tong_anchor_theo_thu_bang_tong_anchor():
    items = _anchor_items()
    weekday = metrics.item_coverage_with_anchors(items, group_cols=("weekday_number", "weekday", "is_weekend_fri_sat"), list_dates=True)
    assert list(weekday.columns) == ["weekday_number", "weekday", "is_weekend_fri_sat", "n_items", "n_distinct_checkin_dates", "checkin_dates"]
    friday = weekday[weekday["weekday"] == "Friday"].iloc[0]
    assert friday["n_items"] == 40 and friday["n_distinct_checkin_dates"] == 1 and friday["checkin_dates"] == "2027-01-01"
    # INVARIANT (file 17 M4): moi ngay thuoc dung 1 thu -> tong anchor theo thu = so anchor phan biet toan bo
    assert int(weekday["n_distinct_checkin_dates"].sum()) == items["checkin_date"].nunique() == 3
    month = metrics.item_coverage_with_anchors(items, group_cols=("checkin_month",) if "checkin_month" in items else ("weekday_number",))
    assert int(month["n_distinct_checkin_dates"].sum()) == 3
    with pytest.raises(ValueError, match="thieu cot"):
        metrics.item_coverage_with_anchors(items, group_cols=("cot_la",))
    assert metrics.item_coverage_with_anchors(items.iloc[0:0], group_cols=("weekday",)).empty


def test_checkin_anchor_dates_table_1_dong_moi_anchor_va_co_calendar_any_city():
    items = _anchor_items()
    calendar = pd.DataFrame({
        "checkin_date": [D(2027, 1, 1), D(2027, 1, 1), D(2026, 9, 19), D(2026, 9, 19), D(2026, 9, 20), D(2026, 9, 20)],
        "city": ["Ha Noi", "Da Lat"] * 3,
        "is_public_holiday": [True, True, False, False, False, False], "is_tet": False,
        "is_festival_period": [False, False, False, True, False, False], "is_major_event": False,
    })
    table = metrics.checkin_anchor_dates_table(items, calendar)
    assert len(table) == 3 and list(table["checkin_date"]) == [D(2026, 9, 19), D(2026, 9, 20), D(2027, 1, 1)]
    friday = table[table["weekday"] == "Friday"].iloc[0]
    assert friday["n_items"] == 40 and friday["n_crawl_dates"] == 8 and friday["n_sources"] == 2 and friday["is_public_holiday_any_city"]
    saturday = table[table["weekday"] == "Saturday"].iloc[0]
    assert saturday["is_festival_period_any_city"] and not saturday["is_public_holiday_any_city"]   # 1 trong 2 city co festival => any_city True
    assert int(table["n_items"].sum()) == len(items)
    assert friday["min_lead_time"] == 10 and friday["max_lead_time"] == 17
