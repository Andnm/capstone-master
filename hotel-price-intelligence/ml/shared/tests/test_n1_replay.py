"""N1 replay matcher (GPT file 58 N1-M1): rate key trung reference KHONG chung minh reference bi anh huong; chi matcher that tren toan bo offer truoc/sau patch moi co nghia. Thuan."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.n1_replay import align_options, replay_item  # noqa: E402
from app.warehouse.canonicalize import compute_canonical_keys  # noqa: E402

CHECKIN = "2026-10-11"
POLICY = "Hủy miễn phí trước 5 ngày"


def observation(record_id, name, *, bed="1 giường đôi", breakfast=True, free_cancel=True, occupancy=2, area="20 m²"):
    return {"record_id": record_id, "hotel_id": "hotel-x", "checkin_date": CHECKIN, "is_sold_out": 0, "room_type_raw": name, "max_occupancy": occupancy, "bed_config": bed,
            "room_area": area, "breakfast_included": int(breakfast), "free_cancellation": int(free_cancel), "cancellation_policy": POLICY, "room_option_index": record_id}


def html(breakfast_old, breakfast_new=None, *, free_cancel=True):
    new = breakfast_old if breakfast_new is None else breakfast_new
    return lambda ordinal: {"ordinal": ordinal, "old": {"breakfast_included": breakfast_old, "free_cancellation": free_cancel, "cancellation_policy": POLICY},
                            "new": {"breakfast_included": new, "free_cancellation": free_cancel, "cancellation_policy": POLICY}}


def rows(*builders):
    """Dong HTML: mot dong tieu de (None, None) o dau (khong sinh option) roi cac dong option theo thu tu."""
    header = {"ordinal": 0, "old": {"breakfast_included": None, "free_cancellation": None, "cancellation_policy": None},
              "new": {"breakfast_included": None, "free_cancellation": None, "cancellation_policy": None}}
    return [header, *(builder(i + 1) for i, builder in enumerate(builders))]


def assignment_for(reference_observation):
    keys = compute_canonical_keys({k: reference_observation[k] for k in ("record_id", "hotel_id", "checkin_date", "is_sold_out", "room_type_raw", "max_occupancy", "bed_config",
                                                                        "room_area", "breakfast_included", "free_cancellation", "cancellation_policy")})
    return {"id": 7, "canonical_room_key": keys.canonical_room_key, "canonical_rate_key": keys.canonical_rate_key, "canonical_series_id": "series-x",
            "room_type_anchor_raw": reference_observation["room_type_raw"], "max_occupancy": reference_observation["max_occupancy"], "room_area": reference_observation["room_area"]}


REFERENCE = observation(0, "Deluxe Room")


def test_mimosa_counterexample_affected_rate_equals_reference_rate_but_the_selected_alias_does_not_change():
    observations = [observation(1, "Deluxe Room", bed="2 giường đơn"), observation(2, "Standard Room")]          # o1 alias khong bi anh huong; o2 bi anh huong, cung rate key voi reference
    result = replay_item(observations=observations, html_rows=rows(html(True), html(True, False)), assignment=assignment_for(REFERENCE))
    assert result["class"] == "evaluated" and result["affected_option_indexes"] == [1]
    assert result["affected_option_rate_equals_reference_rate"] is True                              # association...
    assert result["selected_reference_match_affected"] is False and result["old_to_patched_transition"] == "unchanged"   # ...nhung matcher KHONG doi
    assert result["old_match"]["status"] == result["patched_match"]["status"] == "alias" and result["old_match"]["selected_scan_record_id"] == 1
    assert result["patched_match"]["selected_scan_record_id"] == 1 and result["room_key_changes"] == 0 and result["rate_key_changes"] == 1


@pytest.mark.parametrize("extra_unaffected", [False, True])
def test_huy_hoang_case_the_only_matching_option_is_affected_so_alias_becomes_unavailable(extra_unaffected):
    observations = [observation(1, "Deluxe Room", bed="2 giường đơn")]
    builders = [html(True, False)]
    if extra_unaffected:                                                                              # option khac khong lien quan (khac rate, khac ten): khong cuu duoc match
        observations.append(observation(2, "Family Suite", breakfast=False, free_cancel=False))
        builders.append(html(False, free_cancel=False))
    result = replay_item(observations=observations, html_rows=rows(*builders), assignment=assignment_for(REFERENCE))
    assert result["old_match"]["status"] == "alias" and result["patched_match"]["status"] == "unavailable"
    assert result["selected_reference_match_affected"] is True and result["old_to_patched_transition"] == "alias->unavailable"
    assert result["patched_match"]["selected_scan_record_id"] is None and result["selected_record_changed"] is True


def test_exact_match_that_loses_its_rate_key_becomes_unavailable_too():
    exact = dict(REFERENCE, record_id=1, room_option_index=1)
    result = replay_item(observations=[exact], html_rows=rows(html(True, False)), assignment=assignment_for(REFERENCE))
    assert result["old_match"]["status"] == "exact" and result["patched_match"]["status"] == "unavailable"


def test_no_assignment_is_not_evaluated_and_never_reported_as_unaffected():
    result = replay_item(observations=[observation(1, "Deluxe Room")], html_rows=rows(html(True, False)), assignment=None)
    assert result["class"] == "no_assignment" and "old_match" not in result and "old_to_patched_transition" not in result
    assert result["rate_key_changes"] == 1 and result["room_key_changes"] == 0 and result["affected_option_indexes"] == [0]


def test_unaligned_html_is_unmapped_and_never_guessed():
    observations = [observation(1, "Deluxe Room"), observation(2, "Standard Room")]
    short = replay_item(observations=observations, html_rows=rows(html(True, False)), assignment=assignment_for(REFERENCE))
    assert short["class"] == "unmapped" and "(1) != so observation DB (2)" in short["reason"]
    other_policy = rows(html(True), html(True, False))
    other_policy[2]["old"]["free_cancellation"] = False                                              # tuple dieu kien lech DB
    assert replay_item(observations=observations, html_rows=other_policy, assignment=assignment_for(REFERENCE))["class"] == "unmapped"


def test_align_skips_header_rows_and_requires_full_tuple_equality():
    observations = [observation(1, "A"), observation(2, "B", breakfast=False)]
    aligned, reason = align_options(rows(html(True), html(False)), observations)
    assert reason is None and [r["ordinal"] for r in aligned] == [1, 2]
    aligned, reason = align_options(rows(html(True), html(True)), observations)                     # breakfast option 2 lech => khong can chinh
    assert aligned is None and "tuple dieu kien lech" in reason


def test_item_without_changed_rows_has_no_affected_options_and_an_unchanged_match():
    observations = [observation(1, "Deluxe Room", bed="2 giường đơn")]
    result = replay_item(observations=observations, html_rows=rows(html(True)), assignment=assignment_for(REFERENCE))
    assert result["affected_option_indexes"] == [] and result["old_to_patched_transition"] == "unchanged" and result["affected_option_rate_equals_reference_rate"] is False
