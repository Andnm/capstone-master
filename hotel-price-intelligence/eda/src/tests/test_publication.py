"""Test registry `publication.py` + `dictionary.py` + `save_figure` (thuan, khong MySQL) - GPT review 12 eda file 11 muc 5-6: metadata (metric ID/scope/grain/
denominator) va data dictionary phai phu DUNG cac artifact that; khong artifact/metric/cot 'mo coi'."""
from __future__ import annotations

import pytest

import dictionary
import publication
import queries
import wave_a

SECTIONS = {f"7.{i}" for i in range(1, 13)} | {"wave_b"}


def test_moi_bang_publish_co_du_metadata_khong_rong():
    for name, spec in publication.PUBLISHED_TABLES.items():
        assert spec.name == name
        for field in ("metric_id", "section", "scope", "grain", "denominator"):
            assert getattr(spec, field).strip(), f"{name}.{field} rong"
        assert spec.section in SECTIONS, f"{name}: section {spec.section!r} la"


def test_metric_id_cua_bang_publish_la_catalog_hoac_derived():
    for name, spec in publication.PUBLISHED_TABLES.items():
        if spec.metric_id.startswith("derived:"):
            assert len(spec.metric_id) > len("derived:"), name
        else:
            assert spec.metric_id in queries.CATALOG, f"{name}: metric_id {spec.metric_id!r} khong co trong CATALOG"
            assert spec.metric_id == name, f"{name}: bang SQL nguyen ban phai mang dung ten catalog metric"


def test_moi_catalog_metric_duoc_thu_thap_va_khong_co_metric_mo_coi():
    assert set(queries.CATALOG) == set(wave_a.COLLECTED_METRIC_IDS), (
        f"catalog {sorted(set(queries.CATALOG) ^ set(wave_a.COLLECTED_METRIC_IDS))} khong khop danh sach collect_wave_a_data")
    assert publication.INTERMEDIATE_METRICS <= set(queries.CATALOG)
    referenced = {mid for mid in queries.CATALOG if any(mid in spec.metric_id for spec in publication.PUBLISHED_TABLES.values())}
    orphans = set(queries.CATALOG) - referenced - publication.INTERMEDIATE_METRICS
    assert not orphans, f"metric chay nhung khong ra artifact nao va khong khai bao trung gian: {sorted(orphans)}"


def test_item_availability_ghi_dung_nguon_effective_khong_phai_sql_hotel_id_tho():
    """Acceptance review (file 15): 6 bang `item_availability_*` duoc tinh tu item frame da resolve EFFECTIVE hotel/city - `TABLE_METADATA.csv` khong duoc ghi
    nguon la cac SQL `item_status_counts_*_main` (hotel_id tho, chi la diagnostic)."""
    names = [n for n in publication.PUBLISHED_TABLES if n.startswith("item_availability_")]
    assert len(names) == 6
    for name in names:
        metric_id = publication.PUBLISHED_TABLES[name].metric_id
        assert metric_id.startswith("derived:wave_a._effective_availability_tables"), name
        assert "item_status_counts_" not in metric_id, name
    sql_diagnostics = {m for m in queries.CATALOG if m.startswith("item_status_counts_")}
    assert sql_diagnostics <= publication.INTERMEDIATE_METRICS


def test_assert_effective_availability_reconciles_sql_bat_duoc_lech():
    """Guard doi soat: item frame effective phai mo ta cung tap item MAIN voi SQL `include_eda_main` (tong + tung status)."""
    import pandas as pd

    base = {"n_success": 5, "n_sold_out": 2, "n_not_bookable": 1, "n_partial": 0, "n_error": 1, "n_items": 9}
    tables = {"item_availability_overall": pd.DataFrame([base])}
    wave_a._assert_effective_availability_reconciles_sql({"m": {"item_status_counts_overall_main": pd.DataFrame([base])}}, tables)  # khop -> im lang
    for column, value in (("n_error", 0), ("n_items", 8), ("n_success", 6)):
        bad = pd.DataFrame([{**base, column: value}])
        with pytest.raises(RuntimeError, match="LECH SQL include_eda_main") as info:
            wave_a._assert_effective_availability_reconciles_sql({"m": {"item_status_counts_overall_main": bad}}, tables)
        assert column in str(info.value)


def test_bang_goc_analysis_dir_chi_gom_quality_findings_va_readiness():
    roots = {name for name, spec in publication.PUBLISHED_TABLES.items() if spec.root_level}
    assert roots == {"quality_findings", "dataset_readiness_by_horizon"}
    assert wave_a._ROOT_LEVEL_TABLES == frozenset(roots)
    assert publication.table_artifact_path("quality_findings") == "quality_findings.csv"
    assert publication.table_artifact_path("preflight_core_counts") == "tables/preflight_core_counts.csv"


