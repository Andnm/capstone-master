"""Luật hằng số dùng chung (thuần stdlib): đơn vị điểm %, hướng NGHIÊM NGẶT ±2, số nguyên phần mười cho review. Không sklearn/DB."""
import math

import pytest

from app.dashboard import rules


def test_pct_is_percentage_points_not_fraction():
    assert rules.pct_change(1_000_000, 1_100_000) == pytest.approx(10.0)          # +10 điểm %, không phải 0,1
    assert rules.pct_change(1_000_000, 1_000_000) == 0.0                          # persistence
    assert rules.pct_change(1_000_000, 500_000) == pytest.approx(-50.0)


@pytest.mark.parametrize("pct, expected", [(2.0, "stable"), (-2.0, "stable"), (2.0000001, "up"), (-2.0000001, "down"), (0.0, "stable"), (10.0, "up"), (-64.4, "down"), (1.999, "stable")])
def test_direction_strict_on_unrounded_value(pct, expected):
    assert rules.direction_from_pct(pct) == expected


def test_direction_boundary_is_not_decided_by_rounded_display():
    pct = rules.pct_change(1_000_000, 1_020_000)                                   # đúng +2,000...
    assert pct == pytest.approx(2.0) and rules.direction_from_pct(pct) == "stable"
    pct = rules.pct_change(1_000_000, 1_020_001)                                   # 2,0001 hiển thị "+2,0%" nhưng vẫn là up
    assert f"{pct:.1f}" == "2.0" and rules.direction_from_pct(pct) == "up"


def test_direction_rejects_non_finite():
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            rules.direction_from_pct(bad)


def test_pct_change_rejects_bad_prices():
    for current in (0, -1, math.nan, math.inf):
        with pytest.raises(ValueError):
            rules.pct_change(current, 1.0)


def test_review_tenths_are_integers_and_float_predicate_bug_is_documented():
    assert rules.tenths_from_score("8.3") == 83 and rules.tenths_from_score(7.8) == 78 and rules.tenths_from_score("9.90") == 99 and rules.tenths_from_score(None) is None
    assert abs(8.3 - 7.8) > 0.5                                                    # float: 0.5000000000000009 -> sẽ loại oan
    assert abs(rules.tenths_from_score(8.3) - rules.tenths_from_score(7.8)) <= rules.REVIEW_BAND_TENTHS   # số nguyên: 5 <= 5


def test_review_tenths_reject_precision_and_range():
    for bad in ("8.35", 8.123, "10.1", -0.1):
        with pytest.raises(ValueError):
            rules.tenths_from_score(bad)


def test_constants_match_design():
    assert rules.SUPPORTED_HORIZONS == (1, 3) and rules.UNSUPPORTED_HORIZONS == (7, 14)
    assert (rules.MIN_GROUP_SIZE, rules.MAX_PEERS, rules.REVIEW_BAND_TENTHS, rules.POSITION_ALERT_PCT, rules.MARKET_ALERT_PCT) == (5, 15, 5, 15.0, 2.0)
    assert rules.MARKET_ALERT_PCT == rules.DIRECTION_THRESHOLD_PCT
