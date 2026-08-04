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
from datetime import datetime

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from app.scraper.driver import get_driver
from app.scraper.parser import _nfc
from app.scraper.url_utils import force_vnd_currency

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


def scrape_booking_hotel(url: str, checkin: str, checkout: str) -> tuple:
    """Cào 1 khách sạn cho 1 cặp checkin/checkout (luôn nên là 1 đêm).

    Trả về (result, error_message). result = {
        'hotel_name', 'hotel_link', 'address', 'latitude', 'longitude',
        'review_score', 'review_count', 'popular_facilities' (list),
        'amenity_count_text' (raw "Xem tất cả N tiện nghi" nếu có),
        'is_sold_out': bool,
        'rooms': [ { 'room_type_raw', 'max_occupancy', 'bed_options' (str, đã join "và"/"hoặc" đúng ngữ nghĩa),
                     'room_area', 'price_per_night', 'original_price',
                     'discount_percent_text', 'facility_lines' (list) }, ... ]
    }
    """
    driver = None
    forced_url = force_vnd_currency(url)

    try:
        driver = get_driver(is_headless=True)
        driver.set_page_load_timeout(60)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                driver.get(forced_url)
                if attempt == 0:
                    vietnamese_cookies = [
                        {'name': 'booked_before', 'value': '1'},
                        {'name': 'currency', 'value': 'VND'},
                        {'name': 'language', 'value': 'vi'},
                        {'name': 'country', 'value': 'vn'},
                        {'name': 'selected_currency', 'value': 'VND'},
                        {'name': 'lang', 'value': 'vi'},
                    ]
                    for cookie in vietnamese_cookies:
                        try:
                            driver.add_cookie(cookie)
                        except Exception:
                            pass
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, 'h2.pp-header__title, h1, [data-testid="price-and-discounted-price"]')
                    )
                )
                break
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(5)

        time.sleep(3)

        result = {
            'hotel_name': None,
            'hotel_link': forced_url,
            'address': None,
            'latitude': None,
            'longitude': None,
            'review_score': None,
            'review_count': None,
            'popular_facilities': [],
            'amenity_count_text': None,
            'is_sold_out': False,
            'rooms': [],
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
                addr_parts = [
                    address_obj.get('streetAddress'),
                    address_obj.get('addressLocality'),
                    address_obj.get('addressRegion'),
                ]
                result['address'] = ", ".join([p for p in addr_parts if p])
            elif isinstance(address_obj, str):
                result['address'] = address_obj
            geo_obj = json_ld.get('geo')
            if isinstance(geo_obj, dict):
                try:
                    result['latitude'] = float(geo_obj.get('latitude'))
                    result['longitude'] = float(geo_obj.get('longitude'))
                except Exception:
                    pass

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
            facility_items = facility_wrapper.find_elements(By.CSS_SELECTOR, 'li span.f6b6d2a959')
            for item in facility_items:
                text = _nfc(item.text.strip())
                if text:
                    if 'tiện nghi' in text.lower() and 'tất cả' in text.lower():
                        result['amenity_count_text'] = text
                    else:
                        result['popular_facilities'].append(text)
        except Exception:
            pass

        if _looks_sold_out(driver):
            result['is_sold_out'] = True
            return result, None

        _extract_rooms(driver, result)

        if not result['rooms']:
            # Không trích được phòng nào, và cũng không thấy thông báo sold-out rõ ràng
            # -> coi là lỗi cào (để job_runner retry), KHÔNG phải sold-out xác nhận.
            return None, "Không tìm thấy phòng nào và không có thông báo hết phòng rõ ràng"

        return result, None

    except Exception as e:
        return None, f"Lỗi: {str(e)}"
    finally:
        if driver:
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

    row_index = 0
    while row_index < len(room_rows):
        row = room_rows[row_index]

        try:
            room_type_cell = row.find_elements(By.CSS_SELECTOR, 'th.hprt-table-cell-roomtype')
        except Exception:
            room_type_cell = []

        if not room_type_cell:
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

                if room_type_raw and price_per_night is not None:
                    result['rooms'].append({
                        'room_type_raw': room_type_raw,
                        'max_occupancy': max_occupancy,
                        'bed_options': bed_options,
                        'room_area': room_area,
                        'price_per_night': price_per_night,
                        'original_price': original_price,
                        'discount_percent': float(discount_percent_text) if discount_percent_text else None,
                        'facility_lines': facility_lines,
                    })
            except Exception:
                continue

        row_index += rowspan


def _get_room_name(room_type_header):
    for selector in _ROOM_NAME_SELECTORS:
        try:
            el = room_type_header.find_element(By.CSS_SELECTOR, selector)
            text = el.text.strip()
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
                text = re.sub(r'^[•\-–—]\s*', '', el.text.strip())
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
                text = el.text.strip()
                if text and len(text) > 3 and text not in facility_lines:
                    facility_lines.append(text)

    return facility_lines


def _get_discount_percent(pricing_row):
    try:
        price_cell = pricing_row.find_element(
            By.CSS_SELECTOR,
            '.hprt-table-cell-price, [data-testid="price-and-discounted-price"], .hprt-price-block',
        )
        match = re.search(r'(?:Ti[ếe]t ki[ệe]m|Gi[aả]m)\s+(\d+)%', _nfc(price_cell.text), re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        for badge in pricing_row.find_elements(By.CSS_SELECTOR, '.bui-badge__text'):
            text = _nfc(badge.text.strip().lower())
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
            text = _nfc(el.text.strip())
            if text and ('còn' in text.lower() or 'left' in text.lower()):
                return text
        except Exception:
            continue

    for xpath_cond in _SCARCITY_XPATH_CONTAINS:
        try:
            el = pricing_row.find_element(By.XPATH, f'.//*[{xpath_cond}]')
            text = _nfc(el.text.strip())
            if text:
                return text
        except Exception:
            continue
    return None


def _extract_price(pricing_row, selectors):
    for selector in selectors:
        try:
            el = pricing_row.find_element(By.CSS_SELECTOR, selector)
            text = el.text.strip().replace('\n', ' ').replace('\xa0', ' ')
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
        raw = el.get_attribute(attr_name) or el.text
        if raw:
            match = re.search(r'([\d\.,]+)', str(raw).strip())
            if match:
                value = match.group(1).replace('.', '').replace(',', '')
                if value:
                    return float(value)
    except Exception:
        pass
    return None
