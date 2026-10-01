"""Ownership resolve (muc 8) - pure, khong can MySQL/Excel.

Fixture dung DUNG hinh dang cua du lieu that da do duoc (discuss/warehouse-build-implementation/
01b): local so huu (2026-09-14, 2026-09-17) slot N1, VPS so huu (2026-09-14, 2026-09-27) slot V2,
va VPS THUC TE da cao ca 2026-09-17 - tuc chay nham bo slot cua may chinh.
"""
import datetime as dt

import pytest

from app.warehouse.errors import OwnershipConflictError
from app.warehouse.ownership_manifest import (
    FLAGS_FULL,
    FLAGS_RAW_ONLY,
    EligibilityFlags,
    OwnershipManifest,
    OwnershipRow,
    assert_no_conflict,
    compute_ownership_manifest_sha256,
    resolve_item_ownership,
    resolve_run_ownership,
    schedule_day_key,
    schedule_manifest_row_key,
)

D = dt.date
CRAWL = D(2026, 9, 14)

ROWS = [
    OwnershipRow("local_primary", CRAWL, "N1", D(2026, 9, 17)),
    OwnershipRow("local_primary", CRAWL, "N6", D(2026, 10, 4)),
    OwnershipRow("local_primary", D(2026, 9, 15), "N1", D(2026, 9, 19)),
    OwnershipRow("vps", CRAWL, "V2", D(2026, 9, 27)),
    OwnershipRow("vps", D(2026, 8, 31), "V3", D(2026, 10, 1)),
]
MANIFEST = OwnershipManifest.from_rows(ROWS)


def resolve(source, crawl, checkin, status="success", hotel_id="h1", in_cohort=True, run_flags=FLAGS_FULL):
    return resolve_item_ownership(
        manifest=MANIFEST, source_code=source, crawl_date=crawl, checkin_date=checkin,
        item_status=status, hotel_id=hotel_id, hotel_in_cohort=in_cohort, run_flags=run_flags,
    )


# --------------------------------------------------------------------------------------
# 6 nhanh phan loai (GPT file 02b)
# --------------------------------------------------------------------------------------
def test_owner_success():
    result = resolve("local_primary", CRAWL, D(2026, 9, 17))
    assert result.ownership_status == "owner_success"
    assert result.schedule_slot == "N1"
    assert result.flags == FLAGS_FULL
    assert result.exclusion_reason is None


@pytest.mark.parametrize("status", ["partial", "sold_out", "not_bookable", "error"])
def test_owner_failure_cho_moi_status_terminal_khac_success(status):
    result = resolve("local_primary", CRAWL, D(2026, 9, 17), status=status)
    assert result.ownership_status == "owner_failure"
    assert result.exclusion_reason == f"owner_failure_status_{status}"
    # Giu EDA main de muc 13 van dem duoc success/partial/sold_out/not_bookable/error,
    # nhung tat reference/training (GPT file 04 O2).
    assert result.flags == EligibilityFlags(True, True, False, False)


def test_non_owner_duplicate_dung_slot_cua_chu_so_huu_that():
    """VPS cao dung ngay 14/09 nhung o check-in 17/09 - slot N1 cua may chinh."""
    result = resolve("vps", CRAWL, D(2026, 9, 17))
    assert result.ownership_status == "non_owner_duplicate"
    assert result.schedule_slot == "N1"
    assert result.owner_source == "local_primary"
    assert result.flags == FLAGS_RAW_ONLY
    assert result.exclusion_reason == "non_owner_duplicate"


def test_protocol_deviation_khi_hotel_ngoai_cohort():
    result = resolve("local_primary", CRAWL, D(2026, 9, 17), hotel_id="mac-dalat", in_cohort=False)
    assert result.ownership_status == "protocol_deviation"
    assert result.schedule_slot == "N1"
    assert result.flags == FLAGS_RAW_ONLY
    assert result.exclusion_reason == "hotel_outside_cohort_manifest"


def test_hotel_id_null_khong_bi_coi_la_ngoai_cohort():
    """Item error thuong co hotel_id NULL (local 478 / vps 174 dong that) - phai la owner_failure."""
    result = resolve("local_primary", CRAWL, D(2026, 9, 17), status="error", hotel_id=None, in_cohort=False)
    assert result.ownership_status == "owner_failure"


def test_unassigned_off_plan_checkin_other_day():
    """VPS ngay 31/08 co ke hoach, nhung check-in 01/10 la cua ngay khac -> khong muon slot ngay khac."""
    result = resolve("vps", D(2026, 8, 31), D(2026, 10, 14))
    assert result.ownership_status == "unassigned"
    assert result.schedule_slot is None
    assert result.exclusion_reason == "off_plan_unknown"

    other_day = resolve("vps", D(2026, 8, 31), D(2026, 9, 27))
    assert other_day.ownership_status == "unassigned"
    assert other_day.exclusion_reason == "off_plan_checkin_other_day"


