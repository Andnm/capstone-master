"""10 fixture chốt qua discuss/anomaly-detection-recompute/ (file 06/09/11, PASS FOR DESIGN file 12).

Verdict tính theo TỪNG record_id, dùng 2 baseline: context (các phòng khác cùng khách sạn/checkin/
lần cào) và temporal (lịch sử TRƯỚC ĐÓ của chính phòng đó, causal). Pure function, không cần DB thật.
"""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "recompute_anomalies.py"
_spec = importlib.util.spec_from_file_location("recompute_anomalies", _SCRIPT_PATH)
recompute_anomalies = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = recompute_anomalies  # can thiet de dataclass resolve duoc __module__
_spec.loader.exec_module(recompute_anomalies)
apply_rule = recompute_anomalies.apply_rule
compute_decisions = recompute_anomalies.compute_decisions

_BASE_TIME = datetime(2026, 9, 1, 0, 30)
_REC = 0


def _row(hotel_id, room_key, item_id, day_offset, checkin_date, price, *, is_sold_out=False):
    global _REC
    _REC += 1
    return {
        "record_id": _REC,
        "hotel_id": hotel_id,
        "room_identity_key": room_key,
        "crawl_run_item_id": item_id,
        "observed_at": _BASE_TIME + timedelta(days=day_offset),
        "checkin_date": checkin_date,
        "price_per_night": price,
        "is_sold_out": is_sold_out,
    }


_CHECKIN_A = datetime(2026, 10, 1).date()
_CHECKIN_B = datetime(2026, 10, 8).date()
_CHECKIN_C = datetime(2026, 10, 15).date()


def _status(decisions, record_id):
    return decisions[record_id][0]


# --------------------------------------------------------------------------------------
# 1. Lumina always-high: suspected suot - persistence KHONG tu dong len confirmed nua
#    (GPT review file 15 MAJOR 2: audit du lieu that cho thay persistence-only bat nham villa/suite
#    cao cap hop le co cung dau hieu thong ke voi khoa mem that - ha xuong suspected, cho rule v2)
# --------------------------------------------------------------------------------------
def test_lumina_always_high_room_stays_suspected_persistence_alone_not_confirmed():
    rows = []
    # item1 (checkin A, day0): suite luon cao, 2 phong khac binh thuong
    r1 = _row("lumina", "suite", 101, 0, _CHECKIN_A, 90_000_000)
    rows += [r1, _row("lumina", "deluxe", 101, 0, _CHECKIN_A, 3_000_000),
             _row("lumina", "standard", 101, 0, _CHECKIN_A, 2_800_000)]
    # item2 (checkin B, day1)
    r2 = _row("lumina", "suite", 102, 1, _CHECKIN_B, 90_000_000)
    rows += [r2, _row("lumina", "deluxe", 102, 1, _CHECKIN_B, 3_100_000),
             _row("lumina", "standard", 102, 1, _CHECKIN_B, 2_900_000)]
    # item3 (checkin A lai, day2) -> du 3 item, 2 checkin date rieng biet (A, B) - du persistence
    r3 = _row("lumina", "suite", 103, 2, _CHECKIN_A, 90_000_000)
    rows += [r3, _row("lumina", "deluxe", 103, 2, _CHECKIN_A, 3_050_000),
             _row("lumina", "standard", 103, 2, _CHECKIN_A, 2_950_000)]

    decisions, counts = compute_decisions(rows)

    assert _status(decisions, r1["record_id"]) == "suspected"
    assert decisions[r1["record_id"]][1] == "contextual_high"
    assert _status(decisions, r2["record_id"]) == "suspected"
    # du persistence (3 item, 2 checkin) nhung KHONG tu dong thanh confirmed - chi doi reason
    assert _status(decisions, r3["record_id"]) == "suspected"
    assert decisions[r3["record_id"]][1] == "persistent_contextual_high"
    assert counts.get("confirmed", 0) == 0


# --------------------------------------------------------------------------------------
# 2. NYE broad surge: moi phong cung tang -> khong confirmed, van la normal
# --------------------------------------------------------------------------------------
def test_broad_hotel_wide_surge_stays_normal():
    rows = [
        _row("roma", "ocean_view", 201, 0, _CHECKIN_A, 50_000_000),
        _row("roma", "city_view", 201, 0, _CHECKIN_A, 45_000_000),
        _row("roma", "garden_view", 201, 0, _CHECKIN_A, 48_000_000),
    ]
    decisions, _ = compute_decisions(rows)
    for r in rows:
        assert _status(decisions, r["record_id"]) == "normal"


