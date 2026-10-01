"""Connectivity probe and state machine for the crawler network circuit breaker."""
from dataclasses import dataclass, field
import socket
from typing import Sequence

import requests


def booking_network_reachable(timeout_seconds: int = 10) -> bool:
    """Return True when DNS and Booking's HTTPS endpoint are reachable.

    Any HTTP response means the network path is alive. CAPTCHA, 403 and 429 are
    handled by the scraper's own taxonomy and are not treated as an outage.
    """
    try:
        socket.getaddrinfo("www.booking.com", 443, type=socket.SOCK_STREAM)
        response = requests.get(
            "https://www.booking.com/robots.txt",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout_seconds,
            allow_redirects=True,
            stream=True,
        )
        response.close()
        return True
    except (OSError, requests.RequestException):
        return False


@dataclass
class NetworkCircuitBreaker:
    failure_threshold: int = 3
    backoff_seconds: Sequence[int] = field(default_factory=lambda: (30, 60, 120, 300))
    recovery_successes_required: int = 2
    recovery_confirm_seconds: int = 15
    consecutive_failures: int = 0
    probe_attempt: int = 0
    consecutive_probe_successes: int = 0
    is_open: bool = False

    def record_network_failure(self) -> bool:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.is_open = True
            self.probe_attempt = 0
            self.consecutive_probe_successes = 0
        return self.is_open

    def record_non_network_result(self) -> None:
        if not self.is_open:
            self.consecutive_failures = 0

    def next_probe_delay(self) -> int:
        if self.consecutive_probe_successes:
            return self.recovery_confirm_seconds
        if not self.backoff_seconds:
            return 300
        return int(self.backoff_seconds[min(self.probe_attempt, len(self.backoff_seconds) - 1)])

    def record_probe_result(self, reachable: bool) -> bool:
        """Record one lightweight probe; return True only when recovery is confirmed."""
        if not reachable:
            self.consecutive_probe_successes = 0
            self.probe_attempt += 1
            return False

        self.consecutive_probe_successes += 1
        if self.consecutive_probe_successes < self.recovery_successes_required:
            return False

        self.reset()
        return True

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.probe_attempt = 0
        self.consecutive_probe_successes = 0
        self.is_open = False
