"""sync_anomaly_registry.py - event log replay. Fixture chot qua discuss/anomaly-v2-ground-truth/
(file 13-17): validate_events() la pure (khong DB), phan apply_event/main dung fake cursor mo phong
mot phan cac bang lien quan.

`RegistryError`/`load_registry`/`validate_events`/`verify_db_matches_event_log` song trong
`app/scraper/anomaly_registry_lib.py` (khong o sync_anomaly_registry.py nua - discuss file 21 M3) -
test o day goi qua `sync_mod.X` (re-export tu import), khong phai dinh nghia rieng trong script.
"""
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

_LIB_SPEC = importlib.util.spec_from_file_location(
    "anomaly_registry_lib",
    Path(__file__).resolve().parents[1] / "app" / "scraper" / "anomaly_registry_lib.py",
)
_lib = importlib.util.module_from_spec(_LIB_SPEC)
sys.modules[_LIB_SPEC.name] = _lib
_LIB_SPEC.loader.exec_module(_lib)

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_anomaly_registry.py"
_spec = importlib.util.spec_from_file_location("sync_anomaly_registry", _SCRIPT_PATH)
sync_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = sync_mod
_spec.loader.exec_module(sync_mod)

RegistryError = sync_mod.RegistryError
validate_events = sync_mod.validate_events
verify_and_prepare_local_members = sync_mod.verify_and_prepare_local_members
apply_event = sync_mod.apply_event
parse_iso_utc = sync_mod.parse_iso_utc
verify_db_matches_event_log = sync_mod.verify_db_matches_event_log
find_payload_drifted_events = sync_mod.find_payload_drifted_events


def _activate_event(seq, review_id, members, decided_at="2026-09-04T10:00:00Z", decision="exclude_from_train"):
    return {
        "sequence": seq, "event_id": f"{review_id}-activate-1", "review_id": review_id,
        "action": "activate", "decision": decision, "reason_code": "test", "rationale": "test",
        "evidence": {}, "reviewer": "test", "decided_at": decided_at, "members": members,
    }


def _member(source_code="local_primary", record_id=1, fp="a" * 64):
    return {"source_code": source_code, "source_record_id": record_id, "source_record_sha256": fp}


def _file(events, declared_sources=("local_primary", "vps", "local_aux")):
    return {"schema_version": 1, "published_at": "2026-09-04T10:00:00Z",
            "declared_sources": list(declared_sources), "events": events}


def _supersede_event(seq, review_id, old_review_id, members, decision="keep_as_valid",
                      decided_at="2026-09-04T11:00:00Z"):
    return {
        "sequence": seq, "event_id": f"{review_id}-supersede-1", "review_id": review_id,
        "action": "supersede", "supersedes_review_id": old_review_id, "decision": decision,
        "reason_code": "x", "rationale": "x", "evidence": {}, "reviewer": "x",
        "decided_at": decided_at, "members": members,
    }


def _retract_event(seq, review_id, target_review_id, decided_at="2026-09-04T12:00:00Z"):
    return {
        "sequence": seq, "event_id": f"{review_id}-retract-1", "review_id": review_id,
        "action": "retract", "retracts_review_id": target_review_id, "reason_code": "x",
        "rationale": "x", "evidence": {}, "reviewer": "x", "decided_at": decided_at, "members": [],
    }


# ---------------------------------------------------------------------------------------
# validate_events - pure
# ---------------------------------------------------------------------------------------
def test_validate_events_accepts_well_formed_file():
    data = _file([_activate_event(1, "r1", [_member()])])
    events = validate_events(data)
    assert len(events) == 1


def test_validate_events_rejects_non_contiguous_sequence():
    data = _file([_activate_event(1, "r1", []), _activate_event(3, "r2", [])])
    with pytest.raises(RegistryError, match="sequence"):
        validate_events(data)


def test_validate_events_rejects_duplicate_sequence():
    ev1 = _activate_event(1, "r1", [])
    ev2 = _activate_event(1, "r2", [])
    with pytest.raises(RegistryError):
        validate_events(_file([ev1, ev2]))


def test_validate_events_rejects_unknown_source_code_in_members():
    data = _file([_activate_event(1, "r1", [_member(source_code="vsp")])])  # go nham "vsp"
    with pytest.raises(RegistryError, match="declared_sources"):
        validate_events(data)


def test_validate_events_accepts_member_from_foreign_but_declared_source():
    """Member cua nguon KHAC (vps) trong file la HOP LE - day khong phai loi (discuss file 15 M1)."""
    data = _file([_activate_event(1, "r1", [_member(source_code="vps")])])
    events = validate_events(data)
    assert len(events) == 1


def test_validate_events_supersede_must_reference_earlier_activate():
    ev = {"sequence": 1, "event_id": "e1", "review_id": "r2", "action": "supersede",
          "supersedes_review_id": "r1-never-activated", "decision": "keep_as_valid",
          "reason_code": "x", "rationale": "x", "evidence": {}, "reviewer": "x",
          "decided_at": "2026-09-04T10:00:00Z", "members": []}
    with pytest.raises(RegistryError, match="supersedes_review_id"):
        validate_events(_file([ev]))


