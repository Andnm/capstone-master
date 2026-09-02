"""Cào 1 trang khách sạn Booking.com — lấy CẢ thông tin khách sạn LẪN giá phòng trong 1 lần.
(Code cũ tách 'info' và 'price' thành 2 export riêng dù bản thân hàm scrape đã lấy chung -
 ở đây gộp lại đúng như dữ liệu gốc, không tách nữa.)

Phần selector/anti-bot được port gần như nguyên văn từ
Project/hotel_scraper_project/backend/app/services/booking_scraper.py (đã tinh chỉnh qua thực tế).
Phần mới thêm: parse JSON-LD để lấy address/geo, phát hiện sold-out rõ ràng.
"""
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from app.core.config import settings
from app.scraper.driver import get_driver
from app.scraper.artifacts import save_page_artifacts
from app.scraper.errors import ErrorCode, ScrapeFailure, classify_exception, failure
from app.scraper.parser import _nfc
from app.scraper.url_utils import build_scrape_url, clean_hotel_link, extract_hotel_slug

# Các cụm từ Booking hay dùng khi 1 khách sạn KHÔNG CÒN PHÒNG cho khoảng ngày đã chọn.
# Đã xác minh với trang thật (test 2026-08-01, hotel bella-vt ngày 30/8 hết phòng): Booking hiện
# đúng cụm "Không có phòng trống trên trang web chúng tôi vào những ngày bạn chọn" ở CỘT GIÁ của
# TỪNG DÒNG PHÒNG (không phải 1 thông báo chung toàn trang) khi hết phòng cho ngày đã chọn.
# Bỏ "rất tiếc" (cụm cũ đoán mò, quá chung chung, dễ match nhầm nội dung không liên quan -> sold-out giả,
# nguy hiểm hơn cả lỗi cũ vì sẽ ghi đè dữ liệu giá thật thành NULL).
_SOLD_OUT_PHRASES = [
    "không có phòng trống",
    "không còn phòng trống",
    "sold out",
    "no availability",
]


def _extract_json_ld(driver):
    """Đọc script application/ld+json trên trang, trả về dict đầu tiên tìm được (nếu có)."""
    data = {}
    try:
        scripts = driver.find_elements(By.XPATH, '//script[@type="application/ld+json"]')
        for script in scripts:
            try:
                parsed = json.loads(script.get_attribute('innerHTML'))
                if isinstance(parsed, dict):
                    data.update(parsed)
            except Exception:
                continue
    except Exception:
        pass
    return data


# Selector ổn định (id thật, không phải class hash build-time) tìm được bằng cách soi DOM thật
# lúc hết phòng (test 2026-08-01, bella-vt ngày 30/8): khi hết phòng, table#hprt-table không tồn
# tại luôn, thay vào đó có khối #no_av_rooms chứa #no_availability_msg.
_SOLD_OUT_SELECTORS = ['#no_availability_msg', '.no_availability_msg_light', '#no_av_rooms']

_NOT_BOOKABLE_SELECTORS = ['.non-bookable-container .error', '.non-bookable-container']
_NOT_BOOKABLE_PHRASES = [
    'hiện tại việc đặt phòng tại khách sạn này không thể thực hiện được',
    'hiện tại không thể đặt phòng tại chỗ nghỉ này trên trang web chúng tôi',
    'currently it is not possible to make reservations for this hotel',
    'this property is not taking reservations on our site right now',
]


def _not_bookable_message(driver) -> Optional[str]:
    """Return Booking's property-level non-bookable message, if present.

    This state is different from a sold-out check-in date: it applies to the
    property and must not create a NULL-price demand observation.
    """
    for selector in _NOT_BOOKABLE_SELECTORS:
        try:
            for element in driver.find_elements(By.CSS_SELECTOR, selector):
                text = _nfc((element.text or element.get_attribute('textContent') or '').strip())
                normalized = text.lower()
                if text and any(phrase in normalized for phrase in _NOT_BOOKABLE_PHRASES):
                    return text[:500]
        except Exception:
            continue
    try:
        body_text = _nfc(driver.find_element(By.TAG_NAME, 'body').text or '')
        normalized = body_text.lower()
        for phrase in _NOT_BOOKABLE_PHRASES:
            index = normalized.find(phrase)
            if index >= 0:
                start = max(0, index - 80)
                return body_text[start:index + len(phrase) + 120][:500]
    except Exception:
        pass
    return None


def _looks_sold_out(driver) -> bool:
    # Case 1 (ưu tiên - ổn định hơn): selector riêng cho khối thông báo hết phòng.
    for selector in _SOLD_OUT_SELECTORS:
        try:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                return True
        except Exception:
            continue

    # Case 2 (fallback): tìm cụm từ trong toàn bộ text trang, phòng khi Booking đổi cấu trúc DOM
    # nhưng vẫn giữ nguyên câu chữ thông báo.
    try:
        body_text = _nfc(driver.find_element(By.TAG_NAME, 'body').text.lower())
        return any(phrase in body_text for phrase in _SOLD_OUT_PHRASES)
    except Exception:
        return False