# --------------------------------------------------------------------------------------
# 3. Temporal spike cua 1 phong (lich su binh thuong) + context high -> confirmed ngay,
#    khong can doi persistence
# --------------------------------------------------------------------------------------
def test_temporal_spike_with_context_high_confirms_immediately():
    rows = []
    # 5 item truoc, 3 ngay quan sat khac nhau, gia binh thuong on dinh; can >=3 loai phong tong
    # cong de "deluxe" co >=2 phong KHAC lam context (chi 1 phong khac -> insufficient_evidence)
    for day in range(5):
        iid = 300 + day
        rows.append(_row("hotelx", "deluxe", iid, day, _CHECKIN_A, 3_000_000))
        rows.append(_row("hotelx", "standard", iid, day, _CHECKIN_A, 2_800_000))
        rows.append(_row("hotelx", "economy", iid, day, _CHECKIN_A, 2_500_000))
    # item spike: deluxe nhay vot, cac phong khac van binh thuong (context thap)
    spike = _row("hotelx", "deluxe", 399, 10, _CHECKIN_A, 25_000_000)
    rows.append(spike)
    rows.append(_row("hotelx", "standard", 399, 10, _CHECKIN_A, 2_900_000))
    rows.append(_row("hotelx", "economy", 399, 10, _CHECKIN_A, 2_600_000))

    decisions, _ = compute_decisions(rows)
    assert _status(decisions, spike["record_id"]) == "confirmed"
    assert decisions[spike["record_id"]][1] == "high_price_outlier"
    # Test nay cung la "positive control" cho fix o duoi: toan bo item dung CHUNG 1 checkin_date,
    # nen spike that so voi lich su CUNG ngay luu tru van phai confirmed binh thuong.


# --------------------------------------------------------------------------------------
# GPT review file 17 MAJOR: temporal history tung tron ca cac checkin_date khac nhau - 1 phong
# o ngay luu tru DAT (nhung ON DINH dat, khong spike) bi so nham voi lich su gia RE cua NHUNG NGAY
# LUU TRU KHAC cung phong - Case that: "Phu Tho Da Lat / Phong Tieu chuan", checkin 02/09 luon
# 10,5-11,7 trieu tu lan quan sat dau, nhung bi confirmed vi history con chua cac checkin gia
# 0,29-1,03 trieu. Sua: temporal history phai khoa ca checkin_date, KHONG duoc tron.
# --------------------------------------------------------------------------------------
def test_different_checkin_dates_do_not_contaminate_each_others_temporal_history():
    rows = []
    # checkin X: 4 item, gia RE on dinh (~300k) - xay du lich su "re" trong chung 1 (hotel,room)
    for day in range(4):
        iid = 500 + day
        rows.append(_row("phutho", "std_room", iid, day, _CHECKIN_A, 300_000))
        rows.append(_row("phutho", "sib1", iid, day, _CHECKIN_A, 280_000))
        rows.append(_row("phutho", "sib2", iid, day, _CHECKIN_A, 290_000))
    # checkin Y: rieng biet, gia DAT on dinh (~10 trieu) tu quan sat DAU TIEN - khong phai spike
    # that, chi la 1 ngay luu tru khac von di dat hon (vd cuoi tuan/le). Moi item van co sib1/sib2
    # RE de tao context cao (contextual-high candidate), dung de kiem tra rieng nhanh temporal.
    y_records = []
    for day in range(4, 7):  # 3 item tren checkin Y
        iid = 600 + day
        r = _row("phutho", "std_room", iid, day, _CHECKIN_B, 10_000_000 + day * 10_000)
        y_records.append(r)
        rows.append(r)
        rows.append(_row("phutho", "sib1", iid, day, _CHECKIN_B, 290_000))
        rows.append(_row("phutho", "sib2", iid, day, _CHECKIN_B, 300_000))

    decisions, _ = compute_decisions(rows)

    # checkin Y moi co 2 quan sat TRUOC quan sat thu 3 - CHUA du 5 item rieng cua checkin Y de tinh
    # temporal_sufficient => khong duoc confirmed qua nhanh high_price_outlier, du pool chung ca 2
    # checkin (4 X + 3 Y = 7 item, 7 ngay) thua thai neu tinh gop (dung la bug cu).
    third_y = y_records[2]
    assert _status(decisions, third_y["record_id"]) in ("suspected", "insufficient_evidence")
    assert decisions[third_y["record_id"]][1] != "high_price_outlier"


