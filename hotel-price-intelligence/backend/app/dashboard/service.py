"""Logic nghiệp vụ API dashboard: chỉ đọc `SnapshotStore` (JSON đã xác minh). Không DB, không ML, không IO ngoài store.

Quy tắc đã khóa (DESIGN v3 / API_CONTRACT v3): % là điểm phần trăm; hướng do `rules.direction_from_pct` (đã tính ở snapshot, KHÔNG tính lại từ số làm tròn);
nhóm so sánh B = cùng city + cùng ngày nhận phòng + |Δ review| <= 5 phần mười (SỐ NGUYÊN), tối đa 15 gần nhất, n >= 5; thiếu mẫu số ⇒ null (không 0 giả), không alert.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Optional

from . import labels, rules
from . import schemas as S
from .store import SnapshotStore

CITIES = ("Hà Nội", "Hồ Chí Minh", "Vũng Tàu", "Đà Lạt", "Phú Quốc")
HOTEL_ID = re.compile(r"^[A-Za-z0-9._\-]{1,150}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNSUPPORTED = ((7, "model_not_available"), (14, "model_not_available"))
CLASSIFIER_LIMIT_KEYS = ("classifier_limit_claim", "classifier_limit_up_recall", "classifier_limit_concentration", "classifier_limit_uncalibrated")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(code)
        self.status, self.code, self.message_id, self.details = status, code, message_id, details

    @property
    def message(self) -> str:
        return labels.TEMPLATES[self.message_id]


def valid_date(value: Optional[str]) -> bool:
    if not value or not DATE.match(value):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _require_hotel_id(value: Optional[str]) -> str:
    if not value or not HOTEL_ID.match(value):
        raise ApiError(422, "invalid_parameter", "err_invalid_parameter", {"parameter": "hotelId"})
    return value


def _require_date(value: Optional[str], name: str) -> str:
    if not valid_date(value):
        raise ApiError(422, "invalid_parameter", "err_invalid_parameter", {"parameter": name})
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------- DTO helpers
def _hotel(store: SnapshotStore, hotel_id: str) -> S.Hotel:
    record = store.hotels[hotel_id]
    tenths = record.get("reviewScoreTenths")
    meta_ok = bool(record.get("metadata"))
    return S.Hotel(id=hotel_id, name=record["name"], city=record["city"], review_score=None if tenths is None else tenths / 10,
                   metadata_as_of=(store.manifest.get("hotelsMeta") or {}).get("exportedAtUtc") if meta_ok else None)


def _long_date(iso_date: str) -> str:
    """'YYYY-MM-DD' -> 'DD/MM/YYYY' (chỉ cắt chuỗi, không qua múi giờ)."""
    return f"{iso_date[8:10]}/{iso_date[5:7]}/{iso_date[0:4]}"


def _snapshot_info(store: SnapshotStore) -> S.SnapshotInfo:
    m = store.manifest
    return S.SnapshotInfo(snapshot_id=m["snapshotId"], data_as_of=m["dataAsOf"], generated_at=m["generatedAt"], claim_level=m["claimLevel"],
                          dataset_versions=m["datasetVersions"], label=labels.render("snapshot_label", data_as_of=_long_date(m["dataAsOf"])))


def _classifier_capability(store: SnapshotStore) -> dict[str, Any]:
    cap = store.manifest["capabilities"]["classifier"]
    return {"available": bool(cap["available"]), "reason": cap.get("reason")}


def get_config(store: SnapshotStore) -> S.DashboardConfig:
    cap = _classifier_capability(store)
    return S.DashboardConfig(snapshot=_snapshot_info(store), horizons=[1, 3], unsupported_horizons=[S.UnsupportedHorizonConfig(horizon=h, reason="model_not_available") for h, _ in UNSUPPORTED],
                             classifier=S.ClassifierCapability(**cap),
                             limits=S.Limits(min_group_size=rules.MIN_GROUP_SIZE, position_alert_pct=rules.POSITION_ALERT_PCT, market_alert_pct=rules.MARKET_ALERT_PCT))


def list_hotels(store: SnapshotStore, city: Optional[str]) -> S.HotelsResponse:
    if city is not None and city not in CITIES:
        raise ApiError(422, "invalid_parameter", "err_invalid_parameter", {"parameter": "city"})
    out = []
    for hotel_id in sorted(store.by_hotel):
        base = _hotel(store, hotel_id)
        if city is not None and base.city != city:
            continue
        options = [S.CheckInOption(date=p["checkIn"], supported_horizons=sorted(int(h) for h in p["horizons"])) for p in store.by_hotel[hotel_id]]
        out.append(S.HotelWithCheckIns(**base.model_dump(), check_ins=options))
    return S.HotelsResponse(snapshot_id=store.snapshot_id, hotels=out)


def _pair_or_error(store: SnapshotStore, hotel_id: str, check_in: str) -> dict[str, Any]:
    if hotel_id not in store.by_hotel:
        raise ApiError(404, "hotel_not_found", "err_hotel_not_found")
    pair = store.by_key.get((hotel_id, check_in))
    if pair is not None:
        return pair
    if check_in <= store.data_as_of:
        raise ApiError(422, "no_forecast_after_checkin", "err_no_forecast_after_checkin")
    raise ApiError(404, "check_in_not_supported", "err_check_in_not_supported")


def _forecast(store: SnapshotStore, pair: dict[str, Any]) -> S.ForecastResponse:
    served = sorted(int(h) for h in pair["horizons"])
    unsupported = [S.UnsupportedHorizon(horizon=h, reason="after_checkin") for h in (1, 3) if h not in served] + \
                  [S.UnsupportedHorizon(horizon=h, reason=reason) for h, reason in UNSUPPORTED]
    predictions = [S.HorizonPrediction(horizon=h, method=pair["horizons"][str(h)]["method"], routing_reason=pair["horizons"][str(h)]["routingReason"],
                                       predicted_price=pair["horizons"][str(h)]["predictedPrice"], expected_delta=pair["horizons"][str(h)]["expectedDelta"],
                                       expected_pct_change=pair["horizons"][str(h)]["expectedPctChange"], direction=pair["horizons"][str(h)]["direction"],
                                       target_observation_date=pair["horizons"][str(h)]["targetObservationDate"]) for h in served]
    warnings: list[S.ApiWarning] = []
    if pair["inferenceMode"] == "cold_start":
        warnings.append(S.ApiWarning(code="cold_start_preliminary", message=labels.render("cold_start_preliminary")))
    for h in served:
        if pair["horizons"][str(h)]["method"] == "persistence":
            warnings.append(S.ApiWarning(code="persistence_routed", message=labels.render("persistence_routed", horizon=h)))
    hotel = _hotel(store, pair["hotelId"])
    if not store.hotels[pair["hotelId"]].get("metadata"):
        warnings.append(S.ApiWarning(code="metadata_missing", message=labels.render("metadata_missing")))
    cap = _classifier_capability(store)
    results = []
    if cap["available"]:
        for h, label in sorted((int(k), v) for k, v in (pair.get("classifier") or {}).items()):
            if h in served:
                results.append(S.ClassifierResult(horizon=h, label=label, record_id=pair["recordId"]))
    bundles = {f"h{b['horizon']}": b["id"] for b in store.manifest["bundles"]}
    room = pair["roomSummary"]
    return S.ForecastResponse(
        snapshot_id=store.snapshot_id, data_as_of=store.data_as_of, hotel=hotel, check_in=pair["checkIn"], lead_days=pair["leadDays"], current_price=pair["currentPrice"],
        current_observed_at=pair["currentObservedAt"], inference_mode=pair["inferenceMode"], history_observation_count=pair["historyObservationCount"],
        history_span_days=pair["historySpanDays"], room_summary=S.RoomSummary(max_occupancy=room["maxOccupancy"], area_m2=room["areaM2"], breakfast_included=room["breakfastIncluded"], free_cancellation=room["freeCancellation"]),
        evidence=S.Evidence(level="dev_exploratory", claim="exploratory"), supported_horizons=served, unsupported_horizons=unsupported, predictions=predictions,
        classifier=S.ClassifierBlock(available=cap["available"], reason=cap["reason"], results=results, claim="none"), warnings=warnings,
        history=[S.HistoryPoint(date=d, price=p) for d, p in store.history[pair["canonicalSeriesId"]]],
        provenance=S.Provenance(canonical_series_id=pair["canonicalSeriesId"], record_id=pair["recordId"], bundles=bundles))


def get_forecast(store: SnapshotStore, hotel_id: Optional[str], check_in: Optional[str]) -> S.ForecastResponse:
    hotel_id = _require_hotel_id(hotel_id)
    check_in = _require_date(check_in, "checkIn")
    return _forecast(store, _pair_or_error(store, hotel_id, check_in))


# ---------------------------------------------------------------- hotelier
def _tenths(store: SnapshotStore, hotel_id: str) -> Optional[int]:
    return store.hotels[hotel_id].get("reviewScoreTenths")


def _group(store: SnapshotStore, anchor: dict[str, Any]) -> dict[str, Any]:
    """Nhóm so sánh B cho (anchor, check-in). Trả peers [(pair, gap_tenths)], n, insufficient, reason, median_price, max_gap_tenths."""
    anchor_t = _tenths(store, anchor["hotelId"])
    if anchor_t is None:
        return {"peers": [], "n": 0, "insufficient": True, "reason": "anchor_review_missing", "median_price": None, "max_gap": None}
    candidates = []
    for pair in store.by_city_checkin.get((anchor["city"], anchor["checkIn"]), []):
        if pair["hotelId"] == anchor["hotelId"] or "1" not in pair["horizons"]:
            continue
        peer_t = _tenths(store, pair["hotelId"])
        if peer_t is None:
            continue
        gap = abs(peer_t - anchor_t)                                            # SỐ NGUYÊN phần mười
        if gap <= rules.REVIEW_BAND_TENTHS:
            candidates.append((gap, pair["hotelId"], pair))
    candidates.sort(key=lambda t: (t[0], t[1]))
    peers = [(pair, gap) for gap, _, pair in candidates[: rules.MAX_PEERS]]
    n = len(peers)
    if n < rules.MIN_GROUP_SIZE:
        return {"peers": peers, "n": n, "insufficient": True, "reason": "group_too_small", "median_price": None, "max_gap": max((g for _, g in peers), default=None)}
    return {"peers": peers, "n": n, "insufficient": False, "reason": None, "median_price": median(p["currentPrice"] for p, _ in peers), "max_gap": max(g for _, g in peers)}


def _compset_hotel(store: SnapshotStore, pair: dict[str, Any], *, median_price: Optional[float], gap_tenths: Optional[int], is_anchor: bool) -> S.CompsetHotel:
    base = _hotel(store, pair["hotelId"])
    pct = None if median_price is None else rules.pct_change(median_price, pair["currentPrice"])
    return S.CompsetHotel(**base.model_dump(), current_price=pair["currentPrice"], observed_at=pair["currentObservedAt"], pct_vs_group_median=pct,
                          change7_days_pct=pair["change7DaysPct"], review_gap=None if gap_tenths is None else gap_tenths / 10, is_anchor=is_anchor)


def _price_rank(anchor_price: float, peers: list[tuple[dict[str, Any], int]]) -> S.PriceRank:
    cheaper = sum(1 for p, _ in peers if p["currentPrice"] < anchor_price)
    return S.PriceRank(rank=cheaper + 1, of=len(peers) + 1)                        # 1 = rẻ nhất; hòa cùng hạng (xếp hạng thi đấu)


def get_hotelier(store: SnapshotStore, hotel_id: Optional[str], check_in: Optional[str]) -> S.HotelierResponse:
    hotel_id = _require_hotel_id(hotel_id)
    check_in = _require_date(check_in, "checkIn")
    anchor = _pair_or_error(store, hotel_id, check_in)
    group = _group(store, anchor)
    med = group["median_price"]
    peers = group["peers"]
    per_horizon: list[S.GroupHorizonSummary] = []
    if not group["insufficient"]:
        for h in (1, 3):
            matched = [p for p, _ in peers if str(h) in p["horizons"]]
            if len(matched) < rules.MIN_GROUP_SIZE:
                continue
            items = [p["horizons"][str(h)] for p in matched]
            per_horizon.append(S.GroupHorizonSummary(
                horizon=h, n_matched=len(matched), n_model=sum(i["method"] == "model" for i in items), n_persistence=sum(i["method"] == "persistence" for i in items),
                current_median_price=median(p["currentPrice"] for p in matched), predicted_median_price=median(i["predictedPrice"] for i in items),
                median_pct_change=median(i["expectedPctChange"] for i in items)))
    summary = S.GroupSummary(insufficient=group["insufficient"], reason=group["reason"], n=group["n"], max_review_gap=None if group["max_gap"] is None else group["max_gap"] / 10,
                             current_median_price=med, price_rank=None if group["insufficient"] else _price_rank(anchor["currentPrice"], peers), per_horizon=per_horizon)
    anchor_gap = 0 if _tenths(store, anchor["hotelId"]) is not None else None
    anchor_dto = _compset_hotel(store, anchor, median_price=med, gap_tenths=anchor_gap, is_anchor=True)
    compset = [_compset_hotel(store, p, median_price=med, gap_tenths=g, is_anchor=False) for p, g in peers]

    prices = []
    for pair in store.by_hotel[hotel_id]:
        g = _group(store, pair)
        prices.append(S.CheckInPrice(check_in=pair["checkIn"], anchor_price=pair["currentPrice"], group_median_price=g["median_price"], n=g["n"]))

    alerts: list[S.HotelierAlert] = []
    if not group["insufficient"]:
        pct_anchor = anchor_dto.pct_vs_group_median
        if pct_anchor is not None and abs(pct_anchor) >= rules.POSITION_ALERT_PCT:
            alerts.append(S.HotelierAlert(id=f"position-{check_in}", severity="attention", category="position", title=labels.render("alert_position_title"),
                                          description=labels.render("alert_position_desc", pct=labels.fmt_pct(pct_anchor), n=group["n"]),
                                          rule=S.AlertRule(name="position_gap", threshold=rules.POSITION_ALERT_PCT, value=pct_anchor)))
        for item in per_horizon:
            if abs(item.median_pct_change) > rules.MARKET_ALERT_PCT:
                alerts.append(S.HotelierAlert(id=f"market-h{item.horizon}", severity="info", category="market", title=labels.render("alert_market_title"),
                                              description=labels.render("alert_market_desc", horizon=item.horizon, pct=labels.fmt_pct(item.median_pct_change), threshold=labels.fmt_plain(rules.MARKET_ALERT_PCT)),
                                              rule=S.AlertRule(name="market_move", threshold=rules.MARKET_ALERT_PCT, value=item.median_pct_change, horizon=item.horizon)))
    warnings: list[S.ApiWarning] = []
    if not group["insufficient"] and group["n"] < 2 * rules.MIN_GROUP_SIZE:
        warnings.append(S.ApiWarning(code="group_small", message=labels.render("group_small", n=group["n"])))
    if group["max_gap"] is not None and group["max_gap"] > rules.REVIEW_BAND_TENTHS:
        warnings.append(S.ApiWarning(code="review_gap_wide", message=labels.render("review_gap_wide", gap=labels.fmt_plain(group["max_gap"] / 10))))
    if not store.hotels[hotel_id].get("metadata"):
        warnings.append(S.ApiWarning(code="metadata_missing", message=labels.render("metadata_missing")))
    return S.HotelierResponse(snapshot_id=store.snapshot_id, data_as_of=store.data_as_of, check_in=check_in, anchor=anchor_dto, compset=compset, group_summary=summary,
                              check_in_prices=prices, anchor_forecast=_forecast(store, anchor), alerts=alerts, warnings=warnings)


# ---------------------------------------------------------------- model-info
def _vn_date(iso_utc: Optional[str]) -> str:
    """ISO UTC -> 'DD/MM/YYYY' theo ngày VN (UTC+7). Thiếu -> '—'."""
    if not iso_utc:
        return "—"
    moment = datetime.fromisoformat(iso_utc.replace("Z", "+00:00")) + timedelta(hours=7)
    return moment.strftime("%d/%m/%Y")


def _triple(values: list[float]) -> list[float]:
    return [round(100.0 * v, 4) for v in values]


def get_model_info(store: SnapshotStore) -> S.ModelInfoResponse:
    info = store.model_info
    horizons = []
    for item in info["horizons"]:
        ev = item["evidence"]
        ordinary = ev["ordinary"]
        key = "ordinary_warning" if ordinary["belowThreshold"] else "ordinary_note"
        lo, hi = ordinary["testLiftPct"][1], ordinary["testLiftPct"][2]
        limits = [
            labels.render(key, share_pct=labels.fmt_plain(ordinary["shareOfRowsPct"]), lift_pct=labels.fmt_pct(ordinary["testLiftPct"][0]), lo_pct=labels.fmt_pct(lo), hi_pct=labels.fmt_pct(hi)),
            labels.render("served_variant_note", lift_pct=labels.fmt_pct(ev["testRoutedLiftPct"][0]), lo_pct=labels.fmt_pct(ev["testRoutedLiftPct"][1]), hi_pct=labels.fmt_pct(ev["testRoutedLiftPct"][2])),
            labels.render("cold_start_limit"), labels.render("concentration"), labels.render("test_exposed"),
        ]
        comp = item["compatibility"]
        horizons.append(S.HorizonModelInfo(
            horizon=item["horizon"], task="regression", model_name=item["modelName"], family=item["family"], dataset_version=item["datasetVersion"], claim_level=item["claimLevel"],
            bundle_sha256=item["bundleSha256"], trained_environment=item["trainedEnvironment"], validated_runtime=item["validatedRuntime"],
            compatibility=S.Compatibility(status=comp["status"], rows=comp["rows"], max_abs_diff_vnd=comp["maxAbsDiffVnd"], rows_over_tolerance=comp["rowsOverTolerance"], tolerance_vnd=comp["toleranceVnd"]),
            routing=[S.RoutingInfo(mode=r["mode"], use_model=r["useModel"], reason=r["reason"], validation_lift_vnd=r["validationLiftVnd"], ci95=r["ci95"], validation_rows=r["validationRows"]) for r in item["routing"]],
            evidence=S.HorizonEvidence(validation_lift_pct=ev["validationLiftPct"], test_lift_pct=ev["testLiftPct"], test_exposed=True, source=ev["source"], source_sha256=ev["sourceSha256"]),
            limitations=limits))
    cap = _classifier_capability(store)
    cls = info.get("classifier", {"horizons": []})
    cls_limits = [labels.render(key) for key in CLASSIFIER_LIMIT_KEYS] + [labels.render("test_exposed")]       # câu chữ do nhãn đã khóa, không do builder snapshot viết
    classifier = S.ClassifierModelInfo(available=cap["available"], reason=cap["reason"], claim="none",
                                       note=labels.render("classifier_note") if cap["available"] else labels.render("classifier_missing"),
                                       horizons=[S.ClassifierHorizonModelInfo(**{**h, "limitations": cls_limits}) for h in cls.get("horizons", [])] if cap["available"] else [])
    rule_infos = [
        S.RuleInfo(name="direction", description=labels.render("rule_direction", threshold=labels.fmt_plain(rules.DIRECTION_THRESHOLD_PCT)), threshold=rules.DIRECTION_THRESHOLD_PCT),
        S.RuleInfo(name="position_alert", description=labels.render("rule_position_alert", threshold=labels.fmt_plain(rules.POSITION_ALERT_PCT)), threshold=rules.POSITION_ALERT_PCT),
        S.RuleInfo(name="market_alert", description=labels.render("rule_market_alert", threshold=labels.fmt_plain(rules.MARKET_ALERT_PCT), min_n=rules.MIN_GROUP_SIZE), threshold=rules.MARKET_ALERT_PCT),
        S.RuleInfo(name="group", description=labels.render("rule_group", max_peers=rules.MAX_PEERS, min_n=rules.MIN_GROUP_SIZE), threshold=float(rules.MIN_GROUP_SIZE)),
    ]
    exported = _vn_date((store.manifest.get("hotelsMeta") or {}).get("exportedAtUtc"))
    notes = [labels.render("note_meaning"), labels.render("note_unsupported_horizon"), labels.render("note_snapshot_scope"),
             labels.render("metadata_date_note", exported_at=exported), labels.render("no_soldout"), labels.render("not_booking_site")]
    return S.ModelInfoResponse(snapshot=_snapshot_info(store), horizons=horizons, unsupported_horizons=[S.UnsupportedHorizonConfig(horizon=h, reason="model_not_available") for h, _ in UNSUPPORTED],
                               classifier=classifier, rules=rule_infos, notes=notes)
