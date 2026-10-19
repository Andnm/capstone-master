"""Tien ich dung chung cho test MySQL cua dataset builder."""
from __future__ import annotations

import datetime as dt
import itertools

from dataset_builder import config as cfg
from dataset_builder import manifest
from dataset_builder.db import connect, execute, fetch_all

_counter = itertools.count(1)


def trivial_step(ctx):
    return {}


def steps_with(real: dict | None = None) -> dict:
    """Registry cho test: step that khi duoc cai, con lai tra {} (khong lam gi)."""
    from dataset_builder.steps import STEP_FUNCTIONS
    registry = {name: trivial_step for name in STEP_FUNCTIONS}
    if real:
        registry.update(real)
    return registry


def make_dataset(fx: dict, *, tag: str | None = None, purpose: str = "rehearsal", anomaly_mode: str = "evaluation_asof",
                 cutoff: dt.datetime | None = dt.datetime(2026, 10, 5), purge_gap_days: int = 14) -> tuple[str, str]:
    database = fx["warehouse_database"]
    tag = tag or f"t{next(_counter)}"
    version = f"ds_20261005_{tag}"
    with connect(database) as conn:
        thresholds = manifest.resolve_pinned_thresholds(conn, fx["batch_id"])
        config = cfg.build_config(
            import_batch_id=fx["batch_id"], purpose=purpose, anomaly_mode=anomaly_mode, anomaly_cutoff_at=cutoff,
            anomaly_registry_file_sha256=cfg.default_registry_sha256(), purge_gap_days=purge_gap_days, **thresholds)
        manifest.init_dataset_build(conn, dataset_version=version, config=config)
    return database, version


def drop_dataset(database: str, version: str) -> None:
    """Don sach moi bang con theo thu tu child-first roi xoa manifest (dung cho teardown test)."""
    from dataset_builder.cleanup import cleanup_from
    with connect(database) as conn:
        cleanup_from(conn, version, "causal_references")
        execute(conn, "DELETE FROM dataset_build_manifests WHERE dataset_version=%s", (version,))
        conn.commit()


def rows(database: str, sql: str, params: tuple = ()):
    with connect(database) as conn:
        result = fetch_all(conn, sql, params)
        conn.commit()
        return result


def registry_file(tmp_path, events: list[dict]):
    import json
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"schema_version": 1, "declared_sources": ["local_primary", "vps", "local_aux"], "events": events}),
                    encoding="utf-8")
    return path


def registry_event(seq, review_id, decided_at, members, *, decision="exclude_from_train"):
    return {"sequence": seq, "event_id": f"ev-{seq}", "action": "activate", "review_id": review_id, "decision": decision,
            "reason_code": "test", "rationale": "test", "evidence": {}, "reviewer": "claude", "decided_at": decided_at,
            "members": members}


def member_for(database, hotel, day, room="A", *, tamper=False):
    r = rows(database, "SELECT m.source_code, m.source_record_id, m.source_record_sha256 FROM etl_observation_map m "
                       "JOIN price_observations po ON po.record_id=m.warehouse_record_id "
                       "WHERE po.hotel_id=%s AND DATE(po.observed_at)=%s AND po.room_type_raw=%s", (hotel, dt.date(2026, 9, day), room))[0]
    sha = ("0" * 64) if tamper else r["source_record_sha256"]
    return {"source_code": r["source_code"], "source_record_id": r["source_record_id"], "source_record_sha256": sha}
