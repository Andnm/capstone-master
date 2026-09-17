"""Runner Wave A (GPT review 12 eda B1/M1/M2/M3) - CHAY TU BAT KY CWD NAO:

    python run_wave_a.py
    python run_wave_a.py --notebook 01_warehouse_full_history_eda.ipynb --timeout 1800
    python run_wave_a.py --ownership-manifest ... --cohort-history ... --report ...  # rebuild batch moi

Repo-root/src-dir duoc suy tu CHINH `__file__` cua file nay (khong doan `Path.cwd()` trong notebook -
day la "entrypoint/runner co repo-root resolver dua tren __file__ cua module that" GPT yeu cau o B1).
Kernel notebook nhan duong dan qua BIEN MOI TRUONG (dat TRUOC khi mo kernel - `nbclient`/`jupyter_client`
ke thua `os.environ` cua tien trinh cha, da tu kiem chung): `EDA_SRC_DIR`/`EDA_ANALYSIS_DIR` la BAT
BUOC; `EDA_POINTER_PATH`/`EDA_OWNERSHIP_MANIFEST_PATH`/`EDA_COHORT_HISTORY_PATH`/
`EDA_COHORT_HISTORY_BASE_DIR`/`EDA_VN_HOLIDAYS_CSV`/`EDA_WAREHOUSE_VALIDATION_REPORT_PATH`/
`EDA_SOURCE_MANIFEST_PATH` la TUY CHON - notebook chi doc qua neu co, con khong thi tu dung default
cua `wave_a.py` (khong doi hanh vi production hien tai).

GPT review 12 eda M3 (refactor thanh ham testable): truoc day `main()` khong nhan tham so nao, moi
duong dan (pointer/ownership/cohort/report...) hardcode nam RIENG trong `wave_a.py` va notebook TU
GOI VOI DEFAULT - khong co cach nao chay runner tren fixture disposable de kiem tra wiring runner <->
env <-> notebook <-> fixture that su hoat dong (test cu goi thang `wave_a.*`, bo qua ca nbclient lan
notebook that). `run_wave_a()` gio nhan DU tham so GPT yeu cau (pointer, ownership_manifest,
cohort_history (+base_dir), vn_holidays, validation_report, source_manifest, outputs_dir, kernel/
timeout) va chuyen tiep chung cho notebook qua bien moi truong o tren - test co the tro toan bo vao
fixture roi chay THAT nbclient tren CHINH Notebook 01 source (xem `src/tests/test_wave_a_dry_run.py`).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

EDA_DIR = Path(__file__).resolve().parent
SRC_DIR = EDA_DIR / "src"
NOTEBOOKS_DIR = EDA_DIR / "notebooks"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import nbformat  # noqa: E402
from nbclient import NotebookClient  # noqa: E402

import artifacts  # noqa: E402
import db  # noqa: E402

# Tuy chon - notebook chi doc qua os.environ.get(...) neu co gia tri (xem cell dau
# notebooks/01_warehouse_full_history_eda.ipynb).
_OPTIONAL_ENV_PARAMS: tuple[str, ...] = (
    "EDA_POINTER_PATH", "EDA_OWNERSHIP_MANIFEST_PATH", "EDA_COHORT_HISTORY_PATH",
    "EDA_COHORT_HISTORY_BASE_DIR", "EDA_VN_HOLIDAYS_CSV", "EDA_WAREHOUSE_VALIDATION_REPORT_PATH",
    "EDA_SOURCE_MANIFEST_PATH",
)


def run_wave_a(
    *, pointer_path: Path | None = None, ownership_manifest_path: Path | None = None,
    cohort_history_path: Path | None = None, cohort_history_base_dir: Path | None = None,
    vn_holidays_csv: Path | None = None, warehouse_validation_report_path: Path | None = None,
    source_manifest_path: Path | None = None,
    outputs_dir: Path | None = None, notebook_name: str = "01_warehouse_full_history_eda.ipynb",
    kernel: str = "eda_venv", timeout: int = 1800,
) -> Path:
    """Chay Notebook Wave A qua nbclient, quan ly artifact lifecycle day du. Tra ve `analysis_dir`.

    Moi tham so ngoai `notebook_name`/`kernel`/`timeout`/`outputs_dir` la TUY CHON: `None` nghia la
    "de `wave_a.py`/`db.py` tu dung default cua no" (production binh thuong: khong truyen gi ca, giu
    nguyen hanh vi hien tai). Test truyen fixture path de chay THAT tren du lieu disposable.

    Vong doi artifact (GPT review 12 M2): tao analysis directory FAIL-IF-EXISTS -> execute BAN COPY
    notebook qua `nbclient`, CWD pin cung vao `eda/notebooks/` -> ghi executed notebook ->
    `artifact_manifest.json` CHI duoc ghi SAU CUNG, khi khong co loi nao. Loi bat ky ->
    `artifacts.mark_failed()` roi RE-RAISE (khong nuot loi, khong de lai artifact "trong nhu PASS").
    """
    source_notebook = NOTEBOOKS_DIR / notebook_name
    if not source_notebook.exists():
        raise FileNotFoundError(f"khong tim thay {source_notebook}")

    pointer = db.load_pointer(pointer_path or db.DEFAULT_POINTER_PATH)
    analysis_id = artifacts.analysis_id(pointer["batch_id"])
    new_dir_kwargs = {"outputs_dir": outputs_dir} if outputs_dir is not None else {}
    analysis_dir = artifacts.new_analysis_dir(analysis_id, **new_dir_kwargs)  # FAIL-IF-EXISTS
    print(f"analysis_dir: {analysis_dir}")

    overrides = {
        "EDA_POINTER_PATH": pointer_path, "EDA_OWNERSHIP_MANIFEST_PATH": ownership_manifest_path,
        "EDA_COHORT_HISTORY_PATH": cohort_history_path, "EDA_COHORT_HISTORY_BASE_DIR": cohort_history_base_dir,
        "EDA_VN_HOLIDAYS_CSV": vn_holidays_csv, "EDA_WAREHOUSE_VALIDATION_REPORT_PATH": warehouse_validation_report_path,
        "EDA_SOURCE_MANIFEST_PATH": source_manifest_path,
    }
    saved_env = {name: os.environ.get(name) for name in ("EDA_SRC_DIR", "EDA_ANALYSIS_DIR", *_OPTIONAL_ENV_PARAMS)}
    try:
        os.environ["EDA_SRC_DIR"] = str(SRC_DIR)
        os.environ["EDA_ANALYSIS_DIR"] = str(analysis_dir)
        for env_name in _OPTIONAL_ENV_PARAMS:
            value = overrides[env_name]
            if value is not None:
                os.environ[env_name] = str(value)
            else:
                os.environ.pop(env_name, None)

        notebook = nbformat.read(source_notebook, as_version=4)
        client = NotebookClient(
            notebook, timeout=timeout, kernel_name=kernel,
            resources={"metadata": {"path": str(NOTEBOOKS_DIR)}},
        )
        client.execute()

        executed_path = analysis_dir / "executed_notebooks" / notebook_name
        nbformat.write(notebook, executed_path)

        # Sau khi notebook da chay xong (report/dictionary/summary/quality/tables da duoc chinh
        # notebook ghi ben trong), manifest la buoc CUOI CUNG - GPT review 12 eda M2.
        manifest_path = artifacts.write_artifact_manifest(analysis_dir)
        print(f"artifact_manifest.json: {manifest_path}")
    except Exception as exc:
        artifacts.mark_failed(analysis_dir, error=f"{type(exc).__name__}: {exc}")
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"Da danh dau {analysis_dir / 'FAILED.json'} - artifact khong trong nhu PASS.", file=sys.stderr)
        raise
    finally:
        # Khoi phuc dung gia tri CU (khong chi xoa) - runner co the duoc goi nhieu lan trong cung 1
        # tien trinh (test), khong duoc de env leak sang lan chay ke tiep.
        for name, prev_value in saved_env.items():
            if prev_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prev_value

    return analysis_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Chay Notebook Wave A, quan ly artifact lifecycle day du.")
    parser.add_argument("--notebook", default="01_warehouse_full_history_eda.ipynb")
    parser.add_argument("--kernel", default="eda_venv",
                        help="ten kernelspec da dang ky (xem README.md: `ipykernel install --name eda_venv`)")
    parser.add_argument("--timeout", type=int, default=1800, help="giay toi da cho notebook chay")
    parser.add_argument("--pointer", type=Path, default=None, help="override warehouse_current.json")
    parser.add_argument("--ownership-manifest", type=Path, default=None)
    parser.add_argument("--cohort-history", type=Path, default=None)
    parser.add_argument("--cohort-history-base-dir", type=Path, default=None)
    parser.add_argument("--vn-holidays-csv", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None, help="override warehouse validation report json")
    parser.add_argument("--source-manifest", type=Path, default=None, help="override source_manifest.json")
    args = parser.parse_args()

    try:
        analysis_dir = run_wave_a(
            pointer_path=args.pointer, ownership_manifest_path=args.ownership_manifest,
            cohort_history_path=args.cohort_history, cohort_history_base_dir=args.cohort_history_base_dir,
            vn_holidays_csv=args.vn_holidays_csv, warehouse_validation_report_path=args.report,
            source_manifest_path=args.source_manifest,
            notebook_name=args.notebook, kernel=args.kernel, timeout=args.timeout,
        )
    except Exception:
        return 1

    print(f"Wave A hoan tat: {analysis_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