def test_unassigned_pre_protocol_pilot():
    """Ngay 08/08 nam TRUOC ngay dau tien co ke hoach -> pilot truoc protocol."""
    result = resolve("local_primary", D(2026, 8, 8), D(2026, 8, 20))
    assert result.ownership_status == "unassigned"
    assert result.exclusion_reason == "pre_protocol_pilot"


def test_ngay_sau_cua_so_ke_hoach_khong_bi_goi_nham_la_pilot():
    """GPT review 06 MINOR 1: truoc day MOI ngay khong co plan deu bi ghi `pre_protocol_pilot`,
    ke ca run chay SAU ngay cuoi workbook - lam sai audit du eligibility van dung."""
    result = resolve("local_primary", D(2027, 6, 1), D(2027, 6, 10))
    assert result.ownership_status == "unassigned"
    assert result.exclusion_reason == "post_protocol_window"


def test_ngay_nam_trong_cua_so_nhung_trong_lich():
    """13/09 nam giua 14/09 va 15/09 ve mat cua so nhung khong co dong nao trong fixture."""
    result = resolve("local_primary", D(2026, 9, 14) - dt.timedelta(days=0), D(2026, 9, 17))
    assert result.ownership_status == "owner_success"  # sanity: fixture van dung
    gap = resolve_item_ownership(
        manifest=MANIFEST, source_code="vps", crawl_date=D(2026, 9, 10),
        checkin_date=D(2026, 12, 25), item_status="success", hotel_id="h1",
        hotel_in_cohort=True, run_flags=FLAGS_FULL,
    )
    assert gap.exclusion_reason == "day_not_in_ownership_manifest"


# --------------------------------------------------------------------------------------
# Run-level + bat bien "item khong the cao hon run cha"
# --------------------------------------------------------------------------------------
def test_run_co_ke_hoach():
    run = resolve_run_ownership(manifest=MANIFEST, source_code="local_primary", crawl_date=CRAWL)
    assert run.has_plan is True
    assert run.planned_crawl_date == CRAWL
    assert run.flags == FLAGS_FULL
    assert run.exclusion_reason is None


def test_run_pilot_khong_co_ke_hoach():
    run = resolve_run_ownership(manifest=MANIFEST, source_code="local_primary", crawl_date=D(2026, 8, 8))
    assert run.has_plan is False
    assert run.planned_crawl_date is None
    # chk_run_map_schedule: khong co planned_crawl_date => 3 co phai FALSE.
    assert run.flags == FLAGS_RAW_ONLY
    assert run.exclusion_reason == "run_not_in_ownership_manifest"


def test_item_khong_the_co_eligibility_cao_hon_run_cha():
    result = resolve("local_primary", CRAWL, D(2026, 9, 17), run_flags=FLAGS_RAW_ONLY)
    assert result.ownership_status == "owner_success"
    assert result.flags == FLAGS_RAW_ONLY


# --------------------------------------------------------------------------------------
# Conflict + hash
# --------------------------------------------------------------------------------------
def test_conflicting_ownership_fail_cung():
    rows = list(ROWS) + [OwnershipRow("vps", CRAWL, "V9", D(2026, 9, 17))]
    with pytest.raises(OwnershipConflictError, match="Conflicting ownership"):
        assert_no_conflict(rows)


def test_cung_nguon_claim_2_slot_cho_1_cap_ngay_cung_la_conflict():
    rows = [
        OwnershipRow("vps", CRAWL, "V1", D(2026, 9, 27)),
        OwnershipRow("vps", CRAWL, "V2", D(2026, 9, 27)),
    ]
    with pytest.raises(OwnershipConflictError):
        assert_no_conflict(rows)


def test_manifest_hash_khong_phu_thuoc_thu_tu_dong():
    assert compute_ownership_manifest_sha256(ROWS) == compute_ownership_manifest_sha256(
        sorted(ROWS, key=lambda row: row.checkin_date, reverse=True)
    )


def test_manifest_hash_doi_khi_doi_slot():
    changed = [OwnershipRow("local_primary", CRAWL, "N2", D(2026, 9, 17))] + ROWS[1:]
    assert compute_ownership_manifest_sha256(changed) != compute_ownership_manifest_sha256(ROWS)


def test_row_key_va_day_key_khac_nhau_va_on_dinh():
    args = dict(
        ownership_manifest_sha256="m" * 64, owner_source="local_primary",
        crawl_date=CRAWL, cohort_manifest_sha256="c" * 64,
    )
    day = schedule_day_key(**args)
    row = schedule_manifest_row_key(schedule_slot="N1", checkin_date=D(2026, 9, 17), **args)
    assert day != row
    assert len(day) == 64 and len(row) == 64
    assert day == schedule_day_key(**args)


def test_row_key_doi_khi_doi_cohort_hash():
    base = dict(
        ownership_manifest_sha256="m" * 64, owner_source="local_primary", crawl_date=CRAWL,
        schedule_slot="N1", checkin_date=D(2026, 9, 17),
    )
    assert (
        schedule_manifest_row_key(cohort_manifest_sha256="c" * 64, **base)
        != schedule_manifest_row_key(cohort_manifest_sha256="d" * 64, **base)
    )
