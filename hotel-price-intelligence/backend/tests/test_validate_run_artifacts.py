"""Test thuần (không DB/Settings/Selenium) cho validate_run_artifacts rev2: fail-closed, hai chiều, phạm vi điều kiện, oracle = hợp đồng parser, mutation controls."""
import ast
import gzip
import importlib.util
import itertools
import unicodedata
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRAPER_SRC = HERE.parent / "app" / "scraper" / "booking_scraper.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = _load(HERE.parent / "scripts" / "validate_run_artifacts.py", "validator_under_test")
from app.scraper import parser as PARSER  # noqa: E402

AFF = "Bao gồm bữa sáng rất ngon"
NEG = "Không bao gồm bữa sáng"
PAID = "Bữa sáng VND 100.000 (không bắt buộc)"
CXL = "Hủy miễn phí trước 25 tháng 10, 2026"


def make_html(rows, extra_body=""):
    out = ['<html><body>', extra_body, '<table id="hprt-table"><tbody>']
    for name, lines in rows:
        items = "".join(f'<li class="bui-list__item">{line}</li>' for line in lines)
        out.append(
            f'<tr class="js-rt-block-row"><th class="hprt-table-cell-roomtype"><a class="hprt-roomtype-link">{name}</a></th>'
            f'<td class="hprt-table-cell-conditions"><ul class="bui-list">{items}</ul></td><td>VND 1.000.000</td></tr>')
    out.append('</tbody></table></body></html>')
    return "".join(out)


def make_item(tmp_path, rows, breakfast, *, status="success", write=True, extra_body="", item_id=1, **overrides):
    path = tmp_path / f"page{item_id}.html.gz"
    if write:
        path.write_bytes(gzip.compress(make_html(rows, extra_body).encode("utf-8")))
    rooms = [{"room_option_index": i, "price_per_night": 1000000, "original_price": None, "bed_config": None, "room_area": None,
              "cancellation_policy": None, "discount_percent": None, "price_includes_tax": None,
              "breakfast_included": breakfast[i], "free_cancellation": None, "rooms_left": None} for i in range(len(rows))]
    item = {"id": item_id, "status": status, "artifact_html_path": str(path), "dom_room_row_count": len(rows),
            "hotel_link": "https://x.test/h?checkin=2026-10-15&checkout=2026-10-16", "checkin_date": "2026-10-15",
            "checkout_date": "2026-10-16", "hotel_name": None, "hotel_address": None, "rooms": rooms,
            "duplicate_options_count": 0, "rejected_options_count": 0}
    item.update(overrides)
    return item


def fields(report):
    return [m["field"] for m in report["mismatches"]]


# ---------------- hai chiều + phạm vi ----------------
@pytest.mark.parametrize("line,db,ok", [
    (AFF, True, True), (AFF, False, False), (NEG, True, False), (NEG, False, True),
    (PAID, False, True), (PAID, True, False), ("Miễn phí hủy bất kỳ lúc nào", False, True),
])
def test_breakfast_checked_both_ways(tmp_path, line, db, ok):
    report = V.validate_item(make_item(tmp_path, [("Deluxe", [line, CXL])], [db]))
    assert (report["status"] == "ok") is ok, report
    if not ok:
        assert "breakfast_included" in fields(report)


def test_breakfast_mention_outside_condition_scope_is_not_evidence(tmp_path):
    body = '<div class="review">Bao gồm bữa sáng rất ngon, khách sạn tuyệt vời</div>'
    assert V.validate_item(make_item(tmp_path, [("Deluxe", [PAID, CXL])], [False], extra_body=body))["status"] == "ok"
    assert V.validate_item(make_item(tmp_path, [("Deluxe Bao gồm bữa sáng", [CXL])], [False], item_id=2))["status"] == "ok"


# ---------------- DB NULL (N03-M1) ----------------
def test_db_none_with_determinate_dom_condition_is_a_missing_value_mismatch(tmp_path):
    for line in (AFF, NEG, PAID, CXL):
        report = V.validate_item(make_item(tmp_path, [("Deluxe", [line])], [None]))
        assert report["status"] == "mismatch" and "breakfast_included" in fields(report), line
        assert report["mismatches"][0]["dom"] == "missing_value"


