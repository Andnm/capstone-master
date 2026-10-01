"""Canonicalization/fingerprint - dung de chan tai su dung technical ID sau reset/reseed DB va dam
bao event hash on dinh khi file duoc append them event moi (discuss/anomaly-v2-ground-truth/ file
15-16)."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.scraper.anomaly_registry_lib import (
    canonical_json,
    check_registry_integrity,
    checksum_of_pairs,
    event_payload_sha256,
    local_member_checksum,
    members_match_exactly,
    observation_fingerprint,
    observation_fingerprint_payload,
    registry_file_sha256,
    sha256_hex,
)


def _row(**overrides):
    base = {
        "record_id": 64359, "hotel_id": "lumina-dalat-premium", "crawl_run_id": 12,
        "crawl_run_item_id": 345, "observed_at": datetime(2026, 8, 20, 7, 22, 48),
        "checkin_date": date(2026, 9, 6), "checkout_date": date(2026, 9, 7),
        "room_option_index": 3, "room_identity_key": "2583b58e" + "0" * 56,
        "rate_plan_key": "d943586d" + "0" * 56, "price_total": Decimal("90000000"),
        "price_per_night": Decimal("90000000"),
    }
    base.update(overrides)
    return base


def test_canonical_json_sorts_keys_deterministically():
    a = canonical_json({"b": 1, "a": 2})
    b = canonical_json({"a": 2, "b": 1})
    assert a == b
    assert a.index('"a"') < a.index('"b"')


def test_canonical_json_decimal_whole_number_becomes_int():
    assert canonical_json({"price": Decimal("90000000")}) == '{"price":90000000}'


def test_canonical_json_decimal_fractional_raises():
    with pytest.raises(ValueError):
        canonical_json({"price": Decimal("90000000.50")})


def test_canonical_json_date_and_datetime_iso_format():
    out = canonical_json({"d": date(2026, 9, 6), "dt": datetime(2026, 8, 20, 7, 22, 48)})
    assert '"2026-09-06"' in out
    assert '"2026-08-20T07:22:48"' in out


def test_observation_fingerprint_stable_for_same_row():
    row = _row()
    assert observation_fingerprint(row) == observation_fingerprint(dict(row))


def test_observation_fingerprint_changes_when_price_differs():
    row_a = _row(price_per_night=Decimal("90000000"))
    row_b = _row(price_per_night=Decimal("76500000"))
    assert observation_fingerprint(row_a) != observation_fingerprint(row_b)


def test_observation_fingerprint_changes_when_record_id_reused_for_different_observation():
    """Case that discuss tim ra: DB reset/reseed co the tai su dung record_id cho quan sat khac -
    fingerprint PHAI khac de sync_anomaly_registry.py fail-closed thay vi ap nham verdict."""
    original = _row(record_id=64359, hotel_id="lumina-dalat-premium", price_per_night=Decimal("90000000"))
    reused_id_different_observation = _row(
        record_id=64359, hotel_id="some-other-hotel", price_per_night=Decimal("500000"),
        observed_at=datetime(2027, 1, 1, 0, 0, 0),
    )
    assert observation_fingerprint(original) != observation_fingerprint(reused_id_different_observation)


def test_observation_fingerprint_payload_excludes_mutable_fields():
    """Payload CHI gom field bat bien - is_anomaly/reference_match_status khong duoc anh huong hash,
    vi day la metadata mutable/recomputed, khong phai danh tinh quan sat."""
    payload = observation_fingerprint_payload(_row())
    assert "is_anomaly" not in payload
    assert "reference_match_status" not in payload
    assert "reference_match_score" not in payload


def test_members_match_exactly_ignores_order():
    a = [{"source_code": "local_primary", "source_record_id": 1, "source_record_sha256": "x"},
         {"source_code": "local_primary", "source_record_id": 2, "source_record_sha256": "y"}]
    b = list(reversed(a))
    assert members_match_exactly(a, b) is True


def test_members_match_exactly_detects_different_set():
    a = [{"source_code": "local_primary", "source_record_id": 1, "source_record_sha256": "x"}]
    b = [{"source_code": "local_primary", "source_record_id": 2, "source_record_sha256": "x"}]
    assert members_match_exactly(a, b) is False


def test_members_match_exactly_detects_fingerprint_tamper():
    a = [{"source_code": "local_primary", "source_record_id": 1, "source_record_sha256": "x"}]
    b = [{"source_code": "local_primary", "source_record_id": 1, "source_record_sha256": "DIFFERENT"}]
    assert members_match_exactly(a, b) is False


def test_event_payload_sha256_unaffected_by_key_order():
    ev1 = {"sequence": 1, "action": "activate", "review_id": "r1"}
    ev2 = {"review_id": "r1", "sequence": 1, "action": "activate"}
    assert event_payload_sha256(ev1) == event_payload_sha256(ev2)


def test_event_payload_sha256_changes_with_content():
    ev1 = {"sequence": 1, "action": "activate", "review_id": "r1"}
    ev2 = {"sequence": 1, "action": "activate", "review_id": "r2"}
    assert event_payload_sha256(ev1) != event_payload_sha256(ev2)


def test_event_payload_sha256_stable_when_unrelated_event_appended_elsewhere():
    """Dung dung diem M1 (discuss file 13): hash event PHAI doc lap voi viec file duoc append them
    event khac - test bang cach hash 1 event giong het nhau du no 'nam trong' ngu canh nao."""
    ev = {"sequence": 5, "action": "retract", "review_id": "r5", "retracts_review_id": "r1"}
    assert event_payload_sha256(ev) == event_payload_sha256(dict(ev))


def test_registry_file_sha256_matches_plain_sha256():
    raw = b'{"schema_version":1}'
    assert registry_file_sha256(raw) == sha256_hex(raw.decode("utf-8"))


def test_checksum_of_pairs_order_independent():
    a = checksum_of_pairs([(2, "b"), (1, "a")])
    b = checksum_of_pairs([(1, "a"), (2, "b")])
    assert a == b


def test_checksum_of_pairs_sensitive_to_content():
    a = checksum_of_pairs([(1, "a")])
    b = checksum_of_pairs([(1, "b")])
    assert a != b


# ---------------------------------------------------------------------------------------
# check_registry_integrity() - consumer gate (discuss file 19 M4: daily_quality_monitor.py va
# export API deu phai fail-closed/WARN neu registry khong current, khong chi ghi trong tai lieu.
#
# Discuss file 21 M3: sau khi "bien nhan" sync (status='success' + file hash khop) qua, ham nay gio
# CHAY TIEP full verify_db_matches_event_log() - fake cursor phai ho tro ca 5 query cua buoc do
# (events_applied, decisions day du field, members, resolutions, projection), khong chi 2 query
# receipt nhu ban truoc.
# ---------------------------------------------------------------------------------------
class _FakeIntegrityCursor:
    def __init__(self, identity, latest_sync, events_applied=None, decisions=None, members=None,
                 resolutions=None, observations=None, fingerprint_rows=None):
        self._identity = identity
        self._latest_sync = latest_sync
        self._events_applied = events_applied or []
        self._decisions = decisions or []
        self._members = members or []
        self._resolutions = resolutions or []
        self._observations = observations or []
        self._fingerprint_rows = fingerprint_rows or []
        self._next = None

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        if norm.startswith("SELECT source_code FROM anomaly_registry_source_identity"):
            self._next = {"source_code": self._identity} if self._identity else None
        elif norm.startswith("SELECT status, registry_file_sha256 FROM anomaly_registry_sync_runs"):
            self._next = self._latest_sync
        elif norm.startswith("SELECT event_id, sequence_no, event_payload_sha256, action, review_id, member_count FROM anomaly_registry_events_applied"):
            self._next = self._events_applied
        elif norm.startswith(
            "SELECT review_id, decision, reason_code, rationale, evidence_json, reviewer, "
            "decided_at, state, member_count, member_checksum, superseded_by_review_id "
            "FROM anomaly_review_decisions"
        ):
            self._next = self._decisions
        elif norm.startswith("SELECT review_id, source_record_id, source_record_sha256 FROM anomaly_review_members"):
            self._next = self._members
        elif norm.startswith("SELECT source_record_id, review_id FROM anomaly_review_resolutions"):
            self._next = self._resolutions
        elif norm.startswith("SELECT po.record_id") and "IN (" in norm:
            ids = set(params or ())
            self._next = [r for r in self._fingerprint_rows if r["record_id"] in ids]
        elif norm.startswith("SELECT record_id, is_anomaly FROM price_observations"):
            self._next = self._observations
        else:
            raise AssertionError(f"unexpected SQL in fake integrity cursor: {norm[:100]}")

    def fetchone(self):
        return self._next

    def fetchall(self):
        return self._next


def test_check_registry_integrity_not_provisioned():
    cursor = _FakeIntegrityCursor(identity=None, latest_sync=None)
    result = check_registry_integrity(cursor, "local_primary", registry_path=Path("irrelevant.json"))
    assert result["ok"] is False
    assert "provision" in result["reason"]


def test_check_registry_integrity_identity_mismatch():
    cursor = _FakeIntegrityCursor(identity="vps", latest_sync=None)
    result = check_registry_integrity(cursor, "local_primary", registry_path=Path("irrelevant.json"))
    assert result["ok"] is False
    assert result["identity_matches"] is False


def test_check_registry_integrity_no_source_code_uses_identity_directly():
    """API consumer khong truyen source_code - tu dung identity da provision, khong so sanh."""
    cursor = _FakeIntegrityCursor(identity="local_primary", latest_sync={"status": "success", "registry_file_sha256": "irrelevant"})
    result = check_registry_integrity(cursor, source_code=None, registry_path=Path("irrelevant.json"))
    assert result["source_code"] == "local_primary"
    assert result["identity_matches"] is True


def test_check_registry_integrity_missing_file(tmp_path):
    cursor = _FakeIntegrityCursor(identity="local_primary", latest_sync=None)
    missing = tmp_path / "does_not_exist.json"
    result = check_registry_integrity(cursor, "local_primary", registry_path=missing)
    assert result["ok"] is False
    assert "khong tim thay file" in result["reason"]


def test_check_registry_integrity_never_synced(tmp_path):
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_bytes(b'{"schema_version":1}')
    cursor = _FakeIntegrityCursor(identity="local_primary", latest_sync=None)
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is False
    assert "chua tung chay sync" in result["reason"]


def test_check_registry_integrity_last_sync_failed(tmp_path):
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_bytes(b'{"schema_version":1}')
    cursor = _FakeIntegrityCursor(
        identity="local_primary",
        latest_sync={"status": "failed", "registry_file_sha256": "whatever"},
    )
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is False
    assert "failed" in result["reason"]


def test_check_registry_integrity_stale_file_hash(tmp_path):
    """Case chinh cua M4: sync THANH CONG truoc do, nhung anomaly_registry.json da doi tu luc do -
    KHONG duoc coi la current chi vi lan sync gan nhat 'thanh cong'."""
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_bytes(b'{"schema_version":1,"events":[]}')
    cursor = _FakeIntegrityCursor(
        identity="local_primary",
        latest_sync={"status": "success", "registry_file_sha256": "old_hash_before_file_changed"},
    )
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is False
    assert "da doi nhung chua sync lai" in result["reason"]


def test_check_registry_integrity_happy_path(tmp_path):
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_bytes(
        b'{"schema_version":1,"published_at":"x","declared_sources":["local_primary"],"events":[]}'
    )
    current_hash = registry_file_sha256(registry_file.read_bytes())
    cursor = _FakeIntegrityCursor(
        identity="local_primary",
        latest_sync={"status": "success", "registry_file_sha256": current_hash},
    )
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is True
    assert result["reason"] is None
    assert result["drift_errors"] is None


def test_check_registry_integrity_happy_path_rejects_invalid_registry_after_receipt_passes(tmp_path):
    """Ngay ca khi receipt (status='success' + hash khop) qua, file HIEN TAI van phai la 1 event log
    hop le (vd khong thieu declared_sources) - mo ta duoc trang thai "sync tung thanh cong nhung file
    sau do bi sua thanh khong hop le nua"."""
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_bytes(b'{"schema_version":1,"published_at":"x","events":[]}')  # THIEU declared_sources
    current_hash = registry_file_sha256(registry_file.read_bytes())
    cursor = _FakeIntegrityCursor(
        identity="local_primary",
        latest_sync={"status": "success", "registry_file_sha256": current_hash},
    )
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is False
    assert "khong con hop le" in result["reason"]


