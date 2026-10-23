"""API dashboard (A2–A4b, A6–A9) trên snapshot TỔNG HỢP nhỏ: không DB, không ML, không mạng. Chạy được cả trong backend\\venv (fastapi/pydantic/httpx)."""
import json
import re
from pathlib import Path

import pytest

import dashboard_fixtures as F
from app.dashboard import labels, rules, service
from app.dashboard.app import create_app
from app.dashboard.settings import Settings, load_settings
from app.dashboard.store import SnapshotUnavailable, load_store

P = "/api/dashboard/v1"


def make_client(tmp_path, **kwargs):
    root = tmp_path / "serving"
    F.write_snapshot(root, **kwargs)
    settings = Settings(serving_root=root, cors_origins=("http://localhost:3100", "http://127.0.0.1:3100"))
    return F.SyncClient(create_app(settings))


@pytest.fixture()
def client(tmp_path):
    return make_client(tmp_path)


def get(client, path, **params):
    return client.get(P + path, params=params)


def error_code(response):
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) <= {"code", "message", "details"}
    return body["error"]["code"]


# ---------------------------------------------------------------- health / config / hotels
def test_health_config_and_label(client):
    assert get(client, "/health").json() == {"status": "ok", "snapshotId": F.SNAPSHOT_ID, "reason": None}
    cfg = get(client, "/config").json()
    assert cfg["snapshot"]["dataAsOf"] == "2026-10-04" and cfg["snapshot"]["claimLevel"] == "dev_research_exploratory"
    assert cfg["snapshot"]["label"] == labels.render("snapshot_label", data_as_of="04/10/2026")
    assert cfg["horizons"] == [1, 3] and [u["horizon"] for u in cfg["unsupportedHorizons"]] == [7, 14] and all(u["reason"] == "model_not_available" for u in cfg["unsupportedHorizons"])
    assert cfg["classifier"] == {"available": False, "reason": "dependency_missing"}
    assert cfg["limits"] == {"minGroupSize": 5, "positionAlertPct": 15.0, "marketAlertPct": 2.0}


def test_hotels_lists_only_pairs_with_forecast_and_real_horizons_per_pair(client):
    body = get(client, "/hotels").json()
    by_id = {h["id"]: h for h in body["hotels"]}
    assert "anchor-hotel" in by_id and by_id["anchor-hotel"]["name"] == "Anchor Hotel" and by_id["anchor-hotel"]["reviewScore"] == 8.0
    options = {o["date"]: o["supportedHorizons"] for o in by_id["anchor-hotel"]["checkIns"]}
    assert options == {"2026-10-05": [1], "2026-10-07": [1, 3], "2026-10-12": [1, 3]}                  # ngày giữa min/max nhưng hổng (06, 08–11) KHÔNG có
    assert all(o["supportedHorizons"] for h in body["hotels"] for o in h["checkIns"]) and len(body["hotels"]) <= 1000
    assert by_id["noname-slug"]["name"] == "noname-slug" and by_id["noname-slug"]["metadataAsOf"] is None
    assert by_id["anchor-hotel"]["metadataAsOf"] == "2026-10-22T17:51:57Z"


def test_hotels_city_filter_and_invalid_city(client):
    assert {h["city"] for h in get(client, "/hotels", city="Đà Lạt").json()["hotels"]} == {"Đà Lạt"}
    r = get(client, "/hotels", city="Atlantis")
    assert r.status_code == 422 and error_code(r) == "invalid_parameter"