def test_validate_events_supersede_must_keep_exact_same_members():
    members = [_member(record_id=1, fp="a" * 64)]
    activate = _activate_event(1, "r1", members)
    supersede = {
        "sequence": 2, "event_id": "r1-supersede-1", "review_id": "r1-v2", "action": "supersede",
        "supersedes_review_id": "r1", "decision": "keep_as_valid", "reason_code": "x",
        "rationale": "x", "evidence": {}, "reviewer": "x", "decided_at": "2026-09-04T11:00:00Z",
        "members": [_member(record_id=2, fp="b" * 64)],  # KHAC member - phai fail
    }
    with pytest.raises(RegistryError, match="member set"):
        validate_events(_file([activate, supersede]))


def test_validate_events_supersede_with_identical_members_passes():
    members = [_member(record_id=1, fp="a" * 64)]
    activate = _activate_event(1, "r1", members)
    supersede = {
        "sequence": 2, "event_id": "r1-supersede-1", "review_id": "r1-v2", "action": "supersede",
        "supersedes_review_id": "r1", "decision": "keep_as_valid", "reason_code": "x",
        "rationale": "x", "evidence": {}, "reviewer": "x", "decided_at": "2026-09-04T11:00:00Z",
        "members": members,
    }
    events = validate_events(_file([activate, supersede]))
    assert len(events) == 2


def test_validate_events_retract_must_reference_earlier_activate():
    ev = {"sequence": 1, "event_id": "e1", "review_id": "r1", "action": "retract",
          "retracts_review_id": "never-activated", "reason_code": "x", "rationale": "x",
          "evidence": {}, "reviewer": "x", "decided_at": "2026-09-04T10:00:00Z", "members": []}
    with pytest.raises(RegistryError, match="retracts_review_id"):
        validate_events(_file([ev]))


def test_validate_events_rejects_decreasing_decided_at():
    ev1 = _activate_event(1, "r1", [], decided_at="2026-09-04T12:00:00Z")
    ev2 = _activate_event(2, "r2", [], decided_at="2026-09-04T10:00:00Z")  # som hon ev1
    with pytest.raises(RegistryError, match="decided_at"):
        validate_events(_file([ev1, ev2]))


def test_validate_events_rejects_invalid_action():
    ev = _activate_event(1, "r1", [])
    ev["action"] = "delete"
    with pytest.raises(RegistryError, match="action"):
        validate_events(_file([ev]))


# ---------------------------------------------------------------------------------------
# MIN1 (discuss file 21): duplicate member (source_code, source_record_id) trong CUNG 1 event -
# truoc day member_owner chi ghi SAU khi duyet xong list nen khong tu bat duplicate trong chinh no,
# va members_match_exactly() chuan hoa thanh set nen che mat duplicate khi so supersede.
# ---------------------------------------------------------------------------------------
def test_validate_events_rejects_duplicate_member_identical_within_same_event():
    member = _member(record_id=1, fp="a" * 64)
    ev = _activate_event(1, "r1", [member, dict(member)])  # y het nhau, lap 2 lan
    with pytest.raises(RegistryError, match="NHIEU LAN"):
        validate_events(_file([ev]))


def test_validate_events_rejects_duplicate_member_id_different_fingerprint_within_same_event():
    ev = _activate_event(1, "r1", [_member(record_id=1, fp="a" * 64), _member(record_id=1, fp="b" * 64)])
    with pytest.raises(RegistryError, match="NHIEU LAN"):
        validate_events(_file([ev]))


# ---------------------------------------------------------------------------------------
# MIN2 (discuss file 21): decided_at phai so CHRONOLOGICAL (da parse), khong phai lexical string.
# ---------------------------------------------------------------------------------------
def test_validate_events_compares_decided_at_chronologically_not_lexically():
    """LEXICAL string order va CHRONOLOGICAL order co the KHAC NHAU khi offset khac nhau - vd
    '...T23:00:00+07:00' (=16:00 UTC) so voi '...T17:30:00Z' (=17:30 UTC): string 'T17' < 'T23' nen
    so sanh string se KET LUAN SAI la giam dan, trong khi thuc te 16:00 UTC < 17:30 UTC la TANG dan.
    validate_events() phai dung parse_iso_utc() de so dung, khong duoc raise oan o day."""
    ev1 = _activate_event(1, "r1", [], decided_at="2026-09-04T23:00:00+07:00")  # = 16:00 UTC
    ev2 = _activate_event(2, "r2", [], decided_at="2026-09-04T17:30:00Z")       # = 17:30 UTC, MUON hon
    events = validate_events(_file([ev1, ev2]))
    assert len(events) == 2


def test_validate_events_rejects_malformed_decided_at_with_registry_error_not_raw_exception():
    ev = _activate_event(1, "r1", [], decided_at="not-a-timestamp")
    with pytest.raises(RegistryError, match="decided_at"):
        validate_events(_file([ev]))


def test_validate_events_rejects_missing_decided_at_with_registry_error_not_raw_exception():
    ev = _activate_event(1, "r1", [])
    del ev["decided_at"]
    with pytest.raises(RegistryError, match="decided_at"):
        validate_events(_file([ev]))


def test_load_registry_rejects_file_missing_declared_sources(tmp_path):
    path = tmp_path / "anomaly_registry.json"
    path.write_text('{"schema_version": 1, "published_at": "x", "events": []}', encoding="utf-8")
    with pytest.raises(RegistryError, match="declared_sources"):
        sync_mod.load_registry(path)


