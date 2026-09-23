"""Acceptance test tren ARTIFACT OFFICIAL cua batch `b20260916_2src` (review file 17 cua GPT: M1 taxonomy NULL, M2 baseline production, M3 duplicate canonical key,
M4 anchor check-in, MINOR 1 near-time collision). Chi DOC CSV trong `eda/outputs/` (gitignored) - khong MySQL, khong dong vao warehouse that.

Test bo qua (skip) khi may nay chua co analysis nao cua batch co bang file 17 (vd CI/may khac, hoac chi con run cu truoc khi co `checkin_anchor_dates_main`).
So lieu snapshot duoc hard-code CHI o day - logic production khong biet gi ve 29 anchor hay Friday = 1 (cac invariant tong quat nam o `test_metrics_file17.py` va
`test_wave_a_dry_run.py`). Doi batch/snapshot => doi hang so o day cung luc voi `BATCH_ID`, khong sua logic.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

BATCH_ID = "b20260916_2src"
OUTPUTS = Path(__file__).resolve().parents[2] / "outputs"
ANCHORS_BY_WEEKDAY = {"Monday": 2, "Tuesday": 2, "Wednesday": 6, "Thursday": 6, "Friday": 1, "Saturday": 6, "Sunday": 6}


def _latest_analysis() -> "Path | None":
    """Analysis moi nhat cua batch da hoan tat (co artifact_manifest.json) VA co bang file 17; ten thu muc chua timestamp nen sort ten = sort thoi gian."""
    candidates = sorted(p for p in OUTPUTS.glob(f"eda_{BATCH_ID}_*")
                        if (p / "artifact_manifest.json").exists() and (p / "tables" / "checkin_anchor_dates_main.csv").exists())
    return candidates[-1] if candidates else None


@pytest.fixture(scope="module")
def official() -> Path:
    directory = _latest_analysis()
    if directory is None:
        pytest.skip(f"chua co analysis official cua {BATCH_ID} voi bang file 17 trong {OUTPUTS}")
    return directory


def _table(directory: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(directory / "tables" / f"{name}.csv")


def test_manifest_pin_head_sach_khong_dirty(official):
    """MINOR 3 file 17: run cuoi phai sinh tu working tree sach va pin dung HEAD (khong tai su dung run dirty)."""
    code = json.loads((official / "input_manifest.json").read_text(encoding="utf-8"))["code_provenance"]
    assert code["is_dirty"] is False, "run official phai sinh tu working tree sach (is_dirty=false)"
    assert len(code["git_head"]) == 40


def test_anchor_snapshot_29_ngay_friday_chi_1_va_tong_theo_thu_bang_tong_anchor(official):
    """M4: 29 anchor phan biet; Friday = 1 ngay (2027-01-01); moi bang weekday co `n_distinct_checkin_dates` khop bang companion."""
    anchors = _table(official, "checkin_anchor_dates_main")
    assert len(anchors) == 29 and anchors["checkin_date"].is_unique
    per_weekday = anchors.groupby("weekday")["checkin_date"].nunique().to_dict()
    assert per_weekday == ANCHORS_BY_WEEKDAY and sum(per_weekday.values()) == 29
    friday = anchors[anchors["weekday"] == "Friday"]["checkin_date"].tolist()
    assert friday == [dt.date(2027, 1, 1).isoformat()]
    for name in ("item_checkin_weekday_distribution_main", "price_distribution_by_weekday_main", "checkin_weekday_distribution_main"):
        table = _table(official, name).set_index("weekday")
        assert table["n_distinct_checkin_dates"].to_dict() == ANCHORS_BY_WEEKDAY, name
    # bang gia weekday khong the bo cot so anchor (chinh la dieu GPT phat hien thieu o run #2)
    price = _table(official, "price_distribution_by_weekday_main")
    assert {"n_obs", "n_distinct_checkin_dates"} <= set(price.columns)


def test_taxonomy_null_giu_du_tong_cell_va_free_cancellation_co_mau_so_rieng(official):
    """M1: 3 lop cong lai = tong NULL cell cua bang missingness goc (1.782.754), khong nuot/nhan doi; required_contract chi con free_cancellation (7.102 /
    1.276.337), khong bi chim trong tong 20,4 trieu cell; khong con finding `unexpected_nulls_available_observations`."""
    raw = _table(official, "missingness_available_observations")
    classes = _table(official, "missingness_null_class_summary").set_index("null_class")
    assert int(classes["n_null_cells"].sum()) == int(raw["n_null"].sum()) == 1_782_754
    assert int(classes.loc["required_contract", "n_null_cells"]) == 7_102
    assert int(classes.loc["source_metadata_expected_gap", "n_null_cells"]) == int(classes.loc["source_metadata_expected_gap", "n_total_cells"]) == 271_646
    required = _table(official, "missingness_required_contract_by_field").set_index("field")
    assert int(required.loc["free_cancellation", "n_null"]) == 7_102 and int(required.loc["free_cancellation", "n_total"]) == 1_276_337
    assert int(required["n_null"].sum()) == 7_102                    # ngoai free_cancellation, moi field bat buoc con lai day du
    findings = _table(official, "quality_findings")
    assert "unexpected_nulls_available_observations" not in set(findings["check_id"])
    by_id = findings.set_index("check_id")
    assert int(by_id.loc["required_null_free_cancellation", "count"]) == 7_102 and int(by_id.loc["required_null_free_cancellation", "denominator"]) == 1_276_337


def test_baseline_van_hanh_chi_production_va_pilot_nam_o_phu_luc_raw(official):
    """M2: bang chinh = 52 source-day production (5 co); phu luc RAW = 62 (co ca pilot) - baseline z-score cua bang chinh khong bi pilot dinh hinh."""
    primary = _table(official, "run_day_operational_flags")
    appendix = _table(official, "run_day_operational_flags_raw_appendix")
    assert len(primary) == 52 and bool(primary["is_protocol_source_day"].all()) and int(primary["is_any_anomalous"].sum()) == 5
    assert len(appendix) == 62 and int(appendix["is_any_anomalous"].sum()) == 9
    pilot_flagged = appendix[appendix["is_any_anomalous"].astype(bool) & ~appendix["is_protocol_source_day"].astype(bool)]
    assert len(pilot_flagged) == 6                                    # dung 6 dong pilot ma GPT thay o run #2
    runs = _table(official, "run_duration_and_throughput")
    assert int(runs["is_protocol_run"].sum()) == 52 and int((~runs["is_protocol_run"].astype(bool)).sum()) == 10


def test_duplicate_summary_khop_finding_va_khong_doi_canonicalization(official):
    """M3: bang tom tat source x city khop finding `duplicate_daily_series` (156.634 nhom; 3.377 cung gia; 153.257 khac gia); audit sample xac dinh, moi group co
    du dong chi tiet va co ca hai nguon."""
    summary = _table(official, "duplicate_series_summary_by_source_city")
    grand = summary[(summary["scope"] == "RAW") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")].iloc[0]
    assert (int(grand["duplicate_groups"]), int(grand["same_price_groups"]), int(grand["divergent_price_groups"]), int(grand["extra_observations"]),
            int(grand["max_group_size"])) == (156_634, 3_377, 153_257, 195_704, 12)
    assert float(grand["divergent_share"]) == pytest.approx(153_257 / 156_634)
    finding = _table(official, "quality_findings").set_index("check_id").loc["duplicate_daily_series"]
    assert int(finding["count"]) == 156_634
    sample = _table(official, "duplicate_series_audit_sample")
    assert not sample.empty and set(sample["source_code"]) == {"local_primary", "vps"}
    sizes = sample.groupby("group_id")["price_rank_in_group"].size()
    assert (sizes == sample.groupby("group_id")["group_size"].first()).all()      # moi group audit co du tung option (khong cat bot)
    assert bool((sample.groupby("group_id")["price_per_night"].nunique() > 1).all())   # audit chi gom nhom KHAC gia
    spread = _table(official, "duplicate_series_price_spread_summary")
    assert {"absolute_vnd", "relative_symmetric"} <= set(spread["spread_kind"])


def test_near_time_collision_tap_trung_dung_hai_ngay_crawl_nhu_gpt_da_tu_join(official):
    """MINOR 1: 173 option-pair bucket 0-5 phut tap trung vao 2026-08-27 (103 cap, 13 hotel) va 2026-09-14 (70 cap, 3 hotel); tong khop bang stratification."""
    near = _table(official, "collision_option_near_time_concentration")
    stratified = _table(official, "collision_option_time_diff_stratification").set_index("time_diff_bucket")
    assert int(near["n_option_pairs"].sum()) == int(stratified.loc["0-5", "n_option_pairs"]) == 173
    assert int(near["n_exact_price_match"].sum()) == int(stratified.loc["0-5", "n_exact_price_match"]) == 85
    assert float(near["share_of_near_time_pairs"].sum()) == pytest.approx(1.0)
    by_date = near.groupby("vn_crawl_date")["n_option_pairs"].sum().to_dict()
    assert by_date == {"2026-08-27": 103, "2026-09-14": 70}
    assert near[near["vn_crawl_date"] == "2026-08-27"]["hotel_id"].nunique() == 13
    assert near[near["vn_crawl_date"] == "2026-09-14"]["hotel_id"].nunique() == 3
    assert int(near[near["vn_crawl_date"] == "2026-08-27"]["n_non_exact"].sum()) == 41
    assert int(near[near["vn_crawl_date"] == "2026-09-14"]["n_non_exact"].sum()) == 47


def test_hinh_active_hotel_va_bao_cao_co_chu_thich_cohort_va_anchor(official):
    """MINOR 2 + M4: report va hinh weekday/active-hotel cua run official ton tai; report co caveat 'khong phai uoc luong causal' o muc 7.4 va 7.7."""
    report = (official / "EDA_REPORT.md").read_text(encoding="utf-8")
    assert report.count("KHONG phai uoc luong causal") >= 2
    for figure in ("active_hotel_by_date", "checkin_coverage_weekday_month"):
        assert (official / "figures" / f"{figure}.png").stat().st_size > 10_000
