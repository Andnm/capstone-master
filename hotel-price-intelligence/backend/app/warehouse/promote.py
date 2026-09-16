"""Buoc 17 - promote: atomic replace `outputs/warehouse/warehouse_current.json` (muc 3a, muc 18).

Chi promote batch `pass`, sau khi TU KIEM LAI tai thoi diem promote (khong tin status co san - muc 16):
re-verify `source_manifest_sha256` va chay lai toan bo `validate_warehouse`. Da promote dung batch nay
-> no-op. KHONG BAO GIO duoc goi trong rehearsal (GPT file 10).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .atomic import atomic_write_json
from .connection import warehouse_connection
from .errors import BatchStateError, ProvenanceError
from .naming import require_warehouse_database
from .provenance import require_replayable_provenance
from .registry import verify_source_manifest
from .validation import CHECKSUM_TABLES, semantic_checksums, validate_warehouse

POINTER_PATH = Path(__file__).resolve().parents[4] / "outputs" / "warehouse" / "warehouse_current.json"


def promote_warehouse(database: str, batch_id: str, *, pointer_path: Path = POINTER_PATH) -> dict[str, Any]:
    database = require_warehouse_database(database)
    with warehouse_connection(database) as wh:
        cursor = wh.cursor(dictionary=True)
        cursor.execute("SELECT * FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
        batch = cursor.fetchone()
        cursor.close()
        if batch is None:
            raise BatchStateError(f"khong co batch {batch_id!r} trong {database}.")
        if batch["status"] != "pass":
            raise BatchStateError(f"batch {batch_id} co status={batch['status']!r} - chi promote batch 'pass'.")
        if batch["warehouse_database"] != database:
            raise BatchStateError(f"batch ghi warehouse_database={batch['warehouse_database']!r} != {database!r}.")
        try:  # provenance phai la commit THAT, con ton tai trong repo (GPT review 12 MAJOR 2 + 14 MINOR)
            require_replayable_provenance(batch["canonicalization_git_commit"])
        except ProvenanceError as exc:
            raise ProvenanceError(
                f"batch {batch_id}: {exc} Commit code roi build warehouse MOI - khong promote batch nay."
            ) from exc
        manifest_sha = verify_source_manifest(wh, batch_id)
        validation = validate_warehouse(wh, batch_id=batch_id)
        if not validation["ok"]:
            failed = [check["name"] for check in validation["checks"] if not check["ok"]]
            raise BatchStateError(f"validation tai thoi diem promote FAIL: {failed} - khong promote.")
        drift = _checksum_drift(wh, batch)
        if drift["semantic"]:
            raise BatchStateError(
                f"du lieu warehouse DA DOI sau build o {drift['semantic']}: semantic checksum lech ban da pin "
                f"trong notes luc build - khong promote. Mot rebuild reference hop le khong duoc lam doi "
                f"semantic checksum (GPT review 12 MAJOR 1)."
            )
    payload = {
        "warehouse_database": database, "batch_id": batch_id, "source_manifest_sha256": manifest_sha,
        "canonicalization_version": batch["canonicalization_version"],
        "cohort_manifest_sha256": batch["cohort_manifest_sha256"],
        "ownership_manifest_sha256": batch["ownership_manifest_sha256"],
        "batch_finished_at": batch["finished_at"],
        "canonicalization_git_commit": batch["canonicalization_git_commit"],
        "exact_checksum_lech_ban_pin": drift["exact"],
    }
    if pointer_path.exists():
        current = json.loads(pointer_path.read_text(encoding="utf-8"))
        if current.get("warehouse_database") == database and current.get("batch_id") == batch_id:
            return {"promoted": False, "reason": "da promote dung batch nay (no-op)", **payload}
    atomic_write_json(pointer_path, payload)
    return {"promoted": True, **payload}


def _checksum_drift(wh, batch: dict[str, Any]) -> dict[str, list[str]]:
    """So checksum hien tai voi ban da PIN trong `notes.checksums` luc build.

    `semantic` la GATE: noi dung phai y nguyen. `exact` chi bao cao, vi mot lan rebuild reference hop le
    lam `hotel_reference_rooms.id` nhay (InnoDB khong lui AUTO_INCREMENT sau DELETE) - noi dung van dung.
    """
    pinned = json.loads(batch.get("notes") or "{}").get("checksums") or {}
    expected = set(CHECKSUM_TABLES)
    for kind in ("semantic", "exact"):
        keys = set(pinned.get(kind) or {})
        if keys != expected:  # thieu key = im lang bo qua bang do -> khong con la gate (GPT review 14 MAJOR)
            raise BatchStateError(
                f"batch {batch['batch_id']!r}: `notes.checksums.{kind}` phai co DUNG {len(expected)} bang. "
                f"Thieu {sorted(expected - keys)}, thua {sorted(keys - expected)} - khong chung minh duoc du "
                f"lieu chua bi doi sau build, khong promote."
            )
    current = semantic_checksums(wh)
    return {kind: sorted(table for table in CHECKSUM_TABLES if current[kind][table] != pinned[kind][table])
            for kind in ("semantic", "exact")}