def test_load_registry_rejects_unsupported_schema_version(tmp_path):
    path = tmp_path / "anomaly_registry.json"
    path.write_text(
        '{"schema_version": 2, "published_at": "x", "declared_sources": [], "events": []}',
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="schema_version"):
        sync_mod.load_registry(path)


def test_load_registry_accepts_well_formed_file(tmp_path):
    path = tmp_path / "anomaly_registry.json"
    path.write_text(
        '{"schema_version": 1, "published_at": "x", "declared_sources": ["local_primary"], "events": []}',
        encoding="utf-8",
    )
    data, raw = sync_mod.load_registry(path)
    assert data["declared_sources"] == ["local_primary"]
    assert isinstance(raw, bytes)


def test_parse_iso_utc_strips_z_suffix_to_naive_datetime():
    dt = parse_iso_utc("2026-09-04T10:39:43Z")
    assert dt == datetime(2026, 9, 4, 10, 39, 43)
    assert dt.tzinfo is None


# ---------------------------------------------------------------------------------------
# M1 (discuss file 21): validate_events() phai chan overlap-activate O CAP FILE, khong doi den
# apply_event() (cap DB) moi fail - tranh partial sync (1 event commit, event thu 2 moi fail).
# ---------------------------------------------------------------------------------------
def test_validate_events_rejects_overlapping_activate_at_file_validation_time():
    member = _member(record_id=1, fp="a" * 64)
    activate1 = _activate_event(1, "r1", [member])
    activate2 = _activate_event(2, "r2", [member], decided_at="2026-09-04T11:00:00Z")
    with pytest.raises(RegistryError, match="CHONG LEN"):
        validate_events(_file([activate1, activate2]))


def test_validate_events_allows_activate_after_member_released_by_retract():
    """member_owner phai THA member ra khi retract - activate lai SAU do (bang review_id moi) hop
    le, khong bi tinh la overlap voi review DA retracted."""
    member = _member(record_id=1, fp="a" * 64)
    activate1 = _activate_event(1, "r1", [member])
    retract = _retract_event(2, "r1-retract", "r1")
    activate2 = _activate_event(3, "r2", [member], decided_at="2026-09-04T12:00:00Z")
    events = validate_events(_file([activate1, retract, activate2]))
    assert len(events) == 3


def test_validate_events_supersede_transfers_ownership_not_overlap():
    """supersede CHUYEN chu so huu (khong phai them chu so huu moi ma khong tha nguoi cu) - activate
    khac KHONG duoc chong len member da chuyen cho review moi qua supersede."""
    member = _member(record_id=1, fp="a" * 64)
    activate1 = _activate_event(1, "r1", [member])
    supersede = _supersede_event(2, "r1-v2", "r1", [member])
    activate_conflict = _activate_event(3, "r3", [member], decided_at="2026-09-04T12:00:00Z")
    with pytest.raises(RegistryError, match="CHONG LEN"):
        validate_events(_file([activate1, supersede, activate_conflict]))


# ---------------------------------------------------------------------------------------
# verify_and_prepare_local_members - fake cursor, fingerprint fail-closed
# ---------------------------------------------------------------------------------------
class _FakeFingerprintCursor:
    """Mo phong dung _load_scope-style row cho OBSERVATION_FINGERPRINT_QUERY."""

    def __init__(self, rows_by_id):
        self._rows_by_id = rows_by_id
        self._next = None

    def execute(self, sql, params):
        record_id = params[0]
        self._next = self._rows_by_id.get(record_id)

    def fetchone(self):
        return self._next


def _obs_row(record_id, **overrides):
    from datetime import date
    from decimal import Decimal
    base = {
        "record_id": record_id, "hotel_id": "h1", "crawl_run_id": 1, "crawl_run_item_id": 10,
        "observed_at": datetime(2026, 8, 20, 7, 0, 0), "checkin_date": date(2026, 9, 6),
        "checkout_date": date(2026, 9, 7), "room_option_index": 0,
        "room_identity_key": "a" * 64, "rate_plan_key": "b" * 64,
        "price_total": Decimal("90000000"), "price_per_night": Decimal("90000000"),
    }
    base.update(overrides)
    return base


def test_verify_local_members_filters_to_this_source_only():
    cursor = _FakeFingerprintCursor({1: _obs_row(1)})
    members = [_member(source_code="local_primary", record_id=1, fp=_lib.observation_fingerprint(_obs_row(1))),
               _member(source_code="vps", record_id=999, fp="x" * 64)]
    prepared = verify_and_prepare_local_members(cursor, members, "local_primary")
    assert len(prepared) == 1
    assert prepared[0]["source_record_id"] == 1


def test_verify_local_members_raises_when_record_missing():
    cursor = _FakeFingerprintCursor({})  # 1 khong ton tai
    members = [_member(source_code="local_primary", record_id=1, fp="a" * 64)]
    with pytest.raises(RegistryError, match="KHONG TON TAI"):
        verify_and_prepare_local_members(cursor, members, "local_primary")


