"""Bootstrap sys.path + gate cho test can MySQL that (EDA_CURATED_PLAN.md muc 10.1).

Quy uoc giong `WAREHOUSE_SMOKE=1` cua backend: test thuan (khong can MySQL) luon chay; test danh dau
`@pytest.mark.mysql` chi chay khi dat `EDA_SMOKE=1`, va tao/xoa DISPOSABLE database rieng - khong bao
gio dung/sua `warehouse_current.json` hay warehouse da promote.

Fixture warehouse DUNG CHUNG (scope=session, dung bang `build_fixture_warehouse` -> `build_warehouse()` that) de cac file integration khong
phai build lai (~7 giay/lan): `price_wh` (1 nguon, gia tri biet truoc - xem `fixture_specs.price_fixture_spec`) va `collision_wh`
(2 nguon co collision - `fixture_specs.collision_fixture_spec`). `price_db`/`collision_db` la ket noi READ-ONLY that qua `db.connect()`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MPLBACKEND", "Agg")  # test ve hinh khong can display

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# GPT review 12 eda M3: test end-to-end can `import run_wave_a` (o `eda/`, ngang hang `src/`, khong
# phai ben trong no) de goi dung ham runner that, khong goi tat qua `wave_a.*` truc tiep.
EDA_DIR = SRC_DIR.parent
if str(EDA_DIR) not in sys.path:
    sys.path.insert(0, str(EDA_DIR))


def pytest_collection_modifyitems(config, items):
    if os.environ.get("EDA_SMOKE") == "1":
        return
    skip_mysql = pytest.mark.skip(reason="can MySQL that - dat EDA_SMOKE=1 de chay")
    for item in items:
        if "mysql" in item.keywords:
            item.add_marker(skip_mysql)


@pytest.fixture(scope="session")
def price_wh(tmp_path_factory):
    import fixture_specs
    from warehouse_fixture import build_fixture_warehouse

    with build_fixture_warehouse(tmp_path_factory.mktemp("price_wh"), **fixture_specs.price_fixture_spec()) as fx:
        yield fx


@pytest.fixture(scope="session")
def collision_wh(tmp_path_factory):
    import fixture_specs
    from warehouse_fixture import build_fixture_warehouse

    with build_fixture_warehouse(tmp_path_factory.mktemp("collision_wh"), **fixture_specs.collision_fixture_spec()) as fx:
        yield fx


@pytest.fixture(scope="session")
def price_db(price_wh):
    import db

    with db.connect(pointer_path=price_wh["pointer_path"]) as (conn, snapshot):
        yield conn, snapshot, price_wh


@pytest.fixture(scope="session")
def collision_db(collision_wh):
    import db

    with db.connect(pointer_path=collision_wh["pointer_path"]) as (conn, snapshot):
        yield conn, snapshot, collision_wh


@pytest.fixture(scope="session")
def turnover_wh(tmp_path_factory):
    import fixture_specs
    from warehouse_fixture import build_fixture_warehouse

    with build_fixture_warehouse(tmp_path_factory.mktemp("turnover_wh"), **fixture_specs.turnover_fixture_spec()) as fx:
        yield fx


@pytest.fixture(scope="session")
def turnover_db(turnover_wh):
    import db

    with db.connect(pointer_path=turnover_wh["pointer_path"]) as (conn, snapshot):
        yield conn, snapshot, turnover_wh
