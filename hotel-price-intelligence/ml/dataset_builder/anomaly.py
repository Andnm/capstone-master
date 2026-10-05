"""Anomaly registry replay trong dataset build (CLAUDE.md muc 4.5 "Chua lam (Phase 3)", spec muc 14).

Warehouse KHONG doc `price_observations.is_anomaly` (co do tu DB nguon, khong phai authority). Thay vao do
replay `anomaly_registry.json` (git tracked, event log append-only) theo mot trong hai che do:
- `evaluation_asof`: CHI event co `decided_at <= cutoff` (1 cutoff DUY NHAT cho ca train+validation+test) ->
  khong quyet dinh nao sau cutoff lot vao bat ky phan nao, ke ca train.
- `retrospective_full`: moi event - chi cho EDA/production-fit, khong bao gio de bao metric holdout.

Member cua registry la `(source_code, source_record_id, source_record_sha256)` cua DB NGUON; map sang
`warehouse_record_id` qua `etl_observation_map` va doi chieu fingerprint (chong tai su dung record_id): lech =
FAIL cung. Member cua source khong nam trong batch bi bo qua co chu dich (ghi vao report).
Tai dung `compute_expected_full_state_from_events` cua tang van hanh de khong lech nghia replay.
"""
from __future__ import annotations

import datetime as dt
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import env  # noqa: F401
from .db import fetch_all

from app.scraper.anomaly_registry_lib import (  # noqa: E402
    DEFAULT_REGISTRY_PATH, RegistryError, checksum_of_pairs, compute_expected_full_state_from_events, load_registry,
    parse_iso_utc, registry_file_sha256, validate_events,
)

_CHUNK = 500


class AnomalyReplayError(RuntimeError):
    pass


@dataclass
class AnomalyReplay:
    mode: str
    cutoff_at: dt.datetime | None
    registry_file_sha256: str
    excluded_record_ids: set[int] = field(default_factory=set)
    events_total: int = 0
    events_applied: int = 0
    per_source: dict[str, dict[str, Any]] = field(default_factory=dict)
    decision_member_counts: Counter = field(default_factory=Counter)
    ignored_sources: dict[str, int] = field(default_factory=dict)

    def manifest_checksums(self) -> dict[str, Any]:
        """Noi dung ghi vao `dataset_build_manifests.anomaly_source_member_checksums` (JSON)."""
        return {
            "mode": self.mode, "cutoff_at": self.cutoff_at.isoformat() + "Z" if self.cutoff_at else None,
            "registry_file_sha256": self.registry_file_sha256, "events_total": self.events_total,
            "events_applied": self.events_applied, "per_source": self.per_source,
            "ignored_sources": self.ignored_sources, "excluded_record_count": len(self.excluded_record_ids),
        }


REGISTRY_PATH_ENV = "DATASET_BUILDER_ANOMALY_REGISTRY"


def registry_path() -> Path:
    """File registry dung khi build. Bien moi truong chi de TEST (fixture co record id rieng); sha256 cua file
    van duoc ghim vao config nen doi file = dataset_version moi."""
    override = os.environ.get(REGISTRY_PATH_ENV)
    return Path(override) if override else DEFAULT_REGISTRY_PATH


def _parse_cutoff(value: str | None) -> dt.datetime | None:
    return parse_iso_utc(value) if value else None


def replay_registry(conn, *, config: dict[str, Any], registry_file: Path | None = None) -> AnomalyReplay:
    anomaly = config["anomaly"]
    mode, cutoff = anomaly["mode"], _parse_cutoff(anomaly["cutoff_at"])
    path = registry_file or registry_path()
    try:
        data, raw = load_registry(path)
        events = validate_events(data)
    except RegistryError as exc:
        raise AnomalyReplayError(f"registry file khong hop le: {exc}") from exc
    digest = registry_file_sha256(raw)
    if digest != anomaly["registry_file_sha256"]:
        raise AnomalyReplayError(
            f"registry file da doi sau khi init: sha256 hien tai {digest} != da ghim {anomaly['registry_file_sha256']} "
            f"- tao dataset_version moi (khong am tham ap quyet dinh moi vao build cu).")
    if mode == "evaluation_asof":
        used = [e for e in events if parse_iso_utc(e["decided_at"]) <= cutoff]
    elif mode == "retrospective_full":
        used = list(events)
    else:
        raise AnomalyReplayError(f"anomaly mode khong hop le: {mode!r}")
    result = AnomalyReplay(mode=mode, cutoff_at=cutoff, registry_file_sha256=digest,
                           events_total=len(events), events_applied=len(used))
    batch_id = config["import_batch_id"]
    batch_sources = {r["source_code"] for r in fetch_all(conn, "SELECT source_code FROM etl_import_sources WHERE import_batch_id=%s", (batch_id,))}
    declared = set(data["declared_sources"])
    for source in sorted(declared):
        state = compute_expected_full_state_from_events(used, source)
        fingerprints: dict[int, str] = state["expected_member_fingerprints"]
        if source not in batch_sources:
            if fingerprints:
                result.ignored_sources[source] = len(fingerprints)
            continue
        mapped = _map_members(conn, batch_id, source, fingerprints)
        excluded_source_ids = state["expected_true_ids"]
        result.excluded_record_ids.update(mapped[rid] for rid in excluded_source_ids)
        for rid in excluded_source_ids:
            result.decision_member_counts["exclude_from_train"] += 1
        for review_id, decision in state["expected_decisions"].items():
            if state["decision_states"].get(review_id) == "active":
                result.decision_member_counts[f"active:{decision['decision']}"] += decision["member_count"]
        result.per_source[source] = {
            "members_checked": len(fingerprints),
            "member_checksum": checksum_of_pairs([(source, rid, sha) for rid, sha in fingerprints.items()]),
            "excluded_members": len(excluded_source_ids),
            "excluded_checksum": checksum_of_pairs([(source, rid, fingerprints[rid]) for rid in excluded_source_ids]),
        }
    return result


def _map_members(conn, batch_id: str, source: str, fingerprints: dict[int, str]) -> dict[int, int]:
    """source_record_id -> warehouse_record_id; FAIL neu thieu hoac fingerprint lech (khong doan, khong bo qua)."""
    mapped: dict[int, int] = {}
    ids = sorted(fingerprints)
    missing: list[int] = []
    mismatched: list[int] = []
    for start in range(0, len(ids), _CHUNK):
        part = ids[start:start + _CHUNK]
        placeholders = ",".join(["%s"] * len(part))
        rows = fetch_all(
            conn,
            f"SELECT source_record_id, source_record_sha256, warehouse_record_id FROM etl_observation_map "
            f"WHERE import_batch_id=%s AND source_code=%s AND source_record_id IN ({placeholders})",
            (batch_id, source, *part))
        found = {r["source_record_id"]: r for r in rows}
        for rid in part:
            row = found.get(rid)
            if row is None:
                missing.append(rid)
            elif row["source_record_sha256"] != fingerprints[rid]:
                mismatched.append(rid)
            else:
                mapped[rid] = row["warehouse_record_id"]
    if missing or mismatched:
        raise AnomalyReplayError(
            f"registry member cua source {source!r} khong khop warehouse: thieu {len(missing)} (mau {missing[:5]}), "
            f"fingerprint lech {len(mismatched)} (mau {mismatched[:5]}) - dung (fail-closed).")
    return mapped
