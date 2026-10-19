"""Ghep mot horizon: resume NGU NGHIA (local roi Drive) -> chay -> phan tich -> kiem hash -> chep len Drive co kiem lai. Script/notebook chi goi ham nay.

- Resume = tim run THANH CONG (runner.training_success: complete + contract_complete + champion + du artifact + analysis + validator predictions DOI CHIEU SNAPSHOT DA GHIM) cung identity, o `out_root` roi o Drive.
  Co o Drive ma local mat (Colab reset runtime) => khoi phuc bang `copy_run_verified` (re-hash) roi dung lai, KHONG fit. Run diagnostic (incomplete/no_valid_model) khong bao gio la cache; khong bi xoa/ghi de.
- `probe_device` (cuda that) chi chay khi that su phai huan luyen (resume khong ton GPU).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .analysis import analyze_horizon
from .candidates import probe_device
from .data import LoadedDataset
from .drive import copy_run_verified
from .runner import execute_horizon, find_successful_run, run_identity
from .transaction import verify_run_dir


def train_horizon(cfg: dict[str, Any], loaded: LoadedDataset, snapshot: tuple[pd.DataFrame, dict[str, Any]], out_root: Path | str, *, run_id: str,
                  drive_root: Path | str | None = None, clock: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    snap, snap_manifest = snapshot
    dataset_version = loaded.pin["dataset_version"]
    out_dir = Path(out_root) / dataset_version
    drive_dir = None if drive_root is None else Path(drive_root) / dataset_version
    identity = run_identity(cfg, loaded)
    resumed_from: str | None = None
    probe: dict[str, Any] | None = None

    run_dir = find_successful_run(out_dir, identity, require_analysis=True, snap=snap)
    if run_dir is not None:
        resumed_from = "local"
    elif drive_dir is not None:
        drive_hit = find_successful_run(drive_dir, identity, require_analysis=True, snap=snap)
        if drive_hit is not None:
            run_dir = copy_run_verified(drive_hit, out_dir)                    # khoi phuc co kiem hash, khong fit
            resumed_from = "drive"
    if run_dir is None:
        probe = probe_device(cfg)                                              # yeu cau cuda ma khong co GPU => DeviceError truoc khi ton thoi gian CV

        def analysis(result: Any, loaded_: LoadedDataset, cfg_: dict[str, Any]) -> dict[str, Any]:
            return analyze_horizon(result.predictions, result.report, snap, snap_manifest, cfg_)

        run_dir = execute_horizon(cfg, loaded, out_dir, run_id=run_id, clock=clock, analysis=analysis, probe=probe, snap=snap)
    manifest = verify_run_dir(run_dir)
    drive_run = None
    if drive_dir is not None:
        drive_run = copy_run_verified(run_dir, drive_dir)                       # idempotent neu da co y het
    return {"horizon": int(loaded.horizon), "dataset_version": dataset_version, "run_dir": str(run_dir), "drive_run_dir": None if drive_run is None else str(drive_run),
            "status": manifest["status"], "contract_complete": manifest["contract_complete"], "resumed_from": resumed_from, "files": sorted(manifest["files"]), "device_probe": probe}
