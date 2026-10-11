"""86/87: (1) cleanup khong phu thuoc log chan doan thanh cong (STOP truoc log); (2) handshake co han sau launch supervisor — chua thay `started` != chua tung launch.
Dung lai FakeWorld/fixture cua test_operator_q0 (supervisor/publisher gia, dong ho ao). Khong SQL, khong process that."""
from __future__ import annotations

import json

import pytest

from test_operator_q0 import evidence, set_evidence, world  # noqa: F401  (fixture)


class FakeSup:
    """Handle supervisor gia cho start_handshake: poll() = None khi con song."""

    def __init__(self, alive=True):
        self.alive = alive
        self.pid = 4242

    def poll(self):
        return None if self.alive else 1


def failing_log(world, monkeypatch, *kinds):
    """log() nem OSError cho cac loai su kien chan doan da neu (mo phong dia day), nhung ghi binh thuong cac su kien khac."""
    real = world.mod.log

    def flaky(kind, **fields):
        if kind in kinds:
            raise OSError("disk full (simulated)")
        return real(kind, **fields)
    monkeypatch.setattr(world.mod, "log", flaky)


# =========================================================================== (1) cleanup khong phu thuoc log
def test_87_evidence_error_with_a_failing_error_log_still_requests_stop_and_verifies(world, monkeypatch):
    world.start_publisher()
    failing_log(world, monkeypatch, "evidence_error")

    def broken():
        raise ValueError("corrupt evidence")
    monkeypatch.setattr(world.mod, "evidence_state", broken)
    assert world.mod.cmd_evidence() == 1                                                                         # nonzero, khong bi nuot thanh PASS
    assert world.mod.STOP.exists() and world.stopped()


def test_87_terminal_stop_log_failure_still_calls_cmd_stop(world, monkeypatch):
    world.start_publisher()
    failing_log(world, monkeypatch, "stop_required_by_terminal_path")
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 0)
    assert world.mod.cmd_q0() == 0
    assert world.mod.STOP.exists() and world.stopped() and "publisher_stopped" in world.op_events()


def test_87_stop_is_requested_before_any_diagnostic_log(world, monkeypatch):
    world.start_publisher()
    seen = []
    real = world.mod.log

    def spying(kind, **fields):
        if kind == "stop_required_by_terminal_path":
            seen.append(world.mod.STOP.exists())
        return real(kind, **fields)
    monkeypatch.setattr(world.mod, "log", spying)
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 2)
    world.mod.cmd_q0()
    assert seen == [True]


def test_87_a_failing_stopped_log_does_not_turn_a_verified_stop_into_a_failure(world, monkeypatch):
    world.start_publisher()
    failing_log(world, monkeypatch, "publisher_stopped")
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 0)
    assert world.mod.cmd_q0() == 0 and world.stopped()


def test_87_an_unwritable_stop_request_ends_in_nonzero_not_a_fake_verified_dead(world, monkeypatch):
    world.start_publisher()
    world.stopped_after_stop_file = False
    monkeypatch.setattr(world.mod, "request_stop", lambda: None)                                                # khong ghi duoc STOP
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 0)
    with pytest.raises(SystemExit) as info:
        world.mod.cmd_q0()
    assert info.value.code == 3 and "publisher_stop_timeout" in world.op_events()


# =========================================================================== (2) handshake co han
def test_87_evidence_waits_for_a_pending_supervisor_that_starts_after_two_seconds(world, monkeypatch):
    world.launch_pending()
    world.timeline.append((2.0, {"event": "started", "pid": 1234}))
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 0
    assert world.started() and not world.mod.STOP.exists() and world.t - world.t0 >= 2.0                        # da cho handshake that su, khong ket thuc o t0


def test_87_a_supervisor_that_refused_before_start_is_the_only_never_started_case_and_makes_evidence_fail_without_a_stop(world, monkeypatch):
    world.launch_pending()
    world.append({"event": "refused", "reasons": ["pool_size_unreadable"]})
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 1
    assert not world.mod.STOP.exists() and "stop_not_needed" in world.op_events() and "publisher_stopped" not in world.op_events()


def test_87_absence_of_events_is_pending_not_never_started_and_a_terminal_path_requests_stop(world, monkeypatch):
    world.launch_pending()
    world.refuse_on_stop_when_pending = True                                                                    # supervisor dang pool-check; thay STOP o lan kiem lai => refused
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 2)
    assert world.mod.cmd_q0() == 2
    assert world.mod.STOP.exists() and "publisher_never_started_confirmed_refused" in world.op_events() and "stop_not_needed" not in world.op_events()


