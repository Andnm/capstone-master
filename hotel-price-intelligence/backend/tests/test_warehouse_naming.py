"""Whitelist ten database + guard DROP staging - pure.

Ten database duoc noi THANG vao DDL (`CREATE DATABASE x`) vi MySQL khong nhan placeholder cho
identifier. Do do whitelist o day la ranh gioi an toan duy nhat.
"""
import pytest

from app.warehouse.errors import NamingError
from app.warehouse.naming import (
    require_droppable_staging,
    require_identifier,
    require_warehouse_database,
    staging_database_name,
)


@pytest.mark.parametrize("name", ["warehouse_1", "a", "A_9", "wh_staging_b1_vps"])
def test_ten_hop_le(name):
    assert require_identifier(name) == name


@pytest.mark.parametrize("name", [
    "", "co dau cach", "ten-gach", "ten`backtick", "ten'nhay", "ten;drop", "ten.db", "x" * 65,
])
def test_ten_khong_hop_le_bi_tu_choi(name):
    with pytest.raises(NamingError):
        require_identifier(name)


@pytest.mark.parametrize("name", ["mysql", "MySQL", "information_schema", "sys"])
def test_database_he_thong_bi_tu_choi(name):
    with pytest.raises(NamingError, match="he thong"):
        require_identifier(name)


def test_warehouse_phai_co_prefix():
    assert require_warehouse_database("warehouse_x") == "warehouse_x"
    with pytest.raises(NamingError, match="warehouse_"):
        require_warehouse_database("hotel_price_intel")


def test_staging_name_deterministic():
    assert staging_database_name("b1", "vps") == "wh_staging_b1_vps"


def test_staging_name_qua_dai_thi_fail():
    with pytest.raises(NamingError, match="dai"):
        staging_database_name("b" * 50, "local_primary")


def test_drop_chi_cho_phep_staging_da_tao():
    allowed = {"wh_staging_b1_vps"}
    assert require_droppable_staging("wh_staging_b1_vps", allowed=allowed) == "wh_staging_b1_vps"


def test_drop_tu_choi_database_khong_co_prefix():
    with pytest.raises(NamingError, match="prefix"):
        require_droppable_staging("hotel_price_intel", allowed={"hotel_price_intel"})


def test_drop_tu_choi_staging_cua_batch_khac():
    with pytest.raises(NamingError, match="danh sach staging"):
        require_droppable_staging("wh_staging_b2_vps", allowed={"wh_staging_b1_vps"})
