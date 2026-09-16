"""Runner Wave A (GPT review 12 eda B1/M1/M2) - CHAY TU BAT KY CWD NAO:

    python run_wave_a.py
    python run_wave_a.py --notebook 01_warehouse_full_history_eda.ipynb --timeout 1800

Repo-root/src-dir duoc suy tu CHINH `__file__` cua file nay (khong doan `Path.cwd()` trong notebook -
day la "entrypoint/runner co repo-root resolver dua tren __file__ cua module that" GPT yeu cau o B1).
Kernel notebook nhan duong dan qua BIEN MOI TRUONG `EDA_SRC_DIR`/`EDA_ANALYSIS_DIR` (dat TRUOC khi mo
kernel - `nbclient`/`jupyter_client` ke thua `os.environ` cua tien trinh cha, da tu kiem chung).

Vong doi artifact (M2): tao analysis directory FAIL-IF-EXISTS -> execute BAN COPY notebook qua
`nbclient`, CWD pin cung vao `eda/notebooks/` -> ghi executed notebook -> `artifact_manifest.json`
CHI duoc ghi SAU CUNG, khi khong co loi nao. Loi bat ky -> `artifacts.mark_failed()` roi RE-RAISE
(khong nuot loi, khong de lai artifact "trong nhu PASS").
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Chay Notebook Wave A, quan ly artifact lifecycle day du.")
    parser.add_argument("--notebook", default="01_warehouse_full_history_eda.ipynb")
    parser.add_argument("--kernel", default="eda_venv",
                        help="ten kernelspec da dang ky (xem README.md: `ipykernel install --name eda_venv`)")
    parser.add_argument("--timeout", type=int, default=1800, help="giay toi da cho notebook chay")
    args = parser.parse_args()

    source_notebook = NOTEBOOKS_DIR / args.notebook
    if not source_notebook.exists():
        print(f"FAIL: khong tim thay {source_notebook}", file=sys.stderr)
        return 1

    pointer = db.load_pointer()
    analysis_id = artifacts.analysis_id(pointer["batch_id"])
    analysis_dir = artifacts.new_analysis_dir(analysis_id)  # FAIL-IF-EXISTS - khong ghi de analysis cu
    print(f"analysis_dir: {analysis_dir}")

    try:
        os.environ["EDA_SRC_DIR"] = str(SRC_DIR)
        os.environ["EDA_ANALYSIS_DIR"] = str(analysis_dir)

        notebook = nbformat.read(source_notebook, as_version=4)
        client = NotebookClient(
            notebook, timeout=args.timeout, kernel_name=args.kernel,
            resources={"metadata": {"path": str(NOTEBOOKS_DIR)}},
        )
        client.execute()

        executed_path = analysis_dir / "executed_notebooks" / args.notebook
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

    print(f"Wave A hoan tat: {analysis_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