def test_db_none_with_truly_empty_conditions_is_counted_separately_not_as_confirmed(tmp_path):
    report = V.validate_item(make_item(tmp_path, [("Deluxe", [])], [None]))
    assert report["status"] == "ok"
    assert report["breakfast"] == {"true_confirmed": 0, "false_confirmed": 0, "null_consistent_empty_conditions": 1}
    summary = V.summarize([report], [1])
    assert summary["breakfast_options_validated"] == 0                       # NULL không tăng số đã xác nhận
    item = make_item(tmp_path, [("Deluxe", [])], [None], item_id=3)
    item["rooms"][0]["rooms_left"] = 2                                      # có rooms_left nghĩa là parser đã có dòng => NULL là thiếu giá trị
    assert V.validate_item(item)["status"] == "mismatch"


# ---------------- fail-closed: lỗi, trạng thái, tập item ----------------
def test_missing_corrupt_and_exception_are_errors_and_exit_nonzero(tmp_path):
    missing = V.validate_item(make_item(tmp_path, [("D", [AFF])], [True], write=False))
    assert missing["status"] == "error" and missing["error"] == "missing_artifact"
    bad = tmp_path / "bad.html.gz"
    bad.write_bytes(b"not gzip")
    corrupt = V.validate_item(make_item(tmp_path, [("D", [AFF])], [True], artifact_html_path=str(bad), item_id=2))
    assert corrupt["status"] == "error" and corrupt["error"] == "corrupt_artifact"
    broken = make_item(tmp_path, [("D", [AFF])], [True], item_id=3)
    broken["rooms"][0].pop("room_option_index")
    exception = V.validate_item(broken)
    assert exception["status"] == "error" and exception["error"] == "validator_exception"
    for report in (missing, corrupt, exception):
        assert V.summarize([report], [report["item_id"]])["exit_code"] == 1


def test_status_handling(tmp_path):
    ready = make_item(tmp_path, [("D", [AFF])], [True], status="queued")                   # queued CÓ hàng giá: không được thành N/A
    assert V.validate_item(ready)["status"] == "not_ready"
    assert V.summarize([V.validate_item(ready)], [1])["exit_code"] == 1
    assert V.validate_item(make_item(tmp_path, [("D", [AFF])], [True], status="weird", item_id=2))["error"] == "unknown_status"
    no_price = {"id": 3, "status": "success", "rooms": []}
    assert V.validate_item(no_price)["error"] == "priced_status_without_priced_rooms"
    sold_with_price = make_item(tmp_path, [("D", [AFF])], [True], status="sold_out", item_id=4)
    assert V.validate_item(sold_with_price)["error"] == "priced_rooms_on_non_priced_status"
    ok_na = [V.validate_item({"id": 5, "status": "sold_out", "rooms": [{"room_option_index": 0, "price_per_night": None}]}),
             V.validate_item({"id": 6, "status": "error", "rooms": []}), V.validate_item({"id": 7, "status": "not_bookable", "rooms": []})]
    assert [r["status"] for r in ok_na] == ["not_applicable"] * 3 and V.summarize(ok_na, [5, 6, 7])["exit_code"] == 0


def _ok(i):
    return {"item_id": i, "status": "ok", "mismatch_count": 0, "mismatches": []}


def test_summary_exact_set_and_multiplicity():
    assert V.summarize([_ok(1), _ok(2)], [1, 2])["exit_code"] == 0
    assert V.summarize([_ok(1)], [1, 2])["exit_code"] == 1                  # thiếu
    assert V.summarize([_ok(1), _ok(9)], [1])["exit_code"] == 1             # thừa (probe GPT: trước đây exit 0)
    assert V.summarize([_ok(1), _ok(1)], [1])["exit_code"] == 1             # trùng báo cáo (probe GPT: trước đây exit 0)
    assert V.summarize([_ok(1)], [1, 1])["exit_code"] == 1                  # trùng ở tập mong đợi
    assert V.summarize([], [])["exit_code"] == 1                            # tập rỗng (probe GPT: trước đây exit 0)
    assert V.summarize([_ok("1")], ["1"])["exit_code"] == 1                 # ID sai kiểu
    unknown = {"item_id": 1, "status": "ok?", "mismatch_count": 0, "mismatches": []}
    assert V.summarize([unknown], [1])["exit_code"] == 1                    # trạng thái báo cáo lạ
    with pytest.raises(TypeError):
        V.summarize([_ok(1)])                                                # expected_item_ids bắt buộc, không lấy từ chính reports


