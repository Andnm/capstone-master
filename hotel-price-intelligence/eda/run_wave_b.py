"""Runner Wave B (Curated ML EDA) - dau vao TUONG MINH, khong pointer, khong "dataset moi nhat" (GPT file 52 muc 1).

    python run_wave_b.py --database warehouse_dsdev_20261004_3src --batch-id b20261004_3src \\
        --dataset-version ds_20261006_rh9 --dataset-dir ../../outputs/datasets/ds_20261006_rh9

Repo/src duoc suy tu `__file__`. Kernel notebook nhan dau vao qua bien moi truong dat TRUOC khi mo kernel. Vong doi artifact giong Wave A: thu muc analysis fail-if-exists,
notebook chay tren BAN COPY qua nbclient (CWD pin vao `notebooks/`), `artifact_manifest.json` ghi SAU CUNG, loi bat ky => `FAILED.json` roi re-raise (khong de lai artifact
trong nhu PASS). Output la REHEARSAL/EXPLORATORY neu dataset purpose != official; runner KHONG promote pointer nao.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import uuid
from pathlib import Path

EDA_DIR = Path(__file__).resolve().parent
SRC_DIR = EDA_DIR / "src"
NOTEBOOKS_DIR = EDA_DIR / "notebooks"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import nbformat  # noqa: E402
from nbclient import NotebookClient  # noqa: E402

import artifacts  # noqa: E402

NOTEBOOK = "02_curated_ml_eda.ipynb"
ENV_KEYS = ("EDA_SRC_DIR", "EDA_ANALYSIS_DIR", "EDA_WB_DATABASE", "EDA_WB_BATCH_ID", "EDA_WB_DATASET_VERSION", "EDA_WB_DATASET_DIR")


def analysis_id(dataset_version: str) -> str:
    return f"waveb_{dataset_version}_{dt.datetime.now(dt.timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"       # tien to rieng, khong lan voi eda_b<batch> cua Wave A


def run_wave_b(*, database: str, batch_id: str, dataset_version: str, dataset_dir: Path, outputs_dir: Path | None = None, kernel: str = "eda_venv",
               timeout: int = 3600, notebook_name: str = NOTEBOOK) -> Path:
    source_notebook = NOTEBOOKS_DIR / notebook_name
    if not source_notebook.exists():
        raise FileNotFoundError(source_notebook)
    dataset_dir = Path(dataset_dir).resolve()
    if not (dataset_dir / "samples.parquet").exists():
        raise FileNotFoundError(f"khong tim thay samples.parquet trong {dataset_dir}")
    kwargs = {"outputs_dir": outputs_dir} if outputs_dir is not None else {}
    analysis_dir = artifacts.new_analysis_dir(analysis_id(dataset_version), **kwargs)
    print(f"analysis_dir: {analysis_dir}")
    saved = {name: os.environ.get(name) for name in ENV_KEYS}
    try:
        os.environ.update({"EDA_SRC_DIR": str(SRC_DIR), "EDA_ANALYSIS_DIR": str(analysis_dir), "EDA_WB_DATABASE": database, "EDA_WB_BATCH_ID": batch_id,
                           "EDA_WB_DATASET_VERSION": dataset_version, "EDA_WB_DATASET_DIR": str(dataset_dir)})
        notebook = nbformat.read(source_notebook, as_version=4)
        NotebookClient(notebook, timeout=timeout, kernel_name=kernel, resources={"metadata": {"path": str(NOTEBOOKS_DIR)}}).execute()
        nbformat.write(notebook, analysis_dir / "executed_notebooks" / notebook_name)
        print(f"artifact_manifest.json: {artifacts.write_artifact_manifest(analysis_dir)}")
    except Exception as exc:
        artifacts.mark_failed(analysis_dir, error=f"{type(exc).__name__}: {exc}")
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(f"Da danh dau {analysis_dir / 'FAILED.json'} - artifact khong trong nhu PASS.", file=sys.stderr)
        raise
    finally:
        for name, previous in saved.items():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
    return analysis_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Chay Notebook Wave B tren mot dataset_version duoc chi dinh ro.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--dataset-version", required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--outputs-dir", type=Path, default=None)
    parser.add_argument("--kernel", default="eda_venv")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    analysis = run_wave_b(database=args.database, batch_id=args.batch_id, dataset_version=args.dataset_version, dataset_dir=args.dataset_dir,
                          outputs_dir=args.outputs_dir, kernel=args.kernel, timeout=args.timeout)
    print(f"OK: {analysis}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