def _wait_for_availability_stable(
    driver, timeout: float = None, minimum_wait: float = None,
    stable_rounds_required: int = None,
    heartbeat: Optional[Callable[[], None]] = None,
) -> None:
    """Chờ bảng phòng hydrate xong thay vì cào ngay khi mới thấy tiêu đề trang."""
    # Booking có thể chèn CẢ rate đối tác của phòng đã thấy LẪN nguyên 1 loại phòng mới, trễ hơn
    # nhiều so với lúc bảng "trông có vẻ" đã ổn định. Không kết luận ổn định trước minimum_wait,
    # dù số dòng tạm thời đứng yên. Phát hiện 2026-08-20 (audit qua proxy VN): Era Apartment Kim Ma
    # thiếu hẳn 1 loại phòng (2/3), Fusion Suites Vung Tau thiếu 2 loại phòng (14/16) - route qua
    # proxy có thêm 1 chặng relay khiến các request tải phòng phụ chậm hơn kết nối trực tiếp, đủ để
    # trễ qua khỏi window mặc định cũ (timeout=20s/minimum_wait=8s/4 vòng). Tham số mặc định lấy từ
    # settings để tinh chỉnh được qua .env mà không cần sửa code.
    timeout = settings.AVAILABILITY_WAIT_TIMEOUT_SECONDS if timeout is None else timeout
    minimum_wait = settings.AVAILABILITY_WAIT_MINIMUM_SECONDS if minimum_wait is None else minimum_wait
    stable_rounds_required = (
        settings.AVAILABILITY_WAIT_STABLE_ROUNDS if stable_rounds_required is None else stable_rounds_required
    )
    started_at = time.time()
    deadline = started_at + timeout
    last_count = -1
    stable_rounds = 0
    while time.time() < deadline:
        if heartbeat:
            heartbeat()
        count = len(driver.find_elements(By.CSS_SELECTOR, 'tr.js-rt-block-row'))
        if count > 0 and count == last_count:
            stable_rounds += 1
            if stable_rounds >= stable_rounds_required and time.time() - started_at >= minimum_wait:
                return
        else:
            stable_rounds = 0
        if _not_bookable_message(driver) or _looks_sold_out(driver):
            return
        last_count = count
        time.sleep(0.75)


def _detect_block(driver) -> Optional[ScrapeFailure]:
    try:
        text = _nfc((driver.find_element(By.TAG_NAME, 'body').text or '').lower())
    except Exception:
        return None
    if any(marker in text for marker in ('captcha', 'verify you are human', 'xác minh bạn là người')):
        return failure(ErrorCode.CAPTCHA, 'Booking yêu cầu CAPTCHA/xác minh người dùng')
    if any(marker in text for marker in ('access denied', 'request blocked', 'automated traffic')):
        return failure(ErrorCode.BLOCKED, 'Booking chặn request/automated traffic')
    return None


@dataclass(frozen=True)
class DeadLinkConfirmation:
    """Kết quả probe xác nhận lần 2 cho một nghi vấn dead_link.

    verdict:
      - "confirmed": probe lần 2 (driver mới, canonical URL, không checkin/checkout) redirect
        /searchresults sạch -> đúng là link chết, được phép cascade sibling.
      - "not_confirmed": probe lần 2 tải được trang property thật (không not_bookable) -> lần
        redirect đầu chỉ là fluke, không phải dead link.
      - "not_bookable": probe lần 2 tải được trang property nhưng Booking báo not_bookable ->
        đây là trạng thái property hợp lệ, không phải dead link.
      - "inconclusive": probe lần 2 không đủ tín hiệu (CAPTCHA/block/network/driver error, hoặc
        landing page không rõ là property hay search-results) -> không được kết luận, không cascade.
    scrape_failure: ScrapeFailure GỐC từ chính probe (vd NETWORK_TIMEOUT/CAPTCHA/BLOCKED/DRIVER_INIT)
      khi verdict="inconclusive", để worker giữ đúng semantics circuit-breaker/retry hiện có thay vì
      gộp chung thành một mã "unconfirmed" mất taxonomy thật.
    """

    verdict: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    scrape_failure: Optional[ScrapeFailure] = None
    not_bookable_message: Optional[str] = None