def test_check_registry_integrity_detects_drift_after_successful_sync(tmp_path):
    """M3 (discuss file 21): receipt (status=success + file hash khop) khong du - phai kiem DB HIEN
    TAI con khop event log khong. Gia lap: sync thanh cong that, nhung SAU DO ai do xoa tay 1
    resolution ngoai script - receipt van 'sach' nhung DB da drift, is_anomaly co the van dang dung
    (chua bi anh huong) nen chi kiem boolean la khong du."""
    obs_row = _row(record_id=1)
    fp = observation_fingerprint(obs_row)
    ev = {
        "sequence": 1, "event_id": "r1-activate-1", "review_id": "r1", "action": "activate",
        "decision": "exclude_from_train", "reason_code": "rc", "rationale": "rat", "evidence": {},
        "reviewer": "gpt", "decided_at": "2026-09-04T10:00:00Z",
        "members": [{"source_code": "local_primary", "source_record_id": 1, "source_record_sha256": fp}],
    }
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_text(
        canonical_json({"schema_version": 1, "published_at": "2026-09-04T10:00:00Z",
                         "declared_sources": ["local_primary"], "events": [ev]}),
        encoding="utf-8",
    )
    current_hash = registry_file_sha256(registry_file.read_bytes())

    events_applied = [{
        "event_id": ev["event_id"], "sequence_no": 1, "event_payload_sha256": event_payload_sha256(ev),
        "action": "activate", "review_id": "r1", "member_count": 1,
    }]
    decisions = [{
        "review_id": "r1", "decision": "exclude_from_train", "reason_code": "rc", "rationale": "rat",
        "evidence_json": "{}", "reviewer": "gpt", "decided_at": datetime(2026, 9, 4, 10, 0, 0),
        "state": "active", "member_count": 1,
        "member_checksum": local_member_checksum(ev["members"]), "superseded_by_review_id": None,
    }]
    members = [{"review_id": "r1", "source_record_id": 1, "source_record_sha256": fp}]
    resolutions: list = []  # da bi xoa tay ngoai script - day chinh la drift can bat
    observations = [{"record_id": 1, "is_anomaly": True}]

    cursor = _FakeIntegrityCursor(
        identity="local_primary",
        latest_sync={"status": "success", "registry_file_sha256": current_hash},
        events_applied=events_applied, decisions=decisions, members=members,
        resolutions=resolutions, observations=observations, fingerprint_rows=[obs_row],
    )
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is False
    assert "drift" in result["reason"]
    assert result["drift_errors"]
    assert any("resolution record=1" in e for e in result["drift_errors"])