def test_unmappable_is_never_waived(tmp_path):
    item = make_item(tmp_path, [("D", [AFF]), ("E", [NEG])], [True, False], duplicate_options_count=1)
    item["rooms"] = item["rooms"][:1]
    report = V.validate_item(item)
    assert report["status"] == "skipped_unmappable" and report["explained_by_accounting"] is True
    assert V.summarize([report], [1])["exit_code"] == 1
    unexplained = dict(report, explained_by_accounting=False)
    assert V.summarize([unexplained], [1])["exit_code"] == 1
    with pytest.raises(TypeError):
        V.summarize([report], [1], allow_skipped=[1])                        # cơ chế waiver đã bị gỡ


# ---------------- mutation controls ----------------
@pytest.mark.parametrize("always,expect_fail_lines", [(True, {NEG, PAID}), (False, {AFF})])
def test_mutation_parser_always_true_or_false_is_caught(tmp_path, always, expect_fail_lines):
    caught = set()
    for n, line in enumerate((AFF, NEG, PAID)):
        report = V.validate_item(make_item(tmp_path, [("Deluxe", [line, CXL])], [always], item_id=n + 1))
        if report["status"] != "ok":
            caught.add(line)
    assert caught == expect_fail_lines


# ---------------- oracle = hợp đồng đã khóa của parser ----------------
REGISTERED = [(NEG, False), (AFF, True), ("Giá không bao gồm bữa sáng", False), ("Chưa bao gồm bữa sáng", False),
              ("Bao gồm bữa sáng, không bao gồm bữa tối", True), ("Không bao gồm bữa tối, bao gồm bữa sáng", True),
              ("Không bao gồm bữa sáng; bao gồm bữa tối", False), ("Bao gồm bữa sáng (trẻ em: không bao gồm bữa sáng)", True),
              ("No breakfast included", False), ("Breakfast included", True), ("Breakfast not included", False),
              ("KHÔNG BAO GỒM BỮA SÁNG", False), (PAID, False), ("Không có bữa sáng", False), ("Not breakfast included", False),
              ("Without breakfast included", False), ("Casino breakfast included", True), ("Piano breakfast included", True),
              ("Bao bữa sáng kiểu lục địa", True), ("Giá bao gồm bữa sáng", True),
              (unicodedata.normalize("NFD", NEG), False), (unicodedata.normalize("NFD", AFF), True)]
# 6 probe GPT (file 04 §2): hợp đồng khóa = cụm CHÍNH XÁC + từ phủ định NGAY trước (chỉ khoảng trắng) => dấu câu/đa khoảng trắng/NBSP trong cụm KHÔNG khớp
SCOPED_CONTRACT = [("Không: bao gồm bữa sáng", True), ("Không (bao gồm bữa sáng)", True), ("Bao-gồm bữa sáng", False),
                   ("Bao  gồm bữa sáng", False), ("Bao gồm\u00a0bữa sáng", False), ("Breakfast, included", False)]


@pytest.mark.parametrize("line,want", REGISTERED + SCOPED_CONTRACT)
def test_oracle_and_parser_agree_with_registered_and_scoped_contract(line, want):
    assert V.line_affirms_breakfast(line) is want, line
    assert PARSER.parse_room_conditions([line])["breakfast_included"] is want, line


def test_differential_oracle_vs_parser_on_generated_lines():
    phrases = ["bao gồm bữa sáng", "Bao gồm bữa sáng", "BAO GỒM BỮA SÁNG", "bao bữa sáng", "Breakfast included", "breakfast  included",
               "bao-gồm bữa sáng", "bao\u00a0gồm bữa sáng", "Breakfast, included"]
    prefixes = ["", "Giá ", "Không ", "không  ", "Không\u00a0", "Không: ", "Không (", "Không-", "Chưa ", "No ", "no", "Not ", "not-", "Without ",
                "Casino ", "Piano ", "xkhông ", "Knot ", "NO\t", "\u00a0không ", "Không, ", "Không và ", "Không_", "Không"]
    suffixes = ["", " ngon", ", không bao gồm bữa tối", " (trẻ em: không bao gồm bữa sáng)", "; bao gồm bữa sáng"]
    checked = disagreements = 0
    for prefix, phrase, suffix in itertools.product(prefixes, phrases, suffixes):
        for form in ("NFC", "NFD"):
            line = unicodedata.normalize(form, prefix + phrase + suffix)
            if not line.strip():
                continue
            parser_value = PARSER.parse_room_conditions([line])["breakfast_included"]
            oracle_value = V.line_affirms_breakfast(line)
            checked += 1
            if parser_value is not oracle_value:
                disagreements += 1
                assert False, (repr(line), parser_value, oracle_value)
    assert checked > 2000 and disagreements == 0