def test_verify_local_members_fail_closed_on_fingerprint_mismatch():
    """Case that discuss tim ra: record_id ton tai (co the do DB reset/reseed tai su dung) nhung noi
    dung khac evidence goc - PHAI fail, khong duoc ap nham verdict."""
    real_row = _obs_row(64359, hotel_id="a-completely-different-hotel", price_per_night=500000)
    cursor = _FakeFingerprintCursor({64359: real_row})
    stale_fp_from_original_lumina_observation = "f" * 64  # gia lap hash cu, khong con khop
    members = [_member(source_code="local_primary", record_id=64359, fp=stale_fp_from_original_lumina_observation)]
    with pytest.raises(RegistryError, match="fingerprint KHONG KHOP"):
        verify_and_prepare_local_members(cursor, members, "local_primary")


def test_verify_local_members_passes_when_fingerprint_matches():
    row = _obs_row(1)
    cursor = _FakeFingerprintCursor({1: row})
    members = [_member(source_code="local_primary", record_id=1, fp=_lib.observation_fingerprint(row))]
    prepared = verify_and_prepare_local_members(cursor, members, "local_primary")
    assert len(prepared) == 1


# ---------------------------------------------------------------------------------------
# apply_event end-to-end (activate -> supersede -> retract) - fake DB trong bo nho, mo phong dung
# cac bang lien quan (khong dung DB that de khong lam ban audit table append-only).
#
# Tu discuss file 21 M2, verify_db_matches_event_log() gio doc DAY DU: events_applied (khong chi
# resolutions/decisions/projection nhu ban truoc), va decisions gio so CA field (decision, reason,
# evidence, reviewer, decided_at, member_count/checksum), khong chi "state". Fake DB/cursor duoc mo
# rong tuong ung; helper _apply() mo phong dung 2 buoc main() lam (apply_event() + ghi
# events_applied) de cac test goi verify_db_matches_event_log() sau apply van dung nhu that.
# ---------------------------------------------------------------------------------------
class _InMemoryCursor:
    def __init__(self, db):
        self.db = db
        self._next = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        params = params or ()
        self.rowcount = 0

        if norm.startswith("SELECT po.record_id") and "IN (" in norm:  # batch fingerprint recompute (M1)
            self._next = [self.db["observations"][rid] for rid in params if rid in self.db["observations"]]
        elif norm.startswith("SELECT po.record_id"):  # OBSERVATION_FINGERPRINT_QUERY (single, fetchone)
            self._next = self.db["observations"].get(params[0])
        elif norm.startswith("INSERT INTO anomaly_review_decisions"):
            (review_id, decision, reason_code, rationale, evidence_json, reviewer, decided_at,
             state, member_count, member_checksum, created_at) = params
            self.db["decisions"][review_id] = {
                "review_id": review_id, "decision": decision, "reason_code": reason_code,
                "rationale": rationale, "evidence_json": evidence_json, "reviewer": reviewer,
                "decided_at": decided_at, "state": state, "member_count": member_count,
                "member_checksum": member_checksum, "superseded_by_review_id": None,
            }
            self.rowcount = 1
        elif norm.startswith("INSERT INTO anomaly_review_members"):
            review_id, source_code, source_record_id, fp, materialized_at = params
            self.db["members"].append({
                "review_id": review_id, "source_code": source_code,
                "source_record_id": source_record_id, "source_record_sha256": fp,
            })
            self.rowcount = 1
        elif norm.startswith("INSERT INTO anomaly_review_resolutions"):
            source_code, source_record_id, review_id, resolved_at = params
            self.db["resolutions"][(source_code, source_record_id)] = review_id
            self.rowcount = 1
        elif norm.startswith("UPDATE price_observations SET is_anomaly"):
            is_anomaly = params[0]
            record_ids = params[1:]
            for rid in record_ids:
                self.db["observations"][rid]["is_anomaly"] = is_anomaly
            self.rowcount = len(record_ids)
        elif norm.startswith("SELECT source_record_id, review_id FROM anomaly_review_resolutions") and "IN (" in norm:
            source_code = params[0]
            ids = params[1:]
            self._next = [
                {"source_record_id": rid, "review_id": rv}
                for (sc, rid), rv in self.db["resolutions"].items()
                if sc == source_code and rid in ids
            ]
        elif norm.startswith("SELECT source_record_id, review_id FROM anomaly_review_resolutions"):
            # full scan cho 1 source_code - dung boi verify_db_matches_event_log() (M2 fix)
            (source_code,) = params
            self._next = [
                {"source_record_id": rid, "review_id": rv}
                for (sc, rid), rv in self.db["resolutions"].items()
                if sc == source_code
            ]
        elif norm.startswith(
            "SELECT review_id, decision, reason_code, rationale, evidence_json, reviewer, "
            "decided_at, state, member_count, member_checksum, superseded_by_review_id "
            "FROM anomaly_review_decisions"
        ):
            self._next = list(self.db["decisions"].values())
        elif norm.startswith("SELECT event_id, sequence_no, event_payload_sha256, action, review_id, member_count FROM anomaly_registry_events_applied"):
            self._next = list(self.db["events_applied"].values())
        elif norm.startswith("SELECT record_id, is_anomaly FROM price_observations"):
            self._next = [
                {"record_id": rid, "is_anomaly": row["is_anomaly"]}
                for rid, row in self.db["observations"].items()
            ]
        elif norm.startswith("SELECT review_id, source_record_id, source_record_sha256 FROM anomaly_review_members"):
            (source_code,) = params
            self._next = [
                {"review_id": m["review_id"], "source_record_id": m["source_record_id"],
                 "source_record_sha256": m["source_record_sha256"]}
                for m in self.db["members"] if m["source_code"] == source_code
            ]
        elif norm.startswith("SELECT source_record_id FROM anomaly_review_members"):
            review_id, source_code = params
            self._next = [
                {"source_record_id": m["source_record_id"]} for m in self.db["members"]
                if m["review_id"] == review_id and m["source_code"] == source_code
            ]
        elif norm.startswith("UPDATE anomaly_review_decisions SET state='superseded'"):
            new_review_id, old_review_id = params
            d = self.db["decisions"].get(old_review_id)
            if d and d["state"] == "active":
                d["state"] = "superseded"
                d["superseded_by_review_id"] = new_review_id
                self.rowcount = 1
        elif norm.startswith("UPDATE anomaly_review_resolutions SET review_id"):
            new_review_id, resolved_at, source_code, source_record_id, old_review_id = params
            current = self.db["resolutions"].get((source_code, source_record_id))
            if current == old_review_id:
                self.db["resolutions"][(source_code, source_record_id)] = new_review_id
                self.rowcount = 1
        elif norm.startswith("UPDATE anomaly_review_decisions SET state='retracted'"):
            (target,) = params
            d = self.db["decisions"].get(target)
            if d and d["state"] == "active":
                d["state"] = "retracted"
                self.rowcount = 1
        elif norm.startswith("DELETE FROM anomaly_review_resolutions"):
            source_code, target_review_id = params[0], params[1]
            ids_to_delete = params[2:]
            deleted = 0
            for rid in ids_to_delete:
                key = (source_code, rid)
                if self.db["resolutions"].get(key) == target_review_id:
                    del self.db["resolutions"][key]
                    deleted += 1
            self.rowcount = deleted
        else:
            raise AssertionError(f"unexpected SQL in in-memory fake: {norm[:100]}")

    def fetchall(self):
        return self._next

    def fetchone(self):
        return self._next


