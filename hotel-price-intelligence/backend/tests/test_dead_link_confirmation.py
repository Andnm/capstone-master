"""Contract: 1 redirect /searchresults khong bao gio du de cascade sibling ngay. Phai probe lan 2
bang driver rieng tren canonical URL (khong checkin/checkout) truoc khi ket luan confirmed/
not_confirmed/not_bookable/inconclusive. Chi verdict "confirmed" moi duoc cascade + tang streak.
"""
from datetime import date

from app.database.durable import DurableQueueRepository
from app.scraper.booking_scraper import DeadLinkConfirmation, confirm_dead_link
from app.scraper.errors import ErrorCode, failure
from app.scraper.worker import CrawlWorker


# --------------------------------------------------------------------------------------
# Fake Selenium driver cho confirm_dead_link() - khong can Chrome/Edge that.
# --------------------------------------------------------------------------------------
class _FakeElement:
    def __init__(self, text=""):
        self.text = text

    def get_attribute(self, name):
        return self.text


class _FakeDriver:
    def __init__(
        self, current_url, *, body_text="", raise_on_get=None, not_bookable_els=None,
        raise_on_set_timeout=None,
    ):
        self.current_url = current_url
        self.body_text = body_text
        self.raise_on_get = raise_on_get
        self.not_bookable_els = not_bookable_els or []
        self.raise_on_set_timeout = raise_on_set_timeout
        self.quit_called = False

    def set_page_load_timeout(self, seconds):
        if self.raise_on_set_timeout:
            raise self.raise_on_set_timeout

    def get(self, url):
        if self.raise_on_get:
            raise self.raise_on_get

    def find_element(self, by, value):
        return _FakeElement(self.body_text)

    def find_elements(self, by, value):
        return self.not_bookable_els

    def quit(self):
        self.quit_called = True


def _patch_driver(monkeypatch, driver):
    monkeypatch.setattr('app.scraper.booking_scraper.get_driver', lambda is_headless=True: driver)
    monkeypatch.setattr('app.scraper.booking_scraper.time.sleep', lambda *_: None)


# --------------------------------------------------------------------------------------
# 1-4: confirm_dead_link() phan loai verdict
# --------------------------------------------------------------------------------------

def test_probe_loads_real_property_page_is_not_confirmed(monkeypatch):
    driver = _FakeDriver('https://www.booking.com/hotel/vn/serenity-airport.vi.html')
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'not_confirmed'
    assert driver.quit_called is True


def test_probe_redirects_to_searchresults_again_is_confirmed(monkeypatch):
    driver = _FakeDriver('https://www.booking.com/searchresults.vi.html?ss=serenity')
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'confirmed'


def test_probe_captcha_is_inconclusive_not_confirmed(monkeypatch):
    driver = _FakeDriver(
        'https://www.booking.com/searchresults.vi.html?ss=serenity',
        body_text='please verify you are human',
    )
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'inconclusive'
    assert result.scrape_failure.code == ErrorCode.CAPTCHA


def test_probe_network_timeout_exception_is_inconclusive_with_original_failure(monkeypatch):
    driver = _FakeDriver(
        'https://www.booking.com/hotel/vn/serenity-airport.html',
        raise_on_get=Exception('net::ERR_CONNECTION_TIMED_OUT'),
    )
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'inconclusive'
    assert result.scrape_failure.code == ErrorCode.NETWORK_TIMEOUT


def test_probe_detects_not_bookable_property_page(monkeypatch):
    driver = _FakeDriver(
        'https://www.booking.com/hotel/vn/serenity-airport.vi.html',
        not_bookable_els=[_FakeElement(
            'Hiện tại việc đặt phòng tại khách sạn này không thể thực hiện được'
        )],
    )
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'not_bookable'
    assert result.not_bookable_message


def test_probe_lands_on_unrelated_page_is_inconclusive_not_not_confirmed(monkeypatch):
    driver = _FakeDriver('https://www.booking.com/index.vi.html')
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'inconclusive'


def test_probe_redirect_to_a_different_property_is_inconclusive_not_not_confirmed(monkeypatch):
    # Booking co the redirect sang MOT PROPERTY KHAC (khong phai searchresults) - tuyet doi khong
    # duoc doc trang cua hotel khac roi ket luan/ghi de trang thai cho hotel goc (GPT MAJOR 4).
    driver = _FakeDriver('https://www.booking.com/hotel/vn/some-other-hotel.vi.html')
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'inconclusive'
    assert result.evidence['probe_error_code'] == 'property_mismatch'


