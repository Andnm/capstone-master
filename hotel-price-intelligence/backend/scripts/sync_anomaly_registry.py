"""Replay anomaly_registry.json (event log append-only) vao DB vận hành CỦA CHÍNH NÓ.

File la nguon su that DUY NHAT cho quyet dinh review (exclude_from_train/keep_as_valid/needs_review),
chua CONCRETE member (source_code, source_record_id, source_record_sha256) - khong phai selector dong
(selector chi la audit evidence, KHONG bao gio duoc chay lai de suy membership - discuss file 13 M1).

Moi DB CHI materialize member co source_code khop chinh no (THIS_SOURCE_CODE, doc tu
anomaly_registry_source_identity - phai provision truoc bang provision_anomaly_source_identity.py).
Member cua nguon KHAC trong file la HOP LE va duoc bo qua co chu dich, KHONG phai loi (discuss file
15 M1 - dung nham lan nay tung lam sync khong bao gio thanh cong khi file co nhieu nguon).

Fingerprint (source_record_sha256) duoc verify lai TU DU LIEU HIEN TAI truoc khi materialize - neu
record_id ton tai nhung noi dung khac (vd DB bi reset/reseed, id bi tai su dung) thi FAIL CLOSED,
khong ap verdict nham vao quan sat khac (discuss file 15-16).

`RegistryError`/`load_registry`/`validate_events`/`compute_expected_full_state_from_events`/
`verify_db_matches_event_log` song trong `app/scraper/anomaly_registry_lib.py`, KHONG o day nua -
de `check_registry_integrity()` (dung boi daily_quality_monitor.py va API export) tai su dung DUNG
1 bo logic voi script nay, khong phai 2 ban song song co the driff nhau (discuss file 21 M3).

Run (tu backend/):
    python scripts/sync_anomaly_registry.py --source-code local_primary --apply
    python scripts/sync_anomaly_registry.py --source-code local_primary --apply \\
        --registry-file path/khac/anomaly_registry.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # cho phep import reconcile_anomaly_projection
# du script duoc load truc tiep (python scripts/x.py, tu them scripts/ vao sys.path[0]) hay dynamic
# import qua importlib (nhu trong test) - truong hop sau khong tu them scripts/ vao sys.path.

from app.core.database import get_db_connection
from app.scraper.anomaly_registry_lib import (
    DEFAULT_REGISTRY_PATH,
    OBSERVATION_FINGERPRINT_QUERY,
    RegistryError,
    SourceIdentityError,
    event_payload_sha256,
    load_registry,
    local_member_checksum,
    observation_fingerprint,
    parse_iso_utc,
    registry_file_sha256,
    require_source_identity,
    validate_events,
    verify_db_matches_event_log,
)
from reconcile_anomaly_projection import (
    compute_active_resolution_checksum,
    compute_anomaly_projection_checksum,
)


def verify_and_prepare_local_members(cursor, members: list[dict], this_source: str) -> list[dict]:
    """Loc member khop THIS_SOURCE_CODE, verify record ton tai + fingerprint khop. FAIL CLOSED neu
    khong khop - tra ve danh sach da kem du lieu row that (dung de tinh checksum/log)."""
    local = [m for m in members if m["source_code"] == this_source]
    prepared = []
    for m in local:
        cursor.execute(OBSERVATION_FINGERPRINT_QUERY, (m["source_record_id"],))
        row = cursor.fetchone()
        if row is None:
            raise RegistryError(
                f"record_id={m['source_record_id']} (source_code={this_source}) KHONG TON TAI trong "
                f"DB nay - khong the materialize. Neu day la lan dau chay tren DB nay, kiem tra lai "
                f"dung DB/dung run da duoc quet chua."
            )
        actual_fp = observation_fingerprint(row)
        if actual_fp != m["source_record_sha256"]:
            raise RegistryError(
                f"record_id={m['source_record_id']} TON TAI nhung fingerprint KHONG KHOP evidence "
                f"goc trong registry (mong {m['source_record_sha256']}, thuc te {actual_fp}) - co the "
                f"do DB bi reset/reseed va record_id bi tai su dung cho quan sat khac. KHONG "
                f"materialize - can review lai thu cong truoc khi retry."
            )
        prepared.append({**m, "_row": row})
    return prepared


def refresh_projection_for_records(cursor, record_ids: list[int], is_anomaly: bool) -> None:
    if not record_ids:
        return
    for start in range(0, len(record_ids), 5000):
        chunk = record_ids[start:start + 5000]
        placeholders = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"UPDATE price_observations SET is_anomaly=%s WHERE record_id IN ({placeholders})",
            (is_anomaly, *chunk),
        )


def find_payload_drifted_events(events: list[dict], applied_rows: dict[str, str]) -> list[dict]:
    """Pure - trong so event trong file DA duoc apply truoc do (co trong applied_rows: event_id ->
    event_payload_sha256 da luu luc apply) nhung noi dung HIEN TAI khac hash da luu - vi pham
    append-only (event da publish khong duoc sua). Dung boi main()'s --dry-run de bao drift SOM,
    truoc khi --apply that su raise (discuss file 21 M2: dry-run truoc chi in so pending, khong phat
    hien duoc case nay cho den luc --apply chay that)."""
    return [
        ev for ev in events
        if ev["event_id"] in applied_rows and event_payload_sha256(ev) != applied_rows[ev["event_id"]]
    ]


def _require_rowcount(cursor, expected: int, context: str) -> None:
    if cursor.rowcount != expected:
        raise RegistryError(
            f"{context}: mong {expected} dong bi anh huong, thuc te {cursor.rowcount} - "
            f"dung, khong tiep tuc (defense-in-depth rowcount check, discuss file 19 M1)."
        )


def _insert_decision(cursor, review_id, ev, state, member_count, member_checksum, now):
    cursor.execute(
        """
        INSERT INTO anomaly_review_decisions
          (review_id, decision, reason_code, rationale, evidence_json, reviewer, decided_at,
           state, member_count, member_checksum, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            review_id, ev["decision"], ev["reason_code"], ev["rationale"],
            json.dumps(ev.get("evidence", {}), ensure_ascii=False), ev["reviewer"],
            ev["decided_at"], state, member_count, member_checksum, now,
        ),
    )