def test_oracle_negation_and_other_classes():
    assert V.line_negates_breakfast(NEG) and not V.line_affirms_breakfast(NEG)
    assert V.line_affirms_breakfast(AFF) and not V.line_negates_breakfast(AFF)
    assert V.line_mentions_breakfast_other(PAID) and not V.line_affirms_breakfast(PAID) and not V.line_negates_breakfast(PAID)
    assert not V.line_mentions_breakfast_other(CXL)


def test_facility_selectors_do_not_drift_from_the_scraper():
    tree = ast.parse(SCRAPER_SRC.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("_FACILITY_SELECTORS", "_FACILITY_BROADER_SELECTORS"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    assert found["_FACILITY_SELECTORS"] == V.FACILITY_SELECTORS
    assert found["_FACILITY_BROADER_SELECTORS"] == V.FACILITY_BROADER_SELECTORS


# ---------------- rev4: ngày sau redirect đổi slug CHỈ tin form#hprt-form (+ đồng thuận form#top-book) — GPT file 10 ----------------
REQ = "https://x.test/h?group_adults=2&checkin=2026-10-15&checkout=2026-10-16"
FINAL_NO_DATES = "https://x.test/h-renamed?label=abc&sid=def"


def _form(fid, checkin="2026-10-15", checkout=None, interval="1", extra=""):
    parts = [f'<form id="{fid}">']
    if checkin is not None:
        parts.append(f'<input type="hidden" name="checkin" value="{checkin}">')
    if checkout is not None:
        parts.append(f'<input type="hidden" name="checkout" value="{checkout}">')
    if interval is not None:
        parts.append(f'<input type="hidden" name="interval" value="{interval}">')
    parts.append(extra + "</form>")
    return "".join(parts)


GOOD_FORMS = _form("hprt-form") + _form("top-book")


def _redirect_item(tmp_path, *, final=FINAL_NO_DATES, requested=REQ, body=GOOD_FORMS, item_id=1):
    return make_item(tmp_path, [("Deluxe", [AFF, CXL])], [True], extra_body=body, item_id=item_id, hotel_link=final, requested_hotel_link=requested)


def test_final_url_without_dates_is_valid_only_with_paired_dates_from_the_booking_form(tmp_path):
    report = V.validate_item(_redirect_item(tmp_path))
    assert report["status"] == "ok" and report["redirect_dropped_dates_verified_by_dom"] == ["checkin", "checkout"]
    summary = V.summarize([report], [1])
    assert summary["exit_code"] == 0 and summary["redirect_items_with_dropped_url_dates_verified_by_dom"] == [1]
    for n, body in enumerate((_form("hprt-form", checkout="2026-10-16", interval=None) + _form("top-book"),          # checkout tường minh
                              _form("hprt-form", checkout="2026-10-16", interval="1") + _form("top-book"),            # cả hai đồng thuận
                              _form("hprt-form") + _form("top-book", checkout="2026-10-16", interval=None)), start=2):
        assert V.validate_item(_redirect_item(tmp_path, body=body, item_id=n))["status"] == "ok", body


# 5 probe đối kháng GPT (file 10 §2): bản rev3 chấp nhận cả 5 — rev4 phải TỪ CHỐI
@pytest.mark.parametrize("name,body", [
    ("checkout_wrong_expected_only_in_comment", _form("hprt-form", checkout="2026-10-20", interval=None) + _form("top-book") + "<!-- 2026-10-16 -->"),
    ("checkout_wrong_expected_only_in_review", _form("hprt-form", checkout="2026-10-20", interval=None) + _form("top-book") + '<div class="review">Ở 2026-10-16 rất tốt</div>'),
    ("checkout_wrong_expected_only_in_script", _form("hprt-form", checkout="2026-10-20", interval=None) + _form("top-book") + '<script>var d="2026-10-16";</script>'),
    ("two_conflicting_checkin_in_form", '<form id="hprt-form"><input name="checkin" value="2026-10-15"><input name="checkin" value="2026-10-18"><input name="interval" value="1"></form>' + _form("top-book")),
    ("checkin_wrong_in_booking_form_expected_in_unrelated_form", _form("hprt-form", checkin="2026-10-20") + _form("top-book") + _form("search-widget")),
])
def test_rev3_false_accepts_are_rejected(tmp_path, name, body):
    report = V.validate_item(_redirect_item(tmp_path, body=body))
    assert report["status"] == "mismatch" and {"url_checkin", "url_checkout"} & set(fields(report)), name
    assert report["redirect_dropped_dates_verified_by_dom"] != ["checkin", "checkout"]


@pytest.mark.parametrize("name,body", [
    ("no_hprt_form", _form("top-book")),
    ("duplicate_hprt_form", _form("hprt-form") + _form("hprt-form") + _form("top-book")),
    ("interval_zero", _form("hprt-form", interval="0") + _form("top-book", interval="0")),
    ("interval_negative", _form("hprt-form", interval="-1") + _form("top-book")),
    ("interval_non_numeric", _form("hprt-form", interval="abc") + _form("top-book")),
    ("interval_decimal", _form("hprt-form", interval="1.5") + _form("top-book")),
    ("interval_wrong_derives_other_checkout", _form("hprt-form", interval="2") + _form("top-book", interval="2")),
    ("explicit_checkout_vs_interval_conflict", _form("hprt-form", checkout="2026-10-17", interval="1") + _form("top-book")),
    ("no_checkout_source", _form("hprt-form", interval=None) + _form("top-book")),
    ("malformed_date", _form("hprt-form", checkin="15/10/2026") + _form("top-book")),
    ("missing_checkin_input", _form("hprt-form", checkin=None) + _form("top-book")),
    ("top_book_disagrees", _form("hprt-form") + _form("top-book", checkin="2026-10-16")),
    ("top_book_malformed", _form("hprt-form") + _form("top-book", checkin=None)),
    ("top_book_duplicated", _form("hprt-form") + _form("top-book") + _form("top-book")),
    ("expected_dates_only_in_unrelated_form", _form("hprt-form", checkin="2026-10-30") + _form("search-widget")),
    ("no_forms_at_all", ""),
])
def test_form_date_evidence_controls_reject(tmp_path, name, body):
    report = V.validate_item(_redirect_item(tmp_path, body=body))
    assert report["status"] == "mismatch" and ({"url_checkin", "url_checkout"} & set(fields(report))), name
    assert report["redirect_dropped_dates_verified_by_dom"] != ["checkin", "checkout"], name


def test_final_url_that_keeps_dates_must_match_and_requested_url_must_carry_the_item_dates(tmp_path):
    ok = V.validate_item(_redirect_item(tmp_path, final=REQ, body=""))
    assert ok["status"] == "ok" and ok["redirect_dropped_dates_verified_by_dom"] == []
    wrong_final = V.validate_item(_redirect_item(tmp_path, final=REQ.replace("2026-10-15", "2026-10-20"), item_id=2))
    assert wrong_final["status"] == "mismatch" and "url_checkin" in fields(wrong_final)
    wrong_requested = V.validate_item(_redirect_item(tmp_path, requested=REQ.replace("2026-10-16", "2026-10-17"), item_id=3))
    assert wrong_requested["status"] == "mismatch" and "requested_url_checkout" in fields(wrong_requested)
    no_dates_requested = V.validate_item(_redirect_item(tmp_path, requested="https://x.test/h?group_adults=2", item_id=4))
    assert no_dates_requested["status"] == "mismatch" and "requested_url_checkin" in fields(no_dates_requested)


def test_legacy_item_without_requested_link_keeps_the_strict_final_url_rule(tmp_path):
    assert V.validate_item(_redirect_item(tmp_path, requested=None))["status"] == "mismatch"      # URL cuối mất ngày, không có requested link => không miễn trừ
    assert V.validate_item(_redirect_item(tmp_path, requested=None, final=REQ, body="", item_id=2))["status"] == "ok"
