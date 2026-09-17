"""Test enforcement cho coverage matrix (GPT review 12 eda file 11 muc 5: "test phai fail neu mot
bullet bat buoc khong co mapping/artifact/test")."""
from __future__ import annotations

import pandas as pd
import pytest

from coverage_matrix import _COLUMNS, coverage_matrix_dataframe, missing_required_rows, write_coverage_matrix_md


def test_coverage_matrix_khong_con_bullet_nao_thieu():
    df = coverage_matrix_dataframe()
    missing = missing_required_rows(df)
    assert missing.empty, f"con {len(missing)} bullet chua implemented:\n{missing.to_string()}"


def test_coverage_matrix_du_cot_bat_buoc():
    df = coverage_matrix_dataframe()
    assert list(df.columns) == list(_COLUMNS)
    for col in _COLUMNS:
        assert df[col].notna().all(), f"cot {col!r} co gia tri rong"
        assert (df[col].astype(str).str.strip() != "").all(), f"cot {col!r} co chuoi rong"


def test_coverage_matrix_phu_du_12_section():
    df = coverage_matrix_dataframe()
    sections = set(df["plan_section"])
    expected_sections = {f"7.{i}" for i in range(1, 13)}
    missing_sections = expected_sections - sections
    assert not missing_sections, f"thieu section {missing_sections} trong coverage matrix"


def test_coverage_matrix_khong_trung_bullet():
    df = coverage_matrix_dataframe()
    duplicated = df.duplicated(subset=["plan_section", "bullet"])
    assert not duplicated.any(), f"trung bullet: {df.loc[duplicated, ['plan_section', 'bullet']].to_dict('records')}"


def test_missing_required_rows_bat_duoc_dong_chua_implemented():
    df = pd.DataFrame({
        "plan_section": ["7.1"], "bullet": ["x"], "metric_id": ["m"], "artifact": ["a"], "grain": ["g"],
        "scope": ["s"], "denominator": ["d"], "test_id": ["t"], "status": ["missing"],
    })
    out = missing_required_rows(df)
    assert len(out) == 1


def test_write_coverage_matrix_md(tmp_path):
    df = coverage_matrix_dataframe()
    path = tmp_path / "EDA_COVERAGE_MATRIX.md"
    write_coverage_matrix_md(df, path)
    text = path.read_text(encoding="utf-8")
    assert "DAY DU" in text
    assert "7.1" in text and "7.12" in text
    for col in _COLUMNS:
        assert col in text
