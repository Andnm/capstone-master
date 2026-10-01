"""Anomaly v2 detector - sinh CANDIDATE SIGNAL cho price_observations, KHONG tu quyet dinh loai bo
gi khoi train.

Lich su: v1 (2026-09-03) tu dong "confirmed"/loai truc tiep is_anomaly bang 1 rule ngguong. Audit du
lieu that (discuss/anomaly-v2-ground-truth/, 17 file, PASS FOR DESIGN file 17) phat hien ca that
(Lumina Premium Da Lat) khong bao gio dat duoc "confirmed" vi rule doi hoi vua context cao vua co cu
nhay so voi lich su rieng - mot soft-lock "cao ngay tu dau" khong bao gio "nhay" so voi chinh no. Dong
thoi phan lon 38 dong "confirmed" cu la false positive (gia that, da user verify tren Booking.com).

Thiet ke v2: tach RULE (script nay) khoi VERDICT (registry, xem sync_anomaly_registry.py). Script nay
CHI ghi price_anomaly_signals - khong dung ho, khong ghi is_anomaly. is_anomaly gio la projection cua
anomaly_review_resolutions, duoc dong bo boi sync_anomaly_registry.py +
reconcile_anomaly_projection.py. Xem CLAUDE.md muc 4.5.

4 signal_code (KHONG loai tru lan nhau, 1 record co the co nhieu signal):
  low_price_outlier      - gia duoi nguong hop ly (khong can room_identity_key)
  context_level_high     - gia CHINH record nay >= 5x median cac phong KHAC cung item (cung luc cao)
  temporal_level_shift    - gia >= 5x median lich su TRUOC DO cua CHINH (hotel,room,checkin) do (causal)
  hotel_wide_level_shift   - toan bo cac phong ghep duoc trong item deu nhan len cung 1 he so so voi
                              item lien truoc CUNG (hotel,checkin) - phat hien hotel-wide multiplicative
                              shift (case Lumina: 2,8125x dong nhat tren 11 phong, khong phai 1 phong
                              rieng le bi khoa mem)

Run (dry-run mac dinh, chi in bao cao - tu backend/):
    python scripts/recompute_anomalies.py --source-code local_primary
    python scripts/recompute_anomalies.py --source-code local_primary --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import median, mean, stdev
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_db_connection
from app.scraper.anomaly_registry_lib import SourceIdentityError, require_source_identity

METHOD_VERSION = "v2"

CONFIG = {
    "method_version": METHOD_VERSION,
    "low_price_high_severity_below": 10_000,
    "low_price_notable_severity_below": 50_000,
    "context_high_ratio": 5,
    "context_min_other_room_keys": 2,
    "temporal_high_ratio": 5,
    "temporal_min_distinct_items": 5,
    "temporal_min_distinct_dates": 3,
    "hotel_wide_min_factor": 2.0,
    "hotel_wide_max_dispersion": 0.15,
    "hotel_wide_min_coverage": 0.70,
    "hotel_wide_min_paired_rooms": 5,
    "hotel_wide_max_baseline_offset": 3,
}


def config_sha256(config: dict) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _vn_date(observed_at_utc_naive: datetime) -> date:
    from zoneinfo import ZoneInfo
    return observed_at_utc_naive.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()


@dataclass(frozen=True)
class HistoryEntry:
    item_id: int
    run_finished_at: datetime
    run_id: int
    representative_price: Decimal


@dataclass
class SignalRow:
    record_id: int
    signal_code: str
    severity: str
    record_observed_at: datetime
    evidence_available_at: datetime
    metrics: dict = field(default_factory=dict)


def _load_scope(cursor) -> list[dict]:
    """Common scope cho ca 4 signal: run completed + item success + gia duong, KHONG loai
    room_identity_key NULL o day - low_price_outlier khong can no, 3 signal con lai tu loc rieng."""
    cursor.execute(
        """
        SELECT po.record_id, po.hotel_id, po.room_identity_key, po.crawl_run_item_id,
               po.observed_at, po.checkin_date, po.price_per_night,
               cri.crawl_run_id, cr.finished_at AS run_finished_at, cr.id AS run_id
        FROM price_observations po
        JOIN crawl_run_items cri ON cri.id = po.crawl_run_item_id
        JOIN crawl_runs cr ON cr.id = cri.crawl_run_id
        WHERE cr.status = 'completed' AND cri.status = 'success'
          AND po.is_sold_out = 0 AND po.price_per_night IS NOT NULL AND po.price_per_night > 0
        """
    )
    return cursor.fetchall()


def compute_signals(rows: list[dict], config: dict, now: datetime) -> list[SignalRow]:
    """Pure - khong dung DB, de test. Tra ve toan bo signal (record co the xuat hien nhieu lan voi
    signal_code khac nhau, KHONG bao gio quyet dinh exclude/keep - do la viec cua registry)."""
    signals: list[SignalRow] = []

    # ---- low_price_outlier: khong can room_identity_key ----
    for r in rows:
        price = Decimal(str(r["price_per_night"]))
        if price < config["low_price_high_severity_below"]:
            severity = "high"
        elif price < config["low_price_notable_severity_below"]:
            severity = "notable"
        else:
            continue
        signals.append(SignalRow(
            record_id=r["record_id"], signal_code="low_price_outlier", severity=severity,
            record_observed_at=r["observed_at"], evidence_available_at=r["run_finished_at"],
            metrics={"price": str(price)},
        ))

    # ---- chuan bi du lieu chung cho context/temporal/hotel_wide (can room_identity_key) ----
    keyed_rows = [r for r in rows if r["room_identity_key"] is not None]

    item_room_records: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    item_meta: dict[int, tuple[str, date, datetime, int]] = {}
    for r in keyed_rows:
        iid = r["crawl_run_item_id"]
        item_room_records[iid][r["room_identity_key"]].append(r)
        item_meta[iid] = (r["hotel_id"], r["checkin_date"], r["run_finished_at"], r["run_id"])

    representative: dict[tuple[int, str], Decimal] = {}
    for iid, room_map in item_room_records.items():
        for room_key, entries in room_map.items():
            representative[(iid, room_key)] = median(Decimal(str(e["price_per_night"])) for e in entries)

    # nhom item theo hotel (dung cho context - khong can sap xep) va theo (hotel,checkin) da sap xep
    # causal (dung cho hotel_wide_level_shift)
    checkin_items: dict[tuple[str, date], list[int]] = defaultdict(list)
    for iid, (hotel_id, checkin_date, finished_at, run_id) in item_meta.items():
        checkin_items[(hotel_id, checkin_date)].append(iid)
    for key in checkin_items:
        checkin_items[key].sort(key=lambda iid: (item_meta[iid][2], item_meta[iid][3], iid))

    # ---- context_level_high: so trong CUNG 1 item, khong can lich su ----
    for iid, room_map in item_room_records.items():
        room_keys = list(room_map.keys())
        for room_key in room_keys:
            other_reps = [representative[(iid, rk)] for rk in room_keys if rk != room_key]
            context_room_count = len(other_reps)
            if context_room_count < config["context_min_other_room_keys"]:
                continue
            context_median = median(other_reps)
            for r in room_map[room_key]:
                price = Decimal(str(r["price_per_night"]))
                if price < context_median * config["context_high_ratio"]:
                    continue
                signals.append(SignalRow(
                    record_id=r["record_id"], signal_code="context_level_high", severity="notable",
                    record_observed_at=r["observed_at"], evidence_available_at=r["run_finished_at"],
                    metrics={
                        "price": str(price), "context_median": str(context_median),
                        "context_room_count": context_room_count,
                        "ratio": str(price / context_median),
                    },
                ))

    # ---- temporal_level_shift: causal, khoa (hotel,room,checkin), KHONG dung rate_plan_key (qua
    #      sparse - xem discuss finding C) ----
    hotel_items_all: dict[str, list[int]] = defaultdict(list)
    for iid, (hotel_id, checkin_date, finished_at, run_id) in item_meta.items():
        hotel_items_all[hotel_id].append(iid)
    for hotel_id in hotel_items_all:
        hotel_items_all[hotel_id].sort(key=lambda iid: (item_meta[iid][2], item_meta[iid][3], iid))

    temporal_history: dict[tuple[str, str, date], list[HistoryEntry]] = defaultdict(list)
    for hotel_id, items in hotel_items_all.items():
        for iid in items:
            _, checkin_date, finished_at, run_id = item_meta[iid]
            for room_key in item_room_records[iid]:
                temporal_key = (hotel_id, room_key, checkin_date)
                prior = [
                    h for h in temporal_history[temporal_key]
                    if (h.run_finished_at, h.run_id, h.item_id) < (finished_at, run_id, iid)
                ]
                distinct_items_prior = len({h.item_id for h in prior})
                distinct_dates_prior = len({_vn_date(h.run_finished_at) for h in prior})
                rep_price = representative[(iid, room_key)]

                if (distinct_items_prior >= config["temporal_min_distinct_items"]
                        and distinct_dates_prior >= config["temporal_min_distinct_dates"]):
                    temporal_median = median(h.representative_price for h in prior)
                    if rep_price >= temporal_median * config["temporal_high_ratio"]:
                        contributing = sorted({h.item_id for h in prior})
                        for r in item_room_records[iid][room_key]:
                            signals.append(SignalRow(
                                record_id=r["record_id"], signal_code="temporal_level_shift",
                                severity="notable", record_observed_at=r["observed_at"],
                                evidence_available_at=finished_at,
                                metrics={
                                    "price": str(rep_price), "temporal_median": str(temporal_median),
                                    "prior_distinct_items": distinct_items_prior,
                                    "prior_distinct_dates": distinct_dates_prior,
                                    "ratio": str(rep_price / temporal_median),
                                    "contributing_item_ids": contributing,
                                },
                            ))

                temporal_history[temporal_key].append(HistoryEntry(
                    item_id=iid, run_finished_at=finished_at, run_id=run_id,
                    representative_price=rep_price,
                ))

    # ---- hotel_wide_level_shift: paired-room ratio vs <=3 baseline item lien truoc CUNG
    #      (hotel,checkin), duyet offset 1->2->3, lay CAI DAU TIEN dat du gate lam primary ----
    for (hotel_id, checkin_date), items in checkin_items.items():
        for i in range(1, len(items)):
            cur_iid = items[i]
            cur_keys = set(item_room_records[cur_iid].keys())
            baselines = items[max(0, i - config["hotel_wide_max_baseline_offset"]):i][::-1]
            candidates = []
            for offset, base_iid in enumerate(baselines, start=1):
                base_keys = set(item_room_records[base_iid].keys())
                paired = cur_keys & base_keys
                union_n = len(cur_keys | base_keys)
                if len(paired) < config["hotel_wide_min_paired_rooms"] or union_n == 0:
                    continue
                ratios = {}
                for rk in paired:
                    b = representative[(base_iid, rk)]
                    if b > 0:
                        ratios[rk] = representative[(cur_iid, rk)] / b
                if len(ratios) < 2:
                    continue
                vals = list(ratios.values())
                mf = median(vals)
                mn = mean(vals)
                disp = (stdev(vals) / mn) if mn > 0 else None
                coverage = len(ratios) / union_n
                candidates.append({
                    "offset": offset, "baseline_item_id": base_iid,
                    "baseline_run_finished_at": item_meta[base_iid][2].isoformat(),
                    "paired_room_count": len(ratios), "union_room_key_count": union_n,
                    "coverage": coverage, "median_factor": float(mf),
                    "dispersion": float(disp) if disp is not None else None,
                    "per_room_ratios": {rk: str(v) for rk, v in ratios.items()},
                    "passes_gate": (
                        coverage >= config["hotel_wide_min_coverage"]
                        and mf >= config["hotel_wide_min_factor"]
                        and disp is not None and disp <= config["hotel_wide_max_dispersion"]
                    ),
                })
            primary = next((c for c in candidates if c["passes_gate"]), None)
            if primary is None:
                continue
            other_baselines = [
                {k: v for k, v in c.items() if k != "per_room_ratios"}
                for c in candidates if c is not primary
            ]
            metrics = {
                "baseline_item_id": primary["baseline_item_id"],
                "baseline_source_code": None,  # set bang source_code cua chinh DB dang chay o main()
                "baseline_run_finished_at": primary["baseline_run_finished_at"],
                "paired_room_count": primary["paired_room_count"],
                "union_room_key_count": primary["union_room_key_count"],
                "coverage": primary["coverage"], "median_factor": primary["median_factor"],
                "dispersion": primary["dispersion"],
                "per_room_ratios": primary["per_room_ratios"],
                "other_baselines": other_baselines,
            }
            paired_keys = set(primary["per_room_ratios"].keys())
            evidence_at = max(item_meta[cur_iid][2], item_meta[primary["baseline_item_id"]][2])
            for room_key in paired_keys:
                for r in item_room_records[cur_iid][room_key]:
                    signals.append(SignalRow(
                        record_id=r["record_id"], signal_code="hotel_wide_level_shift",
                        severity="notable", record_observed_at=r["observed_at"],
                        evidence_available_at=evidence_at, metrics=metrics,
                    ))

    return signals


_INSERT_CHUNK_SIZE = 5000


def _apply(cursor, signals: list[SignalRow], config_hash: str, record_ids_in_scope: set[int]) -> dict:
    """Ghi config (neu chua co) + reconcile toan bo signal cua method_version nay trong dung scope
    da quet - insert/update signal moi, XOA signal cu khong con dung (anti-join, tranh NOT IN)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    cursor.execute(
        "SELECT config_sha256 FROM anomaly_signal_configs WHERE config_sha256=%s", (config_hash,)
    )
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO anomaly_signal_configs (config_sha256, method_version, config_json, created_at) "
            "VALUES (%s,%s,%s,%s)",
            (config_hash, METHOD_VERSION, json.dumps(CONFIG, sort_keys=True), now),
        )

    cursor.execute(
        "CREATE TEMPORARY TABLE tmp_current_signals (record_id BIGINT NOT NULL, signal_code VARCHAR(40) NOT NULL, "
        "PRIMARY KEY (record_id, signal_code))"
    )
    cursor.execute(
        "CREATE TEMPORARY TABLE tmp_scope_record_ids (record_id BIGINT PRIMARY KEY)"
    )
    try:
        scope_rows = [(rid,) for rid in record_ids_in_scope]
        for start in range(0, len(scope_rows), _INSERT_CHUNK_SIZE):
            cursor.executemany(
                "INSERT INTO tmp_scope_record_ids (record_id) VALUES (%s)",
                scope_rows[start:start + _INSERT_CHUNK_SIZE],
            )
        cursor.execute("SELECT COUNT(*) AS n FROM tmp_scope_record_ids")
        populated = cursor.fetchone()
        populated_n = populated["n"] if isinstance(populated, dict) else populated[0]
        if populated_n != len(record_ids_in_scope):
            raise RuntimeError(
                f"tmp_scope_record_ids populated {populated_n} nhung scope co {len(record_ids_in_scope)} - dung."
            )

        sig_rows = [(s.record_id, s.signal_code) for s in signals]
        for start in range(0, len(sig_rows), _INSERT_CHUNK_SIZE):
            cursor.executemany(
                "INSERT INTO tmp_current_signals (record_id, signal_code) VALUES (%s,%s)",
                sig_rows[start:start + _INSERT_CHUNK_SIZE],
            )
        cursor.execute("SELECT COUNT(*) AS n FROM tmp_current_signals")
        populated_sig = cursor.fetchone()
        populated_sig_n = populated_sig["n"] if isinstance(populated_sig, dict) else populated_sig[0]
        if populated_sig_n != len(sig_rows):
            raise RuntimeError(
                f"tmp_current_signals populated {populated_sig_n} nhung signals co {len(sig_rows)} - dung."
            )

        cursor.execute(
            """
            DELETE s FROM price_anomaly_signals s
            LEFT JOIN tmp_current_signals t
              ON t.record_id = s.record_id AND t.signal_code = s.signal_code
            INNER JOIN tmp_scope_record_ids sc ON sc.record_id = s.record_id
            WHERE s.method_version = %s AND t.record_id IS NULL
            """,
            (METHOD_VERSION,),
        )
        deleted = cursor.rowcount

        upsert_rows = [
            (
                s.record_id, METHOD_VERSION, s.signal_code, s.severity, config_hash,
                s.record_observed_at, s.evidence_available_at, now,
                json.dumps(s.metrics, sort_keys=True, default=str),
            )
            for s in signals
        ]
        for start in range(0, len(upsert_rows), _INSERT_CHUNK_SIZE):
            cursor.executemany(
                """
                INSERT INTO price_anomaly_signals
                  (record_id, method_version, signal_code, severity, config_sha256,
                   record_observed_at, evidence_available_at, computed_at, metrics_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  severity=VALUES(severity), config_sha256=VALUES(config_sha256),
                  record_observed_at=VALUES(record_observed_at),
                  evidence_available_at=VALUES(evidence_available_at),
                  computed_at=VALUES(computed_at), metrics_json=VALUES(metrics_json)
                """,
                upsert_rows[start:start + _INSERT_CHUNK_SIZE],
            )
    finally:
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_current_signals")
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_scope_record_ids")

    return {"upserted": len(sig_rows), "deleted_stale": deleted}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-code", required=True, help="Nhan cho baseline_source_code trong metrics (label, khong loc SQL).")
    parser.add_argument("--apply", action="store_true", help="Ghi signal that; mac dinh chi dry-run in bao cao (KHONG insert config, KHONG ghi gi).")
    args = parser.parse_args()

    config_hash = config_sha256(CONFIG)

    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            require_source_identity(cursor, args.source_code)
        except SourceIdentityError as exc:
            cursor.close()
            raise SystemExit(str(exc)) from exc

        rows = _load_scope(cursor)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        signals = compute_signals(rows, CONFIG, now)
        for s in signals:
            if s.signal_code == "hotel_wide_level_shift" and s.metrics.get("baseline_source_code") is None:
                s.metrics["baseline_source_code"] = args.source_code

        counts: dict[str, int] = defaultdict(int)
        for s in signals:
            counts[s.signal_code] += 1

        print(f"Method version: {METHOD_VERSION}  config_sha256: {config_hash}")
        print(f"Tong record trong pham vi (run completed + item success): {len(rows)}")
        for code in ("low_price_outlier", "context_level_high", "temporal_level_shift", "hotel_wide_level_shift"):
            print(f"  {code}: {counts.get(code, 0)}")
        print(f"Tong signal row (co the >1 signal/record): {len(signals)}")

        if not args.apply:
            print("\nDry run - khong ghi gi (khong insert config, khong ghi signal). Chay lai voi --apply.")
            cursor.close()
            return

        record_ids_in_scope = {r["record_id"] for r in rows}
        try:
            result = _apply(cursor, signals, config_hash, record_ids_in_scope)
        except Exception:
            conn.rollback()
            cursor.close()
            raise
        conn.commit()
        cursor.close()
        print(f"\nDa upsert {result['upserted']} signal row, xoa {result['deleted_stale']} signal cu khong con dung.")


if __name__ == "__main__":
    main()