# ---------------------------------------------------------------- forecast
def test_forecast_shape_units_and_horizons_by_lead(client):
    r = get(client, "/forecast", hotelId="anchor-hotel", checkIn="2026-10-05")                       # lead 1
    assert r.status_code == 200
    body = r.json()
    assert body["supportedHorizons"] == [1] and [p["horizon"] for p in body["predictions"]] == [1]
    assert {(u["horizon"], u["reason"]) for u in body["unsupportedHorizons"]} == {(3, "after_checkin"), (7, "model_not_available"), (14, "model_not_available")}
    p1 = body["predictions"][0]
    assert p1["expectedPctChange"] == 1.0 and p1["direction"] == "stable" and p1["targetObservationDate"] == "2026-10-05" and p1["method"] == "model"
    assert p1["expectedDelta"] == pytest.approx(10_000.0) and p1["predictedPrice"] == pytest.approx(1_010_000.0)
    assert body["leadDays"] == 1 and body["dataAsOf"] == "2026-10-04" and body["currentObservedAt"].endswith("Z")
    assert body["evidence"] == {"level": "dev_exploratory", "claim": "exploratory"}
    assert "confidenceTier" not in r.text and "confidence" not in r.text.lower().replace("claimlevel", "")
    assert [h["date"] for h in body["history"]] == sorted(h["date"] for h in body["history"]) and body["history"][-1]["date"] <= "2026-10-04"
    assert body["provenance"]["bundles"] == {"h1": "reg-h1-test", "h3": "reg-h3-test"}
    assert body["classifier"] == {"available": False, "reason": "dependency_missing", "results": [], "claim": "none"}
    both = get(client, "/forecast", hotelId="anchor-hotel", checkIn="2026-10-07").json()
    assert both["supportedHorizons"] == [1, 3] and both["predictions"][1]["expectedPctChange"] == 2.5 and both["predictions"][1]["direction"] == "up"
    assert both["predictions"][1]["targetObservationDate"] == "2026-10-07"


def test_forecast_cold_start_null_span_and_persistence_warning_and_metadata(client):
    body = get(client, "/forecast", hotelId="noname-slug", checkIn="2026-10-12").json()
    assert body["inferenceMode"] == "cold_start" and body["historySpanDays"] is None and body["historyObservationCount"] == 0
    assert body["hotel"]["name"] == "noname-slug" and body["hotel"]["reviewScore"] is None
    codes = [w["code"] for w in body["warnings"]]
    assert codes.count("cold_start_preliminary") == 1 and codes.count("persistence_routed") == 1 and "metadata_missing" in codes       # h1 persistence; h3 model
    p1 = body["predictions"][0]
    assert p1["method"] == "persistence" and p1["expectedPctChange"] == 0.0 and p1["direction"] == "stable" and p1["predictedPrice"] == 400_000 and p1["routingReason"] == "ci_lower_not_positive"
    text = get(client, "/forecast", hotelId="noname-slug", checkIn="2026-10-12").text
    assert '"historySpanDays":null' in text.replace(" ", "") and "NaN" not in text and "Infinity" not in text


@pytest.mark.parametrize("params, status, code", [
    ({"hotelId": "ghost", "checkIn": "2026-10-12"}, 404, "hotel_not_found"),
    ({"hotelId": "anchor-hotel", "checkIn": "2026-10-20"}, 404, "check_in_not_supported"),            # sau as-of nhưng không có cặp
    ({"hotelId": "anchor-hotel", "checkIn": "2026-10-06"}, 404, "check_in_not_supported"),            # giữa min/max nhưng hổng
    ({"hotelId": "anchor-hotel", "checkIn": "2026-10-04"}, 422, "no_forecast_after_checkin"),         # lead 0
    ({"hotelId": "anchor-hotel", "checkIn": "2026-09-01"}, 422, "no_forecast_after_checkin"),         # quá khứ
    ({"hotelId": "anchor-hotel", "checkIn": "2026-13-01"}, 422, "invalid_parameter"),
    ({"hotelId": "anchor-hotel", "checkIn": "12/10/2026"}, 422, "invalid_parameter"),
    ({"hotelId": "anchor-hotel", "checkIn": "2026-02-30"}, 422, "invalid_parameter"),
    ({"hotelId": "anchor-hotel"}, 422, "invalid_parameter"),
    ({"checkIn": "2026-10-12"}, 422, "invalid_parameter"),
    ({"hotelId": "../etc/passwd", "checkIn": "2026-10-12"}, 422, "invalid_parameter"),
    ({"hotelId": "a" * 300, "checkIn": "2026-10-12"}, 422, "invalid_parameter"),
])
def test_forecast_error_matrix(client, params, status, code):
    r = get(client, "/forecast", **params)
    assert r.status_code == status and error_code(r) == code
    for needle in ("Traceback", "D:\\", "C:\\", ".py", "File \""):
        assert needle not in r.text


