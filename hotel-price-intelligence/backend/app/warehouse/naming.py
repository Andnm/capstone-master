"""Whitelist ten database + guard staging (WAREHOUSE_EDA_ML_SPEC.md muc 3a buoc 5/12).

Ly do ton tai: ten database duoc noi thang vao DDL (`CREATE DATABASE x` / `DROP DATABASE x` khong
the tham so hoa bang placeholder). Moi ten di vao SQL PHAI qua `require_identifier()` truoc.

Buoc 12 doi "validate prefix + ten tuyet doi truoc khi drop" - `require_droppable_staging()` lam
dung viec do: chi cho drop database co prefix staging cua DUNG batch dang chay, va phai nam trong
danh sach staging ma chinh tien trinh nay da tao.
"""
from __future__ import annotations

import re

from .errors import NamingError

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
MAX_IDENTIFIER_LEN = 64  # gioi han ten database cua MySQL

WAREHOUSE_DB_PREFIX = "warehouse_"
STAGING_DB_PREFIX = "wh_staging_"

# Nhung ten khong bao gio duoc dung lam warehouse/staging du co qua regex.
RESERVED_DATABASES = frozenset({
    "mysql", "information_schema", "performance_schema", "sys",
})


def require_identifier(name: str, *, what: str = "database") -> str:
    """Tra lai chinh `name` neu hop le; nguoc lai raise. KHONG tu sua ten."""
    if not isinstance(name, str) or not name:
        raise NamingError(f"{what} name rong hoac khong phai chuoi: {name!r}")
    if len(name) > MAX_IDENTIFIER_LEN:
        raise NamingError(f"{what} name dai {len(name)} ky tu, vuot gioi han {MAX_IDENTIFIER_LEN}: {name!r}")
    if not IDENTIFIER_RE.match(name):
        raise NamingError(
            f"{what} name {name!r} khong qua whitelist ^[A-Za-z0-9_]+$ (muc 3a buoc 5) - "
            f"khong chap nhan dau cach, dau nhay, backtick hay ky tu dac biet."
        )
    if name.lower() in RESERVED_DATABASES:
        raise NamingError(f"{what} name {name!r} la database he thong cua MySQL, tu choi.")
    return name


def require_warehouse_database(name: str) -> str:
    """Warehouse DB phai co prefix rieng de khong bao gio nham voi DB van hanh (hotel_price_intel)."""
    require_identifier(name, what="warehouse database")
    if not name.startswith(WAREHOUSE_DB_PREFIX):
        raise NamingError(
            f"warehouse database {name!r} phai bat dau bang {WAREHOUSE_DB_PREFIX!r} - guard nay chan "
            f"viec lo tay tro lenh warehouse vao DB van hanh."
        )
    return name


def staging_database_name(batch_id: str, source_code: str) -> str:
    """Ten staging deterministic theo (batch, source) - de guard drop biet chinh xac ten hop le."""
    require_identifier(batch_id, what="batch_id")
    require_identifier(source_code, what="source_code")
    name = f"{STAGING_DB_PREFIX}{batch_id}_{source_code}"
    if len(name) > MAX_IDENTIFIER_LEN:
        raise NamingError(
            f"ten staging sinh ra dai {len(name)} ky tu (> {MAX_IDENTIFIER_LEN}): {name!r} - "
            f"rut ngan batch_id hoac source_code."
        )
    return require_identifier(name, what="staging database")


def require_droppable_staging(name: str, *, allowed: set[str]) -> str:
    """Guard buoc 12: chi drop dung staging cua batch nay, da nam trong tap `allowed` da tao.

    Hai lop kiem tra co y trung nhau (prefix + membership) de mot lop hong khong du de xoa nham DB.
    """
    require_identifier(name, what="staging database")
    if not name.startswith(STAGING_DB_PREFIX):
        raise NamingError(
            f"tu choi DROP {name!r}: khong co prefix staging {STAGING_DB_PREFIX!r}. "
            f"DROP DATABASE chi duoc phep tren staging do chinh build nay tao."
        )
    if name not in allowed:
        raise NamingError(
            f"tu choi DROP {name!r}: khong nam trong danh sach staging ma build nay da tao "
            f"({sorted(allowed)}). Co the ban dang chay nham batch."
        )
    return name