def _make_db(record_ids):
    return {
        "observations": {rid: _obs_row(rid, is_anomaly=False) for rid in record_ids},
        "decisions": {}, "members": [], "resolutions": {}, "events_applied": {},
    }


def _apply(cursor, db, ev, source, now):
    """Test helper: goi apply_event() ROI tu ghi vao events_applied - mo phong dung 2 buoc main()
    lam (INSERT events_applied nam O main(), KHONG PHAI trong apply_event()) de cac test goi truc
    tiep apply_event() roi verify_db_matches_event_log() van dung nhu luong that (discuss file 21
    M2: verify gio doc ca events_applied, khong chi resolutions/decisions/projection)."""
    member_count = apply_event(cursor, ev, source, now)
    db["events_applied"][ev["event_id"]] = {
        "event_id": ev["event_id"], "sequence_no": ev["sequence"],
        "event_payload_sha256": _lib.event_payload_sha256(ev), "action": ev["action"],
        "review_id": ev["review_id"], "member_count": member_count,
    }
    return member_count


def test_apply_event_activate_sets_projection_true_for_exclude():
    db = _make_db([1, 2])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    ev = _activate_event(1, "r1", [_member(record_id=1, fp=fp)], decision="exclude_from_train")
    count = apply_event(cursor, ev, "local_primary", datetime(2026, 9, 4))
    assert count == 1
    assert db["observations"][1]["is_anomaly"] is True
    assert db["observations"][2]["is_anomaly"] is False
    assert db["resolutions"][("local_primary", 1)] == "r1"


def test_apply_event_activate_keep_as_valid_does_not_set_projection_true():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    ev = _activate_event(1, "r1", [_member(record_id=1, fp=fp)], decision="keep_as_valid")
    apply_event(cursor, ev, "local_primary", datetime(2026, 9, 4))
    assert db["observations"][1]["is_anomaly"] is False
    assert db["resolutions"][("local_primary", 1)] == "r1"  # co resolution nhung khong exclude


def test_apply_event_supersede_flips_projection_and_moves_resolution():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    activate = _activate_event(1, "r1", members, decision="exclude_from_train")
    apply_event(cursor, activate, "local_primary", datetime(2026, 9, 4))
    assert db["observations"][1]["is_anomaly"] is True

    supersede = {
        "sequence": 2, "event_id": "r1-supersede-1", "review_id": "r1-v2", "action": "supersede",
        "supersedes_review_id": "r1", "decision": "keep_as_valid", "reason_code": "x",
        "rationale": "x", "evidence": {}, "reviewer": "x", "decided_at": "2026-09-04T11:00:00Z",
        "members": members,
    }
    apply_event(cursor, supersede, "local_primary", datetime(2026, 9, 4, 11))
    assert db["observations"][1]["is_anomaly"] is False  # keep_as_valid giờ - phải tắt lại
    assert db["decisions"]["r1"]["state"] == "superseded"
    assert db["decisions"]["r1"]["superseded_by_review_id"] == "r1-v2"
    assert db["decisions"]["r1-v2"]["state"] == "active"
    assert db["resolutions"][("local_primary", 1)] == "r1-v2"


