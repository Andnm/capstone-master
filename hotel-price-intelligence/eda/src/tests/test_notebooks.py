"""Test tren SOURCE notebook (EDA_CURATED_PLAN.md muc 4, muc 10.1: "notebook source luon output-free").

Parse truc tiep file .ipynb nhu JSON (khong can nbformat/jupyter de chay test nay), khong thuc thi
notebook - chi kiem cau truc + cu phap tung code cell.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

NOTEBOOKS_DIR = Path(__file__).resolve().parents[2] / "notebooks"
NOTEBOOK_PATHS = sorted(NOTEBOOKS_DIR.glob("*.ipynb"))


@pytest.mark.skipif(not NOTEBOOK_PATHS, reason="chua co notebook nao trong eda/notebooks/")
@pytest.mark.parametrize("path", NOTEBOOK_PATHS, ids=lambda p: p.name)
def test_notebook_source_output_free(path: Path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    code_cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    assert code_cells, f"{path.name}: khong co code cell nao - kiem tra lai file"
    for index, cell in enumerate(code_cells):
        assert cell.get("execution_count") is None, (
            f"{path.name} code cell #{index}: execution_count != None - notebook nay DA duoc chay va "
            f"luu lai, vi pham 'source notebook luon output-free'. Chay lai qua nbclient tren ban COPY, "
            f"khong luu de len file source."
        )
        assert cell.get("outputs") == [], (
            f"{path.name} code cell #{index}: co outputs - vi pham 'source notebook luon output-free'."
        )


@pytest.mark.skipif(not NOTEBOOK_PATHS, reason="chua co notebook nao trong eda/notebooks/")
@pytest.mark.parametrize("path", NOTEBOOK_PATHS, ids=lambda p: p.name)
def test_notebook_code_cells_hop_le_cu_phap(path: Path):
    """Bat loi go nham TRUOC khi GPT/nguoi review phai tu chay notebook moi phat hien."""
    notebook = json.loads(path.read_text(encoding="utf-8"))
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        source = "".join(cell["source"])
        try:
            compile(source, f"<{path.name} cell {index}>", "exec")
        except SyntaxError as exc:  # noqa: PERF203 - test can thong bao dung cell loi
            pytest.fail(f"{path.name} cell #{index}: SyntaxError: {exc}")


def test_co_dung_2_notebook_theo_ten_da_chot():
    names = {p.name for p in NOTEBOOK_PATHS}
    assert "01_warehouse_full_history_eda.ipynb" in names
    assert "02_curated_ml_eda.ipynb" in names
