from app.scraper.network import NetworkCircuitBreaker, booking_network_reachable
from app.scraper.worker import CrawlWorker
from app.scraper.errors import ErrorCode, classify_exception


def test_network_circuit_opens_after_three_consecutive_failures():
    circuit = NetworkCircuitBreaker(failure_threshold=3)

    assert circuit.record_network_failure() is False
    assert circuit.record_network_failure() is False
    assert circuit.record_network_failure() is True
    assert circuit.is_open is True


def test_selenium_disconnected_error_is_classified_as_network_timeout():
    result = classify_exception(
        'unknown error: net::ERR_INTERNET_DISCONNECTED (Session info: chrome=140)'
    )

    assert result.code == ErrorCode.NETWORK_TIMEOUT
    assert result.retryable is True


def test_non_network_result_resets_failure_streak():
    circuit = NetworkCircuitBreaker(failure_threshold=3)
    circuit.record_network_failure()
    circuit.record_network_failure()
    circuit.record_non_network_result()

    assert circuit.consecutive_failures == 0
    assert circuit.record_network_failure() is False


def test_probe_backoff_and_two_success_recovery():
    circuit = NetworkCircuitBreaker(
        failure_threshold=1,
        backoff_seconds=(30, 60, 120, 300),
        recovery_successes_required=2,
        recovery_confirm_seconds=15,
    )
    circuit.record_network_failure()

    assert circuit.next_probe_delay() == 30
    assert circuit.record_probe_result(False) is False
    assert circuit.next_probe_delay() == 60
    assert circuit.record_probe_result(False) is False
    assert circuit.next_probe_delay() == 120
    assert circuit.record_probe_result(False) is False
    assert circuit.next_probe_delay() == 300
    assert circuit.record_probe_result(False) is False
    assert circuit.next_probe_delay() == 300

    assert circuit.record_probe_result(True) is False
    assert circuit.next_probe_delay() == 15
    assert circuit.record_probe_result(True) is True
    assert circuit.is_open is False
    assert circuit.consecutive_failures == 0


def test_booking_probe_accepts_any_http_response(monkeypatch):
    class _Response:
        def close(self):
            return None

    monkeypatch.setattr('app.scraper.network.socket.getaddrinfo', lambda *args, **kwargs: [object()])
    monkeypatch.setattr('app.scraper.network.requests.get', lambda *args, **kwargs: _Response())

    assert booking_network_reachable(1) is True


def test_booking_probe_returns_false_when_dns_fails(monkeypatch):
    def _offline(*args, **kwargs):
        raise OSError('offline')

    monkeypatch.setattr('app.scraper.network.socket.getaddrinfo', _offline)

    assert booking_network_reachable(1) is False


def test_worker_waits_for_two_probes_then_resumes_automatically(monkeypatch):
    class _Queue:
        def __init__(self):
            self.wait_heartbeats = []
            self.online_heartbeats = 0

        def heartbeat_network_wait(self, worker_id, **kwargs):
            self.wait_heartbeats.append((worker_id, kwargs))

        def heartbeat_item(self, worker_id, item_id):
            self.online_heartbeats += 1

    worker = object.__new__(CrawlWorker)
    worker.worker_id = 'test-worker'
    worker.queue = _Queue()
    worker.driver = None
    worker.driver_items = 0
    worker.driver_start_ms = 0
    worker.network_breaker = NetworkCircuitBreaker(
        failure_threshold=1,
        backoff_seconds=(0,),
        recovery_successes_required=2,
        recovery_confirm_seconds=0,
    )
    worker.network_breaker.record_network_failure()
    probe_results = iter((True, True))
    monkeypatch.setattr(
        'app.scraper.worker.booking_network_reachable',
        lambda timeout_seconds: next(probe_results),
    )

    worker._wait_until_network_recovers()

    assert worker.network_breaker.is_open is False
    assert len(worker.queue.wait_heartbeats) == 2
    assert worker.queue.online_heartbeats == 1
