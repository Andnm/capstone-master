"""Causal first-approval/freeze (spec muc 11) -> `ml_reference_assignments`.

Xu ly `crawl_runs` theo (finished_at, source_code, source_run_id) tang dan; sau MOI run hoan tat danh gia
candidate dung dau cua cac series (hotel, checkin) ma run do cham toi, dung thoi diem va co che production
thuc su quyet dinh (`_refresh_reference()`): rank truoc, kiem tra nguong sau. Series approve LAN DAU thi
dong bang vinh vien (khong danh gia lai, khong dung bang chung sau do).

Loi thuat toan:
- `CausalReplay` la logic THUAN (khong DB) de test duoc dung; `build_causal_references` chi nap run tu DB.
- Accumulator theo series: bo nho ~ so candidate (vai tram nghin tren du lieu that), khong giu observation.
- `distinct_item_count`: moi item dem 1 lan/candidate; `observation_count`: moi quan sat; `distinct_run_count`:
  1 lan/run co candidate. `eligible_item_count`: item co >=1 quan sat du dieu kien (khong sold-out, gia khong NULL,
  co canonical_room_key). Cung mau so cho moi candidate cua series nen xep hang `item_coverage DESC` == `items DESC`.

KHAC BIET CHU DICH so voi reference_builder.py/production (ghi vao dau ra de audit):
1. Thuoc tinh mo ta (room_type_anchor_raw, bed_config, ...) lay `max()` theo thu tu codepoint cua Python, khong
   theo collation MySQL (utf8mb4_unicode_ci) nhu `MAX()` SQL. Chi dung de tinh diem alias o buoc matching (da
   canonical_text hoa), nen khong doi ket qua exact/alias trong thuc te; ghi nhan o `attribute_max_semantics`.
2. Nguong coverage so bang phep chia so thuc (`items/eligible >= min_coverage`), khong qua DECIMAL(.,4) cua MySQL.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import env  # noqa: F401
from .db import execute, executemany, fetch_all, utc_now

from app.warehouse.canonicalize import EMPTY_ROOM_KEY, canonical_series_id  # noqa: E402

ATTRIBUTE_MAX_SEMANTICS = "python-codepoint-max (khac MAX() collation cua MySQL; chi anh huong anchor/bed/area)"

# Thu tu cot cua mot dong evidence do SQL tra ve - `CausalReplay.process_run` doc dung thu tu nay.
EVIDENCE_COLUMNS = (
    "item_id", "source_code", "source_item_id", "hotel_id", "checkin_date", "room_key", "rate_key",
    "room_type_raw", "room_type_norm", "max_occupancy", "bed_config", "room_area", "breakfast_included", "free_cancellation",
)


@dataclass
class RunEvidence:
    run_id: int
    finished_at: dt.datetime
    source_code: str
    source_run_id: int
    rows: list[tuple]


@dataclass
class Assignment:
    hotel_id: str
    checkin_date: dt.date
    canonical_room_key: str
    canonical_rate_key: str
    canonical_series_id: str
    room_type_anchor_raw: str
    room_type_norm: str | None
    max_occupancy: int | None
    bed_config: str | None
    room_area: str | None
    breakfast_included: bool | None
    free_cancellation: bool | None
    approved_at: dt.datetime
    approving_run_id: int
    approving_item_id: int
    evidence_run_count: int
    evidence_item_count: int
    eligible_item_count: int
    coverage: float
    confidence_score: float


class _Cand:
    __slots__ = ("obs", "items", "runs", "last_run", "anchor", "norm", "occ", "bed", "area", "bf", "fc")

    def __init__(self) -> None:
        self.obs = self.items = self.runs = 0
        self.last_run = None
        self.anchor = self.norm = self.occ = self.bed = self.area = self.bf = self.fc = None


class _Series:
    __slots__ = ("eligible", "cands", "approved")

    def __init__(self) -> None:
        self.eligible = 0
        self.cands: dict[tuple[str, str], _Cand] = {}
        self.approved = False


def _max(current: Any, value: Any) -> Any:
    if value is None:
        return current
    if current is None:
        return value
    return value if value > current else current


def _rank_key(cand: _Cand, room_key: str, rate_key: str) -> tuple:
    unique = cand.obs == cand.items
    return (0 if unique else 1, -cand.items, -cand.runs,
            0 if (cand.occ is not None and cand.occ <= 2) else 1, -cand.obs, room_key, rate_key)


@dataclass
class CausalReplay:
    min_runs: int
    min_coverage: float
    series: dict[tuple[str, dt.date], _Series] = field(default_factory=dict)
    runs_processed: int = 0

    def process_run(self, run: RunEvidence) -> list[Assignment]:
        self.runs_processed += 1
        by_series: dict[tuple[str, dt.date], dict[int, list[tuple]]] = {}
        for row in run.rows:
            by_series.setdefault((row[3], row[4]), {}).setdefault(row[0], []).append(row)
        approved: list[Assignment] = []
        for key in sorted(by_series, key=lambda k: (k[0], k[1])):
            state = self.series.get(key)
            if state is None:
                state = self.series[key] = _Series()
            if state.approved:
                continue
            items = by_series[key]
            containing: dict[tuple[str, str], list[tuple[str, int, int]]] = {}
            for item_id in sorted(items):
                item_rows = items[item_id]
                state.eligible += 1
                seen: dict[tuple[str, str], _Cand] = {}
                for (_i, _sc, _si, _h, _c, room_key, rate_key, raw, norm, occ, bed, area, bf, fc) in item_rows:
                    ck = (room_key, rate_key)
                    cand = state.cands.get(ck)
                    if cand is None:
                        cand = state.cands[ck] = _Cand()
                    cand.obs += 1
                    seen.setdefault(ck, cand)
                    cand.anchor = _max(cand.anchor, raw)
                    cand.norm = _max(cand.norm, norm)
                    cand.occ = _max(cand.occ, occ)
                    cand.bed = _max(cand.bed, bed)
                    cand.area = _max(cand.area, area)
                    cand.bf = _max(cand.bf, bf)
                    cand.fc = _max(cand.fc, fc)
                first = item_rows[0]
                for ck, cand in seen.items():
                    cand.items += 1
                    if cand.last_run != run.run_id:
                        cand.runs += 1
                        cand.last_run = run.run_id
                    containing.setdefault(ck, []).append((first[1], first[2], item_id))
            assignment = self._evaluate(key, state, run, containing, items)
            if assignment is not None:
                state.approved = True
                approved.append(assignment)
        return approved

    def _evaluate(self, key, state: _Series, run: RunEvidence, containing, items) -> Assignment | None:
        if not state.cands or state.eligible <= 0:
            return None
        best_key = min(state.cands, key=lambda ck: _rank_key(state.cands[ck], ck[0], ck[1]))
        best = state.cands[best_key]
        coverage = best.items / state.eligible
        unique = best.obs == best.items
        if not (best.runs >= self.min_runs and coverage >= self.min_coverage and unique):
            return None
        # approving item: item cua DUNG run duyet, uu tien item chua candidate duoc chon; tie-break theo
        # (source_code, source_item_id), khong dung technical ID.
        holders = containing.get(best_key)
        if holders:
            approving = min(holders)[2]
        else:
            approving = min((rows[0][1], rows[0][2], item_id) for item_id, rows in items.items())[2]
        hotel_id, checkin_date = key
        return Assignment(
            hotel_id=hotel_id, checkin_date=checkin_date, canonical_room_key=best_key[0], canonical_rate_key=best_key[1],
            canonical_series_id=canonical_series_id(hotel_id, checkin_date, best_key[0], best_key[1]),
            room_type_anchor_raw=(best.anchor or "")[:500], room_type_norm=(best.norm[:100] if best.norm else None),
            max_occupancy=best.occ, bed_config=(best.bed[:500] if best.bed else None), room_area=best.area,
            breakfast_included=None if best.bf is None else bool(best.bf), free_cancellation=None if best.fc is None else bool(best.fc),
            approved_at=run.finished_at, approving_run_id=run.run_id, approving_item_id=approving,
            evidence_run_count=best.runs, evidence_item_count=best.items, eligible_item_count=state.eligible,
            coverage=round(coverage, 4), confidence_score=round(coverage, 4),
        )

    def unapproved_reasons(self) -> Counter:
        """Ly do series chua bao gio duoc duyet (dua tren candidate dung dau cuoi cung) - de bao cao coverage."""
        reasons: Counter = Counter()
        for state in self.series.values():
            if state.approved or not state.cands or state.eligible <= 0:
                continue
            best_key = min(state.cands, key=lambda ck: _rank_key(state.cands[ck], ck[0], ck[1]))
            best = state.cands[best_key]
            failed = []
            if best.runs < self.min_runs:
                failed.append("insufficient_runs")
            if best.items / state.eligible < self.min_coverage:
                failed.append("low_coverage")
            if best.obs != best.items:
                failed.append("non_unique_per_item")
            reasons["+".join(failed) or "unknown"] += 1
        return reasons


# --------------------------------------------------------------------------- DB adapter

_RUN_ORDER_SQL = """
SELECT r.id AS run_id, r.finished_at, rm.source_code, rm.source_run_id
FROM crawl_runs r
JOIN etl_run_map rm ON rm.warehouse_run_id = r.id AND rm.import_batch_id = %s
WHERE r.status = 'completed' AND rm.include_reference = TRUE
ORDER BY r.finished_at, rm.source_code, rm.source_run_id
"""

_RUN_EVIDENCE_SQL = """
SELECT po.crawl_run_item_id AS item_id, im.source_code, im.source_item_id, po.hotel_id, po.checkin_date,
       cok.canonical_room_key AS room_key, cok.canonical_rate_key AS rate_key, po.room_type_raw, po.room_type_norm,
       po.max_occupancy, po.bed_config, po.room_area, po.breakfast_included, po.free_cancellation