def test_apply_event_retract_removes_resolution_and_resets_projection():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    activate = _activate_event(1, "r1", [_member(record_id=1, fp=fp)], decision="exclude_from_train")
    apply_event(cursor, activate, "local_primary", datetime(2026, 9, 4))
    assert db["observations"][1]["is_anomaly"] is True

    retract = {
        "sequence": 2, "event_id": "r1-retract-1", "review_id": "r1-retract", "action": "retract",
        "retracts_review_id": "r1", "reason_code": "x", "rationale": "x", "evidence": {},
        "reviewer": "x", "decided_at": "2026-09-04T11:00:00Z", "members": [],
    }
    apply_event(cursor, retract, "local_primary", datetime(2026, 9, 4, 11))
    assert db["observations"][1]["is_anomaly"] is False
    assert ("local_primary", 1) not in db["resolutions"]
    assert db["decisions"]["r1"]["state"] == "retracted"


def test_apply_event_activate_skips_foreign_source_members_entirely():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    ev = _activate_event(1, "r1", [_member(source_code="vps", record_id=999, fp="x" * 64)])
    count = apply_event(cursor, ev, "local_primary", datetime(2026, 9, 4))
    assert count == 0
    assert db["observations"][1]["is_anomaly"] is False
    assert ("local_primary", 1) not in db["resolutions"]


# ---------------------------------------------------------------------------------------
# M1 (discuss file 19, phong thu tang DB): retract sau supersede khong duoc xoa nham resolution cua
# review MOI. Tai hien dung kich ban GPT bat duoc: activate r1 -> supersede r1 bang r2 -> retract r1.
# ---------------------------------------------------------------------------------------
def test_validate_events_rejects_retract_of_already_superseded_review():
    """Day la lop phong thu O CAP FILE - stale target bi chan truoc khi apply bat cu event nao."""
    members = [_member(record_id=1, fp="a" * 64)]
    activate = _activate_event(1, "r1", members)
    supersede = _supersede_event(2, "r1-v2", "r1", members)
    retract_stale = _retract_event(3, "r1-retract", "r1")  # r1 da superseded, khong con active
    with pytest.raises(RegistryError, match="stale target|KHONG PHAI"):
        validate_events(_file([activate, supersede, retract_stale]))


def test_validate_events_rejects_double_supersede_of_same_target():
    members = [_member(record_id=1, fp="a" * 64)]
    activate = _activate_event(1, "r1", members)
    supersede1 = _supersede_event(2, "r1-v2", "r1", members)
    supersede2 = _supersede_event(3, "r1-v3", "r1", members, decided_at="2026-09-04T12:00:00Z")
    with pytest.raises(RegistryError, match="KHONG PHAI"):
        validate_events(_file([activate, supersede1, supersede2]))


def test_validate_events_rejects_double_retract():
    members = [_member(record_id=1, fp="a" * 64)]
    activate = _activate_event(1, "r1", members)
    retract1 = _retract_event(2, "r1-retract-a", "r1")
    retract2 = _retract_event(3, "r1-retract-b", "r1", decided_at="2026-09-04T13:00:00Z")
    with pytest.raises(RegistryError, match="KHONG PHAI"):
        validate_events(_file([activate, retract1, retract2]))


def test_apply_event_retract_after_supersede_does_not_touch_new_review_resolution():
    """Kich ban dung GPT tai hien: activate r1 (exclude) -> supersede r1 bang r2 (keep) -> retract r1.
    Sau retract r1, resolution PHAI VAN con tro r2 (khong bi xoa nham) va is_anomaly phai dung theo
    decision cua r2 (keep_as_valid -> FALSE), khong phai bi anh huong boi retract cua r1."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]

    activate = _activate_event(1, "r1", members, decision="exclude_from_train")
    apply_event(cursor, activate, "local_primary", datetime(2026, 9, 4))
    assert db["resolutions"][("local_primary", 1)] == "r1"
    assert db["observations"][1]["is_anomaly"] is True

    supersede = _supersede_event(2, "r1-v2", "r1", members, decision="keep_as_valid")
    apply_event(cursor, supersede, "local_primary", datetime(2026, 9, 4, 11))
    assert db["resolutions"][("local_primary", 1)] == "r1-v2"
    assert db["observations"][1]["is_anomaly"] is False

    # validate_events() da chan kich ban nay o cap file (test rieng o tren) - o day goi THANG
    # apply_event() (lop phong thu thu 2, phong khi co drift/goi truc tiep khong qua validate). r1
    # khong con 'active' (da superseded) nen rowcount check tren UPDATE state phai raise NGAY, truoc
    # khi kip cham vao resolution cua r2 - dung tinh than M1 "defense-in-depth".
    retract_r1 = _retract_event(3, "r1-retract", "r1")
    with pytest.raises(RegistryError, match="retracted"):
        apply_event(cursor, retract_r1, "local_primary", datetime(2026, 9, 4, 12))
    assert db["resolutions"][("local_primary", 1)] == "r1-v2"  # VAN CON, KHONG bi xoa nham
    assert db["observations"][1]["is_anomaly"] is False  # dung theo r2 (keep_as_valid), khong doi
    assert db["decisions"]["r1"]["state"] == "superseded"  # KHONG bi chuyen thanh retracted
    assert db["decisions"]["r1-v2"]["state"] == "active"  # r2 KHONG bi anh huong


def test_apply_event_activate_rejects_overlapping_with_existing_resolution():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    apply_event(cursor, _activate_event(1, "r1", members), "local_primary", datetime(2026, 9, 4))

    duplicate_activate = _activate_event(2, "r2-different-review", members)
    with pytest.raises(RegistryError, match="chong len"):
        apply_event(cursor, duplicate_activate, "local_primary", datetime(2026, 9, 4, 1))


# ---------------------------------------------------------------------------------------
# M2 (discuss file 19, mo rong file 21): verify_db_matches_event_log() phai la nguon that (khong
# vong tron) VA so DU CA 4 tang (events_applied, decision full field, member, resolution+projection).
# ---------------------------------------------------------------------------------------
def test_verify_db_matches_event_log_passes_after_correct_apply():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert errors == []


def test_verify_db_matches_event_log_catches_resolution_silently_deleted_outside_scripts():
    """Dung diem GPT nhan manh: neu resolution bi mat (vd 1 nguoi xoa tay ngoai script) NHUNG
    is_anomaly boolean CUNG bi xoa/sai theo, kieu kiem tra cu (is_anomaly vs resolutions, ca 2 tu
    DB) se "khop" va bao success SAI. verify_db_matches_event_log() phai bat duoc vi no so voi
    CHINH event log, khong so 2 bang DB voi nhau."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))
    assert db["resolutions"][("local_primary", 1)] == "r1"
    assert db["observations"][1]["is_anomaly"] is True

    # gia lap drift ngoai script: ai do xoa thang resolution VA tat boolean - "khop nhau" theo kieu
    # kiem tra cu nhung SAI theo event log that.
    del db["resolutions"][("local_primary", 1)]
    db["observations"][1]["is_anomaly"] = False

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("resolution record=1" in e for e in errors)
    assert any("projection record=1" in e for e in errors)