def test_error_messages_come_from_label_constants(client):
    r = get(client, "/forecast", hotelId="ghost", checkIn="2026-10-12")
    assert r.json()["error"]["message"] == labels.TEMPLATES["err_hotel_not_found"]


# ---------------------------------------------------------------- A2 units & direction at the API boundary
def world_with_pct(pct: float):
    hotels = [F.hotel("solo", "Hà Nội", 80)]
    return [F.pair("solo", "Hà Nội", "2026-10-12", 1_000_000, h1_pct=pct, lead=8)], hotels


@pytest.mark.parametrize("pct, direction", [(10.0, "up"), (2.0, "stable"), (-2.0, "stable"), (2.0001, "up"), (-2.0001, "down"), (0.0, "stable"), (-64.4, "down")])
def test_a2_percentage_points_and_strict_direction(tmp_path, pct, direction):
    pairs, hotels = world_with_pct(pct)
    c = make_client(tmp_path, pairs=pairs, hotels=hotels)
    p = get(c, "/forecast", hotelId="solo", checkIn="2026-10-12").json()["predictions"][0]
    assert p["expectedPctChange"] == pytest.approx(pct) and p["direction"] == direction == rules.direction_from_pct(pct)
    assert p["predictedPrice"] == pytest.approx(1_000_000 * (1 + pct / 100)) and p["expectedDelta"] == pytest.approx(1_000_000 * pct / 100)


# ---------------------------------------------------------------- hotelier
def test_hotelier_group_b_median_rank_and_exclusions(client):
    body = get(client, "/hotelier", hotelId="anchor-hotel", checkIn="2026-10-12").json()
    ids = [h["id"] for h in body["compset"]]
    assert ids == ["p-80", "p-79", "p-78", "p-82", "p-83", "p-75", "p-85"]                              # (gap, hotelId); p-86 (0,6) và far-91 bị loại; anchor không nằm trong peers
    assert "anchor-hotel" not in ids and "far-91" not in ids and "p-86" not in ids
    gap = {h["id"]: h["reviewGap"] for h in body["compset"]}
    assert gap["p-85"] == 0.5 and gap["p-75"] == 0.5 and gap["p-80"] == 0.0                              # đúng 0,5 được GIỮ (số nguyên)
    g = body["groupSummary"]
    assert g["insufficient"] is False and g["reason"] is None and g["n"] == 7 and g["maxReviewGap"] == 0.5
    assert g["currentMedianPrice"] == 1_000_000 and g["priceRank"] == {"rank": 4, "of": 8}
    assert body["anchor"]["isAnchor"] is True and body["anchor"]["pctVsGroupMedian"] == 0.0 and body["anchor"]["reviewGap"] == 0.0
    h1 = [x for x in g["perHorizon"] if x["horizon"] == 1][0]
    assert h1["nMatched"] == 7 and h1["nModel"] == 7 and h1["nPersistence"] == 0
    assert h1["currentMedianPrice"] == 1_000_000 and h1["predictedMedianPrice"] == pytest.approx(1_010_000) and h1["medianPctChange"] == pytest.approx(1.0)
    peer = {h["id"]: h for h in body["compset"]}["p-85"]
    assert peer["pctVsGroupMedian"] == pytest.approx(20.0) and peer["isAnchor"] is False                 # (1.2M − 1.0M)/1.0M
    assert [w["code"] for w in body["warnings"]] == ["group_small"]
    assert body["anchorForecast"]["hotel"]["id"] == "anchor-hotel"
    assert [a["id"] for a in body["alerts"]] == ["market-h3"]          # h1 median +1,0 ≤ 2 ⇒ không alert; h3 median +2,5 > 2 ⇒ alert; vị thế 0% ⇒ không alert


