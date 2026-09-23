"""Enforcement cho coverage matrix (GPT review 12 eda file 11 muc 5: "test phai fail neu mot bullet bat buoc khong co mapping/artifact/test").

Khong tin matrix tu khai bao: parse CHINH `EDA_CURATED_PLAN.md` de lay danh sach bullet muc 7.1-7.12 roi doi chieu 2 chieu; moi metric_id/artifact/test_id phai
resolve toi thuc the that (catalog / registry publish / ham that / ham test that qua AST). Cac ham resolve co test am rieng chung minh chung THAT SU tu choi mapping sai.
"""
from __future__ import annotations

import ast
import re
import unicodedata
from pathlib import Path

import pandas as pd
import pytest

import coverage_matrix
import dictionary  # noqa: F401  (import de chac chan module import duoc cung matrix)
import db
import holidays
import metrics
import null_taxonomy
import protocol_schedule
import publication
import queries
import report
import wave_a

TESTS_DIR = Path(__file__).resolve().parent
PLAN_PATH = Path(__file__).resolve().parents[3] / "EDA_CURATED_PLAN.md"
FUNCTION_MODULES = {
    "metrics": metrics, "queries": queries, "wave_a": wave_a, "db": db, "holidays": holidays, "protocol_schedule": protocol_schedule,
    "publication": publication, "report": report, "null_taxonomy": null_taxonomy,
}


# ---------------------------------------------------------------- helpers (co test am ben duoi)
def normalize(text: str) -> str:
    """Bo dau tieng Viet + markdown + danh so dau dong + dau cau cuoi, hoa thuong - de so bullet plan (co dau) voi matrix bat ke cach danh may."""
    text = re.sub(r"^\s*(?:-\s+|\d+\.\s+)", "", text.strip())
    text = re.sub(r"^para:\s*", "", text)
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[*`“”\"']", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()[:40]


def plan_bullets(path: Path = PLAN_PATH) -> dict[str, list[str]]:
    """{section: [dong dau cua moi bullet top-level `- ` hoac `N. `]} cho `### 7.N.` (bo qua code fence)."""
    result: dict[str, list[str]] = {}
    section, in_code = None, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        header = re.match(r"^### (7\.\d+)\.", line)
        if header:
            section = header.group(1)
            result[section] = []
            continue
        if line.startswith("## "):
            section = None
        if section and (line.startswith("- ") or re.match(r"^\d+\. ", line)):
            result[section].append(line)
    return result


def index_test_functions() -> dict[str, set[str]]:
    """{file test: {ten ham test}} bang AST - khong import (tranh chay fixture/skip)."""
    index: dict[str, set[str]] = {}
    for path in TESTS_DIR.glob("test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        index[path.name] = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")}
    return index


def resolve_metric_id(metric_id: str) -> bool:
    if metric_id in queries.CATALOG or metric_id in publication.PUBLISHED_TABLES:
        return True
    if "." in metric_id:
        module_name, _, attr = metric_id.partition(".")
        module = FUNCTION_MODULES.get(module_name)
        return module is not None and hasattr(module, attr)
    return False


def resolve_test_id(test_id: str, index: dict[str, set[str]]) -> bool:
    file_name, sep, function = test_id.partition("::")
    return bool(sep) and function in index.get(file_name, set())


def resolve_artifact(artifact: str) -> bool:
    return artifact in publication.known_artifact_paths()


# ---------------------------------------------------------------- enforcement tren matrix that
def test_moi_bullet_cua_plan_co_dung_1_dong_trong_matrix_va_khong_dong_nao_ngoai_plan():
    """Parse THANG plan: moi bullet 7.x phai co dung 1 dong (khop 40 ky tu dau sau chuan hoa); dong 'para:' la yeu cau van xuoi (khong tinh bullet)."""
    plan = plan_bullets()
    assert set(plan) == {f"7.{i}" for i in range(1, 13)}, "plan khong con du 12 section 7.x - parser can cap nhat"
    assert sum(len(v) for v in plan.values()) >= 80, "parse plan ra qua it bullet - kiem tra parser"
    rows = coverage_matrix.coverage_matrix_rows()
    for section, bullets in plan.items():
        matrix_keys = [normalize(r["plan_bullet"]) for r in rows if r["plan_section"] == section and not r["plan_bullet"].startswith("para:")]
        plan_keys = [normalize(b) for b in bullets]
        assert sorted(plan_keys) == sorted(matrix_keys), (
            f"{section}: bullet plan khong khop matrix - thieu trong matrix: {sorted(set(plan_keys) - set(matrix_keys))}; "
            f"thua ngoai plan: {sorted(set(matrix_keys) - set(plan_keys))}")


def test_moi_dong_co_du_metric_artifact_test_va_implemented():
    df = coverage_matrix.coverage_matrix_dataframe()
    assert list(df.columns) == list(coverage_matrix.COLUMNS)
    assert coverage_matrix.missing_required_rows(df).empty
    for row in coverage_matrix.coverage_matrix_rows():
        for field in ("metric_ids", "artifacts", "test_ids"):
            assert row[field], f"{row['bullet_id']} thieu {field}"
        for field in ("grain", "scope", "denominator", "plan_bullet"):
            assert str(row[field]).strip(), f"{row['bullet_id']} thieu {field}"
        assert row["status"] == "implemented", row["bullet_id"]
    assert df["bullet_id"].is_unique


def test_moi_metric_id_resolve_duoc_toi_catalog_bang_publish_hoac_ham_that():
    bad = [(r["bullet_id"], m) for r in coverage_matrix.coverage_matrix_rows() for m in r["metric_ids"] if not resolve_metric_id(m)]
    assert not bad, f"metric_id khong resolve duoc: {bad}"


