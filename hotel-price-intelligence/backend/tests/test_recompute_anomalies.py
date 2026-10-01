"""Anomaly detector v2 - chi sinh CANDIDATE SIGNAL, khong tu quyet dinh exclude/keep. Thiet ke chot
qua discuss/anomaly-v2-ground-truth/ (17 file, PASS FOR DESIGN file 17).

Thay hoan toan test v1 (apply_rule/compute_decisions da bi xoa - rule tu dong confirm khong con ton
tai, xem discuss/anomaly-detection-recompute/ cho ly do: soft-lock "cao ngay tu dau" khong bao gio
dat "confirmed" duoc vi kien truc doi hoi ca context cao lan cu nhay temporal).
"""
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recompute_anomalies.py"
_spec = importlib.util.spec_from_file_location("recompute_anomalies", _SCRIPT_PATH)
recompute_anomalies = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = recompute_anomalies  # can thiet de dataclass resolve duoc __module__
_spec.loader.exec_module(recompute_anomalies)

compute_signals = recompute_anomalies.compute_signals
config_sha256 = recompute_anomalies.config_sha256
CONFIG = recompute_anomalies.CONFIG

_BASE_TIME = datetime(2026, 9, 1, 0, 30)
_REC = 0
_RUN = 0
_ITEM = 0


def _row(hotel_id, room_key, item_id, day_offset, checkin_date, price, *, run_id=None):
    global _REC
    _REC += 1
    finished = _BASE_TIME + timedelta(days=day_offset, hours=1)
    return {
        "record_id": _REC,
        "hotel_id": hotel_id,
        "room_identity_key": room_key,
        "crawl_run_item_id": item_id,
        "observed_at": _BASE_TIME + timedelta(days=day_offset),
        "checkin_date": checkin_date,
        "price_per_night": price,
        "crawl_run_id": run_id if run_id is not None else item_id,
        "run_finished_at": finished,
        "run_id": run_id if run_id is not None else item_id,
    }


def _signals_by_code(signals, code):
    return [s for s in signals if s.signal_code == code]


def _signals_for_record(signals, record_id):
    return [s for s in signals if s.record_id == record_id]


_CHECKIN_A = date(2026, 10, 1)
_CHECKIN_B = date(2026, 10, 8)


# ---------------------------------------------------------------------------------------
# config_sha256
# ---------------------------------------------------------------------------------------
def test_config_sha256_deterministic_and_sensitive_to_change():
    h1 = config_sha256(CONFIG)
    h2 = config_sha256(dict(CONFIG))
    assert h1 == h2
    changed = {**CONFIG, "hotel_wide_min_factor": 3.0}
    assert config_sha256(changed) != h1


# ---------------------------------------------------------------------------------------
# low_price_outlier - khong can room_identity_key, thuan tuy tu gia cua chinh record
# ---------------------------------------------------------------------------------------
def test_low_price_outlier_severity_tiers():
    rows = [
        _row("h1", None, 1, 0, _CHECKIN_A, Decimal("5000")),      # high
        _row("h1", None, 2, 0, _CHECKIN_A, Decimal("30000")),     # notable
        _row("h1", None, 3, 0, _CHECKIN_A, Decimal("50000")),     # KHONG fire (>= floor notable)
        _row("h1", None, 4, 0, _CHECKIN_A, Decimal("9999999")),   # binh thuong
    ]
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    low = _signals_by_code(signals, "low_price_outlier")
    by_record = {s.record_id: s for s in low}
    assert by_record[rows[0]["record_id"]].severity == "high"
    assert by_record[rows[1]["record_id"]].severity == "notable"
    assert rows[2]["record_id"] not in by_record
    assert rows[3]["record_id"] not in by_record


def test_low_price_outlier_fires_even_without_room_identity_key():
    rows = [_row("h1", None, 1, 0, _CHECKIN_A, Decimal("1000"))]
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert len(_signals_by_code(signals, "low_price_outlier")) == 1


# ---------------------------------------------------------------------------------------
# context_level_high
# ---------------------------------------------------------------------------------------
def test_context_level_high_fires_with_enough_peers():
    rows = [
        _row("h1", "suite", 101, 0, _CHECKIN_A, Decimal("20000000")),
        _row("h1", "deluxe", 101, 0, _CHECKIN_A, Decimal("3000000")),
        _row("h1", "standard", 101, 0, _CHECKIN_A, Decimal("2800000")),
    ]
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    ctx = _signals_by_code(signals, "context_level_high")
    assert len(ctx) == 1
    assert ctx[0].record_id == rows[0]["record_id"]
    assert ctx[0].metrics["context_room_count"] == 2


def test_context_level_high_needs_at_least_2_other_rooms():
    rows = [
        _row("h1", "suite", 101, 0, _CHECKIN_A, Decimal("20000000")),
        _row("h1", "deluxe", 101, 0, _CHECKIN_A, Decimal("3000000")),
    ]
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert _signals_by_code(signals, "context_level_high") == []