def test_check_registry_integrity_detects_observation_mutated_after_materialize(tmp_path):
    """M1 diem 3 (discuss file 21): moi thu (events_applied/decision/member/resolution/projection)
    deu khop, nhung price_observations THAT da bi sua (vd gia bi doi) SAU khi member duoc materialize
    - member table van giu hash CU nen so voi no khong bat duoc, phai recompute tu row that."""
    obs_row_at_materialize_time = _row(record_id=1, price_per_night=Decimal("90000000"))
    fp_at_materialize_time = observation_fingerprint(obs_row_at_materialize_time)
    ev = {
        "sequence": 1, "event_id": "r1-activate-1", "review_id": "r1", "action": "activate",
        "decision": "exclude_from_train", "reason_code": "rc", "rationale": "rat", "evidence": {},
        "reviewer": "gpt", "decided_at": "2026-09-04T10:00:00Z",
        "members": [{"source_code": "local_primary", "source_record_id": 1,
                     "source_record_sha256": fp_at_materialize_time}],
    }
    registry_file = tmp_path / "anomaly_registry.json"
    registry_file.write_text(
        canonical_json({"schema_version": 1, "published_at": "2026-09-04T10:00:00Z",
                         "declared_sources": ["local_primary"], "events": [ev]}),
        encoding="utf-8",
    )
    current_hash = registry_file_sha256(registry_file.read_bytes())

    events_applied = [{
        "event_id": ev["event_id"], "sequence_no": 1, "event_payload_sha256": event_payload_sha256(ev),
        "action": "activate", "review_id": "r1", "member_count": 1,
    }]
    decisions = [{
        "review_id": "r1", "decision": "exclude_from_train", "reason_code": "rc", "rationale": "rat",
        "evidence_json": "{}", "reviewer": "gpt", "decided_at": datetime(2026, 9, 4, 10, 0, 0),
        "state": "active", "member_count": 1,
        "member_checksum": local_member_checksum(ev["members"]), "superseded_by_review_id": None,
    }]
    members = [{"review_id": "r1", "source_record_id": 1, "source_record_sha256": fp_at_materialize_time}]
    resolutions = [{"source_record_id": 1, "review_id": "r1"}]
    observations = [{"record_id": 1, "is_anomaly": True}]
    # observation THAT sau nay bi sua gia (vd nguoi van hanh backfill/sua tay ngoai script) - member
    # table van con giu fp_at_materialize_time (khong tu cap nhat), nen chi so voi member table se PASS
    # SAI - phai recompute tu row nay.
    mutated_row = _row(record_id=1, price_per_night=Decimal("76500000"))

    cursor = _FakeIntegrityCursor(
        identity="local_primary",
        latest_sync={"status": "success", "registry_file_sha256": current_hash},
        events_applied=events_applied, decisions=decisions, members=members,
        resolutions=resolutions, observations=observations, fingerprint_rows=[mutated_row],
    )
    result = check_registry_integrity(cursor, "local_primary", registry_path=registry_file)
    assert result["ok"] is False
    assert any("observation record=1" in e and "fingerprint HIEN TAI" in e for e in result["drift_errors"])