def test_hotelier_market_alert_only_when_strictly_above_threshold(tmp_path):
    def world(pct):
        hotels = [F.hotel("anc", "Hà Nội", 80)] + [F.hotel(f"q{i}", "Hà Nội", 80) for i in range(5)]
        pairs = [F.pair(h["hotelId"], "Hà Nội", "2026-10-12", 1_000_000, h1_pct=pct, lead=8, h3_pct=pct, record_id=i) for i, h in enumerate(hotels)]
        return pairs, hotels
    for pct, expected in ((2.0, 0), (2.0001, 2), (-2.5, 2)):
        pairs, hotels = world(pct)
        body = get(make_client(tmp_path / f"w{pct}", pairs=pairs, hotels=hotels), "/hotelier", hotelId="anc", checkIn="2026-10-12").json()
        alerts = [a for a in body["alerts"] if a["category"] == "market"]
        assert len(alerts) == expected, (pct, alerts)
        for a in alerts:
            assert a["rule"]["name"] == "market_move" and a["rule"]["threshold"] == 2.0 and abs(a["rule"]["value"]) > 2.0 and a["rule"]["horizon"] in (1, 3)


def test_hotelier_position_alert_at_fifteen_percent(tmp_path):
    hotels = [F.hotel("anc", "Hà Nội", 80)] + [F.hotel(f"q{i}", "Hà Nội", 80) for i in range(5)]
    for price, expect in ((1_150_000, 1), (1_149_999, 0), (850_000, 1), (850_001, 0)):
        pairs = [F.pair("anc", "Hà Nội", "2026-10-12", price, lead=8, record_id=1)] + [F.pair(h["hotelId"], "Hà Nội", "2026-10-12", 1_000_000, lead=8, record_id=i + 2) for i, h in enumerate(hotels[1:])]
        body = get(make_client(tmp_path / f"p{price}", pairs=pairs, hotels=hotels), "/hotelier", hotelId="anc", checkIn="2026-10-12").json()
        assert len([a for a in body["alerts"] if a["category"] == "position"]) == expect, price


def test_hotelier_market_alert_appears_in_fixture_for_h3(client):
    body = get(client, "/hotelier", hotelId="anchor-hotel", checkIn="2026-10-07").json()
    markets = [a for a in body["alerts"] if a["category"] == "market"]
    assert [a["id"] for a in markets] == ["market-h3"] and markets[0]["rule"]["value"] == pytest.approx(2.5)
    assert "%" in markets[0]["description"] and labels.banned_hits(markets[0]["description"]) == []


@pytest.mark.parametrize("hotel_id, check_in, reason, n", [("lonely", "2026-10-12", "group_too_small", 3), ("norev", "2026-10-12", "anchor_review_missing", 0), ("noname-slug", "2026-10-12", "anchor_review_missing", 0)])
def test_a4b_insufficient_group_has_no_denominator_and_no_alerts(client, hotel_id, check_in, reason, n):
    r = get(client, "/hotelier", hotelId=hotel_id, checkIn=check_in)
    assert r.status_code == 200
    body = r.json()
    g = body["groupSummary"]
    assert g["insufficient"] is True and g["reason"] == reason and g["n"] == n and len(body["compset"]) == n
    assert g["currentMedianPrice"] is None and g["priceRank"] is None and g["perHorizon"] == [] and body["alerts"] == []
    assert body["anchor"]["pctVsGroupMedian"] is None and all(h["pctVsGroupMedian"] is None for h in body["compset"])
    assert all(c["groupMedianPrice"] is None for c in body["checkInPrices"])
    assert "NaN" not in r.text and "Infinity" not in r.text
    assert body["anchor"]["pctVsGroupMedian"] is None                                    # null, KHÔNG phải 0