def test_context_level_high_normal_price_does_not_fire():
    rows = [
        _row("h1", "suite", 101, 0, _CHECKIN_A, Decimal("4000000")),
        _row("h1", "deluxe", 101, 0, _CHECKIN_A, Decimal("3000000")),
        _row("h1", "standard", 101, 0, _CHECKIN_A, Decimal("2800000")),
    ]
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert _signals_by_code(signals, "context_level_high") == []


def test_roma_broad_surge_all_rooms_up_together_stays_normal():
    """Ca hotel tang gia dong loat (surge that, vd Tet) - context ratio giua cac phong giu nguyen,
    khong bi bao nham (case that: Roma Hotel Phu Quoc 01/01/2027, verify Booking.com)."""
    rows = [
        _row("roma", "suite", 101, 0, _CHECKIN_A, Decimal("20000000")),
        _row("roma", "deluxe", 101, 0, _CHECKIN_A, Decimal("15000000")),
        _row("roma", "standard", 101, 0, _CHECKIN_A, Decimal("14000000")),
    ]
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert _signals_by_code(signals, "context_level_high") == []


# ---------------------------------------------------------------------------------------
# temporal_level_shift - causal, khoa (hotel, room, checkin), khong dung rate_plan_key
# ---------------------------------------------------------------------------------------
def _build_temporal_history(hotel, room, checkin, prices, other_room_price=Decimal("1000000")):
    """N item lien tiep, gia binh thuong (prices[:-1]) roi 1 item cuoi tang vot (prices[-1])."""
    rows = []
    for i, price in enumerate(prices):
        item_id = 200 + i
        rows.append(_row(hotel, room, item_id, i, checkin, price))
        # them 1 room khac de khong bi thieu context (khong lien quan test nay nhung giu scope hop le)
        rows.append(_row(hotel, "other", item_id, i, checkin, other_room_price))
    return rows


def test_temporal_level_shift_fires_with_enough_prior_evidence():
    prices = [Decimal("1000000")] * 6 + [Decimal("6000000")]  # >=5 item, >=3 ngay prior, spike cuoi
    rows = _build_temporal_history("h1", "suite", _CHECKIN_A, prices)
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    temporal = _signals_by_code(signals, "temporal_level_shift")
    last_record_id = rows[-2]["record_id"]  # dong "suite" cua item cuoi (rows xen ke suite/other)
    assert any(s.record_id == last_record_id for s in temporal)


def test_temporal_level_shift_insufficient_evidence_does_not_fire():
    prices = [Decimal("1000000")] * 2 + [Decimal("6000000")]  # chi 2 item prior, chua du 5
    rows = _build_temporal_history("h1", "suite", _CHECKIN_A, prices)
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert _signals_by_code(signals, "temporal_level_shift") == []


def test_temporal_level_shift_does_not_mix_different_checkin_dates():
    """Bug that da sua o v1 (discuss anomaly-detection-recompute file 17): lich su phai khoa dung
    checkin_date, khong tron cac ngay check-in khac nhau."""
    rows = []
    # 5 item gia re cho checkin B (khac ngay), du item nhung KHONG cung checkin voi record dang xet
    for i in range(5):
        rows.append(_row("h1", "suite", 300 + i, i, _CHECKIN_B, Decimal("900000")))
        rows.append(_row("h1", "other", 300 + i, i, _CHECKIN_B, Decimal("800000")))
    # 2 item gia cao ON DINH tu dau cho checkin A (chi 2 - chua du 5 item prior CHO CHECKIN A)
    for i in range(2):
        rows.append(_row("h1", "suite", 400 + i, 10 + i, _CHECKIN_A, Decimal("9000000")))
        rows.append(_row("h1", "other", 400 + i, 10 + i, _CHECKIN_A, Decimal("800000")))
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    # neu bug con: 5 item checkin B se bi tinh nham lam prior cho checkin A -> fire sai
    assert _signals_by_code(signals, "temporal_level_shift") == []


# ---------------------------------------------------------------------------------------
# hotel_wide_level_shift - fixture that: Lumina item 19579, factor 2.8125 dong nhat
# ---------------------------------------------------------------------------------------
def _hotel_wide_rows(hotel, checkin, room_prices_per_item):
    """room_prices_per_item: list cac dict {room_key: price} theo dung thu tu causal (item0=baseline
    dau tien, item cuoi = item dang xet)."""
    rows = []
    for i, room_prices in enumerate(room_prices_per_item):
        item_id = 500 + i
        for room_key, price in room_prices.items():
            rows.append(_row(hotel, room_key, item_id, i, checkin, price))
    return rows


