"""Data dictionary Wave A (EDA_CURATED_PLAN.md muc 9): moi cot xuat hien trong bang publish PHAI co dinh nghia ngu nghia (khong chi dump
information_schema) - ten bien, nguon, grain, kieu/don vi, timezone, allowed values, structural-missing rule, eligibility scope, dung lam
feature hay chi audit, leakage caveat (GPT review 12 eda file 11 muc 6.4: 'mo rong dictionary cho toan bo field duoc publish, nhat la
collision/divergence, availability, missingness va denominator columns').

Cot co ten rieng -> `_FIELDS` (dinh nghia tay). Cot dang chuan (`n_*`, `*_rate`, `p<so>`, `is_*`...) -> `_PATTERNS` (sinh dinh nghia tu ten +
quy uoc denominator ghi trong TABLE_METADATA.csv). `describe(column)` tra ve dict day du khoa hoac None; test kiem tra moi cot cua moi bang
publish deu resolve duoc.
"""
from __future__ import annotations

import re
from typing import Callable

FIELD_KEYS = ("definition", "source", "grain", "type_unit", "timezone", "allowed_values", "structural_missing",
              "eligibility_scope", "usage", "leakage_caveat")

_AUDIT_USAGE = "chi audit/mo ta EDA - KHONG phai feature"
_NO_LEAK = "Thong ke toan lich su warehouse (biet sau thoi diem du bao) - khong duoc dua vao feature/train, chi mo ta"


def _d(definition: str, *, source: str = "warehouse", grain: str = "theo bang", unit: str = "n/a", tz: str = "n/a", allowed: str = "tu do",
       structural: str = "khong co", scope: str = "xem TABLE_METADATA.csv", usage: str = _AUDIT_USAGE, leakage: str = _NO_LEAK) -> dict[str, str]:
    return {"definition": definition, "source": source, "grain": grain, "type_unit": unit, "timezone": tz, "allowed_values": allowed,
            "structural_missing": structural, "eligibility_scope": scope, "usage": usage, "leakage_caveat": leakage}


_VN = "Asia/Ho_Chi_Minh (+07:00 co dinh, tu UTC)"
_PRICE_SRC = "price_observations.price_per_night"
_FEATURE_OK = "co the la feature v1 neu quan sat co tai prediction_time (xem plan muc 5) - trong Wave A chi mo ta"
_DATE_LEAK = "Ngay check-in/quan sat la thuoc tinh cua observation, khong phai thong tin tuong lai"

