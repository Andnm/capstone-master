"""Lich EXPECTED tu ownership manifest + cohort history THAT (GPT review 12 eda M5: protocol
continuity phai REPLAY tu chinh 2 file manifest nguon, khong suy tu ket qua da import).

Tai su dung loader da test cua backend (`app.warehouse.ownership_manifest`,
`app.warehouse.cohort_manifest`) qua `db._ensure_backend_importable()` - khong viet lai logic
parse/validate manifest o day.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Mapping

import pandas as pd

import db


def expected_schedule(
    ownership_manifest_path: str | Path, cohort_history_path: str | Path, *, base_dir: str | Path,
    protocol_complete_through_date_by_source: Mapping[str, dt.date],
) -> "pd.DataFrame":
    """1 dong / `(owner_source, crawl_date, schedule_slot, checkin_date, hotel_id)` DUOC LEN LICH -
    "duoc len lich" nghia la co 1 dong trong ownership manifest CHO DUNG (crawl_date, checkin_date) cua
    dung nguon do, VA hotel dang trong cohort co hieu luc tai `crawl_date` (theo cohort history, KHONG
    theo workbook hien tai - xem CLAUDE.md muc 2).

    GPT review 12 eda M1 (sua loi logic that): window moi nguon la `[planned_window(source).start,
    min(planned_window(source).end, protocol_complete_through_date_by_source[source])]`, lay TU CHINH
    ownership manifest - DOC LAP voi viec ngay do co run thuc te hay khong. Ban truoc dung tap "ngay
    THUC SU co run" de loc, nen 1 ngay ownership manifest noi "phai crawl" nhung KHONG CO RUN NAO CA se
    khong bao gio vao duoc `expected`, boi vay `missing_run` KHONG BAO GIO xuat hien - dung mat loai
    gap quan trong nhat cua chinh metric nay.

    `protocol_complete_through_date_by_source` (GPT review 12 eda file 11 muc 6.3: "khong goi chung la
    cutoff mo ho" - ten tham so phai noi ro GIA DINH, khong chi la mot ranh gioi ky thuat): ngay VN
    CUOI CUNG ma dump cua nguon do DUOC GIA DINH da ghi nhan day du moi run/item hoan tat truoc hoac
    trong ngay do (thuong la ngay VN cua `dump_taken_at`). Chan KHONG cho ngay "chua toi luc crawl"
    (tuong lai so voi dump) bi tinh nham thanh thieu.

    Tra ve DataFrame co the RONG (0 dong) neu khong co crawl_date nao khop - KHONG raise, de ben goi
    tu quyet dinh co coi la loi hay khong (vd batch rong that su).
    """
    db._ensure_backend_importable()
    from app.warehouse.cohort_manifest import load_cohort_history
    from app.warehouse.ownership_manifest import load_ownership_manifest

    manifest = load_ownership_manifest(ownership_manifest_path)
    cohort = load_cohort_history(cohort_history_path, base_dir=base_dir)

    rows: list[dict] = []
    for row in manifest.rows:
        window = manifest.planned_window(row.owner_source)
        if window is None:
            continue
        complete_through = protocol_complete_through_date_by_source.get(row.owner_source)
        window_end = min(window[1], complete_through) if complete_through is not None else window[1]
        if not (window[0] <= row.crawl_date <= window_end):
            continue
        for hotel_id in cohort.hotel_city:
            if not cohort.contains_at(hotel_id, row.crawl_date):
                continue
            rows.append({
                "owner_source": row.owner_source, "crawl_date": row.crawl_date,
                "schedule_slot": row.schedule_slot, "checkin_date": row.checkin_date, "hotel_id": hotel_id,
            })
    return pd.DataFrame(
        rows, columns=["owner_source", "crawl_date", "schedule_slot", "checkin_date", "hotel_id"]
    )


def classify_outcomes(
    expected: "pd.DataFrame", actual: "pd.DataFrame", *,
    source_run_dates: Mapping[str, set[dt.date]] | None = None,
) -> "pd.DataFrame":
    """LEFT JOIN `expected` (tu `expected_schedule`) voi `actual` (tu DB - item da duoc ownership
    resolve, hotel_id o day PHAI la `effective_hotel_id` da resolve - xem `resolve_effective_hotel_id`)
    theo `(owner_source, crawl_date, checkin_date, hotel_id)`. Tra ve `expected` + cot `outcome`, lay
    gia tri tu `actual` neu co dong khop.

    `actual`: 1 dong / item, cot bat buoc `source_code, crawl_date, checkin_date, hotel_id, outcome`.

    `source_run_dates` (GPT review 12 eda M1, tuy chon): `{owner_source: {crawl_date co IT NHAT 1 run
    THAT SU}}` - vd tu `queries.actual_crawl_dates_by_source()`. Neu truyen vao, dong khong khop duoc
    se TACH 2 loai thay vi gop chung `'missing_run'`:
      - `missing_source_run`: crawl_date do KHONG co run nao cua source (ca ngay khong chay);
      - `missing_item_in_existing_run`: crawl_date do CO it nhat 1 run cua source, nhung item/hotel
        nay khong nam trong do (vd bi bo sot, hoac that bai khong resolve duoc va bi loai o buoc
        `resolve_effective_hotel_id`).
    Neu KHONG truyen (None), giu nguyen nhan `'missing_run'` phang (tuong thich nguoc cho caller chua
    can phan biet 2 loai).
    """
    required_expected = ("owner_source", "crawl_date", "checkin_date", "hotel_id")
    required_actual = ("source_code", "crawl_date", "checkin_date", "hotel_id", "outcome")
    missing_e = [c for c in required_expected if c not in expected.columns]
    missing_a = [c for c in required_actual if c not in actual.columns]
    if missing_e:
        raise ValueError(f"expected thieu cot {missing_e}")
    if missing_a:
        raise ValueError(f"actual thieu cot {missing_a}")

    actual_renamed = actual.rename(columns={"source_code": "owner_source"})
    join_keys = ["owner_source", "crawl_date", "checkin_date", "hotel_id"]
    duplicated = actual_renamed.duplicated(subset=join_keys)
    if duplicated.any():
        raise ValueError(
            f"actual co {int(duplicated.sum())} dong trung khoa {join_keys} - du lieu bat thuong "
            f"(1 item phai resolve ownership DUNG 1 lan), kiem tra truoc khi merge de tranh nhan dong am tham."
        )

    merged = expected.merge(actual_renamed[[*join_keys, "outcome"]], on=join_keys, how="left")
    merged["outcome"] = merged["outcome"].fillna("missing_run")

    if source_run_dates is not None:
        run_rows = [
            {"owner_source": source, "crawl_date": crawl_date}
            for source, dates in source_run_dates.items() for crawl_date in dates
        ]
        run_dates_df = pd.DataFrame(run_rows, columns=["owner_source", "crawl_date"])
        run_dates_df["_has_run"] = True
        merged = merged.merge(run_dates_df, on=["owner_source", "crawl_date"], how="left")
        # `.fillna(False)` sau LEFT JOIN de lai dtype `object` (tron True/NaN), nen `~` se goi Python
        # bitwise-invert tren TUNG bool rieng le (~True=-2, ~False=-1, ca hai deu truthy) thay vi phu
        # dinh dung boolean - phai ep ve dtype bool THAT truoc khi dung `~` (da tu bat qua test that).
        merged["_has_run"] = merged["_has_run"].fillna(False).astype(bool)
        missing_mask = merged["outcome"] == "missing_run"
        merged.loc[missing_mask & merged["_has_run"], "outcome"] = "missing_item_in_existing_run"
        merged.loc[missing_mask & ~merged["_has_run"], "outcome"] = "missing_source_run"
        merged = merged.drop(columns=["_has_run"])
    return merged


def resolve_effective_hotel_id(
    actual: "pd.DataFrame", cohort_history_path: str | Path, *, base_dir: str | Path,
) -> "pd.DataFrame":
    """Item co `hotel_id=NULL` (GPT review 12 eda M2) van co the resolve duoc qua
    `extract_hotel_slug(source_hotel_link)` - da xac minh tren du lieu that: toan bo la item 'error'
    (dead link/CAPTCHA/network) xay ra SAU khi Booking da tra ve dung trang property (URL con nguyen
    slug) nhung TRUOC khi parser luu duoc `hotel_id`.

    `actual`: DataFrame tu `queries.protocol_continuity_actual` (cot bat buoc `hotel_id`,
    `source_hotel_link`, `crawl_date`).

    Tra ve `actual` + 3 cot moi:
      - `effective_hotel_id`: `hotel_id` goc neu co; neu khong, slug resolve tu `source_hotel_link`
        NEU slug do la 1 hotel DANG active trong cohort tai DUNG `crawl_date` (GPT: "assert mapping
        duy nhat va member thuoc cohort hieu luc" - dung `cohort.contains_at()`, KHONG chi trust slug
        parse duoc ma khong doi chieu, tranh resolve nham 1 chuoi rac tu URL khong phai property page);
        None neu khong resolve duoc.
      - `hotel_id_resolution`: `'original'` (hotel_id da co san) | `'resolved_from_link'` (resolve
        thanh cong tu source_hotel_link) | `'unattributed'` (khong resolve duoc - can giu rieng, xem
        `summarize_unattributed`).
      - `effective_city`: city lich su tu cohort manifest cua `effective_hotel_id`. Cot nay la nguon
        identity dung chung cho cac metric item-grain (active hotel, availability, collision), tranh
        tinh trang protocol resolve duoc hotel nhung cac bang khac van day item vao `(unknown)`.

    KHONG raise neu khong resolve duoc mot vai dong - do la ket qua binh thuong (link that su chet
    truoc khi Booking tra property page). Ben goi (`classify_outcomes`) chi nen join voi cac dong co
    `effective_hotel_id` non-null; loc dong `unattributed` truoc khi goi la trach nhiem cua caller.
    """
    db._ensure_backend_importable()
    from app.scraper.url_utils import extract_hotel_slug
    from app.warehouse.cohort_manifest import load_cohort_history

    required = ("hotel_id", "source_hotel_link", "crawl_date")
    missing = [c for c in required if c not in actual.columns]
    if missing:
        raise ValueError(f"actual thieu cot {missing}")

    cohort = load_cohort_history(cohort_history_path, base_dir=base_dir)

    result = actual.copy()
    result["effective_hotel_id"] = result["hotel_id"]
    result["hotel_id_resolution"] = "original"
    result.loc[result["hotel_id"].isna(), "hotel_id_resolution"] = "unattributed"

    # Chi item hotel_id=NULL moi can resolve - thieu so so voi tong item (vd 623/167.469 thuc te), nen
    # lap qua tung dong ('.at[]') re hon la 'apply(axis=1)' tren toan bo DataFrame.
    missing_idx = result.index[result["hotel_id"].isna()]
    for idx in missing_idx:
        link = result.at[idx, "source_hotel_link"]
        crawl_date = result.at[idx, "crawl_date"]
        slug = extract_hotel_slug(link)
        if slug and cohort.contains_at(slug, crawl_date):
            result.at[idx, "effective_hotel_id"] = slug
            result.at[idx, "hotel_id_resolution"] = "resolved_from_link"

    result["effective_city"] = result["effective_hotel_id"].map(cohort.hotel_city)

    return result


def summarize_unattributed(resolved_actual: "pd.DataFrame", *, sample_size: int = 10) -> "pd.DataFrame":
    """1 dong / `(source_code, crawl_date)` - item KHONG resolve duoc hotel_id sau
    `resolve_effective_hotel_id` (GPT review 12 eda M2 diem 4: "chi giu 'unattributed' cho phan that
    su khong map duoc", kem sample link/hash - MIN1: khong de `sample_keys` rong khi count>0).

    `resolved_actual`: dau ra cua `resolve_effective_hotel_id` (cot bat buoc `source_code, crawl_date,
    hotel_id_resolution, source_link_hash`). Tra ve DataFrame RONG (dung schema) neu khong co dong
    unattributed nao.
    """
    required = ("source_code", "crawl_date", "hotel_id_resolution", "source_link_hash")
    missing = [c for c in required if c not in resolved_actual.columns]
    if missing:
        raise ValueError(f"resolved_actual thieu cot {missing}")

    columns = ["source_code", "crawl_date", "n_unattributed_errors", "sample_keys"]
    unattributed = resolved_actual[resolved_actual["hotel_id_resolution"] == "unattributed"]
    if unattributed.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict] = []
    for (source_code, crawl_date), group in unattributed.groupby(["source_code", "crawl_date"]):
        samples = group["source_link_hash"].drop_duplicates().head(sample_size).tolist()
        rows.append({
            "source_code": source_code, "crawl_date": crawl_date,
            "n_unattributed_errors": len(group), "sample_keys": json.dumps(samples),
        })
    return pd.DataFrame(rows, columns=columns).sort_values(["source_code", "crawl_date"]).reset_index(drop=True)