def test_unexpected_exception_during_probe_never_escapes_the_function(monkeypatch):
    # set_page_load_timeout()/current_url tu Selenium co the raise tren mot session da chet theo
    # cach khong duoc bat boi cac try/except cu the - phai co luoi an toan cuoi cung, khong duoc
    # de exception thoat ra ngoai va lam crash worker process (GPT MAJOR 4).
    driver = _FakeDriver(
        'https://www.booking.com/hotel/vn/serenity-airport.html',
        raise_on_set_timeout=RuntimeError('session terminated unexpectedly'),
    )
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.verdict == 'inconclusive'
    assert driver.quit_called is True


def test_every_verdict_evidence_carries_verdict_and_finish_timestamp(monkeypatch):
    driver = _FakeDriver('https://www.booking.com/searchresults.vi.html')
    _patch_driver(monkeypatch, driver)

    result = confirm_dead_link('https://www.booking.com/hotel/vn/serenity-airport.html', 'req', 'final')

    assert result.evidence['verdict'] == 'confirmed'
    assert 'probe_finished_at_utc' in result.evidence
    assert '+00:00' in result.evidence['probe_finished_at_utc'] or result.evidence['probe_finished_at_utc'].endswith('Z')


# --------------------------------------------------------------------------------------
# 5-7: compute_dead_link_streak() - pure, khong can DB
# --------------------------------------------------------------------------------------

def test_streak_increments_on_consecutive_calendar_day():
    repo = DurableQueueRepository()
    streak, reset_start, review = repo.compute_dead_link_streak(1, date(2026, 9, 1), date(2026, 9, 2))
    assert (streak, reset_start, review) == (2, False, False)


def test_streak_reaches_review_required_at_three():
    repo = DurableQueueRepository()
    streak, reset_start, review = repo.compute_dead_link_streak(2, date(2026, 9, 1), date(2026, 9, 2))
    assert (streak, review) == (3, True)


def test_streak_same_calendar_day_does_not_double_count():
    repo = DurableQueueRepository()
    streak, reset_start, review = repo.compute_dead_link_streak(2, date(2026, 9, 2), date(2026, 9, 2))
    assert (streak, reset_start) == (2, False)


def test_streak_resets_after_gap():
    repo = DurableQueueRepository()
    streak, reset_start, review = repo.compute_dead_link_streak(5, date(2026, 8, 20), date(2026, 9, 2))
    assert (streak, reset_start, review) == (1, True, False)


def test_streak_starts_fresh_when_no_prior_confirmation():
    repo = DurableQueueRepository()
    streak, reset_start, review = repo.compute_dead_link_streak(0, None, date(2026, 9, 2))
    assert (streak, reset_start) == (1, True)


# --------------------------------------------------------------------------------------
# 8, 10, 12: CrawlWorker._handle_dead_link_confirmation() dinh tuyen dung, khong lam mat
# taxonomy that cua probe, khong bao gio cascade tru verdict "confirmed"
# --------------------------------------------------------------------------------------
class _FakeQueue:
    def __init__(self):
        self.calls = []

    def record_confirmed_dead_link(self, item, evidence, **kwargs):
        self.calls.append(('record_confirmed_dead_link', evidence))

    def record_failure(self, item, scrape_failure, **kwargs):
        self.calls.append(('record_failure', scrape_failure.code, kwargs.get('dead_link_confirmation')))

    def defer_network_failure(self, item, scrape_failure, **kwargs):
        self.calls.append(('defer_network_failure', scrape_failure.code, kwargs.get('dead_link_confirmation')))

    def persist_success(self, **kwargs):
        self.calls.append(('persist_success', kwargs.get('is_not_bookable'), kwargs.get('dead_link_confirmation')))


def _make_worker():
    worker = object.__new__(CrawlWorker)
    worker.queue = _FakeQueue()
    worker.driver = None
    worker.driver_items = 0
    return worker


_ITEM = {
    'id': 1, 'crawl_run_id': 1, 'source_link_hash': 'hash1',
    'source_hotel_link': 'https://www.booking.com/hotel/vn/serenity-airport.html',
    'hotel_name_hint': 'Serenity Airport', 'market_hint': 'Hồ Chí Minh',
}


def test_confirmed_verdict_only_path_that_cascades(monkeypatch):
    worker = _make_worker()
    confirmation = DeadLinkConfirmation('confirmed', {'probe_final_url': 'x'})

    outcome = worker._handle_dead_link_confirmation(_ITEM, confirmation, __import__('time').perf_counter())

    assert outcome == ErrorCode.DEAD_LINK
    assert worker.queue.calls[0][0] == 'record_confirmed_dead_link'
    assert all(call[0] != 'record_failure' for call in worker.queue.calls)


def test_not_confirmed_verdict_retries_without_cascade():
    worker = _make_worker()
    confirmation = DeadLinkConfirmation('not_confirmed', {'first_final_url': 'x'})

    outcome = worker._handle_dead_link_confirmation(_ITEM, confirmation, __import__('time').perf_counter())

    assert outcome == ErrorCode.PROPERTY_REDIRECT_UNCONFIRMED
    kind, code, evidence = worker.queue.calls[0]
    assert kind == 'record_failure'
    assert code == ErrorCode.PROPERTY_REDIRECT_UNCONFIRMED
    assert evidence == confirmation.evidence