def test_moi_hinh_tham_chieu_bang_co_that_va_khong_trung_ten():
    assert len(publication.PUBLISHED_FIGURES) == len({f.name for f in publication.PUBLISHED_FIGURES.values()})
    for name, spec in publication.PUBLISHED_FIGURES.items():
        assert spec.section in SECTIONS and spec.description.strip()
        assert spec.tables and all(t in publication.PUBLISHED_TABLES for t in spec.tables), f"{name}: bang nguon khong ton tai"
        assert publication.figure_artifact_path(name) == f"figures/{name}.png"


def test_known_artifact_paths_khong_trung_lap():
    paths = [publication.table_artifact_path(n) for n in publication.PUBLISHED_TABLES] + [
        publication.figure_artifact_path(n) for n in publication.PUBLISHED_FIGURES] + sorted(publication.STATIC_ARTIFACTS)
    assert len(paths) == len(set(paths)) == len(publication.known_artifact_paths())


def test_figure_artifact_path_tu_choi_ten_la():
    with pytest.raises(KeyError, match="PUBLISHED_FIGURES"):
        publication.figure_artifact_path("hinh_la_khong_khai_bao")


def test_save_figure_chi_luu_hinh_khai_bao_va_ghi_png(tmp_path):
    import matplotlib.pyplot as plt

    (tmp_path / "figures").mkdir()
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    path = wave_a.save_figure(fig, tmp_path / "figures", "price_histogram_main")
    assert path == tmp_path / "figures" / "price_histogram_main.png" and path.stat().st_size > 500
    with pytest.raises(KeyError):
        wave_a.save_figure(fig, tmp_path / "figures", "hinh_la_khong_khai_bao")
    plt.close(fig)


# ---------------------------------------------------------------- dictionary
def test_moi_truong_explicit_co_du_10_thuoc_tinh_khong_rong():
    for column, spec in dictionary.explicit_fields().items():
        assert set(spec) == set(dictionary.FIELD_KEYS), column
        assert all(isinstance(spec[k], str) and spec[k].strip() for k in dictionary.FIELD_KEYS), column


@pytest.mark.parametrize("column", ["n_success", "n_option_pairs", "success_rate", "exact_price_match_rate", "p95", "p1", "is_price_outlier",
                                    "breakfast_included_concordance_rate", "duration_minutes_z", "n_series_with_reappearance"])
def test_cot_dang_chuan_duoc_pattern_phu_va_du_thuoc_tinh(column):
    spec = dictionary.describe(column)
    assert spec is not None and set(spec) == set(dictionary.FIELD_KEYS)
    assert all(spec[k].strip() for k in dictionary.FIELD_KEYS)


@pytest.mark.parametrize("column", ["breakfast_included_concordance_rate", "free_cancellation_concordance_rate", "cancellation_policy_concordance_rate"])
def test_concordance_structural_duoc_ghi_ro_trong_dictionary(column):
    """GPT file 13 M5: 3 thuoc tinh nam TRONG canonical_rate_key -> concordance cua shared option-pair la structural; pattern `(.+)_rate` chung KHONG duoc dien giai
    chung thanh 'n_<field>_concordance / mau so' (khong co cot do)."""
    definition = dictionary.describe(column)["definition"]
    assert "STRUCTURAL" in definition and "canonical_rate_key" in definition and "n_option_pairs" in definition


@pytest.mark.parametrize("column", ["price_includes_tax_concordance_rate", "taxes_fees_concordance_rate", "currency_concordance_rate"])
def test_concordance_doc_lap_hoac_hang_so_duoc_mo_ta_dung(column):
    definition = dictionary.describe(column)["definition"]
    assert "n_option_pairs" in definition and "STRUCTURAL" not in definition
    assert ("doc lap" in definition) or ("both-null" in definition) or ("100%" in definition)


def test_cot_khong_co_dinh_nghia_tra_none():
    assert dictionary.describe("cot_khong_ton_tai_xyz") is None


def test_moi_cot_output_schema_cua_catalog_deu_co_dinh_nghia():
    """Cac cot SQL nguyen ban (output_schema) phai duoc dictionary phu ngay o muc thuan (khong can MySQL). Cot cua bang derived duoc kiem o
    test dry-run (write_dictionary raise KeyError neu thieu)."""
    missing = {}
    for metric_id, metric in queries.CATALOG.items():
        for column in metric.output_schema:
            if dictionary.describe(column) is None:
                missing.setdefault(column, []).append(metric_id)
    assert not missing, f"cot chua co dinh nghia: {missing}"


def test_dictionary_khong_bao_gio_tra_dinh_nghia_rong_cho_denominator_columns():
    for column in ("n_items", "n_obs", "n_total", "n_scheduled", "denominator", "count"):
        spec = dictionary.describe(column)
        assert spec is not None and ("mau so" in spec["definition"].lower() or "so " in spec["definition"].lower())
