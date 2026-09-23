"""Test registry `null_taxonomy.py` (thuan, khong MySQL) - file 17 M1: taxonomy phai phu DUNG tap field that cua missingness, co rationale, khong lop la."""
from __future__ import annotations

import pandas as pd
import pytest

import null_taxonomy as nt
import queries


def test_registry_phu_dung_missingness_field_groups_that():
    """Registry va `queries._MISSINGNESS_FIELD_GROUPS` PHAI cung tap field va cung field_group (them field o mot ben ma quen ben kia -> test do)."""
    nt.validate_against_field_groups(queries._MISSINGNESS_FIELD_GROUPS)
    assert set(nt.FIELD_RULES) == {f for fields in queries._MISSINGNESS_FIELD_GROUPS.values() for f in fields}
    assert len(nt.FIELD_RULES) == 16


def test_moi_field_co_rationale_khong_rong_va_source_override_dung_lop_hop_le():
    for rule in nt.FIELD_RULES.values():
        assert rule.rationale.strip() and rule.field_group and rule.null_class in nt.NULL_CLASSES
        assert all(cls in nt.NULL_CLASSES for cls in rule.source_overrides.values())
    assert nt.FIELD_RULES["git_commit"].source_overrides == {"vps": "source_metadata_expected_gap"}
    overrides = [r.field for r in nt.FIELD_RULES.values() if r.source_overrides]
    assert overrides == ["git_commit"]   # ngoai le theo nguon duy nhat da biet - them ngoai le moi phai di qua review


def test_canonical_key_role_khop_payload_that_cua_backend():
    """room_identity_key = (ten phong, suc chua, giuong, dien tich); rate_plan_key = (breakfast, free_cancellation, cancellation_policy) - doi chieu payload backend."""
    import db

    db._ensure_backend_importable()
    from app.scraper.reference import rate_plan_key, room_identity_payload

    room_fields = {"room_type_raw", "max_occupancy", "bed_config", "room_area"}
    rate_fields = {"breakfast_included", "free_cancellation", "cancellation_policy"}
    assert {f for f, r in nt.FIELD_RULES.items() if r.canonical_key_role == nt.ROOM_IDENTITY_KEY} == room_fields
    assert {f for f, r in nt.FIELD_RULES.items() if r.canonical_key_role == nt.RATE_PLAN_KEY} == rate_fields
    # doi 1 field trong nhom rate_plan -> key doi; doi field NGOAI nhom (taxes_fees, price_includes_tax) -> key KHONG doi (khop nhan `none`)
    base = {"breakfast_included": True, "free_cancellation": False, "cancellation_policy": "Khong hoan tien"}
    assert rate_plan_key(base) != rate_plan_key({**base, "free_cancellation": None})
    assert rate_plan_key(base) == rate_plan_key({**base, "taxes_fees": 5.0, "price_includes_tax": True})
    assert set(room_identity_payload({"room_type_raw": "A", "max_occupancy": 2, "bed_config": "1 giuong", "room_area": "20 m2"})) == {"name", "occupancy", "bed", "area"}


def test_registry_dataframe_1_dong_moi_field_va_ghi_ngoai_le():
    frame = nt.registry_dataframe()
    assert len(frame) == 16 and frame["field"].is_unique
    assert list(frame.columns) == ["field", "field_group", "null_class", "canonical_key_role", "source_overrides", "rationale"]
    git = frame[frame["field"] == "git_commit"].iloc[0]
    assert git["source_overrides"] == "vps=source_metadata_expected_gap"
    assert set(frame.loc[frame["field"] != "git_commit", "source_overrides"]) == {"none"}


def test_validate_bat_lop_khong_hop_le_va_field_khac_group():
    groups = {g: list(fields) for g, fields in queries._MISSINGNESS_FIELD_GROUPS.items()}
    moved = {**groups, "price": [*groups["price"], "bed_config"], "room_identity": [f for f in groups["room_identity"] if f != "bed_config"]}
    with pytest.raises(ValueError, match="khac group"):
        nt.validate_against_field_groups(moved)