def test_inconclusive_network_timeout_goes_through_network_failure_path_not_generic_unconfirmed():
    worker = _make_worker()
    probe_failure = failure(ErrorCode.NETWORK_TIMEOUT, 'timeout during probe')
    confirmation = DeadLinkConfirmation('inconclusive', {'first_final_url': 'x'}, scrape_failure=probe_failure)

    outcome = worker._handle_dead_link_confirmation(_ITEM, confirmation, __import__('time').perf_counter())

    assert outcome == ErrorCode.NETWORK_TIMEOUT
    kind, code, evidence = worker.queue.calls[0]
    assert kind == 'defer_network_failure'
    assert evidence == confirmation.evidence  # evidence khong duoc mat o nhanh network timeout
    assert all(call[0] != 'record_failure' for call in worker.queue.calls)


def test_inconclusive_captcha_preserves_real_error_code_not_generic():
    worker = _make_worker()
    probe_failure = failure(ErrorCode.CAPTCHA, 'captcha during probe')
    confirmation = DeadLinkConfirmation('inconclusive', {'first_final_url': 'x'}, scrape_failure=probe_failure)

    outcome = worker._handle_dead_link_confirmation(_ITEM, confirmation, __import__('time').perf_counter())

    assert outcome == ErrorCode.CAPTCHA
    kind, code, evidence = worker.queue.calls[0]
    assert (kind, code) == ('record_failure', ErrorCode.CAPTCHA)


def test_inconclusive_without_probe_failure_uses_dead_link_inconclusive_code():
    worker = _make_worker()
    confirmation = DeadLinkConfirmation('inconclusive', {'first_final_url': 'x', 'probe_error_code': 'unrecognized_landing_page'})

    outcome = worker._handle_dead_link_confirmation(_ITEM, confirmation, __import__('time').perf_counter())

    assert outcome == ErrorCode.DEAD_LINK_INCONCLUSIVE
    kind, code, evidence = worker.queue.calls[0]
    assert (kind, code) == ('record_failure', ErrorCode.DEAD_LINK_INCONCLUSIVE)
    assert evidence == confirmation.evidence


def test_not_bookable_verdict_persists_via_existing_not_bookable_path_not_as_error():
    worker = _make_worker()
    confirmation = DeadLinkConfirmation(
        'not_bookable', {'probe_final_url': 'https://www.booking.com/hotel/vn/serenity-airport.html'},
        not_bookable_message='khong the dat phong',
    )

    outcome = worker._handle_dead_link_confirmation(_ITEM, confirmation, __import__('time').perf_counter())

    assert outcome is None
    kind, is_not_bookable, evidence = worker.queue.calls[0]
    assert (kind, is_not_bookable) == ('persist_success', True)
    assert evidence == confirmation.evidence  # evidence khong duoc mat o nhanh not_bookable
    assert all(call[0] != 'record_failure' for call in worker.queue.calls)


# --------------------------------------------------------------------------------------
# 9, 13: record_confirmed_dead_link() dung hotel_id suy tu URL (khong can row hotels), va
# cascade + health update chay trong CUNG 1 transaction (1 connection, 1 commit).
# --------------------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._pending = None

    def execute(self, sql, params=None):
        self.conn.executed.append((' '.join(sql.split()), params))
        if 'SELECT * FROM hotel_link_health' in sql:
            self._pending = self.conn.health_row
        else:
            self._pending = None

    def fetchone(self):
        return self._pending

    def fetchall(self):
        return []

    def close(self):
        pass


class _FakeConn:
    def __init__(self, health_row=None):
        self.health_row = health_row
        self.executed = []
        self.commits = 0

    def cursor(self, dictionary=False):
        return _FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_confirmed_dead_link_derives_hotel_id_from_url_without_hotels_row(monkeypatch):
    fake_conn = _FakeConn(health_row=None)  # chua tung co row - hotel chet ngay lan cao dau tien
    monkeypatch.setattr('app.database.durable.get_db_connection', lambda: fake_conn)
    repo = DurableQueueRepository()
    monkeypatch.setattr(repo, 'recompute_run', lambda run_id: None)

    repo.record_confirmed_dead_link(_ITEM, {'probe_final_url': 'x'})

    insert_calls = [p for sql, p in fake_conn.executed if 'INSERT INTO hotel_link_health' in sql]
    assert len(insert_calls) == 1
    params = insert_calls[0]
    assert params[0] == _ITEM['source_link_hash']
    assert params[1] == 'serenity-airport'  # extract_hotel_slug(source_hotel_link), khong tu bang hotels
    assert params[3] == 1  # streak dau tien = 1