def test_hotel_wide_level_shift_fires_on_uniform_multiplicative_jump():
    base = {
        "deluxe_double": Decimal("812308"), "deluxe_king": Decimal("960000"),
        "studio": Decimal("1993846"), "family_studio": Decimal("2141539"),
        "suite": Decimal("29600000"),
    }
    shifted = {k: v * Decimal("2.8125") for k, v in base.items()}
    reverted = dict(base)
    rows = _hotel_wide_rows("lumina", _CHECKIN_A, [base, shifted, reverted])
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    hw = _signals_by_code(signals, "hotel_wide_level_shift")
    shifted_item_id = 501
    fired_record_ids = {s.record_id for s in hw}
    shifted_rows = [r for r in rows if r["crawl_run_item_id"] == shifted_item_id]
    assert len(shifted_rows) == 5
    assert fired_record_ids == {r["record_id"] for r in shifted_rows}
    sample = hw[0]
    assert abs(sample.metrics["median_factor"] - 2.8125) < 1e-6
    assert sample.metrics["dispersion"] < 0.01


def test_hotel_wide_level_shift_reverted_item_does_not_fire():
    """Item thu 3 (quay lai gia binh thuong) khong duoc fire - chi item THUC SU nhay so voi baseline
    lien truoc moi fire, dung theo dung thiet ke (moi item tu so voi <=3 item lien truoc CUA NO)."""
    base = {"a": Decimal("1000000"), "b": Decimal("2000000"), "c": Decimal("3000000"),
            "d": Decimal("4000000"), "e": Decimal("5000000")}
    shifted = {k: v * Decimal("3") for k, v in base.items()}
    reverted = dict(base)
    rows = _hotel_wide_rows("h1", _CHECKIN_A, [base, shifted, reverted])
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    hw = _signals_by_code(signals, "hotel_wide_level_shift")
    reverted_item_id = 502
    assert all(r["crawl_run_item_id"] != reverted_item_id for r in rows if r["record_id"] in {s.record_id for s in hw})


def test_hotel_wide_level_shift_needs_min_5_paired_rooms():
    base = {"a": Decimal("1000000"), "b": Decimal("2000000")}  # chi 2 phong, duoi gate 5
    shifted = {k: v * Decimal("3") for k, v in base.items()}
    rows = _hotel_wide_rows("h1", _CHECKIN_A, [base, shifted])
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert _signals_by_code(signals, "hotel_wide_level_shift") == []


def test_hotel_wide_level_shift_high_dispersion_does_not_fire():
    """Cac phong khong nhay CUNG mot he so (dispersion cao) - khac han level-shift that."""
    base = {"a": Decimal("1000000"), "b": Decimal("1000000"), "c": Decimal("1000000"),
            "d": Decimal("1000000"), "e": Decimal("1000000")}
    uneven = {"a": Decimal("1000000"), "b": Decimal("5000000"), "c": Decimal("1200000"),
              "d": Decimal("9000000"), "e": Decimal("1100000")}
    rows = _hotel_wide_rows("h1", _CHECKIN_A, [base, uneven])
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    assert _signals_by_code(signals, "hotel_wide_level_shift") == []


def test_hotel_wide_level_shift_picks_nearest_passing_baseline():
    """Shift xay ra giua item0->item1 roi giu nguyen item1->item2: so voi baseline GAN NHAT (item1)
    khong thay doi gi (factor~1, KHONG dat gate); phai lui ve baseline XA HON (item0, offset 2) moi
    thay dung cu nhay - dung thu tu duyet offset 1->2->3, lay CAI DAU TIEN dat gate."""
    base = {"a": Decimal("1000000"), "b": Decimal("2000000"), "c": Decimal("3000000"),
            "d": Decimal("4000000"), "e": Decimal("5000000")}
    already_shifted = {k: v * Decimal("3") for k, v in base.items()}
    current = dict(already_shifted)  # khong doi them tu item1 -> item2
    rows = _hotel_wide_rows("h1", _CHECKIN_A, [base, already_shifted, current])
    signals = compute_signals(rows, CONFIG, datetime.now(timezone.utc))
    hw = _signals_by_code(signals, "hotel_wide_level_shift")
    # item1 (already_shifted) CUNG fire dung, vi no cung nhay so voi baseline cua CHINH no (item0) -
    # chi kiem tra rieng item2 (current, item_id=502) de dung trong tinh huong dang test.
    item2_signals = [s for s in hw if s.record_observed_at.day == 3]  # item2 o day_offset=2 -> ngay 3
    assert len(item2_signals) == 5
    # baseline chinh cua item2 phai la offset 2 (item dau, base) vi offset 1 (already_shifted) factor~1
    assert item2_signals[0].metrics["baseline_item_id"] == 500
    assert item2_signals[0].metrics["other_baselines"][0]["passes_gate"] is False


