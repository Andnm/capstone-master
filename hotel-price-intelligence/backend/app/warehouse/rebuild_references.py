"""Rebuild full-history reference ROI khoi `build_warehouse` (spec muc 18, dong `build_warehouse_references`).

GPT review 12 MAJOR 1: duong nay co the lam mot batch PASS lech khoi chinh config no da pin, nen phai
fail-closed TRUOC moi write, theo 5 lop:

1. batch phai ton tai va `warehouse_database` phai khop.
2. TU CHOI neu `warehouse_current.json` dang tro vao warehouse nay - khong duoc mutate warehouse ma
   downstream dang coi la current.
3. re-verify `source_manifest_sha256` (spec muc 16).
4. re-tinh `etl_config()` cua code dang chay, bat buoc TRUNG `etl_import_batches.etl_config_sha256`
   -> bat truong hop doi threshold trong .env (hoac doi version) roi rebuild.
5. rebuild voi `commit=False`, roi: so semantic checksum 2 bang reference voi ban da pin trong
   `notes.checksums` luc build -> chay lai `validate_warehouse` -> chi commit khi ca hai dat. Lop nay
   bat truong hop doi THUAT TOAN ma quen bump `REFERENCE_ALGORITHM_VERSION`: hash config khong doi
   nhung noi dung reference doi.

Bat ky lop nao FAIL -> rollback, khong ghi gi, batch khong bi doi trang thai.

Ve ID: `DELETE` khong lui AUTO_INCREMENT cua InnoDB, nen `hotel_reference_rooms.id` sau rebuild lech so
voi luc build (checksum "exact" doi, "semantic" khong). Vi vay gate o day - va gate cua
`promote_warehouse` - deu la SEMANTIC; moi lan rebuild thanh cong duoc ghi vao `notes.reference_rebuilds`
kem exact checksum moi de van truy nguoc duoc.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .connection import warehouse_connection
from .errors import BatchStateError, ValidationError
from .etl_config import etl_config, etl_config_sha256
from .naming import require_warehouse_database
from .promote import POINTER_PATH
from .reference_builder import REFERENCE_ALGORITHM_VERSION, build_full_history_references
from .registry import verify_source_manifest
from .validation import semantic_checksums, validate_warehouse

REFERENCE_TABLES = ("hotel_room_candidates", "hotel_reference_rooms")


def rebuild_full_history_references(database: str, batch_id: str, *,
                                   pointer_path: Path = POINTER_PATH) -> dict[str, Any]:
    database = require_warehouse_database(database)
    with warehouse_connection(database) as wh:
        cursor = wh.cursor(dictionary=True)
        cursor.execute("SELECT * FROM etl_import_batches WHERE batch_id=%s", (batch_id,))
        batch = cursor.fetchone()
        cursor.close()
        if batch is None:
            raise BatchStateError(f"khong co batch {batch_id!r} trong {database}.")
        if batch["warehouse_database"] != database:
            raise BatchStateError(
                f"batch ghi warehouse_database={batch['warehouse_database']!r} != {database!r}.")
        _refuse_if_current(database, batch_id, pointer_path)
        verify_source_manifest(wh, batch_id)
        config, current_sha = etl_config(), etl_config_sha256()
        if current_sha != batch["etl_config_sha256"]:
            raise BatchStateError(
                f"etl_config cua code dang chay (sha {current_sha}) KHAC config batch da pin "
                f"(sha {batch['etl_config_sha256']}) - rebuild se lam batch lech khoi config cua chinh no. "
                f"Config hien tai: {config}. Hoac tra config ve dung, hoac build warehouse/batch MOI."
            )
        pinned = _pinned_reference_checksums(batch)
        result = build_full_history_references(
            wh, batch_id=batch_id, activated_at=batch["started_at"], min_runs=config["reference_min_runs"],
            min_coverage=config["reference_min_coverage"], commit=False,
        )
        after = semantic_checksums(wh, tables=REFERENCE_TABLES)
        drifted = sorted(table for table in REFERENCE_TABLES if pinned[table] != after["semantic"][table])
        if drifted:
            wh.rollback()
            raise ValidationError(
                f"rebuild cho noi dung KHAC ban da pin luc build o {drifted}, du hash config khong doi - "
                f"nghi thuat toan reference da doi ma chua bump REFERENCE_ALGORITHM_VERSION "
                f"({REFERENCE_ALGORITHM_VERSION}). Da rollback, khong ghi gi."
            )
        validation = validate_warehouse(wh, batch_id=batch_id)
        if not validation["ok"]:
            failed = [check["name"] for check in validation["checks"] if not check["ok"]]
            wh.rollback()
            raise ValidationError(f"validation sau rebuild FAIL: {failed} - da rollback, khong ghi gi.")
        _record_rebuild(wh, batch, after)
        wh.commit()
    return {"rebuilt": True, "semantic_khop_ban_pin": True, "validation_ok": True, **result}


def _refuse_if_current(database: str, batch_id: str, pointer_path: Path) -> None:
    if not pointer_path.exists():
        return
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("warehouse_database") == database:
        raise BatchStateError(
            f"{pointer_path.name} dang tro vao warehouse nay (batch {pointer.get('batch_id')!r}) - KHONG "
            f"rebuild reference tren warehouse dang la current. Build warehouse/batch MOI neu can doi "
            f"reference (batch duoc yeu cau: {batch_id!r})."
        )


def _pinned_reference_checksums(batch: dict[str, Any]) -> dict[str, str]:
    notes = json.loads(batch.get("notes") or "{}")
    pinned = (notes.get("checksums") or {}).get("semantic") or {}
    missing = [table for table in REFERENCE_TABLES if not pinned.get(table)]
    if missing:
        raise BatchStateError(
            f"batch {batch['batch_id']!r} khong co semantic checksum da pin cho {missing} trong notes - "
            f"khong verify duoc rebuild co tai lap hay khong. Batch nay do ban code cu build: build "
            f"warehouse MOI thay vi rebuild."
        )
    return {table: pinned[table] for table in REFERENCE_TABLES}


def _record_rebuild(wh, batch: dict[str, Any], after: dict[str, dict[str, str]]) -> None:
    """Ghi dau vet rebuild vao notes. KHONG sua `notes.checksums` da pin luc build."""
    notes = json.loads(batch.get("notes") or "{}")
    notes.setdefault("reference_rebuilds", []).append({
        "at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0).isoformat(),
        "etl_config_sha256": batch["etl_config_sha256"],
        "reference_algorithm_version": REFERENCE_ALGORITHM_VERSION,
        "exact_checksum_sau_rebuild": {table: after["exact"][table] for table in REFERENCE_TABLES},
    })
    cursor = wh.cursor()
    try:
        cursor.execute("UPDATE etl_import_batches SET notes=%s WHERE batch_id=%s",
                       (json.dumps(notes, ensure_ascii=False, sort_keys=True, default=str), batch["batch_id"]))
    finally:
        cursor.close()