def test_87_handshake_timeout_in_evidence_requests_stop_and_confirms_end(world, monkeypatch):
    world.launch_pending()
    world.refuse_on_stop_when_pending = True
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 2                                                                        # khong co publisher chay; start that bai co kiem soat
    assert world.mod.STOP.exists() and "handshake_timeout_in_evidence" in world.op_events() and world.t - world.t0 >= 60


def test_87_handshake_timeout_with_an_unresponsive_supervisor_is_nonzero(world, monkeypatch):
    world.launch_pending()
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 3
    assert world.mod.STOP.exists() and "publisher_stop_timeout" in world.op_events()


def test_87_cancel_during_the_handshake_requests_stop_and_propagates(world, monkeypatch):
    world.launch_pending()
    world.refuse_on_stop_when_pending = True
    real_sleep = world.mod.time.sleep

    def cancel_on_first_sleep(seconds):
        if not world.mod.STOP.exists():
            raise KeyboardInterrupt
        real_sleep(seconds)
    monkeypatch.setattr(world.mod.time, "sleep", cancel_on_first_sleep)
    with pytest.raises(KeyboardInterrupt):
        world.mod.cmd_evidence()
    assert world.mod.STOP.exists() and any(e["event"] == "refused" for e in world.events())


# =========================================================================== start_handshake (sau launch trong cmd_start)
def test_87_start_handshake_succeeds_when_started_appears_after_two_seconds(world):
    world.launch_pending()
    world.timeline.append((2.0, {"event": "started", "pid": 1234}))
    assert world.mod.start_handshake(FakeSup()) == 0 and "handshake_started" in world.op_events()
    assert not world.mod.STOP.exists()


def test_87_start_handshake_reports_refusal_as_never_started(world):
    world.launch_pending()
    world.timeline.append((3.0, {"event": "refused", "reasons": ["pool_size_unreadable"]}))
    assert world.mod.start_handshake(FakeSup()) == 2
    assert not world.mod.STOP.exists()


def test_87_start_handshake_with_a_supervisor_that_exited_without_events_is_never_started(world):
    world.launch_pending()
    assert world.mod.start_handshake(FakeSup(alive=False)) == 2 and "handshake_supervisor_exited_without_event" in world.op_events()
    assert world.t - world.t0 < 5                                                                               # supervisor da chet: khong duoc ngoi cho het 60 s


def test_87_start_handshake_exit_race_rereads_events_once_more(world):
    world.launch_pending()

    class RacingSup(FakeSup):
        def poll(self):                                                                                         # supervisor thoat DUNG luc ghi `started` (sau khi trang thai da duoc doc la pending)
            if not world.started():
                world.append({"event": "started", "pid": 1234})
            return 1
    assert world.mod.start_handshake(RacingSup()) == 0


def test_87_start_handshake_timeout_with_a_live_supervisor_requests_stop_and_confirms(world):
    world.launch_pending()
    world.refuse_on_stop_when_pending = True
    assert world.mod.start_handshake(FakeSup()) == 2
    assert world.mod.STOP.exists() and "handshake_incomplete" in world.op_events() and "publisher_never_started_confirmed_refused" in world.op_events()


def test_87_start_handshake_timeout_with_an_unresponsive_supervisor_is_nonzero(world):
    world.launch_pending()
    assert world.mod.start_handshake(FakeSup()) == 3 and world.mod.STOP.exists()


def test_87_start_handshake_cancel_requests_stop_and_propagates(world, monkeypatch):
    world.launch_pending()
    world.refuse_on_stop_when_pending = True
    real_sleep = world.mod.time.sleep

    def cancel_once(seconds):
        if not world.mod.STOP.exists():
            raise KeyboardInterrupt
        real_sleep(seconds)
    monkeypatch.setattr(world.mod.time, "sleep", cancel_once)
    with pytest.raises(KeyboardInterrupt):
        world.mod.start_handshake(FakeSup())
    assert world.mod.STOP.exists()


def test_87_start_handshake_a_publisher_that_stopped_at_once_is_not_success(world):
    world.launch_pending()
    world.append({"event": "started", "pid": 1234})
    world.append({"event": "stopped", "verified_dead": True, "reason": "exited"})
    assert world.mod.start_handshake(FakeSup()) == 3