def test_confirmed_dead_link_writes_health_and_cascade_in_one_commit(monkeypatch):
    fake_conn = _FakeConn(health_row=None)
    monkeypatch.setattr('app.database.durable.get_db_connection', lambda: fake_conn)
    repo = DurableQueueRepository()
    monkeypatch.setattr(repo, 'recompute_run', lambda run_id: None)

    repo.record_confirmed_dead_link(_ITEM, {'probe_final_url': 'x'})

    tables_touched = {
        sql.split()[1] if sql.startswith('UPDATE') else sql.split()[2]
        for sql, _ in fake_conn.executed
    }
    assert 'hotel_link_health' in tables_touched
    assert 'crawl_run_items' in tables_touched
    assert fake_conn.commits == 1  # 1 connection, 1 commit -> atomic


def test_confirmed_dead_link_evidence_timestamp_is_timezone_aware(monkeypatch):
    fake_conn = _FakeConn(health_row=None)
    monkeypatch.setattr('app.database.durable.get_db_connection', lambda: fake_conn)
    repo = DurableQueueRepository()
    monkeypatch.setattr(repo, 'recompute_run', lambda run_id: None)

    repo.record_confirmed_dead_link(_ITEM, {'probe_final_url': 'x'})

    item_update = next(
        p for sql, p in fake_conn.executed
        if sql.startswith('UPDATE crawl_run_items') and 'dead_link_confirmation' in sql
    )
    evidence_json = item_update[-2]  # dead_link_confirmation la tham so ngay truoc item["id"]
    import json as _json
    saved_evidence = _json.loads(evidence_json)
    assert '+00:00' in saved_evidence['confirmed_at_utc'] or saved_evidence['confirmed_at_utc'].endswith('Z')


# --------------------------------------------------------------------------------------
# MAJOR 1 (GPT review file 05): reset_dead_link_health phai nam TRONG cung transaction cua
# persist_success(), khong phai 1 transaction rieng sau commit - neu khong, 1 loi rieng le o
# reset co the bien 1 item success THAT thanh error/DB_ERROR du observation da luu xong.
# --------------------------------------------------------------------------------------
_SUCCESS_ITEM = dict(
    _ITEM,
    checkin_date='2026-09-10',
    hotel_link='https://www.booking.com/hotel/vn/serenity-airport.html',
)
_SUCCESS_HOTEL = {
    'hotel_id': 'serenity-airport', 'name': 'Serenity Airport', 'name_normalized': 'serenity airport',
    'hotel_link': 'https://www.booking.com/hotel/vn/serenity-airport.html',
}


def test_persist_success_resets_dead_link_health_in_same_transaction(monkeypatch):
    fake_conn = _FakeConn(health_row=None)
    monkeypatch.setattr('app.database.durable.get_db_connection', lambda: fake_conn)
    repo = DurableQueueRepository()
    monkeypatch.setattr(repo, 'recompute_run', lambda run_id: None)

    repo.persist_success(
        item=_SUCCESS_ITEM, hotel=_SUCCESS_HOTEL, records=[], diagnostics={}, timings={},
        artifacts={}, is_sold_out=False, is_not_bookable=False,
    )

    reset_calls = [sql for sql, _ in fake_conn.executed if 'UPDATE hotel_link_health' in sql]
    assert len(reset_calls) == 1
    # Chi 1 get_db_connection() (1 fake_conn) duoc dung cho toan bo persist_success + reset, va
    # chi dung dung 1 lan commit() -> health reset la mot phan cua CUNG transaction, khong phai
    # transaction rieng sau khi transaction chinh da commit.
    assert fake_conn.commits == 1


def test_hotels_upsert_does_not_overwrite_existing_name_with_empty_string(monkeypatch):
    # MINOR 1 (GPT review file 05): not_bookable tu canonical probe chi co hotel_name_hint, co the
    # rong - khong duoc ha chat luong ten hotel that da parse tu lan cao thanh cong truoc do.
    fake_conn = _FakeConn(health_row=None)
    monkeypatch.setattr('app.database.durable.get_db_connection', lambda: fake_conn)
    repo = DurableQueueRepository()
    monkeypatch.setattr(repo, 'recompute_run', lambda run_id: None)

    repo.persist_success(
        item=_SUCCESS_ITEM, hotel=dict(_SUCCESS_HOTEL, name='', name_normalized=''),
        records=[], diagnostics={}, timings={}, artifacts={},
        is_sold_out=False, is_not_bookable=False,
    )

    hotel_upsert_sql = next(sql for sql, _ in fake_conn.executed if sql.startswith('INSERT INTO hotels'))
    assert "COALESCE(NULLIF(VALUES(name),''),name)" in hotel_upsert_sql
    assert "COALESCE(NULLIF(VALUES(name_normalized),''),name_normalized)" in hotel_upsert_sql