@pytest.mark.parametrize("n_peers, insufficient", [(0, True), (1, True), (4, True), (5, False)])
def test_a4b_witness_zero_one_four_five_peers(tmp_path, n_peers, insufficient):
    hotels = [F.hotel("anc", "Hà Nội", 80)] + [F.hotel(f"q{i}", "Hà Nội", 80) for i in range(n_peers)]
    pairs = [F.pair(h["hotelId"], "Hà Nội", "2026-10-12", 1_000_000 + 1000 * i, lead=8, record_id=i) for i, h in enumerate(hotels)]
    body = get(make_client(tmp_path, pairs=pairs, hotels=hotels), "/hotelier", hotelId="anc", checkIn="2026-10-12").json()
    assert body["groupSummary"]["insufficient"] is insufficient and body["groupSummary"]["n"] == n_peers
    assert (body["groupSummary"]["currentMedianPrice"] is None) is insufficient


def test_hotelier_check_in_prices_one_row_per_anchor_pair(client):
    body = get(client, "/hotelier", hotelId="anchor-hotel", checkIn="2026-10-12").json()
    rows = {r["checkIn"]: r for r in body["checkInPrices"]}
    assert set(rows) == {"2026-10-05", "2026-10-07", "2026-10-12"} and all(r["n"] == 7 and r["groupMedianPrice"] == 1_000_000 and r["anchorPrice"] == 1_000_000 for r in rows.values())


def test_hotelier_peer_cap_is_fifteen_and_ties_break_by_id(tmp_path):
    hotels = [F.hotel("anc", "Hà Nội", 80)] + [F.hotel(f"q{i:02d}", "Hà Nội", 80) for i in range(20)]
    pairs = [F.pair(h["hotelId"], "Hà Nội", "2026-10-12", 1_000_000, lead=8, record_id=i) for i, h in enumerate(hotels)]
    body = get(make_client(tmp_path, pairs=pairs, hotels=hotels), "/hotelier", hotelId="anc", checkIn="2026-10-12").json()
    assert [h["id"] for h in body["compset"]] == [f"q{i:02d}" for i in range(15)] and body["groupSummary"]["n"] == 15 and body["warnings"] == []


def test_hotelier_errors_follow_same_matrix(client):
    assert get(client, "/hotelier", hotelId="ghost", checkIn="2026-10-12").status_code == 404
    assert error_code(get(client, "/hotelier", hotelId="anchor-hotel", checkIn="2026-10-04")) == "no_forecast_after_checkin"
    assert error_code(get(client, "/hotelier", hotelId="anchor-hotel", checkIn="bad")) == "invalid_parameter"


# ---------------------------------------------------------------- model-info
def test_model_info_evidence_limitations_and_rules(client):
    body = get(client, "/model-info").json()
    h1, h3 = body["horizons"]
    assert (h1["horizon"], h1["task"], h3["horizon"]) == (1, "regression", 3) and h1["evidence"]["testExposed"] is True
    assert h1["evidence"]["validationLiftPct"] == [14.5, 9.0, 19.9] and h1["compatibility"]["toleranceVnd"] == 0.01
    assert any("không gọi là cải thiện ròng" in x for x in h3["limitations"]) and not any("không gọi là cải thiện ròng" in x for x in h1["limitations"])
    assert any("đã lộ" in x for x in h1["limitations"]) and any("tập trung" in x for x in h1["limitations"])
    assert body["classifier"]["available"] is False and body["classifier"]["claim"] == "none" and body["classifier"]["horizons"] == []
    assert body["classifier"]["note"] == labels.TEMPLATES["classifier_missing"]
    assert {r["name"] for r in body["rules"]} == {"direction", "position_alert", "market_alert", "group"}
    assert [u["horizon"] for u in body["unsupportedHorizons"]] == [7, 14] and len(body["notes"]) == 6
    assert "confidenceTier" not in json.dumps(body)


