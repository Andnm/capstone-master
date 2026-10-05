"""Logic THUAN cua causal first-approval (khong DB) - spec muc 11 + 3 causality test muc 17."""
from __future__ import annotations

import datetime as dt

import pytest

from dataset_builder.causal_references import CausalReplay, RunEvidence

D = dt.date
T0 = dt.datetime(2026, 9, 1, 10, 0, 0)
RK_A, RK_B = "a" * 64, "b" * 64
TK = "t" * 64


def _row(item_id, hotel, checkin, rk, *, tk=TK, source_code="local", source_item_id=None, raw="Deluxe", norm="deluxe",
         occ=2, bed="1 bed", area="20 m2", bf=1, fc=0):
    return (item_id, source_code, source_item_id if source_item_id is not None else item_id, hotel, checkin, rk, tk,
            raw, norm, occ, bed, area, bf, fc)


def _run(n, rows, *, hours=0, source_code="local"):
    return RunEvidence(run_id=n, finished_at=T0 + dt.timedelta(days=n, hours=hours), source_code=source_code,
                       source_run_id=n, rows=rows)


def _replay(runs, *, min_runs=3, min_coverage=0.8):
    replay = CausalReplay(min_runs=min_runs, min_coverage=min_coverage)
    approved = []
    for run in runs:
        approved.extend(replay.process_run(run))
    return replay, approved


def test_approved_at_third_run_when_candidate_present_in_all_three():
    runs = [_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_A)]) for n in (1, 2, 3, 4)]
    replay, approved = _replay(runs)
    assert len(approved) == 1
    a = approved[0]
    assert a.approving_run_id == 3 and a.approved_at == runs[2].finished_at
    assert (a.evidence_run_count, a.evidence_item_count, a.eligible_item_count, a.coverage) == (3, 3, 3, 1.0)
    assert a.approving_item_id == 103


def test_not_approved_before_min_runs():
    replay, approved = _replay([_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_A)]) for n in (1, 2)])
    assert approved == []
    assert replay.unapproved_reasons() == {"insufficient_runs": 1}


def test_low_coverage_blocks_approval():
    # A co mat o run 1,2,3 nhung moi run co them 1 item khac khong chua A -> coverage 3/6 = 0.5
    rows = lambda n: [_row(100 + n, "h1", D(2026, 9, 20), RK_A), _row(200 + n, "h1", D(2026, 9, 20), RK_B)]
    # moi item 1 candidate: item 10n chua A, item 20n chua B; eligible = 2 items/run
    replay, approved = _replay([_run(n, rows(n)) for n in (1, 2, 3)])
    assert approved == []
    reasons = replay.unapproved_reasons()
    assert "low_coverage" in next(iter(reasons))


def test_non_unique_per_item_blocks_approval():
    dup = lambda n: [_row(100 + n, "h1", D(2026, 9, 20), RK_A), _row(100 + n, "h1", D(2026, 9, 20), RK_A)]
    replay, approved = _replay([_run(n, dup(n)) for n in (1, 2, 3, 4)])
    assert approved == []
    assert replay.unapproved_reasons() == {"non_unique_per_item": 1}


def test_freeze_after_first_approval_ignores_later_better_candidate():
    runs = [_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_A)]) for n in (1, 2, 3)]
    # sau khi da duyet A, tu run 4 tro di chi con B (lon hon) - assignment KHONG doi
    runs += [_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_B)]) for n in (4, 5, 6, 7)]
    replay, approved = _replay(runs)
    assert len(approved) == 1 and approved[0].canonical_room_key == RK_A and approved[0].approving_run_id == 3


def test_ranking_prefers_more_items_then_runs_then_keys():
    # B xuat hien o ca 3 run, A chi o 2 -> B thang du A duoc xep truoc theo ten
    runs = [_run(1, [_row(101, "h1", D(2026, 9, 20), RK_B)]),
            _run(2, [_row(102, "h1", D(2026, 9, 20), RK_A), _row(102, "h1", D(2026, 9, 20), RK_B)]),
            _run(3, [_row(103, "h1", D(2026, 9, 20), RK_B)])]
    replay, approved = _replay(runs)
    assert len(approved) == 1 and approved[0].canonical_room_key == RK_B
    assert approved[0].evidence_item_count == 3