def test_verify_db_matches_event_log_catches_decision_state_drift():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["decisions"]["r1"]["state"] = "retracted"  # gia lap sua tay ngoai script

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("decision 'r1'" in e for e in errors)


def test_verify_db_matches_event_log_catches_decision_field_drift_beyond_state():
    """M2 (discuss file 21): ban truoc CHI so 'state' - decision bi doi
    exclude_from_train -> keep_as_valid trong khi state/resolution/boolean giu nguyen van PASS SAI.
    Verifier gio phai bat duoc drift o field 'decision' rieng, khong chi 'state'."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["decisions"]["r1"]["decision"] = "keep_as_valid"  # state van "active" - gia lap sua tay

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("decision 'r1'" in e and "decision=" in e for e in errors)


def test_verify_db_matches_event_log_catches_missing_local_member():
    """M2: member bi xoa tay khoi anomaly_review_members khong duoc bo qua - truoc day verifier
    khong he doc bang nay."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["members"].clear()  # gia lap xoa tay member row

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("member 'r1': THIEU" in e for e in errors)


def test_verify_db_matches_event_log_catches_extra_or_tampered_member():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["members"][0]["source_record_sha256"] = "tampered" + "0" * 56  # gia lap sua tay fingerprint

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("member 'r1': THIEU" in e for e in errors)
    assert any("member 'r1': THUA/SAI" in e for e in errors)


def test_verify_db_matches_event_log_catches_missing_applied_event():
    """M2: neu 1 event trong file KHONG co dong tuong ung trong anomaly_registry_events_applied
    (vd bang audit bi xoa tay/mat du da apply that), verifier phai bat duoc - truoc day verifier
    khong he doc bang nay."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    apply_event(cursor, ev, "local_primary", datetime(2026, 9, 4))  # KHONG dung _apply() -> thieu events_applied

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any(f"event '{ev['event_id']}': KHONG co trong anomaly_registry_events_applied" in e for e in errors)


def test_verify_db_matches_event_log_catches_extra_orphan_applied_event():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["events_applied"]["orphan-event-99"] = {
        "event_id": "orphan-event-99", "sequence_no": 99, "event_payload_sha256": "x" * 64,
        "action": "activate", "review_id": "r99", "member_count": 0,
    }

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("orphan-event-99" in e and "thua/mo coi" in e for e in errors)


def test_verify_db_matches_event_log_catches_rogue_decision_not_in_event_log():
    """M1 (discuss file 21): decision co review_id KHONG nam trong event log (vd con sot lai tu 1
    file cu, hoac chen tay) truoc day bi bo qua hoan toan - verifier chi duyet chieu 'expected ->
    actual', khong duyet nguoc 'actual -> expected'."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["decisions"]["rogue-review"] = {
        "review_id": "rogue-review", "decision": "exclude_from_train", "reason_code": "x",
        "rationale": "x", "evidence_json": "{}", "reviewer": "x", "decided_at": datetime(2026, 9, 4),
        "state": "active", "member_count": 0, "member_checksum": _lib.local_member_checksum([]),
        "superseded_by_review_id": None,
    }

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("rogue-review" in e and "rogue/mo coi" in e for e in errors)


