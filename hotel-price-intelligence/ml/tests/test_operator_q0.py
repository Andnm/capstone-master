"""Operator Q0 (84): sau publisher-start, MOI duong ket thuc (evidence busy/stale/loi, Q0 success/refused/fail/exception/cancel) phai yeu cau dung + xac minh chet. Khong SQL, khong process that:
supervisor/publisher la GIA (bien `time.sleep` thanh dong ho gia va ghi su kien `stopped` khi thay stop-file)."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

OPERATOR = Path(r"D:\MSE\CAPSTONE\outputs\repair-run-20261009\operator_q0.py")


class FakeWorld:
    def __init__(self, mod, run: Path, monkeypatch):
        self.mod, self.run = mod, run
        self.t = 1_000.0
        self.t0 = self.t
        self.verified_dead = True
        self.stopped_after_stop_file = True
        self.refuse_on_stop_when_pending = False
        self.timeline = []                                                                                      # [(offset_s, event)] ghi vao SUP_LOG khi dong ho ao toi moc
        monkeypatch.setattr(mod.time, "sleep", self.sleep)
        monkeypatch.setattr(mod.time, "time", lambda: self.t)

    def sleep(self, seconds):
        self.t += seconds
        if self.t > 100_000:
            raise RuntimeError("runaway wait")                                                                   # vong cho khong ket thuc phai THAT BAI test, khong treo
        for item in [i for i in self.timeline if self.t - self.t0 >= i[0]]:
            self.timeline.remove(item)
            self.append(item[1])
        if self.mod.STOP.exists() and self.started() and not self.stopped() and self.stopped_after_stop_file:
            self.append({"event": "stopped", "reason": "stop_requested", "verified_dead": self.verified_dead, "killed_by_supervisor": True, "exit_code": 1})
        elif self.mod.STOP.exists() and not self.started() and self.refuse_on_stop_when_pending and self.mod.SUP_STDOUT.exists() and not any(e["event"] == "refused" for e in self.events()):
            self.append({"event": "refused", "reasons": ["stop_file_already_exists"], "stage": "after_pool_check"})

    def append(self, event):
        with open(self.mod.SUP_LOG, "ab") as handle:
            handle.write((json.dumps({"at_utc": "2026-10-09T10:20:00.000000Z", **event}) + "\n").encode("utf-8"))

    def events(self):
        return [json.loads(x) for x in self.mod.SUP_LOG.read_text(encoding="utf-8").splitlines()] if self.mod.SUP_LOG.exists() else []

    def started(self):
        return any(e["event"] == "started" for e in self.events())

    def stopped(self):
        return any(e["event"] == "stopped" for e in self.events())

    def start_publisher(self):
        self.append({"event": "started", "pid": 1234})

    def launch_pending(self):
        self.mod.SUP_STDOUT.write_bytes(b"")                                                                    # da launch supervisor, chua co su kien nao

    def op_events(self):
        return [json.loads(x)["event"] for x in self.mod.OP_LOG.read_text(encoding="utf-8").splitlines()] if self.mod.OP_LOG.exists() else []


@pytest.fixture
def world(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("operator_q0_under_test", OPERATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name, filename in (("SUP_LOG", "sup.jsonl"), ("OP_LOG", "op.jsonl"), ("STOP", "STOP"), ("LIVE", "live.json"), ("SUP_STDOUT", "sup_stdout.log")):
        monkeypatch.setattr(mod, name, tmp_path / filename)
    return FakeWorld(mod, tmp_path, monkeypatch)


def evidence(world, *, active_runs=0, active_items=0, read_only=True, age=10, generator=None):
    mod = world.mod
    return {"present": True, "age_s": age, "active_runs": active_runs, "active_items": active_items, "read_only_verified": read_only, "latest_run_id": 1, "latest_run_status": "completed",
            "sha256": "x" * 64, "generator_sha256": generator or mod.SHA["source"]}


def set_evidence(world, monkeypatch, **kwargs):
    state = evidence(world, **kwargs)
    monkeypatch.setattr(world.mod, "evidence_state", lambda: state)


# =========================================================================== evidence: busy/stale/loi => dung; sach => KHONG dung
@pytest.mark.parametrize("kwargs", [dict(active_runs=1), dict(active_items=2), dict(read_only=False), dict(age=500), dict(generator="f" * 64)])
def test_84_evidence_that_is_busy_stale_or_wrong_stops_and_verifies_the_started_publisher(world, monkeypatch, kwargs):
    world.start_publisher()
    set_evidence(world, monkeypatch, **kwargs)
    assert world.mod.cmd_evidence() == 1
    assert world.mod.STOP.exists() and world.stopped() and "publisher_stopped" in world.op_events()


def test_84_clean_evidence_does_not_stop_the_publisher(world, monkeypatch):
    world.start_publisher()
    set_evidence(world, monkeypatch)
    assert world.mod.cmd_evidence() == 0
    assert not world.mod.STOP.exists() and not world.stopped()


def test_84_evidence_error_stops_the_publisher(world, monkeypatch):
    world.start_publisher()

    def broken():
        raise ValueError("corrupt evidence")
    monkeypatch.setattr(world.mod, "evidence_state", broken)
    assert world.mod.cmd_evidence() == 1
    assert world.mod.STOP.exists() and world.stopped()


def test_84_evidence_cancel_stops_the_publisher_and_propagates(world, monkeypatch):
    world.start_publisher()

    def cancelled():
        raise KeyboardInterrupt
    monkeypatch.setattr(world.mod, "evidence_state", cancelled)
    with pytest.raises(KeyboardInterrupt):
        world.mod.cmd_evidence()
    assert world.mod.STOP.exists() and world.stopped()


def test_84_missing_first_evidence_waits_then_treats_absence_as_error_and_stops(world, monkeypatch):
    world.start_publisher()
    monkeypatch.setattr(world.mod, "evidence_state", lambda: {"present": False})
    assert world.mod.cmd_evidence() == 1
    assert world.mod.STOP.exists() and world.stopped()


def test_84_evidence_without_a_started_publisher_does_not_fabricate_a_stop(world, monkeypatch):
    set_evidence(world, monkeypatch, active_runs=1)
    assert world.mod.cmd_evidence() == 1
    assert not world.mod.STOP.exists() and "stop_not_needed" in world.op_events() and "publisher_stopped" not in world.op_events()


# =========================================================================== Q0: moi ket thuc deu dung publisher
@pytest.mark.parametrize("q0_code", [0, 1, 2, 3])
def test_84_q0_success_refused_or_failed_always_stops_the_started_publisher(world, monkeypatch, q0_code):
    world.start_publisher()
    monkeypatch.setattr(world.mod, "_q0_body", lambda: q0_code)
    assert world.mod.cmd_q0() == q0_code
    assert world.mod.STOP.exists() and world.stopped() and "publisher_stopped" in world.op_events()


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError, OSError])
def test_84_q0_exception_or_cancel_stops_the_started_publisher_and_propagates(world, monkeypatch, exc):
    world.start_publisher()

    def boom():
        raise exc("simulated")
    monkeypatch.setattr(world.mod, "_q0_body", boom)
    with pytest.raises(exc):
        world.mod.cmd_q0()
    assert world.mod.STOP.exists() and world.stopped()


def test_84_q0_refused_before_any_publisher_start_does_not_fabricate_verified_dead(world, monkeypatch):
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 2)
    assert world.mod.cmd_q0() == 2
    assert not world.mod.STOP.exists() and "stop_not_needed" in world.op_events() and "publisher_stopped" not in world.op_events()


def test_84_cleanup_that_is_not_verified_dead_makes_a_successful_q0_exit_nonzero(world, monkeypatch):
    world.start_publisher()
    world.verified_dead = False
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 0)
    with pytest.raises(SystemExit) as info:
        world.mod.cmd_q0()
    assert info.value.code == 3 and "q0_cleanup_not_verified" in world.op_events()


def test_84_cleanup_that_times_out_is_nonzero_and_starts_nothing_new(world, monkeypatch):
    world.start_publisher()
    world.stopped_after_stop_file = False                                                                       # supervisor "treo": khong bao stopped
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 0)
    with pytest.raises(SystemExit) as info:
        world.mod.cmd_q0()
    assert info.value.code == 3 and "publisher_stop_timeout" in world.op_events() and sum(e["event"] == "started" for e in world.events()) == 1


def test_84_an_already_stopped_publisher_is_not_asked_to_stop_twice(world, monkeypatch):
    world.start_publisher()
    world.append({"event": "stopped", "verified_dead": True, "reason": "wall_deadline"})
    monkeypatch.setattr(world.mod, "_q0_body", lambda: 0)
    assert world.mod.cmd_q0() == 0
    assert "publisher_stopped" in world.op_events()
