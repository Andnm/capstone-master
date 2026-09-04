"""Canonicalization/fingerprint/registry-replay helpers dung chung cho anomaly registry v2
(sync_anomaly_registry.py, reconcile_anomaly_projection.py, preview_anomaly_members.py,
daily_quality_monitor.py, API export). Tach khoi recompute_anomalies.py de registry replay khong
can import logic detector.

`validate_events`/`verify_db_matches_event_log`/`check_registry_integrity` song chung o day (khong
o sync_anomaly_registry.py) de check_registry_integrity() (dung boi consumer nhu API/monitor) co the
tai su dung DUNG 1 bo logic verify voi sync_anomaly_registry.py, khong phai tu viet lai rieng
(discuss/anomaly-v2-ground-truth/ file 21 M3).

Thiet ke chot qua discuss/anomaly-v2-ground-truth/ (PASS FOR DESIGN file 17, sua theo review
implementation file 19/21).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "database" / "anomaly_registry.json"


class RegistryError(Exception):
    pass


class SourceIdentityError(Exception):
    pass


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        raise ValueError(f"gia tri VND khong phai so nguyen trong fingerprint payload: {value}")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"khong the canonical-hoa kieu: {type(value)!r}")


def canonical_json(payload: Any) -> str:
    """UTF-8, sort_keys, khong whitespace thua, Decimal VND -> int, date/datetime -> ISO-8601."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_iso_utc(value: str) -> datetime:
    """'2026-09-04T10:39:43Z' -> naive UTC datetime, khop dung convention DB (time_zone='+00:00',
    cot DATETIME khong tu luu tzinfo)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def observation_fingerprint_payload(row: dict) -> dict:
    """Chieu chieu bat bien cua 1 dong price_observations + run/item cha - dung de chan tai su dung
    record_id sau khi DB bi reset/reseed (discuss file 15-16). CO Y bo qua field mutable/recomputed
    (is_anomaly, reference_match_status...)."""
    return {
        "source_run_id": row["crawl_run_id"],
        "source_item_id": row["crawl_run_item_id"],
        "source_record_id": row["record_id"],
        "hotel_id": row["hotel_id"],
        "observed_at": row["observed_at"],
        "checkin_date": row["checkin_date"],
        "checkout_date": row["checkout_date"],
        "room_option_index": row["room_option_index"],
        "room_identity_key": row["room_identity_key"],
        "rate_plan_key": row["rate_plan_key"],
        "price_total": row["price_total"],
        "price_per_night": row["price_per_night"],
    }


def observation_fingerprint(row: dict) -> str:
    return sha256_hex(canonical_json(observation_fingerprint_payload(row)))


_OBSERVATION_FINGERPRINT_COLUMNS = """
    po.record_id, po.hotel_id, po.crawl_run_id, po.crawl_run_item_id,
    po.observed_at, po.checkin_date, po.checkout_date, po.room_option_index,
    po.room_identity_key, po.rate_plan_key, po.price_total, po.price_per_night
"""

OBSERVATION_FINGERPRINT_QUERY = f"""
    SELECT {_OBSERVATION_FINGERPRINT_COLUMNS}
    FROM price_observations po
    WHERE po.record_id = %s