def confirm_dead_link(
    source_hotel_link: str,
    first_requested_url: Optional[str],
    first_final_url: Optional[str],
    *,
    heartbeat: Optional[Callable[[], None]] = None,
) -> DeadLinkConfirmation:
    """Probe lần 2 cho một nghi vấn dead_link, bằng driver RIÊNG, ngắn hạn (không đụng driver
    batch của worker) trên canonical URL đã bỏ hết query/tracking/checkin/checkout.

    Hàm này KHÔNG BAO GIỜ được để exception thoát ra ngoài - process_item()/run_forever() không
    bọc lại lần nữa, một exception thoát khỏi đây sẽ crash cả worker process giữa chừng.
    """
    canonical_url = clean_hotel_link(source_hotel_link)
    source_slug = extract_hotel_slug(source_hotel_link)
    evidence: Dict[str, Any] = {
        "first_requested_url": first_requested_url,
        "first_final_url": first_final_url,
        "probe_requested_url": canonical_url,
    }

    def _finish(verdict, *, scrape_failure=None, not_bookable_message=None):
        evidence["verdict"] = verdict
        evidence["probe_finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        return DeadLinkConfirmation(
            verdict, evidence, scrape_failure=scrape_failure, not_bookable_message=not_bookable_message
        )

    driver = None
    try:
        try:
            driver = get_driver(is_headless=True)
        except Exception as exc:
            evidence["probe_error_code"] = ErrorCode.DRIVER_INIT.value
            evidence["probe_message"] = str(exc)[:500]
            return _finish("inconclusive", scrape_failure=failure(ErrorCode.DRIVER_INIT, str(exc)))

        try:
            driver.set_page_load_timeout(60)
            if heartbeat:
                heartbeat()
            driver.get(canonical_url)
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.TAG_NAME, 'body'))
            )
            # Để redirect chain / anti-bot interstitial (nếu có) settle trước khi đọc current_url.
            time.sleep(2)
            probe_final_url = driver.current_url or canonical_url
        except Exception as exc:
            try:
                probe_final_url = driver.current_url
            except Exception:
                probe_final_url = None
            evidence["probe_final_url"] = probe_final_url
            classified = classify_exception(str(exc))
            evidence["probe_error_code"] = classified.code.value
            evidence["probe_message"] = str(exc)[:500]
            return _finish("inconclusive", scrape_failure=classified)

        evidence["probe_final_url"] = probe_final_url

        # Kiểm tra block/CAPTCHA TRƯỚC khi diễn giải URL - trang chặn có thể tự redirect về URL
        # trông giống property hoặc giống search-results, không được gắn nhầm thành confirmed.
        try:
            blocked = _detect_block(driver)
        except Exception:
            blocked = None
        if blocked:
            evidence["probe_error_code"] = blocked.code.value
            evidence["probe_message"] = blocked.message
            return _finish("inconclusive", scrape_failure=blocked)

        if '/searchresults.' in probe_final_url:
            return _finish("confirmed")

        probe_slug = extract_hotel_slug(probe_final_url)
        if probe_slug is not None and probe_slug != source_slug:
            # Booking co the redirect sang MOT PROPERTY KHAC (khong phai searchresults, khong phai
            # chinh no) - tuyet doi khong duoc doc trang cua hotel B roi ket luan/ghi de trang thai
            # cho hotel A. Coi la inconclusive, khong cascade, khong persist not_bookable cho A.
            evidence["probe_error_code"] = "property_mismatch"
            evidence["probe_message"] = f"Probe redirect sang property khac: {probe_slug} != {source_slug}"
            return _finish("inconclusive")

        if probe_slug is not None:
            try:
                not_bookable_msg = _not_bookable_message(driver)
            except Exception:
                not_bookable_msg = None
            if not_bookable_msg:
                return _finish("not_bookable", not_bookable_message=not_bookable_msg)
            return _finish("not_confirmed")

        # Không phải /searchresults, cũng không phải path /hotel/.../slug.html nhận diện được
        # (vd homepage, login, URL lạ) - không phải bằng chứng property còn sống lẫn đã chết.
        evidence["probe_error_code"] = "unrecognized_landing_page"
        return _finish("inconclusive")
    except Exception as exc:
        # Lưới an toàn cuối cùng cho bất kỳ lỗi nào chưa lường trước (vd set_page_load_timeout
        # hoặc current_url tự raise trên 1 session đã chết theo cách khác) - không bao giờ để lọt
        # ra ngoài, luôn trả inconclusive.
        evidence["probe_error_code"] = "unexpected_probe_error"
        evidence["probe_message"] = str(exc)[:500]
        return _finish("inconclusive", scrape_failure=classify_exception(str(exc)))
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                try:
                    driver.service.process.kill()
                except Exception:
                    pass


