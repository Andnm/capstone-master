"""Taxonomy loi cua warehouse build - tat ca deu FAIL CLOSED.

Khong co exception nao trong module nay mang y nghia "canh bao roi di tiep". Bat ky cai nao raise
ra deu phai dung build va khong duoc tu sua/tu doan (WAREHOUSE_EDA_ML_SPEC.md muc 3a/16).
"""
from __future__ import annotations


class WarehouseError(Exception):
    """Base cho moi loi warehouse build."""


class ManifestError(WarehouseError):
    """Source/cohort/ownership manifest thieu field, sai kieu, sai checksum hoac mau thuan."""


class OwnershipConflictError(ManifestError):
    """Hai nguon cung claim 1 (crawl_date, checkin_date) - muc 8 yeu cau FAIL cung luc sinh manifest."""


class NamingError(WarehouseError):
    """Ten database/staging khong qua whitelist ^[A-Za-z0-9_]+$ hoac khong dung prefix bat buoc."""


class SqlScriptError(WarehouseError):
    """setup.sql/dump chua statement bi cam (CREATE DATABASE/DROP DATABASE/USE) hoac khong parse duoc."""


class SchemaMismatchError(WarehouseError):
    """Schema nguon lech so voi setup.sql o muc do KHONG cosmetic (muc 5)."""


class PreflightError(WarehouseError):
    """Kiem tra truoc import that bai (vd gia khong nguyen -> observation_fingerprint se raise)."""


class CoreImportError(WarehouseError):
    """Loi trong buoc import/remap 4 bang core.

    Ten co hau to `Core` de khong che builtin `ImportError` khi ai do `from ... import *`.
    """


class ValidationError(WarehouseError):
    """Row-count reconcile / orphan / duplicate / integrity gate that bai (muc 7/17)."""


class BatchStateError(WarehouseError):
    """Warehouse database khong o trang thai mong doi (core khong rong, batch da ton tai...)."""