# --------------------------------------------------------------------------------------
# 4. Giam gia that hop le (thap hon context nhung khong duoi san) -> khong bi bat
#    (rule chi bat mot chieu gia CAO)
# --------------------------------------------------------------------------------------
def test_legitimate_low_price_not_caught_by_symmetric_rule():
    cheap = _row("hoteld", "promo_room", 401, 0, _CHECKIN_A, 1_000_000)
    rows = [
        cheap,
        _row("hoteld", "deluxe", 401, 0, _CHECKIN_A, 3_000_000),
        _row("hoteld", "standard", 401, 0, _CHECKIN_A, 2_900_000),
    ]
    decisions, _ = compute_decisions(rows)
    assert _status(decisions, cheap["record_id"]) == "normal"


# --------------------------------------------------------------------------------------
# 5. Gia < 50.000 VND -> confirmed/implausible_low, khong can context/history
# --------------------------------------------------------------------------------------
def test_price_below_absolute_floor_is_confirmed_implausible_low():
    tiny = _row("hotele", "solo_room", 501, 0, _CHECKIN_A, 40_000)
    rows = [tiny]
    decisions, _ = compute_decisions(rows)
    assert decisions[tiny["record_id"]] == ("confirmed", "implausible_low")


# --------------------------------------------------------------------------------------
# 6. Chi 1 loai phong trong khach san/item -> insufficient_evidence
# --------------------------------------------------------------------------------------
def test_single_room_type_in_hotel_is_insufficient_evidence():
    only = _row("hotelf", "only_room", 601, 0, _CHECKIN_A, 50_000_000)
    rows = [only]
    decisions, _ = compute_decisions(rows)
    assert decisions[only["record_id"]] == ("insufficient_evidence", None)


# --------------------------------------------------------------------------------------
# 7. Nhieu rate-plan trong 1 phong khong thoi phong context/persistence cho phong khac
# --------------------------------------------------------------------------------------
def test_multiple_rate_plans_do_not_inflate_context_for_sibling_room():
    # room X co 3 rate plan (2.0/2.2/2.4 trieu, representative = median = 2.2tr)
    x1 = _row("hotelg", "room_x", 701, 0, _CHECKIN_A, 2_000_000)
    x2 = _row("hotelg", "room_x", 701, 0, _CHECKIN_A, 2_200_000)
    x3 = _row("hotelg", "room_x", 701, 0, _CHECKIN_A, 2_400_000)
    y = _row("hotelg", "room_y", 701, 0, _CHECKIN_A, 2_100_000)
    z = _row("hotelg", "room_z", 701, 0, _CHECKIN_A, 1_900_000)
    rows = [x1, x2, x3, y, z]

    decisions, _ = compute_decisions(rows)
    # context cho X phai la median([Y=2.1tr, Z=1.9tr]) = 2.0tr (dung 2 phong, khong phai 2 phong
    # nhung bi tinh nhu the co nhieu ban sao vi X co 3 dong) -> ratio cao nhat cua X (2.4/2.0=1.2) < 5
    for r in (x1, x2, x3):
        assert _status(decisions, r["record_id"]) == "normal"
    assert _status(decisions, y["record_id"]) == "normal"
    assert _status(decisions, z["record_id"]) == "normal"


# --------------------------------------------------------------------------------------
# 8. Item eligibility: chi doc tu item da duoc coi la hop le (o tang operational, hien tai la moi
#    item status='success' dua vao truoc khi goi compute_decisions - test nay xac nhan
#    compute_decisions() khong tu them dieu kien loc nao khac ngoai sold-out/gia NULL/room-key NULL,
#    tin tuong scope da duoc _load_scope() loc dung tu truoc)
# --------------------------------------------------------------------------------------
def test_not_applicable_for_sold_out_and_null_price_and_null_room_key():
    sold_out = _row("hotelh", "deluxe", 801, 0, _CHECKIN_A, None, is_sold_out=True)
    null_price = _row("hotelh", "standard", 801, 0, _CHECKIN_A, None)
    null_room_key = _row("hotelh", None, 801, 0, _CHECKIN_A, 3_000_000)
    valid = _row("hotelh", "suite", 801, 0, _CHECKIN_A, 3_100_000)
    rows = [sold_out, null_price, null_room_key, valid]

    decisions, counts = compute_decisions(rows)
    assert decisions[sold_out["record_id"]] == ("not_applicable", None)
    assert decisions[null_price["record_id"]] == ("not_applicable", None)
    assert decisions[null_room_key["record_id"]] == ("not_applicable", None)
    assert counts["not_applicable"] == 3
    # valid record chi con 0 phong khac hop le de so sanh (3 cai kia bi loai) -> insufficient_evidence
    assert _status(decisions, valid["record_id"]) == "insufficient_evidence"