_FIELDS: dict[str, dict[str, str]] = {
    # ---- dinh danh / chieu phan tich
    "source_code": _d("Ma nguon thu thap (local_primary/vps/...) - provenance ETL, KHONG phai feature.", source="etl_item_map.source_code",
                      grain="item/run", allowed="dang mo (registry etl_import_sources)", scope="RAW+MAIN"),
    "owner_source": _d("Nguon so huu (owner) cua slot theo ownership manifest.", source="ownership manifest", grain="expected slot", scope="PROTOCOL"),
    "source_a": _d("Nguon co ma nho hon (thu tu bang chu) trong cap collision.", source="etl_item_map.source_code", grain="item pair", scope="RAW"),
    "source_b": _d("Nguon co ma lon hon trong cap collision.", source="etl_item_map.source_code", grain="item pair", scope="RAW"),
    "item_id_a": _d("crawl_run_items.id (warehouse) cua item nguon A trong cap collision.", source="crawl_run_items.id", grain="item pair", scope="RAW"),
    "item_id_b": _d("crawl_run_items.id (warehouse) cua item nguon B trong cap collision.", source="crawl_run_items.id", grain="item pair", scope="RAW"),
    "item_id": _d("crawl_run_items.id (warehouse) cua owned item; khoa audit noi bo, khong phai feature.", source="crawl_run_items.id", grain="item", scope="MAIN"),
    "item_finished_at": _d("Thoi diem owned item ket thuc (UTC), dung de audit collision/protocol.", source="crawl_run_items.finished_at", grain="item",
                           unit="datetime", tz="UTC", scope="MAIN"),
    "hotel_id": _d("Slug URL Booking cua khach san (khoa on dinh xuyen suot cac lan crawl); '(unattributed)' = item loi chua resolve hotel.",
                   source="hotels.hotel_id / crawl_run_items.hotel_id", grain="hotel", allowed="slug hoac '(unattributed)'",
                   structural="NULL o item error truoc khi parser luu hotel_id (M2)", scope="RAW+MAIN"),
    "city": _d("Thanh pho cua hotel (hotels.city) trong 5 thanh pho scope; '(unknown)' = hotel chua co city; '(all)' = tong hop.",
               source="hotels.city", grain="hotel", allowed="Ho Chi Minh/Ha Noi/Vung Tau/Da Lat/Phu Quoc/(unknown)/(all)", usage=_FEATURE_OK),
    "checkin_date": _d("Ngay nhan phong (ngay luu tru) - KHONG phai ngay quan sat.", source="price_observations.checkin_date / crawl_run_items.checkin_date",
                       grain="observation/item", unit="date", usage=_FEATURE_OK, leakage=_DATE_LEAK),
    "vn_crawl_date": _d("Ngay crawl theo gio Viet Nam = DATE(started_at UTC + 7h) cua run.", source="crawl_runs.started_at", grain="run/item",
                        unit="date", tz=_VN, leakage=_DATE_LEAK),
    "crawl_date": _d("Ngay crawl theo gio Viet Nam (cung y nghia vn_crawl_date, ten dung o bang protocol/availability).", source="crawl_runs.started_at",
                     grain="run/item", unit="date", tz=_VN, leakage=_DATE_LEAK),
    "vn_observation_date": _d("Ngay quan sat theo gio VN = DATE(observed_at UTC + 7h) - dung cho daily snapshot/turnover, KHONG dung timezone he thong.",
                              source="price_observations.observed_at", grain="observation", unit="date", tz=_VN, leakage=_DATE_LEAK),
    "observed_date_min": _d("Ngay quan sat (VN) som nhat cua observation co gia MAIN.", source="price_observations.observed_at", grain="warehouse", unit="date", tz=_VN),
    "observed_date_max": _d("Ngay quan sat (VN) muon nhat cua observation co gia MAIN.", source="price_observations.observed_at", grain="warehouse", unit="date", tz=_VN),
    "checkin_date_min": _d("Ngay check-in som nhat cua observation co gia MAIN.", source="price_observations.checkin_date", grain="warehouse", unit="date"),
    "checkin_date_max": _d("Ngay check-in muon nhat cua observation co gia MAIN.", source="price_observations.checkin_date", grain="warehouse", unit="date"),
    "checkin_month": _d("Thang cua ngay check-in, dinh dang YYYY-MM.", source="checkin_date", grain="thang", unit="text YYYY-MM", usage=_FEATURE_OK, leakage=_DATE_LEAK),
    "weekday_number": _d("Thu trong tuan cua check-in theo MySQL WEEKDAY(): 0=Thu Hai ... 6=Chu Nhat.", source="checkin_date", grain="thu", unit="0-6",
                         usage=_FEATURE_OK, leakage=_DATE_LEAK),
    "weekday": _d("Ten thu (tieng Anh) cua ngay check-in.", source="checkin_date", grain="thu", unit="text", usage=_FEATURE_OK, leakage=_DATE_LEAK),
    "is_weekend_fri_sat": _d("Check-in la Thu Sau hoac Thu Bay (quy uoc CLAUDE.md muc 5.1 'thu 6, 7', KHONG phai Thu Bay/Chu Nhat).", source="checkin_date",
                             grain="thu", unit="boolean (0/1)", allowed="0/1", usage=_FEATURE_OK, leakage=_DATE_LEAK),
    "lead_time_bucket": _d("Bucket lead time (ngay giua ngay quan sat/crawl VN va check-in): 0, 1-3, 4-7, 8-14, 15-30, 31-60, 61+ ('(invalid)' neu lead_time am - khong ky vong).",
                           source="price_observations.lead_time hoac DATEDIFF(checkin_date, vn_crawl_date)", grain="observation/item", unit="bucket (ngay)",
                           allowed="0,1-3,4-7,8-14,15-30,31-60,61+,(invalid); bucket legacy: 0-3,4-7,8-14,15-30,31-60,61+", usage=_FEATURE_OK,
                           leakage="lead_time biet tai prediction_time; bucket legacy chi dung doi chieu bang lich su"),
    "history_days_bucket": _d("Bucket so ngay crawl khac nhau co item success cua (hotel, check-in): 1, 2, 3-6, 7-13, 14-29, 30+.", source="crawl_run_items",
                              grain="series (hotel_id, checkin_date)", unit="bucket", scope="MAIN"),
    "evidence_runs_bucket": _d("Bucket so run completed co item success trong scope REFERENCE EVIDENCE: 1, 2, 3+.", source="crawl_run_items/crawl_runs",
                               grain="series (hotel_id, checkin_date)", unit="bucket", allowed="1,2,3+", scope="REFERENCE EVIDENCE"),
    "status": _d("Tuy bang: trang thai terminal cua item (success/sold_out/not_bookable/partial/error), HOAC trang thai reference (proposed/approved/retired), "
                 "HOAC trang thai su kien holiday (confirmed/provisional - provisional = lich chua duoc cong bo chinh thuc).",
                 source="crawl_run_items.status / hotel_reference_rooms.status / data/vn_holidays.csv", grain="item/reference/event",
                 allowed="theo bang", scope="MAIN/REFERENCE EVIDENCE/CONFIG"),
    "item_status": _d("crawl_run_items.status cua item cha cua observation.", source="crawl_run_items.status", grain="observation", allowed="success/sold_out/partial", scope="MAIN"),
    "ownership_status": _d("Ket qua ownership resolution cua item (owner_success/owner_failure/non_owner_duplicate/protocol_deviation/unassigned).",
                           source="etl_item_map.ownership_status", grain="item", scope="RAW"),
    "ownership_status_a": _d("ownership_status cua item A trong cap collision.", source="etl_item_map.ownership_status", grain="item pair", scope="RAW"),
    "ownership_status_b": _d("ownership_status cua item B trong cap collision.", source="etl_item_map.ownership_status", grain="item pair", scope="RAW"),
    "exclusion_reason": _d("Ly do item bi loai khoi MAIN/reference/train (vd non_owner_duplicate, owner_failure_status_error, off_plan...).",
                           source="etl_item_map.exclusion_reason", grain="item", structural="NULL khi khong bi loai", scope="RAW"),
    "outcome": _d("Ket qua cua 1 slot expected trong lich protocol: owner_success, owner_failure_status_{sold_out,not_bookable,error}, missing_source_run "
                  "(ca ngay khong co run cua nguon), missing_item_in_existing_run (co run nhung thieu item nay).",
                  source="protocol_schedule.classify_outcomes", grain="expected slot", allowed="6 gia tri PROTOCOL_ITEM_OUTCOMES", scope="PROTOCOL"),
    "schedule_slot": _d("Ma slot trong ownership manifest (N1..N9/F1..F3, V1...).", source="ownership manifest", grain="expected slot", scope="PROTOCOL"),
    "last_error_code": _d("Ma loi cuoi cua item (dead_link, dead_link_skipped, parser_empty, driver_init, property_not_bookable...); '(none)' neu khong co. "
                          "Cac ma CAPTCHA/block (neu xuat hien o batch sau) hien trong cot nay.", source="crawl_run_items.last_error_code", grain="item", scope="RAW"),
    "run_id": _d("crawl_runs.id (warehouse) cua run.", source="crawl_runs.id", grain="run", scope="RAW"),
    "started_at_vn": _d("Thoi diem run bat dau, gio VN.", source="crawl_runs.started_at", grain="run", unit="datetime", tz=_VN, scope="RAW"),
    "finished_at_vn": _d("Thoi diem run ket thuc, gio VN.", source="crawl_runs.finished_at", grain="run", unit="datetime", tz=_VN, scope="RAW"),
    "finish_hour_vn": _d("Gio VN (0-23) run ket thuc.", source="crawl_runs.finished_at", grain="run", unit="gio", tz=_VN, allowed="0-23", scope="RAW"),
    "duration_minutes": _d("Thoi luong run = finished_at - started_at.", source="crawl_runs", grain="run", unit="phut", scope="RAW"),
    "items_per_hour": _d("So item trong run / thoi luong (gio).", source="derived", grain="run", unit="item/gio", scope="RAW"),
    "observations_per_hour": _d("So observation trong run / thoi luong (gio).", source="derived", grain="run", unit="observation/gio", scope="RAW"),
    "crosses_next_crawl_day": _d("Run ket thuc o ngay VN sau ngay bat dau (keo qua ngay crawl ke tiep).", source="crawl_runs", grain="run", unit="boolean", scope="RAW"),
    "canonical_series_id": _d("ID canonical (room, rate) cua 1 series gia - hash on dinh tu curated_observation_keys.", source="curated_observation_keys.canonical_series_id",
                              grain="canonical series", scope="MAIN"),
    "canonical_room_key": _d("Khoa canonical phong (curated).", source="curated_observation_keys.canonical_room_key", grain="observation", scope="RAW"),
    "canonical_rate_key": _d("Khoa canonical rate plan (curated).", source="curated_observation_keys.canonical_rate_key", grain="observation", scope="RAW"),
    "record_id": _d("price_observations.record_id (warehouse) - khoa audit, KHONG dung sort/join giua nguon.", source="price_observations.record_id", grain="observation"),
    "horizon_days": _d("Horizon du bao K (1/3/7/14 ngay); dong K=0 khong xuat hien trong bang publish.", source="metrics.HORIZON_DAYS", grain="horizon", unit="ngay", allowed="1,3,7,14"),
    "field": _d("Ten field duoc do missingness; rate-plan bao gom ca free_cancellation.", source="price_observations/hotels/crawl_runs", grain="field",
                allowed="cac field da dang ky trong queries._MISSINGNESS_FIELD_GROUPS"),
    "field_group": _d("Nhom field: room_identity, rate_plan, price, hotel_attributes, artifact_source_metadata.", source="queries._MISSINGNESS_FIELD_GROUPS", grain="field",
                      allowed="5 nhom"),
    "missing_kind": _d("Phan loai missing: structural_expected cho sentinel sold-out; unexpected_if_null cho field payload; "
                       "structural_not_requested khi run khong yeu cau luu artifact; unexpected_if_missing khi da yeu cau artifact.",
                       source="queries missingness/artifact rules", grain="field_value_cell hoac item group",
                       allowed="structural_expected/unexpected_if_null/structural_not_requested/unexpected_if_missing", scope="MAIN"),
    "save_artifacts": _d("Run co yeu cau luu HTML/screenshot hay khong.", source="crawl_runs.save_artifacts", grain="run/item", unit="boolean",
                         allowed="TRUE/FALSE", scope="MAIN"),
    "is_sold_out": _d("Observation la sentinel sold-out (khong co gia; CLAUDE.md muc 4.5).", source="price_observations.is_sold_out", grain="observation",
                      unit="boolean", structural="TRUE => price_per_night PHAI NULL (sentinel demand, khong phai gia 0)", scope="MAIN"),
    "time_diff_bucket": _d("Bucket chenh lech thoi gian (phut) giua 2 nguon: 0-5, 6-15, 16-60, 61+ (bien lien tuc, quy uoc floor).", source="derived", grain="pair",
                           allowed="0-5,6-15,16-60,61+", scope="RAW"),
    "is_concordant": _d("status_a == status_b (duong cheo cua bang concordance).", source="derived", grain="item pair", unit="boolean", scope="RAW"),
    "dump_taken_at": _d("Thoi diem chup dump cua nguon (UTC).", source="etl_import_sources.dump_taken_at", grain="source", unit="datetime", tz="UTC", scope="CONFIG"),
    "source_priority": _d("Do uu tien cua nguon trong batch (so nho = uu tien cao).", source="etl_import_sources.source_priority", grain="source", scope="CONFIG"),
    "dump_sha256": _d("SHA-256 file dump nguon.", source="etl_import_sources.dump_sha256", grain="source", unit="hex64", scope="CONFIG"),
    "schema_sha256": _d("SHA-256 schema dump nguon.", source="etl_import_sources.schema_sha256", grain="source", unit="hex64", scope="CONFIG"),
    "hotels": _d("So dong bang hotels trong warehouse (count nen doi soat voi validation report).", source="hotels", grain="warehouse (1 dong)", unit="so nguyen", scope="RAW"),
    "crawl_runs": _d("So dong bang crawl_runs trong warehouse.", source="crawl_runs", grain="warehouse (1 dong)", unit="so nguyen", scope="RAW"),
    "crawl_run_items": _d("So dong bang crawl_run_items trong warehouse.", source="crawl_run_items", grain="warehouse (1 dong)", unit="so nguyen", scope="RAW"),
    "price_observations": _d("So dong bang price_observations trong warehouse (gom ca sentinel sold-out).", source="price_observations", grain="warehouse (1 dong)",
                             unit="so nguyen", scope="RAW"),
    "curated_observation_keys": _d("So dong curated_observation_keys (phai bang price_observations - moi observation co dung 1 canonical key).",
                                   source="curated_observation_keys", grain="warehouse (1 dong)", unit="so nguyen", scope="RAW"),
    "runs": _d("So crawl run khac nhau cua nguon trong ngay crawl VN (COUNT DISTINCT).", source="crawl_runs", grain="source x vn_crawl_date", unit="so nguyen", scope="RAW"),
    "items": _d("So item (crawl_run_items) RAW cua nguon trong ngay crawl VN.", source="crawl_run_items", grain="source x vn_crawl_date", unit="so nguyen", scope="RAW"),
    "observations": _d("So price_observations (gom sentinel sold-out) cua item RAW trong nguon/ngay crawl VN.", source="price_observations", grain="source x vn_crawl_date",
                       unit="so nguyen", scope="RAW"),
    "selector_version": _d("Phien ban selector cua crawler ghi trong crawl_runs; '(unknown)' neu NULL (run cu truoc khi ghi version).", source="crawl_runs.selector_version",
                           grain="run", structural="NULL o run tao truoc khi migration ghi version", scope="MAIN"),
    "kind": _d("Loai dong cua truy van canonical_series_facts_main: 'joint' = phan phoi chung (n_observed_days x max_gap_days); 'sample' = 1 series (sample audit).",
               source="queries.canonical_series_facts_main", grain="canonical series -> nhom / series", allowed="joint/sample", scope="MAIN"),
    "series_id": _d("canonical_series_id = SHA-256(hotel_id, checkin_date, room_key, rate_key) - dinh danh series (chi co o dong sample).",
                    source="curated_observation_keys.canonical_series_id", grain="canonical series", unit="hex64", structural="NULL o dong kind='joint'", scope="MAIN"),
    "non_terminal_runs": _d("So crawl_runs khong terminal (status khac completed/failed) - PHAI = 0 (assert truoc khi thu thap).", source="crawl_runs.status",
                            grain="warehouse (1 dong)", unit="so nguyen", scope="RAW"),
    "non_terminal_items": _d("So crawl_run_items dang queued/running - PHAI = 0 (assert truoc khi thu thap).", source="crawl_run_items.status", grain="warehouse (1 dong)",
                             unit="so nguyen", scope="RAW"),
    "source_hotel_link": _d("URL nguon cua item - CHI lay cho item co hotel_id NULL (de resolve slug), NULL o item con lai (tiet kiem bo nho).",
                            source="crawl_run_items.source_hotel_link", grain="item", structural="NULL khi item da co hotel_id", scope="MAIN"),
    "source_link_hash": _d("SHA-256 URL nguon - CHI lay cho item co hotel_id NULL (sample key cho item khong resolve duoc).", source="crawl_run_items.source_link_hash",
                           grain="item", unit="hex64", structural="NULL khi item da co hotel_id", scope="MAIN"),
    "check": _d("Ten phep doi soat count nen (hotels/crawl_runs/.../rejections/reference_*).", source="build_input_manifest", grain="check", scope="RAW"),
    "eda_value": _d("Gia tri EDA doc tu warehouse.", source="warehouse", grain="check", scope="RAW"),
    "validation_report_value": _d("Gia tri ghi trong warehouse validation report cua batch.", source="reports/<batch>.json", grain="check", scope="RAW"),
    "match": _d("eda_value == validation_report_value.", source="derived", grain="check", unit="boolean", scope="RAW"),
    "cohort_version": _d("Version cohort khach san (v1.0/v1.1/v2).", source="cohort_history JSON", grain="cohort version", scope="CONFIG"),
    "effective_from_crawl_date": _d("Ngay crawl dau tien cohort version co hieu luc (theo du lieu that, khong theo workbook hien tai).", source="cohort_history JSON",
                                    grain="cohort version", unit="date", tz=_VN, scope="CONFIG"),
    "size": _d("So khach san trong cohort version.", source="cohort_history JSON", grain="cohort version", scope="CONFIG"),
    "size_change": _d("size - size cua version lien truoc.", source="derived", grain="cohort version", scope="CONFIG"),
    "change_type": _d("baseline/attrition/addition/unchanged so voi version lien truoc (Mac Valley = attrition tu nhien, khong phai thay the).",
                      source="metrics.cohort_attrition_table", grain="cohort version", allowed="baseline/attrition/addition/unchanged", scope="CONFIG"),
    "members_sha256": _d("SHA-256 tap thanh vien cohort (canonical, tu loader warehouse).", source="cohort_history JSON", grain="cohort version", unit="hex64", scope="CONFIG"),
    # ---- holiday / calendar
    "holiday_date": _d("Ngay dien ra su kien (duong lich; am lich da quy doi).", source="data/vn_holidays.csv", grain="event", unit="date", scope="CONFIG", usage=_FEATURE_OK),
    "event_code": _d("Ma on dinh cua su kien.", source="data/vn_holidays.csv", grain="event", scope="CONFIG"),
    "name": _d("Ten su kien (tieng Viet).", source="data/vn_holidays.csv", grain="event", scope="CONFIG"),
    "event_type": _d("public_holiday / festival / major_event.", source="data/vn_holidays.csv", grain="event", allowed="public_holiday/festival/major_event", scope="CONFIG"),
    "scope": _d("national (ap dung ca 5 thanh pho) hoac city (chi dung thanh pho).", source="data/vn_holidays.csv", grain="event", allowed="national/city", scope="CONFIG"),
    "is_tet": _d("Su kien/ngay nam trong giai doan Tet Nguyen Dan.", source="data/vn_holidays.csv", grain="event / (checkin_date, city)", unit="boolean", scope="CONFIG", usage=_FEATURE_OK),
    "source_url": _d("URL nguon cua thong tin su kien.", source="data/vn_holidays.csv", grain="event", scope="CONFIG"),
    "is_public_holiday": _d("Ngay check-in la ngay nghi le (event_type=public_holiday) ap dung cho city do.", source="derived tu vn_holidays.csv", grain="(checkin_date, city)",
                            unit="boolean", scope="CONFIG", usage=_FEATURE_OK),
    "is_festival_period": _d("Ngay check-in nam trong festival (event_type=festival) ap dung cho city do.", source="derived tu vn_holidays.csv", grain="(checkin_date, city)",
                             unit="boolean", scope="CONFIG", usage=_FEATURE_OK),
    "is_major_event": _d("Ngay check-in co major_event ap dung cho city do.", source="derived tu vn_holidays.csv", grain="(checkin_date, city)", unit="boolean",
                         scope="CONFIG", usage=_FEATURE_OK),
    "holiday_event_count": _d("So su kien (national + city) trung ngay check-in cua city, aggregate TRUOC join (khong nhan dong).", source="derived tu vn_holidays.csv",
                              grain="(checkin_date, city)", scope="CONFIG"),
    "confirmed_event_count": _d("So su kien status=confirmed trong ngay/city.", source="data/vn_holidays.csv", grain="(checkin_date, city)", scope="CONFIG"),
    "provisional_event_count": _d("So su kien status=provisional trong ngay/city (lich chua cong bo chinh thuc - phai ra soat truoc khi du bao).",
                                  source="data/vn_holidays.csv", grain="(checkin_date, city)", scope="CONFIG"),
    # ---- gia
    "price_per_night": _d("Gia 1 dem (VND) cua 1 room option - target chinh cua model; CHUA loai anomaly.", source=_PRICE_SRC, grain="observation", unit="VND",
                          structural="NULL khi is_sold_out=1 (sentinel, khong phai missing)", scope="MAIN/RAW (khong sold-out)",
                          usage="target (Wave B); trong Wave A chi mo ta", leakage="Gia tai t+k la nhan, khong duoc dung lam feature tai t"),
    "min_price": _d("Gia nho nhat trong nhom.", source=_PRICE_SRC, grain="observation -> nhom", unit="VND", scope="MAIN/RAW"),
    "max_price": _d("Gia lon nhat trong nhom.", source=_PRICE_SRC, grain="observation -> nhom", unit="VND", scope="MAIN/RAW"),
    "mean_price": _d("Gia trung binh trong nhom (observation-weighted, khong phai gia khach san trung binh).", source=_PRICE_SRC, grain="observation -> nhom", unit="VND", scope="MAIN/RAW"),
    "median_price": _d("Trung vi gia hop le trong (hotel, check-in, ngay quan sat) - phep tong hop sensitivity.", source=_PRICE_SRC, grain="(hotel_id, checkin_date, vn_observation_date)",
                       unit="VND", scope="MAIN"),
    "std_price": _d("Do lech chuan mau (ddof=1) cua gia trong hotel.", source=_PRICE_SRC, grain="hotel", unit="VND", scope="MAIN"),
    "coefficient_of_variation": _d("std_price / mean_price cua hotel (chi hotel >= 5 observation).", source="derived", grain="hotel", unit="ty le", scope="MAIN"),
    "hotel_median_price": _d("Trung vi gia cua chinh hotel (base cua robust z).", source=_PRICE_SRC, grain="hotel", unit="VND", scope="MAIN"),
    "price_mad": _d("Median Absolute Deviation cua gia trong hotel.", source=_PRICE_SRC, grain="hotel", unit="VND", scope="MAIN"),
    "price_robust_z": _d("|gia - median hotel| / (1.4826 x MAD) - CHI FLAG outlier (nguong 5), khong xoa.", source="derived", grain="observation", unit="robust sigma", scope="MAIN",
                         structural="NULL khi MAD = 0"),
    "max_robust_z": _d("robust z lon nhat trong hotel.", source="derived", grain="hotel", unit="robust sigma", scope="MAIN"),
    "bin_index": _d("Chi so bin cua histogram (linear: 0..79; log10: floor(log10(gia) x 20)).", source="derived", grain="bin", scope="MAIN"),
    "bin_lo": _d("Can duoi bin gia (VND), thang thuong.", source=_PRICE_SRC, grain="bin", unit="VND", scope="MAIN"),
    "bin_hi": _d("Can tren bin gia (VND), thang thuong.", source=_PRICE_SRC, grain="bin", unit="VND", scope="MAIN"),
    "log10_lo": _d("Can duoi bin log10(gia).", source=_PRICE_SRC, grain="bin", unit="log10 VND", scope="MAIN"),
    "log10_hi": _d("Can tren bin log10(gia).", source=_PRICE_SRC, grain="bin", unit="log10 VND", scope="MAIN"),
    "price_lo": _d("Can duoi bin gia = 10^log10_lo (VND).", source=_PRICE_SRC, grain="bin", unit="VND", scope="MAIN"),
    "price_hi": _d("Can tren bin gia = 10^log10_hi (VND).", source=_PRICE_SRC, grain="bin", unit="VND", scope="MAIN"),
    "aggregation": _d("Phep tong hop sensitivity: min_price hoac median_price cua (hotel, check-in, ngay quan sat).", source="derived", grain="series-day", allowed="min_price/median_price", scope="MAIN"),
    "min_value": _d("Gia tri nho nhat cua phep tong hop.", source="derived", grain="series-day", unit="VND", scope="MAIN"),
    "max_value": _d("Gia tri lon nhat cua phep tong hop.", source="derived", grain="series-day", unit="VND", scope="MAIN"),
    "mean_value": _d("Gia tri trung binh cua phep tong hop.", source="derived", grain="series-day", unit="VND", scope="MAIN"),
    "price_abs_diff": _d("|gia nguon A - gia nguon B| cua cung canonical option (VND). Khac 0 KHONG tu dong la loi parser (thoi diem crawl khac nhau).",
                         source=_PRICE_SRC, grain="shared canonical option pair", unit="VND", scope="RAW"),
    "price_relative_diff": _d("price_abs_diff / trung binh 2 gia (doi xung - 2 nguon la peer, khong co ground truth).", source="derived", grain="shared canonical option pair",
                              unit="ty le", scope="RAW"),
    "price_per_night_a": _d("Gia option o nguon A.", source=_PRICE_SRC, grain="shared canonical option pair", unit="VND", scope="RAW"),
    "price_per_night_b": _d("Gia option o nguon B.", source=_PRICE_SRC, grain="shared canonical option pair", unit="VND", scope="RAW"),
    "observed_at_a": _d("Thoi diem quan sat option o nguon A (UTC).", source="price_observations.observed_at", grain="shared canonical option pair", unit="datetime", tz="UTC", scope="RAW"),
    "observed_at_b": _d("Thoi diem quan sat option o nguon B (UTC).", source="price_observations.observed_at", grain="shared canonical option pair", unit="datetime", tz="UTC", scope="RAW"),
    "observed_at_diff_minutes": _d("|observed_at_a - observed_at_b| (phut) - bien audit bat buoc de dien giai chenh lech gia.", source="derived", grain="shared canonical option pair",
                                   unit="phut", scope="RAW"),
    "observed_finish_a": _d("item.finished_at cua item A (UTC).", source="crawl_run_items.finished_at", grain="item pair", unit="datetime", tz="UTC", scope="RAW"),
    "observed_finish_b": _d("item.finished_at cua item B (UTC).", source="crawl_run_items.finished_at", grain="item pair", unit="datetime", tz="UTC", scope="RAW"),
    "status_a": _d("Trang thai terminal cua item nguon A.", source="crawl_run_items.status", grain="item pair", allowed="5 status terminal", scope="RAW"),
    "status_b": _d("Trang thai terminal cua item nguon B.", source="crawl_run_items.status", grain="item pair", allowed="5 status terminal", scope="RAW"),
    "currency_concordant": _d("2 nguon cung tien te - LUON TRUE vi price_observations khong luu currency (ep VND qua URL).", source="CLAUDE.md muc 4.3", grain="shared canonical option pair",
                              unit="boolean", scope="RAW"),
    "breakfast_included_concordant": _d("breakfast_included cua 2 nguon khop (NULL-safe).", source="price_observations.breakfast_included", grain="shared canonical option pair",
                                        unit="boolean", scope="RAW"),
    "free_cancellation_concordant": _d("free_cancellation cua 2 nguon khop (NULL-safe).", source="price_observations.free_cancellation", grain="shared canonical option pair",
                                       unit="boolean", scope="RAW"),
    "cancellation_policy_concordant": _d("Cancellation policy sau canonical_text cua 2 nguon khop. Shared canonical rate key da rang buoc "
                                         "thuoc tinh nay nen day la structural audit.", source="price_observations.cancellation_policy",
                                         grain="shared canonical option pair", unit="boolean", scope="RAW"),
    "price_includes_tax_concordant": _d("price_includes_tax cua 2 nguon khop (NULL-safe).", source="price_observations.price_includes_tax", grain="shared canonical option pair",
                                        unit="boolean", scope="RAW"),
    "taxes_fees_state": _d("Tinh trang hien dien taxes_fees o cap 2 nguon: ca hai NULL, mot ben NULL, hoac ca hai co gia tri.",
                           source="price_observations.taxes_fees", grain="shared canonical option pair", allowed="both_null/one_null/both_present", scope="RAW"),
    "taxes_fees_concordant": _d("taxes_fees NULL-safe concordance: both-null=True, one-null=False; both-present khop trong tolerance 0.01 VND.",
                                source="price_observations.taxes_fees", grain="shared canonical option pair", unit="boolean", scope="RAW"),
    "taxes_fees_abs_diff": _d("|taxes_fees A - taxes_fees B|, chi co gia tri khi ca hai ben present.", source="price_observations.taxes_fees",
                              grain="shared canonical option pair", unit="VND", structural="NULL neu it nhat mot ben NULL", scope="RAW"),
    "option_jaccard": _d("n_shared_options / n_union_options cua tap canonical key 2 item (1 = giong het, 0 = roi nhau).", source="derived", grain="success-success item pair",
                         unit="ty le", scope="RAW"),
    # ---- reference
    "approved": _d("So series (hotel, check-in) co reference status=approved.", source="hotel_reference_rooms.status", grain="full-history reference series", scope="REFERENCE EVIDENCE"),
    "proposed": _d("So series co reference status=proposed (chua du evidence).", source="hotel_reference_rooms.status", grain="full-history reference series", scope="REFERENCE EVIDENCE"),
    "n_approved": _d("So reference status=approved cua 1 series (ky vong dung <= 1).", source="hotel_reference_rooms", grain="series", scope="REFERENCE EVIDENCE"),
    "matches_approved_key": _d("EXACT: canonical room/rate key cua CHINH observation = 1 reference approved. KHAC series_has_approved_reference.", source="derived", grain="observation",
                               unit="boolean", scope="MAIN/RAW"),
    "series_has_approved_reference": _d("LONG hon exact: (hotel_id, checkin_date) CO reference approved, khong doi hoi dung key (GPT file 09 muc 2).", source="derived", grain="observation",
                                        unit="boolean", scope="RAW"),
    "attributes_updated_at": _d("Lan cuoi refresh thuoc tinh hotel.", source="hotels", grain="hotel", unit="datetime", tz="UTC"),
    # ---- ready
    "dataset_version": _d("Dinh danh dataset build (Wave B).", source="dataset_build_manifests", grain="dataset_version", scope="ML CURATED"),
    "import_batch_id": _d("Batch warehouse ma dataset build dua vao.", source="dataset_build_manifests", grain="dataset_version", scope="ML CURATED"),
    "ready": _d("dataset_version PASS va du du lieu ca 3 bang ml_* (Wave B san sang).", source="derived", grain="dataset_version", unit="boolean", scope="ML CURATED"),
    "actual_causal_labels": _d("So label causal that - NULL/not_available cho toi khi Wave B (dataset builder) chay xong.", source="ml_samples", grain="horizon",
                               structural="luon NULL o Wave A", scope="ML CURATED"),
    "actual_status": _d("not_available cho toi khi Wave B PASS.", source="derived", grain="horizon", allowed="not_available", scope="ML CURATED"),
    # ---- quality findings
    "check_id": _d("Ten check chat luong du lieu (plan 7.11).", source="wave_a.build_quality_findings", grain="check", scope="RAW/MAIN"),
    "severity": _d("info/medium/high theo quy uoc cua check.", source="wave_a.build_quality_findings", grain="check", allowed="info/medium/high"),
    "grain": _d("Don vi dem cua check.", source="wave_a.build_quality_findings", grain="check"),
    "count": _d("So vi pham (numerator).", source="derived", grain="theo check", scope="RAW/MAIN"),
    "denominator": _d("Mau so cua check (grain cua chinh check).", source="derived", grain="theo check", scope="RAW/MAIN"),
    "rate": _d("count / denominator (NULL neu denominator = 0).", source="derived", grain="theo check", unit="ty le", scope="RAW/MAIN"),
    "sample_keys": _d("Mau khoa vi pham (record_id/item_id/hotel_id...; JSON) - toi da 10, chi khi count > 0.", source="queries.quality_violation_samples", grain="check", unit="JSON"),
    "likely_cause": _d("Nguyen nhan co the (chua ket luan).", source="wave_a.build_quality_findings", grain="check", unit="text"),
    "recommended_action": _d("Hanh dong de xuat.", source="wave_a.build_quality_findings", grain="check", unit="text"),
    "n_unattributed_errors": _d("So item error owned voi hotel_id=NULL khong resolve duoc tu source_hotel_link (GPT M2).", source="queries.protocol_continuity_actual", grain="source x crawl_date",
                                scope="MAIN"),
    # ---- protocol
    "owner_success": _d("So slot expected co item owner_success.", source="protocol_schedule.classify_outcomes", grain="expected slot", scope="PROTOCOL"),
    "owner_failure_status_sold_out": _d("So slot expected co item owner nhung status sold_out.", source="protocol_schedule.classify_outcomes", grain="expected slot", scope="PROTOCOL"),
    "owner_failure_status_not_bookable": _d("So slot expected co item owner nhung status not_bookable.", source="protocol_schedule.classify_outcomes", grain="expected slot", scope="PROTOCOL"),
    "owner_failure_status_error": _d("So slot expected co item owner nhung status error.", source="protocol_schedule.classify_outcomes", grain="expected slot", scope="PROTOCOL"),
    "missing_source_run": _d("So slot expected ma CA NGAY khong co run nao cua nguon (protocol_complete_through_date la ranh gioi tren).", source="protocol_schedule.classify_outcomes",
                             grain="expected slot", scope="PROTOCOL"),
    "missing_item_in_existing_run": _d("So slot expected ma ngay do CO run cua nguon nhung khong co item hotel/check-in nay.", source="protocol_schedule.classify_outcomes",
                                       grain="expected slot", scope="PROTOCOL"),
    "n_scheduled": _d("So slot expected (ownership manifest x cohort hieu luc) - MAU SO cua ty le protocol.", source="protocol_schedule.expected_schedule", grain="expected slot", scope="PROTOCOL"),
    # ---- turnover / series
    "first_observed": _d("Ngay quan sat (VN) dau tien cua canonical series.", source="price_observations", grain="canonical series", unit="date", tz=_VN, scope="MAIN"),
    "last_observed": _d("Ngay quan sat (VN) cuoi cung cua canonical series.", source="price_observations", grain="canonical series", unit="date", tz=_VN, scope="MAIN"),
    "max_gap_days": _d("Khoang cach lon nhat (ngay) giua 2 ngay quan sat lien tiep cua series (1 = lien tuc; >1 = co ngay not_observed/turnover_unknown roi xuat hien lai).",
                       source="derived", grain="canonical series", unit="ngay", scope="MAIN"),
    "span_days": _d("So ngay tu first_observed den last_observed, tinh ca hai dau.", source="derived", grain="canonical series", unit="ngay", scope="MAIN"),
    "median_gap_days": _d("Trung vi khoang cach giua cac ngay quan sat lien tiep tren toan bo canonical-series population.", source="derived", grain="canonical series", unit="ngay", scope="MAIN",
                          structural="0 khi series chi co 1 ngay quan sat"),
    "sum_span_days": _d("Tong (ngay cuoi - ngay dau + 1) cua cac series trong nhom.", source="derived", grain="canonical series -> nhom", unit="ngay", scope="MAIN"),
    "mean_span_days": _d("sum_span_days / n_series.", source="derived", grain="nhom", unit="ngay", scope="MAIN"),
    "observed_fraction_of_span": _d("Tong ngay observed / tong span - ty le ngay co mat trong khoang first..last.", source="derived", grain="nhom", unit="ty le", scope="MAIN"),
    "reappearance_rate": _d("n_series_with_reappearance / n_series.", source="derived", grain="nhom", unit="ty le", scope="MAIN"),
    "share_of_series": _d("n_series / tong n_series cua bang chung.", source="derived", grain="nhom", unit="ty le", scope="MAIN"),
    "share_of_observations": _d("n_observations / tong n_observations.", source="derived", grain="nhom", unit="ty le", scope="MAIN"),
    "share_ge3": _d("n_series_ge3 / n_series - ty le series co >= 3 evidence run.", source="derived", grain="nhom", unit="ty le", scope="REFERENCE EVIDENCE"),
    # ---- coverage / counts co ten rieng
    "n_obs": _d("So observation co gia hop le, khong sold-out trong nhom - MAU SO cua thong ke gia (grain observation).", source=_PRICE_SRC, grain="observation -> nhom", scope="MAIN/RAW"),
    "n_items": _d("So item trong nhom - MAU SO cua ty le availability/protocol (grain item, moi item dung 1 lan).", source="crawl_run_items", grain="item -> nhom", scope="MAIN/RAW"),
    "n_distinct_checkin_dates": _d("So check-in date phan biet trong nhom calendar flag, khong bi nhan theo so hotel/item.", source="derived", grain="calendar flag group", scope="MAIN"),
    "n_checkin_date_city_cells": _d("So cap (checkin_date, effective city) phan biet trong nhom calendar flag.", source="derived", grain="calendar flag group", scope="MAIN"),
    "n_total": _d("So quan sat/cell trong nhom lam mau so cua null_rate.", source="derived", grain="theo bang", scope="MAIN"),
    "n_null": _d("So cell NULL cua field trong nhom (numerator cua null_rate).", source="derived", grain="field_value_cell", scope="MAIN"),
    "null_rate": _d("n_null / n_total.", source="derived", grain="field_value_cell", unit="ty le", scope="MAIN"),
    "n_observations": _d("So observation trong nhom (MAU SO tuy bang - xem TABLE_METADATA.denominator).", source="price_observations", grain="observation -> nhom", scope="MAIN/RAW"),
    "n_matched": _d("So observation co canonical key khop dung reference approved (numerator exact-key).", source="derived", grain="observation -> bucket", scope="MAIN/RAW"),
    "n_series_has_reference": _d("So observation thuoc series co reference approved (numerator series-exists, KHONG doi hoi dung key).", source="derived", grain="observation -> bucket", scope="RAW"),
    "n_items_matched": _d("So success MAIN item co it nhat 1 option khop dung key approved (numerator).", source="derived", grain="item -> bucket", scope="MAIN"),
    "match_rate": _d("n_matched / n_observations - exact approved-key coverage.", source="derived", grain="observation -> bucket", unit="ty le", scope="MAIN/RAW"),
    "series_reference_rate": _d("n_series_has_reference / n_observations - series-exists coverage (metric long, khong phai exact).", source="derived", grain="observation -> bucket",
                                unit="ty le", scope="RAW"),
    "availability_rate": _d("n_items_matched / n_items - item-level exact-reference availability.", source="derived", grain="item -> bucket", unit="ty le", scope="MAIN"),
    "approval_rate": _d("approved / n series co candidate.", source="derived", grain="series -> city x thang", unit="ty le", scope="REFERENCE EVIDENCE"),
    "n": _d("So series (hotel, check-in) co it nhat 1 reference row (approved + proposed + retired) trong nhom.", source="hotel_reference_rooms", grain="series", scope="REFERENCE EVIDENCE"),
    "theoretical_date_pairs": _d("So cap ngay quan sat (t, t+K) cua CUNG canonical series - ly thuyet, CHUA phai label causal.", source="derived", grain="horizon", scope="MAIN"),
    "series_with_pair": _d("So canonical series co it nhat 1 cap ngay cach dung K ngay.", source="derived", grain="horizon", scope="MAIN"),
    "mean_candidates_per_series": _d("So candidate trung binh moi series (hotel, check-in).", source="hotel_room_candidates", grain="series", scope="REFERENCE EVIDENCE"),
    "max_candidates_per_series": _d("So candidate toi da cua 1 series.", source="hotel_room_candidates", grain="series", scope="REFERENCE EVIDENCE"),
    "mean_distinct_run_count": _d("distinct_run_count trung binh cua candidate.", source="hotel_room_candidates.distinct_run_count", grain="candidate", scope="REFERENCE EVIDENCE"),
    "max_distinct_run_count": _d("distinct_run_count lon nhat cua candidate.", source="hotel_room_candidates.distinct_run_count", grain="candidate", scope="REFERENCE EVIDENCE"),
    "mean_distinct_item_count": _d("distinct_item_count trung binh cua candidate.", source="hotel_room_candidates.distinct_item_count", grain="candidate", scope="REFERENCE EVIDENCE"),
    "mean_item_coverage": _d("item_coverage trung binh cua candidate (>= 0.8 la nguong duyet).", source="hotel_room_candidates.item_coverage", grain="candidate", unit="ty le", scope="REFERENCE EVIDENCE"),
    "min_runs": _d("distinct_run_count nho nhat cua reference trong status.", source="hotel_reference_rooms", grain="reference row", scope="REFERENCE EVIDENCE"),
    "mean_runs": _d("distinct_run_count trung binh cua reference trong status.", source="hotel_reference_rooms", grain="reference row", scope="REFERENCE EVIDENCE"),
    "max_runs": _d("distinct_run_count lon nhat cua reference trong status.", source="hotel_reference_rooms", grain="reference row", scope="REFERENCE EVIDENCE"),
    "min_items": _d("distinct_item_count nho nhat cua reference trong status.", source="hotel_reference_rooms", grain="reference row", scope="REFERENCE EVIDENCE"),
    "mean_items": _d("distinct_item_count trung binh cua reference trong status.", source="hotel_reference_rooms", grain="reference row", scope="REFERENCE EVIDENCE"),
    "max_items": _d("distinct_item_count lon nhat cua reference trong status.", source="hotel_reference_rooms", grain="reference row", scope="REFERENCE EVIDENCE"),
    "min_coverage": _d("coverage nho nhat cua reference trong status.", source="hotel_reference_rooms.coverage", grain="reference row", unit="ty le", scope="REFERENCE EVIDENCE"),
    "mean_coverage": _d("coverage trung binh cua reference trong status.", source="hotel_reference_rooms.coverage", grain="reference row", unit="ty le", scope="REFERENCE EVIDENCE"),
    "max_coverage": _d("coverage lon nhat cua reference trong status.", source="hotel_reference_rooms.coverage", grain="reference row", unit="ty le", scope="REFERENCE EVIDENCE"),
    # ---- collision summary
    "status_disagreement_rate": _d("n_status_disagreement / n_collision_item_pairs.", source="derived", grain="collision item pairs", unit="ty le", scope="RAW"),
    "exact_price_match_rate": _d("n_exact_price_match / n_option_pairs (mau so: shared canonical option-pairs 1-1).", source="derived", grain="shared canonical option pair", unit="ty le", scope="RAW"),
    "duration_minutes_z": _d("z-score cua duration_minutes trong nguon (chi khi >= 3 ngay, std > 0).", source="derived", grain="source x ngay", unit="z", scope="RAW"),
    "error_rate_z": _d("z-score cua error_rate trong nguon.", source="derived", grain="source x ngay", unit="z", scope="RAW"),
    "sold_out_rate_z": _d("z-score cua sold_out_rate trong nguon.", source="derived", grain="source x ngay", unit="z", scope="RAW"),
    "not_bookable_rate_z": _d("z-score cua not_bookable_rate trong nguon.", source="derived", grain="source x ngay", unit="z", scope="RAW"),
    "artifact_path": _d("Duong dan artifact tuong doi analysis_dir.", source="publication.py", grain="bang", scope="CONFIG"),
    "table": _d("Ten bang publish.", source="publication.py", grain="bang", scope="CONFIG"),
    "metric_id": _d("Metric ID on dinh (catalog metric hoac derived:<ham>).", source="publication.py", grain="bang", scope="CONFIG"),
    "plan_section": _d("Muc plan 7.x tuong ung.", source="publication.py", grain="bang", scope="CONFIG"),
    "columns": _d("Danh sach cot cua bang.", source="derived", grain="bang", scope="CONFIG"),
    "file_size_bytes": _d("Kich thuoc file CSV da publish tren dia, dung de audit chi phi artifact lon.", source="filesystem sau khi ghi CSV", grain="bang", unit="byte", scope="CONFIG"),
}