def test_moi_artifact_thuoc_danh_sach_publish_da_khai_bao():
    bad = [(r["bullet_id"], a) for r in coverage_matrix.coverage_matrix_rows() for a in r["artifacts"] if not resolve_artifact(a)]
    assert not bad, f"artifact khong nam trong publication.known_artifact_paths(): {bad}"


def test_moi_test_id_tro_toi_ham_test_that():
    index = index_test_functions()
    bad = [(r["bullet_id"], t) for r in coverage_matrix.coverage_matrix_rows() for t in r["test_ids"] if not resolve_test_id(t, index)]
    assert not bad, f"test_id khong tro toi ham test that: {bad}"


def test_moi_bang_va_hinh_publish_duoc_it_nhat_1_dong_tham_chieu():
    referenced = {a for r in coverage_matrix.coverage_matrix_rows() for a in r["artifacts"]}
    tables = {publication.table_artifact_path(n) for n in publication.PUBLISHED_TABLES}
    figures = {publication.figure_artifact_path(n) for n in publication.PUBLISHED_FIGURES}
    # bang cua Wave B guard + bang reconciliation/metadata la dieu kien/khai bao, khong thuoc bullet nao cua 7.1-7.12
    exempt = {publication.table_artifact_path("wave_b_dataset_version_readiness")}
    assert not (tables - referenced - exempt), f"bang publish khong bullet nao tham chieu: {sorted(tables - referenced - exempt)}"
    assert not (figures - referenced), f"hinh publish khong bullet nao tham chieu: {sorted(figures - referenced)}"


def test_coverage_matrix_du_12_section_va_bullet_id_duy_nhat():
    df = coverage_matrix.coverage_matrix_dataframe()
    assert set(df["plan_section"]) == {f"7.{i}" for i in range(1, 13)}
    assert df["bullet_id"].is_unique and not df.duplicated(subset=["plan_section", "plan_bullet"]).any()


def test_write_coverage_matrix_md_ghi_du_cot_va_trang_thai(tmp_path):
    df = coverage_matrix.coverage_matrix_dataframe()
    path = tmp_path / "EDA_COVERAGE_MATRIX.md"
    coverage_matrix.write_coverage_matrix_md(df, path)
    text = path.read_text(encoding="utf-8")
    assert "full Wave A" in text and f"{len(df)}/{len(df)}" in text
    for column in coverage_matrix.COLUMNS:
        assert column in text


# ---------------------------------------------------------------- test AM: chung minh enforcement THAT SU tu choi mapping sai
def test_missing_required_rows_bat_duoc_dong_chua_implemented_hoac_thieu_mapping():
    base = {"plan_section": "7.1", "bullet_id": "7.1.1", "plan_bullet": "x", "metric_ids": "m", "artifacts": "a", "grain": "g", "scope": "s",
            "denominator": "d", "test_ids": "t", "status": "implemented"}
    df = pd.DataFrame([base, {**base, "status": "missing"}, {**base, "test_ids": ""}, {**base, "artifacts": "  "}, {**base, "metric_ids": ""}])
    assert len(coverage_matrix.missing_required_rows(df)) == 4


def test_resolve_metric_id_tu_choi_id_gia_va_module_la():
    assert resolve_metric_id("price_distribution_overall_main") and resolve_metric_id("metrics.finish_hour_distribution")
    assert resolve_metric_id("collision_item_summary")  # ten bang publish
    for bad in ("khong_ton_tai", "metrics.khong_co_ham_nay", "module_la.ham", "derived:x"):
        assert not resolve_metric_id(bad), bad


def test_resolve_test_id_tu_choi_test_khong_ton_tai():
    index = index_test_functions()
    assert resolve_test_id("test_metrics.py::test_finish_hour_distribution", index)
    for bad in ("test_metrics.py::test_khong_ton_tai", "test_khong_co_file.py::test_x", "test_metrics.py", "::test_finish_hour_distribution"):
        assert not resolve_test_id(bad, index), bad


def test_resolve_artifact_tu_choi_artifact_la():
    assert resolve_artifact("tables/preflight_core_counts.csv") and resolve_artifact("EDA_COVERAGE_MATRIX.md") and resolve_artifact("quality_findings.csv")
    assert not resolve_artifact("tables/khong_co.csv") and not resolve_artifact("figures/khong_co.png")


def test_parser_plan_bat_duoc_bullet_moi_them_vao_plan(tmp_path):
    """Neu them 1 bullet vao plan ma khong them vao matrix, enforcement phai thay lech (mo phong bang ban sao plan)."""
    fake = tmp_path / "plan.md"
    fake.write_text(PLAN_PATH.read_text(encoding="utf-8").replace(
        "- Load pointer và xác minh đúng DB/batch PASS.", "- Load pointer và xác minh đúng DB/batch PASS.\n- Bullet moi chua co trong matrix."), encoding="utf-8")
    original, modified = plan_bullets(PLAN_PATH)["7.1"], plan_bullets(fake)["7.1"]
    assert len(modified) == len(original) + 1
    rows = coverage_matrix.coverage_matrix_rows()
    matrix_keys = [normalize(r["plan_bullet"]) for r in rows if r["plan_section"] == "7.1"]
    assert normalize("- Bullet moi chua co trong matrix.") not in matrix_keys


def test_normalize_bo_dau_markdown_va_danh_so():
    assert normalize("- **Exact approved-key observation coverage:** numerator") == normalize("**Exact approved-key observation coverage:** numerator")
    assert normalize("1. **Item/protocol continuity:** lịch expected") == "item/protocol continuity: lich expected"
    assert normalize("para: Mọi tỷ lệ phải có") == normalize("Mọi tỷ lệ phải có")