def test_causality_1_adding_observations_after_approval_does_not_change_assignment():
    base = [_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_A)]) for n in (1, 2, 3)]
    extra = [_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_B)]) for n in (4, 5, 6)]
    _, before = _replay(base)
    _, after = _replay(base + extra)
    assert before == after[:1]


def test_causality_2_truncating_after_cutoff_keeps_earlier_assignments():
    runs = []
    for n in range(1, 8):
        runs.append(_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_A), _row(300 + n, "h2", D(2026, 9, 22), RK_B)]))
    _, full = _replay(runs)
    cutoff = runs[4].finished_at
    _, truncated = _replay([r for r in runs if r.finished_at <= cutoff])
    assert [a for a in full if a.approved_at <= cutoff] == truncated


def test_causality_3_same_input_same_output_independent_of_dict_order():
    runs = [_run(n, [_row(100 + n, f"h{k}", D(2026, 9, 20 + k), RK_A) for k in range(5)]) for n in (1, 2, 3, 4)]
    reversed_rows = [_run(r.run_id, list(reversed(r.rows))) for r in runs]
    _, one = _replay(runs)
    _, two = _replay(reversed_rows)
    assert [(a.hotel_id, a.checkin_date, a.approved_at, a.approving_item_id) for a in one] == \
           [(a.hotel_id, a.checkin_date, a.approved_at, a.approving_item_id) for a in two]


def test_approving_item_tiebreak_uses_source_ids_not_technical_ids():
    # 2 item cung series trong run duyet: item id nho hon nhung source_item_id lon hon
    runs = [_run(1, [_row(1, "h1", D(2026, 9, 20), RK_A)]), _run(2, [_row(2, "h1", D(2026, 9, 20), RK_A)]),
            _run(3, [_row(900, "h1", D(2026, 9, 20), RK_A, source_item_id=50),
                     _row(5, "h1", D(2026, 9, 20), RK_A, source_item_id=70)])]
    _, approved = _replay(runs, min_coverage=0.5)
    assert len(approved) == 1 and approved[0].approving_item_id == 900  # (local, 50) < (local, 70)


def test_series_are_independent_per_checkin_date():
    runs = [_run(n, [_row(100 + n, "h1", D(2026, 9, 20), RK_A), _row(200 + n, "h1", D(2026, 9, 21), RK_A)]) for n in (1, 2, 3)]
    _, approved = _replay(runs)
    assert sorted(a.checkin_date for a in approved) == [D(2026, 9, 20), D(2026, 9, 21)]
    assert len({a.canonical_series_id for a in approved}) == 2


def test_attribute_max_uses_codepoint_order_and_none_skipped():
    runs = [_run(1, [_row(1, "h1", D(2026, 9, 20), RK_A, raw="Deluxe", occ=None, bf=0)]),
            _run(2, [_row(2, "h1", D(2026, 9, 20), RK_A, raw="deluxe", occ=2, bf=1)]),
            _run(3, [_row(3, "h1", D(2026, 9, 20), RK_A, raw="Deluxe", occ=3, bf=None)])]
    _, approved = _replay(runs)
    assert approved[0].room_type_anchor_raw == "deluxe" and approved[0].max_occupancy == 3 and approved[0].breakfast_included is True


def test_coverage_threshold_boundary_is_inclusive():
    def run_with(items_with_a: int, total: int):
        replay = CausalReplay(min_runs=1, min_coverage=0.8)
        rows = [_row(i, "h1", D(2026, 9, 20), RK_A if i <= items_with_a else RK_B) for i in range(1, total + 1)]
        return replay.process_run(_run(1, rows))

    assert len(run_with(4, 5)) == 1          # 4/5 = 0.8 dung bang nguong -> duyet
    assert run_with(3, 4) == []              # 3/4 = 0.75 < 0.8
    assert run_with(3, 5) == []              # 3/5 = 0.6