# --------------------------------------------------------------------------------------
# 9. Future observation khong doi flag cua record qua khu da tinh xong (causal immutability)
# --------------------------------------------------------------------------------------
def test_future_observation_does_not_change_past_record_flags():
    rows_past = []
    r1 = _row("lumina2", "suite", 901, 0, _CHECKIN_A, 90_000_000)
    rows_past += [r1, _row("lumina2", "deluxe", 901, 0, _CHECKIN_A, 3_000_000),
                  _row("lumina2", "standard", 901, 0, _CHECKIN_A, 2_800_000)]
    r2 = _row("lumina2", "suite", 902, 1, _CHECKIN_B, 90_000_000)
    rows_past += [r2, _row("lumina2", "deluxe", 902, 1, _CHECKIN_B, 3_100_000),
                  _row("lumina2", "standard", 902, 1, _CHECKIN_B, 2_900_000)]

    decisions_before, _ = compute_decisions(rows_past)
    status_before_r1 = decisions_before[r1["record_id"]]
    status_before_r2 = decisions_before[r2["record_id"]]

    # them 1 item TUONG LAI (day sau) - se day r2 sang persistence du (3 item, nhung r1/r2 la qua khu)
    rows_future = list(rows_past)
    r3 = _row("lumina2", "suite", 903, 2, _CHECKIN_C, 90_000_000)
    rows_future += [r3, _row("lumina2", "deluxe", 903, 2, _CHECKIN_C, 3_050_000),
                     _row("lumina2", "standard", 903, 2, _CHECKIN_C, 2_950_000)]

    decisions_after, _ = compute_decisions(rows_future)
    assert decisions_after[r1["record_id"]] == status_before_r1
    assert decisions_after[r2["record_id"]] == status_before_r2
    # r3 (item moi nhat) co the co reason khac (du persistence), nhung r1/r2 (qua khu) khong bi
    # tinh lai nguoc chi vi co them du lieu tuong lai


# --------------------------------------------------------------------------------------
# 10. Idempotent: chay compute_decisions() 2 lan tren cung du lieu ra cung ket qua; verdict co the
#     la confirmed trong 1 tap du lieu va normal/khac trong tap khac (khong bi "dinh" TRUE vinh vien)
# --------------------------------------------------------------------------------------
def test_compute_decisions_is_idempotent_and_reversible():
    rows = []
    r1 = _row("hoteli", "suite", 1001, 0, _CHECKIN_A, 90_000_000)
    rows += [r1, _row("hoteli", "deluxe", 1001, 0, _CHECKIN_A, 3_000_000),
             _row("hoteli", "standard", 1001, 0, _CHECKIN_A, 2_800_000)]

    decisions_a, _ = compute_decisions(rows)
    decisions_b, _ = compute_decisions(rows)
    assert decisions_a == decisions_b  # idempotent tren cung du lieu

    # neu sau nay hotel giam gia that (khong con lech), verdict phai co the quay ve normal, khong
    # bi "cong don TRUE vinh vien" - mo phong bang cach doi han gia suite ve muc binh thuong
    rows_fixed = [dict(r) for r in rows]
    for r in rows_fixed:
        if r["room_identity_key"] == "suite":
            r["price_per_night"] = 3_050_000
    decisions_fixed, _ = compute_decisions(rows_fixed)
    fixed_status = decisions_fixed[r1["record_id"]]
    assert fixed_status[0] == "normal"  # dao nguoc duoc, khong bi ket qua cu chi phoi


def test_apply_rule_matrix_direct():
    # low floor thang truoc moi dieu kien khac
    assert apply_rule(price=10_000, context_median=1_000_000, context_room_count=5,
                       temporal_median=None, temporal_sufficient=False, persistence_ok=True) \
        == ("confirmed", "implausible_low")
    # khong du context
    assert apply_rule(price=5_000_000, context_median=None, context_room_count=1,
                       temporal_median=None, temporal_sufficient=False, persistence_ok=False) \
        == ("insufficient_evidence", None)
    # binh thuong
    assert apply_rule(price=1_000_000, context_median=1_000_000, context_room_count=3,
                       temporal_median=None, temporal_sufficient=False, persistence_ok=False) \
        == ("normal", None)
    # cao, khong temporal, khong persistence -> suspected
    assert apply_rule(price=10_000_000, context_median=1_000_000, context_room_count=3,
                       temporal_median=None, temporal_sufficient=False, persistence_ok=False) \
        == ("suspected", "contextual_high")
    # cao, co persistence nhung KHONG co temporal spike -> van suspected, KHONG tu dong confirmed
    # (GPT review file 15 MAJOR 2 - fix chinh cua vong nay)
    assert apply_rule(price=10_000_000, context_median=1_000_000, context_room_count=3,
                       temporal_median=None, temporal_sufficient=False, persistence_ok=True) \
        == ("suspected", "persistent_contextual_high")
    # cao + temporal spike that (gia vuot ca lich su rieng) -> van confirmed nhu cu
    assert apply_rule(price=10_000_000, context_median=1_000_000, context_room_count=3,
                       temporal_median=1_000_000, temporal_sufficient=True, persistence_ok=False) \
        == ("confirmed", "high_price_outlier")


