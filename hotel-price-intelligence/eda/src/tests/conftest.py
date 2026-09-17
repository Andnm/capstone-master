"""Bootstrap sys.path + gate cho test can MySQL that (EDA_CURATED_PLAN.md muc 10.1).

Quy uoc giong `WAREHOUSE_SMOKE=1` cua backend: test thuan (khong can MySQL) luon chay; test danh dau
`@pytest.mark.mysql` chi chay khi dat `EDA_SMOKE=1`, va tao/xoa DISPOSABLE database rieng - khong bao
gio dung/sua `warehouse_current.json` hay warehouse da promote.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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