_Rule = tuple[re.Pattern, Callable[[re.Match], dict[str, str]]]


def _count_rule(match: re.Match) -> dict[str, str]:
    what = match.group(1)
    return _d(f"So luong '{what}' trong nhom (numerator hoac tong tuy ngu canh; MAU SO cua ty le tuong ung ghi trong TABLE_METADATA.denominator).",
              grain="theo bang", unit="so nguyen", scope="theo bang")


def _rate_rule(match: re.Match) -> dict[str, str]:
    what = match.group(1)
    return _d(f"Ty le '{what}' = n_{what} / mau so cua bang (n_items hoac n_observations - xem TABLE_METADATA.denominator).", grain="theo bang", unit="ty le (0-1)",
              structural="NULL khi mau so = 0", scope="theo bang")


def _percentile_rule(match: re.Match) -> dict[str, str]:
    return _d(f"Percentile P{match.group(1)} cua gia (VND) trong nhom, noi suy tuyen tinh (khop pandas Series.quantile).", source=_PRICE_SRC, grain="observation -> nhom", unit="VND",
              scope="MAIN/RAW")


def _flag_rule(match: re.Match) -> dict[str, str]:
    return _d(f"Co boolean '{match.group(1)}' (FLAG de audit, khong xoa dong nao).", grain="theo bang", unit="boolean", scope="theo bang")


