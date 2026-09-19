"""Test `report.py` (thuan, khong MySQL): dinh dang, dictionary generator, doi chieu bang lich su 7.10 va cong 'full Wave A' theo coverage matrix."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import coverage_matrix
import dictionary
import report


def _tables_7_10(legacy_rates: dict[str, float]) -> dict[str, pd.DataFrame]:
    legacy = pd.DataFrame({
        "lead_time_bucket": list(legacy_rates), "n_observations": [1000] * len(legacy_rates),
        "n_series_has_reference": [round(v * 1000) for v in legacy_rates.values()], "series_reference_rate": list(legacy_rates.values()),
    })
    exact = pd.DataFrame({"lead_time_bucket": ["0", "1-3"], "n_observations": [100, 100], "n_matched": [3, 4], "match_rate": [0.03, 0.04]})
    return {
        "reference_series_exists_coverage_raw_legacy_bucket": legacy,
        "reference_approval_by_city_month": pd.DataFrame({"city": ["A"], "checkin_month": ["2026-09"], "approved": [2], "proposed": [8], "n": [10],
                                                         "approval_rate": [0.2]}),
        "reference_status_evidence_summary": pd.DataFrame({"status": ["approved"], "n_references": [2]}),
        "reference_uniqueness_per_series": pd.DataFrame({"n_approved": [0, 1], "n_series": [8, 2]}),
        "reference_exact_key_coverage_main": exact, "reference_exact_key_coverage_raw": exact,
        "reference_item_level_availability_by_lead_time": pd.DataFrame({"lead_time_bucket": ["0"], "n_items": [10], "n_items_matched": [3],
                                                                         "availability_rate": [0.3]}),
    }


def test_report_7_10_so_sanh_bang_lich_su_va_danh_dau_lech_qua_0_5pp():
    """Ty le do duoc sat bang lich su CLAUDE.md (30,4/27,1/...) -> khong lech; lech >0,5pp -> bi danh dau. Kem caveat bat buoc cua plan 7.10."""
    close = dict(report.HISTORICAL_SERIES_EXISTS_COVERAGE)                  # trung khit
    text_ok = report._s710({"tables": _tables_7_10(close)})
    assert "0/6 bucket lech > 0,5 diem phan tram" in text_ok and "within_0_5pp" in text_ok
    skewed = {**close, "4-7": 0.10, "61+": 0.30}                            # 2 bucket lech xa
    text_bad = report._s710({"tables": _tables_7_10(skewed)})
    assert "2/6 bucket lech > 0,5 diem phan tram" in text_bad
    assert "delta_vs_historical" in text_bad and "0.304" in text_bad.replace(",", ".") or "30" in text_bad
    # caveat bat buoc: khong phai causal coverage, khong loc con 2.457 approved roi coi la toan quan the, 2 metric khong cung dinh nghia
    for phrase in ("KHONG phai causal train coverage", "toan quan the", "KHONG cung dinh nghia", "Ba trang thai `unavailable/alias/ambiguous`"):
        assert phrase in text_ok, phrase
    assert "series co > 1 reference approved = 0" in text_ok  # tinh duy nhat: ky vong 0


def test_historical_series_exists_coverage_khop_dung_bang_claude_md():
    assert report.HISTORICAL_SERIES_EXISTS_COVERAGE == {"0-3": 0.304, "4-7": 0.271, "8-14": 0.183, "15-30": 0.112, "31-60": 0.076, "61+": 0.071}


def test_format_cell_khong_dung_ky_hieu_khoa_hoc_cho_gia_tien():
    assert report._format_cell(500000.0) == "500,000" and report._format_cell(np.int64(1234567)) == "1,234,567"
    assert report._format_cell(0.123456789) == "0.1235" and report._format_cell(float("nan")) == "" and report._format_cell(None) == ""
    assert "e+" not in report._format_cell(9_000_000.0).lower()


def test_md_table_rong_va_cat_bot_dong_thua():
    assert report._md_table(pd.DataFrame()) == "_(khong co dong nao)_" and report._md_table(None) == "_(khong co dong nao)_"
    table = report._md_table(pd.DataFrame({"a": range(20), "b": range(20)}), max_rows=3)
    assert "hien 3/20 dong" in table and table.count("\n| ") == 3 + 1  # header + 3 dong (sep khong bat dau '| ---' truoc newline)
    assert "a | b" in report._md_table(pd.DataFrame({"a": [1], "b": [2], "c": [3]}), columns=["a", "b"])


def test_dictionary_rows_hop_nhat_cot_qua_cac_bang_va_du_thuoc_tinh():
    tables = {"t1": pd.DataFrame({"n_items": [1], "city": ["A"]}), "t2": pd.DataFrame({"n_items": [2], "success_rate": [0.5]})}
    rows = report.dictionary_rows(tables).set_index("field")
    assert set(rows.index) == {"n_items", "city", "success_rate"}
    assert rows.loc["n_items", "tables"] == "t1, t2"
    for field in dictionary.FIELD_KEYS:
        assert rows[field].astype(str).str.strip().ne("").all(), field


def test_dictionary_rows_raise_khi_cot_chua_co_dinh_nghia():
    with pytest.raises(KeyError, match="chua co dinh nghia"):
        report.dictionary_rows({"t": pd.DataFrame({"cot_khong_ton_tai_xyz": [1]})})


def test_write_dictionary_liet_ke_moi_truong_voi_du_10_muc(tmp_path):
    class Snap:
        database, batch_id = "warehouse_x", "b1"

    report.write_dictionary({"snapshot": Snap()}, {"t": pd.DataFrame({"n_items": [1], "price_per_night": [1.0]})}, tmp_path)
    text = (tmp_path / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    assert "### `price_per_night`" in text and "### `n_items`" in text
    for label in ("Dinh nghia", "Xuat hien o", "Nguon", "Grain", "Kieu/don vi", "Timezone", "Allowed values", "Structural missing", "Eligibility scope",
                  "Su dung", "Leakage caveat"):
        assert label in text, label


def test_write_report_ghi_partial_neu_matrix_con_dong_thieu(tmp_path):
    """Chi duoc goi 'full Wave A' khi matrix khong con muc required nao thieu (GPT file 11 muc 6.5)."""
    matrix = coverage_matrix.coverage_matrix_dataframe()
    ok = report._s_coverage({"matrix": matrix})
    assert "FULL WAVE A" in ok and "PARTIAL" not in ok
    broken = matrix.copy()
    broken.loc[0, "status"] = "missing"
    broken.loc[1, "test_ids"] = ""
    bad = report._s_coverage({"matrix": broken})
    assert "PARTIAL" in bad and "2/" in bad and "FULL WAVE A" not in bad