def test_verify_db_matches_event_log_catches_rogue_member_group():
    """Tuong tu decision, nhung o cap member: 1 nhom review_id co member local trong DB nhung khong
    he ton tai trong event log hien tai."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["members"].append({
        "review_id": "rogue-review", "source_code": "local_primary",
        "source_record_id": 999, "source_record_sha256": "z" * 64,
    })

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("rogue-review" in e and "rogue/mo coi ca nhom" in e for e in errors)


def test_verify_db_matches_event_log_catches_wrong_superseded_by_review_id():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    activate = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, activate, "local_primary", datetime(2026, 9, 4))
    supersede = _supersede_event(2, "r1-v2", "r1", members, decision="keep_as_valid")
    _apply(cursor, db, supersede, "local_primary", datetime(2026, 9, 4, 11))

    db["decisions"]["r1"]["superseded_by_review_id"] = "some-other-review"  # gia lap sua tay

    errors = verify_db_matches_event_log(cursor, [activate, supersede], "local_primary")
    assert any("decision 'r1'" in e and "superseded_by_review_id" in e for e in errors)


def test_verify_db_matches_event_log_catches_observation_mutated_after_materialize():
    """M1 diem 3 (discuss file 21): events_applied/decision/member/resolution/projection deu khop,
    nhung price_observations THAT bi sua (vd gia bi doi) SAU khi member da materialize - member table
    van giu fingerprint CU (khong tu cap nhat) nen chi so voi no la khong du, phai recompute tu
    chinh row hien tai."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    ev = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, ev, "local_primary", datetime(2026, 9, 4))

    db["observations"][1]["price_per_night"] = db["observations"][1]["price_per_night"] + 1  # sua gia SAU materialize

    errors = verify_db_matches_event_log(cursor, [ev], "local_primary")
    assert any("observation record=1" in e and "fingerprint HIEN TAI" in e for e in errors)


def test_verify_db_matches_event_log_after_supersede_expects_new_review_active():
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    activate = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, activate, "local_primary", datetime(2026, 9, 4))
    supersede = _supersede_event(2, "r1-v2", "r1", members, decision="keep_as_valid")
    _apply(cursor, db, supersede, "local_primary", datetime(2026, 9, 4, 11))

    errors = verify_db_matches_event_log(cursor, [activate, supersede], "local_primary")
    assert errors == []


def test_compute_expected_full_state_from_events_retract_removes_only_old_review_members():
    members = [_member(record_id=1, fp="a" * 64)]
    activate = _activate_event(1, "r1", members)
    supersede = _supersede_event(2, "r1-v2", "r1", members, decision="keep_as_valid")
    retract = _retract_event(3, "r1-retract", "r1")
    expected = _lib.compute_expected_full_state_from_events([activate, supersede, retract], "local_primary")
    assert expected["decision_states"]["r1"] == "retracted"
    assert expected["decision_states"]["r1-v2"] == "active"
    assert expected["resolutions"][1] == "r1-v2"  # KHONG bi xoa boi retract cua r1 (dung diem M1)


def test_verify_db_matches_event_log_does_not_crash_on_retract_event_missing_decision_field():
    """B1 (discuss file 21): retract KHONG co field 'decision' - verify_db_matches_event_log() (qua
    compute_expected_full_state_from_events) tung KeyError ngay khi co retract trong events, khien
    sync KHONG BAO GIO dat 'success' mot khi retract tung dung."""
    db = _make_db([1])
    cursor = _InMemoryCursor(db)
    fp = _lib.observation_fingerprint(db["observations"][1])
    members = [_member(record_id=1, fp=fp)]
    activate = _activate_event(1, "r1", members, decision="exclude_from_train")
    _apply(cursor, db, activate, "local_primary", datetime(2026, 9, 4))
    retract = _retract_event(2, "r1-retract", "r1")
    _apply(cursor, db, retract, "local_primary", datetime(2026, 9, 4, 1))

    errors = verify_db_matches_event_log(cursor, [activate, retract], "local_primary")  # khong duoc raise KeyError
    assert errors == []
    assert db["observations"][1]["is_anomaly"] is False


# ---------------------------------------------------------------------------------------
# M2 (dry-run, discuss file 21): find_payload_drifted_events() - pure, dung boi main()'s dry-run
# branch de bao payload-hash drift tren event DA apply truoc do (vi pham append-only), khong chi
# in so pending nhu ban truoc.
# ---------------------------------------------------------------------------------------
def test_find_payload_drifted_events_empty_when_nothing_applied():
    ev = _activate_event(1, "r1", [_member()])
    assert find_payload_drifted_events([ev], {}) == []


def test_find_payload_drifted_events_empty_when_applied_hash_matches():
    ev = _activate_event(1, "r1", [_member()])
    applied_rows = {ev["event_id"]: _lib.event_payload_sha256(ev)}
    assert find_payload_drifted_events([ev], applied_rows) == []


def test_find_payload_drifted_events_detects_content_changed_after_publish():
    ev = _activate_event(1, "r1", [_member()])
    applied_rows = {ev["event_id"]: "stale_hash_from_before_file_was_edited" + "0" * 40}
    drifted = find_payload_drifted_events([ev], applied_rows)
    assert len(drifted) == 1
    assert drifted[0]["event_id"] == ev["event_id"]


def test_find_payload_drifted_events_ignores_not_yet_applied_events():
    ev = _activate_event(1, "r1", [_member()])
    assert find_payload_drifted_events([ev], {"some-other-event-id": "x" * 64}) == []