def scrape_booking_hotel(
    url: str,
    checkin: str,
    checkout: str,
    *,
    driver=None,
    save_artifact: bool = False,
    artifact_root: Optional[str] = None,
    run_id: Optional[int] = None,
    item_id: Optional[int] = None,
    heartbeat: Optional[Callable[[], None]] = None,
) -> tuple:
    """Cào 1 khách sạn cho 1 cặp checkin/checkout (luôn nên là 1 đêm).

    Trả về (result, error_message). result = {
        'hotel_name', 'hotel_link', 'address',
        'review_score', 'review_count', 'popular_facilities' (list),
        'is_sold_out': bool,
        'is_not_bookable': bool,
        'rooms': [ { 'room_type_raw', 'max_occupancy', 'bed_options' (str, đã join "và"/"hoặc" đúng ngữ nghĩa),
                     'room_area', 'price_per_night', 'original_price',
                     'discount_percent', 'taxes_fees', 'price_includes_tax',
                     'facility_lines' (list) }, ... ]
    }
    """
    owns_driver = driver is None
    forced_url = build_scrape_url(url, checkin, checkout)
    meta = {
        'driver_start_ms': 0,
        'page_load_ms': 0,
        'availability_wait_ms': 0,
        'parse_ms': 0,
        'artifact_html_path': None,
        'screenshot_path': None,
        'final_url': forced_url,
    }

    try:
        if driver is None:
            started = time.perf_counter()
            driver = get_driver(is_headless=True)
            meta['driver_start_ms'] = round((time.perf_counter() - started) * 1000)
        driver.set_page_load_timeout(60)

        max_retries = 3
        loaded = False
        last_load_error = None
        for attempt in range(max_retries):
            try:
                if heartbeat:
                    heartbeat()
                page_started = time.perf_counter()
                driver.get(forced_url)
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, 'h2.pp-header__title, h1, [data-testid="price-and-discounted-price"]')
                    )
                )
                meta['page_load_ms'] += round((time.perf_counter() - page_started) * 1000)
                wait_started = time.perf_counter()
                _wait_for_availability_stable(driver, heartbeat=heartbeat)
                meta['availability_wait_ms'] += round((time.perf_counter() - wait_started) * 1000)
                loaded = True
                break
            except Exception as exc:
                last_load_error = exc
                if attempt < max_retries - 1:
                    time.sleep(5)
        meta['final_url'] = getattr(driver, 'current_url', forced_url) or forced_url
        if not loaded:
            return None, classify_exception(str(last_load_error), ErrorCode.NETWORK_TIMEOUT), meta

        blocked = _detect_block(driver)
        if blocked:
            return None, blocked, meta

        # Booking chuyển link chỗ nghỉ đã gỡ/không còn hợp lệ về trang kết quả
        # tìm kiếm. Trường hợp này không phải lỗi selector và cũng không được ghi
        # thành sold-out; báo rõ để người dùng sửa link nguồn trong Excel.
        if '/searchresults.' in driver.current_url:
            return None, failure(
                ErrorCode.DEAD_LINK,
                "Link chỗ nghỉ không còn mở được trên Booking (bị chuyển về trang tìm kiếm)",
                False,
            ), meta

        result = {
            'hotel_name': None,
            'hotel_link': meta['final_url'],
            'address': None,
            'review_score': None,
            'review_count': None,
            'popular_facilities': [],
            'is_sold_out': False,
            'is_not_bookable': False,
            'booking_status_reason': None,
            'rooms': [],
            'diagnostics': {},
        }

        json_ld = _extract_json_ld(driver)
        if json_ld:
            if 'name' in json_ld:
                result['hotel_name'] = json_ld.get('name')
            aggregate = json_ld.get('aggregateRating') or {}
            if aggregate.get('ratingValue'):
                try:
                    result['review_score'] = float(str(aggregate['ratingValue']).replace(',', '.'))
                except Exception:
                    pass
            if aggregate.get('reviewCount'):
                try:
                    result['review_count'] = int(re.sub(r'[^\d]', '', str(aggregate['reviewCount'])))
                except Exception:
                    pass
            address_obj = json_ld.get('address')
            if isinstance(address_obj, dict):
                # Booking thường đặt địa chỉ hiển thị đầy đủ vào streetAddress; nối thêm locality /
                # region làm địa chỉ bị lặp. Chỉ fallback sang các phần còn lại khi streetAddress rỗng.
                street = (address_obj.get('streetAddress') or '').strip()
                if street:
                    result['address'] = street
                else:
                    parts = [address_obj.get('addressLocality'), address_obj.get('addressRegion')]
                    result['address'] = ", ".join(str(p).strip() for p in parts if p)
            elif isinstance(address_obj, str):
                result['address'] = address_obj

        if not result['hotel_name']:
            for selector in ['h2.pp-header__title', 'h1[data-testid="title"]', 'h1']:
                try:
                    element = driver.find_element(By.CSS_SELECTOR, selector)
                    text = element.text.strip()
                    if text and len(text) >= 3:
                        result['hotel_name'] = text
                        break
                except Exception:
                    continue

        if not result['address']:
            for selector in ['[data-node_tt_id="location_score_tooltip"]', '.hp_address_subtitle',
                              '[data-testid="address"]']:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, selector)
                    text = el.text.strip()
                    if text:
                        result['address'] = text
                        break
                except Exception:
                    continue

        try:
            facility_wrapper = driver.find_element(
                By.CSS_SELECTOR, '[data-testid="property-most-popular-facilities-wrapper"]'
            )
            facility_items = facility_wrapper.find_elements(By.CSS_SELECTOR, 'li')
            for item in facility_items:
                text = _nfc(item.text.strip())
                if text and text not in result['popular_facilities']:
                    result['popular_facilities'].append(text)
        except Exception:
            pass

        not_bookable_message = _not_bookable_message(driver)
        if not_bookable_message:
            result['is_not_bookable'] = True
            result['booking_status_reason'] = not_bookable_message
            result['diagnostics'] = {
                'dom_room_row_count': 0, 'candidate_rate_count': 0,
                'parsed_options_count': 0, 'rejected_options_count': 0,
                'parse_warning_count': 0, 'rejected_options': [],
                'final_url': meta['final_url'],
            }
            return result, None, meta

        if _looks_sold_out(driver):
            result['is_sold_out'] = True
            result['diagnostics'] = {
                'dom_room_row_count': 0, 'candidate_rate_count': 0,
                'parsed_options_count': 0, 'rejected_options_count': 0,
                'parse_warning_count': 0, 'rejected_options': [],
                'final_url': meta['final_url'],
            }
            return result, None, meta

        parse_started = time.perf_counter()
        result['diagnostics'] = _extract_rooms(driver, result)
        result['diagnostics']['final_url'] = meta['final_url']
        meta['parse_ms'] = round((time.perf_counter() - parse_started) * 1000)

        if not result['rooms']:
            # Không trích được phòng nào, và cũng không thấy thông báo sold-out rõ ràng
            # -> coi là lỗi cào (để job_runner retry), KHÔNG phải sold-out xác nhận.
            return None, failure(
                ErrorCode.PARSER_EMPTY,
                "Không tìm thấy phòng nào và không có thông báo hết phòng rõ ràng",
            ), meta

        return result, None, meta

    except Exception as e:
        return None, classify_exception(str(e)), meta
    finally:
        if driver and save_artifact and artifact_root and run_id is not None and item_id is not None:
            try:
                meta.update(save_page_artifacts(driver, artifact_root, run_id, item_id))
            except Exception as artifact_error:
                meta['artifact_error'] = str(artifact_error)
        if driver and owns_driver:
            try:
                driver.quit()
            except Exception:
                try:
                    driver.service.process.kill()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Selector fallback chains — PORT ĐẦY ĐỦ từ code cũ (booking_scraper.py gốc), không cắt bớt.
# Bản đầu tiên của file này đã bị rút gọn nhầm (thiếu nhiều tầng fallback so với code cũ) —
# đã soát lại và khôi phục đúng độ phủ gốc, vì mỗi selector fallback ở đây tương ứng 1 biến
# thể layout Booking thực tế đã gặp (theo comment gốc: "Vietnamese market", "wholesalers
# table layout"...), không phải code thừa.
# ---------------------------------------------------------------------------

