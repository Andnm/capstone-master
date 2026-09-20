import time

from app.core.config import settings
from app.scraper.errors import ErrorCode, failure
from app.scraper.network import NetworkCircuitBreaker
from app.scraper.worker import CrawlWorker


class _Queue:
    def __init__(self):
        self.deferred = []
        self.failures = []
        self.heartbeats = []

    def heartbeat_item(self, worker_id, item_id):
        self.heartbeats.append((worker_id, item_id))

    def defer_network_failure(self, item, scrape_failure, **kwargs):
        self.deferred.append((item, scrape_failure, kwargs))

    def record_failure(self, item, scrape_failure, **kwargs):
        self.failures.append((item, scrape_failure, kwargs))


def _worker():
    worker = object.__new__(CrawlWorker)
    worker.worker_id = 'test-worker'
    worker.queue = _Queue()
    worker.driver = None
    worker.driver_items = 0
    worker.driver_start_ms = 0
    worker.network_breaker = NetworkCircuitBreaker(failure_threshold=3)
    return worker


def _item():
    return {
        'id': 10,
        'crawl_run_id': 20,
        'source_hotel_link': 'https://www.booking.com/hotel/vn/example.vi.html',
        'requested_hotel_link': None,
        'hotel_name_hint': 'Example',
        'market_hint': 'Hà Nội',
        'checkin_date': '2026-09-24',
        'checkout_date': '2026-09-25',
    }


def test_fresh_confirmation_can_replace_false_not_bookable(monkeypatch):
    worker = _worker()
    confirmed_result = {
        'hotel_name': 'Example',
        'is_not_bookable': False,
        'is_sold_out': False,
        'rooms': [{'room_type_raw': 'Deluxe'}],
        'diagnostics': {},
    }
    monkeypatch.setattr(
        'app.scraper.worker.scrape_booking_hotel',
        lambda *args, **kwargs: (confirmed_result, None, {'final_url': 'property-url'}),
    )

    result, scrape_failure, meta = worker._confirm_not_bookable(
        _item(),
        {'is_not_bookable': True, 'booking_status_reason': 'stale banner'},
    )

    assert scrape_failure is None
    assert result is confirmed_result
    assert result['rooms']
    assert meta['final_url'] == 'property-url'


def test_not_bookable_requires_two_matching_sessions(monkeypatch):
    worker = _worker()
    confirmed_result = {
        'hotel_name': 'Example',
        'is_not_bookable': True,
        'is_sold_out': False,
        'booking_status_reason': 'visible banner',
        'rooms': [],
        'diagnostics': {},
    }
    monkeypatch.setattr(
        'app.scraper.worker.scrape_booking_hotel',
        lambda *args, **kwargs: (confirmed_result, None, {'final_url': 'property-url'}),
    )

    result, scrape_failure, _ = worker._confirm_not_bookable(
        _item(),
        {'is_not_bookable': True, 'booking_status_reason': 'first banner'},
    )

    assert scrape_failure is None
    assert result['is_not_bookable'] is True
    assert result['diagnostics']['not_bookable_confirmed_twice'] is True


def test_parser_empty_is_reclassified_when_proxy_route_is_down(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(settings, 'PROXY_SERVER', 'proxy.example:1234')
    monkeypatch.setattr(worker, '_network_reachable', lambda: False)

    outcome = worker._record_scrape_failure(
        _item(),
        failure(ErrorCode.PARSER_EMPTY, 'empty'),
        {'final_url': 'property-url'},
        time.perf_counter(),
    )

    assert outcome == ErrorCode.PROXY_UNAVAILABLE
    assert len(worker.queue.deferred) == 1
    assert worker.queue.deferred[0][1].code == ErrorCode.PROXY_UNAVAILABLE
    assert not worker.queue.failures