# --------------------------------------------------------------------------------------
# MINOR 1 (GPT review file 15): test ranh gioi DB - _load_scope() phai loc dung run completed +
# item success; _apply() phai doi dung ca FALSE->TRUE lan TRUE->FALSE theo record_id, va dung lai
# (khong UPDATE) khi temp-table populate sai so.
# --------------------------------------------------------------------------------------
class _FakeScopeCursor:
    def __init__(self):
        self.executed_sql = None

    def execute(self, sql, params=None):
        self.executed_sql = " ".join(sql.split())

    def fetchall(self):
        return []


def test_load_scope_filters_completed_run_and_success_item():
    cursor = _FakeScopeCursor()
    recompute_anomalies._load_scope(cursor)
    assert "JOIN crawl_runs cr" in cursor.executed_sql
    assert "cr.status = 'completed'" in cursor.executed_sql
    assert "cri.status = 'success'" in cursor.executed_sql


class _FakeApplyCursor:
    """Mo phong dung cac cau SQL cu the ma _apply() phat ra - khong phai fake MySQL tong quat."""

    def __init__(self, initial_state):
        self.state = dict(initial_state)  # record_id -> bool (is_anomaly hien tai)
        self.temp_rows: dict[int, bool] = {}
        self.executed: list[tuple[str, object]] = []
        self.rowcount = 0
        self._pending_fetch = None

    def execute(self, sql, params=None):
        text = " ".join(sql.split())
        self.executed.append((text, params))
        if text.startswith("CREATE TEMPORARY TABLE"):
            self.temp_rows = {}
        elif text.startswith("SELECT COUNT(*) AS n FROM anomaly_decisions"):
            self._pending_fetch = {"n": len(self.temp_rows)}
        elif text.startswith("UPDATE price_observations"):
            changed = 0
            for rid, confirmed in self.temp_rows.items():
                if self.state.get(rid) != confirmed:
                    self.state[rid] = confirmed
                    changed += 1
            self.rowcount = changed
        elif text.startswith("DROP TEMPORARY TABLE"):
            pass
        else:
            self._pending_fetch = None

    def executemany(self, sql, seq):
        for rid, confirmed in seq:
            self.temp_rows[rid] = confirmed

    def fetchone(self):
        return self._pending_fetch

    def close(self):
        pass


def test_apply_flips_both_directions_by_record_id():
    cursor = _FakeApplyCursor({1: False, 2: True, 3: False})
    decisions = {
        1: ("confirmed", "implausible_low"),  # False -> True
        2: ("normal", None),                   # True -> False
        3: ("normal", None),                   # False -> False (khong doi)
    }
    updated = recompute_anomalies._apply(cursor, decisions)
    assert cursor.state == {1: True, 2: False, 3: False}
    assert updated == 2


class _FakeApplyCursorDropsOneRow(_FakeApplyCursor):
    """Gia lap loi: executemany bi mat 1 dong (vd loi ket noi giua chung)."""

    def executemany(self, sql, seq):
        seq = list(seq)
        super().executemany(sql, seq[:-1])


def test_apply_raises_and_never_updates_when_temp_table_count_mismatches():
    cursor = _FakeApplyCursorDropsOneRow({1: False, 2: False})
    decisions = {1: ("confirmed", "implausible_low"), 2: ("normal", None)}
    try:
        recompute_anomalies._apply(cursor, decisions)
        assert False, "phai raise RuntimeError khi temp table thieu dong"
    except RuntimeError:
        pass
    # UPDATE khong duoc chay khi da phat hien sai lech - khong ghi du lieu sai
    assert not any(sql.startswith("UPDATE price_observations") for sql, _ in cursor.executed)
    assert cursor.state == {1: False, 2: False}  # khong doi gi
