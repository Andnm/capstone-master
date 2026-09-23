"""Taxonomy NULL cua tung field tren observation KHONG sold-out (file 17 M1): registry TUONG MINH thay cho mot finding gop "unexpected NULL".

Ba lop (NULL nghia khac nhau - khong duoc cong vao mot tong):
  * `required_contract`            - NULL la VI PHAM hop dong du lieu that (parser/scraper phai luon dien). Moi field co 1 finding rieng (mau so rieng).
  * `optional_listing`             - Booking co the KHONG cong bo (vd `taxes_fees`, `bed_config`, `room_area`): NULL mo ta listing, khong phai loi.
  * `source_metadata_expected_gap` - thieu THEO NGUON da biet (vd `git_commit` cua VPS: Docker khong co .git): ngoai le co khai bao theo (field, source).

`canonical_key_role` la truc TRUC GIAO: field co nam trong `room_identity_key` / `rate_plan_key` (`app.scraper.reference`) hay khong - NULL o field
trong key doi ngu nghia canonical key (json null != true/false, room khong co bed...) nen luon duoc ghi ben canh lop.

Khong gian lan de qua `quality_findings`: BANG missingness (mo ta toan bo NULL) giu nguyen; registry nay chi GAN NHAN va tach finding.
Module THUAN (khong DB) de test khoa taxonomy/denominator (`tests/test_null_taxonomy.py`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd

REQUIRED_CONTRACT = "required_contract"
OPTIONAL_LISTING = "optional_listing"
SOURCE_METADATA_EXPECTED_GAP = "source_metadata_expected_gap"
NULL_CLASSES: tuple[str, ...] = (REQUIRED_CONTRACT, OPTIONAL_LISTING, SOURCE_METADATA_EXPECTED_GAP)
# Chi dung o bang KHONG co cot nguon (vd missingness_by_item_status_sold_out): field co `source_overrides` khong the gan 1 lop duy nhat.
SOURCE_DEPENDENT = "source_dependent"

ROOM_IDENTITY_KEY = "room_identity_key"
RATE_PLAN_KEY = "rate_plan_key"
NO_KEY = "none"


@dataclass(frozen=True)
class FieldRule:
    field: str
    field_group: str
    null_class: str
    canonical_key_role: str
    rationale: str
    source_overrides: Mapping[str, str] = field(default_factory=dict)

    def class_for(self, source_code: str | None) -> str:
        """Lop cua field cho `source_code` (override theo nguon neu co, con khong la lop mac dinh)."""
        if source_code is not None and source_code in self.source_overrides:
            return self.source_overrides[source_code]
        return self.null_class


_RULES: tuple[FieldRule, ...] = (
    # ---- room_identity (payload phong; sentinel sold-out khong co)
    FieldRule("room_type_raw", "room_identity", REQUIRED_CONTRACT, ROOM_IDENTITY_KEY,
              "Ten phong nguyen goc: bat buoc - canonical_room_key can ten phong (observation khong co room identity bi canonicalize tu choi)."),
    FieldRule("max_occupancy", "room_identity", OPTIONAL_LISTING, ROOM_IDENTITY_KEY,
              "Booking khong luon cong bo suc chua trong bang phong; NULL = khong cong bo (van nam trong room_identity_key)."),
    FieldRule("bed_config", "room_identity", OPTIONAL_LISTING, ROOM_IDENTITY_KEY,
              "Booking khong luon liet ke giuong (nhat la can ho/villa); NULL = khong cong bo (van nam trong room_identity_key)."),
    FieldRule("room_area", "room_identity", OPTIONAL_LISTING, ROOM_IDENTITY_KEY,
              "Dien tich chi hien khi listing cong bo (CLAUDE.md muc 4.3: string/null); van nam trong room_identity_key."),
    # ---- rate_plan
    FieldRule("breakfast_included", "rate_plan", REQUIRED_CONTRACT, RATE_PLAN_KEY,
              "Bool bat buoc, nam trong rate_plan_key (CLAUDE.md muc 4.3)."),
    FieldRule("free_cancellation", "rate_plan", REQUIRED_CONTRACT, RATE_PLAN_KEY,
              "Bool bat buoc, nam trong rate_plan_key: NULL tao canonical_rate_key rieng (json null != true/false) nen chia/gop nham rate plan."),
    FieldRule("cancellation_policy", "rate_plan", REQUIRED_CONTRACT, RATE_PLAN_KEY,
              "Van ban chinh sach huy bat buoc, nam trong rate_plan_key (canonical_text)."),
    FieldRule("price_includes_tax", "rate_plan", OPTIONAL_LISTING, NO_KEY,
              "Bool/null (CLAUDE.md muc 4.3): NULL khi nguon khong noi ro gia da gom thue hay chua."),
    # ---- price
    FieldRule("price_per_night", "price", REQUIRED_CONTRACT, NO_KEY,
              "Target chinh: observation available luon co gia > 0 (quality check price_non_positive)."),
    FieldRule("taxes_fees", "price", OPTIONAL_LISTING, NO_KEY,
              "Chi co so tien khi trang tach rieng thue/phi; NULL khi nguon chi noi 'da bao gom' (CLAUDE.md muc 4.3)."),
    # ---- hotel_attributes
    FieldRule("review_score", "hotel_attributes", OPTIONAL_LISTING, NO_KEY,
              "Thuoc tinh hotel: hotel moi/chua co danh gia thi Booking khong hien diem; NULL hop le."),
    FieldRule("review_count", "hotel_attributes", OPTIONAL_LISTING, NO_KEY,
              "Thuoc tinh hotel: hotel chua co danh gia thi khong co so luot; NULL hop le."),
    FieldRule("address", "hotel_attributes", REQUIRED_CONTRACT, NO_KEY,
              "Field chinh xac dinh vi tri hotel (CLAUDE.md muc 4.3): bat buoc."),
    # ---- artifact_source_metadata (crawl_runs)
    FieldRule("scraper_version", "artifact_source_metadata", REQUIRED_CONTRACT, NO_KEY,
              "Metadata run bat buoc (provenance scraper); NULL chi o run tao truoc migration ghi version."),
    FieldRule("selector_version", "artifact_source_metadata", REQUIRED_CONTRACT, NO_KEY,
              "Metadata run bat buoc (provenance selector); NULL chi o run tao truoc migration ghi version."),
    FieldRule("git_commit", "artifact_source_metadata", REQUIRED_CONTRACT, NO_KEY,
              "Metadata run bat buoc, TRU ngoai le da biet: nguon `vps` chay Docker khong co .git nen luon NULL (source_overrides).",
              source_overrides={"vps": SOURCE_METADATA_EXPECTED_GAP}),
)

FIELD_RULES: dict[str, FieldRule] = {rule.field: rule for rule in _RULES}


def classify(field_name: str, source_code: str | None = None) -> str:
    """Lop NULL cua `field_name` (theo nguon neu co override). Field chua khai bao -> KeyError (khong doan)."""
    return FIELD_RULES[field_name].class_for(source_code)


def class_without_source(field_name: str) -> str:
    """Lop cho bang khong co cot nguon: field co source_overrides -> `source_dependent` (khong gan mot lop duy nhat sai)."""
    rule = FIELD_RULES[field_name]
    return SOURCE_DEPENDENT if rule.source_overrides else rule.null_class


def required_fields() -> list[str]:
    """Field `required_contract` MAC DINH (moi field mot finding rieng); thu tu khai bao."""
    return [rule.field for rule in _RULES if rule.null_class == REQUIRED_CONTRACT]


def validate_against_field_groups(field_groups: Mapping[str, Iterable[str]]) -> None:
    """Registry PHAI phu dung tap field cua `queries._MISSINGNESS_FIELD_GROUPS` (khong thieu, khong thua, dung group) - test/collect goi de bat lech."""
    expected = {f: group for group, fields in field_groups.items() for f in fields}
    declared = {rule.field: rule.field_group for rule in _RULES}
    if expected != declared:
        raise ValueError(
            f"null_taxonomy lech missingness field groups: thieu {sorted(set(expected) - set(declared))}, thua {sorted(set(declared) - set(expected))}, "
            f"khac group {sorted(f for f in set(expected) & set(declared) if expected[f] != declared[f])}")
    bad = [r.field for r in _RULES if r.null_class not in NULL_CLASSES or any(c not in NULL_CLASSES for c in r.source_overrides.values())]
    if bad:
        raise ValueError(f"field co lop khong hop le: {bad}")


def registry_dataframe() -> "pd.DataFrame":
    """Bang registry cong bo (1 dong / field) - `null_taxonomy_registry.csv`."""
    return pd.DataFrame([{
        "field": r.field, "field_group": r.field_group, "null_class": r.null_class, "canonical_key_role": r.canonical_key_role,
        "source_overrides": "; ".join(f"{s}={c}" for s, c in sorted(r.source_overrides.items())) or "none", "rationale": r.rationale,
    } for r in _RULES])