_ROOM_ROW_FALLBACK_SELECTORS = [
    'table#hprt-table tbody tr',
    '.hprt-table tbody tr',
    'tr[data-block-id]',
    'tr[data-hotel-rounded-price]',
    'tr.js-rt-block-row',
    'tr.hprt-table-row',
    'tr.e2e-hprt-table-row',
    '.hprt-roomtype-block',
    '.hprt-block',
    '.hprt-price-block',
    '.prco-wrapper',
    '.bui-price-display',
    '.hprt-table-cell-roomtype',
    '.hprt-roomtype-link',
    '.hprt-roomtype-icon-link',
    '.hp-rt-group_recommendation',
    '.rt-bed-types',
    '.hprt-facilities-facility',
    '.c-occupancy-icons',
    '.js-average-per-night-price',
    '.js-strikethrough-price',
    '.bui-price-display__value',
    '.hprt-nos-select',
    '.only_x_left',
    '.urgency_message_red',
    '.thisRoomAvailabilityNew',
    '[data-block-id*="_"]',
    '[data-block-id]',
    '.rt-room-type',
    '[data-testid*="room"]',
    '.accommodation-type',
    '.room-recommendation',
    '.bui-room-table__row',
    '.hp-accommodation-block',
    '.hp-room-types__item',
    '.room-info-container',
    '.roomtable',
    '[data-room-id]',
    '.availability-table tr',
    '.room-option',
    '.bicon-room + .bicon__text',
    '.room-details-wrapper',
    '[data-testid="accommodation-option"]',
    '[data-testid="room-option"]',
    '[data-testid="room-info"]',
    '.bui-table__row',
    '.sr-room__title-container',
    '.room-type-block',
]

_ROOM_NAME_SELECTORS = [
    '.hprt-roomtype-link',
    '.hprt-roomtype-icon-link',
    '.hprt-roomtype-name',
    'a[data-room-name]',
    '.room-name',
    '.sr-room__title',
    'h4',
    'h3',
]

_ROOM_SIZE_SELECTORS = [
    '.hprt-facilities-facility[data-name-en="room size"] .bui-badge__text',
    '.hprt-roomtype-icon-info .bui-u-sr-only',
]
_ROOM_SIZE_XPATH_CONTAINS = [
    'contains(text(), "m²")',
    'contains(text(), "m2")',
    'contains(text(), "feet²")',
    'contains(text(), "ft²")',
]

_FACILITY_SELECTORS = [
    '.hprt-table-cell-conditions .bui-list__item',
    '.hprt-conditions-bui .bui-list__item',
    '.bui-list--text .bui-list__item',
    '.hprt-conditions li',
    '.hprt-table-cell-conditions li',
    '.e2e-cancellation[data-testid="cancellation-subtitle"]',
    '.e2e-prepayment[data-testid="prepayment-subtitle"]',
    '.bui-list__description',
    '.hprt-conditions-bui li',
]
_FACILITY_BROADER_SELECTORS = [
    '.bui-list__description strong',
    '.hprt-table-cell-conditions strong',
    '.policy-title',
    '[data-testid="policy-title"]',
]

_PRICE_SELECTORS = [
    '.js-average-per-night-price',
    '.bui-price-display__value .prc-no-css',
    '.bui-price-display__value',
    '.prco-text-nowrap-helper',
    '.prco-f-font-heading',
    '.js-strikethrough-price',
    '.bui-price-display__original',
    '.bui-f-color-destructive',
    '[data-testid="price-and-discounted-price"] .prco-valign-middle-helper',
    '.prco-inline-block-maker-helper',
    '.bui_font_strong',
    '.prco-text-color-bold',
    '.hprt-price-block .bui-price-display',
    '.prco-wrapper .bui-price-display',
    'span[aria-hidden="true"]',
    '.price',
    '.rate',
]

_ORIGINAL_PRICE_SELECTORS = [
    '.bui-price-display__original',
    '.bui-price-display__strikethrough',
    '[data-testid="price-and-discounted-price"] .bui-price-display__strikethrough',
    '.prco-text-stack s',
]

_SCARCITY_SELECTORS = ['.only_x_left', '.urgency_message_red', '.thisRoomAvailabilityNew', '.top_scarcity']
_SCARCITY_XPATH_CONTAINS = ['contains(text(), "Chúng tôi còn")', 'contains(text(), "căn")']