# ---------------------------------------------------------------- A6/A7/A8/A9 vận hành & cô lập
@pytest.mark.parametrize("mutate", ["missing_current", "bad_manifest_hash", "tampered_catalog", "wrong_schema", "missing_file", "bad_snapshot_id", "empty_catalog"])
def test_corrupt_or_missing_snapshot_is_503_and_health_unavailable(tmp_path, mutate):
    root = tmp_path / "serving"
    directory = F.write_snapshot(root, **({"schema": "serving-snapshot-1"} if mutate == "wrong_schema" else {}), **({"drop_files": ("history.json",)} if mutate == "missing_file" else {}),
                                 **({"pairs": [], "hotels": []} if mutate == "empty_catalog" else {}))
    if mutate == "missing_current":
        (root / "CURRENT.json").unlink()
    elif mutate == "bad_manifest_hash":
        (directory / "manifest.json").write_bytes((directory / "manifest.json").read_bytes() + b" ")
    elif mutate == "tampered_catalog":
        (directory / "catalog.json").write_bytes((directory / "catalog.json").read_bytes().replace(b"1000000", b"1000001", 1))
    elif mutate == "bad_snapshot_id":
        (root / "CURRENT.json").write_text(json.dumps({"snapshotId": "../../etc", "manifestSha256": "0" * 64}), encoding="utf-8")
    c = F.SyncClient(create_app(Settings(serving_root=root, cors_origins=())))
    health = get(c, "/health").json()
    assert health["status"] == "unavailable" and health["snapshotId"] is None and health["reason"]
    for path, params in (("/config", {}), ("/hotels", {}), ("/forecast", {"hotelId": "anchor-hotel", "checkIn": "2026-10-12"}), ("/hotelier", {"hotelId": "anchor-hotel", "checkIn": "2026-10-12"}), ("/model-info", {})):
        r = get(c, path, **params)
        assert r.status_code == 503 and error_code(r) == "snapshot_unavailable", (mutate, path)
        assert str(tmp_path) not in r.text


def test_store_rejects_nan_constants_in_json(tmp_path):
    root = tmp_path / "serving"
    directory = F.write_snapshot(root)
    data = (directory / "catalog.json").read_bytes().replace(b'"currentPrice":1000000.0', b'"currentPrice":NaN', 1)
    assert b"NaN" in data
    (directory / "catalog.json").write_bytes(data)
    with pytest.raises(SnapshotUnavailable):
        load_store(root)