# ---------------------------------------------------------------------------------------
# _apply(): stale signal reconciliation, config insert-once, dry-run khong ghi gi
# ---------------------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, existing_config_hashes=None):
        self.executed = []
        self._existing_config_hashes = set(existing_config_hashes or [])
        self._temp_tables = {}
        self._next_fetchone = None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        norm = " ".join(sql.split())
        if norm.startswith("SELECT config_sha256 FROM anomaly_signal_configs"):
            self._next_fetchone = {"config_sha256": params[0]} if params[0] in self._existing_config_hashes else None
        elif norm.startswith("CREATE TEMPORARY TABLE tmp_current_signals"):
            self._temp_tables["tmp_current_signals"] = []
        elif norm.startswith("CREATE TEMPORARY TABLE tmp_scope_record_ids"):
            self._temp_tables["tmp_scope_record_ids"] = []
        elif norm.startswith("SELECT COUNT(*) AS n FROM tmp_scope_record_ids"):
            self._next_fetchone = {"n": len(self._temp_tables.get("tmp_scope_record_ids", []))}
        elif norm.startswith("SELECT COUNT(*) AS n FROM tmp_current_signals"):
            self._next_fetchone = {"n": len(self._temp_tables.get("tmp_current_signals", []))}
        elif norm.startswith("DELETE s FROM price_anomaly_signals"):
            self.rowcount = 7  # gia lap co xoa - test chi kiem tra SQL duoc goi dung dang
        elif norm.startswith("DROP TEMPORARY TABLE"):
            pass

    def executemany(self, sql, rows):
        norm = " ".join(sql.split())
        self.executed.append((norm, f"<{len(rows)} rows>"))
        if norm.startswith("INSERT INTO tmp_scope_record_ids"):
            self._temp_tables.setdefault("tmp_scope_record_ids", []).extend(rows)
        elif norm.startswith("INSERT INTO tmp_current_signals"):
            self._temp_tables.setdefault("tmp_current_signals", []).extend(rows)

    def fetchone(self):
        return self._next_fetchone


def test_apply_inserts_config_only_once_for_same_hash():
    cursor = _FakeCursor(existing_config_hashes=set())
    signals = [recompute_anomalies.SignalRow(
        record_id=1, signal_code="low_price_outlier", severity="high",
        record_observed_at=datetime.now(), evidence_available_at=datetime.now(), metrics={"price": "1"},
    )]
    recompute_anomalies._apply(cursor, signals, "abc123", {1})
    insert_config_calls = [c for c in cursor.executed if c[0].startswith("INSERT INTO anomaly_signal_configs")]
    assert len(insert_config_calls) == 1


def test_apply_skips_config_insert_when_hash_already_exists():
    cursor = _FakeCursor(existing_config_hashes={"abc123"})
    signals = [recompute_anomalies.SignalRow(
        record_id=1, signal_code="low_price_outlier", severity="high",
        record_observed_at=datetime.now(), evidence_available_at=datetime.now(), metrics={"price": "1"},
    )]
    recompute_anomalies._apply(cursor, signals, "abc123", {1})
    insert_config_calls = [c for c in cursor.executed if c[0].startswith("INSERT INTO anomaly_signal_configs")]
    assert len(insert_config_calls) == 0


def test_apply_deletes_stale_signals_via_anti_join_not_not_in():
    cursor = _FakeCursor(existing_config_hashes={"abc123"})
    recompute_anomalies._apply(cursor, [], "abc123", {1, 2, 3})
    delete_calls = [c for c in cursor.executed if c[0].startswith("DELETE s FROM price_anomaly_signals")]
    assert len(delete_calls) == 1
    assert "LEFT JOIN tmp_current_signals" in delete_calls[0][0]
    assert "NOT IN" not in delete_calls[0][0]


def test_dry_run_never_calls_apply(monkeypatch, capsys):
    """Dry-run (khong --apply) khong duoc goi _apply() - chi tinh/in, khong ghi gi."""
    called = {"n": 0}
    monkeypatch.setattr(recompute_anomalies, "_apply", lambda *a, **k: called.__setitem__("n", called["n"] + 1))

    class _NoopConn:
        def cursor(self, dictionary=True):
            return _EmptyCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    class _EmptyCursor:
        def execute(self, *a, **k):
            pass

        def fetchall(self):
            return []

        def fetchone(self):
            return {"source_code": "local_primary"}  # identity check phai pass de test toi dung phan dry-run

        def close(self):
            pass

    import contextlib

    @contextlib.contextmanager
    def _fake_get_conn():
        yield _NoopConn()

    monkeypatch.setattr(recompute_anomalies, "get_db_connection", _fake_get_conn)
    monkeypatch.setattr(sys, "argv", ["recompute_anomalies.py", "--source-code", "local_primary"])
    recompute_anomalies.main()
    assert called["n"] == 0