def _extract_rooms(driver, result):
    """Port đầy đủ từ code cũ: loop qua bảng room, xử lý rowspan để gộp header + từng dòng giá."""
    room_rows = driver.find_elements(By.CSS_SELECTOR, 'tr.js-rt-block-row')
    if not room_rows:
        for selector in _ROOM_ROW_FALLBACK_SELECTORS:
            room_rows = driver.find_elements(By.CSS_SELECTOR, selector)
            if room_rows:
                break

    diagnostics = {
        'dom_room_row_count': len(room_rows),
        'candidate_rate_count': 0,
        'parsed_options_count': 0,
        'rejected_options_count': 0,
        'parse_warning_count': 0,
        'rejected_options': [],
    }

    def reject(row_index, option_index, reason, message=None):
        diagnostics['rejected_options'].append({
            'row_index': row_index,
            'option_index': option_index,
            'reason_code': reason,
            'message': (message or '')[:300] or None,
        })

    row_index = 0
    while row_index < len(room_rows):
        row = room_rows[row_index]

        try:
            room_type_cell = row.find_elements(By.CSS_SELECTOR, 'th.hprt-table-cell-roomtype')
        except Exception:
            room_type_cell = []

        if not room_type_cell:
            diagnostics['candidate_rate_count'] += 1
            reject(row_index, 0, 'missing_room_header')
            row_index += 1
            continue

        room_type_header = room_type_cell[0]
        rowspan = 1
        try:
            rowspan_attr = room_type_header.get_attribute('rowspan')
            if rowspan_attr:
                rowspan = int(rowspan_attr)
        except Exception:
            pass

        # QUAN TRỌNG: row_index phải luôn nhảy đúng `rowspan` bất kể bên dưới có lỗi hay không -
        # nếu không, các dòng giá còn lại của CHÍNH phòng này (rowspan > 1) sẽ bị lệch chỉ số,
        # bị nhận nhầm thành "dòng không có header" và bị bỏ qua âm thầm ở vòng lặp kế tiếp.
        # Đây là bug thật đã gây mất phòng/mất mức giá ngẫu nhiên (phát hiện 2026-08-01).
        try:
            room_type_raw = _get_room_name(room_type_header)
            max_occupancy = _get_num_guests(room_type_header, row)
            bed_options = _get_bed_options(room_type_header)
            room_area = _get_room_size(room_type_header)
        except Exception:
            room_type_raw = None
            max_occupancy = None
            bed_options = None
            room_area = None

        for option_idx in range(rowspan):
            if row_index + option_idx >= len(room_rows):
                break
            diagnostics['candidate_rate_count'] += 1
            # Mỗi dòng giá (option_idx) xử lý độc lập - 1 dòng lỗi không được làm mất các dòng
            # giá khác của CÙNG phòng đó.
            try:
                pricing_row = room_rows[row_index + option_idx]

                facility_lines = _get_facility_lines(pricing_row)
                scarcity_line = _get_scarcity_text(pricing_row)
                if scarcity_line:
                    facility_lines.append(scarcity_line)

                discount_percent_text = _get_discount_percent(pricing_row)
                price_per_night = _extract_price(pricing_row, _PRICE_SELECTORS)
                if price_per_night is None:
                    price_per_night = _extract_price_from_attr(
                        pricing_row, '.js-average-per-night-price', 'data-price-per-night-raw'
                    )

                original_price = _extract_price(pricing_row, _ORIGINAL_PRICE_SELECTORS)
                if original_price is None:
                    original_price = _extract_price_from_attr(
                        pricing_row, '.js-strikethrough-price', 'data-strikethrough-value'
                    )

                taxes_fees, price_includes_tax = _extract_tax_info(pricing_row)

                if room_type_raw and price_per_night is not None:
                    result['rooms'].append({
                        'room_type_raw': room_type_raw,
                        'max_occupancy': max_occupancy,
                        'bed_options': bed_options,
                        'room_area': room_area,
                        'price_per_night': price_per_night,
                        'original_price': original_price,
                        'discount_percent': float(discount_percent_text) if discount_percent_text else None,
                        'taxes_fees': taxes_fees,
                        'price_includes_tax': price_includes_tax,
                        'facility_lines': facility_lines,
                    })
                elif not room_type_raw:
                    reject(row_index + option_idx, option_idx, 'missing_room_name')
                else:
                    reject(row_index + option_idx, option_idx, 'missing_price')
            except Exception as exc:
                reject(row_index + option_idx, option_idx, 'unknown_dom_variant', str(exc))

        row_index += rowspan

    diagnostics['parsed_options_count'] = len(result['rooms'])
    diagnostics['rejected_options_count'] = len(diagnostics['rejected_options'])
    diagnostics['parse_warning_count'] = diagnostics['rejected_options_count']
    # Mọi candidate phải được giải thích là parsed hoặc rejected.
    accounted = diagnostics['parsed_options_count'] + diagnostics['rejected_options_count']
    if accounted != diagnostics['candidate_rate_count']:
        reject(-1, -1, 'candidate_accounting_mismatch', f"candidate={diagnostics['candidate_rate_count']}, accounted={accounted}")
        diagnostics['rejected_options_count'] = len(diagnostics['rejected_options'])
        diagnostics['parse_warning_count'] = diagnostics['rejected_options_count']
    return diagnostics


def _get_room_name(room_type_header):
    for selector in _ROOM_NAME_SELECTORS:
        try:
            el = room_type_header.find_element(By.CSS_SELECTOR, selector)
            # Booking có thể ẩn một phần bảng ở viewport headless hẹp. `.text`
            # khi đó rỗng dù tên phòng vẫn có trong DOM.
            text = (el.get_attribute('textContent') or el.text or '').strip()
            text = re.sub(r'\s+', ' ', text)
            if text and len(text) > 3:
                return text
        except Exception:
            continue
    return None