"""

_FINGERPRINT_BATCH_CHUNK = 500


def observation_fingerprint_batch_query(chunk_size: int) -> str:
    """Batch cua OBSERVATION_FINGERPRINT_QUERY - dung khi can recompute fingerprint cho nhieu
    record_id cung luc (discuss file 21 M1 diem 3: tranh N+1 khi registry lon)."""
    placeholders = ",".join(["%s"] * chunk_size)
    return f"""
        SELECT {_OBSERVATION_FINGERPRINT_COLUMNS}
        FROM price_observations po
        WHERE po.record_id IN ({placeholders})
    """


def event_payload_sha256(event: dict) -> str:
    """Hash CHINH payload cua 1 event (khong gom field ngoai event, vd published_at cua ca file) -
    them event moi vao file KHONG lam doi hash cua event cu (discuss file 13 M1)."""
    return sha256_hex(canonical_json(event))


def registry_file_sha256(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def member_key(member: dict) -> tuple[str, int]:
    return (member["source_code"], member["source_record_id"])


def members_match_exactly(a: list[dict], b: list[dict]) -> bool:
    """So 2 danh sach member co giong het (source_code, source_record_id, source_record_sha256)
    khong, khong quan tam thu tu - dung de ep supersede giu nguyen member set (discuss file 13/14)."""
    def _norm(members: list[dict]) -> set[tuple[str, int, str]]:
        return {(m["source_code"], m["source_record_id"], m["source_record_sha256"]) for m in members}
    return _norm(a) == _norm(b)


def checksum_of_pairs(pairs: list[tuple]) -> str:
    """SHA-256 cua danh sach tuple da sort - dung cho active_resolution_checksum/
    anomaly_projection_checksum (moi tuple da la kieu JSON-serializable: str/int/bool)."""
    ordered = sorted(pairs)
    return sha256_hex(canonical_json(ordered))


def local_member_checksum(members: list[dict]) -> str:
    """Hash CHI member local (khop 1 source_code) - member_count va member_checksum phai cung scope
    (discuss file 19 MIN2)."""
    return checksum_of_pairs([
        (m["source_code"], m["source_record_id"], m["source_record_sha256"]) for m in members
    ])


def require_source_identity(cursor, expected_source_code: str) -> None:
    """Doc anomaly_registry_source_identity va FAIL neu chua provision hoac khong khop
    expected_source_code - moi entrypoint ghi/doc theo source_code (sync/reconcile/recompute/
    preview) deu phai goi ham nay truoc khi lam gi khac (discuss file 19 M3: reconcile --apply
    truoc do khong verify identity, co the xoa nham co neu chay nham --source-code tren nham may)."""
    cursor.execute("SELECT source_code FROM anomaly_registry_source_identity WHERE id=1")
    row = cursor.fetchone()
    identity = row["source_code"] if row else None
    if identity is None:
        raise SourceIdentityError(
            "anomaly_registry_source_identity CHUA duoc provision cho DB nay - chay "
            "provision_anomaly_source_identity.py --source-code <X> truoc."
        )
    if identity != expected_source_code:
        raise SourceIdentityError(
            f"--source-code='{expected_source_code}' KHONG KHOP identity da provision cua DB nay "
            f"('{identity}') - co the ban dang chay nham lenh tren nham may. Dung."
        )


# =========================================================================================
# Registry file: load/validate (chuyen tu sync_anomaly_registry.py sang day - discuss file 21 M3,
# de check_registry_integrity() dung chung DUNG 1 bo logic voi sync_anomaly_registry.py)
# =========================================================================================
def load_registry(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != 1:
        raise RegistryError(f"schema_version khong duoc ho tro: {data.get('schema_version')!r}")
    if "declared_sources" not in data or not isinstance(data["declared_sources"], list):
        raise RegistryError("thieu declared_sources (mang) o top-level file.")
    events = data.get("events")
    if not isinstance(events, list):
        raise RegistryError("thieu 'events' (mang) o top-level file.")
    return data, raw


def validate_events(data: dict) -> list[dict]:
    """Validate toan bo file TRUOC khi apply bat ky event nao. Track ca review-level state (active/
    superseded/retracted) LAN member-level ownership (discuss file 21 M1: ban truoc chi track review
    state, nen 2 activate CHONG LEN cung 1 member van "hop le" o cap file - chi fail luc apply DB,
    tao partial sync tranh duoc ngay tu validation)."""
    declared = set(data["declared_sources"])
    events = sorted(data["events"], key=lambda e: e.get("sequence", -1))

    seen_sequences = set()
    seen_event_ids: set[str] = set()
    review_state: dict[str, str] = {}            # review_id -> 'active'|'superseded'|'retracted'
    review_members: dict[str, list[dict]] = {}     # review_id -> members cua CHINH event activate/supersede no
    member_owner: dict[tuple[str, int], str] = {}  # (source_code, source_record_id) -> review_id dang active
    prev_decided_at: datetime | None = None

    for idx, ev in enumerate(events, start=1):
        seq = ev.get("sequence")
        if seq != idx:
            raise RegistryError(
                f"sequence khong lien tuc/khong bat dau tu 1: mong {idx}, gap {seq!r} (event_id={ev.get('event_id')})."
            )
        if seq in seen_sequences:
            raise RegistryError(f"sequence trung: {seq}")
        seen_sequences.add(seq)

        event_id = ev.get("event_id")
        if event_id in seen_event_ids:
            raise RegistryError(f"sequence {seq}: event_id trung lap trong file: {event_id!r}")
        seen_event_ids.add(event_id)

        action = ev.get("action")
        if action not in ("activate", "supersede", "retract"):
            raise RegistryError(f"action khong hop le o sequence {seq}: {action!r}")

        members = ev.get("members", [])
        seen_member_keys: set[tuple[str, int]] = set()
        for m in members:
            if m["source_code"] not in declared:
                raise RegistryError(
                    f"sequence {seq}: member source_code='{m['source_code']}' khong nam trong "
                    f"declared_sources={sorted(declared)} - co the go nham, sua file truoc khi sync."
                )
            key = member_key(m)
            if key in seen_member_keys:
                raise RegistryError(
                    f"sequence {seq}: member (source_code={key[0]}, source_record_id={key[1]}) xuat "
                    f"hien NHIEU LAN trong CUNG 1 event (discuss file 21 MIN1 - ke ca khi 2 lan khai "
                    f"bao fingerprint khac nhau cho cung 1 ID, van la mau thuan/trung lap phai chan "
                    f"tu day, khong doi den luc INSERT vi pham PK o tang DB moi fail)."
                )
            seen_member_keys.add(key)

        review_id = ev.get("review_id")
        if action == "activate":
            if review_id in review_state:
                raise RegistryError(
                    f"sequence {seq}: review_id='{review_id}' da xuat hien truoc do trong file - "
                    f"activate chi duoc dung cho review_id MOI, chua tung ton tai."
                )
            for m in members:
                key = (m["source_code"], m["source_record_id"])
                if key in member_owner:
                    raise RegistryError(
                        f"sequence {seq}: activate review_id='{review_id}' CHONG LEN member "
                        f"(source_code={key[0]}, source_record_id={key[1]}) dang thuoc review "
                        f"'{member_owner[key]}' - can retract/supersede review do truoc khi activate "
                        f"chong len (chan tu validate, khong doi den luc apply DB moi fail)."
                    )
            review_state[review_id] = "active"
            review_members[review_id] = members
            for m in members:
                member_owner[(m["source_code"], m["source_record_id"])] = review_id
        elif action == "supersede":
            target = ev.get("supersedes_review_id")
            target_state = review_state.get(target)
            if target_state is None:
                raise RegistryError(
                    f"sequence {seq}: supersedes_review_id='{target}' chua tung duoc activate o "
                    f"sequence truoc do trong CHINH file nay."
                )
            if target_state != "active":
                raise RegistryError(
                    f"sequence {seq}: supersedes_review_id='{target}' dang o trang thai "
                    f"'{target_state}', KHONG PHAI 'active' - khong the supersede 1 review da "
                    f"superseded/retracted truoc do (stale target)."
                )
            if review_id in review_state:
                raise RegistryError(
                    f"sequence {seq}: review_id='{review_id}' cua chinh event supersede nay da ton "
                    f"tai truoc do - review_id moi phai la duy nhat."
                )
            if not members_match_exactly(members, review_members[target]):
                raise RegistryError(
                    f"sequence {seq}: supersede PHAI giu nguyen member set cua review bi supersede "
                    f"('{target}') - muon doi tap record phai retract roi activate lai bang review_id moi."
                )
            review_state[target] = "superseded"
            review_state[review_id] = "active"
            review_members[review_id] = members
            for m in members:
                member_owner[(m["source_code"], m["source_record_id"])] = review_id  # chuyen chu so huu
        elif action == "retract":
            target = ev.get("retracts_review_id")
            target_state = review_state.get(target)
            if target_state is None:
                raise RegistryError(
                    f"sequence {seq}: retracts_review_id='{target}' chua tung duoc activate o "
                    f"sequence truoc do trong CHINH file nay."
                )
            if target_state != "active":
                raise RegistryError(
                    f"sequence {seq}: retracts_review_id='{target}' dang o trang thai "
                    f"'{target_state}', KHONG PHAI 'active' - khong the retract 1 review da "
                    f"superseded/retracted truoc do (stale target, hoac retract trung lap)."
                )
            review_state[target] = "retracted"
            for key in [k for k, owner in member_owner.items() if owner == target]:
                del member_owner[key]

        # MIN2 (discuss file 21): so CHRONOLOGICAL (da parse), khong phai lexical string - string so
        # sanh chi dung neu moi timestamp cung 1 format UTC 'Z' co dinh; offset/precision khac nhau
        # co the lam thu tu string khac thu tu thoi gian that. parse_iso_utc() da tu xu ly ca offset
        # khac 'Z' (khong chi rieng hau to Z) roi quy ve UTC truoc khi so sanh.
        decided_at_raw = ev.get("decided_at")
        try:
            decided_at_parsed = parse_iso_utc(decided_at_raw)
        except (ValueError, TypeError, AttributeError) as exc:
            raise RegistryError(
                f"sequence {seq}: decided_at={decided_at_raw!r} khong parse duoc thanh ISO-8601 UTC "
                f"hop le (dinh dang mong doi vd '2026-09-04T10:00:00Z') - {exc}"
            ) from exc
        if prev_decided_at is not None and decided_at_parsed < prev_decided_at:
            raise RegistryError(
                f"sequence {seq}: decided_at ({decided_at_raw}, da parse={decided_at_parsed}) nho hon "
                f"event truoc do (da parse={prev_decided_at}) - decided_at phai khong giam dan theo "
                f"sequence."
            )
        prev_decided_at = decided_at_parsed

    return events


# =========================================================================================
# Expected-state replay + full verifier (discuss file 19 M2, mo rong theo file 21 M2: phai so DU
# CA 4 tang - applied events, decision (moi field), member (local set), resolution+projection -
# khong chi state/resolution/boolean nhu ban dau.
# =========================================================================================
def compute_expected_full_state_from_events(events: list[dict], this_source: str) -> dict:
    """Replay TOAN BO event (da validate) thanh trang thai MONG DOI DAY DU cho dung this_source -
    dung lam nguon that duy nhat khi verify DB (khong so DB voi DB - kiem tra vong tron da bi bat o
    file 19 M2). retract CHI xoa resolution dang THUC SU tro toi review bi retract (kiem tra CHU SO
    HUU HIEN HANH trong chinh dict resolutions dang xay dung, khong dung lai danh sach member GOC -
    tranh lap lai loi M1 o tang pure-Python nay, tung bi bat khi tu viet ham nay lan dau)."""
    decision_states: dict[str, str] = {}
    resolutions: dict[int, str] = {}
    expected_decisions: dict[str, dict] = {}
    expected_members: dict[str, list[dict]] = {}
    expected_events: dict[str, dict] = {}
    superseded_by: dict[str, str] = {}  # old review_id -> review_id thay the no (chi khi state='superseded')

    for ev in events:
        action = ev["action"]
        review_id = ev["review_id"]
        local_members = [
            {"source_code": m["source_code"], "source_record_id": m["source_record_id"],
             "source_record_sha256": m["source_record_sha256"]}
            for m in ev.get("members", []) if m["source_code"] == this_source
        ]
        local_ids = {m["source_record_id"] for m in local_members}

        if action == "retract":
            # retract KHONG mang field "members" (schema) - member_count "that" cua chinh event nay
            # la so member LOCAL cua review dang bi retract (da luu tu luc activate/supersede cua
            # NO), giong het cach apply_event() dem local_ids truoc khi DELETE resolutions. Dung
            # len(local_members) (luon =0 cho retract) o day se sai lech voi member_count that ma
            # main() ghi vao anomaly_registry_events_applied (tu gia tri tra ve cua apply_event()).
            event_member_count = len(expected_members.get(ev["retracts_review_id"], []))
        else:
            event_member_count = len(local_members)

        expected_events[ev["event_id"]] = {
            "sequence": ev["sequence"], "action": action, "review_id": review_id,
            "payload_sha256": event_payload_sha256(ev), "member_count": event_member_count,
        }

        if action in ("activate", "supersede"):
            if action == "supersede":
                old = ev["supersedes_review_id"]
                decision_states[old] = "superseded"
                superseded_by[old] = review_id
            decision_states[review_id] = "active"
            expected_members[review_id] = local_members
            expected_decisions[review_id] = {
                "decision": ev["decision"], "reason_code": ev["reason_code"],
                "rationale": ev["rationale"], "evidence": ev.get("evidence", {}),
                "reviewer": ev["reviewer"], "decided_at": parse_iso_utc(ev["decided_at"]),
                "member_count": len(local_members),
                "member_checksum": local_member_checksum(local_members),
            }
            for rid in local_ids:
                resolutions[rid] = review_id
        elif action == "retract":
            old = ev["retracts_review_id"]
            decision_states[old] = "retracted"
            for rid in [rid for rid, owner in resolutions.items() if owner == old]:
                del resolutions[rid]

    expected_true_ids = {
        rid for rid, review_id in resolutions.items()
        if decision_states.get(review_id) == "active"
        and expected_decisions.get(review_id, {}).get("decision") == "exclude_from_train"
    }

    # Union fingerprint mong doi cho TUNG record_id local, bat ke thuoc review nao - dung de recompute
    # tu price_observations THAT (discuss file 21 M1 diem 3), khong chi so voi hash da luu san trong
    # bang member (ca 2 co the CUNG giu hash CU neu observation bi sua SAU khi materialize). Injective
    # trong 1 registry da qua validate_events() (overlap-activate va supersede-giu-nguyen-member-set
    # da dam bao 1 record_id khong the co 2 fingerprint mong doi khac nhau).
    expected_member_fingerprints: dict[int, str] = {}
    for members in expected_members.values():
        for m in members:
            expected_member_fingerprints[m["source_record_id"]] = m["source_record_sha256"]

    return {
        "decision_states": decision_states,
        "resolutions": resolutions,
        "expected_decisions": expected_decisions,
        "expected_members": expected_members,
        "expected_events": expected_events,
        "expected_true_ids": expected_true_ids,
        "superseded_by": superseded_by,
        "expected_member_fingerprints": expected_member_fingerprints,
    }


def verify_db_matches_event_log(cursor, events: list[dict], this_source: str) -> list[str]:
    """Query DB THAT va so voi trang thai MONG DOI tu CHINH event log da validate. Tra list loi dang
    doc duoc; rong = DB khop hoan toan voi event log. So DU CA 4 tang (discuss file 21 M2):

    1. anomaly_registry_events_applied - khong thieu, khong thua, payload hash/sequence/action/
       review_id/member_count khop.
    2. anomaly_review_decisions - EXACT set review_id (ca chieu nguoc: decision "rogue" khong nam
       trong event log cung bi bat, khong chi thieu) + state + decision + reason_code + rationale +
       evidence_json (parse lai, khong so string) + reviewer + decided_at + member_count +
       member_checksum + superseded_by_review_id.
    3. anomaly_review_members (local) - EXACT set review_id co member local (ca nhom "rogue" khong
       duoc mong doi co member nao cung bi bat), dung set (source_record_id, fingerprint) cho tung
       review, khong thieu/thua/sai fingerprint.
    4. anomaly_review_resolutions + is_anomaly projection - nhu ban truoc.
    5. price_observations HIEN TAI - recompute observation_fingerprint() tu chinh row that (batch
       query, khong N+1) cho MOI record_id local mong doi, so voi source_record_sha256 khai bao
       trong event log - bat truong hop observation bi sua SAU khi review da materialize (member
       table van giu hash CU nen so voi no khong du, phai so voi du lieu HIEN TAI).

    Chay lai duoc bat cu luc nao (READ-ONLY, khong sua gi), ke ca khi khong co event moi nao duoc
    apply - day chinh la diem GPT yeu cau: 1 lan sync 'thanh cong' (ke ca toan bo la no-op) van phai
    CHUNG MINH duoc DB dung, khong chi tin cache cu."""
    expected = compute_expected_full_state_from_events(events, this_source)
    errors: list[str] = []

    # 1) applied events
    cursor.execute(
        "SELECT event_id, sequence_no, event_payload_sha256, action, review_id, member_count "
        "FROM anomaly_registry_events_applied"
    )
    actual_events = {r["event_id"]: r for r in cursor.fetchall()}
    for event_id, exp in expected["expected_events"].items():
        act = actual_events.get(event_id)
        if act is None:
            errors.append(f"event '{event_id}': KHONG co trong anomaly_registry_events_applied (thieu).")
            continue
        if act["event_payload_sha256"] != exp["payload_sha256"]:
            errors.append(
                f"event '{event_id}': payload_sha256 DB={act['event_payload_sha256']!r} "
                f"khac mong doi={exp['payload_sha256']!r}"
            )
        if act["sequence_no"] != exp["sequence"]:
            errors.append(f"event '{event_id}': sequence_no DB={act['sequence_no']} khac mong doi={exp['sequence']}")
        if act["action"] != exp["action"]:
            errors.append(f"event '{event_id}': action DB={act['action']!r} khac mong doi={exp['action']!r}")
        if act["review_id"] != exp["review_id"]:
            errors.append(f"event '{event_id}': review_id DB={act['review_id']!r} khac mong doi={exp['review_id']!r}")
        if act["member_count"] != exp["member_count"]:
            errors.append(
                f"event '{event_id}': member_count DB={act['member_count']} khac mong doi={exp['member_count']}"
            )
    for event_id in actual_events:
        if event_id not in expected["expected_events"]:
            errors.append(f"event '{event_id}': co trong DB nhung KHONG con trong event log hien tai (thua/mo coi).")

    # 2) decisions - EXACT set review_id (ca 2 chieu, discuss file 21 M1)
    cursor.execute(
        "SELECT review_id, decision, reason_code, rationale, evidence_json, reviewer, decided_at, "
        "state, member_count, member_checksum, superseded_by_review_id FROM anomaly_review_decisions"
    )
    actual_decisions = {r["review_id"]: r for r in cursor.fetchall()}
    for review_id, expected_state in expected["decision_states"].items():
        act = actual_decisions.get(review_id)
        if act is None:
            errors.append(f"decision '{review_id}': KHONG ton tai trong DB.")
            continue
        if act["state"] != expected_state:
            errors.append(f"decision '{review_id}': mong state='{expected_state}', DB co {act['state']!r}")
        expected_superseded_by = expected["superseded_by"].get(review_id)
        if act["superseded_by_review_id"] != expected_superseded_by:
            errors.append(
                f"decision '{review_id}': mong superseded_by_review_id={expected_superseded_by!r}, "
                f"DB co {act['superseded_by_review_id']!r}"
            )
        exp_fields = expected["expected_decisions"].get(review_id)
        if exp_fields is not None:  # retract khong co field nay - chi activate/supersede
            if act["decision"] != exp_fields["decision"]:
                errors.append(f"decision '{review_id}': mong decision='{exp_fields['decision']}', DB co {act['decision']!r}")
            if act["reason_code"] != exp_fields["reason_code"]:
                errors.append(f"decision '{review_id}': mong reason_code='{exp_fields['reason_code']}', DB co {act['reason_code']!r}")
            if act["rationale"] != exp_fields["rationale"]:
                errors.append(f"decision '{review_id}': rationale khac mong doi.")
            actual_evidence = act["evidence_json"]
            if isinstance(actual_evidence, str):
                actual_evidence = json.loads(actual_evidence)
            if actual_evidence != exp_fields["evidence"]:
                errors.append(f"decision '{review_id}': evidence_json khac mong doi.")
            if act["reviewer"] != exp_fields["reviewer"]:
                errors.append(f"decision '{review_id}': mong reviewer='{exp_fields['reviewer']}', DB co {act['reviewer']!r}")
            if act["decided_at"] != exp_fields["decided_at"]:
                errors.append(f"decision '{review_id}': mong decided_at={exp_fields['decided_at']}, DB co {act['decided_at']}")
            if act["member_count"] != exp_fields["member_count"]:
                errors.append(f"decision '{review_id}': mong member_count={exp_fields['member_count']}, DB co {act['member_count']}")
            if act["member_checksum"] != exp_fields["member_checksum"]:
                errors.append(f"decision '{review_id}': member_checksum khac mong doi.")
    for review_id in actual_decisions:
        if review_id not in expected["decision_states"]:
            errors.append(
                f"decision '{review_id}': co trong DB nhung KHONG nam trong event log hien tai "
                f"(rogue/mo coi - khong duoc bat ky event nao tao ra)."
            )

    # 3) members (local) - EXACT set review_id co member local (ca 2 chieu, discuss file 21 M1)
    cursor.execute(
        "SELECT review_id, source_record_id, source_record_sha256 FROM anomaly_review_members "
        "WHERE source_code=%s",
        (this_source,),
    )
    actual_members_by_review: dict[str, set] = {}
    for r in cursor.fetchall():
        actual_members_by_review.setdefault(r["review_id"], set()).add(
            (r["source_record_id"], r["source_record_sha256"])
        )
    for review_id, exp_members in expected["expected_members"].items():
        exp_set = {(m["source_record_id"], m["source_record_sha256"]) for m in exp_members}
        act_set = actual_members_by_review.get(review_id, set())
        if exp_set != act_set:
            missing = exp_set - act_set
            extra = act_set - exp_set
            if missing:
                errors.append(f"member '{review_id}': THIEU trong DB: {sorted(missing)}")
            if extra:
                errors.append(f"member '{review_id}': THUA/SAI trong DB: {sorted(extra)}")
    for review_id, act_set in actual_members_by_review.items():
        if review_id not in expected["expected_members"]:
            errors.append(
                f"member '{review_id}': co {len(act_set)} member local trong DB nhung review nay "
                f"KHONG nam trong event log hien tai (rogue/mo coi ca nhom)."
            )

    # 4) resolutions + projection
    cursor.execute(
        "SELECT source_record_id, review_id FROM anomaly_review_resolutions WHERE source_code=%s",
        (this_source,),
    )
    actual_resolutions = {r["source_record_id"]: r["review_id"] for r in cursor.fetchall()}
    for rid, expected_rv in expected["resolutions"].items():
        actual_rv = actual_resolutions.get(rid)
        if actual_rv != expected_rv:
            errors.append(f"resolution record={rid}: mong review_id='{expected_rv}', DB co {actual_rv!r}")
    for rid, actual_rv in actual_resolutions.items():
        if rid not in expected["resolutions"]:
            errors.append(
                f"resolution record={rid}: DB co review_id='{actual_rv}' nhung event log KHONG "
                f"mong doi resolution nao cho record nay (resolution mo coi/thua)."
            )

    cursor.execute("SELECT record_id, is_anomaly FROM price_observations")
    for row in cursor.fetchall():
        rid = row["record_id"]
        expected_true = rid in expected["expected_true_ids"]
        actual_true = bool(row["is_anomaly"])
        if expected_true and not actual_true:
            errors.append(f"projection record={rid}: mong is_anomaly=TRUE (theo event log), DB dang FALSE")
        elif actual_true and not expected_true:
            errors.append(f"projection record={rid}: mong is_anomaly=FALSE (theo event log), DB dang TRUE")

    # 5) fingerprint HIEN TAI cua price_observations - recompute tu chinh row that (KHONG chi so voi
    # hash da luu san trong anomaly_review_members, vi ca 2 co the CUNG giu hash CU neu observation bi
    # sua SAU khi materialize - discuss file 21 M1 diem 3). Batch theo _FINGERPRINT_BATCH_CHUNK de
    # tranh N+1 khi registry lon.
    expected_fp = expected["expected_member_fingerprints"]
    record_ids = sorted(expected_fp.keys())
    actual_rows: dict[int, dict] = {}
    for start in range(0, len(record_ids), _FINGERPRINT_BATCH_CHUNK):
        chunk = record_ids[start:start + _FINGERPRINT_BATCH_CHUNK]
        cursor.execute(observation_fingerprint_batch_query(len(chunk)), tuple(chunk))
        for row in cursor.fetchall():
            actual_rows[row["record_id"]] = row
    for rid, exp_fp in expected_fp.items():
        row = actual_rows.get(rid)
        if row is None:
            errors.append(
                f"observation record={rid}: KHONG TON TAI trong price_observations (co the da bi "
                f"xoa/reset) - khong the verify lai fingerprint hien tai voi registry."
            )
            continue
        actual_fp = observation_fingerprint(row)
        if actual_fp != exp_fp:
            errors.append(
                f"observation record={rid}: fingerprint HIEN TAI ({actual_fp}) khac voi luc "
                f"materialize trong registry ({exp_fp}) - du lieu price_observations co the da bi "
                f"sua SAU khi review nay duoc quyet dinh."
            )

    return errors


def check_registry_integrity(
    cursor, source_code: str | None = None, registry_path: Path = DEFAULT_REGISTRY_PATH
) -> dict:
    """Kiem tra is_anomaly cua source_code nay co dang tin duoc khong TRUOC KHI bat ky consumer nao
    (daily_quality_monitor.py, export API) doc no - discuss file 19 M4, mo rong theo file 21 M3
    ("chi kiem 'bien nhan' sync khong du - phai kiem DB HIEN TAI, vi drift SAU lan sync thanh cong
    van lot qua"). KHONG raise - tra dict co "ok": bool de caller tu quyet dinh phan ung (WARN gate
    hay HTTP 409/503).

    2 buoc: (1) "bien nhan" - co sync-run status='success' khop dung file hash hien tai khong (re,
    fail nhanh neu chua ai sync dung file nay); (2) NEU (1) qua, chay full verify_db_matches_event_log
    - bat drift xay ra SAU lan sync thanh cong do (vd ai do sua tay resolution/decision/member).

    source_code=None: tu doc identity da provision va dung LUON, khong so sanh - hop ly cho consumer
    noi bo nhu API (chi bao gio noi voi DB cua chinh no, khong co rui ro "--source-code sai" nhu CLI
    script). Truyen source_code ro rang khi can validate giong CLI (vd tu 1 wrapper script)."""
    result: dict[str, Any] = {
        "ok": False, "reason": None, "registry_file_sha256": None,
        "source_code": source_code, "identity_provisioned": False,
        "identity_matches": None, "last_sync_status": None, "last_sync_file_sha256": None,
        "drift_errors": None,
    }

    cursor.execute("SELECT source_code FROM anomaly_registry_source_identity WHERE id=1")
    row = cursor.fetchone()
    identity = row["source_code"] if row else None
    result["identity_provisioned"] = identity is not None
    if identity is None:
        result["reason"] = "anomaly_registry_source_identity chua duoc provision cho DB nay."
        return result

    if source_code is None:
        source_code = identity
        result["source_code"] = source_code
    result["identity_matches"] = identity == source_code
    if identity != source_code:
        result["reason"] = (
            f"source_code='{source_code}' khong khop identity da provision cua DB nay ('{identity}')."
        )
        return result

    if not registry_path.exists():
        result["reason"] = f"khong tim thay file registry tai {registry_path}."
        return result
    current_hash = registry_file_sha256(registry_path.read_bytes())
    result["registry_file_sha256"] = current_hash

    cursor.execute(
        """
        SELECT status, registry_file_sha256 FROM anomaly_registry_sync_runs
        WHERE source_code=%s ORDER BY sync_id DESC LIMIT 1
        """,
        (source_code,),
    )
    latest = cursor.fetchone()
    if latest is None:
        result["reason"] = "chua tung chay sync_anomaly_registry.py --apply tren DB nay."
        return result
    result["last_sync_status"] = latest["status"]
    result["last_sync_file_sha256"] = latest["registry_file_sha256"]
    if latest["status"] != "success":
        result["reason"] = f"lan sync gan nhat co status='{latest['status']}', khong phai 'success'."
        return result
    if latest["registry_file_sha256"] != current_hash:
        result["reason"] = (
            f"lan sync THANH CONG gan nhat dung registry_file_sha256="
            f"{latest['registry_file_sha256'][:12]}... nhung file anomaly_registry.json HIEN TAI la "
            f"{current_hash[:12]}... - file da doi nhung chua sync lai tren DB nay."
        )
        return result

    # "Bien nhan" hop le - gio kiem DB HIEN TAI co con khop khong (bat drift sau lan sync thanh cong).
    try:
        data, _raw = load_registry(registry_path)
        events = validate_events(data)
    except RegistryError as exc:
        result["reason"] = f"registry file hien tai khong con hop le: {exc}"
        return result
    drift_errors = verify_db_matches_event_log(cursor, events, source_code)
    if drift_errors:
        result["drift_errors"] = drift_errors[:20]
        result["reason"] = (
            f"da sync thanh cong nhung DB HIEN TAI da drift khoi event log ({len(drift_errors)} loi) "
            f"- co the ai do sua tay resolution/decision/member ngoai script."
        )
        return result

    result["ok"] = True
    return result
