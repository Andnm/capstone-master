"""Render EDA_REPORT.md va DATA_DICTIONARY.md (GPT review 12 eda M4 + file 11 muc 5-6). Moi con so tinh TRUC TIEP tu `tables`/`data` cua CHINH
lan chay nay - khong co so lieu bia. Moi section co **Fact** (con so that + denominator), doi khi **Dien giai** (quan sat co che an toan gan
voi con so) va **Caveat** (gioi han/quy uoc). Day KHONG phai ket luan nghien cuu cuoi cung - do la viec cua vong GPT review ket qua that.

Danh sach bang/hinh moi section lay tu `publication.py` (khong duy tri tay) nen bao cao luon tro dung artifact co that.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import coverage_matrix
import db
import dictionary
import metrics
import publication

# Bang lich su CLAUDE.md muc 7.2 (series-exists coverage, bucket legacy, RAW) - CHI de doi chieu, khong phai ket qua cua lan chay nay.
HISTORICAL_SERIES_EXISTS_COVERAGE = {"0-3": 0.304, "4-7": 0.271, "8-14": 0.183, "15-30": 0.112, "31-60": 0.076, "61+": 0.071}
HISTORICAL_TOLERANCE_PP = 0.005  # 0,5 diem phan tram


# ============================================================================== dinh dang
def _fmt_int(value: Any) -> str:
    return "n/a" if value is None or (isinstance(value, float) and np.isnan(value)) else f"{int(value):,}"


def _fmt_pct(value: Any, digits: int = 1) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value):.{digits}%}"


def _fmt_num(value: Any, digits: int = 0) -> str:
    return "n/a" if value is None or pd.isna(value) else f"{float(value):,.{digits}f}"


def _format_cell(value: Any) -> str:
    """1 gia tri -> chuoi doc duoc trong bang Markdown. Float nguyen (vd 500000.0 tu SUM/COUNT) hien thi co dau phay ngan cach, KHONG duoc
    de pandas/`%g` tu doi sang ky hieu khoa hoc (5e+05) - xau cho mot bao cao ve GIA TIEN. NaN/NaT hien thi rong."""
    if value is None or value is pd.NA or value is pd.NaT:
        return ""
    if isinstance(value, float) and np.isnan(value):
        return ""
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return f"{int(value):,}"
    return str(value)


def _md_table(df: "pd.DataFrame | None", *, max_rows: int = 15, columns: list[str] | None = None) -> str:
    """Bang Markdown GFM nho, tu viet (khong them dependency `tabulate`)."""
    if df is None or len(df) == 0:
        return "_(khong co dong nao)_"
    view = df[columns] if columns else df
    view = view.head(max_rows)
    header = "| " + " | ".join(str(c) for c in view.columns) + " |"
    sep = "| " + " | ".join("---" for _ in view.columns) + " |"
    lines = [header, sep]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(_format_cell(v).replace("|", "/") for v in row) + " |")
    if len(df) > max_rows:
        lines.append(f"\n_(hien {max_rows}/{len(df)} dong - xem CSV day du)_")
    return "\n".join(lines)


def _artifacts_line(section: str) -> str:
    tables = [f"`{publication.table_artifact_path(n)}`" for n, s in publication.PUBLISHED_TABLES.items() if s.section == section]
    figures = [f"`{publication.figure_artifact_path(n)}`" for n, s in publication.PUBLISHED_FIGURES.items() if s.section == section]
    parts = []
    if tables:
        parts.append("Bang: " + ", ".join(tables))
    if figures:
        parts.append("Hinh: " + ", ".join(figures))
    return "\n\n".join(parts)


def _first(df: "pd.DataFrame") -> "pd.Series | None":
    return df.iloc[0] if len(df) else None


# ============================================================================== cac section
def _s71(ctx: dict[str, Any]) -> str:
    data, tables, manifest = ctx["data"], ctx["tables"], ctx["input_manifest"]
    core = data["core_counts"].iloc[0]
    ranges = _first(tables["observation_date_ranges_main"])
    rej = _first(tables["preflight_rejections"])
    recon = tables["preflight_reconciliation"]
    text = (
        "## 7.1 Preflight / snapshot identity\n\n" + _artifacts_line("7.1") + "\n\n"
        f"**Fact.** `hotels`={_fmt_int(core['hotels'])}, `crawl_runs`={_fmt_int(core['crawl_runs'])}, `crawl_run_items`={_fmt_int(core['crawl_run_items'])}, "
        f"`price_observations`={_fmt_int(core['price_observations'])}, `curated_observation_keys`={_fmt_int(core['curated_observation_keys'])}, "
        f"rejections={_fmt_int(rej['n_rejections'] if rej is not None else None)}. Non-terminal runs/items = "
        f"{int(data['non_terminal']['non_terminal_runs'].iloc[0])}/{int(data['non_terminal']['non_terminal_items'].iloc[0])} (PHAI = 0, da assert truoc khi thu thap tiep). "
        f"Khoang quan sat (VN) {ranges['observed_date_min']} -> {ranges['observed_date_max']}; check-in {ranges['checkin_date_min']} -> {ranges['checkin_date_max']}.\n\n"
        f"Doi soat voi warehouse validation report: {int(recon['match'].sum())}/{len(recon)} check khop (lech se lam pipeline FAIL truoc khi toi day):\n\n"
        f"{_md_table(recon)}\n\nNguon trong batch:\n\n{_md_table(tables['preflight_import_sources'], columns=['source_code', 'source_priority', 'dump_taken_at'])}\n\n"
        f"Canonicalization: `{data['snapshot'].canonicalization_version}` (commit `{data['snapshot'].canonicalization_git_commit[:10]}`)."
    )
    if manifest:
        through = manifest.get("protocol_complete_through_date_by_source", {})
        text += (
            "\n\n**protocol_complete_through_date** (theo nguon, ngay VN cua `dump_taken_at`): "
            + ", ".join(f"`{s}`={d}" for s, d in through.items()) + ". "
            f"**Gia dinh:** {manifest.get('protocol_complete_through_date_assumption', '')}\n\n"
            "**Cohort reproducibility** (declared vs computed members hash/size, khong chi hash byte tho cua workbook):\n\n"
            + _md_table(pd.DataFrame(manifest.get("cohort_workbook_versions", [])), columns=[
                "cohort_version", "effective_from_crawl_date", "declared_size", "computed_size", "validation_status"])
        )
    return text


def _near_time_paragraph(near: "pd.DataFrame") -> str:
    """Bang tap trung near-time (0-5 phut): cho thay divergence gan nhau ve thoi gian nam o dau (file 17 MINOR 1) - mo ta, khong ket luan parser."""
    if near is None or len(near) == 0:
        return "**Near-time (0-5 phut).** Khong co shared option-pair nao trong bucket 0-5 phut."
    by_date = near.groupby("vn_crawl_date", as_index=False).agg(
        n_option_pairs=("n_option_pairs", "sum"), n_non_exact=("n_non_exact", "sum"), n_hotels=("hotel_id", "nunique"))
    by_date["non_exact_rate"] = by_date["n_non_exact"] / by_date["n_option_pairs"]
    total_pairs, total_non_exact = int(near["n_option_pairs"].sum()), int(near["n_non_exact"].sum())
    return (
        f"**Near-time (0-5 phut) tap trung.** {_fmt_int(total_pairs)} option-pair near-time nam trong {_fmt_int(len(by_date))} ngay crawl / "
        f"{_fmt_int(near['hotel_id'].nunique())} hotel ({_fmt_int(total_non_exact)} khac gia, {_fmt_pct(total_non_exact / total_pairs)}); theo ngay crawl:\n\n"
        f"{_md_table(by_date, max_rows=10)}\n\nBang chi tiet `source x ngay x hotel`: `tables/collision_option_near_time_concentration.csv` ({_fmt_int(len(near))} dong). "
        "Day la mo ta phan bo (divergence khong deu toan he thong), KHONG tu dong gan loi parser - dang dieu tra theo hotel/session/duplicate key."
    )


def _s72(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    protocol = t["protocol_continuity_summary"]
    item_sum = _first(t["collision_item_summary"])
    opt_sum = _first(t["collision_option_summary"])
    cov_sum = _first(t["collision_option_coverage_summary"])
    return (
        "## 7.2 Source, ownership va protocol coverage (+ collision audit)\n\n" + _artifacts_line("7.2") + "\n\n"
        f"**Fact.** Item theo (nguon, ownership_status, exclusion_reason):\n\n{_md_table(t['ownership_by_source_status_reason'], max_rows=20)}\n\n"
        f"RAW so voi MAIN theo nguon (item + observation):\n\n{_md_table(t['raw_vs_main_by_source'])}\n\n"
        f"Owner success/failure/missing (outcome tren lich EXPECTED, mau so `n_scheduled`):\n\n{_md_table(protocol)}\n\n"
        "**Collision / source divergence audit (RAW, khong doi MAIN, khong average/dedupe hai nguon).** Cap chon HOAN TOAN theo khoa "
        "(ngay crawl VN, hotel_id, check-in), khong theo gia. Moi ty le co MAU SO RIENG:\n\n"
        f"- *Item-level* (mau so = collision item-pairs = **{_fmt_int(item_sum['n_collision_item_pairs'])}**): status khop "
        f"{_fmt_int(item_sum['n_status_concordant'])}, bat dong {_fmt_int(item_sum['n_status_disagreement'])} "
        f"({_fmt_pct(item_sum['status_disagreement_rate'])}).\n"
        f"- *Success-success item-pairs* (mau so cua option-level) = **{_fmt_int(item_sum['n_success_success_pairs'])}**; coverage tap canonical key: "
        f"jaccard trung binh {_fmt_num(cov_sum['mean_option_jaccard'], 3)}, {_fmt_int(cov_sum['n_pairs_identical_option_sets'])} cap giong het, "
        f"{_fmt_int(cov_sum['n_pairs_disjoint_option_sets'])} cap roi nhau; {_fmt_int(cov_sum['total_ambiguous_shared_keys'])} key chung bi loai vi "
        f"lap trong item ({_fmt_int(cov_sum['total_duplicate_canonical_keys'])} dong lap key).\n"
        f"- *Shared canonical option-pairs 1-1* (mau so = **{_fmt_int(opt_sum['n_option_pairs'])}**): exact price match "
        f"{_fmt_pct(opt_sum['exact_price_match_rate'])}; chenh tuyet doi trung vi {_fmt_num(opt_sum['median_price_abs_diff'])} VND, "
        f"lon nhat {_fmt_num(opt_sum['max_price_abs_diff'])} VND; chenh tuong doi (doi xung) trung vi {_fmt_pct(opt_sum['median_price_relative_diff'])}; "
        f"concordance breakfast {_fmt_pct(opt_sum['breakfast_included_concordance_rate'])}, free-cancellation "
        f"{_fmt_pct(opt_sum['free_cancellation_concordance_rate'])}, cancellation-policy "
        f"{_fmt_pct(opt_sum['cancellation_policy_concordance_rate'])}, tax-inclusion {_fmt_pct(opt_sum['price_includes_tax_concordance_rate'])}, "
        f"currency {_fmt_pct(opt_sum['currency_concordance_rate'])} (luon 100%: currency khong luu, ep VND). "
        f"`taxes_fees`: both-null {_fmt_int(opt_sum['n_taxes_both_null'])}, one-null {_fmt_int(opt_sum['n_taxes_one_null'])}, "
        f"both-present {_fmt_int(opt_sum['n_taxes_both_present'])}; NULL-safe concordance {_fmt_pct(opt_sum['taxes_fees_concordance_rate'])}, "
        f"median absolute diff tren both-present {_fmt_num(opt_sum['median_taxes_fees_abs_diff'])} VND.\n\n"
        "**Cach doc concordance (structural vs doc lap).** `breakfast`, `free_cancellation` va `cancellation_policy` deu nam TRONG `canonical_rate_key` "
        "(hash cua chinh 3 thuoc tinh nay) nen 2 option chi thanh 'shared canonical option-pair' khi 3 thuoc tinh do da bang nhau theo dinh nghia: "
        "concordance cua chung la STRUCTURAL - 100% o day KHONG phai bang chung doc lap rang hai parser doc giong nhau. Audit doc lap co y nghia chi gom "
        "`price_includes_tax` (khong nam trong key) va `taxes_fees` (phai doc cung 3 trang thai both-null/one-null/both-present: ty le concordance cao co the chi "
        "vi ca hai nguon deu khong cong bo so tien thue/phi).\n\n"
        f"Chenh lech thoi gian (bien audit bat buoc), stratify theo phut - item-level (`finished_at`):\n\n{_md_table(t['collision_item_time_diff_stratification'])}\n\n"
        f"Option-level (`observed_at`, kem ty le exact price match theo bucket):\n\n{_md_table(t['collision_option_time_diff_stratification'])}\n\n"
        f"{_near_time_paragraph(t['collision_option_near_time_concentration'])}\n\n"
        "**Caveat.** Gia khac nhau khi 2 nguon crawl o thoi diem khac nhau la *divergence quan sat duoc*, KHONG tu dong la loi parser: Booking doi gia/option "
        "theo thoi diem va session. Chi coi la nghi ngo khi khac o bucket 0-5 phut. MAIN giu owner theo ownership manifest; nguon con lai chi dung cho RAW audit."
    )


def _s73(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    dur = t["run_duration_and_throughput"]
    protocol_runs = dur[dur["is_protocol_run"].astype(bool)] if len(dur) else dur
    n_pilot = len(dur) - len(protocol_runs)
    flags = t["run_day_operational_flags"]
    appendix = t["run_day_operational_flags_raw_appendix"]
    flagged = flags[flags["is_any_anomalous"]] if len(flags) else flags
    appendix_flagged = appendix[appendix["is_any_anomalous"]] if len(appendix) else appendix
    appendix_pilot_flagged = appendix_flagged[~appendix_flagged["is_protocol_source_day"]] if len(appendix_flagged) else appendix_flagged
    median_by_source = (protocol_runs.groupby("source_code")[["duration_minutes", "items_per_hour", "observations_per_hour"]].median().reset_index()
                        if len(protocol_runs) else pd.DataFrame())
    return (
        "## 7.3 Crawl operations va capacity\n\n" + _artifacts_line("7.3") + "\n\n"
        f"**Fact.** {len(dur)} run terminal co `finished_at`: **{len(protocol_runs)} run PRODUCTION** (protocol, `is_protocol_run`) + {n_pilot} run pilot/pre-protocol "
        f"(khong co item owner, giu o RAW). Trung vi theo nguon CHI tren run production (phut, item/gio, observation/gio):\n\n{_md_table(median_by_source)}\n\n"
        f"Run production keo qua ngay crawl ke tiep: {int(protocol_runs['crosses_next_crawl_day'].sum()) if len(protocol_runs) else 0}/{len(protocol_runs)}. "
        f"So check-in slot/run production (phan bo): trung vi {_fmt_num(protocol_runs['n_checkin_slots'].median() if len(protocol_runs) else np.nan, 0)}.\n\n"
        f"**Co bat thuong (grain = SOURCE-DAY, tuc `source x vn_crawl_date`; bang chinh chi gom source-day production).** Baseline z-score tinh RIENG tren cac source-day "
        f"production cua tung nguon (|z| >= 2 tren duration / error / sold-out / not-bookable rate); run pilot khong dinh hinh baseline: "
        f"**{len(flagged)}/{len(flags)} source-day production bi flag**.\n\n"
        f"{_md_table(flagged, columns=['source_code', 'vn_crawl_date', 'n_runs', 'n_items', 'duration_minutes', 'error_rate', 'sold_out_rate', 'not_bookable_rate'])}\n\n"
        f"Phu luc RAW (`tables/run_day_operational_flags_raw_appendix.csv`): {len(appendix)} source-day gom ca pilot/pre-protocol, baseline gom ca pilot nen "
        f"{len(appendix_flagged)} co, trong do {len(appendix_pilot_flagged)} la source-day pilot - CHI de doi chieu, khong dung ket luan production.\n\n"
        f"Ma loi (error/not_bookable/partial) theo ngay: `tables/run_day_error_code_counts_raw.csv` "
        f"({len(t['run_day_error_code_counts_raw'])} dong) - moi ma CAPTCHA/block neu co se hien o cot `last_error_code`.\n\n"
        "**Dien giai.** Run keo qua ngay ke tiep lam giam buffer truoc lich 00:30 hom sau (CLAUDE.md muc 4.8). **Caveat.** Day la quality/capacity metric, "
        "KHONG dung thoi luong run de suy ra chat luong gia; co bat thuong chi la FLAG, khong loc ngay nao."
    )


def _anchor_caveat(t: dict[str, "pd.DataFrame"]) -> str:
    """Caveat anchor check-in (file 17 M4): so anchor phan biet theo thu/thang tu bang item-grain - coverage cua cac anchor da chon, KHONG phai weekday/holiday effect."""
    weekday = t["item_checkin_weekday_distribution_main"]
    anchors = t["checkin_anchor_dates_main"]
    if len(weekday) == 0 or len(anchors) == 0:
        return "**Anchor check-in.** Khong co anchor check-in nao."
    per_weekday = ", ".join(f"{r.weekday} {int(r.n_distinct_checkin_dates)}" for r in weekday.sort_values("weekday_number").itertuples())
    return (
        f"**Anchor check-in ({_fmt_int(len(anchors))} ngay phan biet, {anchors['checkin_date'].min()} -> {anchors['checkin_date'].max()}).** So anchor theo thu: {per_weekday} "
        "(tong = so anchor; danh sach ngay: `tables/checkin_anchor_dates_main.csv`, cot `checkin_dates` cua bang weekday). Cac bang/hinh theo thu, thang va co holiday la mo ta "
        "COVERAGE/PHAN PHOI cua cac anchor da chon; chung KHONG phai uoc luong causal weekday effect hay holiday uplift - mot thu chi co k anchor thi ket qua cua thu do "
        "la ket qua cua k ngay cu the lap qua nhieu hotel/crawl day, khong the tach khoi ngay do."
    )


def _s74(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    active = t["active_hotel_by_crawl_date_source"]
    tracked = t["checkin_dates_tracked_by_crawl_date_source"]
    active_desc = active.groupby("source_code")["n_active_hotels"].describe().reset_index() if len(active) else pd.DataFrame()
    tracked_desc = tracked.groupby("source_code")["n_checkin_dates_tracked"].describe().reset_index() if len(tracked) else pd.DataFrame()
    return (
        "## 7.4 Hotel va check-in coverage\n\n" + _artifacts_line("7.4") + "\n\n"
        f"**Fact.** Hotel active (>= 1 item DA DUOC OWN trong ngay) theo nguon:\n\n{_md_table(active_desc)}\n\n"
        f"So check-in date duoc theo doi/ngay theo nguon:\n\n{_md_table(tracked_desc)}\n\n"
        f"Cohort attrition theo version:\n\n{_md_table(t['cohort_attrition_by_version'])}\n\n"
        f"Check-in theo thu (PRIMARY, owned item; `is_weekend_fri_sat` = Thu Sau/Bay):\n\n{_md_table(t['item_checkin_weekday_distribution_main'])}\n\n"
        f"Check-in theo thang (PRIMARY, owned item):\n\n{_md_table(t['item_checkin_month_distribution_main'])}\n\n"
        "Cac bang `checkin_*_distribution_main.csv` o grain observation duoc giu lam phu luc option-mix; "
        "khong dung chung lam ket luan coverage vi hotel/item co nhieu room option se duoc nhan trong so. Lead-time bucket coverage: xem 7.6.\n\n"
        f"{_anchor_caveat(t)}\n\n"
        "**Caveat.** \"Active\" dung ownership_status (owner_success/owner_failure), KHONG dung `hotels.booking_status` hien tai (se viet lai lich su - "
        "CLAUDE.md muc 2 ve cohort v1/v1.1/v2). Mac Valley duoc ghi nhan dung truoc khi roi cohort (attrition tu nhien, khong phai thay the)."
    )


def _s75(ctx: dict[str, Any]) -> str:
    t, data = ctx["tables"], ctx["data"]
    protocol = t["protocol_continuity_summary"]
    exceptions = t["protocol_continuity_exceptions"]
    by_days = t["canonical_series_turnover_by_observed_days"]
    max_gap = t["canonical_series_max_gap_distribution"]
    n_series = int(by_days["n_series"].sum()) if len(by_days) else 0
    n_reappear = int(by_days["n_series_with_reappearance"].sum()) if len(by_days) else 0
    unattributed = int(t["protocol_continuity_unattributed_errors"]["n_unattributed_errors"].sum()) if len(t["protocol_continuity_unattributed_errors"]) else 0
    return (
        "## 7.5 Protocol continuity va room/rate turnover\n\n" + _artifacts_line("7.5") + "\n\n"
        "Ba metric TACH RIENG, khong gom thanh 1 loai \"gap\":\n\n"
        f"1. **Item/protocol continuity** (lich EXPECTED tu ownership + cohort history; cutoff tren la `protocol_complete_through_date`):\n\n{_md_table(protocol)}\n\n"
        f"   Ngoai le (khong phai owner_success): {len(exceptions)} slot - liet ke o `tables/protocol_continuity_exceptions.csv`. Item loi khong resolve duoc hotel_id: {unattributed}.\n"
        "   `missing_source_run` = ca ngay khong co run cua nguon; `missing_item_in_existing_run` = ngay co run nhung thieu item nay.\n"
        f"2. **Canonical-series presence/turnover** ({_fmt_int(n_series)} series, tinh tren TOAN BO population): "
        f"{_fmt_int(n_reappear)} series co reappearance (max_gap > 1, {_fmt_pct(n_reappear / n_series if n_series else np.nan)}). "
        f"Phan bo theo so ngay observed:\n\n{_md_table(by_days)}\n\nPhan bo max gap:\n\n{_md_table(max_gap)}\n\n"
        f"Phan bo median consecutive-day gap:\n\n{_md_table(t['canonical_series_median_gap_distribution'])}\n\n"
        "Bang `canonical_series_turnover_by_series_main.csv` luu first/last/span/max/median gap cho moi series; "
        "`canonical_series_turnover_sample_main.csv` chi la top-gap sample de audit, khong lam nguon tinh metric population.\n\n"
        "3. **Parser completeness**: chi ket luan parser missing khi item success/available co payload vi pham completeness rule cua 7.9 - xem missingness.\n\n"
        "**Caveat.** Ngay vang cua 1 canonical series chi la `not_observed/turnover_unknown` (inventory turnover that su co the lam room/rate bien mat roi quay lai); "
        "KHONG suy moi ngay giua first/last la expected roi goi la missing."
    )


def _s76(ctx: dict[str, Any]) -> str:
    t, data = ctx["tables"], ctx["data"]
    cal = t["observation_counts_by_calendar_flags_main"]
    events = t["vn_holidays_events_audit"]
    return (
        "## 7.6 Lead time va calendar coverage\n\n" + _artifacts_line("7.6") + "\n\n"
        f"**Fact.** Lead-time distribution PRIMARY (owned item, moi item dung 1 lan):\n\n{_md_table(t['item_lead_time_bucket_distribution_main'])}\n\n"
        f"Theo (effective city, nguon, bucket): `tables/item_lead_time_bucket_distribution_by_city_source_main.csv` "
        f"({len(t['item_lead_time_bucket_distribution_by_city_source_main'])} dong).\n\n"
        f"Coverage theo co calendar cua ngay check-in, PRIMARY o item grain (kem so ngay va ngay-city cell distinct):\n\n"
        f"{_md_table(t['item_calendar_coverage_main'])}\n\n"
        f"Phu luc option-weighted: lead-time observation va calendar observation nam o "
        f"`lead_time_bucket_distribution_main.csv`, `lead_time_bucket_distribution_by_city_source_main.csv` va "
        f"`observation_counts_by_calendar_flags_main.csv` ({len(cal)} nhom co).\n\n"
        f"VN holidays: {len(events)} event (`{data['holiday_csv'].path.name}`, sha256 `{data['holiday_csv'].sha256[:12]}...`); bang ngay-city trung gian "
        f"`tables/holiday_calendar_flags_by_checkin_date_city.csv` ({len(t['holiday_calendar_flags_by_checkin_date_city'])} dong, du 7 cot co/dem).\n\n"
        "**Caveat.** Calendar dung `holiday_date = checkin_date` (nhu cau tai ngay nhan phong, KHONG phai ngay quan sat). National event ap dung ca 5 thanh pho, "
        "city event chi dung city; event status `provisional` chua duoc cong bo chinh thuc."
    )


def _s77(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    main, raw = _first(t["price_distribution_overall_main"]), _first(t["price_distribution_overall_raw"])
    outliers = t["price_outlier_summary_by_hotel_main"]
    sens = t["price_sensitivity_summary_main"]
    quality = t["quality_findings"].set_index("check_id")
    tpn = quality.loc["price_total_per_night_inconsistent"] if "price_total_per_night_inconsistent" in quality.index else None

    def stats(row):
        if row is None:
            return "khong co du lieu"
        return (f"n={_fmt_int(row['n_obs'])}, min={_fmt_num(row['min_price'])}, P1={_fmt_num(row['p1'])}, P5={_fmt_num(row['p5'])}, median={_fmt_num(row['p50'])}, "
                f"P75={_fmt_num(row['p75'])}, P95={_fmt_num(row['p95'])}, P99={_fmt_num(row['p99'])}, max={_fmt_num(row['max_price'])} (VND)")

    return (
        "## 7.7 Price distribution\n\n" + _artifacts_line("7.7") + "\n\n"
        f"**Fact.** Observation co gia hop le, khong sold-out, grain observation - MAIN: {stats(main)}.\n\nRAW: {stats(raw)}.\n\n"
        f"Theo thanh pho (MAIN):\n\n{_md_table(t['price_distribution_by_city_main'], columns=['city', 'n_obs', 'p5', 'p50', 'p95', 'p99', 'max_price'])}\n\n"
        f"Theo lead-time bucket (MAIN):\n\n{_md_table(t['price_distribution_by_lead_time_bucket_main'], columns=['lead_time_bucket', 'n_obs', 'p5', 'p50', 'p95'])}\n\n"
        f"Theo thu check-in (MAIN; `n_distinct_checkin_dates` = so ngay anchor cua thu do):\n\n"
        f"{_md_table(t['price_distribution_by_weekday_main'], columns=['weekday', 'is_weekend_fri_sat', 'n_distinct_checkin_dates', 'n_obs', 'p50', 'mean_price'])}\n\n"
        f"Theo co holiday/Tet/festival/major-event (MAIN):\n\n{_md_table(t['price_distribution_by_calendar_flags_main'], columns=['is_public_holiday', 'is_tet', 'is_festival_period', 'is_major_event', 'n_distinct_checkin_dates', 'n_obs', 'p50', 'mean_price'])}\n\n"
        f"{_anchor_caveat(t)}\n\n"
        f"Hotel-level dispersion: {len(t['price_hotel_dispersion_main'])} hotel co >= 5 observation. Sensitivity (grain hotel x check-in x ngay quan sat, "
        f"min / median hop le - hotel nhieu option khong lan at hotel it option):\n\n{_md_table(sens)}\n\n"
        f"Robust within-hotel outlier (|gia - median hotel| >= 5 robust-sigma, CHI FLAG, khong xoa): {_fmt_int(outliers['n_outliers'].sum() if len(outliers) else 0)} "
        f"observation tren {len(outliers)} hotel duoc quet (hotel < 5 observation khong bao gio bi flag) - sample <= 200 lech nhat o "
        f"`tables/price_outlier_sample_main.csv`.\n\n"
        f"`price_total` = `price_per_night` cho stay 1 dem: {_fmt_int(tpn['count']) if tpn is not None else 'n/a'} vi pham (xem quality_findings).\n\n"
        "**Dien giai.** Distribution o grain observation tra loi \"gia cac room option da thu duoc\", KHONG phai \"gia khach san trung binh\"; phan phoi lech phai manh "
        "nen histogram log10 doc duoc duoi tot hon. **Caveat.** Khong winsorize truoc khi bao distribution goc; MAIN la ket luan chinh, RAW chi de doi chieu."
    )


def _s78(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    overall = t["item_availability_overall"].iloc[0]
    present = metrics.status_present_report(t["item_availability_overall"])
    by_hotel = t["item_availability_by_hotel"]
    top_nb = by_hotel.sort_values("n_not_bookable", ascending=False).head(5) if len(by_hotel) else by_hotel
    rate_cols = [f"{s}_rate" for s in metrics.TERMINAL_ITEM_STATUSES]
    return (
        "## 7.8 Availability state (item grain)\n\n" + _artifacts_line("7.8") + "\n\n"
        f"**Fact.** n_items MAIN terminal = {_fmt_int(overall['n_items'])}; " + ", ".join(
            f"{s}={_fmt_int(present[s])} ({_fmt_pct(overall[f'{s}_rate'], 2)})" for s in metrics.TERMINAL_ITEM_STATUSES) +
        f". Status co mat (0 = van bao, khong bi bo qua): {present}.\n\n"
        f"Theo thanh pho:\n\n{_md_table(t['item_availability_by_city'], columns=['city', 'n_items', *rate_cols])}\n\n"
        f"Theo lead-time bucket (checkin - ngay crawl VN):\n\n{_md_table(t['item_availability_by_lead_time_bucket'], columns=['lead_time_bucket', 'n_items', *rate_cols])}\n\n"
        f"Theo thang check-in:\n\n{_md_table(t['item_availability_by_checkin_month'], columns=['checkin_month', 'n_items', *rate_cols])}\n\n"
        f"Theo hotel: {len(by_hotel)} hotel (`tables/item_availability_by_hotel.csv`); 5 hotel co nhieu not_bookable nhat:\n\n"
        f"{_md_table(top_nb, columns=['hotel_id', 'city', 'n_items', 'n_not_bookable', 'not_bookable_rate'])}\n\n"
        f"`not_bookable` rate theo (ngay crawl, hotel): `tables/item_availability_by_crawl_date_hotel.csv` ({len(t['item_availability_by_crawl_date_hotel'])} dong).\n\n"
        "**Caveat.** Rate PHAI o grain item (moi item dung 1 lan): 1 item sold-out/not_bookable chi co 1 sentinel observation, khong phai N option nhu item success. "
        "`hotels.booking_status` la snapshot CUOI - lich su trang thai lay tu item/run (timeline o bang tren), khong backfill bang snapshot hien tai. "
        "Sold-out KHONG phai missing price do parser; sentinel chi dung audit consistency (`sold_out_sentinel_consistency`), khong lam denominator. "
        "`error` va `partial` tach rieng, khong gop vao rate nao khac."
    )


def _s79(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    taxonomy = t["missingness_null_taxonomy"]
    class_summary = t["missingness_null_class_summary"]
    required = t["missingness_required_contract_by_field"]
    nonzero = taxonomy[taxonomy["n_null"] > 0].sort_values("null_rate", ascending=False)
    by_status = t["missingness_by_item_status_sold_out"]
    structural = by_status[by_status["missing_kind"] == "structural_expected"]
    class_dependent_on_sentinel = by_status[(by_status["missing_kind"] == "class_dependent") & by_status["is_sold_out"]]
    sentinel_by_class = class_dependent_on_sentinel.groupby("null_class")["n_null"].sum() if len(class_dependent_on_sentinel) else pd.Series(dtype="int64")
    sentinel_text = ", ".join(f"{name}={_fmt_int(value)}" for name, value in sentinel_by_class.items() if value) or "0"
    artifact = t["artifact_completeness_by_source_crawl_date"]
    artifact_requested = artifact[artifact["save_artifacts"]] if len(artifact) else artifact
    missing_requested = int(
        artifact_requested[["n_html_missing", "n_screenshot_missing"]].sum().sum()
    ) if len(artifact_requested) else 0
    return (
        "## 7.9 Missingness va parser completeness\n\n" + _artifacts_line("7.9") + "\n\n"
        "**Taxonomy NULL (registry `null_taxonomy_registry.csv`).** Moi NULL tren observation available duoc GAN LOP, khong gop vao mot tong 'unexpected': "
        "`required_contract` (NULL la vi pham hop dong du lieu), `optional_listing` (Booking co the khong cong bo - mo ta listing, khong phai loi), "
        "`source_metadata_expected_gap` (thieu theo nguon da khai bao, vd `git_commit` cua VPS). Toan bo bang missingness giu nguyen (khong che NULL); "
        "`canonical_key_role` ghi field co nam trong `room_identity_key`/`rate_plan_key` hay khong.\n\n"
        f"**Fact.** 5 field group toi thieu (room identity, rate plan, price, hotel attributes, artifact/source metadata). Tong theo lop (mau so RIENG tung lop; cell = "
        f"(source, field, observation)):\n\n{_md_table(class_summary)}\n\n"
        f"Field `required_contract` (NULL la vi pham; mau so = observation available cua CHINH field, nguon mien tru bi loai khoi mau so):\n\n"
        f"{_md_table(required, max_rows=12, columns=['field', 'canonical_key_role', 'n_null', 'n_total', 'null_rate', 'sources_counted', 'sources_exempt'])}\n\n"
        f"Field co NULL tren observation available (MAIN, theo nguon, kem lop):\n\n"
        f"{_md_table(nonzero, max_rows=16, columns=['source_code', 'null_class', 'canonical_key_role', 'field', 'n_null', 'n_total', 'null_rate'])}\n\n"
        f"Theo scraper/selector version: `tables/missingness_by_selector_version.csv`; theo ngay crawl: `missingness_by_crawl_date.csv`; theo city: `missingness_by_city.csv`.\n\n"
        f"Theo item status va sold-out: structural missing tren sentinel sold-out (khong co payload phong/gia; ky vong, khong phai loi) = "
        f"{_fmt_int(structural['n_null'].sum() if len(structural) else 0)} cell NULL; NULL o field hotel/run metadata tren sentinel (`class_dependent`, doc theo `null_class`) = {sentinel_text}.\n\n"
        f"Artifact completeness o item grain: {len(artifact)} nhom source/date/save_artifacts; khi `save_artifacts=TRUE` co "
        f"{_fmt_int(missing_requested)} HTML/screenshot path bi thieu. `save_artifacts=FALSE` duoc ghi "
        "`structural_not_requested`, khong bi gan nhan parser missing.\n\n"
        "**Caveat.** Structural missing (sold-out khong co room payload) TACH khoi NULL tren available observation; NULL tren available observation doc theo LOP (khong tu dong la bat thuong). "
        "Mau so la `field_value_cell` (source, field, observation) - 1 observation dong gop nhieu cell, KHONG phai ty le observation bi missing; rieng finding `required_null_<field>` "
        "dung mau so observation cua chinh field."
    )


def _s710(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    legacy = t["reference_series_exists_coverage_raw_legacy_bucket"].copy()
    if len(legacy):
        legacy["historical"] = legacy["lead_time_bucket"].map(HISTORICAL_SERIES_EXISTS_COVERAGE)
        legacy["delta_vs_historical"] = legacy["series_reference_rate"] - legacy["historical"]
        legacy["within_0_5pp"] = legacy["delta_vs_historical"].abs() <= HISTORICAL_TOLERANCE_PP
    n_out = int((~legacy["within_0_5pp"]).sum()) if len(legacy) else 0
    approval = t["reference_approval_by_city_month"]
    status = t["reference_status_evidence_summary"]
    uniq = t["reference_uniqueness_per_series"]
    multi = int(uniq.loc[uniq["n_approved"] > 1, "n_series"].sum()) if len(uniq) else 0
    return (
        "## 7.10 Full-history reference audit\n\n" + _artifacts_line("7.10") + "\n\n"
        f"**Fact.** Approved={_fmt_int(approval['approved'].sum() if len(approval) else 0)}, proposed={_fmt_int(approval['proposed'].sum() if len(approval) else 0)} "
        f"(theo city/thang check-in: `tables/reference_approval_by_city_month.csv`). Candidate coverage / distinct run+item count theo status:\n\n{_md_table(status)}\n\n"
        f"Tinh duy nhat: series co > 1 reference approved = {multi} (ky vong 0).\n\n"
        f"**Exact approved-key observation coverage** (numerator: observation co canonical room/rate key khop reference approved; denominator: observation khong sold-out trong scope). "
        f"MAIN (bang chinh):\n\n{_md_table(t['reference_exact_key_coverage_main'])}\n\nRAW (phu luc):\n\n{_md_table(t['reference_exact_key_coverage_raw'])}\n\n"
        f"**Item-level exact-reference availability** (numerator: success MAIN item co >= 1 option khop; denominator: success MAIN item thuoc series co reference approved), theo lead-time:\n\n"
        f"{_md_table(t['reference_item_level_availability_by_lead_time'])}\n\n"
        f"**Series-has-approved-reference** (metric LONG, RAW, bucket legacy 0-3 gop) doi chieu bang lich su CLAUDE.md muc 7.2 - {n_out}/{len(legacy)} bucket lech > 0,5 diem phan tram:\n\n"
        f"{_md_table(legacy, columns=['lead_time_bucket', 'n_observations', 'series_reference_rate', 'historical', 'delta_vs_historical', 'within_0_5pp'])}\n\n"
        "**Caveat.** Hai metric (exact-key va series-exists) KHONG cung dinh nghia - khong gop thanh 1 con so. Full-history turnover KHONG phai causal train coverage: "
        "reference full-history 'dinh' theo phong/rate con ton tai o lan crawl dau, tinh lai tren toan lich su lo ra phan da mat hang/doi rate; "
        "khong duoc loc con so approved roi coi la toan quan the. Ty le lich su thuoc bang RAW phu luc; ket luan chinh dung MAIN va ghi denominator rieng, "
        "khong ep hai scope cho cung 1 con so. Ba trang thai `unavailable/alias/ambiguous` chi ton tai sau causal matching o Wave B."
    )


def _duplicate_section(t: dict[str, "pd.DataFrame"]) -> str:
    """Dac trung nhom trung canonical key (file 17 M3): so lieu that + anh huong len reference eligibility / collision / Wave B - trung lap, KHONG ket luan parser sai
    va khong doi canonicalization_version."""
    summary = t["duplicate_series_summary_by_source_city"]
    spread = t["duplicate_series_price_spread_summary"]
    audit = t["duplicate_series_audit_sample"]
    coverage = _first(t["collision_option_coverage_summary"])
    grand = summary[(summary["scope"] == "RAW") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")]
    if grand.empty or int(grand.iloc[0]["duplicate_groups"]) == 0:
        return "**Nhom trung canonical key (item x canonical room/rate key).** Khong co nhom nao co > 1 observation."
    g = grand.iloc[0]
    by_source = summary[(summary["scope"] == "RAW") & (summary["city"] == "(all)")]
    by_city = summary[(summary["scope"] == "RAW") & (summary["source_code"] == "(all)") & (summary["city"] != "(all)")]
    main = summary[(summary["scope"] == "MAIN") & (summary["source_code"] == "(all)") & (summary["city"] == "(all)")].iloc[0]
    spread_view = spread[(spread["scope"] == "RAW")]
    cols = ["source_code", "duplicate_groups", "n_groups", "duplicate_group_rate", "extra_observations", "same_price_groups", "divergent_price_groups",
            "divergent_share", "max_group_size"]
    relative = spread[(spread["scope"] == "RAW") & (spread["source_code"] == "(all)") & (spread["spread_kind"] == "relative_symmetric")].iloc[0]
    ambiguous = int(coverage["total_ambiguous_shared_keys"]) if coverage is not None and "total_ambiguous_shared_keys" in coverage else 0
    return (
        "**Nhom trung canonical key (`duplicate_daily_series`, item x canonical room/rate key, RAW).** "
        f"{_fmt_int(g['duplicate_groups'])}/{_fmt_int(g['n_groups'])} nhom ({_fmt_pct(g['duplicate_group_rate'])}) co > 1 observation; "
        f"{_fmt_int(g['extra_observations'])} observation du; lon nhat {_fmt_int(g['max_group_size'])} option/nhom. **{_fmt_int(g['divergent_price_groups'])} nhom "
        f"({_fmt_pct(g['divergent_share'], 2)}) co tu 2 muc gia tro len** (chi {_fmt_int(g['same_price_groups'])} nhom cung mot muc gia) - day KHONG phai dong trung byte. "
        f"Do lech doi xung trong nhom khac gia: trung vi {_fmt_pct(relative['spread_q50'])}, P90 {_fmt_pct(relative['spread_q90'])}. Hien tuong co o ca hai nguon va ca nam thanh pho "
        f"(MAIN: {_fmt_int(main['duplicate_groups'])} nhom trung, {_fmt_pct(main['divergent_share'], 2)} khac gia). Theo nguon:\n\n{_md_table(by_source, columns=cols)}\n\n"
        f"Theo city (RAW): `tables/duplicate_series_summary_by_source_city.csv` ({_fmt_int(len(by_city))} dong city + chi tiet source x city). Phan phoi do lech gia (nhom khac gia):\n\n"
        f"{_md_table(spread_view, columns=['source_code', 'spread_kind', 'n_divergent_groups', 'spread_mean', 'spread_q50', 'spread_q90', 'spread_q99', 'spread_max'])}\n\n"
        f"Audit sample xac dinh ({_fmt_int(audit['group_id'].nunique() if len(audit) else 0)} nhom, {_fmt_int(len(audit))} dong observation, moi nguon x tieu chi lay nhom dau cua tung hotel): "
        "`tables/duplicate_series_audit_sample.csv` (hotel, check-in, item, canonical key, `room_option_index`, gia, gia goc/giam gia/thue-phi/rooms_left va cac cot ngoai canonical key).\n\n"
        "**Dien giai (trung lap - CHUA ket luan).** Nhieu option KHAC GIA cung roi vao mot canonical (room, rate) key, nghia la key hien tai khong phan biet duoc chung. "
        "Anh huong (khong sua trong vong EDA nay): (1) **reference eligibility** - candidate/reference chi duoc duyet khi moi item co DUNG MOT option khop "
        "(`observation_count == distinct_item_count`), nen series co nhom trung key khong dat unique-per-item; "
        f"(2) **collision option-level 1-1** - {_fmt_int(ambiguous)} key chung bi loai khoi so sanh gia vi lap trong item; "
        "(3) **Wave B** - mot item co nhieu option cung key co the cho nhieu exact match cung score (`ambiguous`), va cach chon offer lam daily snapshot/target la quyet dinh rieng. "
        "Khong ket luan parser sai hay can doi canonicalizer; `canonicalization_version` khong doi; doi canonical key hoac quy tac chon cheapest/representative offer la "
        "quyet dinh kien truc rieng sau khi xem audit sample."
    )


def _s711(ctx: dict[str, Any]) -> str:
    findings = ctx["tables"]["quality_findings"]
    n_flagged = int((findings["count"] > 0).sum())
    n_actionable = int(((findings["count"] > 0) & (findings["severity"] != "info")).sum())
    return (
        "## 7.11 Data quality findings\n\n" + _artifacts_line("7.11") + "\n\n"
        f"**Fact.** {len(findings)} check da chay, {n_flagged} check co count > 0 ({n_actionable} o muc medium/high; cac finding NULL theo lop `optional_listing`/"
        f"`source_metadata_expected_gap` la `info` mo ta, khong phai loi) - `quality_findings.csv`:\n\n"
        f"{_md_table(findings, max_rows=40, columns=['check_id', 'severity', 'scope', 'grain', 'count', 'denominator', 'rate'])}\n\n"
        f"{_duplicate_section(ctx['tables'])}\n\n"
        "**Caveat.** Check co count = 0 van co 1 dong (chung minh da chay, khong phai im lang vi khong co gi de bao). Moi finding co sample_keys, likely_cause, "
        "recommended_action trong CSV. Outlier price CHI FLAG (khong xoa, khong phai `is_anomaly`); collision/source divergence la divergence quan sat duoc, khong tu dong la loi parser."
    )


def _s712(ctx: dict[str, Any]) -> str:
    t = ctx["tables"]
    share = t["series_evidence_runs_share"]
    overall = share[share["city"] == "(all)"]
    hist = t["history_length_by_hotel_checkin_main"]
    hist_by_bucket = hist.groupby("history_days_bucket", as_index=False)[["n_series", "sum_span_days"]].sum() if len(hist) else hist
    return (
        "## 7.12 Readiness cho dataset/model\n\n" + _artifacts_line("7.12") + "\n\n"
        f"**Fact.** Cap ngay quan sat cach DUNG K ngay (ly thuyet) va tach biet `actual_causal_labels` (NULL cho toi Wave B):\n\n{_md_table(t['dataset_readiness_by_horizon'])}\n\n"
        f"Do dai lich su theo (hotel, check-in) (so ngay crawl co snapshot success):\n\n{_md_table(hist_by_bucket)}\n\n"
        f"Ty le series co >= 3 evidence run: {_fmt_pct(overall['share_ge3'].iloc[0]) if len(overall) else 'n/a'} "
        f"({_fmt_int(overall['n_series_ge3'].iloc[0]) if len(overall) else 'n/a'}/{_fmt_int(overall['n_series'].iloc[0]) if len(overall) else 'n/a'}).\n\n"
        "Lead-time va city coverage (item grain): xem `tables/item_lead_time_bucket_distribution_by_city_source_main.csv` "
        "va `tables/item_availability_by_lead_time_bucket.csv`.\n\n"
        "**Caveat.** Day la CAP LY THUYET tu full-history, KHONG phai label causal cua dataset ML - label thuc te chi tinh sau causal freeze + item matching (Wave B chua trien khai)."
    )


def _s_wave_b(ctx: dict[str, Any]) -> str:
    wave_b = ctx["tables"]["wave_b_dataset_version_readiness"]
    ready = bool(wave_b["ready"].any()) if len(wave_b) else False
    return (
        "## Wave B dataset readiness\n\n"
        f"**Fact.** {'CO' if ready else 'CHUA CO'} dataset_version nao vua `status=\"pass\"` vua du du lieu ca 3 bang `ml_*` cho batch dang doc.\n\n"
        + (_md_table(wave_b) if len(wave_b) else "_(khong co dataset_build_manifests nao cho batch nay)_")
    )


def _s_coverage(ctx: dict[str, Any]) -> str:
    matrix = ctx["matrix"]
    missing = coverage_matrix.missing_required_rows(matrix)
    status = ("**FULL WAVE A** - moi bullet 7.1-7.12 la `implemented`." if missing.empty
              else f"**PARTIAL - KHONG duoc goi la full Wave A**: {len(missing)}/{len(matrix)} bullet chua implemented.")
    return (
        "## Coverage matrix (plan 7.1-7.12)\n\n"
        f"{status} Bullet: {len(matrix)}; implemented: {len(matrix) - len(missing)}. Chi tiet + metric ID + artifact + test ID: `EDA_COVERAGE_MATRIX.md` / `.csv` "
        "(trong artifact manifest; `test_coverage_matrix.py` fail neu bullet thieu mapping/artifact/test)."
    )


_SECTIONS = (_s71, _s72, _s73, _s74, _s75, _s76, _s77, _s78, _s79, _s710, _s711, _s712, _s_wave_b, _s_coverage)


def build_report_sections(data: dict[str, Any], tables: dict[str, "pd.DataFrame"], *, matrix: "pd.DataFrame",
                          input_manifest: dict[str, Any] | None = None) -> list[str]:
    ctx = {"data": data, "tables": tables, "matrix": matrix, "input_manifest": input_manifest}
    return [section(ctx) for section in _SECTIONS]


def write_report(data: dict[str, Any], tables: dict[str, "pd.DataFrame"], analysis_dir: Path, *, matrix: "pd.DataFrame",
                 input_manifest: dict[str, Any] | None = None) -> None:
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    missing = coverage_matrix.missing_required_rows(matrix)
    title = "EDA Wave A Report" if missing.empty else "EDA Wave A Report (PARTIAL - coverage matrix con thieu)"
    header = (
        f"# {title} - {snapshot.database} / {snapshot.batch_id}\n\n"
        f"Sinh luc {dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()} tu du lieu THAT trong `tables/`, `quality_findings.csv`, "
        f"`eda_summary.json`, `dataset_readiness_by_horizon.csv` cua chinh lan chay nay. Moi con so trong bao cao nay lay TRUC TIEP tu cac file do - khong co so lieu bia. "
        f"Xem `input_manifest.json` cho provenance day du (git commit, hash manifest, thoi luong/peak memory) va `TABLE_METADATA.csv` cho scope/grain/denominator cua tung bang.\n\n"
        f"**Cach doc:** moi section co **Fact** (con so/bang that + denominator), doi khi **Dien giai** (quan sat co che an toan gan voi con so) va **Caveat** "
        f"(gioi han/quy uoc). Day KHONG phai ket luan nghien cuu cuoi cung cho luan van.\n\n---\n"
    )
    body = "\n\n".join(build_report_sections(data, tables, matrix=matrix, input_manifest=input_manifest))
    (analysis_dir / "EDA_REPORT.md").write_text(header + "\n" + body + "\n", encoding="utf-8")


def dictionary_rows(tables: dict[str, "pd.DataFrame"]) -> "pd.DataFrame":
    """1 dong / cot DUY NHAT (hop nhat tren moi bang publish) voi day du thuoc tinh plan muc 9 + danh sach bang co cot do."""
    appears: dict[str, list[str]] = {}
    for name, table in tables.items():
        for column in table.columns:
            appears.setdefault(str(column), []).append(name)
    rows = []
    for column in sorted(appears):
        spec = dictionary.describe(column)
        if spec is None:
            raise KeyError(f"cot {column!r} (bang {appears[column]}) chua co dinh nghia trong dictionary.py")
        rows.append({"field": column, "tables": ", ".join(appears[column]), **{k: spec[k] for k in dictionary.FIELD_KEYS}})
    return pd.DataFrame(rows)


def write_dictionary(data: dict[str, Any], tables: dict[str, "pd.DataFrame"], analysis_dir: Path) -> None:
    snapshot: db.WarehouseSnapshot = data["snapshot"]
    rows = dictionary_rows(tables)
    lines = [
        f"# Data Dictionary (Wave A) - {snapshot.database} / {snapshot.batch_id}\n",
        f"{len(rows)} truong DUY NHAT trong {len(tables)} bang publish (`tables/*.csv` + `quality_findings.csv` + `dataset_readiness_by_horizon.csv`). Moi truong co: nguon, grain, "
        "kieu/don vi, timezone, allowed values, structural-missing rule, eligibility scope, dung lam feature hay chi audit, leakage caveat (plan muc 9). "
        "Mau so cua moi ty le ghi trong `TABLE_METADATA.csv` (cot `denominator`); scope/grain/metric ID cua tung bang cung o do.\n",
        "Quy uoc chung: gia = VND, 1 dem, 2 nguoi lon, khong sold-out; thoi gian luu UTC, bao cao theo Asia/Ho_Chi_Minh (+07:00 co dinh, khong DST); "
        "`is_sold_out` la sentinel demand, KHONG phai gia 0; moi ty le availability o grain item.\n",
    ]
    for row in rows.to_dict("records"):
        lines.append(f"### `{row['field']}`\n")
        lines.append(f"- **Dinh nghia:** {row['definition']}")
        lines.append(f"- **Xuat hien o:** {row['tables']}")
        lines.append(f"- **Nguon:** {row['source']} - **Grain:** {row['grain']}")
        lines.append(f"- **Kieu/don vi:** {row['type_unit']} - **Timezone:** {row['timezone']} - **Allowed values:** {row['allowed_values']}")
        lines.append(f"- **Structural missing:** {row['structural_missing']}")
        lines.append(f"- **Eligibility scope:** {row['eligibility_scope']}")
        lines.append(f"- **Su dung:** {row['usage']}")
        lines.append(f"- **Leakage caveat:** {row['leakage_caveat']}\n")
    (analysis_dir / "DATA_DICTIONARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