def test_unexpected_exception_returns_generic_500_without_leaks(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret path D:\\MSE\\x.py")
    monkeypatch.setattr(service, "get_config", boom)
    r = get(client, "/config")
    assert r.status_code == 500 and error_code(r) == "internal_error" and "secret" not in r.text and "D:" not in r.text


def test_only_get_routes_without_path_params_and_405_is_json(client):
    paths = [r for r in client.app.routes if hasattr(r, "methods")]
    assert paths and all(set(r.methods) <= {"GET", "HEAD", "OPTIONS"} for r in paths)
    assert all("{" not in r.path for r in paths if r.path.startswith(P))
    assert all(r.path.startswith(P) for r in paths)
    r = client.post(P + "/config")
    assert r.status_code == 405 and "error" in r.json()
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_cors_only_for_frontend_origins_without_credentials(client):
    ok = client.get(P + "/health", headers={"Origin": "http://localhost:3100"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3100" and "access-control-allow-credentials" not in ok.headers
    bad = client.get(P + "/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in bad.headers


def test_settings_guard_ports_hosts_and_wildcards(tmp_path):
    with pytest.raises(ValueError):
        load_settings({"DASHBOARD_PORT": "8000"})
    with pytest.raises(ValueError):
        load_settings({"DASHBOARD_HOST": "0.0.0.0"})
    with pytest.raises(ValueError):
        load_settings({"DASHBOARD_CORS_ORIGINS": "*"})
    s = load_settings({})
    assert s.port == 8100 and s.host == "127.0.0.1" and s.cors_origins == ("http://localhost:3100", "http://127.0.0.1:3100")
    assert load_settings({"DASHBOARD_SERVING_ROOT": str(tmp_path)}).serving_root == tmp_path


def test_every_user_visible_api_message_is_a_rendered_constant(client):
    templates = [re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", ".+?", re.escape(t)) + "$") for t in labels.TEMPLATES.values()]
    pools = []
    for params in ({"hotelId": "noname-slug", "checkIn": "2026-10-12"}, {"hotelId": "anchor-hotel", "checkIn": "2026-10-07"}):
        body = get(client, "/forecast", **params).json()
        pools += [w["message"] for w in body["warnings"]]
    for hid in ("anchor-hotel", "lonely"):
        pools += [a["title"] for a in get(client, "/hotelier", hotelId=hid, checkIn="2026-10-07" if hid == "anchor-hotel" else "2026-10-12").json()["alerts"]]
        pools += [a["description"] for a in get(client, "/hotelier", hotelId=hid, checkIn="2026-10-07" if hid == "anchor-hotel" else "2026-10-12").json()["alerts"]]
    info = get(client, "/model-info").json()
    pools += info["notes"] + [r["description"] for r in info["rules"]] + [x for h in info["horizons"] for x in h["limitations"]] + [info["classifier"]["note"], get(client, "/config").json()["snapshot"]["label"]]
    assert pools
    for message in pools:
        assert any(t.match(message) for t in templates), message
        assert labels.banned_hits(message) == [], message


def test_json_never_contains_nan_or_infinity_anywhere(client):
    for path, params in (("/config", {}), ("/hotels", {}), ("/forecast", {"hotelId": "anchor-hotel", "checkIn": "2026-10-12"}), ("/hotelier", {"hotelId": "anchor-hotel", "checkIn": "2026-10-12"}), ("/model-info", {})):
        text = get(client, path, **params).text
        assert not re.search(r"\bNaN\b|\bInfinity\b", text), path


# ---------------------------------------------------------------- nhánh classifier AVAILABLE trên fixture tổng hợp (không model thật, không xgboost)
def test_classifier_available_branch_results_are_subset_of_the_pairs_own_horizons(tmp_path):
    c = make_client(tmp_path, classifier_available=True)
    cfg = get(c, "/config").json()
    assert cfg["classifier"] == {"available": True, "reason": None}
    lead1 = get(c, "/forecast", hotelId="anchor-hotel", checkIn="2026-10-05").json()
    assert lead1["supportedHorizons"] == [1] and lead1["classifier"]["available"] is True and lead1["classifier"]["claim"] == "none"
    assert [(r["horizon"], r["label"], r["recordId"]) for r in lead1["classifier"]["results"]] == [(1, "down", lead1["provenance"]["recordId"])]          # KHÔNG có h3
    both = get(c, "/forecast", hotelId="anchor-hotel", checkIn="2026-10-12").json()
    assert [(r["horizon"], r["label"]) for r in both["classifier"]["results"]] == [(1, "down"), (3, "stable")] and all(r["recordId"] == both["provenance"]["recordId"] for r in both["classifier"]["results"])
    assert "probability" not in json.dumps(both).lower() and "proba" not in json.dumps(both).lower()                                                   # không phát xác suất


def test_classifier_available_model_info_has_full_provenance_child(tmp_path):
    body = get(make_client(tmp_path, classifier_available=True), "/model-info").json()
    info = body["classifier"]
    assert info["available"] is True and info["claim"] == "none" and info["note"] == labels.TEMPLATES["classifier_note"] and [h["horizon"] for h in info["horizons"]] == [1, 3]
    child = info["horizons"][0]
    assert set(child) == {"horizon", "task", "modelName", "family", "datasetVersion", "claimLevel", "bundleSha256", "trainedEnvironment", "validatedRuntime", "classMapping", "compatibility", "coverage", "limitations"}
    assert child["classMapping"] == {"0": "down", "1": "stable", "2": "up"} and child["compatibility"]["labelMismatchCount"] == 0 and child["coverage"]["recordIdsLinkedToRegression"] is True


def test_classifier_unavailable_never_leaks_labels_even_if_snapshot_contains_them(tmp_path):
    root = tmp_path / "serving"
    pairs, hotels = F.default_world()
    F.write_snapshot(root, pairs=F.with_classifier(pairs), hotels=hotels, classifier_available=False)          # nhãn có trong catalog nhưng capability tắt
    c = F.SyncClient(create_app(Settings(serving_root=root, cors_origins=())))
    body = get(c, "/forecast", hotelId="anchor-hotel", checkIn="2026-10-12").json()
    assert body["classifier"] == {"available": False, "reason": "dependency_missing", "results": [], "claim": "none"}