FROM price_observations po
JOIN curated_observation_keys cok ON cok.record_id = po.record_id
JOIN crawl_run_items cri ON cri.id = po.crawl_run_item_id AND cri.status = 'success'
JOIN etl_item_map im ON im.warehouse_item_id = cri.id AND im.import_batch_id = %s AND im.include_reference = TRUE
WHERE po.crawl_run_id = %s AND po.is_sold_out = 0 AND po.price_per_night IS NOT NULL AND cok.canonical_room_key <> %s
ORDER BY po.crawl_run_item_id, po.room_option_index
"""

_INSERT_SQL = """
INSERT INTO ml_reference_assignments (
  import_batch_id, dataset_version, hotel_id, checkin_date, canonical_room_key, canonical_rate_key, canonical_series_id,
  room_type_anchor_raw, room_type_norm, max_occupancy, bed_config, room_area, breakfast_included, free_cancellation,
  approved_at, approving_run_warehouse_id, approving_item_warehouse_id, evidence_run_count, evidence_item_count,
  eligible_item_count, coverage, confidence_score, reference_algorithm_version, created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def load_run_order(conn, batch_id: str) -> list[dict[str, Any]]:
    return fetch_all(conn, _RUN_ORDER_SQL, (batch_id,))


def load_run_evidence(conn, batch_id: str, run: dict[str, Any]) -> RunEvidence:
    rows = fetch_all(conn, _RUN_EVIDENCE_SQL, (batch_id, run["run_id"], EMPTY_ROOM_KEY), dictionary=False)
    return RunEvidence(run_id=run["run_id"], finished_at=run["finished_at"], source_code=run["source_code"],
                       source_run_id=run["source_run_id"], rows=[tuple(r) for r in rows])


def build_causal_references(conn, *, dataset_version: str, config: dict[str, Any],
                            heartbeat: Callable[[], None] | None = None) -> dict[str, Any]:
    """Step `causal_references`. Giu `ml_reference_assignments` cua version nay = ket qua replay day du (full replacement)."""
    gate = config["reference_quality_gate"]
    batch_id = config["import_batch_id"]
    replay = CausalReplay(min_runs=gate["min_runs"], min_coverage=gate["min_coverage"])
    order = load_run_order(conn, batch_id)
    if any(run["finished_at"] is None for run in order):
        raise ValueError("co run completed nhung finished_at NULL - warehouse khong hop le cho causal replay.")
    assignments: list[Assignment] = []
    for run in order:
        assignments.extend(replay.process_run(load_run_evidence(conn, batch_id, run)))
        if heartbeat is not None:
            heartbeat()
    assignments.sort(key=lambda a: (a.approved_at, a.hotel_id, a.checkin_date))
    created_at = utc_now()
    rows = [(batch_id, dataset_version, a.hotel_id, a.checkin_date, a.canonical_room_key, a.canonical_rate_key,
             a.canonical_series_id, a.room_type_anchor_raw, a.room_type_norm, a.max_occupancy, a.bed_config, a.room_area,
             a.breakfast_included, a.free_cancellation, a.approved_at, a.approving_run_id, a.approving_item_id,
             a.evidence_run_count, a.evidence_item_count, a.eligible_item_count, a.coverage, a.confidence_score,
             config["reference_algorithm_version"], created_at) for a in assignments]
    try:
        execute(conn, "DELETE FROM ml_reference_assignments WHERE dataset_version=%s", (dataset_version,))
        executemany(conn, _INSERT_SQL, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    by_day = Counter(a.approved_at.date().isoformat() for a in assignments)
    return {
        "runs_processed": replay.runs_processed,
        "series_seen": len(replay.series),
        "assignments": len(assignments),
        "unapproved_series": len(replay.series) - len(assignments),
        "unapproved_reasons": dict(replay.unapproved_reasons()),
        "approved_per_utc_day": dict(sorted(by_day.items())),
        "first_approved_at": assignments[0].approved_at.isoformat() if assignments else None,
        "last_approved_at": assignments[-1].approved_at.isoformat() if assignments else None,
        "attribute_max_semantics": ATTRIBUTE_MAX_SEMANTICS,
    }
