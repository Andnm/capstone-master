"""Invalidation graph (spec muc 18): xoa CON truoc CHA, trong MOT transaction; khong ON DELETE CASCADE.

    ml_reference_assignments
        ├── ml_item_reference_matches
        └── ml_samples  (4 FK tu tham chieu -> phai detach label truoc moi DELETE)

`cleanup_from(step)` don `step` VA moi step phia sau (output downstream co the stale), theo thu tu
validation -> features_labels -> split -> samples_labels -> item_matches -> causal_references (chi toi `step`).
Khong thay buoc detach bang ON DELETE SET NULL: no co the de `has_label_hK=TRUE` va pha CHECK hai chieu.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import env
from .bundle import remove_validation_artifacts, strip_checksums
from .db import execute, fetch_all
from .manifest import STEPS, reset_manifest_outputs


def dataset_output_dir(dataset_version: str, *, root: Path | None = None) -> Path:
    return (root or env.DATASET_OUTPUT_ROOT) / dataset_version


def _safe_remove_tree(path: Path, *, root: Path) -> None:
    resolved, resolved_root = path.resolve(), root.resolve()
    if resolved_root not in resolved.parents:
        raise ValueError(f"tu choi xoa {resolved}: nam ngoai {resolved_root}")
    if resolved.exists():
        shutil.rmtree(resolved)


def detach_sample_labels(conn, dataset_version: str) -> int:
    """BAT BUOC truoc moi DELETE ml_samples: set dong thoi co va FK de moi CHECK hai chieu van dung."""
    return execute(conn,
                   """UPDATE ml_samples
                      SET has_label_h1=FALSE,  label_source_record_id_h1=NULL,
                          has_label_h3=FALSE,  label_source_record_id_h3=NULL,
                          has_label_h7=FALSE,  label_source_record_id_h7=NULL,
                          has_label_h14=FALSE, label_source_record_id_h14=NULL
                      WHERE dataset_version=%s""", (dataset_version,))


def _cleanup_validation(conn, dataset_version: str, *, root: Path) -> None:
    """Chi go san pham cua validation (reports/validation.json, REPORTS_MANIFEST.json + muc checksum `reports/*`); Parquet/hash cua
    features_labels GIU NGUYEN de retry validation chay duoc (truoc day xoa ca thu muc -> retry luon fail vi thieu Parquet)."""
    out = dataset_output_dir(dataset_version, root=root)
    if root.resolve() not in out.resolve().parents:
        raise ValueError(f"tu choi don {out.resolve()}: nam ngoai {root.resolve()}")
    remove_validation_artifacts(out)
    rows = fetch_all(conn, "SELECT output_parquet_sha256_json AS stored FROM dataset_build_manifests WHERE dataset_version=%s", (dataset_version,))
    stored = rows[0]["stored"] if rows else None
    if isinstance(stored, (str, bytes)):
        stored = json.loads(stored)
    if stored:
        execute(conn, "UPDATE dataset_build_manifests SET output_parquet_sha256_json=%s WHERE dataset_version=%s",
                (json.dumps(strip_checksums(stored), sort_keys=True), dataset_version))


def _cleanup_one(conn, dataset_version: str, step: str, *, output_root: Path | None) -> None:
    root = output_root or env.DATASET_OUTPUT_ROOT
    if step == "validation":
        _cleanup_validation(conn, dataset_version, root=root)
    elif step == "features_labels":
        _safe_remove_tree(dataset_output_dir(dataset_version, root=root), root=root)
    elif step == "split":
        execute(conn, "UPDATE ml_samples SET split=NULL WHERE dataset_version=%s", (dataset_version,))
    elif step == "samples_labels":
        detach_sample_labels(conn, dataset_version)
        execute(conn, "DELETE FROM ml_samples WHERE dataset_version=%s", (dataset_version,))
    elif step == "item_matches":
        execute(conn, "DELETE FROM ml_item_reference_matches WHERE dataset_version=%s", (dataset_version,))
    elif step == "causal_references":
        execute(conn, "DELETE FROM ml_reference_assignments WHERE dataset_version=%s", (dataset_version,))
    else:
        raise ValueError(f"step khong hop le: {step!r}")


def cleanup_from(conn, dataset_version: str, step: str, *, output_root: Path | None = None) -> None:
    """Don `step` va toan bo downstream, child-first, 1 transaction; rollback neu bat ky buoc nao loi.
    Hanh dong tren filesystem (xoa output) idempotent nen chay lai an toan neu crash giua chung."""
    order = [name for name in reversed(STEPS) if STEPS.index(name) >= STEPS.index(step)]
    try:
        for name in order:
            _cleanup_one(conn, dataset_version, name, output_root=output_root)
        reset_manifest_outputs(conn, dataset_version, step)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