_PATTERNS: tuple[_Rule, ...] = (
    (re.compile(r"^p(\d{1,2})$"), _percentile_rule),
    (re.compile(r"^(?:is|has)_(.+)$"), _flag_rule),
    (re.compile(r"^n_(?:total_|unexpected_)?(.+)_rate$"), _rate_rule),
    (re.compile(r"^(.+)_rate$"), _rate_rule),
    (re.compile(r"^n_(.+)$"), _count_rule),
    (re.compile(r"^(?:total|mean|median|max|min)_(.+)$"), lambda m: _d(
        f"Thong ke tong hop '{m.group(0)}' cua cot '{m.group(1)}' (xem bang goc).", grain="theo bang", scope="theo bang")),
    (re.compile(r"^(?:median|mean|max)_(?:price_)?(?:abs|relative)_diff$|^(?:median|mean|max)_price_(?:abs|relative)_diff$"), lambda m: _d(
        f"Thong ke '{m.group(0)}' cua chenh lech gia giua 2 nguon tren shared canonical option-pairs (VND hoac ty le doi xung).",
        source=_PRICE_SRC, grain="shared canonical option pair", scope="RAW")),
    (re.compile(r"^(.+)_concordance_rate$"), lambda m: _d(
        f"Ty le shared canonical option-pairs ma '{m.group(1)}' cua 2 nguon khop nhau (NULL-safe).", grain="shared canonical option pair", unit="ty le", scope="RAW")),
    (re.compile(r"^(.+)_z$"), lambda m: _d(f"z-score cua '{m.group(1)}' trong nguon (chi khi >= 3 ngay, std > 0).", grain="source x ngay", unit="z", scope="RAW")),
)


def describe(column: str) -> dict[str, str] | None:
    """Dinh nghia day du (FIELD_KEYS) cua `column` hoac None neu chua co."""
    if column in _FIELDS:
        return _FIELDS[column]
    for pattern, build in _PATTERNS:
        match = pattern.match(column)
        if match:
            return build(match)
    return None


def explicit_fields() -> dict[str, dict[str, str]]:
    return dict(_FIELDS)