def _get_num_guests(room_type_header, row):
    """Thứ tự ưu tiên đã điều chỉnh sau khi đối chiếu trực tiếp với trang thật (test 2 khách sạn
    2026-08-01): `.bui-u-sr-only` ("Số người tối đa: N") cho kết quả ĐÚNG và ỔN ĐỊNH ở mọi dòng
    kiểm tra được, trong khi đếm icon `.c-occupancy-icons__adults` ra kết quả THẤT THƯỜNG (0 hoặc
    2 ngay trong cùng 1 lần load cho các phòng đều thực sự là 2 người) — không đáng tin cậy làm
    phương án chính. Giữ lại các phương án khác làm fallback cho các biến thể layout khác.
    """
    try:
        # '.bui-u-sr-only' ẩn trực quan cho screen-reader -> Selenium `.text` luôn trả rỗng cho
        # phần tử này (chỉ đọc text ĐANG HIỂN THỊ). Phải dùng textContent (đọc DOM thô, không
        # quan tâm CSS ẩn/hiện) mới lấy được. Xác nhận bằng thực nghiệm 2026-08-01: JS
        # `.textContent` thấy đúng "Số người tối đa: 2" trong khi Selenium `.text` trả rỗng.
        guests_elem = row.find_element(By.CSS_SELECTOR, '.bui-u-sr-only')
        guests_text = _nfc((guests_elem.get_attribute('textContent') or '').strip())
        match = re.search(r'Số người tối đa:\s*(\d+)', guests_text, re.IGNORECASE)
        if not match:
            match = re.search(r'(\d+)\s*người', guests_text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    except Exception:
        pass

    try:
        # Phát hiện 2026-08-01: Booking có 1 biến thể template (thấy ở Bella VT, hàng
        # 'wholesalers_table__roomname' - dòng đối tác) KHÔNG có span '.bui-u-sr-only' chứa
        # "Số người tối đa" ở trên, nhưng CÓ '.hprt-roomtype-occupancy-text' hiển thị thẳng
        # "N người lớn" (không ẩn, không cần textContent). Selector cũ ghi nhầm tên class
        # ('hprt-occupancy-occupancy-info' - không tồn tại) nên không bao giờ khớp được biến
        # thể này -> đây mới là selector đúng: 'hprt-roomtype-occupancy-text/-info'.
        candidates = room_type_header.find_elements(
            By.CSS_SELECTOR,
            '.hprt-roomtype-occupancy-text, .hprt-roomtype-occupancy-info, '
            '.e2e-gr-title, .maxPersons-container, .c-occupancy-icons',
        )
        for cand in candidates:
            text = _nfc((cand.get_attribute('textContent') or '').strip())
            match = re.search(
                r'(?:Được giới thiệu cho|Max people:|Số người tối đa|Sức chứa)[:\s]*(\d+)', text, re.IGNORECASE
            )
            if not match:
                match = re.search(r'(\d+)\s*người\s*lớn', text, re.IGNORECASE)
            if match:
                return int(match.group(1))
    except Exception:
        pass

    try:
        occupancy_icons = row.find_elements(By.CSS_SELECTOR, '.c-occupancy-icons__adults i.bicon-occupancy')
        if not occupancy_icons:
            occupancy_icons = row.find_elements(By.CSS_SELECTOR, '.c-occupancy-icons__adults .bicon.bicon-occupancy')
        if occupancy_icons:
            return len(occupancy_icons)
    except Exception:
        pass

    try:
        occupancy = row.find_elements(By.CSS_SELECTOR, '.bui-icon.bui-icon--adults')
        if occupancy:
            return len(occupancy)
    except Exception:
        pass

    return None


def _get_bed_options(room_type_header):
    """Trả về 1 chuỗi mô tả giường đã nối đúng "và"/"hoặc" LẤY THẲNG TỪ DOM, không tự đoán.

    Phát hiện 2026-08-01 (đối chiếu Elite SOFEA vs Green LP thật): khi 1 phòng có NHIỀU loại
    giường CÙNG TỒN TẠI (vd "1 giường đôi VÀ 1 giường đôi lớn"), Booking tự chèn text node "và"
    làm anh em (sibling) của <span> tên giường, ngay TRONG <li class="rt-bed-type"> - selector
    cũ chỉ lấy `span` bên trong nên luôn bỏ mất chữ "và" này, dẫn tới sau đó phải tự đoán bằng
    " hoặc " (SAI với case cùng tồn tại). Lấy nguyên `textContent` của <ul class="rt-bed-types">
    (thay vì lặn vào từng `span` con) giữ nguyên chữ "và" nếu có.
    Khi 1 phòng cho phép CHỌN giường (radio, có label "Chọn giường bạn muốn (tùy tình trạng sẵn
    có)") thì mỗi lựa chọn nằm trong 1 <ul class="rt-bed-types"> RIÊNG - nhiều ul như vậy mới là
    các phương án THAY THẾ cho nhau, nối bằng " hoặc " là đúng.
    """
    def clean(text):
        text = _nfc(re.sub(r'<svg.*?</svg>', '', text, flags=re.DOTALL).strip())
        text = re.sub(r'\s+', ' ', text).strip()
        return text if text and 'Chọn giường' not in text and 'tùy tình trạng' not in text else None

    try:
        items = room_type_header.find_elements(By.CSS_SELECTOR, '.wholesalers_table__bed_options__text')
        result = [clean(i.text.strip()) for i in items]
        result = [r for r in result if r]
        if result:
            return " hoặc ".join(result)
    except Exception:
        pass

    try:
        bed_lists = room_type_header.find_elements(By.CSS_SELECTOR, '.rt-bed-types')
        options = []
        for ul in bed_lists:
            text = clean((ul.get_attribute('textContent') or '').strip())
            if text:
                options.append(text)
        if options:
            return " hoặc ".join(options)
    except Exception:
        pass

    try:
        result = []
        for elem in room_type_header.find_elements(By.CSS_SELECTOR, '.hprt-roomtype-bed'):
            text = clean((elem.get_attribute('textContent') or '').strip())
            if text:
                result.append(text)
        if result:
            return " hoặc ".join(result)
    except Exception:
        pass

    return None


def _get_room_size(room_type_header):
    # Dùng textContent thay vì .text: 1 trong các selector bên dưới trỏ tới phần tử
    # '.bui-u-sr-only' (ẩn trực quan cho screen-reader) — Selenium `.text` trả rỗng cho phần tử
    # ẩn, phải đọc DOM thô mới lấy được (cùng nguyên nhân bug max_occupancy đã fix ở trên).
    for selector in _ROOM_SIZE_SELECTORS:
        try:
            el = room_type_header.find_element(By.CSS_SELECTOR, selector)
            text = (el.get_attribute('textContent') or '').strip()
            parsed = _parse_room_size_text(text)
            if parsed:
                return parsed
        except Exception:
            continue

    for xpath_cond in _ROOM_SIZE_XPATH_CONTAINS:
        try:
            el = room_type_header.find_element(By.XPATH, f'.//*[{xpath_cond}]')
            text = (el.get_attribute('textContent') or '').strip()
            parsed = _parse_room_size_text(text)
            if parsed:
                return parsed
        except Exception:
            continue
    return None


def _parse_room_size_text(text):
    match_m = re.search(r'(\d+)\s*m[²2]?', text, re.IGNORECASE)
    if match_m:
        return f"{match_m.group(1)} m²"
    match_ft = re.search(r'(\d+)\s*(feet²?|ft²?)', text, re.IGNORECASE)
    if match_ft:
        return f"{match_ft.group(1)} ft²"
    return None


def _get_facility_lines(pricing_row):
    facility_lines = []
    for selector in _FACILITY_SELECTORS:
        try:
            elems = pricing_row.find_elements(By.CSS_SELECTOR, selector)
        except Exception:
            continue
        if elems:
            for el in elems:
                raw_text = el.get_attribute('textContent') or el.text or ''
                text = re.sub(r'^[•\-–—]\s*', '', raw_text.strip())
                if text and len(text) > 3:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line and len(line) > 3 and not re.search(r'^\s*\d+[\.,\d]*\s*(VND|USD|EUR)?\s*$', line):
                            facility_lines.append(line)
            break

    if not facility_lines:
        for selector in _FACILITY_BROADER_SELECTORS:
            try:
                elems = pricing_row.find_elements(By.CSS_SELECTOR, selector)
            except Exception:
                continue
            for el in elems:
                text = (el.get_attribute('textContent') or el.text or '').strip()
                if text and len(text) > 3 and text not in facility_lines:
                    facility_lines.append(text)

    return facility_lines


def _get_discount_percent(pricing_row):
    try:
        price_cell = pricing_row.find_element(
            By.CSS_SELECTOR,
            '.hprt-table-cell-price, [data-testid="price-and-discounted-price"], .hprt-price-block',
        )
        price_text = price_cell.get_attribute('textContent') or price_cell.text or ''
        match = re.search(r'(?:Ti[ếe]t ki[ệe]m|Gi[aả]m)\s+(\d+)%', _nfc(price_text), re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        for badge in pricing_row.find_elements(By.CSS_SELECTOR, '.bui-badge__text'):
            text = _nfc((badge.get_attribute('textContent') or badge.text or '').strip().lower())
            if 'tiết kiệm' in text or 'ưu đãi' in text:
                match = re.search(r'(\d+)\s*%', text)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return None


def _get_scarcity_text(pricing_row):
    """rooms_left signal — Booking đôi khi hiện badge riêng ('.only_x_left'...) NGOÀI danh sách
    conditions. Trả về text thô (thêm vào facility_lines để parser.py bóc số ra rooms_left)."""
    for selector in _SCARCITY_SELECTORS:
        try:
            el = pricing_row.find_element(By.CSS_SELECTOR, selector)
            text = _nfc((el.get_attribute('textContent') or el.text or '').strip())
            if text and ('còn' in text.lower() or 'left' in text.lower()):
                return text
        except Exception:
            continue

    for xpath_cond in _SCARCITY_XPATH_CONTAINS:
        try:
            el = pricing_row.find_element(By.XPATH, f'.//*[{xpath_cond}]')
            text = _nfc((el.get_attribute('textContent') or el.text or '').strip())
            if text:
                return text
        except Exception:
            continue
    return None


def _extract_tax_info(pricing_row):
    """Lấy trạng thái gồm thuế và số tiền thuế/phí khi Booking công bố riêng."""
    try:
        text = _nfc((pricing_row.get_attribute('textContent') or pricing_row.text or ''))
    except Exception:
        return None, None

    normalized = re.sub(r'\s+', ' ', text).strip()
    separately_added_tax = re.search(
        r'\+\s*VND\s*[\d\.,]+[^\d]{0,30}(?:thuế và phí|thuế, phí|taxes and fees)',
        normalized,
        re.IGNORECASE,
    )
    includes = None
    if re.search(r'đã bao gồm thuế và phí|includes taxes and fees', normalized, re.IGNORECASE):
        includes = True
    elif re.search(r'chưa bao gồm thuế và phí|excludes taxes and fees', normalized, re.IGNORECASE):
        includes = False
    elif separately_added_tax:
        # Booking hiện có biến thể chỉ ghi "+ VND ... thuế và phí", không kèm
        # chữ "chưa bao gồm". Dấu cộng là bằng chứng số tiền này nằm ngoài giá.
        includes = False

    amount = None
    # Chỉ parse khi có nhãn thuế/phí và một số tiền VND riêng; không suy ra số từ tổng giá.
    patterns = [
        r'(?:thuế và phí|thuế, phí|taxes and fees)[^\d]{0,30}VND\s*([\d\.,]+)',
        r'\+\s*VND\s*([\d\.,]+)[^\d]{0,30}(?:thuế và phí|thuế, phí|taxes and fees)',
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            raw = match.group(1).replace('.', '').replace(',', '')
            if raw:
                amount = float(raw)
                break
    return amount, includes


def _extract_price(pricing_row, selectors):
    for selector in selectors:
        try:
            el = pricing_row.find_element(By.CSS_SELECTOR, selector)
            text = (el.get_attribute('textContent') or el.text or '')
            text = text.strip().replace('\n', ' ').replace('\xa0', ' ')
            if not text:
                continue
            match = re.search(r'([\d\.,]+)', text)
            if match:
                value = match.group(1).replace('.', '').replace(',', '')
                if value:
                    return float(value)
        except Exception:
            continue
    return None


def _extract_price_from_attr(pricing_row, selector, attr_name):
    try:
        el = pricing_row.find_element(By.CSS_SELECTOR, selector)
        raw = el.get_attribute(attr_name) or el.get_attribute('textContent') or el.text
        if raw:
            match = re.search(r'([\d\.,]+)', str(raw).strip())
            if match:
                value = match.group(1).replace('.', '').replace(',', '')
                if value:
                    return float(value)
    except Exception:
        pass
    return None
