"""Luật hằng số thuần stdlib dùng chung cho builder (ml_serving) và API (dashboard). KHÔNG import gì ngoài stdlib.

Đơn vị phần trăm = ĐIỂM PHẦN TRĂM (10 nghĩa là +10%). Hướng dùng ngưỡng NGHIÊM NGẶT trên giá trị CHƯA làm tròn:
`> +2` up, `< -2` down, còn lại (kể cả đúng ±2) stable. Đây là hàm duy nhất quyết định hướng (API_CONTRACT §4.4).
"""
from __future__ import annotations

import math

DIRECTION_THRESHOLD_PCT = 2.0
SUPPORTED_HORIZONS: tuple[int, ...] = (1, 3)
UNSUPPORTED_HORIZONS: tuple[int, ...] = (7, 14)

MIN_GROUP_SIZE = 5            # n peer tối thiểu để hiện tổng hợp nhóm
MAX_PEERS = 15                # tối đa peer lấy gần nhất theo chênh review
REVIEW_BAND_TENTHS = 5        # |Δ review| <= 0,5 tính bằng SỐ NGUYÊN phần mười (83-78 <= 5); KHÔNG so số thực
POSITION_ALERT_PCT = 15.0     # |pctVsGroupMedian| >= 15 -> alert vị thế (heuristic có tên)
MARKET_ALERT_PCT = DIRECTION_THRESHOLD_PCT   # |medianPctChange| > 2 -> alert thị trường (cùng ngưỡng đổi hướng)


def pct_change(current: float, predicted: float) -> float:
    """Điểm phần trăm: 100*(predicted-current)/current. current phải hữu hạn và > 0."""
    if not (math.isfinite(current) and current > 0 and math.isfinite(predicted)):
        raise ValueError(f"pct_change: giá không hợp lệ current={current!r} predicted={predicted!r}")
    return 100.0 * (predicted - current) / current


def direction_from_pct(pct: float) -> str:
    if not math.isfinite(pct):
        raise ValueError(f"direction_from_pct: pct không hữu hạn: {pct!r}")
    if pct > DIRECTION_THRESHOLD_PCT:
        return "up"
    if pct < -DIRECTION_THRESHOLD_PCT:
        return "down"
    return "stable"


def tenths_from_score(score: float | int | str | None) -> int | None:
    """Đổi điểm review 1 chữ số thập phân sang số nguyên phần mười, không làm tròn tùy tiện. None -> None."""
    from decimal import Decimal

    if score is None:
        return None
    scaled = Decimal(str(score)) * 10
    if scaled != scaled.to_integral_value():
        raise ValueError(f"review score có hơn 1 chữ số thập phân: {score!r}")
    value = int(scaled)
    if not 0 <= value <= 100:
        raise ValueError(f"review score ngoài [0, 10]: {score!r}")
    return value


def semantic_config() -> dict:
    """Danh tính ngữ nghĩa của luật (đi vào manifest + contentSha256; API khóa 503 nếu manifest ≠ luật hiện hành). Đổi bất kỳ hằng nào ⇒ hash snapshot đổi."""
    return {
        "pctUnit": "percentage_points",
        "direction": {"thresholdPct": DIRECTION_THRESHOLD_PCT, "comparison": "strict: > +T up, < -T down, else stable; unrounded"},
        "horizons": {"served": list(SUPPORTED_HORIZONS), "unsupported": list(UNSUPPORTED_HORIZONS), "leadPolicy": "lead_time >= horizon"},
        "compset": {"scope": "same city, same check-in, quote at as-of", "bandTenths": REVIEW_BAND_TENTHS, "maxPeers": MAX_PEERS, "minGroupSize": MIN_GROUP_SIZE,
                    "reviewRepresentation": "integer tenths", "aggregation": "median", "noWidening": True},
        "alerts": {"positionPct": POSITION_ALERT_PCT, "positionComparison": ">=", "marketPct": MARKET_ALERT_PCT, "marketComparison": ">"},
    }