def _reject_overlapping_activate(cursor, this_source: str, record_ids: list[int], review_id: str) -> None:
    """activate KHONG duoc chong len record dang co resolution active tro toi review KHAC (discuss
    file 19 M1 - "activate khong duoc chong len member dang co resolution"). Day la lop phong thu
    THU HAI o cap DB - validate_events() trong lib da chan phan lon case nay ngay tu file-validation
    (discuss file 21 M1), nhung DB co the drift so voi file do can thiep tay ngoai script."""
    if not record_ids:
        return
    placeholders = ",".join(["%s"] * len(record_ids))
    cursor.execute(
        f"SELECT source_record_id, review_id FROM anomaly_review_resolutions "
        f"WHERE source_code=%s AND source_record_id IN ({placeholders})",
        (this_source, *record_ids),
    )
    conflicts = cursor.fetchall()
    if conflicts:
        detail = ", ".join(f"record={c['source_record_id']}->review={c['review_id']}" for c in conflicts)
        raise RegistryError(
            f"activate review_id={review_id}: {len(conflicts)} record DA co resolution active tro "
            f"toi review khac ({detail}) - khong the activate chong len, can retract/supersede "
            f"review cu truoc."
        )


def apply_event(cursor, ev: dict, this_source: str, now: datetime) -> int:
    """Ap dung 1 event trong transaction cua CHINH cursor nay (caller commit). Tra ve member_count
    da materialize/anh huong o CHINH DB nay (co the = 0 neu event khong lien quan source nay).

    Moi UPDATE/DELETE lam thay doi resolutions/decisions deu kem dieu kien scope day du (bao gom
    review_id cu, khong chi source_record_id) + kiem tra rowcount - chan dung ca 2 loi that GPT tim
    ra (file 19 M1): retract xoa nham resolution cua review KHAC (da tung supersede truoc do), va
    supersede/retract nham vao 1 review da khong con active (stale target - validate_events() da
    chan tu file, nhung day la lop phong thu thu 2 o chinh DB, phong truong hop DB drift so voi file
    do can thiep tay ngoai script)."""
    action = ev["action"]
    review_id = ev["review_id"]
    ev = {**ev, "decided_at": parse_iso_utc(ev["decided_at"])}

    if action == "activate":
        prepared = verify_and_prepare_local_members(cursor, ev.get("members", []), this_source)
        record_ids = [m["source_record_id"] for m in prepared]
        _reject_overlapping_activate(cursor, this_source, record_ids, review_id)
        _insert_decision(cursor, review_id, ev, "active", len(prepared), local_member_checksum(prepared), now)
        for m in prepared:
            cursor.execute(
                "INSERT INTO anomaly_review_members "
                "(review_id, source_code, source_record_id, source_record_sha256, materialized_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (review_id, this_source, m["source_record_id"], m["source_record_sha256"], now),
            )
            cursor.execute(
                "INSERT INTO anomaly_review_resolutions "
                "(source_code, source_record_id, review_id, resolved_at) VALUES (%s,%s,%s,%s)",
                (this_source, m["source_record_id"], review_id, now),
            )
        refresh_projection_for_records(cursor, record_ids, ev["decision"] == "exclude_from_train")
        return len(prepared)

    if action == "supersede":
        old_review_id = ev["supersedes_review_id"]
        cursor.execute(
            "SELECT source_record_id FROM anomaly_review_members WHERE review_id=%s AND source_code=%s",
            (old_review_id, this_source),
        )
        old_local_ids = [r["source_record_id"] for r in cursor.fetchall()]
        prepared = verify_and_prepare_local_members(cursor, ev.get("members", []), this_source)
        prepared_ids = {m["source_record_id"] for m in prepared}
        if prepared_ids != set(old_local_ids):
            raise RegistryError(
                f"supersede review_id={review_id}: member local moi khong khop het voi member local "
                f"cua review cu '{old_review_id}' - vi pham rang buoc giu nguyen member set."
            )

        cursor.execute(
            "UPDATE anomaly_review_decisions SET state='superseded', superseded_by_review_id=%s "
            "WHERE review_id=%s AND state='active'",
            (review_id, old_review_id),
        )
        _require_rowcount(cursor, 1, f"supersede {old_review_id}: UPDATE state cu sang superseded")

        if not old_local_ids:
            # review cu chua bao gio apply len DB nay (0 member local o lan activate) -> supersede
            # cung khong co gi de doi resolution, chi ghi decision moi voi 0 member.
            _insert_decision(cursor, review_id, ev, "active", 0, local_member_checksum([]), now)
            return 0

        _insert_decision(cursor, review_id, ev, "active", len(prepared), local_member_checksum(prepared), now)
        record_ids = []
        for m in prepared:
            cursor.execute(
                "INSERT INTO anomaly_review_members "
                "(review_id, source_code, source_record_id, source_record_sha256, materialized_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                (review_id, this_source, m["source_record_id"], m["source_record_sha256"], now),
            )
            cursor.execute(
                "UPDATE anomaly_review_resolutions SET review_id=%s, resolved_at=%s "
                "WHERE source_code=%s AND source_record_id=%s AND review_id=%s",
                (review_id, now, this_source, m["source_record_id"], old_review_id),
            )
            _require_rowcount(
                cursor, 1,
                f"supersede {old_review_id}->{review_id}: UPDATE resolution record={m['source_record_id']}"
                f" (khong con tro toi '{old_review_id}' - co the da bi doi boi thao tac khac)",
            )
            record_ids.append(m["source_record_id"])
        refresh_projection_for_records(cursor, record_ids, ev["decision"] == "exclude_from_train")
        return len(prepared)

    if action == "retract":
        target = ev["retracts_review_id"]
        cursor.execute(
            "SELECT source_record_id FROM anomaly_review_members WHERE review_id=%s AND source_code=%s",
            (target, this_source),
        )
        local_ids = [r["source_record_id"] for r in cursor.fetchall()]
        cursor.execute(
            "UPDATE anomaly_review_decisions SET state='retracted' WHERE review_id=%s AND state='active'",
            (target,),
        )
        _require_rowcount(cursor, 1, f"retract {target}: UPDATE state sang retracted")

        if local_ids:
            placeholders = ",".join(["%s"] * len(local_ids))
            cursor.execute(
                f"DELETE FROM anomaly_review_resolutions "
                f"WHERE source_code=%s AND review_id=%s AND source_record_id IN ({placeholders})",
                (this_source, target, *local_ids),
            )
            _require_rowcount(
                cursor, len(local_ids),
                f"retract {target}: DELETE resolutions (mot vai record co the da tro toi review khac "
                f"qua supersede - retract khong duoc dung nham vao chung)",
            )
            refresh_projection_for_records(cursor, local_ids, False)
        return len(local_ids)

    raise RegistryError(f"action khong duoc ho tro: {action!r}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-code", required=True)
    parser.add_argument("--registry-file", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--apply", action="store_true", help="Ghi that; mac dinh chi validate + dry list, khong ghi gi.")
    args = parser.parse_args()

    data, raw = load_registry(args.registry_file)
    file_hash = registry_file_sha256(raw)
    events = validate_events(data)
    print(f"registry_file_sha256: {file_hash}")
    print(f"declared_sources: {data['declared_sources']}")
    print(f"tong so event trong file: {len(events)}")

    with get_db_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        try:
            require_source_identity(cursor, args.source_code)
        except SourceIdentityError as exc:
            cursor.close()
            raise SystemExit(str(exc)) from exc

        if not args.apply:
            # Dry-run van phai bao duoc payload-hash drift tren event DA apply truoc do (discuss
            # file 21 M2: ban truoc chi in so pending, khong phat hien duoc neu ai do da sua file sau
            # khi 1 event da publish - vi pham append-only ma khong ai biet cho den luc --apply
            # thuc su chay va raise).
            cursor.execute(
                "SELECT event_id, event_payload_sha256 FROM anomaly_registry_events_applied ORDER BY sequence_no"
            )
            applied_rows = {r["event_id"]: r["event_payload_sha256"] for r in cursor.fetchall()}
            pending = [e for e in events if e["event_id"] not in applied_rows]
            drifted = find_payload_drifted_events(events, applied_rows)
            print(f"Da apply truoc do: {len(applied_rows)} event. Con {len(pending)} event CHUA apply.")
            if drifted:
                print(
                    f"CANH BAO: {len(drifted)} event DA apply truoc do nhung noi dung file HIEN TAI "
                    f"khac voi luc apply (vi pham append-only, --apply se raise ngay khi gap):"
                )
                for e in drifted:
                    print(f"  - {e['event_id']} (sequence {e['sequence']})")
            print("Dry run - khong ghi gi. Chay lai voi --apply.")
            cursor.close()
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cursor.execute(
            """
            INSERT INTO anomaly_registry_sync_runs
              (source_code, registry_file_sha256, started_at, status, expected_event_count)
            VALUES (%s,%s,%s,'running',%s)
            """,
            (args.source_code, file_hash, now, len(events)),
        )
        conn.commit()
        sync_id = cursor.lastrowid

        applied_through = None
        try:
            for ev in events:
                seq = ev["sequence"]
                event_id = ev["event_id"]
                payload_hash = event_payload_sha256(ev)

                cursor.execute(
                    "SELECT event_payload_sha256 FROM anomaly_registry_events_applied WHERE event_id=%s",
                    (event_id,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    if existing["event_payload_sha256"] != payload_hash:
                        raise RegistryError(
                            f"event_id='{event_id}' (sequence {seq}) DA duoc apply truoc do voi noi "
                            f"dung KHAC - event da publish khong duoc sua (vi pham append-only)."
                        )
                    applied_through = seq
                    continue  # idempotent no-op

                member_count = apply_event(cursor, ev, args.source_code, now)
                cursor.execute(
                    """
                    INSERT INTO anomaly_registry_events_applied
                      (event_id, sequence_no, event_payload_sha256, action, review_id, member_count, applied_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (event_id, seq, payload_hash, ev["action"], ev["review_id"], member_count, now),
                )
                conn.commit()
                applied_through = seq
                print(f"  [{seq}/{len(events)}] {event_id} ({ev['action']}) -> {member_count} record local")

            # M2 fix (discuss file 19, mo rong file 21): KHONG so is_anomaly voi resolutions (vong
            # tron, ca 2 co the cung drift ma van "khop"). Thay bang replay TOAN BO event log thanh
            # trang thai mong doi DAY DU (events_applied + decision moi field + member + resolution +
            # projection) roi so voi DB THAT - chay du toan bo event trong lan nay la no-op
            # (idempotent skip) hay khong, van luon verify lai tu dau, khong tin cache "da apply".
            mismatch_errors = verify_db_matches_event_log(cursor, events, args.source_code)
            if mismatch_errors:
                detail = "\n  - ".join(mismatch_errors[:20])
                more = f"\n  ... va {len(mismatch_errors) - 20} loi khac" if len(mismatch_errors) > 20 else ""
                raise RegistryError(
                    f"Sau khi replay het event, DB KHONG khop voi event log ({len(mismatch_errors)} "
                    f"loi) - dung, khong danh dau success:\n  - {detail}{more}"
                )

            active_checksum = compute_active_resolution_checksum(cursor, args.source_code)
            projection_checksum = compute_anomaly_projection_checksum(cursor)
            cursor.execute(
                """
                UPDATE anomaly_registry_sync_runs
                SET status='success', finished_at=%s, applied_through_sequence=%s,
                    active_resolution_checksum=%s, anomaly_projection_checksum=%s
                WHERE sync_id=%s
                """,
                (datetime.now(timezone.utc).replace(tzinfo=None), applied_through,
                 active_checksum, projection_checksum, sync_id),
            )
            conn.commit()
            print(f"\nSync THANH CONG. applied_through_sequence={applied_through}")
            print(f"active_resolution_checksum: {active_checksum}")
            print(f"anomaly_projection_checksum: {projection_checksum}")
        except Exception as exc:
            conn.rollback()
            cursor2 = conn.cursor()
            cursor2.execute(
                "UPDATE anomaly_registry_sync_runs SET status='failed', finished_at=%s, "
                "applied_through_sequence=%s, error_message=%s WHERE sync_id=%s",
                (datetime.now(timezone.utc).replace(tzinfo=None), applied_through, str(exc), sync_id),
            )
            conn.commit()
            cursor2.close()
            cursor.close()
            raise
        cursor.close()


if __name__ == "__main__":
    main()
